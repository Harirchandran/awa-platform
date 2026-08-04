import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from backend.api.app import app
from backend.api.dependencies import get_current_user
from backend.schemas.config import build_default_tenant_config
# Mock user for dependency override
MOCK_TENANT_ID = uuid4()
MOCK_USER_ID = uuid4()
async def override_get_current_user():
    return {
        "user_id": MOCK_USER_ID,
        "tenant_id": MOCK_TENANT_ID,
        "role": "admin"
    }
client = TestClient(app)
@pytest.fixture
def mock_db():
    app.dependency_overrides[get_current_user] = override_get_current_user
    with patch("backend.api.routes.tenant.db_client") as t_mock, \
         patch("backend.api.routes.customers.customer_service") as c_mock, \
         patch("backend.api.routes.config.db_client") as cfg_mock, \
         patch("backend.api.entitlements.db_client") as entitlement_mock, \
         patch("backend.db.client.db_client") as usage_mock:
        # Setup tenant mock
        t_mock.get_tenant = AsyncMock(return_value={"id": str(MOCK_TENANT_ID), "name": "Test Tenant"})
        t_mock.get_user = AsyncMock(return_value={"id": str(MOCK_USER_ID), "name": "Test User"})
        t_mock.get_user_tenant_role = AsyncMock(return_value="admin")
        # Setup customers mock
        c_mock.list_customers = AsyncMock(return_value={"items": [{"id": "1", "name": "C1"}], "next_cursor": None})
        # Setup config mock
        cfg_mock.get_tenant_config = AsyncMock(return_value={
            "id": "cfg_1",
            "tenant_id": str(MOCK_TENANT_ID),
            "version": 1,
            "config_json": {"custom": "data"},
            "is_active": True,
        })
        cfg_mock.get_tenant = AsyncMock(return_value={
            "id": MOCK_TENANT_ID,
            "name": "Test Tenant",
            "plan_code": "trial",
            "status": "active",
            "ai_mode": "standard",
        })
        cfg_mock.get_active_config_version = AsyncMock(return_value=1)
        cfg_mock.create_config_version = AsyncMock(return_value={
            "id": uuid4(),
            "tenant_id": str(MOCK_TENANT_ID),
            "version": 2,
            "is_active": False,
        })
        cfg_mock.activate_config_version = AsyncMock(return_value=True)
        cfg_mock.write_audit_log = AsyncMock(return_value=None)
        entitlement_mock.get_tenant = cfg_mock.get_tenant
        entitlement_mock.get_tenant_config = cfg_mock.get_tenant_config
        usage_mock.get_subscription_plan_by_code = AsyncMock(return_value=None)
        yield {
            "tenant": t_mock,
            "customers": c_mock,
            "config": cfg_mock,
            "entitlements": entitlement_mock,
            "usage": usage_mock,
        }
    app.dependency_overrides.pop(get_current_user, None)
def test_get_tenant(mock_db):
    response = client.get("/api/v1/tenant")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["name"] == "Test Tenant"
def test_get_me(mock_db):
    response = client.get("/api/v1/me")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["name"] == "Test User"
    assert data["data"]["role"] == "admin"
def test_list_customers(mock_db):
    response = client.get("/api/v1/customers?limit=10")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert len(data["data"]) == 1
    assert data["data"][0]["name"] == "C1"


def test_update_customer_persists_consent_status(mock_db):
    customer_id = uuid4()
    updated_customer = {
        "id": customer_id,
        "name": "C1",
        "email": "c1@example.com",
        "consent_status": "withdrawn",
        "metadata": {},
    }
    mock_db["customers"].update_customer = AsyncMock(return_value=True)
    mock_db["customers"].get_customer = AsyncMock(return_value=updated_customer)

    response = client.patch(
        f"/api/v1/customers/{customer_id}",
        json={"name": "C1", "consent_status": "withdrawn"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["consent_status"] == "withdrawn"
    mock_db["customers"].update_customer.assert_awaited_once_with(
        MOCK_TENANT_ID,
        customer_id,
        name="C1",
        email=None,
        consent_status="withdrawn",
        metadata=None,
    )
def test_get_config(mock_db):
    response = client.get("/api/v1/config")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["version"] == 1
    plan_policy = data["data"]["plan_policy"]
    assert plan_policy["ai_capability_policy"]["runtime_engine"] == "unified_agent"
    assert plan_policy["ai_capability_policy"]["effective_mode"] == "standard"
    assert any(profile["code"] == "advanced" and profile["allowed"] is False for profile in plan_policy["ai_capability_profiles"])
def test_update_config_rejects_stale_expected_version(mock_db):
    mock_db["config"].get_active_config_version.return_value = 2
    response = client.put(
        "/api/v1/config",
        json={
            "expected_version": 1,
            "config_json": {
                "business_name": "Multi Tab Clinic",
                "business_type": "clinic",
            },
        },
    )
    assert response.status_code == 409
    body = response.json()
    message = body.get("detail") or body.get("error", {}).get("message", "")
    assert "changed in another tab" in message
    mock_db["config"].create_config_version.assert_not_awaited()
def test_update_config_accepts_current_expected_version(mock_db):
    response = client.put(
        "/api/v1/config",
        json={
            "expected_version": 1,
            "config_json": {
                "business_name": "Current Tab Clinic",
                "business_type": "clinic",
            },
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["version"] == 2
    mock_db["config"].create_config_version.assert_awaited_once()
def test_update_config_rejects_ai_mode_not_allowed_by_plan(mock_db):
    response = client.put(
        "/api/v1/config",
        json={
            "expected_version": 1,
            "config_json": {
                "business_name": "Trial Clinic",
                "business_type": "clinic",
                "ai_mode": "concierge",
            },
        },
    )
    assert response.status_code == 403
    body = response.json()
    message = body.get("detail") or body.get("error", {}).get("message", "")
    assert "not available" in message
    mock_db["config"].create_config_version.assert_not_awaited()

def test_update_config_accepts_legacy_concierge_alias_for_entitled_plan(mock_db):
    mock_db["config"].get_tenant.return_value = {
        "id": MOCK_TENANT_ID,
        "name": "Pro Tenant",
        "plan_code": "pro",
        "status": "active",
        "ai_mode": "standard",
    }
    response = client.put(
        "/api/v1/config",
        json={
            "expected_version": 1,
            "config_json": {
                "business_name": "Pro Clinic",
                "business_type": "clinic",
                "ai_mode": "concierge",
            },
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    mock_db["config"].create_config_version.assert_awaited_once()
    saved_config = mock_db["config"].create_config_version.await_args.args[1]
    assert saved_config["ai_mode"] == "advanced"


def test_trial_config_update_creates_and_activates_n_plus_one_with_disabled_reminders(mock_db):
    current = build_default_tenant_config(
        "Trial Clinic",
        business_type="clinic",
        enabled_modules=["booking", "faq", "human_handoff"],
    )
    proposed = build_default_tenant_config(
        "Trial Clinic",
        business_type="clinic",
        enabled_modules=["booking", "faq", "human_handoff"],
    )
    proposed["services"] = [{"id": "consultation", "name": "Consultation", "duration_mins": 30}]
    mock_db["config"].get_tenant_config.return_value = {
        "id": "cfg_1",
        "tenant_id": str(MOCK_TENANT_ID),
        "version": 1,
        "config_json": current,
        "is_active": True,
    }

    response = client.put("/api/v1/config", json={
        "expected_version": 1,
        "config_json": proposed,
    })

    assert response.status_code == 200
    assert response.json()["data"]["version"] == 2
    mock_db["config"].create_config_version.assert_awaited_once()
    mock_db["config"].activate_config_version.assert_awaited_once()
    saved_config = mock_db["config"].create_config_version.await_args.args[1]
    assert saved_config["modules"]["reminders"]["enabled"] is False
    assert saved_config["modules"]["booking"]["enabled"] is True
    assert saved_config["services"][0]["id"] == "consultation"

def test_merge_customers(mock_db):
    primary_id = uuid4()
    secondary_id = uuid4()

    # Mock get_customer for primary and secondary
    mock_db["customers"].get_customer = AsyncMock(side_effect=lambda tenant_id, cid: {"id": cid, "name": "Test Customer"} if cid in (primary_id, secondary_id) else None)
    mock_db["customers"].merge_customers = AsyncMock(return_value=True)

    response = client.post(
        f"/api/v1/customers/{primary_id}/merge",
        json={"secondary_customer_id": str(secondary_id)}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["merged"] is True
    assert data["data"]["primary_id"] == str(primary_id)
    assert data["data"]["secondary_id"] == str(secondary_id)
