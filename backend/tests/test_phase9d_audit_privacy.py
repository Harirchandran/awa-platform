import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from uuid import uuid4, UUID
from backend.api.app import app
from backend.api.dependencies import get_current_user
from backend.api.routes.admin import redact_sensitive_values

# Define mock users
MOCK_TENANT_ID = uuid4()
MOCK_USER_ID = uuid4()

MOCK_SUPERADMIN = {
    "user_id": MOCK_USER_ID,
    "tenant_id": MOCK_TENANT_ID,
    "role": "super_admin"
}

MOCK_TENANT_ADMIN = {
    "user_id": MOCK_USER_ID,
    "tenant_id": MOCK_TENANT_ID,
    "role": "tenant_admin"
}

MOCK_MEMBER = {
    "user_id": MOCK_USER_ID,
    "tenant_id": MOCK_TENANT_ID,
    "role": "member"
}

@pytest.fixture
def clean_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()

def test_redact_sensitive_values():
    payload = {
        "ok_field": "val1",
        "api_key": "secret_key_123",
        "nested": {
            "password_hash": "hash_xyz",
            "normal_field": "val2",
            "provider_payload": {
                "otp_code": "4455",
                "webhook_signature": "sig_abc",
                "normal": "val3"
            }
        },
        "list_field": [
            {"auth_token": "token1"},
            {"normal": "val4"}
        ]
    }
    redacted = redact_sensitive_values(payload)
    assert redacted["ok_field"] == "val1"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["password_hash"] == "[REDACTED]"
    assert redacted["nested"]["normal_field"] == "val2"
    assert redacted["nested"]["provider_payload"] == "[REDACTED]"
    assert redacted["list_field"][0]["auth_token"] == "[REDACTED]"
    assert redacted["list_field"][1]["normal"] == "val4"

@pytest.mark.anyio
async def test_global_audit_logs_unauthorized(clean_overrides):
    # Tenant Admin should be forbidden (403)
    async def override_user():
        return MOCK_TENANT_ADMIN
    app.dependency_overrides[get_current_user] = override_user

    client = TestClient(app)
    response = client.get("/api/v1/admin/audit-logs")
    assert response.status_code == 403
    assert "Super Admin access required" in str(response.json())

    # Member should be forbidden (403)
    async def override_member():
        return MOCK_MEMBER
    app.dependency_overrides[get_current_user] = override_member
    response = client.get("/api/v1/admin/audit-logs")
    assert response.status_code == 403

@pytest.mark.anyio
async def test_global_audit_logs_authorized(clean_overrides):
    async def override_user():
        return MOCK_SUPERADMIN
    app.dependency_overrides[get_current_user] = override_user

    mock_logs = {
        "items": [
            {
                "id": str(uuid4()),
                "tenant_id": str(MOCK_TENANT_ID),
                "actor_id": str(MOCK_USER_ID),
                "action": "UPDATE_GLOBAL_PROMPT",
                "resource_type": "prompt",
                "resource_id": None,
                "details": {"prompt_text": "hello", "secret_key": "12345"},
                "ip_address": "127.0.0.1",
                "created_at": "2026-06-15T12:00:00+00:00",
                "tenant_name": "Admin Tenant",
                "actor_email": "admin@example.com"
            }
        ],
        "next_cursor": None
    }

    client = TestClient(app)
    with patch("backend.api.routes.admin.db_client") as db_mock:
        db_mock.list_platform_audit_logs = AsyncMock(return_value=mock_logs)
        response = client.get("/api/v1/admin/audit-logs")
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 1
        assert data[0]["action"] == "UPDATE_GLOBAL_PROMPT"
        # Details must be redacted
        assert data[0]["details"]["secret_key"] == "[REDACTED]"
        assert data[0]["details"]["prompt_text"] == "hello"

@pytest.mark.anyio
async def test_tenant_scoped_audit_logs_unchanged(clean_overrides):
    async def override_user():
        return MOCK_TENANT_ADMIN
    app.dependency_overrides[get_current_user] = override_user

    mock_logs = {
        "items": [
            {
                "id": str(uuid4()),
                "tenant_id": str(MOCK_TENANT_ID),
                "actor_id": str(MOCK_USER_ID),
                "action": "consent_granted",
                "resource_type": "customer",
                "resource_id": None,
                "details": {"consent_version": "v1.0"},
                "ip_address": "127.0.0.1",
                "created_at": "2026-06-15T12:00:00+00:00",
                "tenant_name": "Test Tenant",
                "actor_email": "user@example.com"
            }
        ],
        "next_cursor": None
    }

    client = TestClient(app)
    with patch("backend.api.routes.audit_logs.db_client") as db_mock:
        db_mock.list_audit_logs = AsyncMock(return_value=mock_logs)
        response = client.get("/api/v1/audit-logs")
        assert response.status_code == 200
        # Check that it called list_audit_logs with caller's tenant_id
        db_mock.list_audit_logs.assert_called_once_with(
            MOCK_TENANT_ID, cursor=None, limit=50, action_type=None, date_from=None, date_to=None
        )

@pytest.mark.anyio
async def test_privacy_erase_superadmin_resolution(clean_overrides):
    async def override_user():
        return MOCK_SUPERADMIN
    app.dependency_overrides[get_current_user] = override_user

    customer_id = uuid4()
    other_tenant_id = uuid4()

    client = TestClient(app)
    with patch("backend.api.privacy.db_client") as db_mock:
        from datetime import datetime
        # Mock the connection fetchval to return the customer's actual tenant_id
        conn_mock = AsyncMock()
        conn_mock.fetchval = AsyncMock(return_value=other_tenant_id)
        conn_mock.fetchrow = AsyncMock(return_value={
            "id": customer_id,
            "status": "pending_erasure",
            "erasure_scheduled_at": datetime.now()
        })

        # Setup context manager mock for acquire()
        class AsyncContextManagerMock:
            async def __aenter__(self):
                return conn_mock
            async def __aexit__(self, exc_type, exc, tb):
                pass

        db_mock.acquire = lambda: AsyncContextManagerMock()
        db_mock.write_privacy_audit = AsyncMock()

        response = client.delete(f"/api/v1/privacy/erase/{customer_id}")
        assert response.status_code == 200

        # Should query conn.fetchval for the customer's actual tenant_id
        conn_mock.fetchval.assert_called_once_with(
            "SELECT tenant_id FROM customers WHERE id = $1", customer_id
        )

@pytest.mark.anyio
async def test_privacy_export_superadmin_resolution(clean_overrides):
    async def override_user():
        return MOCK_SUPERADMIN
    app.dependency_overrides[get_current_user] = override_user

    customer_id = uuid4()
    other_tenant_id = uuid4()

    client = TestClient(app)
    with patch("backend.api.privacy.db_client") as db_mock:
        conn_mock = AsyncMock()
        conn_mock.fetchval = AsyncMock(return_value=other_tenant_id)

        class AsyncContextManagerMock:
            async def __aenter__(self):
                return conn_mock
            async def __aexit__(self, exc_type, exc, tb):
                pass

        db_mock.acquire = lambda: AsyncContextManagerMock()
        db_mock.export_customer_data = AsyncMock(return_value={"customers": {"id": str(customer_id), "name": "C1"}})
        db_mock.write_privacy_audit = AsyncMock()

        response = client.get(f"/api/v1/privacy/export/{customer_id}")
        assert response.status_code == 200

        db_mock.export_customer_data.assert_called_once_with(other_tenant_id, customer_id)

@pytest.mark.anyio
async def test_privacy_consent_superadmin_resolution(clean_overrides):
    async def override_user():
        return MOCK_SUPERADMIN
    app.dependency_overrides[get_current_user] = override_user

    customer_id = uuid4()
    other_tenant_id = uuid4()

    client = TestClient(app)
    with patch("backend.api.privacy.db_client") as db_mock:
        conn_mock = AsyncMock()
        conn_mock.fetchval = AsyncMock(return_value=other_tenant_id)

        class AsyncContextManagerMock:
            async def __aenter__(self):
                return conn_mock
            async def __aexit__(self, exc_type, exc, tb):
                pass

        db_mock.acquire = lambda: AsyncContextManagerMock()
        db_mock.update_customer_consent = AsyncMock(return_value=True)
        db_mock.write_privacy_audit = AsyncMock()
        db_mock.erase_customer_data = AsyncMock(return_value={"customers": 1})

        response = client.post(f"/api/v1/privacy/consent/{customer_id}?status=withdrawn")
        assert response.status_code == 200

        db_mock.update_customer_consent.assert_called_once_with(
            tenant_id=other_tenant_id,
            customer_id=customer_id,
            consent_status="withdrawn"
        )
        db_mock.erase_customer_data.assert_called_once_with(other_tenant_id, customer_id)

@pytest.mark.anyio
async def test_privacy_export_tenant_admin_locked(clean_overrides):
    async def override_user():
        return MOCK_TENANT_ADMIN
    app.dependency_overrides[get_current_user] = override_user

    customer_id = uuid4()

    client = TestClient(app)
    with patch("backend.api.privacy.db_client") as db_mock:
        db_mock.export_customer_data = AsyncMock(return_value={"customers": {"id": str(customer_id), "name": "C1"}})
        db_mock.write_privacy_audit = AsyncMock()

        response = client.get(f"/api/v1/privacy/export/{customer_id}")
        assert response.status_code == 200

        # Enforce that it used caller's MOCK_TENANT_ID, not looking it up in database
        db_mock.export_customer_data.assert_called_once_with(MOCK_TENANT_ID, customer_id)
