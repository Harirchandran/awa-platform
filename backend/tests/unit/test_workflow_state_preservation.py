from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from backend.core.config import settings
from backend.core.context import (
    ConversationSession,
    ExecutionContext,
    PendingAction,
    SessionState,
    Tenant,
    TenantConfig,
    User,
)
from backend.core.states import ConversationState
from backend.logic.engine.core import agentic_engine
from backend.logic.engine.field_extraction import (
    BookingFieldExtraction,
    LeadFieldExtraction,
    OrderFieldExtraction,
    PaymentFieldExtraction,
    ReminderFieldExtraction,
    ReviewFieldExtraction,
    SupportFieldExtraction,
)
from backend.logic.engine.pending_decision import PendingActionDecision
from backend.pipeline.message_router import RoutingDecision, message_router
from backend.tools.registry import ToolExecutionResult

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def explicit_legacy_behavior_runtime(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False)


def _future_date() -> str:
    return (date.today() + timedelta(days=7)).isoformat()


def _config() -> dict:
    return {
        "timezone": "UTC",
        "working_hours": {
            day: {"is_open": True, "open_time": "09:00", "close_time": "17:00"}
            for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        },
        "services": [
            {"id": "service-public", "name": "configured_service", "duration_mins": 30, "price": 10},
        ],
        "modules": {
            "order": {
                "enabled": True,
                "currency": "USD",
                "fulfillment_types": ["pickup", "delivery"],
                "categories": [
                    {
                        "id": "category-public",
                        "name": "configured_category",
                        "items": [
                            {"id": "item-public", "name": "configured_item", "price": 10, "enabled": True},
                        ],
                    }
                ],
            },
            "booking": {
                "enabled": True,
                "fields": [
                    {"key": "service_name", "label": "Service", "type": "string", "required": True},
                    {"key": "preferred_date", "label": "Date", "type": "date", "required": True},
                    {"key": "preferred_time", "label": "Time", "type": "time", "required": True},
                    {"key": "customer_name", "label": "Name", "type": "string", "required": True},
                ],
            },
            "payments": {
                "enabled": True,
                "allowed_currencies": ["USD"],
                "provider": "test_provider",
            },
            "reminders": {"enabled": True},
            "reviews": {"enabled": True},
            "lead_capture": {"enabled": True},
            "support": {"enabled": True},
            "human_handoff": {"enabled": True},
        },
    }


def _context(
    *,
    module: str = "order",
    step_data: dict | None = None,
    text: str = "hours",
    pending_action: PendingAction | None = None,
    task_status: str = "collecting",
) -> ExecutionContext:
    tenant_id = uuid4()
    state = SessionState(
        workflow_state=ConversationState.COLLECTING_DATA,
        step_data=dict(step_data or {}),
        scoped_step_data={module: dict(step_data or {})} if step_data else {},
    )
    state.action_ledger.current_task = module
    state.action_ledger.task_status = task_status
    state.action_ledger.pending_action = pending_action
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="configured_tenant", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+10000000000", name="configured_user"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module=module,
            state=state,
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=_config()),
        incoming_message={"text": text, "channel_type": "awa_chat"},
    )


async def _no_usage(_context):
    return None


async def _no_hydrate(_context):
    return None


async def _prepare_engine(monkeypatch):
    monkeypatch.setattr("backend.logic.engine.core.usage_guard.check_message_allowed", _no_usage)
    monkeypatch.setattr(agentic_engine, "_hydrate_order_catalog", _no_hydrate)

    async def booking_tangent(_context):
        return BookingFieldExtraction(workflow="booking", workflow_action="tangent", confidence=0.95)

    async def lead_tangent(_context):
        return LeadFieldExtraction(workflow="lead_capture", workflow_action="tangent", confidence=0.95)

    async def support_tangent(_context):
        return SupportFieldExtraction(workflow="support", workflow_action="tangent", confidence=0.95)

    async def reminder_tangent(_context):
        return ReminderFieldExtraction(workflow="reminder", workflow_action="tangent", confidence=0.95)

    async def review_tangent(_context):
        return ReviewFieldExtraction(workflow="review", workflow_action="tangent", confidence=0.95)

    async def payment_tangent(_context):
        return PaymentFieldExtraction(workflow="payment", workflow_action="tangent", confidence=0.95)

    monkeypatch.setattr("backend.logic.engine.core.extract_booking_fields", booking_tangent)
    monkeypatch.setattr("backend.logic.engine.core.extract_lead_fields", lead_tangent)
    monkeypatch.setattr("backend.logic.engine.core.extract_support_fields", support_tangent)
    monkeypatch.setattr("backend.logic.engine.core.extract_reminder_fields", reminder_tangent)
    monkeypatch.setattr("backend.logic.engine.core.extract_review_fields", review_tangent)
    monkeypatch.setattr("backend.logic.engine.core.extract_payment_fields", payment_tangent)


async def test_active_order_with_item_quantity_survives_tangent_question(monkeypatch):
    context = _context(module="order", step_data={"item_name": "configured_item", "quantity": 2}, text="hours")

    async def fake_extract(_context):
        return OrderFieldExtraction(workflow="order", workflow_action="tangent", confidence=0.95)

    monkeypatch.setattr("backend.logic.engine.core.extract_order_fields", fake_extract)
    result = await agentic_engine._handle_semantic_order_field_extraction(
        context,
        message_router.route_message(
            "hours",
            context.config.config_data,
            current_task="order",
            current_module="order",
        ),
    )

    assert result is not None
    assert context.session_data.state.get_task_step_data("order") == {
        "item_name": "configured_item",
        "quantity": 2,
    }
    assert context.session_data.state.action_ledger.current_task == "order"


async def test_active_order_survives_low_confidence_semantic_extraction(monkeypatch):
    context = _context(module="order", step_data={"item_name": "configured_item", "quantity": 2}, text="unclear")

    async def fake_extract(_context):
        return OrderFieldExtraction(
            workflow="order",
            workflow_action="continue",
            confidence=0.1,
            field_updates={"quantity": 5},
            field_confidence={"quantity": 0.1},
        )

    monkeypatch.setattr("backend.logic.engine.core.extract_order_fields", fake_extract)
    result = await agentic_engine._handle_semantic_order_field_extraction(
        context,
        message_router.route_message(
            "unclear",
            context.config.config_data,
            current_task="order",
            current_module="order",
        ),
    )

    assert result is not None
    assert context.session_data.state.get_task_step_data("order") == {
        "item_name": "configured_item",
        "quantity": 2,
    }
    assert context.session_data.current_module == "order"


async def test_pending_order_confirmation_survives_tangent(monkeypatch):
    pending = PendingAction(
        tool_key="order.execute",
        args={"item_name": "configured_item", "quantity": 1},
        requires_confirmation=True,
    )
    context = _context(module="order", step_data=pending.args, text="hours", pending_action=pending)

    async def fake_decide(_context):
        return PendingActionDecision(action="tangent", confidence=0.95)

    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", fake_decide)
    result = await agentic_engine._handle_semantic_pending_action(context)

    assert result is not None
    assert context.session_data.state.action_ledger.pending_action is pending
    assert context.session_data.state.get_task_step_data("order") == pending.args


async def test_route_switch_uncertainty_cannot_clear_pending_action(monkeypatch):
    pending = PendingAction(
        tool_key="order.execute",
        args={"item_name": "configured_item", "quantity": 1},
        requires_confirmation=True,
    )
    context = _context(module="order", step_data=pending.args, text="maybe book instead", pending_action=pending)

    async def fake_decide(_context):
        return PendingActionDecision(action="unknown", confidence=0.2)

    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", fake_decide)
    result = await agentic_engine._handle_semantic_pending_action(context)

    assert "confirm this request" in result.response_text.lower()
    assert result.quick_replies == ["Confirm", "Change", "Cancel"]
    assert context.session_data.state.action_ledger.pending_action is pending
    assert context.session_data.state.action_ledger.current_task == "order"


async def test_explicit_semantic_cancel_clears_only_current_order_state(monkeypatch):
    context = _context(module="order", step_data={"item_name": "configured_item", "quantity": 2}, text="cancel")

    async def fake_extract(_context):
        return OrderFieldExtraction(workflow="order", workflow_action="cancel", confidence=0.95)

    monkeypatch.setattr("backend.logic.engine.core.extract_order_fields", fake_extract)
    result = await agentic_engine._handle_semantic_order_field_extraction(
        context,
        RoutingDecision("order", 0.95, "active_task", False, False, "active_task_continuation"),
    )

    assert result.next_state == ConversationState.CANCELLED
    assert context.session_data.state.get_task_step_data("order") == {}
    assert context.session_data.state.action_ledger.current_task is None
    assert context.session_data.current_module is None


async def test_active_booking_survives_opening_hours_tangent(monkeypatch):
    await _prepare_engine(monkeypatch)
    step_data = {
        "service_name": "configured_service",
        "preferred_date": _future_date(),
        "preferred_time": "10:00",
    }
    context = _context(module="booking", step_data=step_data, text="hours")
    result = await agentic_engine._execute_step_inner(context)

    assert "opening hours" in result.response_text.lower()
    assert "name for this booking" in result.response_text.lower()
    assert context.session_data.state.get_task_step_data("booking") == step_data
    assert context.session_data.current_module == "booking"


async def test_explicit_workflow_switch_asks_before_abandoning_active_booking(monkeypatch):
    await _prepare_engine(monkeypatch)
    step_data = {
        "service_name": "configured_service",
        "preferred_date": _future_date(),
        "preferred_time": "10:00",
    }
    context = _context(module="booking", step_data=step_data, text="place order")
    result = await agentic_engine._execute_step_inner(context)

    assert "kept your current request details" in result.response_text
    assert context.session_data.state.get_task_step_data("booking") == step_data
    assert context.session_data.state.action_ledger.current_task == "booking"
    assert context.session_data.current_module == "booking"


async def test_active_payment_and_failed_extraction_preserve_state(monkeypatch):
    context = _context(module="payments", step_data={"amount": 20, "currency": "USD"}, text="unclear")

    async def fake_extract(_context):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("backend.logic.engine.core.extract_payment_fields", fake_extract)
    result = await agentic_engine._handle_semantic_payment_field_extraction(
        context,
        RoutingDecision("payments", 0.75, "active_task", False, False, "active_task_continuation"),
    )

    assert result is not None
    assert context.session_data.state.get_task_step_data("payments") == {"amount": 20, "currency": "USD"}
    assert context.session_data.state.action_ledger.current_task == "payments"


async def test_pending_payment_action_not_cleared_by_ordinary_message(monkeypatch):
    pending = PendingAction(
        tool_key="payments.execute",
        args={"amount": 20, "currency": "USD"},
        requires_confirmation=True,
    )
    context = _context(module="payments", step_data=pending.args, text="what are your hours", pending_action=pending)

    async def fake_decide(_context):
        return PendingActionDecision(action="tangent", confidence=0.95)

    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", fake_decide)
    result = await agentic_engine._handle_semantic_pending_action(context)

    assert result is not None
    assert context.session_data.state.action_ledger.pending_action is pending
    assert context.session_data.state.get_task_step_data("payments") == pending.args


async def test_failed_payment_execution_preserves_pending_action(monkeypatch):
    pending = PendingAction(tool_key="payments.execute", args={"amount": 20, "currency": "USD"})
    context = _context(
        module="payments",
        step_data=pending.args,
        text="confirm",
        pending_action=pending,
        task_status="confirmed",
    )

    async def fake_execute(_tool_key, _context):
        return ToolExecutionResult(
            tool_key="payments.execute",
            success=False,
            denied_reason="tool_failed",
            user_message="I couldn't complete that action right now.",
        )

    from unittest.mock import AsyncMock
    monkeypatch.setattr(
        "backend.logic.engine.core.decide_pending_action",
        AsyncMock(return_value=PendingActionDecision(action="confirm", confidence=0.99))
    )
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", fake_execute)
    result = await agentic_engine._handle_semantic_pending_action(context)

    assert result is not None
    assert context.session_data.state.action_ledger.pending_action is pending
    assert context.session_data.state.action_ledger.task_status == "blocked"


async def test_repeated_confirm_after_success_does_not_reexecute(monkeypatch):
    pending = PendingAction(tool_key="order.execute", args={"item_name": "configured_item", "quantity": 1})
    context = _context(module="order", step_data=pending.args, text="confirm", pending_action=pending, task_status="confirmed")
    calls = 0

    async def fake_execute(_tool_key, _context):
        nonlocal calls
        calls += 1
        return ToolExecutionResult(
            tool_key="order.execute",
            success=True,
            user_message="Completed.",
        )

    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", fake_execute)
    async def fake_decide(_context):
        return PendingActionDecision(action="confirm", confidence=0.99)

    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", fake_decide)
    async def fake_claim(*args, **kwargs):
        return True

    monkeypatch.setattr("backend.logic.engine.core.claim_protected_action_execution", fake_claim)
    first = await agentic_engine._handle_semantic_pending_action(context)
    second = await agentic_engine._handle_semantic_pending_action(context)

    assert first is not None
    assert second is None
    assert calls == 1
    assert context.session_data.state.action_ledger.pending_action is None


@pytest.mark.parametrize(
    ("module", "step_data"),
    [
        ("reminders", {"message": "configured_reminder", "scheduled_for": f"{_future_date()}T10:00:00"}),
        ("reviews", {"rating": 4, "feedback": "configured_feedback"}),
        ("lead_capture", {"phone": "+10000000000", "interest": "configured_interest"}),
        ("support", {"issue_summary": "configured_issue", "urgency": "medium"}),
    ],
)
async def test_active_non_transactional_workflow_survives_tangent(monkeypatch, module, step_data):
    await _prepare_engine(monkeypatch)
    context = _context(module=module, step_data=step_data, text="hours")
    result = await agentic_engine._execute_step_inner(context)

    assert "opening hours" in result.response_text.lower()
    assert "how can i help" not in result.response_text.lower()
    assert context.session_data.state.get_task_step_data(module) == step_data
    assert context.session_data.state.action_ledger.current_task == module


async def test_stale_quick_reply_menu_does_not_clear_active_workflow(monkeypatch):
    await _prepare_engine(monkeypatch)
    context = _context(module="booking", step_data={"service_name": "configured_service"}, text="menu")
    result = await agentic_engine._execute_step_inner(context)

    assert context.session_data.state.get_task_step_data("booking") == {"service_name": "configured_service"}
    assert context.session_data.state.action_ledger.current_task == "booking"
