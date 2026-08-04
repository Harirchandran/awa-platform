from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.domains.events.handlers import handle_review_requested
from backend.domains.reviews.repository import ReviewRepository
from backend.domains.reviews.service import ReviewService
from backend.domains.reviews.tool_handlers import execute_reviews_tool


pytestmark = pytest.mark.anyio


class ReviewDeliveryAuthority:
    def __init__(self, *, channel="awa_chat"):
        self.tenant_id = uuid4()
        self.event_id = uuid4()
        self.review_id = uuid4()
        self.customer_id = uuid4()
        self.conversation_id = uuid4()
        self.channel = channel
        self.saved = {}
        self.queued = {}
        self.published = []

    @property
    def event(self):
        return {
            "id": self.event_id,
            "tenant_id": self.tenant_id,
            "event_type": "review.requested",
            "aggregate_type": "review_request",
            "aggregate_id": self.review_id,
            "conversation_id": uuid4(),
            "customer_id": uuid4(),
            "payload": {"phone": "+15550000000", "message": "untrusted"},
        }

    async def get_review(self, tenant_id, review_id):
        assert (tenant_id, review_id) == (self.tenant_id, self.review_id)
        return {
            "id": self.review_id,
            "tenant_id": self.tenant_id,
            "customer_id": self.customer_id,
            "conversation_id": self.conversation_id,
            "booking_id": None,
            "order_id": None,
        }

    async def get_conversation(self, tenant_id, conversation_id):
        assert (tenant_id, conversation_id) == (self.tenant_id, self.conversation_id)
        return {
            "id": self.conversation_id,
            "customer_id": self.customer_id,
            "channel_type": self.channel,
            "customer_phone": "+15550000000",
        }

    async def save_message(self, **kwargs):
        identity = (kwargs["tenant_id"], kwargs["whatsapp_message_id"])
        if identity in self.saved:
            return {"created": False, "reason": "duplicate"}
        self.saved[identity] = kwargs
        return {"created": True, "id": kwargs["message_id"]}

    async def publish(self, **kwargs):
        assert self.saved
        self.published.append(kwargs)
        return True

    async def enqueue(self, **kwargs):
        key = kwargs["idempotency_key"]
        if key in self.queued:
            existing = dict(self.queued[key])
            existing["metadata"] = {**existing["metadata"], "queue_reused": True}
            return existing
        record = {"id": uuid4(), **kwargs}
        self.queued[key] = record
        return record


def _install_delivery(monkeypatch, authority):
    from backend.db.client import db_client
    from backend.domains.reminders.service import reminder_service
    from backend.domains.reviews.service import review_service

    monkeypatch.setattr(review_service, "get_review_request", authority.get_review)
    monkeypatch.setattr(db_client, "get_conversation", authority.get_conversation)
    monkeypatch.setattr(db_client, "save_message", authority.save_message)
    monkeypatch.setattr(reminder_service, "create_scheduled_message", authority.enqueue)
    monkeypatch.setattr(
        "backend.realtime.public_chat_events.publish_public_chat_message_event",
        authority.publish,
    )


async def test_review_requested_awa_chat_commits_before_sse_and_deduplicates(monkeypatch):
    authority = ReviewDeliveryAuthority()
    _install_delivery(monkeypatch, authority)

    first = await handle_review_requested(authority.event)
    second = await handle_review_requested(authority.event)

    assert first.metadata == {
        "delivery_status": "committed", "delivery_channel": "awa_chat", "deduplicated": False
    }
    assert second.metadata["deduplicated"] is True
    assert len(authority.saved) == 1
    assert len(authority.published) == 1
    saved = next(iter(authority.saved.values()))
    assert saved["message_text"] == (
        "How was your experience? Reply with a rating from 1 to 5, and you can also share feedback."
    )
    assert saved["direction"] == "outbound"
    assert saved["channel_type"] == "awa_chat"
    customer_content = saved["message_text"]
    for forbidden in (
        str(authority.event_id), str(authority.review_id), "+15550000000", "untrusted", "token", "provider"
    ):
        assert forbidden not in customer_content


async def test_review_requested_sse_failure_keeps_committed_message(monkeypatch):
    authority = ReviewDeliveryAuthority()
    _install_delivery(monkeypatch, authority)

    async def fail_publish(**kwargs):
        assert authority.saved
        raise RuntimeError("redis unavailable with secret token")

    monkeypatch.setattr(
        "backend.realtime.public_chat_events.publish_public_chat_message_event", fail_publish
    )
    result = await handle_review_requested(authority.event)
    duplicate = await handle_review_requested(authority.event)
    assert result.status == "processed"
    assert duplicate.metadata["deduplicated"] is True
    assert len(authority.saved) == 1


async def test_review_requested_whatsapp_uses_authoritative_idempotent_queue(monkeypatch):
    authority = ReviewDeliveryAuthority(channel="whatsapp")
    _install_delivery(monkeypatch, authority)

    first = await handle_review_requested(authority.event)
    second = await handle_review_requested(authority.event)

    assert first.metadata == {
        "delivery_status": "queued", "delivery_channel": "whatsapp", "deduplicated": False
    }
    assert second.metadata["deduplicated"] is True
    assert list(authority.queued) == [f"review-request:{authority.event_id}:whatsapp"]
    queued = next(iter(authority.queued.values()))
    assert queued["review_request_id"] == authority.review_id
    assert queued["customer_id"] == authority.customer_id
    assert queued["conversation_id"] == authority.conversation_id


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda event: event.update(id="bad"), "invalid_review_event_identity"),
        (lambda event: event.update(aggregate_type="booking"), "invalid_review_event_authority"),
    ],
)
async def test_review_requested_invalid_identity_never_delivers(monkeypatch, mutate, reason):
    authority = ReviewDeliveryAuthority()
    _install_delivery(monkeypatch, authority)
    event = authority.event
    mutate(event)
    result = await handle_review_requested(event)
    assert result.status == "failed"
    assert result.reason_code == reason
    assert not authority.saved and not authority.queued


async def test_review_requested_missing_or_unsupported_authority_is_retryable(monkeypatch):
    authority = ReviewDeliveryAuthority(channel="email")
    _install_delivery(monkeypatch, authority)
    unsupported = await handle_review_requested(authority.event)
    assert unsupported.status == "failed"
    assert unsupported.reason_code == "unsupported_review_delivery_channel"
    assert not authority.saved and not authority.queued

    async def missing(*args):
        return None

    from backend.domains.reviews.service import review_service
    monkeypatch.setattr(review_service, "get_review_request", missing)
    absent = await handle_review_requested(authority.event)
    assert absent.status == "failed"
    assert absent.reason_code == "review_request_not_found"


async def test_review_requested_delivery_failure_and_cancellation(monkeypatch):
    import asyncio

    authority = ReviewDeliveryAuthority()
    _install_delivery(monkeypatch, authority)

    async def fail_save(**kwargs):
        raise RuntimeError("provider payload phone@example.test")

    from backend.db.client import db_client
    monkeypatch.setattr(db_client, "save_message", fail_save)
    failed = await handle_review_requested(authority.event)
    assert failed.status == "failed"
    assert failed.reason_code == "review_delivery_failed"
    assert "example" not in str(failed.metadata)

    async def cancel_save(**kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(db_client, "save_message", cancel_save)
    with pytest.raises(asyncio.CancelledError):
        await handle_review_requested(authority.event)


class ReviewDatabaseFake:
    def __init__(self, fail=False):
        self.fail = fail
        self.rows = {}
        self.actions = []

    async def get_review_settings(self, tenant_id):
        return {"positive_rating_threshold": 4}

    async def create_review_request(self, **kwargs):
        if self.fail:
            raise RuntimeError("repository unavailable")
        row = {"id": uuid4(), **kwargs}
        self.rows[(kwargs["tenant_id"], row["id"])] = row
        return row

    async def action_review_request(self, tenant_id, review_id, **kwargs):
        if self.fail:
            raise RuntimeError("repository unavailable")
        row = self.rows[(tenant_id, review_id)]
        row.update(
            rating=kwargs.get("rating"),
            feedback_text=kwargs.get("feedback_text"),
            status="public_review_requested",
        )
        self.actions.append((tenant_id, review_id, kwargs["action"]))
        return dict(row)


def _review_context(*, rating=5, feedback="Excellent"):
    tenant_id, customer_id, conversation_id, booking_id = uuid4(), uuid4(), uuid4(), uuid4()
    state = SimpleNamespace(step_data={"rating": rating, "feedback": feedback, "reference": "booking"})
    return SimpleNamespace(
        tenant=SimpleNamespace(id=tenant_id),
        user=SimpleNamespace(id=customer_id),
        session_data=SimpleNamespace(
            conversation_id=conversation_id,
            state=state,
        ),
        booking_id=booking_id,
    )


async def test_reviews_execute_persists_through_service_repository_database_boundary():
    db = ReviewDatabaseFake()
    service = ReviewService(repository=ReviewRepository(db=db), events=None)
    context = _review_context()
    logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None)

    result = await execute_reviews_tool(context, service, logger)

    assert result.success is True
    key = (context.tenant.id, uuid4())
    row = next(row for (tenant_id, _), row in db.rows.items() if tenant_id == context.tenant.id)
    assert row["rating"] == 5
    assert row["feedback_text"] == "Excellent"
    assert row["status"] == "public_review_requested"
    assert row["tenant_id"] == context.tenant.id
    assert row["customer_id"] == context.user.id
    assert row["conversation_id"] == context.session_data.conversation_id
    assert db.actions == [(context.tenant.id, row["id"], "submit_rating")]


async def test_reviews_execute_repository_failure_is_truthful_and_validation_never_persists():
    logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None)
    failing_db = ReviewDatabaseFake(fail=True)
    failing_service = ReviewService(repository=ReviewRepository(db=failing_db), events=None)
    failed = await execute_reviews_tool(_review_context(), failing_service, logger)
    assert failed.success is False
    assert "saved" not in failed.user_message.lower()
    assert not failing_db.rows and not failing_db.actions

    clean_db = ReviewDatabaseFake()
    clean_service = ReviewService(repository=ReviewRepository(db=clean_db), events=None)
    unconfirmed = await execute_reviews_tool(_review_context(rating=6), clean_service, logger)
    assert unconfirmed.success is False
    assert not clean_db.rows and not clean_db.actions
