from uuid import UUID, uuid4
import pytest
import httpx
from unittest.mock import AsyncMock
from backend.api.app import app
from backend.core.config import settings

@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac

class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, exc_type, exc, tb):
        return False

@pytest.mark.anyio
async def test_mock_login_success(client, monkeypatch):
    """
    Test 1: Successful lookup of a known user email.
    Mock the DB response to return a tenant/user ID, and assert the token format
    is mock_token:{tenant_id}:{user_id}:{role}.
    """
    user_id = uuid4()
    tenant_id = uuid4()
    role = "tenant_admin"
    email = "test-success@example.com"

    # Override settings using monkeypatch on the class properties
    monkeypatch.setattr(type(settings), "allow_mock_auth", True)

    # Mock db_client.acquire context manager
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role
    })
    mock_acquire = FakeAcquire(mock_conn)
    monkeypatch.setattr("backend.api.routes.auth.db_client.acquire", lambda: mock_acquire)

    response = await client.post(
        "/api/v1/auth/mock-login",
        json={"email": email}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["role"] == role
    assert data["data"]["access_token"] == f"mock_token:{tenant_id}:{user_id}:{role}"

@pytest.mark.anyio
async def test_mock_login_super_admin(client, monkeypatch):
    """
    Test 2: The admin@docker.local super admin bypass.
    Assert correct mock super_admin token when ENABLE_MOCK_SUPER_ADMIN is True.
    Assert 403 when ENABLE_MOCK_SUPER_ADMIN is False.
    """
    email = "admin@docker.local"

    monkeypatch.setattr(type(settings), "allow_mock_auth", True)

    # Scenario A: ENABLE_MOCK_SUPER_ADMIN = True
    monkeypatch.setattr(settings, "ENABLE_MOCK_SUPER_ADMIN", True)

    response = await client.post(
        "/api/v1/auth/mock-login",
        json={"email": email}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["role"] == "super_admin"
    assert data["data"]["access_token"] == "mock_token:00000000-0000-0000-0000-000000000001:00000000-0000-0000-0000-000000000099:super_admin"

    # Scenario B: ENABLE_MOCK_SUPER_ADMIN = False
    monkeypatch.setattr(settings, "ENABLE_MOCK_SUPER_ADMIN", False)
    response2 = await client.post(
        "/api/v1/auth/mock-login",
        json={"email": email}
    )
    assert response2.status_code == 403
    assert "Mock super admin is disabled" in response2.json()["error"]["message"]

@pytest.mark.anyio
async def test_mock_login_fallback(client, monkeypatch):
    """
    Test 3: Fallback behavior for an unknown email.
    Assert it returns the seeded ...000100 admin token to prevent breaking legacy tests.
    """
    email = "unknown@example.com"

    monkeypatch.setattr(type(settings), "allow_mock_auth", True)

    # Mock db_client.acquire context manager returning None (user not found)
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_acquire = FakeAcquire(mock_conn)
    monkeypatch.setattr("backend.api.routes.auth.db_client.acquire", lambda: mock_acquire)

    response = await client.post(
        "/api/v1/auth/mock-login",
        json={"email": email}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["role"] == "tenant_admin"
    assert data["data"]["access_token"] == "mock_token:00000000-0000-0000-0000-000000000001:00000000-0000-0000-0000-000000000100:tenant_admin"

@pytest.mark.anyio
async def test_mock_login_disabled(client, monkeypatch):
    """
    Assert mock-login endpoint raises 400 when allow_mock_auth is disabled.
    """
    monkeypatch.setattr(type(settings), "allow_mock_auth", False)

    response = await client.post(
        "/api/v1/auth/mock-login",
        json={"email": "test@example.com"}
    )
    assert response.status_code == 400
    assert "Mock authentication is not enabled" in response.json()["error"]["message"]

@pytest.mark.anyio
async def test_mock_login_forbidden_in_production(client, monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(type(settings), "allow_mock_auth", True)

    response = await client.post(
        "/api/v1/auth/mock-login",
        json={"email": "test@example.com"}
    )
    assert response.status_code == 403
    assert "production" in response.json()["error"]["message"].lower()
