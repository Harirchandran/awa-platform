import pytest
from uuid import uuid4
from datetime import date, time
from unittest.mock import AsyncMock, MagicMock, patch
from backend.core.context import ExecutionContext, Tenant, User, ConversationSession, SessionState, TenantConfig
from backend.modules.booking_update_module import booking_update_module
from backend.tools.registry import ToolRegistry
from backend.billing.usage_guard import UsageGuard

pytestmark = pytest.mark.anyio

class FakeUsageDb:
    async def get_tenant_usage_summary(self, tenant_id):
        return {
            "today_message_count": 0,
            "month_message_count": 0,
            "month_ai_tokens_used": 0,
            "month_credits_used": 0,
            "month_tool_action_count": 0,
        }
    async def get_subscription_plan_by_code(self, plan_code):
        return {"plan_code": plan_code, "limits_json": {"allowed_modules": ["booking"]}}

def make_context(step_data, *, trusted_whatsapp=True):
    tenant_id = uuid4()
    user_id = uuid4()
    conversation_id = uuid4()

    tenant = Tenant(id=tenant_id, name="Test Tenant", status="active", plan_code="trial", ai_mode="standard")
    user = User(id=user_id, phone_number="+1234567890", name="Test Customer")

    state = SessionState(step_data=step_data)
    session = ConversationSession(
        id=uuid4(),
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        state_json=state.model_dump()
    )
    session.state = state

    config = TenantConfig(
        tenant_id=tenant_id,
        version=1,
        config_data={
            "timezone": "UTC",
            "modules": {
                "booking": {"enabled": True}
            }
        }
    )

    incoming_message = {"channel_type": "whatsapp"}
    if trusted_whatsapp:
        whatsapp_account_id = uuid4()
        incoming_message.update({
            "source_type": "webhook",
            "tenant_id": str(tenant_id),
            "conversation_id": str(conversation_id),
            "channel_account_id": str(whatsapp_account_id),
            "whatsapp_account_id": str(whatsapp_account_id),
        })

    return ExecutionContext(
        tenant=tenant,
        user=user,
        session_data=session,
        config=config,
        incoming_message=incoming_message,
    )

async def test_booking_update_registration():
    registry = ToolRegistry(guard=UsageGuard(FakeUsageDb()))
    registry.register_module(booking_update_module)

    tool = registry.get_tool("booking.update")
    assert tool is not None
    assert tool.llm_name == "booking_update_execute"
    assert tool.module_name == "booking"

async def test_booking_update_cancel_execution():
    registry = ToolRegistry(guard=UsageGuard(FakeUsageDb()))
    registry.register_module(booking_update_module)
    registry._write_audit = AsyncMock()

    booking_id = uuid4()
    context = make_context({
        "booking_id": str(booking_id),
        "action": "cancel"
    })

    mock_service = AsyncMock()
    mock_service.cancel_booking = AsyncMock(return_value=True)

    with patch("backend.modules.booking_update_module.booking_service", mock_service):
        # 1. First execution should request confirmation
        prepared = await registry.execute_tool("booking.update", context)
        assert prepared.success is False
        assert prepared.denied_reason == "confirmation_required:booking"
        assert prepared.data["required_next_step"] == "confirm"

        # 2. Simulate semantic decision marking the ledger-confirmed action
        context.incoming_message["text"] = "Confirm"
        assert context.session_data.state.confirm_pending_action(
            "booking.update",
            context.session_data.state.get_task_step_data("booking"),
        )
        committed = await registry.execute_tool("booking.update", context)
        assert committed.success is True
        assert "cancelled" in committed.user_message

        mock_service.cancel_booking.assert_awaited_once_with(
            tenant_id=context.tenant.id,
            booking_id=booking_id
        )


async def test_booking_update_raw_whatsapp_channel_does_not_bypass_assurance():
    registry = ToolRegistry(guard=UsageGuard(FakeUsageDb()))
    registry.register_module(booking_update_module)
    registry._write_audit = AsyncMock()
    context = make_context({"booking_id": str(uuid4()), "action": "cancel"}, trusted_whatsapp=False)
    mock_service = AsyncMock()
    mock_service.cancel_booking = AsyncMock(return_value=True)

    with patch("backend.modules.booking_update_module.booking_service", mock_service):
        prepared = await registry.execute_tool("booking.update", context)
        assert prepared.denied_reason == "confirmation_required:booking"
        context.incoming_message["text"] = "Confirm"
        assert context.session_data.state.confirm_pending_action(
            "booking.update", context.session_data.state.get_task_step_data("booking"),
        )
        denied = await registry.execute_tool("booking.update", context)

    assert denied.success is False
    assert denied.denied_reason == "customer_verification_required:booking"
    mock_service.cancel_booking.assert_not_awaited()

async def test_booking_update_reschedule_execution():
    registry = ToolRegistry(guard=UsageGuard(FakeUsageDb()))
    registry.register_module(booking_update_module)
    registry._write_audit = AsyncMock()

    booking_id = uuid4()
    context = make_context({
        "booking_id": str(booking_id),
        "action": "reschedule",
        "new_date": "tomorrow",
        "new_time": "10:00"
    })

    mock_service = AsyncMock()
    mock_service.update_booking = AsyncMock(return_value=True)

    with patch("backend.modules.booking_update_module.booking_service", mock_service):
        # 1. First execution should request confirmation
        prepared = await registry.execute_tool("booking.update", context)
        assert prepared.success is False
        assert prepared.denied_reason == "confirmation_required:booking"
        assert prepared.data["required_next_step"] == "confirm"

        # 2. Simulate semantic decision marking the ledger-confirmed action
        context.incoming_message["text"] = "Confirm"
        assert context.session_data.state.confirm_pending_action(
            "booking.update",
            context.session_data.state.get_task_step_data("booking"),
        )
        committed = await registry.execute_tool("booking.update", context)
        assert committed.success is True
        assert "rescheduled" in committed.user_message

        mock_service.update_booking.assert_awaited_once()
        call_kwargs = mock_service.update_booking.call_args.kwargs
        assert call_kwargs["tenant_id"] == context.tenant.id
        assert call_kwargs["booking_id"] == booking_id
        assert call_kwargs["booking_date"] is not None
        assert call_kwargs["booking_time"] == "10:00:00"
