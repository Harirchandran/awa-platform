from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.domains.reminders.tool_definitions import get_reminders_tool_schema
from backend.modules.reminders_module import reminders_module
pytestmark = pytest.mark.anyio


def test_default_reminder_schema_requires_only_deliverable_concierge_channels():
    schema = get_reminders_tool_schema({"modules": {"reminders": {"enabled": True, "fields": []}}})

    assert schema["properties"]["channel"]["enum"] == ["whatsapp", "manual"]
    assert "channel" in schema["required"]
    assert "awa_chat" not in str(schema).lower()


def _context(step_data, *, phone_number="+919999999999"):
    tenant_id = uuid4()
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Cafe", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number=phone_number, name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module="reminders",
            state=SessionState(workflow_state=ConversationState.EXECUTING, step_data=step_data),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={"business_name": "Cafe"}),
        incoming_message={"text": "confirm"},
    )
async def test_reminder_tool_rejects_unwired_sms_channel(monkeypatch):
    db = SimpleNamespace(create_scheduled_message=AsyncMock())
    monkeypatch.setattr("backend.modules.reminders_module.reminder_service", db)
    context = _context(
        {
            "scheduled_for": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "message_text": "Your appointment is tomorrow.",
            "channel": "sms",
        }
    )
    result = await reminders_module.execute(context)
    assert result.success is False
    assert "not connected" in result.user_message
    db.create_scheduled_message.assert_not_called()
async def test_reminder_tool_persists_supported_whatsapp_channel(monkeypatch):
    message_id = uuid4()
    db = SimpleNamespace(
        create_scheduled_message=AsyncMock(return_value={"id": message_id, "status": "scheduled"})
    )
    monkeypatch.setattr("backend.modules.reminders_module.reminder_service", db)
    scheduled_for = datetime.now(timezone.utc) + timedelta(hours=1)
    context = _context(
        {
            "scheduled_for": scheduled_for.isoformat(),
            "message_text": "Your appointment is tomorrow.",
            "channel": "whatsapp",
            "reference": "booking-1",
        }
    )
    result = await reminders_module.execute(context)
    assert result.success is True
    call = db.create_scheduled_message.call_args.kwargs
    assert call["channel"] == "whatsapp"
    assert call["customer_id"] == context.user.id
    assert call["conversation_id"] == context.session_data.conversation_id


async def test_reminder_tool_rejects_whatsapp_without_customer_phone(monkeypatch):
    db = SimpleNamespace(create_scheduled_message=AsyncMock())
    monkeypatch.setattr("backend.modules.reminders_module.reminder_service", db)
    context = _context(
        {
            "scheduled_for": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "message_text": "Your appointment is tomorrow.",
            "channel": "whatsapp",
        },
        phone_number="",
    )

    result = await reminders_module.execute(context)

    assert result.success is False
    assert result.validation_errors == {"channel": "WhatsApp reminders need a customer phone number."}
    db.create_scheduled_message.assert_not_called()
