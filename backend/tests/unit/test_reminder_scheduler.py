from uuid import uuid4
import pytest
from backend.pipeline import reminder_scheduler
from backend.schemas.canonical_message import ChannelType
class FakeReminderDb:
    def __init__(self):
        self.claimed = []
        self.results = []
        self.saved_messages = []
        self.public_session = {"id": uuid4(), "status": "active"}
    async def claim_due_scheduled_messages(self, **_kwargs):
        return self.claimed
    async def get_public_chat_session_for_conversation(self, _tenant_id, _conversation_id):
        return self.public_session
    async def save_message(self, **kwargs):
        self.saved_messages.append(kwargs)
        return {"created": True}
    async def get_whatsapp_account_for_dispatch(self, _tenant_id, _account_id):
        return None
    async def record_scheduled_message_dispatch_result(self, **kwargs):
        self.results.append(kwargs)
        return {"id": kwargs["message_id"], "status": kwargs["status"]}
@pytest.mark.anyio
async def test_dispatch_email_reminder(monkeypatch):
    fake_db = FakeReminderDb()
    monkeypatch.setattr(reminder_scheduler, "db_client", fake_db)
    tenant_id = uuid4()
    message_id = uuid4()
    await reminder_scheduler.dispatch_scheduled_message({
        "id": message_id,
        "tenant_id": tenant_id,
        "channel": "email",
        "message_text": "Your appointment is tomorrow.",
        "customer_email": "test@example.com",
        "attempt_count": 0,
    })
    assert fake_db.results[0]["status"] == "sent"
    assert fake_db.results[0]["event_type"] == "dispatched"
    assert "email_stub_" in fake_db.results[0]["provider_message_id"]
@pytest.mark.anyio
async def test_dispatch_whatsapp_reminder_without_account_fails_with_retry(monkeypatch):
    fake_db = FakeReminderDb()
    monkeypatch.setattr(reminder_scheduler, "db_client", fake_db)
    await reminder_scheduler.dispatch_scheduled_message({
        "id": uuid4(),
        "tenant_id": uuid4(),
        "conversation_id": uuid4(),
        "customer_phone": "+15550001111",
        "channel": ChannelType.WHATSAPP.value,
        "message_text": "Payment reminder.",
        "attempt_count": 0,
        "metadata": {},
    })
    assert fake_db.saved_messages == []
    assert fake_db.results[0]["status"] == "failed"
    assert fake_db.results[0]["event_type"] == "dispatch_failed"
    assert fake_db.results[0]["retry_at"] is not None
    assert "tenant WhatsApp account" in fake_db.results[0]["last_error"]
@pytest.mark.anyio
async def test_dispatch_due_reminders_once_claims_and_skips_manual(monkeypatch):
    fake_db = FakeReminderDb()
    fake_db.claimed = [{
        "id": uuid4(),
        "tenant_id": uuid4(),
        "channel": "manual",
        "message_text": "Call customer.",
        "attempt_count": 0,
    }]
    monkeypatch.setattr(reminder_scheduler, "db_client", fake_db)
    count = await reminder_scheduler.dispatch_due_reminders_once()
    assert count == 1
    assert fake_db.results[0]["status"] == "skipped"
    assert fake_db.results[0]["event_type"] == "manual_skipped"

from datetime import date, time

class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, exc_type, exc, tb):
        return False

@pytest.mark.anyio
async def test_dispatch_whatsapp_reminder_outside_24h_window_with_template_components(monkeypatch):
    fake_db = FakeReminderDb()
    monkeypatch.setattr(reminder_scheduler, "db_client", fake_db)

    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    booking_id = uuid4()

    from unittest.mock import AsyncMock
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value="Asha")
    mock_conn.fetchrow = AsyncMock(return_value={
        "service_name": "Dental Cleaning",
        "booking_date": date(2026, 6, 28),
        "booking_time": time(10, 0)
    })
    fake_db.acquire = lambda: FakeAcquire(mock_conn)

    fake_db.get_whatsapp_account_for_dispatch = AsyncMock(return_value={
        "phone_number_id": "12345",
        "access_token": "token123"
    })
    from datetime import datetime, timezone, timedelta
    fake_db.get_conversation = AsyncMock(return_value={
        "last_message_at": datetime.now(timezone.utc) - timedelta(hours=48)
    })
    fake_db.get_tenant_config = AsyncMock(return_value={
        "config_json": {
            "modules": {
                "reminders": {
                    "whatsapp_templates": {
                        "before_booking": "booking_reminder_template"
                    }
                }
            }
        }
    })

    mock_send = AsyncMock(return_value={"success": True, "message_id": "meta_msg_id"})
    monkeypatch.setattr(reminder_scheduler.whatsapp_client, "send_template_message", mock_send)

    await reminder_scheduler.dispatch_scheduled_message({
        "id": uuid4(),
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "customer_id": customer_id,
        "booking_id": booking_id,
        "customer_phone": "+15550001111",
        "channel": ChannelType.WHATSAPP.value,
        "message_text": "Your Dental Cleaning is on June 28 at 10:00 AM.",
        "trigger_type": "before_booking",
        "attempt_count": 0,
        "metadata": {"whatsapp_account_id": str(uuid4())},
    })

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["template_name"] == "booking_reminder_template"
    assert kwargs["to_phone"] == "+15550001111"

    components = kwargs["components"]
    assert len(components) == 1
    assert components[0]["type"] == "body"
    params = components[0]["parameters"]
    assert params[0]["text"] == "Asha"
    assert params[1]["text"] == "Dental Cleaning"
    assert "June 28, 2026" in params[2]["text"]
