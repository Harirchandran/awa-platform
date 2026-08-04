from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4
import pytest
from fastapi.testclient import TestClient
from backend.api.app import app
from backend.api.dependencies import require_super_admin
SUPER_ADMIN_ID = uuid4()
async def override_require_super_admin():
    return {
        "user_id": SUPER_ADMIN_ID,
        "tenant_id": uuid4(),
        "role": "super_admin",
    }
class FakeTransaction:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        return False
class FakeConnection:
    def __init__(self):
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.tenant_id = None
    def transaction(self):
        return FakeTransaction()
    async def fetchval(self, query, *args):
        if "FROM tenants" in query:
            return None
        if "FROM users" in query:
            return None
        return None
    async def fetchrow(self, query, *args):
        if "FROM business_types" in query:
            return {
                "enabled_modules": ["booking", "reminders", "reviews", "faq", "human_handoff"],
            }
        if "INSERT INTO tenants" in query:
            self.tenant_id = args[0]
            return {
                "id": args[0],
                "name": args[1],
                "slug": args[2],
                "status": args[3],
                "plan_code": args[4],
                "created_at": datetime(2026, 5, 22, tzinfo=timezone.utc),
            }
        return None
    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "INSERT 0 1"
class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, exc_type, exc, tb):
        return False
@pytest.fixture
def client():
    app.dependency_overrides[require_super_admin] = override_require_super_admin
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_super_admin, None)
def test_super_admin_create_tenant_creates_admin_handoff(client):
    conn = FakeConnection()
    with (
        patch("backend.api.routes.admin.db_client.acquire", return_value=FakeAcquire(conn)),
        patch(
            "backend.api.routes.admin.settings",
            SimpleNamespace(effective_auth_mode="mock", allow_mock_auth=True),
        ),
    ):
        response = client.post(
            "/api/v1/admin/tenants",
            json={
                "name": "Phase Two Clinic",
                "plan_code": "ENTERPRISE",
                "business_type": "clinic",
                "tenant_admin_email": "owner@phase-two.example",
                "tenant_admin_password": "PhaseTwo123",
                "tenant_admin_name": "Phase Owner",
            },
        )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["name"] == "Phase Two Clinic"
    assert payload["business_type"] == "clinic"
    assert payload["admin_access"]["status"] == "created"
    assert payload["admin_access"]["email"] == "owner@phase-two.example"
    assert payload["admin_access"]["role"] == "tenant_admin"
    assert payload["admin_access"]["dev_session_token"].startswith("mock_token:")
    admin_user_id = UUID(payload["admin_access"]["user_id"])
    assert admin_user_id
    assert any("INSERT INTO tenant_configs" in query for query, _args in conn.executed)
    assert any("INSERT INTO users" in query and args[0] == admin_user_id for query, args in conn.executed)
    assert any("INSERT INTO user_tenants" in query and args[1] == admin_user_id for query, args in conn.executed)
def test_super_admin_create_tenant_requires_admin_password(client):
    conn = FakeConnection()
    with (
        patch("backend.api.routes.admin.db_client.acquire", return_value=FakeAcquire(conn)),
        patch(
            "backend.api.routes.admin.settings",
            SimpleNamespace(effective_auth_mode="mock", allow_mock_auth=True),
        ),
    ):
        response = client.post(
            "/api/v1/admin/tenants",
            json={
                "name": "Missing Password Clinic",
                "tenant_admin_email": "owner@missing-password.example",
            },
        )
    assert response.status_code == 400
    assert "Tenant admin password is required" in response.json()["error"]["message"]
    assert conn.executed == []