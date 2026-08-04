import pytest
import datetime
from uuid import UUID, uuid4
import httpx
from backend.api.app import app
from backend.core.config import settings
from backend.db.client import db_client
from backend.security.auth import sign_access_token, verify_jwt

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def setup_auth_mode(monkeypatch):
    """Force AUTH_MODE=postgres and enable mock settings for tests."""
    monkeypatch.setattr(settings, "AUTH_MODE", "postgres")
    # Make sure we use a test JWT secret
    monkeypatch.setattr(settings, "JWT_SECRET", "test_jwt_secret_value_for_unit_testing")
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", "test_jwt_secret_value_for_unit_testing")
    await db_client.connect()
    yield
    await db_client.close()


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac


async def test_migration_auth_tables():
    """Verify that credentials and sessions tables exist."""
    async with db_client.acquire() as conn:
        cred_cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'user_credentials'"
        )
        session_cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'user_sessions'"
        )
    assert len(cred_cols) > 0
    assert len(session_cols) > 0
    col_names = {col["column_name"] for col in cred_cols}
    assert "password_hash" in col_names
    assert "password_algorithm" in col_names


async def test_postgres_register_and_login_flow(client):
    """Test full e2e register, login, refresh, and logout flow under AUTH_MODE=postgres."""
    email = f"auth-test-{uuid4().hex[:8]}@example.com"
    password = "TestPassword123"
    # 1. Register new business
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "business_name": "Test Auth Clinic",
            "email": email,
            "password": password,
            "business_type": "general",
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    reg_data = data["data"]
    assert reg_data["email"] == email
    assert "access_token" in reg_data
    assert "refresh_token" in reg_data
    assert reg_data["requires_login"] is False
    user_id = UUID(reg_data["user_id"])
    tenant_id = UUID(reg_data["tenant_id"])
    # Verify DB records
    async with db_client.acquire() as conn:
        cred = await conn.fetchrow("SELECT * FROM user_credentials WHERE user_id = $1", user_id)
        role = await conn.fetchval("SELECT role FROM user_tenants WHERE user_id = $1 AND tenant_id = $2", user_id, tenant_id)
    assert cred is not None
    assert cred["password_algorithm"] == "bcrypt"
    # Ensure password is NOT stored in plaintext
    assert cred["password_hash"] != password
    assert role == "tenant_admin"
    # 2. Log in with correct password
    login_response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": password,
        }
    )
    assert login_response.status_code == 200
    login_data = login_response.json()["data"]
    assert "access_token" in login_data
    assert "refresh_token" in login_data
    access_token = login_data["access_token"]
    refresh_token = login_data["refresh_token"]
    # 3. Access protected endpoint with token
    me_response = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert me_response.status_code == 200
    me_data = me_response.json()["data"]
    assert me_data["email"] == email
    assert me_data["role"] == "tenant_admin"
    # 4. Refresh session
    refresh_response = await client.post(
        "/api/v1/auth/refresh",
        json={
            "refresh_token": refresh_token,
        }
    )
    assert refresh_response.status_code == 200
    refresh_data = refresh_response.json()["data"]
    assert "access_token" in refresh_data
    assert "refresh_token" in refresh_data
    new_access_token = refresh_data["access_token"]
    new_refresh_token = refresh_data["refresh_token"]
    # Verify old refresh token is now revoked
    from backend.security.auth import hash_refresh_token
    async with db_client.acquire() as conn:
        old_revoked = await conn.fetchval(
            "SELECT revoked_at FROM user_sessions WHERE refresh_token_hash = $1",
            hash_refresh_token(refresh_token)
        )
    assert old_revoked is not None
    # 5. Log out
    logout_response = await client.post(
        "/api/v1/auth/logout",
        json={
            "refresh_token": new_refresh_token,
        }
    )
    assert logout_response.status_code == 200
    # Verify new refresh token is revoked
    async with db_client.acquire() as conn:
        new_revoked = await conn.fetchval(
            "SELECT revoked_at FROM user_sessions WHERE refresh_token_hash = $1",
            hash_refresh_token(new_refresh_token)
        )
    assert new_revoked is not None
    # 6. Try refreshing again (must fail)
    fail_refresh = await client.post(
        "/api/v1/auth/refresh",
        json={
            "refresh_token": new_refresh_token,
        }
    )
    assert fail_refresh.status_code == 401


async def test_postgres_login_failures(client):
    """Verify login failure cases."""
    email = f"auth-fail-{uuid4().hex[:8]}@example.com"
    password = "CorrectPassword123"
    # Register first
    await client.post(
        "/api/v1/auth/register",
        json={
            "business_name": "Failure Test Tenant",
            "email": email,
            "password": password,
        }
    )
    # 1. Login with wrong password (should fail generically)
    wrong_pw_response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "WrongPassword1",
        }
    )
    assert wrong_pw_response.status_code == 401
    assert "Invalid email or password" in wrong_pw_response.json()["error"]["message"]
    # 2. Login with missing user (should fail generically)
    missing_response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "non-existent-user-email@example.com",
            "password": password,
        }
    )
    assert missing_response.status_code == 401
    assert "Invalid email or password" in missing_response.json()["error"]["message"]


async def test_postgres_expired_session_fails(client, monkeypatch):
    """Verify expired refresh tokens are rejected."""
    email = f"auth-expire-{uuid4().hex[:8]}@example.com"
    password = "CorrectPassword123"
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={
            "business_name": "Expiry Test Tenant",
            "email": email,
            "password": password,
        }
    )
    reg = reg_resp.json()["data"]
    refresh_token = reg["refresh_token"]
    from backend.security.auth import hash_refresh_token
    # Force expire in DB
    async with db_client.acquire() as conn:
        await conn.execute(
            "UPDATE user_sessions SET expires_at = NOW() - INTERVAL '1 day' WHERE refresh_token_hash = $1",
            hash_refresh_token(refresh_token)
        )
    # Attempt refresh
    expire_response = await client.post(
        "/api/v1/auth/refresh",
        json={
            "refresh_token": refresh_token,
        }
    )
    assert expire_response.status_code == 401
    assert "Session has expired" in expire_response.json()["error"]["message"]


async def test_mock_auth_remains_functional(client, monkeypatch):
    """Verify AUTH_MODE=mock still works as expected."""
    monkeypatch.setattr(settings, "AUTH_MODE", "mock")
    monkeypatch.setattr(settings, "ENABLE_MOCK_AUTH", True)
    response = await client.get(
        "/api/v1/me",
        headers={"Authorization": "Bearer mock_token"}
    )
    assert response.status_code == 200


async def test_mock_auth_accepts_local_role_tokens(monkeypatch):
    """Local mock auth can mint read/operator/admin roles for permission probes."""
    monkeypatch.setattr(settings, "ENV", "test")
    monkeypatch.setattr(settings, "AUTH_MODE", "mock")
    monkeypatch.setattr(settings, "ENABLE_MOCK_AUTH", True)
    monkeypatch.setattr(settings, "ENABLE_MOCK_SUPER_ADMIN", True)
    tenant_id = uuid4()
    user_id = uuid4()
    for role in ("member", "operator", "tenant_admin", "super_admin"):
        payload = verify_jwt(f"mock_token:{tenant_id}:{user_id}:{role}")
        assert payload["sub"] == str(user_id)
        assert payload["app_metadata"]["tenant_id"] == str(tenant_id)
        assert payload["app_metadata"]["role"] == role


async def test_mock_auth_rejects_role_tokens_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "AUTH_MODE", "mock")
    monkeypatch.setattr(settings, "ENABLE_MOCK_AUTH", True)
    with pytest.raises(Exception):
        verify_jwt(f"mock_token:{uuid4()}:{uuid4()}:member")


async def test_rbac_dependencies_enforced(client, monkeypatch):
    """Verify that RBAC policies still correctly block access for lower roles."""
    # Create an access token representing a 'member' role (insufficient for configuration changes)
    user_id = uuid4()
    tenant_id = uuid4()
    email = f"member-{uuid4().hex[:8]}@example.com"
    # Seed tenant and user in DB
    async with db_client.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, plan_code, status) VALUES ($1, $2, $3, $4, $5)",
            tenant_id, "Member Tenant", f"member-{uuid4().hex[:8]}", "trial", "trial"
        )
        await conn.execute("INSERT INTO users (id, phone_number, email, name) VALUES ($1, $2, $3, $4)", user_id, f"auth:{user_id}", email, "Member User")
        await conn.execute("INSERT INTO user_tenants (id, user_id, tenant_id, role) VALUES ($1, $2, $3, $4)", uuid4(), user_id, tenant_id, "member")
    access_token = sign_access_token(user_id, email, tenant_id, "member")
    # Try putting config (requires tenant_admin / operator / write access)
    response = await client.put(
        "/api/v1/config",
        json={
            "expected_version": 1,
            "config_json": {"test": "data"},
        },
        headers={"Authorization": f"Bearer {access_token}"}
    )
    # Should get 403 Forbidden
    assert response.status_code == 403


async def test_suspended_tenant_token_is_blocked(client):
    """Existing tenant sessions should stop working as soon as the tenant is suspended."""
    user_id = uuid4()
    tenant_id = uuid4()
    email = f"suspended-{uuid4().hex[:8]}@example.com"
    async with db_client.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, plan_code, status) VALUES ($1, $2, $3, $4, $5)",
            tenant_id, "Suspension Tenant", f"suspension-{uuid4().hex[:8]}", "pro", "active"
        )
        await conn.execute(
            "INSERT INTO users (id, phone_number, email, name) VALUES ($1, $2, $3, $4)",
            user_id, f"auth:{user_id}", email, "Suspension User"
        )
        await conn.execute(
            "INSERT INTO user_tenants (id, user_id, tenant_id, role) VALUES ($1, $2, $3, $4)",
            uuid4(), user_id, tenant_id, "tenant_admin"
        )
    access_token = sign_access_token(user_id, email, tenant_id, "tenant_admin")
    active_response = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert active_response.status_code == 200
    async with db_client.acquire() as conn:
        await conn.execute("UPDATE tenants SET status = 'suspended' WHERE id = $1", tenant_id)
    suspended_response = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert suspended_response.status_code == 403
    assert "suspended or inactive" in suspended_response.json()["error"]["message"]


async def test_suspended_tenant_cannot_login_or_refresh(client):
    """Suspension should prevent minting fresh tokens through login or refresh."""
    email = f"suspended-login-{uuid4().hex[:8]}@example.com"
    password = "TestPassword123"
    register_response = await client.post(
        "/api/v1/auth/register",
        json={
            "business_name": "Suspended Login Tenant",
            "email": email,
            "password": password,
            "business_type": "general",
        }
    )
    assert register_response.status_code == 200
    registration = register_response.json()["data"]
    tenant_id = UUID(registration["tenant_id"])
    refresh_token = registration["refresh_token"]
    async with db_client.acquire() as conn:
        await conn.execute("UPDATE tenants SET status = 'suspended' WHERE id = $1", tenant_id)
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_response.status_code == 403
    assert "suspended or inactive" in login_response.json()["error"]["message"]
    refresh_response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_response.status_code == 403
    assert "suspended or inactive" in refresh_response.json()["error"]["message"]


class FakeRedisClient:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)

    async def incr(self, key):
        val = int(self.store.get(key, 0)) + 1
        self.store[key] = str(val)
        return val

    async def expire(self, key, seconds):
        return True

    async def aclose(self):
        pass

    def pipeline(self):
        return FakeRedisPipeline(self)


class FakeRedisPipeline:
    def __init__(self, client):
        self.client = client
        self.cmds = []

    async def incr(self, key):
        self.cmds.append(("incr", key))
        return self

    async def expire(self, key, seconds):
        self.cmds.append(("expire", key))
        return self

    async def execute(self):
        for cmd, key in self.cmds:
            if cmd == "incr":
                val = int(self.client.store.get(key, 0)) + 1
                self.client.store[key] = str(val)
        return []


async def test_postgres_login_rate_limiting(client, monkeypatch):
    """Verify that email login attempts are rate limited."""
    # Setup Fake Redis
    fake_redis = FakeRedisClient()
    monkeypatch.setattr("redis.asyncio.from_url", lambda *args, **kwargs: fake_redis)
    email = f"auth-rate-{uuid4().hex[:8]}@example.com"
    password = "CorrectPassword123"
    # Register first
    await client.post(
        "/api/v1/auth/register",
        json={
            "business_name": "Rate Limit Test Tenant",
            "email": email,
            "password": password,
        }
    )
    # Make 5 failed login attempts
    for _ in range(5):
        resp = await client.post(
            "/api/v1/auth/login",
            json={
                "email": email,
                "password": "WrongPassword1",
            }
        )
        assert resp.status_code == 401
    # Subsequent attempts must trigger 429 Too Many Requests
    rate_limit_resp = await client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "WrongPassword1",
        }
    )
    assert rate_limit_resp.status_code == 429
    assert "Too many" in rate_limit_resp.json()["error"]["message"]
