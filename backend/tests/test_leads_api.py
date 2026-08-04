import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from backend.api.app import app
from backend.api.dependencies import get_current_user

client = TestClient(app)

MOCK_TENANT_ID = uuid4()
MOCK_USER_ID = uuid4()
LEAD_ID = uuid4()

@pytest.fixture
def mock_db_leads():
    with (
        patch("backend.api.routes.leads.lead_service") as mock_service,
        patch("backend.api.routes.leads.require_module_operational_write", new=AsyncMock()),
    ):
        mock_service.list_leads = AsyncMock(return_value={
            "items": [{"id": str(LEAD_ID), "name": "Lead Test", "status": "new"}],
            "next_cursor": None
        })
        mock_service.get_lead = AsyncMock(return_value={
            "id": str(LEAD_ID),
            "tenant_id": str(MOCK_TENANT_ID),
            "name": "Lead Test",
            "status": "new"
        })
        mock_service.update_lead_status = AsyncMock(return_value=True)
        yield mock_service

def test_read_users_can_list_and_read_leads(mock_db_leads):
    async def mock_member_user():
        return {
            "user_id": MOCK_USER_ID,
            "tenant_id": MOCK_TENANT_ID,
            "role": "member"
        }
    app.dependency_overrides[get_current_user] = mock_member_user

    try:
        # Test List Leads
        res = client.get("/api/v1/leads")
        assert res.status_code == 200
        assert res.json()["success"] is True
        assert len(res.json()["data"]) == 1
        mock_db_leads.list_leads.assert_awaited_once_with(
            tenant_id=MOCK_TENANT_ID,
            cursor=None,
            limit=20,
            status=None,
            search=None
        )

        # Test Get Lead
        res = client.get(f"/api/v1/leads/{LEAD_ID}")
        assert res.status_code == 200
        assert res.json()["success"] is True
        mock_db_leads.get_lead.assert_awaited_once_with(MOCK_TENANT_ID, LEAD_ID)

    finally:
        app.dependency_overrides.clear()

def test_read_users_cannot_patch_leads(mock_db_leads):
    async def mock_member_user():
        return {
            "user_id": MOCK_USER_ID,
            "tenant_id": MOCK_TENANT_ID,
            "role": "member"
        }
    app.dependency_overrides[get_current_user] = mock_member_user

    try:
        res = client.patch(f"/api/v1/leads/{LEAD_ID}", json={"status": "contacted"})
        assert res.status_code == 403
        mock_db_leads.update_lead_status.assert_not_awaited()

    finally:
        app.dependency_overrides.clear()

def test_operators_can_patch_leads(mock_db_leads):
    async def mock_operator_user():
        return {
            "user_id": MOCK_USER_ID,
            "tenant_id": MOCK_TENANT_ID,
            "role": "operator"
        }
    app.dependency_overrides[get_current_user] = mock_operator_user

    try:
        res = client.patch(f"/api/v1/leads/{LEAD_ID}", json={"status": "contacted"})
        assert res.status_code == 200
        assert res.json()["success"] is True
        mock_db_leads.update_lead_status.assert_awaited_once_with(MOCK_TENANT_ID, LEAD_ID, "contacted")

    finally:
        app.dependency_overrides.clear()

def test_admins_can_patch_leads(mock_db_leads):
    async def mock_admin_user():
        return {
            "user_id": MOCK_USER_ID,
            "tenant_id": MOCK_TENANT_ID,
            "role": "tenant_admin"
        }
    app.dependency_overrides[get_current_user] = mock_admin_user

    try:
        res = client.patch(f"/api/v1/leads/{LEAD_ID}", json={"status": "converted"})
        assert res.status_code == 200
        assert res.json()["success"] is True
        mock_db_leads.update_lead_status.assert_awaited_once_with(MOCK_TENANT_ID, LEAD_ID, "converted")

    finally:
        app.dependency_overrides.clear()

def test_cross_tenant_patch_is_forbidden(mock_db_leads):
    mock_db_leads.update_lead_status.return_value = False

    async def mock_operator_user():
        return {
            "user_id": MOCK_USER_ID,
            "tenant_id": MOCK_TENANT_ID,
            "role": "operator"
        }
    app.dependency_overrides[get_current_user] = mock_operator_user

    try:
        res = client.patch(f"/api/v1/leads/{LEAD_ID}", json={"status": "lost"})
        assert res.status_code == 404
        mock_db_leads.update_lead_status.assert_awaited_once_with(MOCK_TENANT_ID, LEAD_ID, "lost")

    finally:
        app.dependency_overrides.clear()
