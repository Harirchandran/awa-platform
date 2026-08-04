from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.api.dependencies import get_current_user
from backend.api import health as health_api
from backend.domains.events.dispatcher import EventDispatcher
from backend.domains.events.models import EventHandlerResult
from backend.domains.events.service import EventService
from backend.pipeline import event_dispatcher as dispatcher_worker


pytestmark = pytest.mark.anyio


class OpsFakeEventRepository:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.events = [
            self._event("lead.created", "leads", "pending", now - timedelta(minutes=3)),
            self._event("payment.completed", "payments", "pending", now - timedelta(minutes=2)),
            self._event("review.submitted", "reviews", "pending", now - timedelta(minutes=1), max_attempts=2),
            self._event("handoff.created", "support", "processing", now),
            self._event("booking.created", "bookings", "failed", now - timedelta(minutes=5), attempts=3, max_attempts=3),
            self._event("order.created", "orders", "skipped", now - timedelta(minutes=4), last_error="order_followup_not_enabled_phase5"),
        ]

    def _event(self, event_type, source_domain, status, created_at, attempts=0, max_attempts=3, last_error=None):
        return {
            "id": uuid4(),
            "tenant_id": uuid4(),
            "event_type": event_type,
            "aggregate_type": event_type.split(".")[0],
            "aggregate_id": uuid4(),
            "source_domain": source_domain,
            "correlation_id": None,
            "causation_id": None,
            "conversation_id": uuid4(),
            "customer_id": uuid4(),
            "payload": {"secret": "should-not-be-returned"},
            "status": status,
            "attempts": attempts,
            "max_attempts": max_attempts,
            "available_at": created_at,
            "processed_at": None,
            "last_error": last_error,
            "created_at": created_at,
            "updated_at": created_at,
        }

    async def claim_pending_events(self, limit=25):
        claimed = []
        for event in self.events:
            if event["status"] == "pending" and event["attempts"] < event["max_attempts"]:
                event["status"] = "processing"
                event["attempts"] += 1
                claimed.append(event)
                if len(claimed) >= limit:
                    break
        return claimed

    async def mark_processed(self, tenant_id, event_id):
        return self._mark(tenant_id, event_id, "processed")

    async def mark_skipped(self, tenant_id, event_id, reason_code):
        row = self._mark(tenant_id, event_id, "skipped")
        if row:
            row["last_error"] = reason_code
        return row

    async def mark_failed(self, tenant_id, event_id, error, retry_delay_seconds=60):
        for event in self.events:
            if event["tenant_id"] == tenant_id and event["id"] == event_id and event["status"] == "processing":
                event["last_error"] = error
                if event["attempts"] >= event["max_attempts"]:
                    event["status"] = "failed"
                else:
                    event["status"] = "pending"
                    event["available_at"] = datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds)
                return event
        return None

    async def event_health_summary(self):
        counts = {status: 0 for status in ("pending", "processing", "processed", "failed", "skipped")}
        for event in self.events:
            counts[event["status"]] += 1
        pending = [event for event in self.events if event["status"] == "pending"]
        failed = [event for event in self.events if event["status"] == "failed"]
        now = datetime.now(timezone.utc)
        return {
            "counts": counts,
            "retryable_failed_count": len([event for event in failed if event["attempts"] < event["max_attempts"]]),
            "retrying_count": len([event for event in pending if event["attempts"] > 0]),
            "oldest_pending_event_age_seconds": int((now - min(event["created_at"] for event in pending)).total_seconds()) if pending else 0,
            "oldest_failed_event_age_seconds": int((now - min(event["updated_at"] for event in failed)).total_seconds()) if failed else 0,
            "events_by_type": {event["event_type"]: 1 for event in self.events},
            "events_by_source_domain": {event["source_domain"]: 1 for event in self.events},
        }

    async def list_failed_events(self, tenant_id=None, limit=50):
        return [self._without_payload(event) for event in self.events if event["status"] == "failed"][:limit]

    async def list_skipped_events(self, tenant_id=None, limit=50):
        return [self._without_payload(event) for event in self.events if event["status"] == "skipped"][:limit]

    def _mark(self, tenant_id, event_id, status):
        for event in self.events:
            if event["tenant_id"] == tenant_id and event["id"] == event_id and event["status"] == "processing":
                event["status"] = status
                return event
        return None

    @staticmethod
    def _without_payload(event):
        return {key: value for key, value in event.items() if key != "payload"}


async def test_dispatcher_counts_processed_skipped_retry_and_terminal_failure():
    repo = OpsFakeEventRepository()
    service = EventService(repository=repo)
    calls = {"fail": 0}

    async def fail_then_retry(event):
        calls["fail"] += 1
        raise RuntimeError("temporary secret=hidden")

    dispatcher = EventDispatcher(
        service=service,
        handlers={
            "lead.created": lambda event: _processed(),
            "payment.completed": lambda event: _skipped("payment_completion_followup_not_enabled_phase4"),
            "review.submitted": fail_then_retry,
        },
    )

    assert await dispatcher.dispatch_once(limit=10) == 3
    by_type = {event["event_type"]: event for event in repo.events}
    assert by_type["lead.created"]["status"] == "processed"
    assert by_type["payment.completed"]["status"] == "skipped"
    assert by_type["review.submitted"]["status"] == "pending"
    assert by_type["handoff.created"]["status"] == "processing"

    assert await dispatcher.dispatch_once(limit=10) == 1
    assert by_type["review.submitted"]["status"] == "failed"
    assert await dispatcher.dispatch_once(limit=10) == 0


async def _processed():
    return EventHandlerResult.processed()


async def _skipped(reason):
    return EventHandlerResult.skipped(reason)


async def test_event_health_summary_counts_and_hides_payloads():
    repo = OpsFakeEventRepository()
    service = EventService(repository=repo)

    summary = await service.event_health_summary()
    failed = await service.list_failed_events(limit=10)
    skipped = await service.list_skipped_events(limit=10)

    assert summary["counts"]["pending"] == 3
    assert summary["counts"]["failed"] == 1
    assert summary["counts"]["skipped"] == 1
    assert summary["oldest_pending_event_age_seconds"] >= 0
    assert summary["events_by_type"]["lead.created"] == 1
    assert summary["events_by_source_domain"]["payments"] == 1
    assert "payload" not in failed[0]
    assert "payload" not in skipped[0]
    assert summary["retryable_failed_count"] == 0


async def test_ops_health_requires_super_admin_and_returns_event_summary(monkeypatch):
    async def tenant_admin_user():
        return {"user_id": uuid4(), "tenant_id": uuid4(), "role": "tenant_admin"}

    async def super_admin_user():
        return {"user_id": uuid4(), "tenant_id": uuid4(), "role": "super_admin"}

    async def fake_health(include_errors=False):
        return {
            "status": "healthy",
            "queue_depth": 0,
            "processing_depth": 0,
            "dlq_depth": 0,
            "queue_alert": "ok",
            "dlq_alert": "ok",
            "services": {"redis": "healthy", "database": "healthy", "worker": "healthy", "api": "healthy"},
            "workers": {},
            "worker_last_seen_at": None,
            "worker_lag_seconds": None,
            "version": "1.0.0",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    async def fake_events():
        return {
            "counts": {"pending": 1, "processing": 0, "processed": 2, "failed": 1, "skipped": 1},
            "payloads_included": False,
            "dispatcher": {"status": "healthy"},
        }

    monkeypatch.setattr(health_api, "_collect_health_snapshot", fake_health)
    monkeypatch.setattr(health_api, "_collect_event_ops_snapshot", fake_events)

    app.dependency_overrides[get_current_user] = tenant_admin_user
    try:
        denied = TestClient(app).get("/api/v1/health/ops")
        assert denied.status_code == 403
    finally:
        app.dependency_overrides.clear()

    app.dependency_overrides[get_current_user] = super_admin_user
    try:
        response = TestClient(app).get("/api/v1/health/ops")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["events"]["counts"]["failed"] == 1
        assert data["events"]["payloads_included"] is False
    finally:
        app.dependency_overrides.clear()


async def test_dispatcher_loop_one_iteration_records_heartbeat_and_survives_errors(monkeypatch):
    writes = {}

    class FakeRedis:
        async def set(self, key, value, ex=None, px=None, nx=False, **kwargs):
            writes[key] = {"value": value, "ex": ex, "px": px, "nx": nx}

        async def exists(self, key):
            return False

        async def aclose(self):
            writes["closed"] = True

    async def fake_from_url(*args, **kwargs):
        return FakeRedis()

    calls = {"count": 0}

    async def fake_dispatch(limit):
        calls["count"] += 1
        dispatcher_worker._shutdown_event.set()
        if calls["count"] == 1:
            raise RuntimeError("loop failure without secret payload")
        return 0

    monkeypatch.setattr(dispatcher_worker.redis, "from_url", lambda *args, **kwargs: FakeRedis())
    monkeypatch.setattr(dispatcher_worker, "dispatch_events_once", fake_dispatch)
    dispatcher_worker._shutdown_event.clear()

    await dispatcher_worker.event_dispatcher_loop()

    assert dispatcher_worker.EVENT_DISPATCHER_HEARTBEAT_KEY in writes
    assert dispatcher_worker.EVENT_DISPATCHER_LAST_ERROR_KEY in writes
    assert writes["closed"] is True

@pytest.mark.anyio
async def test_dispatcher_terminalization(monkeypatch):
    from unittest.mock import AsyncMock
    from backend.db.client import PublicChatTerminalFailureOutcome, db_client
    event_id, tenant_id, conversation_id, message_id = uuid4(), uuid4(), uuid4(), uuid4()
    event = {"id": event_id, "tenant_id": tenant_id, "conversation_id": conversation_id,
             "event_type": "conversation.turn.received", "aggregate_type": "message", "aggregate_id": message_id,
             "attempts": 3, "max_attempts": 3,
             "payload": {"public_chat_session_id": str(uuid4()), "channel_account_id": str(uuid4()),
                         "conversation_sequence_number": 1, "config_version": 1,
                         "security_disposition": "none", "trace_id": str(uuid4()),
                         "message": {"message_id": str(uuid4())}}}
    service = SimpleNamespace(claim_exhausted_conversation_turn_events=AsyncMock(return_value=[event]), claim_pending_events=AsyncMock(return_value=[]), mark_processed=AsyncMock(), mark_failed=AsyncMock(), defer_malformed_exhausted_event=AsyncMock())
    publish = AsyncMock(return_value=True)
    monkeypatch.setattr("backend.realtime.public_chat_events.publish_public_chat_terminal_failure", publish)
    dispatcher = EventDispatcher(service=service, handlers={})
    counts = []
    for code in ("terminal_created", "terminal_already_exists", "normal_response_exists"):
        publish.reset_mock()
        outcome = PublicChatTerminalFailureOutcome(code, None if code == "normal_response_exists" else uuid4(), None if code == "normal_response_exists" else "terminal", None if code == "normal_response_exists" else datetime.now(timezone.utc), message_id, event_id, conversation_id, "completed" if code == "normal_response_exists" else "dlq", None if code == "normal_response_exists" else "processing_retries_exhausted")
        finalize = AsyncMock(return_value=outcome)
        monkeypatch.setattr(db_client, "finalize_turn_terminal_failure", finalize)
        counts.append(await dispatcher.dispatch_once(limit=1))
        finalize.assert_awaited_once_with(tenant_id=tenant_id, conversation_id=conversation_id, inbound_message_id=message_id, event_id=event_id, retry_count=3)
        if code == "normal_response_exists": publish.assert_not_awaited()
        else: publish.assert_awaited_once()
        service.mark_failed.assert_not_awaited()
    assert counts == [1, 1, 1]

@pytest.mark.anyio
async def test_dispatcher_failure_recovery(monkeypatch):
    from unittest.mock import AsyncMock
    from backend.db.client import db_client
    event = {"id": uuid4(), "tenant_id": uuid4(), "conversation_id": uuid4(), "attempts": 3, "max_attempts": 3,
             "event_type": "conversation.turn.received", "aggregate_type": "message", "aggregate_id": uuid4(), "payload": {}}
    service = SimpleNamespace(claim_exhausted_conversation_turn_events=AsyncMock(return_value=[event]), claim_pending_events=AsyncMock(return_value=[]), mark_processed=AsyncMock(), mark_failed=AsyncMock(), defer_malformed_exhausted_event=AsyncMock())
    monkeypatch.setattr(db_client, "finalize_turn_terminal_failure", AsyncMock(side_effect=RuntimeError("db_failure")))
    before = event["attempts"]
    count = await EventDispatcher(service=service, handlers={}).dispatch_once(limit=1)
    service.mark_failed.assert_not_awaited()
    service.mark_processed.assert_not_awaited()
    assert event["attempts"] == before
    assert count == 1

@pytest.mark.anyio
async def test_batch_counting(monkeypatch):
    from unittest.mock import AsyncMock
    ordinary = [{"id": uuid4(), "tenant_id": uuid4(), "event_type": "ordinary", "attempts": 0} for _ in range(2)]
    exhausted = [{"id": uuid4(), "tenant_id": uuid4(), "conversation_id": uuid4(), "attempts": 3, "payload": {"source_type": "legacy"}} for _ in range(3)]
    service = SimpleNamespace(claim_exhausted_conversation_turn_events=AsyncMock(return_value=exhausted), claim_pending_events=AsyncMock(return_value=ordinary), mark_processed=AsyncMock(), mark_failed=AsyncMock(), defer_malformed_exhausted_event=AsyncMock())
    dispatcher = EventDispatcher(service=service, handlers={"ordinary": AsyncMock(return_value=EventHandlerResult.processed())})
    count = await dispatcher.dispatch_once(limit=5)
    service.claim_pending_events.assert_awaited_once_with(limit=5)
    service.claim_exhausted_conversation_turn_events.assert_awaited_once_with(limit=3)
    assert service.defer_malformed_exhausted_event.await_count == 3
    assert count == 5


@pytest.mark.anyio
async def test_malformed_exhausted_event_is_backed_off_without_acknowledgement(monkeypatch):
    from unittest.mock import AsyncMock
    from backend.db.client import db_client

    event_id, tenant_id = uuid4(), uuid4()
    malformed = {
        "id": event_id,
        "tenant_id": tenant_id,
        "conversation_id": uuid4(),
        "event_type": "conversation.turn.received",
        "aggregate_type": "message",
        "aggregate_id": None,
        "attempts": 3,
        "max_attempts": 3,
        "payload": {"customer_data": "must-not-be-used"},
    }
    service = SimpleNamespace(
        claim_pending_events=AsyncMock(return_value=[]),
        claim_exhausted_conversation_turn_events=AsyncMock(return_value=[malformed]),
        defer_malformed_exhausted_event=AsyncMock(),
        mark_processed=AsyncMock(),
        mark_failed=AsyncMock(),
    )
    finalize = AsyncMock()
    monkeypatch.setattr(db_client, "finalize_turn_terminal_failure", finalize)

    assert await EventDispatcher(service=service, handlers={}).dispatch_once(limit=1) == 1
    service.defer_malformed_exhausted_event.assert_awaited_once_with(
        tenant_id, event_id, retry_delay_seconds=3600
    )
    service.mark_processed.assert_not_awaited()
    service.mark_failed.assert_not_awaited()
    finalize.assert_not_awaited()
