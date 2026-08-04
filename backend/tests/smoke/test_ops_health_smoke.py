import pytest
from datetime import datetime, timezone
from uuid import uuid4

from backend.api.app import app
from backend.api.dependencies import get_current_user
from backend.pipeline.burst_worker import BURST_WORKER_HEARTBEAT_KEY
from backend.pipeline.reminder_scheduler import REMINDER_SCHEDULER_HEARTBEAT_KEY
from backend.pipeline.reliable_queue import WORKER_HEARTBEAT_KEY

pytestmark = pytest.mark.anyio

async def test_ops_health_full_flow(api_client, mock_redis_globally):
    # 1. Test unauthenticated /api/v1/health (public endpoint)
    # This does not require auth, returns 200
    resp = await api_client.get("/api/v1/health")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # Check that it returns basic liveness summary
    assert "status" in data
    assert "services" in data
    assert "redis" in data["services"]
    assert "database" in data["services"]

    # 2. Test authenticated /api/v1/health/ops (requires super admin)
    # First, test access denied for tenant admin
    async def mock_tenant_admin():
        return {
            "user_id": uuid4(),
            "tenant_id": uuid4(),
            "role": "tenant_admin"
        }
    app.dependency_overrides[get_current_user] = mock_tenant_admin
    try:
        resp = await api_client.get("/api/v1/health/ops")
        assert resp.status_code == 403, resp.text
    finally:
        app.dependency_overrides.clear()

    # Next, test access allowed for super admin
    super_admin_id = uuid4()
    async def mock_super_admin():
        return {
            "user_id": super_admin_id,
            "tenant_id": uuid4(),
            "role": "super_admin"
        }
    app.dependency_overrides[get_current_user] = mock_super_admin

    try:
        # Write healthy heartbeats into mock Redis
        now_iso = datetime.now(timezone.utc).isoformat()
        await mock_redis_globally.set(WORKER_HEARTBEAT_KEY, now_iso)
        await mock_redis_globally.set(BURST_WORKER_HEARTBEAT_KEY, now_iso)
        await mock_redis_globally.set(REMINDER_SCHEDULER_HEARTBEAT_KEY, now_iso)

        resp = await api_client.get("/api/v1/health/ops")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]

        # Verify status is healthy
        assert data["status"] == "healthy"
        assert data["services"]["worker"] == "healthy"
        assert data["workers"]["main"]["status"] == "healthy"
        assert data["workers"]["burst"]["status"] == "healthy"
        assert data["workers"]["reminder_scheduler"]["status"] == "healthy"
        assert data["operator"]["user_id"] == str(super_admin_id)

        # Decay/remove heartbeats to simulate degraded worker status
        await mock_redis_globally.delete(WORKER_HEARTBEAT_KEY)

        resp = await api_client.get("/api/v1/health/ops")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        # Status should now be degraded because main worker is missing heartbeat
        assert data["status"] == "degraded"
        assert data["services"]["worker"] == "unhealthy"
        assert data["workers"]["main"]["status"] == "unhealthy"

    finally:
        app.dependency_overrides.clear()
