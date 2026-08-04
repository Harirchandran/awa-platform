from datetime import datetime, timezone
import pytest
from backend.api.health import health_check, operational_health_check
from backend.api.app import app
from backend.pipeline.burst_worker import BURST_WORKER_HEARTBEAT_KEY
from backend.pipeline.reminder_scheduler import REMINDER_SCHEDULER_HEARTBEAT_KEY
from backend.pipeline.reliable_queue import DLQ_KEY, WORKER_HEARTBEAT_KEY
pytestmark = pytest.mark.anyio
class FakeAcquire:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        return False
    async def fetchval(self, query):
        return 1
class FakeRedis:
    def __init__(self, heartbeats, queue_depth=0, processing_depth=0, dlq_depth=0):
        self.heartbeats = heartbeats
        self.queue_depth = queue_depth
        self.processing_depth = processing_depth
        self.dlq_depth = dlq_depth
    async def ping(self):
        return True
    async def llen(self, key):
        if key == "message_queue":
            return self.queue_depth
        if key == "message_queue:processing":
            return self.processing_depth
        if key == DLQ_KEY:
            return self.dlq_depth
        return 0
    async def get(self, key):
        return self.heartbeats.get(key)
    async def aclose(self):
        return None
async def test_health_reports_worker_healthy_when_heartbeat_exists(monkeypatch):
    heartbeat = datetime.now(timezone.utc).isoformat()
    heartbeats = {
        WORKER_HEARTBEAT_KEY: heartbeat,
        BURST_WORKER_HEARTBEAT_KEY: heartbeat,
        REMINDER_SCHEDULER_HEARTBEAT_KEY: heartbeat,
    }
    monkeypatch.setattr("backend.api.health.get_redis_client", lambda *args, **kwargs: FakeRedis(heartbeats))
    monkeypatch.setattr("backend.api.health.db_client.acquire", lambda: FakeAcquire())
    response = await health_check()
    data = response["data"]
    assert data["status"] == "healthy"
    assert data["services"]["worker"] == "healthy"
    assert data["workers"]["main"]["status"] == "healthy"
    assert data["workers"]["burst"]["status"] == "healthy"
    assert data["workers"]["reminder_scheduler"]["status"] == "healthy"
    assert data["worker_last_seen_at"] == heartbeat
async def test_health_reports_worker_degraded_when_heartbeat_missing(monkeypatch):
    monkeypatch.setattr("backend.api.health.get_redis_client", lambda *args, **kwargs: FakeRedis({}))
    monkeypatch.setattr("backend.api.health.db_client.acquire", lambda: FakeAcquire())
    response = await health_check()
    data = response["data"]
    assert data["status"] == "degraded"
    assert data["services"]["worker"] == "unhealthy"
    assert data["worker_last_seen_at"] is None
async def test_production_health_redacts_operational_details(monkeypatch):
    heartbeat = datetime.now(timezone.utc).isoformat()
    heartbeats = {
        WORKER_HEARTBEAT_KEY: heartbeat,
        BURST_WORKER_HEARTBEAT_KEY: heartbeat,
        REMINDER_SCHEDULER_HEARTBEAT_KEY: heartbeat,
    }
    monkeypatch.setattr("backend.api.health.settings.ENV", "production")
    monkeypatch.setattr("backend.api.health.get_redis_client", lambda *args, **kwargs: FakeRedis(heartbeats))
    monkeypatch.setattr("backend.api.health.db_client.acquire", lambda: FakeAcquire())
    response = await health_check()
    data = response["data"]
    assert data["status"] == "healthy"
    assert "queue_depth" not in data
    assert "workers" not in data
    assert data["services"]["redis"] == "healthy"
async def test_operational_health_includes_safe_worker_details(monkeypatch):
    heartbeat = datetime.now(timezone.utc).isoformat()
    heartbeats = {
        WORKER_HEARTBEAT_KEY: heartbeat,
        BURST_WORKER_HEARTBEAT_KEY: heartbeat,
        REMINDER_SCHEDULER_HEARTBEAT_KEY: heartbeat,
    }
    monkeypatch.setattr(
        "backend.api.health.get_redis_client",
        lambda *args, **kwargs: FakeRedis(heartbeats, queue_depth=12, processing_depth=1),
    )
    monkeypatch.setattr("backend.api.health.db_client.acquire", lambda: FakeAcquire())
    response = await operational_health_check(
        admin={"user_id": "00000000-0000-0000-0000-000000000001", "role": "super_admin"}
    )
    data = response["data"]
    assert data["queue_alert"] == "warning"
    assert data["workers"]["burst"]["last_seen_at"] == heartbeat
    assert data["operator"]["role"] == "super_admin"
async def test_operational_health_reports_dlq_separately_from_active_queue(monkeypatch):
    heartbeat = datetime.now(timezone.utc).isoformat()
    heartbeats = {
        WORKER_HEARTBEAT_KEY: heartbeat,
        BURST_WORKER_HEARTBEAT_KEY: heartbeat,
        REMINDER_SCHEDULER_HEARTBEAT_KEY: heartbeat,
    }
    monkeypatch.setattr(
        "backend.api.health.get_redis_client",
        lambda *args, **kwargs: FakeRedis(heartbeats, dlq_depth=1),
    )
    monkeypatch.setattr("backend.api.health.db_client.acquire", lambda: FakeAcquire())
    response = await operational_health_check(
        admin={"user_id": "00000000-0000-0000-0000-000000000001", "role": "super_admin"}
    )
    data = response["data"]
    assert data["dlq_depth"] == 1
    assert data["queue_alert"] == "ok"
    assert data["dlq_alert"] == "warning"
async def test_operational_health_route_is_registered_under_api_v1():
    def get_all_paths(app_or_router, prefix=""):
        paths = set()
        routes = getattr(app_or_router, "routes", [])
        for route in routes:
            if type(route).__name__ == "_IncludedRouter":
                sub_prefix = prefix + (route.include_context.prefix or "")
                paths.update(get_all_paths(route.original_router, sub_prefix))
            elif hasattr(route, "path"):
                paths.add(prefix + route.path)
        return paths

    paths = get_all_paths(app)
    assert "/api/v1/health/ops" in paths
