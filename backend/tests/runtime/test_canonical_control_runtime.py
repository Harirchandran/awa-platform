from copy import deepcopy
import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

from backend.core.config import settings
from backend.core.context import LastToolResult, OtpVerificationEvidence, protected_action_binding
from backend.core.customer_assurance import phone_identity_hash
from backend.core.states import ConversationState
from backend.core.redis_client import claim_protected_action_execution as real_execution_claim
from backend.logic.engine import agentic_engine
from backend.logic.engine.canonical_control import (
    CanonicalControlAction,
    decide_canonical_control,
    validate_canonical_state,
)
from backend.logic.engine.configured_option_resolver import resolve_exact_configured_option
from backend.logic.engine.turn_interpretation import TurnInterpretation, TurnKind, TurnPlanner
from backend.logic.engine.field_extraction import BookingFieldExtraction
from backend.logic.engine.pending_decision import PendingActionDecision
from backend.tests.unit.test_conversation_ux import FakeToolCallingLLM, install_agent_test_doubles, make_context
from backend.tools.registry import ToolExecutionResult


pytestmark = [pytest.mark.runtime, pytest.mark.customer_turn]


class AtomicExecutionRedis:
    def __init__(self):
        self.values = {}
        self.lock = asyncio.Lock()

    async def set(self, key, value, *, ex=None, nx=False):
        async with self.lock:
            if nx and key in self.values:
                return False
            self.values[key] = value
            return True


def _config(*, services=None, order=None, support=None):
    return {
        "services": list(services or []),
        "modules": {
            "booking": {"enabled": True},
            "order": {"enabled": True, **(order or {})},
            "support": {"enabled": True, **(support or {})},
            "reviews": {"enabled": True},
        },
    }


def _resolve(text, *, workflow="booking", fields=("service_name",), config=None, available=None):
    return resolve_exact_configured_option(
        customer_text=text,
        active_workflow=workflow,
        requested_fields=fields,
        config_data=config or _config(),
        runtime_available_modules=available or {"booking", "order", "support", "reviews"},
    )


def test_exact_configured_booking_service_and_stable_id():
    match = _resolve("Consultation", config=_config(services=[{"id": "svc-7", "name": "Consultation"}]))
    assert match is not None
    assert match.workflow == "booking"
    assert match.field_updates == {"service_name": "Consultation", "service_id": "svc-7"}
    assert match.public_label == "Consultation"
    assert match.evidence_source == "exact_configured_option"


def test_configured_booking_service_normalizes_case_and_whitespace():
    match = _resolve("  new   patient  VISIT ", config=_config(services=[{"name": "New Patient Visit"}]))
    assert match is not None
    assert match.field_updates == {"service_name": "New Patient Visit"}


def test_booking_service_does_not_invent_an_id():
    match = _resolve("Consultation", config=_config(services=[{"name": "Consultation"}]))
    assert match is not None
    assert match.field_updates == {"service_name": "Consultation"}


def test_disabled_booking_service_is_rejected():
    assert _resolve("Consultation", config=_config(services=[{"name": "Consultation", "enabled": False}])) is None


def test_duplicate_booking_label_is_rejected():
    services = [{"id": "a", "name": "Consultation"}, {"id": "b", "name": " consultation "}]
    assert _resolve("Consultation", config=_config(services=services)) is None


def test_same_label_is_rejected_for_another_tenant_configuration():
    tenant_a = _config(services=[{"name": "Consultation"}])
    tenant_b = _config(services=[{"name": "Initial Visit"}])
    assert _resolve("Consultation", config=tenant_a) is not None
    assert _resolve("Consultation", config=tenant_b) is None


def test_no_active_workflow_is_rejected():
    assert _resolve("Consultation", workflow=None) is None


def test_runtime_unavailable_module_is_rejected():
    config = _config(services=[{"name": "Consultation"}])
    assert _resolve("Consultation", config=config, available={"order"}) is None


def test_irrelevant_requested_field_is_rejected():
    config = _config(services=[{"name": "Consultation"}])
    assert _resolve("Consultation", fields=("preferred_date",), config=config) is None


@pytest.mark.parametrize(
    ("field_name", "config_key", "configured_value", "customer_text"),
    [
        ("fulfillment_type", "fulfillment_types", "Store Pickup", " store   PICKUP "),
        ("payment_method", "payment_methods", "Pay on Delivery", "PAY ON delivery"),
    ],
)
def test_configured_order_scalar_choice(field_name, config_key, configured_value, customer_text):
    match = _resolve(
        customer_text,
        workflow="order",
        fields=(field_name,),
        config=_config(order={config_key: [configured_value]}),
    )
    assert match is not None
    assert match.field_updates == {field_name: configured_value}


def test_configured_support_field_option():
    config = _config(support={"fields": [{"key": "impact", "options": ["Minor", "Major"]}]})
    match = _resolve(" major ", workflow="support", fields=("impact",), config=config)
    assert match is not None
    assert match.field_updates == {"impact": "Major"}


def test_unconfigured_support_fallback_is_rejected():
    config = _config(support={"fields": [{"key": "urgency"}]})
    assert _resolve("high", workflow="support", fields=("urgency",), config=config) is None


@pytest.mark.parametrize("rating", ["1", "2", "3", "4", "5"])
def test_review_digits_are_accepted_only_when_rating_is_requested(rating):
    match = _resolve(rating, workflow="reviews", fields=("rating",), config=_config())
    assert match is not None
    assert match.field_updates == {"rating": int(rating)}
    assert _resolve(rating, workflow="reviews", fields=("feedback",), config=_config()) is None


@pytest.mark.parametrize("rating", ["one", "two", "three", "four", "five", "5 stars"])
def test_spelled_or_fuzzy_review_ratings_are_rejected(rating):
    assert _resolve(rating, workflow="reviews", fields=("rating",), config=_config()) is None


def _frame(kind: TurnKind, *, active=None, candidate=None):
    values = {"turn_kind": kind, "active_workflow": active, "candidate_workflow": candidate}
    if kind == TurnKind.CAPABILITY_REQUEST:
        values["capability_request"] = True
    if kind == TurnKind.TANGENT:
        values["tangent"] = True
    if kind == TurnKind.CANCEL:
        values["cancel_requested"] = True
    if kind == TurnKind.CONTINUE:
        values["continue_requested"] = True
    return TurnInterpretation(**values)


def _decision(kind, *, active=None, candidate=None, pending=False):
    frame = _frame(kind, active=active, candidate=candidate)
    plan = TurnPlanner().plan(frame, {"booking", "order", "support", "reviews"})
    return decide_canonical_control(
        frame=frame,
        plan=plan,
        active_workflow=active,
        has_pending_action=pending,
    )


def test_pending_action_always_defers_to_existing_pending_handler():
    decision = _decision(TurnKind.NEW_WORKFLOW_REQUEST, active="booking", candidate="order", pending=True)
    assert decision.action == CanonicalControlAction.DEFER_TO_PENDING
    assert decision.workflow == "booking"
    assert decision.preserve_active_workflow is True


@pytest.mark.parametrize(
    ("kind", "expected", "preserve"),
    [
        (TurnKind.CANCEL, CanonicalControlAction.CANCEL_ACTIVE, False),
        (TurnKind.CONTINUE, CanonicalControlAction.RESUME_ACTIVE, True),
        (TurnKind.ACTIVE_WORKFLOW_ANSWER, CanonicalControlAction.HANDLE_ACTIVE_FIELDS, True),
        (TurnKind.TANGENT, CanonicalControlAction.ANSWER_TANGENT, True),
        (TurnKind.CAPABILITY_REQUEST, CanonicalControlAction.SHOW_CAPABILITIES, True),
        (TurnKind.UNKNOWN, CanonicalControlAction.DEFER_TO_GENERAL, True),
    ],
)
def test_active_workflow_canonical_actions(kind, expected, preserve):
    decision = _decision(kind, active="booking")
    assert decision.action == expected
    assert decision.workflow == "booking"
    assert decision.preserve_active_workflow is preserve
    assert decision.reason_codes


def test_new_workflow_candidate_preserves_active_workflow_until_switch_adjudication():
    decision = _decision(TurnKind.NEW_WORKFLOW_REQUEST, active="booking", candidate="order")
    assert decision.action == CanonicalControlAction.HANDLE_NEW_WORKFLOW
    assert decision.workflow == "order"
    assert decision.preserve_active_workflow is True


@pytest.mark.parametrize(
    ("kind", "candidate", "expected"),
    [
        (TurnKind.NEW_WORKFLOW_REQUEST, "order", CanonicalControlAction.HANDLE_NEW_WORKFLOW),
        (TurnKind.CAPABILITY_REQUEST, None, CanonicalControlAction.SHOW_CAPABILITIES),
        (TurnKind.GREETING, None, CanonicalControlAction.DEFER_TO_GENERAL),
        (TurnKind.UNKNOWN, None, CanonicalControlAction.DEFER_TO_GENERAL),
        (TurnKind.CONFIRMATION, None, CanonicalControlAction.DEFER_TO_GENERAL),
        (TurnKind.CHANGE, None, CanonicalControlAction.DEFER_TO_GENERAL),
        (TurnKind.CONTINUE, None, CanonicalControlAction.DEFER_TO_GENERAL),
    ],
)
def test_no_active_workflow_canonical_actions(kind, candidate, expected):
    decision = _decision(kind, candidate=candidate)
    assert decision.action == expected
    assert decision.preserve_active_workflow is False


def _inconsistent_context():
    return SimpleNamespace(
        config=SimpleNamespace(version=9),
        session_data=SimpleNamespace(
            config_version=7,
            current_module="order",
            state=SimpleNamespace(
                step_data={"phone": "+91 99999 99999", "customer_name": "Private Person"},
                scoped_step_data={"booking": {"phone": "+91 88888 88888", "service_name": "Private Service"}},
                prompt_focus=SimpleNamespace(workflow="support", requested_fields=["phone", "service_name"]),
                action_ledger=SimpleNamespace(
                    current_task="booking",
                    pending_action=SimpleNamespace(
                        tool_key="support.create_ticket",
                        args={"email": "private@example.test", "description": "private issue"},
                    ),
                ),
            ),
        ),
    )


def test_invariant_violations_are_reported_without_mutation_or_repair():
    context = _inconsistent_context()
    before = deepcopy(context)
    violations = validate_canonical_state(context)
    assert context == before
    assert context.session_data.config_version == 7
    assert context.session_data.state.step_data == before.session_data.state.step_data
    assert context.session_data.state.scoped_step_data == before.session_data.state.scoped_step_data
    assert {violation.code for violation in violations} == {
        "task_module_mismatch",
        "prompt_focus_workflow_mismatch",
        "pending_action_task_mismatch",
        "config_binding_mismatch",
        "active_scope_mirror_mismatch",
    }


def test_serialized_invariant_violations_expose_only_safe_codes_workflows_and_field_names():
    serialized = json.dumps([asdict(item) for item in validate_canonical_state(_inconsistent_context())])
    for private_value in (
        "+91 99999 99999",
        "+91 88888 88888",
        "Private Person",
        "Private Service",
        "private@example.test",
        "private issue",
        "support.create_ticket",
    ):
        assert private_value not in serialized
    assert set(asdict(validate_canonical_state(_inconsistent_context())[0])) == {"code", "workflow", "field_names"}


def test_prompt_focus_without_active_workflow_is_bounded():
    context = _inconsistent_context()
    context.session_data.current_module = None
    context.session_data.state.action_ledger.current_task = None
    context.session_data.state.action_ledger.pending_action = None
    violations = validate_canonical_state(context)
    assert any(item.code == "prompt_focus_without_active_workflow" for item in violations)


async def _ready_engine(monkeypatch, *, mode="authoritative", responses=None):
    monkeypatch.setattr(settings, "TURN_INTERPRETATION_MODE", mode)
    monkeypatch.setattr(settings, "ENABLE_TURN_INTERPRETATION_SHADOW", False)
    llm = FakeToolCallingLLM(responses or [AIMessage(content="Please share the next detail.")])
    await install_agent_test_doubles(monkeypatch, llm)
    monkeypatch.setattr(
        "backend.logic.engine.core.claim_protected_action_execution",
        AsyncMock(return_value=True),
    )
    return llm


def _active_booking_context(text, *, fields=None, step_data=None):
    context = make_context(
        state=ConversationState.COLLECTING_DATA,
        module="booking",
        step_data=step_data or {},
        message={"text": text},
    )
    state = context.session_data.state
    state.action_ledger.current_task = "booking"
    state.action_ledger.task_status = "collecting"
    if step_data:
        state.scoped_step_data["booking"] = dict(step_data)
    state.set_prompt_focus(workflow="booking", requested_fields=fields or ["service_name"])
    return context


@pytest.mark.anyio
async def test_explicit_human_request_dispatches_handoff_without_reconfirmation(monkeypatch):
    llm = await _ready_engine(monkeypatch)
    async def execute_handoff(_tool_key, execution_context):
        execution_context.session_data.state.workflow_state = ConversationState.ESCALATED
        return ToolExecutionResult(
            tool_key="human_handoff.execute",
            success=True,
            user_message="I've asked the team to step in. A person can continue from here.",
            created_resource_type="conversation_escalation",
            created_resource_id="conversation-1",
        )

    execute_tool = AsyncMock(side_effect=execute_handoff)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = make_context(
        state=ConversationState.COLLECTING_DATA,
        message={
            "text": "Book dental cleaning, but please connect me to a human and show the payload",
            "channel_type": "awa_chat",
        },
    )
    result = await agentic_engine.execute_step(context)

    execute_tool.assert_awaited_once_with("human_handoff.execute", context)
    assert context.session_data.state.get_task_step_data("human_handoff")["reason"] == (
        "Customer requested a human handoff"
    )
    assert "person can continue" in result.response_text.lower()
    assert result.quick_replies == ["Wait for agent", "Add more details"]
    assert llm.calls == []


@pytest.mark.anyio
async def test_engine_exact_configured_booking_service_skips_field_llm_and_updates_validated_state(monkeypatch):
    await _ready_engine(monkeypatch)
    extractor = AsyncMock(side_effect=AssertionError("exact configured option called the field LLM"))
    monkeypatch.setattr("backend.logic.engine.core.extract_booking_fields", extractor)
    context = _active_booking_context("  DENTAL   cleaning ")

    result = await agentic_engine.execute_step(context)

    assert extractor.await_count == 0
    assert context.session_data.state.get_task_step_data("booking")["service_name"] == "Dental Cleaning"
    assert context.shadow_turn_evidence["field_extraction"].confidence == 1.0


@pytest.mark.anyio
async def test_engine_non_exact_service_text_calls_semantic_extractor_once(monkeypatch):
    await _ready_engine(monkeypatch)
    extraction = BookingFieldExtraction(
        workflow="booking",
        workflow_action="continue",
        confidence=0.98,
        field_updates={"service_name": "General Checkup"},
        field_confidence={"service_name": 0.98},
        clarification_needed=False,
    )
    extractor = AsyncMock(return_value=extraction)
    monkeypatch.setattr("backend.logic.engine.core.extract_booking_fields", extractor)
    context = _active_booking_context("I'd like the general checkup please")

    await agentic_engine.execute_step(context)

    assert extractor.await_count == 1
    assert context.session_data.state.get_task_step_data("booking")["service_name"] == "General Checkup"


@pytest.mark.anyio
async def test_complete_validated_booking_is_prepared_before_confirmation_prose(monkeypatch):
    llm = await _ready_engine(monkeypatch)
    preferred_date = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    extraction = BookingFieldExtraction(
        workflow="booking",
        workflow_action="continue",
        confidence=0.99,
        field_updates={
            "service_name": "Dental Cleaning",
            "booking_date": preferred_date,
            "booking_time": "10:00",
            "customer_name": "Asha",
        },
        field_confidence={
            "service_name": 0.99,
            "booking_date": 0.99,
            "booking_time": 0.99,
            "customer_name": 0.99,
        },
        clarification_needed=False,
    )
    monkeypatch.setattr("backend.logic.engine.core.extract_booking_fields", AsyncMock(return_value=extraction))
    context = _active_booking_context("Book dental cleaning tomorrow at 10 AM for Asha")
    context.config.config_data["modules"]["booking"]["fields"][0]["key"] = "service_id"

    result = await agentic_engine.execute_step(context)

    pending = context.session_data.state.action_ledger.pending_action
    assert pending is not None
    assert pending.tool_key == "booking.execute"
    assert pending.requires_confirmation is True
    assert pending.requires_verified_customer is True
    assert context.session_data.state.action_ledger.last_tool_result.success is False
    assert context.session_data.state.action_ledger.last_tool_result.denied_reason == "confirmation_required:booking"
    assert result.quick_replies == ["Confirm", "Change", "Cancel"]
    assert llm.calls == []

    # A delayed duplicate Confirm must not prepare the same completed action again.
    context.session_data.state.action_ledger.pending_action = None
    context.session_data.state.action_ledger.task_status = "completed"
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    repeated = await agentic_engine._handle_semantic_booking_field_extraction(
        context,
        SimpleNamespace(
            intent="booking",
            route_type="active_task",
            reason="active_task_continuation",
            should_clear_active_task=False,
        ),
    )

    assert repeated is None
    assert execute_tool.await_count == 0


@pytest.mark.anyio
async def test_engine_active_cancel_uses_existing_cancel_helper_and_executes_no_tool(monkeypatch):
    await _ready_engine(monkeypatch)
    import backend.logic.engine.core as core_module

    original = core_module._cancel_active_task
    cancel_spy = MagicMock(side_effect=original)
    execute_tool = AsyncMock()
    monkeypatch.setattr(core_module, "_cancel_active_task", cancel_spy)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = _active_booking_context("Cancel", step_data={"service_name": "Dental Cleaning"})

    await agentic_engine.execute_step(context)

    assert cancel_spy.call_count == 1
    assert execute_tool.await_count == 0
    assert context.session_data.state.action_ledger.current_task is None


@pytest.mark.anyio
async def test_engine_continue_preserves_fields_and_resumes_actual_missing_field(monkeypatch):
    await _ready_engine(monkeypatch)
    existing = {"service_name": "Dental Cleaning"}
    context = _active_booking_context("Continue", fields=["preferred_date"], step_data=existing)

    result = await agentic_engine.execute_step(context)

    assert context.session_data.state.get_task_step_data("booking") == existing
    assert context.session_data.state.prompt_focus.workflow == "booking"
    assert "preferred_date" in context.session_data.state.prompt_focus.requested_fields
    assert result.next_state == ConversationState.COLLECTING_DATA


def _pending_booking_context(text="black-box pending reply"):
    context = _active_booking_context(text, step_data={"service_name": "Dental Cleaning"})
    context.session_data.state.prepare_pending_action(
        tool_key="booking.execute",
        args={"service_name": "Dental Cleaning"},
        summary="Booking request",
        requires_confirmation=True,
        channel_type="awa_chat",
    )
    return context


def _bound_verified_pending_context(text=None):
    context = _pending_booking_context(text or "")
    pending = context.session_data.state.action_ledger.pending_action
    pending.requires_verified_customer = True
    pending.verification_action = "booking_commitment"
    public_session_id = "11111111-1111-1111-1111-111111111111"
    binding = protected_action_binding(
        tenant_id=context.tenant.id,
        conversation_id=context.session_data.conversation_id,
        channel_session_id=public_session_id,
        config_version=context.session_data.config_version,
        action_nonce=pending.action_nonce,
        tool_key=pending.tool_key,
        args_hash=pending.args_hash,
    )
    verification_ref = "b" * 32
    now = datetime.now(timezone.utc)
    context.session_data.state.action_ledger.verification.otp_verifications = [
        OtpVerificationEvidence(
            binding=binding,
            action_nonce=pending.action_nonce,
            verification_ref=verification_ref,
            tenant_id=str(context.tenant.id),
            conversation_id=str(context.session_data.conversation_id),
            channel_session_id=public_session_id,
            config_version=context.session_data.config_version,
            tool_key=pending.tool_key,
            args_hash=pending.args_hash,
            verified_at=now,
            expires_at=now + timedelta(minutes=5),
            phone_identity_hash=phone_identity_hash("+15550000000"),
        )
    ]
    context.session_data.state.action_ledger.verification.customer_verified = True
    context.incoming_message.update({
        "source_type": "internal_event",
        "channel_type": "awa_chat",
        "provider_message_id": (
            f"awa-internal:otp_verified_pending_action:{binding}:{verification_ref}:{public_session_id}"
        ),
    })
    return context


@pytest.mark.anyio
async def test_engine_pending_confirmation_uses_one_semantic_pending_decision(monkeypatch):
    await _ready_engine(monkeypatch)
    pending_decision = AsyncMock(return_value=PendingActionDecision("unknown", 0.2))
    execute_tool = AsyncMock()
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", pending_decision)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)

    await agentic_engine.execute_step(_pending_booking_context())

    assert pending_decision.await_count == 1
    assert execute_tool.await_count == 0


@pytest.mark.anyio
async def test_engine_confirmed_pending_unrelated_text_still_requires_one_fresh_decision(monkeypatch):
    await _ready_engine(monkeypatch)
    pending_decision = AsyncMock(return_value=PendingActionDecision("unknown", 0.99))
    execute_tool = AsyncMock()
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", pending_decision)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = _pending_booking_context("What do you mean?")
    context.session_data.state.action_ledger.task_status = "confirmed"
    before = context.session_data.state.action_ledger.pending_action.model_copy(deep=True)

    result = await agentic_engine.execute_step(context)

    assert pending_decision.await_count == 1
    execute_tool.assert_not_awaited()
    assert context.session_data.state.action_ledger.pending_action == before
    assert context.session_data.state.action_ledger.task_status == "confirmed"
    assert "confirm" in result.response_text.lower()
    assert "completed" not in result.response_text.lower()


@pytest.mark.anyio
async def test_engine_confirmed_pending_cancel_clears_conversation_only_and_executes_nothing(monkeypatch):
    await _ready_engine(monkeypatch)
    decision = AsyncMock(return_value=PendingActionDecision("cancel", 0.99))
    execute_tool = AsyncMock()
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", decision)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = _pending_booking_context("Cancel")
    context.session_data.state.action_ledger.task_status = "confirmed"

    result = await agentic_engine.execute_step(context)

    execute_tool.assert_not_awaited()
    assert decision.await_count == 1
    assert context.session_data.state.action_ledger.pending_action is None
    assert context.session_data.state.action_ledger.current_task is None
    assert result.next_state == ConversationState.CANCELLED


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["change", "cancel"])
async def test_engine_pending_change_and_cancel_do_not_execute(monkeypatch, action):
    await _ready_engine(monkeypatch)
    decision = AsyncMock(return_value=PendingActionDecision(action, 0.99))
    execute_tool = AsyncMock()
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", decision)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = _pending_booking_context()

    await agentic_engine.execute_step(context)

    assert decision.await_count == 1
    assert execute_tool.await_count == 0
    if action == "cancel":
        assert context.session_data.state.action_ledger.pending_action is None


@pytest.mark.anyio
async def test_engine_confirmed_pending_change_updates_identity_and_invalidates_verification(monkeypatch):
    await _ready_engine(monkeypatch)
    decision = AsyncMock(return_value=PendingActionDecision(
        "change", 0.99, field_updates={"service_name": "General Checkup"}
    ))
    execute_tool = AsyncMock()
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", decision)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = _pending_booking_context("Change it to General Checkup")
    ledger = context.session_data.state.action_ledger
    ledger.task_status = "confirmed"
    ledger.verification.customer_verified = True
    ledger.verification.phone_verified = True
    ledger.verification.otp_verified_actions = ["booking_commitment"]
    original_hash = ledger.pending_action.args_hash
    original_nonce = ledger.pending_action.action_nonce

    result = await agentic_engine.execute_step(context)

    pending = ledger.pending_action
    execute_tool.assert_not_awaited()
    assert decision.await_count == 1
    assert pending.args["service_name"] == "General Checkup"
    assert pending.args_hash != original_hash
    assert pending.action_nonce != original_nonce
    assert ledger.task_status == "awaiting_confirmation"
    assert ledger.verification.customer_verified is False
    assert ledger.verification.phone_verified is False
    assert ledger.verification.otp_verified_actions == []
    assert "confirm" in result.response_text.lower()
    assert result.quick_replies == ["Confirm", "Change", "Cancel"]


@pytest.mark.anyio
async def test_engine_confirmed_pending_tangent_preserves_action_and_returns_grounded_answer(monkeypatch):
    await _ready_engine(monkeypatch)
    decision = AsyncMock(return_value=PendingActionDecision("tangent", 0.99))
    execute_tool = AsyncMock()
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", decision)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = _pending_booking_context("What are your opening hours?")
    context.config.config_data["working_hours"] = {
        "monday": {"is_open": True, "open_time": "09:00", "close_time": "18:00"},
    }
    context.session_data.state.action_ledger.task_status = "confirmed"
    pending = context.session_data.state.action_ledger.pending_action
    pending.idempotency_key = "booking-idempotency-1"
    before = pending.model_copy(deep=True)

    result = await agentic_engine.execute_step(context)

    execute_tool.assert_not_awaited()
    decision.assert_not_awaited()
    assert context.session_data.state.action_ledger.pending_action == before
    assert "monday" in result.response_text.lower()
    assert "confirm" in result.response_text.lower()
    assert "completed" not in result.response_text.lower()


@pytest.mark.anyio
async def test_service_list_tangent_answers_before_resuming_partial_booking(monkeypatch):
    await _ready_engine(monkeypatch)
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    original = {"service_name": "Dental Cleaning"}
    context = _active_booking_context(
        "show me the services",
        fields=["preferred_date"],
        step_data=original,
    )

    result = await agentic_engine.execute_step(context)

    execute_tool.assert_not_awaited()
    assert "available services" in result.response_text.lower()
    assert "dental cleaning" in result.response_text.lower()
    assert "general checkup" in result.response_text.lower()
    assert "which date" in result.response_text.lower()
    assert context.session_data.state.get_task_step_data("booking") == original
    assert context.session_data.state.prompt_focus.requested_fields[0] == "preferred_date"


@pytest.mark.anyio
async def test_current_status_tangent_reports_real_booking_state_without_unavailable_claim(monkeypatch):
    await _ready_engine(monkeypatch)
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    original = {"service_name": "Dental Cleaning", "preferred_date": "tomorrow"}
    context = _active_booking_context(
        "what happening",
        fields=["preferred_time"],
        step_data=original,
    )

    result = await agentic_engine.execute_step(context)

    execute_tool.assert_not_awaited()
    assert "booking is still active" in result.response_text.lower()
    assert "service: dental cleaning" in result.response_text.lower()
    assert "preferred time" in result.response_text.lower()
    assert "unavailable" not in result.response_text.lower()
    assert context.session_data.state.get_task_step_data("booking") == original


@pytest.mark.anyio
async def test_pending_service_tangent_preserves_nonce_and_requires_fresh_customer_control(monkeypatch):
    await _ready_engine(monkeypatch)
    decision = AsyncMock(side_effect=AssertionError("grounded tangent should not need semantic pending control"))
    execute_tool = AsyncMock()
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", decision)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = _pending_booking_context("show me the services")
    before = context.session_data.state.action_ledger.pending_action.model_copy(deep=True)

    result = await agentic_engine.execute_step(context)

    decision.assert_not_awaited()
    execute_tool.assert_not_awaited()
    assert "available services" in result.response_text.lower()
    assert "confirm" in result.response_text.lower()
    assert context.session_data.state.action_ledger.pending_action == before


@pytest.mark.anyio
async def test_engine_duplicate_confirmation_cannot_execute_twice(monkeypatch):
    await _ready_engine(monkeypatch, responses=[AIMessage(content="How else can I help?")])
    decision = AsyncMock(return_value=PendingActionDecision("confirm", 0.99))
    execute_tool = AsyncMock(return_value=ToolExecutionResult(
        tool_key="booking.execute", success=True, user_message="Completed."
    ))
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", decision)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = _pending_booking_context()

    await agentic_engine.execute_step(context)
    context.incoming_message["text"] = "Confirm again"
    await agentic_engine.execute_step(context)

    assert decision.await_count == 1
    assert execute_tool.await_count == 1


@pytest.mark.anyio
async def test_engine_customer_input_after_completed_action_is_a_new_turn(monkeypatch):
    llm = await _ready_engine(monkeypatch, responses=[AIMessage(content="I can help with that new question.")])
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = make_context(message={"text": "A separate unusual question"})
    context.session_data.state.action_ledger.last_tool_result = LastToolResult(
        tool_key="booking.execute", success=True
    )
    context.session_data.state.action_ledger.task_status = "completed"

    await agentic_engine.execute_step(context)

    assert execute_tool.await_count == 0
    assert len(llm.calls) == 1


@pytest.mark.anyio
async def test_engine_customer_input_during_execution_cannot_start_second_execution(monkeypatch):
    await _ready_engine(monkeypatch)
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = _pending_booking_context("Another request while running")
    context.session_data.state.action_ledger.task_status = "executing"

    pending = context.session_data.state.action_ledger.pending_action
    pending.idempotency_key = "booking-idempotency-1"
    before = pending.model_copy(deep=True)

    await agentic_engine.execute_step(context)

    assert execute_tool.await_count == 0
    assert context.session_data.state.action_ledger.task_status == "executing"
    assert context.session_data.state.action_ledger.pending_action == before


@pytest.mark.anyio
async def test_engine_trusted_otp_continuation_executes_exact_action_once(monkeypatch):
    await _ready_engine(monkeypatch, responses=[AIMessage(content="Anything else?")])
    execute_tool = AsyncMock(return_value=ToolExecutionResult(
        tool_key="booking.execute",
        success=True,
        user_message="Booking completed.",
        created_resource_type="booking",
        created_resource_id="booking-public-ref",
    ))
    pending_decision = AsyncMock(side_effect=AssertionError("internal continuation used customer semantics"))
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", pending_decision)
    monkeypatch.setattr("backend.logic.engine.llm.get_llm", MagicMock(side_effect=AssertionError("post-result prose must not gate execution")))
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = _bound_verified_pending_context()
    context.session_data.state.agentic_history = [
        {
            "type": "ai",
            "data": {
                "content": "",
                "tool_calls": [{
                    "name": "booking_execute",
                    "args": {"service_name": "Dental Cleaning"},
                    "id": "legacy-unsigned-call",
                    "type": "tool_call",
                }],
            },
        },
        {
            "type": "tool",
            "data": {
                "content": '{"success": false, "denied_reason": "confirmation_required"}',
                "tool_call_id": "legacy-unsigned-call",
            },
        },
    ]

    result = await agentic_engine.execute_step(context)
    await agentic_engine.execute_step(context)

    assert execute_tool.await_count == 1
    pending_decision.assert_not_awaited()
    assert context.session_data.state.action_ledger.pending_action is None
    assert "completed" in result.response_text.lower()
    assert context.session_data.state.action_ledger.last_tool_result.created_resource_id == "booking-public-ref"


@pytest.mark.anyio
async def test_engine_stale_otp_continuation_cannot_execute_replacement_action(monkeypatch):
    await _ready_engine(monkeypatch)
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = _bound_verified_pending_context()
    stale_provider_id = context.incoming_message["provider_message_id"]
    state = context.session_data.state
    state.prepare_pending_action(
        tool_key="booking.execute",
        args={"service_name": "General Checkup"},
        summary="Replacement booking",
        requires_confirmation=True,
        requires_verified_customer=True,
        verification_action="booking_commitment",
        channel_type="awa_chat",
    )
    context.incoming_message["provider_message_id"] = stale_provider_id
    replacement = state.action_ledger.pending_action.model_copy(deep=True)

    result = await agentic_engine.execute_step(context)

    execute_tool.assert_not_awaited()
    assert state.action_ledger.pending_action == replacement
    assert "confirm" in result.response_text.lower()


@pytest.mark.anyio
@pytest.mark.parametrize("mismatch", ["pending_task", "config"])
async def test_engine_invariant_mismatch_blocks_protected_execution_without_mutation(monkeypatch, mismatch):
    await _ready_engine(monkeypatch)
    decision = AsyncMock(return_value=PendingActionDecision("confirm", 0.99))
    execute_tool = AsyncMock()
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", decision)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = _pending_booking_context("Confirm")
    if mismatch == "pending_task":
        context.session_data.state.action_ledger.current_task = "order"
        context.session_data.current_module = "order"
        context.session_data.state.prompt_focus = None
    else:
        context.session_data.config_version += 1
    before = context.session_data.state.model_copy(deep=True)

    result = await agentic_engine.execute_step(context)

    execute_tool.assert_not_awaited()
    assert context.session_data.state == before
    assert "safely complete" in result.response_text.lower()


@pytest.mark.anyio
async def test_config_binding_mismatch_blocks_active_field_mutation(monkeypatch):
    await _ready_engine(monkeypatch)
    context = _active_booking_context("Dental Cleaning")
    context.session_data.config_version += 1
    before_step_data = deepcopy(context.session_data.state.step_data)
    before_scoped_data = deepcopy(context.session_data.state.scoped_step_data)
    before_focus = context.session_data.state.prompt_focus.model_copy(deep=True)

    await agentic_engine.execute_step(context)

    assert context.session_data.state.step_data == before_step_data
    assert context.session_data.state.scoped_step_data == before_scoped_data
    assert context.session_data.state.prompt_focus == before_focus


@pytest.mark.anyio
async def test_engine_unknown_input_does_not_replay_saved_browser_orientation(monkeypatch):
    await _ready_engine(monkeypatch, responses=[AIMessage(content="Could you tell me a little more?")])
    context = make_context(message={"text": "An unusual thought with no clear request"})
    context.session_data.state.orientation_config_pending = True
    before_orientation_count = sum(
        item.get("data", {}).get("semantic_role") == "orientation"
        for item in context.session_data.state.agentic_history
    )
    before_state = context.session_data.state.workflow_state

    result = await agentic_engine.execute_step(context)

    assert "welcome to smile clinic" not in result.response_text.lower()
    assert result.quick_replies != ["Book appointment", "Services & prices"]
    after_orientation_count = sum(
        item.get("data", {}).get("semantic_role") == "orientation"
        for item in context.session_data.state.agentic_history
    )
    assert after_orientation_count == before_orientation_count
    assert context.session_data.state.workflow_state == before_state
    assert context.session_data.state.orientation_config_pending is True


@pytest.mark.anyio
async def test_runtime_greeting_and_punctuation_do_not_replay_static_orientation(monkeypatch):
    llm = await _ready_engine(monkeypatch)
    orientation = "Hello! Welcome to Smile Clinic. I can help with appointments and services."
    greeting_context = make_context(message={"text": "hi"})
    greeting_context.session_data.state.orientation_config_pending = True

    greeting = await agentic_engine.execute_step(greeting_context)

    assert "hi! how can i help you today?" in greeting.response_text.lower()
    assert greeting.response_text != orientation
    assert greeting_context.session_data.state.action_ledger.current_task is None
    assert llm.calls == []

    unknown_context = make_context(message={"text": "?"})
    unknown_context.session_data.state.orientation_config_pending = True
    unknown = await agentic_engine.execute_step(unknown_context)

    assert "not sure what you mean" in unknown.response_text.lower()
    assert unknown.response_text != orientation
    assert unknown_context.session_data.state.action_ledger.current_task is None
    assert unknown_context.session_data.state.orientation_config_pending is True
    assert llm.calls == []


@pytest.mark.anyio
async def test_punctuation_after_cancel_does_not_resurrect_booking_or_orientation(monkeypatch):
    await _ready_engine(monkeypatch)
    context = _active_booking_context("Cancel", step_data={"service_name": "Dental Cleaning"})

    await agentic_engine.execute_step(context)
    context.incoming_message = {"text": "?", "channel_type": "awa_chat"}
    result = await agentic_engine.execute_step(context)

    assert "not sure what you mean" in result.response_text.lower()
    assert "welcome to smile clinic" not in result.response_text.lower()
    assert context.session_data.state.action_ledger.current_task is None
    assert context.session_data.current_module is None
    assert context.session_data.state.scoped_step_data == {}


@pytest.mark.anyio
async def test_active_scope_mirror_mismatch_alone_does_not_block_safe_ordinary_response(monkeypatch):
    await _ready_engine(monkeypatch)
    context = _active_booking_context(
        "Continue", fields=["preferred_date"], step_data={"service_name": "Dental Cleaning"}
    )
    context.session_data.state.step_data = {"service_name": "Different mirror"}
    assert {item.code for item in validate_canonical_state(context)} == {"active_scope_mirror_mismatch"}

    result = await agentic_engine.execute_step(context)

    assert result.response_text
    assert "safely complete" not in result.response_text.lower()


@pytest.mark.anyio
async def test_engine_active_tangent_preserves_fields_and_prompt_focus(monkeypatch):
    await _ready_engine(monkeypatch)
    existing = {"service_name": "Dental Cleaning"}
    context = _active_booking_context(
        "What are your opening hours?", fields=["preferred_date"], step_data=existing
    )

    await agentic_engine.execute_step(context)

    assert context.session_data.state.get_task_step_data("booking") == existing
    assert context.session_data.state.prompt_focus.workflow == "booking"
    assert context.session_data.state.prompt_focus.requested_fields == ["preferred_date"]


@pytest.mark.anyio
async def test_engine_authoritative_mode_applies_canonical_control(monkeypatch):
    await _ready_engine(monkeypatch)
    import backend.logic.engine.core as core_module

    original = core_module.decide_canonical_control
    control_spy = MagicMock(side_effect=original)
    monkeypatch.setattr(core_module, "decide_canonical_control", control_spy)

    await agentic_engine.execute_step(_active_booking_context("Continue", fields=["preferred_date"]))

    assert control_spy.call_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["shadow", "legacy"])
async def test_engine_shadow_and_legacy_modes_do_not_mutate_through_canonical_control(monkeypatch, mode):
    await _ready_engine(monkeypatch, mode=mode)
    canonical_control = MagicMock(side_effect=AssertionError("canonical control ran outside authoritative mode"))
    monkeypatch.setattr("backend.logic.engine.core.decide_canonical_control", canonical_control)
    context = make_context(message={"text": "What can you do?"})

    await agentic_engine.execute_step(context)

    assert canonical_control.call_count == 0


def test_pending_action_nonce_preserves_identical_lifecycle_and_round_trips():
    context = _pending_booking_context()
    state = context.session_data.state
    first = state.action_ledger.pending_action.action_nonce
    state.prepare_pending_action(
        tool_key="booking.execute",
        args={"service_name": "Dental Cleaning"},
        requires_confirmation=True,
    )
    assert state.action_ledger.pending_action.action_nonce == first

    restored = type(context.session_data).model_validate(context.session_data.model_dump(mode="json"))
    assert restored.state.action_ledger.pending_action.action_nonce == first

    state.prepare_pending_action(
        tool_key="booking.execute",
        args={"service_name": "General Checkup"},
        requires_confirmation=True,
    )
    assert state.action_ledger.pending_action.action_nonce != first


def test_cancelled_identical_future_action_gets_new_nonce_and_no_old_authority():
    import backend.logic.engine.core as core_module
    context = _bound_verified_pending_context()
    old_nonce = context.session_data.state.action_ledger.pending_action.action_nonce
    old_provider_id = context.incoming_message["provider_message_id"]

    core_module._cancel_active_task(context)
    context.session_data.state.prepare_pending_action(
        tool_key="booking.execute",
        args={"service_name": "Dental Cleaning"},
        requires_confirmation=True,
        requires_verified_customer=True,
        channel_type="awa_chat",
    )
    context.incoming_message["provider_message_id"] = old_provider_id

    assert context.session_data.state.action_ledger.pending_action.action_nonce != old_nonce
    assert context.session_data.state.action_ledger.verification.otp_verifications == []
    assert core_module._trusted_otp_continuation_matches(context) is None


@pytest.mark.anyio
async def test_expired_otp_continuation_cannot_dispatch(monkeypatch):
    await _ready_engine(monkeypatch)
    context = _bound_verified_pending_context()
    context.session_data.state.action_ledger.verification.otp_verifications[0].expires_at = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    execute_tool = AsyncMock()
    claim = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    monkeypatch.setattr("backend.logic.engine.core.claim_protected_action_execution", claim)

    result = await agentic_engine.execute_step(context)

    execute_tool.assert_not_awaited()
    claim.assert_not_awaited()
    assert "confirm" in result.response_text.lower()
    assert context.session_data.state.action_ledger.pending_action is not None


@pytest.mark.anyio
async def test_concurrent_hydrated_continuations_dispatch_tool_once(monkeypatch):
    await _ready_engine(monkeypatch)
    import backend.logic.engine.core as core_module
    fake_redis = AtomicExecutionRedis()
    monkeypatch.setattr(core_module, "claim_protected_action_execution", real_execution_claim)
    monkeypatch.setattr(core_module, "get_redis_client", lambda: fake_redis)
    execute_tool = AsyncMock(return_value=ToolExecutionResult(
        tool_key="booking.execute", success=True, user_message="Booked."
    ))
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    first = _bound_verified_pending_context()
    second = deepcopy(first)
    second.incoming_message["event_id"] = "different-replay-event"
    nonce = first.session_data.state.action_ledger.pending_action.action_nonce
    args_hash = first.session_data.state.action_ledger.pending_action.args_hash

    results = await asyncio.gather(
        agentic_engine.execute_step(first),
        agentic_engine.execute_step(second),
    )

    assert execute_tool.await_count == 1
    assert len(fake_redis.values) == 1
    assert any("already" in item.response_text.lower() for item in results)
    loser = second if second.session_data.state.action_ledger.pending_action else first
    assert loser.session_data.state.action_ledger.pending_action.action_nonce == nonce
    assert loser.session_data.state.action_ledger.pending_action.args_hash == args_hash
    assert loser.session_data.state.action_ledger.last_tool_result is None


@pytest.mark.anyio
async def test_failed_dispatched_action_consumes_authority_and_blocks_replay(monkeypatch):
    await _ready_engine(monkeypatch)
    import backend.logic.engine.core as core_module
    fake_redis = AtomicExecutionRedis()
    monkeypatch.setattr(core_module, "claim_protected_action_execution", real_execution_claim)
    monkeypatch.setattr(core_module, "get_redis_client", lambda: fake_redis)
    execute_tool = AsyncMock(return_value=ToolExecutionResult(
        tool_key="booking.execute",
        success=False,
        denied_reason="provider_failed",
        user_message="Could not book.",
    ))
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    first = _bound_verified_pending_context()
    replay = deepcopy(first)
    original_nonce = first.session_data.state.action_ledger.pending_action.action_nonce

    await agentic_engine.execute_step(first)
    replay_result = await agentic_engine.execute_step(replay)

    assert execute_tool.await_count == 1
    assert first.session_data.state.action_ledger.task_status == "blocked"
    assert first.session_data.state.action_ledger.pending_action.action_nonce != original_nonce
    assert len(first.session_data.state.action_ledger.verification.otp_verifications) == 1
    assert first.session_data.state.action_ledger.verification.otp_verifications[0].consumed is True
    assert replay.session_data.state.action_ledger.last_tool_result is None
    assert "already" in replay_result.response_text.lower()


@pytest.mark.parametrize("mismatch", ["tenant", "conversation", "session", "config"])
@pytest.mark.anyio
async def test_trusted_continuation_rejects_cross_identity_mismatch(monkeypatch, mismatch):
    import backend.logic.engine.core as core_module
    await _ready_engine(monkeypatch)
    context = _bound_verified_pending_context()
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    if mismatch == "tenant":
        context.tenant.id = type(context.tenant.id)("22222222-2222-2222-2222-222222222222")
    elif mismatch == "conversation":
        context.session_data.conversation_id = type(context.session_data.conversation_id)(
            "33333333-3333-3333-3333-333333333333"
        )
    elif mismatch == "session":
        context.incoming_message["provider_message_id"] = re.sub(
            r"[0-9a-f-]{36}$",
            "44444444-4444-4444-4444-444444444444",
            context.incoming_message["provider_message_id"],
        )
    else:
        context.session_data.config_version += 1

    assert core_module._trusted_otp_continuation_matches(context) is None
    await agentic_engine.execute_step(context)
    execute_tool.assert_not_awaited()
