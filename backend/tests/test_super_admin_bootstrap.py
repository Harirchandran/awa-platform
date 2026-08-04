import pytest
import os
import sys
from uuid import UUID, uuid4
import json
import httpx
from unittest.mock import patch, MagicMock

from backend.core.config import settings
from backend.db.client import db_client
from backend.api.app import app
from backend.cli.bootstrap_super_admin import (
    bootstrap_provisioning,
    main,
    CANONICAL_USER_ID,
    CANONICAL_TENANT_ID,
    CANONICAL_EMAIL,
    CANONICAL_SLUG
)
from backend.security.auth import verify_jwt

pytestmark = pytest.mark.anyio

@pytest.fixture(autouse=True)
async def setup_test_context(monkeypatch):
    """Force AUTH_MODE=postgres and database pool connection."""
    monkeypatch.setattr(settings, "AUTH_MODE", "postgres")
    monkeypatch.setattr(settings, "JWT_SECRET", "test_jwt_secret_value_for_unit_testing")
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", "test_jwt_secret_value_for_unit_testing")
    await db_client.connect()

    # Clean up any existing canonical credentials and audit logs before each test
    async with db_client.acquire() as conn:
        await conn.execute("DELETE FROM user_credentials WHERE user_id = $1", CANONICAL_USER_ID)
        await conn.execute("DELETE FROM audit_logs WHERE actor_id = $1 AND action = 'super_admin_bootstrapped'", CANONICAL_USER_ID)
        # Restore the default seeded email just in case a test modified it
        await conn.execute("UPDATE users SET email = $1 WHERE id = $2", CANONICAL_EMAIL, CANONICAL_USER_ID)

    yield

    # Clean up after each test
    async with db_client.acquire() as conn:
        await conn.execute("DELETE FROM user_credentials WHERE user_id = $1", CANONICAL_USER_ID)
        await conn.execute("DELETE FROM audit_logs WHERE actor_id = $1 AND action = 'super_admin_bootstrapped'", CANONICAL_USER_ID)
        await conn.execute("UPDATE users SET email = $1 WHERE id = $2", CANONICAL_EMAIL, CANONICAL_USER_ID)
    await db_client.close()

@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac

async def test_bootstrap_disabled(monkeypatch):
    """Verify command fails closed when not enabled."""
    monkeypatch.setenv("AWA_SUPER_ADMIN_BOOTSTRAP_ENABLED", "false")
    with patch("sys.argv", ["bootstrap_super_admin"]):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 1

async def test_bootstrap_auth_mode_gating(monkeypatch):
    """Verify command fails if auth mode is not postgres."""
    monkeypatch.setenv("AWA_SUPER_ADMIN_BOOTSTRAP_ENABLED", "true")
    monkeypatch.setattr(settings, "AUTH_MODE", "supabase")
    with patch("sys.argv", ["bootstrap_super_admin"]):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 1

async def test_password_policy_rejection(monkeypatch):
    """Verify weak passwords are rejected."""
    monkeypatch.setenv("AWA_SUPER_ADMIN_BOOTSTRAP_ENABLED", "true")
    monkeypatch.setenv("AWA_SUPER_ADMIN_BOOTSTRAP_PASSWORD", "weak") # too short, no number
    with patch("sys.argv", ["bootstrap_super_admin"]):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 1

async def test_fresh_deployment_success(monkeypatch, client):
    """Verify successful bootstrap on fresh deployment and subsequent login + JWT validation."""
    monkeypatch.setenv("AWA_SUPER_ADMIN_BOOTSTRAP_ENABLED", "true")
    monkeypatch.setenv("AWA_SUPER_ADMIN_BOOTSTRAP_PASSWORD", "SecurePass123!")

    # Run bootstrap logic
    result = await bootstrap_provisioning("SecurePass123!")
    assert result["status"] == "success"
    assert result["super_admin_user_id"] == str(CANONICAL_USER_ID)
    assert result["platform_tenant_id"] == str(CANONICAL_TENANT_ID)
    assert result["role"] == "super_admin"
    assert result["audit_event_id"] is not None

    # Verify credentials stored in DB
    async with db_client.acquire() as conn:
        cred = await conn.fetchrow("SELECT * FROM user_credentials WHERE user_id = $1", CANONICAL_USER_ID)
        assert cred is not None
        assert cred["password_algorithm"] == "bcrypt"

        # Verify audit log
        audit = await conn.fetchrow("SELECT * FROM audit_logs WHERE actor_id = $1 AND action = 'super_admin_bootstrapped'", CANONICAL_USER_ID)
        assert audit is not None
        assert audit["tenant_id"] == CANONICAL_TENANT_ID
        assert audit["id"] == UUID(result["audit_event_id"])

        # Verify no secret in audit details
        details = json.loads(audit["details"])
        assert "password" not in details
        assert "password_hash" not in details

    # Disable mock auth explicitly to verify live/production authentication path
    monkeypatch.setattr(settings, "ENABLE_MOCK_AUTH", False)

    # Attempt normal login using the new credential
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={
            "email": CANONICAL_EMAIL,
            "password": "SecurePass123!"
        }
    )
    print("LOGIN RESPONSE TEXT:", login_resp.text)
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    assert login_data["success"] is True

    access_token = login_data["data"]["access_token"]
    assert login_data["data"]["role"] == "super_admin"
    assert login_data["data"]["tenant_id"] == str(CANONICAL_TENANT_ID)
    assert login_data["data"]["user_id"] == str(CANONICAL_USER_ID)

    # Decode and verify JWT claims
    claims = verify_jwt(access_token)
    assert claims is not None
    assert claims["sub"] == str(CANONICAL_USER_ID)
    assert claims["app_metadata"]["role"] == "super_admin"
    assert claims["app_metadata"]["tenant_id"] == str(CANONICAL_TENANT_ID)

async def test_idempotency_and_no_overwrite(monkeypatch):
    """Verify running the bootstrap twice is idempotent and does not overwrite existing password."""
    monkeypatch.setenv("AWA_SUPER_ADMIN_BOOTSTRAP_ENABLED", "true")

    # First run
    res1 = await bootstrap_provisioning("SecurePass123!")
    assert res1["status"] == "success"

    async with db_client.acquire() as conn:
        hash1 = await conn.fetchval("SELECT password_hash FROM user_credentials WHERE user_id = $1", CANONICAL_USER_ID)

    # Second run with different password
    res2 = await bootstrap_provisioning("AnotherSecurePass999")
    assert res2["status"] == "already_provisioned"
    assert res2["super_admin_user_id"] == str(CANONICAL_USER_ID)
    assert res2["audit_event_id"] is None

    # Verify password hash did NOT change
    async with db_client.acquire() as conn:
        hash2 = await conn.fetchval("SELECT password_hash FROM user_credentials WHERE user_id = $1", CANONICAL_USER_ID)
        assert hash1 == hash2

async def test_transaction_rollback_on_partial_identity_state(monkeypatch):
    """Verify partial or contradictory identity state fails and rolls back atomically."""
    monkeypatch.setenv("AWA_SUPER_ADMIN_BOOTSTRAP_ENABLED", "true")

    # Insert a contradictory state: super-admin user exists but has a different email
    async with db_client.acquire() as conn:
        # Check if tenant exists, if not create it
        exists = await conn.fetchval("SELECT 1 FROM tenants WHERE id = $1", CANONICAL_TENANT_ID)
        if not exists:
            await conn.execute(
                "INSERT INTO tenants (id, name, slug, plan_code, status) VALUES ($1, 'SmileDent Dental Clinic', $2, 'ENTERPRISE', 'active')",
                CANONICAL_TENANT_ID, CANONICAL_SLUG
            )
        # Update user's email to a contradictory value
        await conn.execute(
            "UPDATE users SET email = 'contradictory@email.com' WHERE id = $1",
            CANONICAL_USER_ID
        )

    # Attempt bootstrap - should raise RuntimeError and not insert credentials
    with pytest.raises(RuntimeError) as excinfo:
        await bootstrap_provisioning("SecurePass123!")
    assert "contradictory email" in str(excinfo.value)
