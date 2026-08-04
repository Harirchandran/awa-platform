import pytest
import asyncio
from uuid import uuid4
from datetime import datetime, timezone
from fastapi import HTTPException
from backend.db.client import db_client
from backend.api.routes.auth import refresh_session, RefreshRequest
from backend.core.config import settings

pytestmark = pytest.mark.anyio

async def test_auth_concurrency_refresh_token_race(monkeypatch):
    # Set AUTH_MODE to postgres to bypass the mock mode check
    monkeypatch.setattr(settings, "AUTH_MODE", "postgres")

    # Seed user, tenant, and session
    tenant_id = uuid4()
    user_id = uuid4()
    session_id = uuid4()
    rt_hash = f"hash-{uuid4().hex}"
    email = f"auth-race-{uuid4().hex[:8]}@example.com"

    async with db_client.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, plan_code, status) VALUES ($1, $2, $3, $4, $5)",
            tenant_id, "Auth Race Tenant", f"auth-{uuid4().hex[:8]}", "trial", "trial"
        )
        await conn.execute(
            "INSERT INTO users (id, name, email, phone_number) VALUES ($1, $2, $3, $4)",
            user_id, "Race User", email, f"+1555{uuid4().hex[:7]}"
        )
        await conn.execute(
            "INSERT INTO user_tenants (user_id, tenant_id, role) VALUES ($1, $2, $3)",
            user_id, tenant_id, "tenant_admin"
        )
        await conn.execute(
            """
            INSERT INTO user_sessions (id, user_id, refresh_token_hash, expires_at)
            VALUES ($1, $2, $3, NOW() + INTERVAL '30 days')
            """,
            session_id, user_id, rt_hash
        )

    # Mock hash_refresh_token in security auth
    import backend.security.auth as security_auth

    req = RefreshRequest(refresh_token="my-secret-token")

    original_hash = security_auth.hash_refresh_token
    security_auth.hash_refresh_token = lambda tk: rt_hash if tk == "my-secret-token" else original_hash(tk)

    # Run two refresh requests concurrently
    results = await asyncio.gather(
        refresh_session(req),
        refresh_session(req),
        return_exceptions=True
    )

    security_auth.hash_refresh_token = original_hash

    # One request should succeed, and one should raise HTTPException(401)
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    # Debug print if something fails
    if len(successes) != 1:
        print("Successes:", successes)
        print("Failures:", [(type(f).__name__, getattr(f, "status_code", None), str(f)) for f in failures])

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], HTTPException)
    assert failures[0].status_code == 401
