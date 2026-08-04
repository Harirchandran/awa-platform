from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.core.context import (
    ConversationSession,
    ExecutionContext,
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
    _apply_tool_failure_truth_guard,
    _style_receptionist_result,
    agentic_engine,
)
from backend.logic.engine.nba import append_next_best_actions
from backend.logic.engine.pending_decision import PendingActionDecision
from backend.pipeline.channel_output import normalize_channel_payload
from backend.pipeline.interactive_parser import sanitize_quick_replies
from backend.pipeline.message_router import message_router
from backend.schemas.behavior_profile import EffectiveBehaviorProfile
from backend.tools.registry import ToolExecutionResult


pytestmark = pytest.mark.anyio


def _future_date() -> str:
    return (date.today() + timedelta(days=7)).isoformat()


def _config() -> dict:
    return {
        "business_name": "Transcript Business",
        "default_language": "en",
        "working_hours": {"monday": {"is_open": True, "open_time": "09:00", "close_time": "17:00"}},
        "services": [{"id": "service", "name": "configured_service", "price": 100, "duration_mins": 30}],
        "modules": {
            "booking": {
                "enabled": True,
                "fields": [
                    {"key": "service_name", "label": "Service", "type": "string", "required": True},
                    {"key": "preferred_date", "label": "Date", "type": "date", "required": True},
                    {"key": "preferred_time", "label": "Time", "type": "time", "required": True},
                    {"key": "customer_name", "label": "Name", "type": "string", "required": True},
                ],
            },
            "order": {
                "enabled": True,
                "fulfillment_types": ["pickup", "delivery"],
                "categories": [{"name": "Menu", "items": [{"name": "configured_item", "enabled": True, "price": 10}]}],
            },
            "payments": {"enabled": True, "allowed_currencies": ["USD"]},
            "support": {"enabled": True},
            "human_handoff": {"enabled": True},
            "faq": {"enabled": True},
        },
    }


def _profile(
    *,
    tone: str = "formal",
    handoff_policy: str = "when_stuck",
    fallback_policy: str = "clarify_first",
    language_policy: str = "follow_customer",
) -> RuntimeBehaviorProfileResolution:
    return RuntimeBehaviorProfileResolution(
        source="active",
        effective_profile=EffectiveBehaviorProfile(
            schema_version=1,
            profile_name="Runtime Transcript",
            journey_priority=("booking", "order", "faq", "support"),
            greeting_mode="hybrid",
            clarification_style="compact",
            tone=tone,
            language_policy=language_policy,
            price_visibility="show_when_available",
            service_detail_policy="brief",
            handoff_policy=handoff_policy,
            fallback_policy=fallback_policy,
            question_batching="single",
            menu_policy="show_available_workflows",
        ),
    )


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
        tenant=Tenant(id=tenant_id, name="Transcript Business", plan_code="runtime_test", status="active"),
        user=User(id=uuid4(), phone_number="+10000000000", name="Customer"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=11,
            current_module=module,
            state=state,
        ),
        config=TenantConfig(tenant_id=tenant_id, version=11, config_data=_config()),
        incoming_message={"text": text, "channel_type": "awa_chat"},
    )


def test_transcript_mid_booking_fact_question_preserves_active_workflow_under_profile_style():
    step_data = {
        "service_name": "configured_service",
        "preferred_date": _future_date(),
        "preferred_time": "10:00",
    }
    context = _context(module="booking", step_data=step_data, text="hours")
    context.runtime_behavior_profile = _profile()
    route = message_router.route_message("hours", context.config.config_data, current_task="booking", current_module="booking")

    response = _style_receptionist_result(context, agentic_engine._booking_tangent_response(context, route))

    assert response.response_text.startswith("Please note:")
    assert "opening hours" in response.response_text.lower()
    assert context.session_data.state.get_task_step_data("booking") == step_data
    assert context.session_data.state.action_ledger.current_task == "booking"
    assert context.session_data.current_module == "booking"


async def test_transcript_change_then_cancel_reuses_pending_gate_without_tool_bypass(monkeypatch):
    pending = PendingAction(
        tool_key="order.execute",
        args={"item_name": "configured_item", "quantity": 1},
        summary="Please review this order request.",
        requires_confirmation=True,
    )
    context = _context(module="order", step_data=pending.args, text="change time", pending_action=pending)
    context.runtime_behavior_profile = _profile(tone="friendly")
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    decide = AsyncMock(side_effect=[
        PendingActionDecision(action="change", confidence=0.95, field_updates={"quantity": 2}),
        PendingActionDecision(action="cancel", confidence=0.95),
    ])
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", decide)
    original_nonce = pending.action_nonce
    original_hash = pending.args_hash
    changed = await agentic_engine._handle_semantic_pending_action(context)

    assert changed is not None
    assert context.session_data.state.action_ledger.pending_action is not None
    assert context.session_data.state.action_ledger.pending_action.args["quantity"] == 2
    assert context.session_data.state.action_ledger.pending_action.action_nonce != original_nonce
    assert context.session_data.state.action_ledger.pending_action.args_hash != original_hash
    assert context.session_data.state.action_ledger.task_status == "awaiting_confirmation"
    assert decide.await_count == 1
    execute_tool.assert_not_awaited()

    # Cancel is a second customer turn: preserve durable state and clear turn-local evidence only.
    context.incoming_message = {
        **context.incoming_message,
        "text": "cancel",
        "message_id": str(uuid4()),
    }
    context.shadow_turn_evidence = {}
    context.turn_interpretation = None
    context.turn_interpretation_comparison = None
    context.turn_plan = None
    cancelled = await agentic_engine._handle_semantic_pending_action(context)

    assert cancelled is not None
    assert "cancelled" in cancelled.response_text.lower()
    assert context.session_data.state.action_ledger.pending_action is None
    assert context.session_data.state.action_ledger.current_task is None
    assert decide.await_count == 2
    execute_tool.assert_not_awaited()


async def test_transcript_failed_tool_with_profile_has_no_success_nba_or_fake_completion():
    context = _context(module="order", step_data={"item_name": "configured_item", "quantity": 1})
    context.runtime_behavior_profile = _profile(tone="premium", handoff_policy="early_offer")
    context.incoming_message["_runtime_available_modules"] = ["order", "support"]
    context.session_data.state.record_tool_result(
        ToolExecutionResult(
            tool_key="order.execute",
            success=False,
            denied_reason="out_of_stock",
            user_message="I couldn't place that order because the item is unavailable.",
        ),
    )

    truthful = _apply_tool_failure_truth_guard(context, WorkflowResult("Your order has been placed successfully."))
    with_nba = await append_next_best_actions(context, truthful, tool_registry=None)
    styled = _style_receptionist_result(context, with_nba)

    assert "placed successfully" not in styled.response_text.lower()
    assert "couldn't place" in styled.response_text.lower()
    assert "View menu" not in styled.quick_replies
    assert "Make payment" not in styled.quick_replies
    assert styled.quick_replies == ["Contact support"]
    assert context.session_data.state.action_ledger.last_tool_result.success is False


async def test_transcript_localization_failure_keeps_backend_success_and_visible_text_only(monkeypatch):
    context = _context(module="booking", step_data={"service_name": "configured_service"})
    context.runtime_behavior_profile = _profile(language_policy="local_language_first")
    context.session_data.state.record_tool_result(
        ToolExecutionResult(tool_key="booking.execute", success=True, user_message="Booking confirmed."),
    )
    result = WorkflowResult("Booking confirmed.", quick_replies=["View booking"])

    async def fail_localization(*_args, **_kwargs):
        raise RuntimeError("provider failure")

    monkeypatch.setattr("backend.logic.engine.core.localize_customer_response", fail_localization)
    output = await agentic_engine._localize_active_workflow_result(context, result, was_active_before_turn=True)

    assert output is result
    assert output.quick_replies == ["View booking"]
    assert context.session_data.state.action_ledger.last_tool_result.success is True


def test_transcript_quick_replies_remain_customer_text_without_hidden_action_authority():
    labels = sanitize_quick_replies(
        ["Confirm", '{"action":"order.execute","amount":1}', "Change", "payment_execute"],
    )
    payload = normalize_channel_payload("Please review your request.", "awa_chat", quick_replies=labels)

    assert labels == ["Confirm", "Change"]
    assert payload["text"] == "Please review your request."
    interactive = payload["interactive_data"]
    visible = [button["reply"]["title"] for button in interactive["action"]["buttons"]]
    assert visible == ["Confirm", "Change"]
    assert all("execute" not in label.lower() for label in visible)


def test_transcript_profile_is_presentation_only_for_payment_otp_and_confirmation_boundaries():
    profile = _profile(tone="premium", handoff_policy="early_offer", fallback_policy="handoff_if_available")
    dumped = profile.effective_profile.model_dump()

    assert not ({"payment_provider", "payment_status", "payment_link_url", "otp_required", "requires_confirmation"} & set(dumped))
    assert dumped["tone"] == "premium"
    assert dumped["handoff_policy"] == "early_offer"
