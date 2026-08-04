from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.core.config import settings
from backend.core.context import (
    ConversationSession,
    ExecutionContext,
    LastToolResult,
    PendingAction,
    SessionState,
    Tenant,
    TenantConfig,
    User,
)
from backend.core.runtime_behavior_profile import RuntimeBehaviorProfileResolution
from backend.core.states import ConversationState
from backend.logic.engine.core import (
    WorkflowResult,
    _booking_clarification_response,
    _style_receptionist_result,
    _tool_failure_recovery_response,
    agentic_engine,
)
from backend.logic.engine.pending_decision import PendingActionDecision
from backend.pipeline.message_router import message_router
from backend.schemas.behavior_profile import EffectiveBehaviorProfile
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
            "monday": {"is_open": True, "open_time": "09:00", "close_time": "17:00"},
        },
        "services": [{"id": "service", "name": "configured_service", "duration_mins": 30}],
        "modules": {
            "order": {
                "enabled": True,
                "fulfillment_types": ["pickup", "delivery"],
                "categories": [{"id": "category", "name": "configured_category", "items": []}],
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
            "payments": {"enabled": True, "fields": [{"key": "amount", "type": "number", "required": True}]},
            "reminders": {"enabled": True, "fields": [{"key": "scheduled_for", "type": "string", "required": True}]},
            "reviews": {"enabled": True, "fields": [{"key": "rating", "type": "number", "required": True}]},
            "lead_capture": {"enabled": True},
            "support": {"enabled": True},
            "human_handoff": {"enabled": True},
        },
    }


def _context(
    *,
    module: str,
    step_data: dict | None = None,
    text: str = "hours",
    pending_action: PendingAction | None = None,
) -> ExecutionContext:
    tenant_id = uuid4()
    state = SessionState(
        workflow_state=ConversationState.COLLECTING_DATA,
        step_data=dict(step_data or {}),
        scoped_step_data={module: dict(step_data or {})} if step_data else {},
    )
    state.action_ledger.current_task = module
    state.action_ledger.task_status = "awaiting_confirmation" if pending_action else "collecting"
    state.action_ledger.pending_action = pending_action
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="tenant", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+10000000000", name="customer"),
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


def _route(context: ExecutionContext):
    return message_router.route_message(
        context.incoming_message["text"],
        context.config.config_data,
        current_task=context.session_data.state.action_ledger.current_task,
        current_module=context.session_data.current_module,
    )


def _profile(
    *,
    tone: str = "friendly",
    clarification_style: str = "compact",
    question_batching: str = "single",
    handoff_policy: str = "when_stuck",
    fallback_policy: str = "clarify_first",
    language_policy: str = "follow_customer",
) -> RuntimeBehaviorProfileResolution:
    return RuntimeBehaviorProfileResolution(
        source="active",
        effective_profile=EffectiveBehaviorProfile(
            schema_version=1,
            profile_name="Response Style",
            journey_priority=("booking", "order", "support"),
            greeting_mode="hybrid",
            clarification_style=clarification_style,
            tone=tone,
            language_policy=language_policy,
            price_visibility="show_when_available",
            service_detail_policy="brief",
            handoff_policy=handoff_policy,
            fallback_policy=fallback_policy,
            question_batching=question_batching,
            menu_policy="show_available_workflows",
        ),
    )


def test_order_tangent_resumes_with_configured_fulfillment_options():
    context = _context(module="order", step_data={"item_name": "selected_item", "quantity": 2})

    result = agentic_engine._order_tangent_response(context, _route(context))

    assert "opening hours" in result.response_text.lower()
    assert "fulfillment" in result.response_text.lower()
    assert result.quick_replies == ["pickup", "delivery"]
    assert "help with" not in result.response_text.lower()


def test_booking_tangent_resumes_with_the_next_missing_field():
    context = _context(module="booking", step_data={"service_name": "configured_service"})

    result = agentic_engine._booking_tangent_response(context, _route(context))

    assert "opening hours" in result.response_text.lower()
    assert "Which date" in result.response_text
    assert "help with" not in result.response_text.lower()


def test_payment_tangent_keeps_details_and_asks_only_for_amount():
    context = _context(module="payments", step_data={"currency": "USD"})

    result = agentic_engine._payment_tangent_response(context, _route(context))

    assert "opening hours" in result.response_text.lower()
    assert "payment amount" in result.response_text.lower()
    assert context.session_data.state.get_task_step_data("payments") == {"currency": "USD"}


@pytest.mark.parametrize(
    ("module", "field", "expected"),
    [
        ("booking", "preferred_time", "other time"),
        ("reminders", "scheduled_for", "future time"),
        ("reviews", "rating", "rating is not valid"),
        ("payments", "amount", "amount is not valid"),
    ],
)
def test_invalid_details_receive_targeted_recovery(module, field, expected):
    step_data = {}
    if module == "booking":
        step_data = {"service_name": "configured_service", "preferred_date": _future_date()}
    context = _context(module=module, step_data=step_data)
    context.session_data.state.action_ledger.last_tool_result = LastToolResult(
        tool_key=f"{module}.execute",
        success=False,
        validation_errors={field: "invalid"},
    )

    result = agentic_engine._active_workflow_resume_response(context)

    assert expected in result.response_text.lower()


def test_support_resume_acknowledges_issue_without_claiming_ticket_creation():
    context = _context(module="support")

    result = agentic_engine._active_workflow_resume_response(context)

    assert "I can help with that" in result.response_text
    assert "ticket" not in result.response_text.lower()


def test_pending_confirmation_includes_safe_summary_and_choices_only():
    pending = PendingAction(
        tool_key="payments.execute",
        args={"amount": 20},
        summary="Please review this payment request:\n- amount: 20\n- provider_id: hidden",
        requires_confirmation=True,
    )
    context = _context(module="payments", step_data=pending.args, pending_action=pending)

    result = agentic_engine._active_workflow_resume_response(context)

    assert "amount: 20" in result.response_text
    assert "provider" not in result.response_text.lower()
    assert result.quick_replies == ["Confirm", "Change", "Cancel"]


def test_non_pending_quick_replies_are_current_field_choices_not_confirmation_commands():
    context = _context(module="order", step_data={"item_name": "selected_item", "quantity": 1})

    result = agentic_engine._active_workflow_resume_response(context)

    assert result.quick_replies == ["pickup", "delivery"]
    assert "Confirm" not in result.quick_replies


@pytest.mark.parametrize(
    "tool_key",
    ["order.execute", "support.execute", "reminders.execute", "payments.execute"],
)
def test_failed_tools_never_receive_a_successful_customer_response(tool_key):
    module = tool_key.split(".", 1)[0]
    context = _context(module=module)
    context.session_data.state.record_tool_result(
        ToolExecutionResult(
            tool_key=tool_key,
            success=False,
            denied_reason="tool_failed",
            user_message="I could not complete that request right now.",
        )
    )

    result = _tool_failure_recovery_response(context, WorkflowResult("Completed successfully."))

    assert "completed successfully" not in result.response_text.lower()
    assert "nothing was completed" in result.response_text.lower()
    assert "ticket" not in result.response_text.lower()
    assert "payment link" not in result.response_text.lower()


async def test_pending_execution_failure_stays_recoverable_and_does_not_claim_payment_success(monkeypatch):
    pending = PendingAction(
        tool_key="payments.execute",
        args={"amount": 20},
        summary="Please review this payment request:\n- amount: 20",
    )
    context = _context(module="payments", step_data=pending.args, text="Confirm", pending_action=pending)
    context.session_data.state.action_ledger.task_status = "confirmed"
    original_nonce = pending.action_nonce
    decide_pending_action = AsyncMock(
        return_value=PendingActionDecision(action="confirm", confidence=0.99)
    )
    tool_calls = {"count": 0}

    async def fail_execute(_tool_key, _context):
        tool_calls["count"] += 1
        ledger = _context.session_data.state.action_ledger
        assert ledger.task_status == "executing"
        assert ledger.pending_action is pending
        assert ledger.pending_action.action_nonce == original_nonce
        assert ledger.pending_action.args == {"amount": 20}
        return ToolExecutionResult(
            tool_key="payments.execute",
            success=False,
            denied_reason="provider_unavailable",
            user_message="Payment collection is unavailable right now.",
        )

    async def no_usage(_context):
        return None

    async def no_hydrate(_context):
        return None

    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", decide_pending_action)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", fail_execute)
    monkeypatch.setattr("backend.logic.engine.core.usage_guard.check_message_allowed", no_usage)
    monkeypatch.setattr(agentic_engine, "_hydrate_order_catalog", no_hydrate)

    result = await agentic_engine.execute_step(context)

    decide_pending_action.assert_awaited_once_with(context)
    assert tool_calls["count"] == 1
    assert "payment collection is unavailable" in result.response_text.lower()
    assert "nothing was completed" in result.response_text.lower()
    assert "link created" not in result.response_text.lower()
    ledger = context.session_data.state.action_ledger
    assert ledger.task_status == "blocked"
    assert ledger.pending_action is pending
    assert ledger.pending_action.args == {"amount": 20}
    assert ledger.pending_action.action_nonce != original_nonce
    assert ledger.verification.otp_verified_actions == []
    assert ledger.verification.otp_verifications == []
    assert ledger.last_tool_result is not None
    assert ledger.last_tool_result.success is False
    assert ledger.last_tool_result.denied_reason == "provider_unavailable"
    assert ledger.last_tool_result.created_resource_type is None
    assert ledger.last_tool_result.created_resource_id is None


def test_direct_menu_request_during_active_order_resumes_the_order_instead_of_resetting():
    context = _context(module="order", step_data={"item_name": "selected_item", "quantity": 1}, text="menu")

    direct = agentic_engine._order_tangent_response(context, _route(context))

    assert "fulfillment" in direct.response_text.lower()
    assert context.session_data.state.action_ledger.current_task == "order"


@pytest.mark.parametrize(
    ("tone", "expected_prefix"),
    [
        ("friendly", "I’m happy to help."),
        ("formal", "Please note:"),
        ("concise", "Next:"),
        ("premium", "Certainly."),
    ],
)
def test_behavior_tones_change_only_safe_receptionist_wording(tone, expected_prefix):
    context = _context(module="booking", step_data={"service_name": "configured_service"})
    context.runtime_behavior_profile = _profile(tone=tone)
    before_state = context.session_data.state.model_copy(deep=True)

    result = _style_receptionist_result(context, WorkflowResult("Which date would you like for the booking?"))

    assert result.response_text.startswith(expected_prefix)
    assert "Which date would you like for the booking?" in result.response_text
    assert result.quick_replies == []
    assert context.session_data.state == before_state


def test_single_question_profile_keeps_only_next_missing_booking_field():
    context = _context(module="booking", step_data={})
    context.runtime_behavior_profile = _profile(question_batching="single")
    tool_def = agentic_engine.tool_registry.get_tool("booking.execute")

    result = _booking_clarification_response(context, tool_def)

    assert "Which service" in result.response_text
    assert "share the service and" not in result.response_text
    assert "date" not in result.response_text.lower()
    assert context.session_data.state.get_task_step_data("booking") == {}


def test_grouped_question_profile_asks_safe_missing_fields_without_collecting_them():
    context = _context(module="booking", step_data={})
    context.runtime_behavior_profile = _profile(question_batching="grouped")
    tool_def = agentic_engine.tool_registry.get_tool("booking.execute")
    current_task = context.session_data.state.action_ledger.current_task

    result = _booking_clarification_response(context, tool_def)

    assert "Which service" in result.response_text
    assert "service, date, time, and name together" in result.response_text
    assert context.session_data.state.get_task_step_data("booking") == {}
    assert context.session_data.state.action_ledger.current_task == current_task


def test_profile_styles_active_resume_and_tangent_without_changing_active_workflow():
    context = _context(module="booking", step_data={"service_name": "configured_service"}, text="hours")
    context.runtime_behavior_profile = _profile(tone="formal")
    current_task = context.session_data.state.action_ledger.current_task
    step_data = context.session_data.state.get_task_step_data("booking")

    resumed = _style_receptionist_result(context, agentic_engine._active_workflow_resume_response(context))
    tangent = _style_receptionist_result(context, agentic_engine._booking_tangent_response(context, _route(context)))

    assert resumed.response_text.startswith("Please note:")
    assert tangent.response_text.startswith("Please note:")
    assert context.session_data.state.action_ledger.current_task == current_task
    assert context.session_data.state.get_task_step_data("booking") == step_data


def test_profile_styles_invalid_field_recovery_but_validation_stays_backend_owned():
    context = _context(
        module="booking",
        step_data={"service_name": "configured_service", "preferred_date": _future_date()},
    )
    context.runtime_behavior_profile = _profile(tone="premium")
    context.session_data.state.action_ledger.last_tool_result = LastToolResult(
        tool_key="booking.execute",
        success=False,
        validation_errors={"preferred_time": "invalid"},
    )
    before_state = context.session_data.state.model_copy(deep=True)

    result = _style_receptionist_result(context, agentic_engine._active_workflow_resume_response(context))

    assert result.response_text.startswith("Certainly.")
    assert "other time" in result.response_text.lower()
    assert context.session_data.state == before_state


def test_handoff_style_never_exposes_unavailable_action_but_can_offer_available_support():
    context = _context(module="support")
    context.runtime_behavior_profile = _profile(handoff_policy="early_offer")
    context.session_data.state.record_tool_result(
        ToolExecutionResult(tool_key="support.execute", success=False, user_message="I could not complete that request."),
    )

    unavailable = _style_receptionist_result(context, WorkflowResult("I could not complete that request."))
    context.incoming_message["_runtime_available_modules"] = ["support"]
    available = _style_receptionist_result(context, WorkflowResult("I could not complete that request."))

    assert "Contact support" not in unavailable.quick_replies
    assert available.quick_replies == ["Contact support"]
    assert all(isinstance(reply, str) for reply in available.quick_replies)


def test_style_failure_returns_original_backend_safe_success_response(monkeypatch):
    context = _context(module="booking")
    context.runtime_behavior_profile = _profile()
    context.session_data.state.record_tool_result(
        ToolExecutionResult(tool_key="booking.execute", success=True, user_message="Booking request is ready for review."),
    )
    result = WorkflowResult("Your booking request is ready for review.", quick_replies=["Confirm"])

    def raise_style(*_args, **_kwargs):
        raise RuntimeError("style failure")

    monkeypatch.setattr("backend.logic.engine.core.style_receptionist_response", raise_style)

    styled = _style_receptionist_result(context, result)

    assert styled is result
    assert styled.response_text == "Your booking request is ready for review."
    assert styled.quick_replies == ["Confirm"]
    assert context.session_data.state.action_ledger.last_tool_result.success is True


async def test_localization_integration_failure_keeps_successful_backend_result(monkeypatch):
    context = _context(module="booking")
    context.runtime_behavior_profile = _profile(language_policy="local_language_first")
    context.session_data.state.record_tool_result(
        ToolExecutionResult(tool_key="booking.execute", success=True, user_message="Booking request is ready for review."),
    )
    result = WorkflowResult("Booking request is ready for review.", quick_replies=["View booking"])

    async def raise_localization(*_args, **_kwargs):
        raise RuntimeError("localization integration failure")

    monkeypatch.setattr("backend.logic.engine.core.localize_customer_response", raise_localization)

    localized = await agentic_engine._localize_active_workflow_result(
        context,
        result,
        was_active_before_turn=True,
    )

    assert localized is result
    assert localized.response_text == "Booking request is ready for review."
    assert localized.quick_replies == ["View booking"]
    assert context.session_data.state.action_ledger.last_tool_result.success is True
