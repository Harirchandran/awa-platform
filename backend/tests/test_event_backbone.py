from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from backend.domains.events.dispatcher import EventDispatcher
from backend.domains.events.models import BusinessEventCreate, EventHandlerResult
from backend.domains.events.repository import _json_safe
from backend.domains.events.service import EventService
from backend.domains.leads.service import LeadService
from backend.domains.payments.service import PaymentService
from backend.domains.reminders.service import ReminderService
from backend.domains.reviews.service import ReviewService


pytestmark = pytest.mark.anyio


class FakeEventRepository:
    def __init__(self):
        self.events = []
        self.created = []

    async def create_event(self, event):
        row = {
            "id": uuid4(),
            "tenant_id": event.tenant_id,
            "event_type": event.event_type,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "source_domain": event.source_domain,
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id,
            "conversation_id": event.conversation_id,
            "customer_id": event.customer_id,
            "payload": _json_safe(event.payload),
            "status": "pending",
            "attempts": 0,
            "max_attempts": event.max_attempts,
        }
        self.created.append(event)
        self.events.append(row)
        return row

    async def list_events(self, tenant_id, status=None, event_type=None, limit=50):
        rows = [event for event in self.events if event["tenant_id"] == tenant_id]
        if status:
            rows = [event for event in rows if event["status"] == status]
        if event_type:
            rows = [event for event in rows if event["event_type"] == event_type]
        return rows[:limit]

    async def claim_pending_events(self, limit=25):
        rows = [
            event for event in self.events
            if event["status"] in {"pending", "failed"} and event["attempts"] < event["max_attempts"]
        ][:limit]
        for event in rows:
            event["status"] = "processing"
            event["attempts"] += 1
        return rows

    async def mark_processed(self, tenant_id, event_id):
        return await self._mark(tenant_id, event_id, "processed")

    async def mark_skipped(self, tenant_id, event_id, reason_code):
        row = await self._mark(tenant_id, event_id, "skipped")
        if row:
            row["last_error"] = reason_code
        return row

    async def mark_failed(self, tenant_id, event_id, error, retry_delay_seconds=60):
        for event in self.events:
            if event["tenant_id"] == tenant_id and event["id"] == event_id and event["status"] == "processing":
                event["last_error"] = error
                event["status"] = "failed" if event["attempts"] >= event["max_attempts"] else "pending"
                return event
        return None

    async def _mark(self, tenant_id, event_id, status):
        for event in self.events:
            if event["tenant_id"] == tenant_id and event["id"] == event_id and event["status"] == "processing":
                event["status"] = status
                event["processed_at"] = datetime.now(timezone.utc)
                return event
        return None


async def test_event_service_create_list_claim_process_fail_and_tenant_scope():
    tenant_a = uuid4()
    tenant_b = uuid4()
    repo = FakeEventRepository()
    service = EventService(repository=repo)
    created = await service.emit(BusinessEventCreate(
        tenant_id=tenant_a,
        event_type="lead.created",
        aggregate_type="lead",
        aggregate_id=uuid4(),
        source_domain="leads",
        payload={"seen_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
    ))
    await service.emit(BusinessEventCreate(
        tenant_id=tenant_b,
        event_type="lead.created",
        aggregate_type="lead",
        source_domain="leads",
    ))

    assert created["tenant_id"] == tenant_a
    assert created["payload"]["seen_at"].startswith("2026-01-01")
    assert len(await service.list_events(tenant_a)) == 1
    assert len(await service.list_events(tenant_b)) == 1

    claimed = await service.claim_pending_events(limit=10)
    assert len(claimed) == 2
    assert claimed[0]["attempts"] == 1
    await service.mark_processed(tenant_a, created["id"])
    assert (await service.list_events(tenant_a))[0]["status"] == "processed"

    failed_event = [event for event in claimed if event["tenant_id"] == tenant_b][0]
    await service.mark_failed(tenant_b, failed_event["id"], "temporary issue")
    assert (await service.list_events(tenant_b))[0]["status"] == "pending"


async def test_event_service_rejects_unsupported_event_type():
    repo = FakeEventRepository()
    service = EventService(repository=repo)
    created = await service.emit(BusinessEventCreate(
        tenant_id=uuid4(),
        event_type="unknown.created",
        aggregate_type="unknown",
        source_domain="tests",
    ))
    assert created is None
    assert repo.events == []


async def test_dispatcher_processes_skips_retries_and_avoids_duplicate_processing():
    tenant_id = uuid4()
    repo = FakeEventRepository()
    service = EventService(repository=repo)
    processed = await service.emit(BusinessEventCreate(
        tenant_id=tenant_id,
        event_type="lead.created",
        aggregate_type="lead",
        source_domain="leads",
    ))
    skipped = await service.emit(BusinessEventCreate(
        tenant_id=tenant_id,
        event_type="payment.completed",
        aggregate_type="payment_request",
        source_domain="payments",
    ))
    failing = await service.emit(BusinessEventCreate(
        tenant_id=tenant_id,
        event_type="review.submitted",
        aggregate_type="review_request",
        source_domain="reviews",
        max_attempts=2,
    ))

    calls = {"fail": 0}

    async def fail_once(event):
        calls["fail"] += 1
        if calls["fail"] == 1:
            raise RuntimeError("handler failed")
        return EventHandlerResult.processed()

    dispatcher = EventDispatcher(
        service=service,
        handlers={
            "lead.created": lambda event: _processed(),
            "payment.completed": lambda event: _skipped("not_safe_yet"),
            "review.submitted": fail_once,
        },
    )

    assert await dispatcher.dispatch_once(limit=10) == 3
    states = {event["id"]: event["status"] for event in repo.events}
    assert states[processed["id"]] == "processed"
    assert states[skipped["id"]] == "skipped"
    assert states[failing["id"]] == "pending"

    assert await dispatcher.dispatch_once(limit=10) == 1
    states = {event["id"]: event["status"] for event in repo.events}
    assert states[failing["id"]] == "processed"
    assert await dispatcher.dispatch_once(limit=10) == 0


async def _processed():
    return EventHandlerResult.processed()


async def _skipped(reason):
    return EventHandlerResult.skipped(reason)


async def test_lead_capture_emits_lead_created():
    tenant_id = uuid4()
    lead_id = uuid4()
    customer_id = uuid4()
    conversation_id = uuid4()
    events = SimpleNamespace(emit=AsyncMock())

    class _FakeTx:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None

    class _FakeConn:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        def transaction(self):
            return _FakeTx()

    db = SimpleNamespace(acquire=lambda: _FakeConn())
    event_repo = SimpleNamespace(create_lead_created_event=AsyncMock(return_value={"id": uuid4()}))
    repo = SimpleNamespace(capture_lead=AsyncMock(return_value={
        "id": lead_id,
        "source_channel": "awa_chat",
        "interest": "cleaning",
        "created": True,
    }))
    service = LeadService(
        repository=repo,
        events=events,
        event_repository=event_repo,
        db=db,
    )
    await service.capture_lead(tenant_id, customer_id, conversation_id, {"interest": "cleaning"})

    events.emit.assert_not_awaited()

    event_repo.create_lead_created_event.assert_awaited_once()
    event = event_repo.create_lead_created_event.await_args.args[0]
    assert event.event_type == "lead.created"
    assert event.tenant_id == tenant_id
    assert event.aggregate_id == lead_id
    assert event.customer_id == customer_id
    assert event.conversation_id == conversation_id


async def test_payment_events_requested_and_completed_only_when_paid():
    tenant_id = uuid4()
    payment_id = uuid4()
    events = SimpleNamespace(emit=AsyncMock())
    repo = SimpleNamespace(
        create_payment_request=AsyncMock(return_value={"id": payment_id, "status": "draft", "provider": "manual"}),
        action_payment_request=AsyncMock(side_effect=[
            {"id": payment_id, "status": "sent"},
            {"id": payment_id, "status": "paid", "customer_id": uuid4(), "conversation_id": uuid4()},
        ]),
    )
    service = PaymentService(repository=repo, events=events)
    await service.create_payment_request(tenant_id, amount=100, idempotency_key="pay-1")
    await service.action_payment_request(tenant_id, payment_id, action="send")
    await service.action_payment_request(tenant_id, payment_id, action="mark_paid")
    emitted = [call.args[0].event_type for call in events.emit.await_args_list]
    assert emitted == ["payment.requested", "payment.completed"]


async def test_reminder_events_scheduled_and_sent():
    tenant_id = uuid4()
    message_id = uuid4()
    events = SimpleNamespace(emit=AsyncMock())
    repo = SimpleNamespace(
        create_scheduled_message=AsyncMock(return_value={"id": message_id, "status": "scheduled"}),
        record_scheduled_message_dispatch_result=AsyncMock(return_value={"id": message_id, "status": "sent"}),
    )
    service = ReminderService(repository=repo, events=events)
    await service.create_scheduled_message(
        tenant_id,
        datetime.now(timezone.utc),
        "Follow up",
        channel="whatsapp",
    )
    await service.record_scheduled_message_dispatch_result(
        tenant_id,
        message_id,
        status="sent",
        event_type="dispatched",
    )
    emitted = [call.args[0].event_type for call in events.emit.await_args_list]
    assert emitted == ["reminder.scheduled", "reminder.sent"]


async def test_review_events_requested_submitted_and_negative():
    tenant_id = uuid4()
    review_id = uuid4()
    events = SimpleNamespace(emit=AsyncMock())
    repo = SimpleNamespace(
        create_review_request=AsyncMock(return_value={"id": review_id, "status": "draft"}),
        get_review_settings=AsyncMock(return_value={"positive_rating_threshold": 4}),
        action_review_request=AsyncMock(return_value={
            "id": review_id,
            "status": "negative_feedback_escalated",
            "rating": 2,
            "customer_id": uuid4(),
            "conversation_id": uuid4(),
        }),
    )
    service = ReviewService(repository=repo, events=events)
    await service.create_review_request(tenant_id, customer_id=uuid4())
    await service.action_review_request(tenant_id, review_id, action="submit_rating", rating=2)
    emitted = [call.args[0].event_type for call in events.emit.await_args_list]
    assert emitted == ["review.requested", "review.submitted", "review.negative"]


async def test_booking_completed_review_automation_respects_policy_and_delay(monkeypatch):
    from backend.domains.events.handlers import handle_booking_completed
    from backend.domains.reviews.service import review_service

    now = datetime.now(timezone.utc)
    event = {
        "id": uuid4(), "tenant_id": uuid4(), "event_type": "booking.completed",
        "aggregate_type": "booking", "aggregate_id": uuid4(),
        "conversation_id": uuid4(), "customer_id": uuid4(), "created_at": now,
        "payload": {"source_channel": "awa_chat"},
    }
    settings = AsyncMock(return_value={"ask_after_booking_completed": True, "ask_delay_minutes": 30})
    create = AsyncMock(return_value={"id": uuid4(), "status": "draft"})
    monkeypatch.setattr(review_service, "get_review_settings", settings)
    monkeypatch.setattr(review_service, "create_review_request", create)

    result = await handle_booking_completed(event)
    assert result.status == "processed"
    kwargs = create.await_args.kwargs
    assert kwargs["booking_id"] == event["aggregate_id"]
    assert kwargs["idempotency_key"] == f"booking-completed:{event['aggregate_id']}"
    assert kwargs["event_causation_id"] == str(event["id"])
    assert kwargs["event_available_at"] == now + timedelta(minutes=30)

    settings.return_value = {"ask_after_booking_completed": False, "ask_delay_minutes": 0}
    create.reset_mock()
    disabled = await handle_booking_completed(event)
    assert disabled.status == "processed"
    create.assert_not_awaited()
