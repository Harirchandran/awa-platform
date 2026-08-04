from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.domains.leads.repository import LeadRepository
from backend.domains.leads.service import LeadService
from backend.domains.payments.service import PaymentService
from backend.domains.reminders.service import ReminderService
from backend.domains.reviews.service import ReviewService


pytestmark = pytest.mark.anyio


async def test_lead_service_requires_tenant_for_list_get_and_patch():
    tenant_a = uuid4()
    tenant_b_lead = uuid4()
    repo = SimpleNamespace(
        list_leads=AsyncMock(return_value={"items": [], "next_cursor": None}),
        get_lead=AsyncMock(return_value=None),
        update_lead_status=AsyncMock(return_value=False),
    )
    service = LeadService(repository=repo)

    assert await service.list_leads(tenant_a, status="new") == {"items": [], "next_cursor": None}
    assert await service.get_lead(tenant_a, tenant_b_lead) is None
    assert await service.update_lead_status(tenant_a, tenant_b_lead, "contacted") is False

    repo.list_leads.assert_awaited_once_with(tenant_a, cursor=None, limit=20, status="new", search=None)
    repo.get_lead.assert_awaited_once_with(tenant_a, tenant_b_lead)
    repo.update_lead_status.assert_awaited_once_with(tenant_a, tenant_b_lead, "contacted")


async def test_lead_repository_duplicate_update_filters_by_tenant():
    tenant_id = uuid4()
    customer_id = uuid4()
    conversation_id = uuid4()
    lead_id = uuid4()
    executed_sql = []

    class FakeTx:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeConn:
        async def fetchrow(self, query, *args):
            if "FROM customers" in query:
                return {"name": "Unknown", "phone_number": "Unknown", "email": None, "source_channel": "awa_chat", "metadata": {}}
            if "FROM leads" in query:
                return {"id": lead_id}
            return None

        async def execute(self, query, *args):
            executed_sql.append((query, args))
            return "UPDATE 1"

        def transaction(self):
            return FakeTx()

    class Acquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    repo = LeadRepository(db=SimpleNamespace(acquire=lambda: Acquire()))
    result = await repo.capture_lead(
        tenant_id,
        customer_id,
        conversation_id,
        {"name": "Asha", "phone": "+919999999999", "interest": "cleaning", "step_data": {"name": "Asha"}},
    )

    assert result["id"] == lead_id
    assert result["created"] is False
    lead_update = [call for call in executed_sql if "UPDATE leads SET" in call[0]][0]
    assert "WHERE tenant_id = $6 AND id = $7 AND status = 'new'" in lead_update[0]
    assert lead_update[1][-2:] == (tenant_id, lead_id)


async def test_payment_service_create_is_tenant_scoped_and_idempotent():
    tenant_id = uuid4()
    payment_id = uuid4()
    repo = SimpleNamespace(
        create_payment_request=AsyncMock(return_value={"id": payment_id, "status": "draft", "provider": "manual"}),
        list_payment_requests=AsyncMock(return_value={"items": [], "next_cursor": None}),
        get_payment_request=AsyncMock(return_value=None),
        action_payment_request=AsyncMock(return_value=None),
    )
    service = PaymentService(repository=repo)

    created = await service.create_payment_request(tenant_id, amount=250, idempotency_key="same-key")
    listed = await service.list_payment_requests(tenant_id)
    other_tenant_read = await service.get_payment_request(tenant_id, uuid4())
    mark_paid = await service.action_payment_request(tenant_id, payment_id, action="mark_paid")

    assert created["id"] == payment_id
    assert created["status"] == "draft"
    assert listed == {"items": [], "next_cursor": None}
    assert other_tenant_read is None
    assert mark_paid is None
    repo.create_payment_request.assert_awaited_once_with(
        tenant_id=tenant_id,
        amount=250,
        currency="INR",
        payment_type="custom_payment",
        provider="manual",
        status="draft",
        customer_id=None,
        conversation_id=None,
        booking_id=None,
        order_id=None,
        payment_url=None,
        provider_payment_link_id=None,
        provider_order_id=None,
        expires_at=None,
        metadata=None,
        idempotency_key="same-key",
        created_by=None,
    )


async def test_reminder_service_create_claim_and_dispatch_are_tenant_safe():
    tenant_id = uuid4()
    message_id = uuid4()
    scheduled_for = datetime.now(timezone.utc) + timedelta(minutes=5)
    repo = SimpleNamespace(
        create_scheduled_message=AsyncMock(return_value={"id": message_id, "status": "scheduled"}),
        list_scheduled_messages=AsyncMock(return_value={"items": [], "next_cursor": None}),
        get_scheduled_message=AsyncMock(return_value=None),
        claim_due_scheduled_messages=AsyncMock(return_value=[{"id": message_id, "tenant_id": tenant_id}]),
        record_scheduled_message_dispatch_result=AsyncMock(return_value={"id": message_id, "status": "sent"}),
    )
    service = ReminderService(repository=repo)

    invalid = await service.create_scheduled_message(tenant_id, scheduled_for, "Call back", channel="pager")
    created = await service.create_scheduled_message(tenant_id, scheduled_for, "Call back", channel="whatsapp")
    listed = await service.list_scheduled_messages(tenant_id)
    other_tenant_read = await service.get_scheduled_message(tenant_id, uuid4())
    due = await service.claim_due_scheduled_messages(limit=10, lock_ttl_seconds=300, max_attempts=3)
    recorded = await service.record_scheduled_message_dispatch_result(
        tenant_id,
        message_id,
        status="sent",
        event_type="dispatched",
    )

    assert invalid is None
    assert created["id"] == message_id
    assert listed == {"items": [], "next_cursor": None}
    assert other_tenant_read is None
    assert due[0]["tenant_id"] == tenant_id
    assert recorded["status"] == "sent"
    repo.record_scheduled_message_dispatch_result.assert_awaited_once_with(
        tenant_id,
        message_id,
        status="sent",
        event_type="dispatched",
    )


async def test_review_service_preserves_rating_paths_and_tenant_scope():
    tenant_id = uuid4()
    review_id = uuid4()
    repo = SimpleNamespace(
        create_review_request=AsyncMock(return_value={"id": review_id, "status": "responded"}),
        action_review_request=AsyncMock(return_value={"id": review_id, "status": "negative_feedback_escalated", "rating": 2}),
        list_review_requests=AsyncMock(return_value={"items": [], "next_cursor": None}),
        get_review_request=AsyncMock(return_value=None),
    )
    service = ReviewService(repository=repo)

    created = await service.create_review_request(tenant_id, status="responded", customer_id=uuid4())
    low_rating = await service.action_review_request(tenant_id, review_id, action="submit_rating", rating=2)
    listed = await service.list_review_requests(tenant_id)
    other_tenant_read = await service.get_review_request(tenant_id, uuid4())

    assert created["id"] == review_id
    assert low_rating["status"] == "negative_feedback_escalated"
    assert listed == {"items": [], "next_cursor": None}
    assert other_tenant_read is None
    repo.create_review_request.assert_awaited_once()
    assert repo.create_review_request.await_args.kwargs["tenant_id"] == tenant_id
    repo.action_review_request.assert_awaited_once_with(tenant_id, review_id, action="submit_rating", rating=2)
