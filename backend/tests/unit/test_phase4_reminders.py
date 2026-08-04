import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import asyncpg
from fastapi import HTTPException

from backend.api.routes.reminders import create_scheduled_message
from backend.schemas.reminder import ScheduledMessageCreateRequest
from backend.domains.events.handlers import handle_payment_completed

@pytest.mark.anyio
async def test_create_scheduled_message_foreign_key_violation(monkeypatch):
    # Test that ForeignKeyViolationError yields a 400 Bad Request
    mock_req = ScheduledMessageCreateRequest(
        rule_id=None,
        customer_id=uuid4(),
        conversation_id=None,
        booking_id=None,
        order_id=None,
        payment_request_id=None,
        review_request_id=None,
        trigger_type="manual",
        channel="whatsapp",
        message_text="Test message",
        scheduled_for=datetime.now(timezone.utc),
    )

    mock_user = {"tenant_id": str(uuid4()), "user_id": str(uuid4())}

    async def mock_run_db(*args, **kwargs):
        raise asyncpg.ForeignKeyViolationError("insert or update on table violates foreign key")

    monkeypatch.setattr("backend.api.routes.reminders._run_reminder_db", mock_run_db)
    monkeypatch.setattr("backend.api.routes.reminders.require_module_operational_write", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await create_scheduled_message(mock_req, user=mock_user)

    assert exc_info.value.status_code == 400
    assert "Invalid reference: the provided ID does not exist" in exc_info.value.detail

@pytest.mark.anyio
async def test_handle_payment_completed_creates_reminder(monkeypatch):
    # Test that payment completed event creates a scheduled message
    tenant_id = uuid4()
    payment_request_id = uuid4()
    customer_id = uuid4()
    conversation_id = uuid4()

    mock_rules = [
        {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "name": "Payment completed template rule",
            "trigger_type": "after_payment_completed",
            "offset_minutes": 10,
            "channel": "whatsapp",
            "template_text": "Hi {{customer_name}}, thank you for the payment of {{total_amount}}.",
            "is_active": True,
        }
    ]

    mock_customer = {"id": customer_id, "name": "Valued customer"}

    mock_db = MagicMock()
    mock_db.list_reminder_rules = AsyncMock(return_value=mock_rules)

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"amount": 500})
    mock_db.acquire = MagicMock()
    mock_db.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr("backend.db.client.db_client", mock_db)

    mock_cust_service = MagicMock()
    mock_cust_service.get_customer = AsyncMock(return_value=mock_customer)
    monkeypatch.setattr("backend.domains.customers.service.customer_service", mock_cust_service)

    mock_reminder_service = AsyncMock()
    mock_reminder_service.create_scheduled_message = AsyncMock(return_value={"id": uuid4()})
    monkeypatch.setattr("backend.domains.reminders.service.reminder_service", mock_reminder_service)

    event = {
        "id": str(uuid4()),
        "tenant_id": tenant_id,
        "customer_id": customer_id,
        "conversation_id": conversation_id,
        "payload": {
            "payment_request_id": str(payment_request_id),
            "status": "paid",
            "booking_id": None,
            "order_id": None,
        }
    }

    result = await handle_payment_completed(event)
    assert result.status == "processed"
    assert result.metadata["scheduled_count"] == 1

    mock_reminder_service.create_scheduled_message.assert_called_once()
    args, kwargs = mock_reminder_service.create_scheduled_message.call_args
    assert kwargs["tenant_id"] == tenant_id
    assert kwargs["message_text"] == "Hi Valued customer, thank you for the payment of 500."
    assert kwargs["trigger_type"] == "after_payment_completed"
    assert kwargs["channel"] == "whatsapp"
    assert kwargs["customer_id"] == customer_id
    assert kwargs["conversation_id"] == conversation_id
    assert kwargs["payment_request_id"] == payment_request_id

from datetime import time

@pytest.mark.anyio
async def test_dispatch_message_during_quiet_hours_reschedules(monkeypatch):
    from backend.pipeline import reminder_scheduler
    from zoneinfo import ZoneInfo

    tenant_id = uuid4()
    message_id = uuid4()
    rule_id = uuid4()

    mock_db = MagicMock()
    mock_rule = {
        "quiet_hours_start": time(22, 0),
        "quiet_hours_end": time(8, 0)
    }
    mock_db.get_tenant_config = AsyncMock(return_value={"config_json": {"timezone": "Asia/Kolkata"}})

    mock_db.acquire = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=mock_rule)
    mock_db.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(reminder_scheduler, "db_client", mock_db)

    import zoneinfo
    monkeypatch.setattr(zoneinfo, "ZoneInfo", lambda x: timezone(timedelta(hours=5, minutes=30)))

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            dt = datetime(2026, 6, 30, 23, 30, 0)
            if tz:
                return dt.replace(tzinfo=timezone.utc).astimezone(tz)
            return dt

    monkeypatch.setattr(reminder_scheduler, "datetime", FakeDatetime)

    record = {
        "id": message_id,
        "tenant_id": tenant_id,
        "rule_id": rule_id,
        "channel": "email",
        "message_text": "Reschedule me",
    }

    await reminder_scheduler.dispatch_scheduled_message(record)

    mock_conn.execute.assert_called_once()
    args = mock_conn.execute.call_args.args
    query = args[0]

    assert "UPDATE scheduled_messages" in query
    assert "SET scheduled_for" in query

    rescheduled_dt = args[3]
    expected_utc = datetime(2026, 7, 1, 8, 1, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))).astimezone(timezone.utc)
    assert rescheduled_dt == expected_utc

from backend.api.routes.reminders import get_scheduled_message_metrics

@pytest.mark.anyio
async def test_get_scheduled_message_metrics(monkeypatch):
    tenant_id = uuid4()
    mock_db = MagicMock()

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[
        {"status": "scheduled", "count": 5},
        {"status": "snoozed", "count": 2},
        {"status": "failed", "count": 1},
        {"status": "sent", "count": 10},
    ])
    mock_conn.fetchrow = AsyncMock(return_value={"count": 3})

    mock_db.acquire = MagicMock()
    mock_db.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr("backend.api.routes.reminders.db_client", mock_db)

    mock_user = {"tenant_id": str(tenant_id), "user_id": str(uuid4())}

    response = await get_scheduled_message_metrics(user=mock_user)

    assert response["success"] is True
    data = response["data"]
    assert data["scheduled"] == 7
    assert data["due"] == 3
    assert data["failed"] == 1
    assert data["sent"] == 10
    assert data["skipped"] == 0
