from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from backend.core.context import ConversationSession, ExecutionContext, PendingAction, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.domains.reminders.tool_definitions import get_reminders_tool_schema
from backend.domains.reminders.tool_handlers import execute_reminders_tool
from backend.domains.reviews.tool_handlers import execute_reviews_tool
from backend.logic.engine.core import _set_runtime_available_modules, _unavailable_active_workflow_response

pytestmark = pytest.mark.anyio


def _config():
    return {
        "timezone": "Asia/Kolkata",
        "modules": {
            "reminders": {
                "enabled": True,
                "fields": [
                    {"key": "scheduled_for", "label": "When", "type": "string", "required": True},
                    {"key": "message_text", "label": "Message", "type": "string", "required": True},
                    {"key": "channel", "label": "Channel", "type": "select", "required": True, "options": ["awa_chat", "manual"]},
                ],
            },
            "reviews": {"enabled": True, "fields": []},
        },
    }


def _context(step_data=None, config=None, current_task="reminders"):
    tenant_id = uuid4()
    state = SessionState(
        workflow_state=ConversationState.EXECUTING,
        step_data=dict(step_data or {}),
        scoped_step_data={current_task: dict(step_data or {})} if step_data else {},
    )
    state.action_ledger.current_task = current_task
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Reminder Tenant", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+15550000000", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module=current_task,
            state=state,
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=config or _config()),
        incoming_message={"text": "confirm", "channel_type": "awa_chat"},
    )


async def test_reminder_past_time_rejected_and_timezone_naive_time_is_preserved_as_tenant_time():
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    service = SimpleNamespace(create_scheduled_message=AsyncMock())
    ctx = _context({"scheduled_for": past, "message_text": "Follow up", "channel": "manual"})

    result = await execute_reminders_tool(ctx, service, MagicMock())

    assert result.success is False
    assert "future" in result.user_message
    service.create_scheduled_message.assert_not_called()

    future_local = "2030-01-01T10:00:00"
    service = SimpleNamespace(create_scheduled_message=AsyncMock(return_value={"id": "rem-1", "status": "scheduled"}))
    ctx = _context({"scheduled_for": future_local, "message_text": "Follow up", "channel": "manual"})

    result = await execute_reminders_tool(ctx, service, MagicMock())

    assert result.success is True
    scheduled_for = service.create_scheduled_message.await_args.kwargs["scheduled_for"]
    assert scheduled_for.isoformat() == "2030-01-01T04:30:00+00:00"


async def test_reminder_channel_guard_and_schema_options_use_existing_config():
    schema = get_reminders_tool_schema(_config())
    assert schema["properties"]["channel"]["enum"] == ["manual"]
    assert "awa_chat" not in str(schema["properties"]["channel"]).lower()

    service = SimpleNamespace(create_scheduled_message=AsyncMock())
    ctx = _context(
        {
            "scheduled_for": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "message_text": "Follow up",
            "channel": "email",
        }
    )

    result = await execute_reminders_tool(ctx, service, MagicMock())

    assert result.success is False
    assert "not connected" in result.user_message
    service.create_scheduled_message.assert_not_called()


async def test_missing_or_awa_chat_channel_fails_before_persistence():
    scheduled_for = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    service = SimpleNamespace(create_scheduled_message=AsyncMock())

    missing = await execute_reminders_tool(
        _context({"scheduled_for": scheduled_for, "message_text": "Follow up"}),
        service,
        MagicMock(),
    )
    awa_chat = await execute_reminders_tool(
        _context({"scheduled_for": scheduled_for, "message_text": "Follow up", "channel": "awa_chat"}),
        service,
        MagicMock(),
    )

    assert missing.success is False
    assert missing.validation_errors == {"channel": "Please choose a reminder delivery channel: manual staff follow-up."}
    assert awa_chat.success is False
    assert "cannot deliver scheduled reminders" in awa_chat.user_message
    service.create_scheduled_message.assert_not_called()


async def test_review_rating_validation_and_review_settings_threshold_are_backend_owned():
    service = SimpleNamespace(
        get_review_settings=AsyncMock(return_value={"positive_rating_threshold": 5, "public_review_url": "https://reviews.example"}),
        create_review_request=AsyncMock(return_value={"id": "review-1", "metadata": {}}),
        update_review_request=AsyncMock(return_value={"id": "review-1", "metadata": {}}),
        action_review_request=AsyncMock(
            return_value={"id": "review-1", "status": "negative_feedback_escalated", "public_review_url": "https://reviews.example"}
        ),
    )
    invalid = _context({"rating": 6, "feedback": "Great"}, current_task="reviews")

    result = await execute_reviews_tool(invalid, service, MagicMock())

    assert result.success is False
    assert "1 to 5" in result.user_message
    service.create_review_request.assert_not_called()

    low_by_config = _context({"rating": 4, "feedback": "Needs follow up"}, current_task="reviews")
    result = await execute_reviews_tool(low_by_config, service, MagicMock())

    assert result.success is True
    assert result.data["public_review_url"] is None
    service.action_review_request.assert_awaited()
    assert service.action_review_request.await_args.kwargs["action"] == "escalate_negative"


def test_stale_pending_reminder_or_review_after_module_disabled_hides_confirm():
    for tool_key, available in [
        ("reminders.execute", {"reviews"}),
        ("reviews.execute", {"reminders"}),
    ]:
        ctx = _context(current_task=tool_key.split(".", 1)[0])
        ctx.session_data.state.action_ledger.pending_action = PendingAction(
            tool_key=tool_key,
            args={"rating": 5, "message_text": "Follow up"},
            summary="Please review this request.",
            requires_confirmation=True,
        )
        _set_runtime_available_modules(ctx, available)

        result = _unavailable_active_workflow_response(ctx, available)

        assert result is not None
        assert "currently unavailable" in result.response_text
        assert "Confirm" not in result.quick_replies
        assert ctx.session_data.state.action_ledger.pending_action is None
