from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.core.config import settings
from backend.core.context import (
    ConversationSession,
    ExecutionContext,
    SessionState,
    Tenant,
    TenantConfig,
    User,
)
from backend.logic.engine.core import AgenticEngine, WorkflowResult
from backend.api.routes.public_chat import _save_public_chat_greeting
from backend.logic.engine.field_extraction import BookingFieldExtraction
from backend.logic.engine.pending_decision import PendingActionDecision
from backend.logic.engine.turn_interpretation import (
    ClarificationReason,
    ConfirmationIntent,
    InterpretationSource,
    ShadowTurnSnapshot,
    TurnInterpretation,
    TurnInterpretationCoordinator,
    TurnKind,
)


@pytest.fixture(autouse=True)
def explicit_legacy_behavior_runtime(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False)


pytestmark = pytest.mark.customer_turn


AVAILABLE = {
    "booking", "order", "payments", "lead_capture", "support",
    "reminders", "reviews", "faq", "human_handoff",
}


def _route(
    intent="unknown", confidence=0.4, route_type="unknown", *,
    tangent=False, switch=False, reason="no_deterministic_match",
):
    return SimpleNamespace(
        intent=intent,
        confidence=confidence,
        route_type=route_type,
        is_tangent=tangent,
        should_clear_active_task=switch,
        reason=reason,
    )


def _snapshot(
    text: str,
    *,
    active=None,
    requested=(),
    channel="awa_chat",
    pending=False,
    config_version=7,
):
    return ShadowTurnSnapshot(
        text=text,
        channel=channel,
        active_workflow=active,
        requested_fields=tuple(requested),
        config_version=config_version,
        pending_action=pending,
        pending_status="awaiting_confirmation" if pending else "idle",
    )


def _context(text="hello", *, active=None, channel="awa_chat", config_version=7):
    tenant_id = uuid4()
    state = SessionState()
    session = ConversationSession(
        id=uuid4(), tenant_id=tenant_id, conversation_id=uuid4(),
        config_version=config_version, state=state, current_module=active,
    )
    if active:
        state.action_ledger.current_task = active
        state.action_ledger.task_status = "collecting"
        state.set_prompt_focus(workflow=active, requested_fields=["service_name"])
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Clinic", plan_code="pro", status="active"),
        user=User(id=uuid4(), phone_number="unknown"),
        session_data=session,
        config=TenantConfig(tenant_id=tenant_id, version=config_version, config_data={}),
        incoming_message={"text": text, "channel_type": channel, "_runtime_available_modules": sorted(AVAILABLE)},
    )


def _booking_extraction(**overrides):
    values = {
        "detected_language": "en",
        "workflow": "booking",
        "workflow_action": "continue",
        "confidence": 0.94,
        "field_updates": {"service_name": "Teeth Cleaning"},
        "field_confidence": {"service_name": 0.95},
        "clarification_needed": False,
    }
    values.update(overrides)
    return BookingFieldExtraction(**values)


def test_schema_validates_bounds_workflows_fields_and_safe_defaults():
    frame = TurnInterpretation()
    assert frame.interpretation_version == 1
    assert frame.turn_kind == TurnKind.UNKNOWN
    assert frame.field_updates == {}

    normalized = TurnInterpretation(
        turn_kind="new_workflow_request",
        candidate_workflow="payment",
        workflow_action="continue",
        confidence=0.7,
    )
    assert normalized.candidate_workflow == "payments"

    with pytest.raises(ValidationError):
        TurnInterpretation(candidate_workflow="invented_workflow")
    with pytest.raises(ValidationError):
        TurnInterpretation(confidence=1.01)
    with pytest.raises(ValidationError):
        TurnInterpretation(field_updates={"customer_id": "internal"})
    with pytest.raises(ValidationError):
        TurnInterpretation(field_updates={"service_name": {"nested": "not allowed"}})
    with pytest.raises(ValidationError):
        TurnInterpretation(field_updates={"service_name": "x"}, field_confidence={"phone": 0.9})

    dumped = normalized.model_dump(mode="json")
    assert "raw_provider" not in dumped and "prompt" not in dumped


def test_natural_first_turn_booking_reuses_extraction_candidates():
    coordinator = TurnInterpretationCoordinator()
    extraction = _booking_extraction(
        field_updates={
            "service_name": "Teeth Cleaning",
            "booking_date": "tomorrow",
            "booking_time": "evening",
        },
        field_confidence={"service_name": 0.98, "booking_date": 0.9, "booking_time": 0.88},
    )
    frame = coordinator.interpret_turn(
        _snapshot("I need teeth cleaning tomorrow evening"),
        evidence={
            "routing_decision": _route("booking", 0.9, "workflow", reason="configured_service_booking_candidate"),
            "field_extraction": extraction,
        },
        runtime_available_modules=AVAILABLE,
    )
    assert frame.turn_kind == TurnKind.NEW_WORKFLOW_REQUEST
    assert frame.candidate_workflow == "booking"
    assert set(frame.field_updates) == {"service_name", "booking_date", "booking_time"}
    assert frame.confidence == pytest.approx(0.94)


def test_active_booking_multifield_answer_uses_prompt_focus_without_mutation():
    coordinator = TurnInterpretationCoordinator()
    snapshot = _snapshot(
        "Around 4 pm. My name is Arjun and my phone is 9876543210.",
        active="booking", requested=("booking_time", "customer_name", "phone"),
    )
    extraction = _booking_extraction(
        field_updates={"booking_time": "16:00", "customer_name": "Arjun", "phone": "9876543210"},
        field_confidence={"booking_time": 0.94, "customer_name": 0.96, "phone": 0.97},
    )
    frame = coordinator.interpret_turn(
        snapshot,
        evidence={"routing_decision": _route("booking", 0.8, "active_task", reason="active_task_continuation"), "field_extraction": extraction},
        runtime_available_modules=AVAILABLE,
    )
    assert frame.turn_kind == TurnKind.ACTIVE_WORKFLOW_ANSWER
    assert frame.active_workflow == "booking"
    assert frame.requested_fields == ["booking_time", "customer_name", "phone"]
    assert set(frame.field_updates) == {"booking_time", "customer_name", "phone"}
    assert frame.explicit_switch is False


def test_active_cavity_filling_is_field_answer_not_switch():
    frame = TurnInterpretationCoordinator().interpret_turn(
        _snapshot("Cavity Filling", active="booking", requested=("service_name",)),
        evidence={
            "routing_decision": _route("booking", 0.8, "active_task", reason="active_task_likely_field_value"),
            "field_extraction": _booking_extraction(field_updates={"service_name": "Cavity Filling"}, field_confidence={"service_name": 0.99}),
        },
        runtime_available_modules=AVAILABLE,
    )
    assert frame.turn_kind == TurnKind.ACTIVE_WORKFLOW_ANSWER
    assert frame.field_updates == {"service_name": "Cavity Filling"}
    assert frame.explicit_switch is False


def test_keyword_collision_in_valid_field_value_is_not_command_authority():
    frame = TurnInterpretationCoordinator().interpret_turn(
        _snapshot("Cancel Culture Burger", active="order", requested=("item_name",)),
        evidence={
            "routing_decision": _route("order", 0.8, "active_task", reason="active_task_likely_field_value"),
            "field_extraction": SimpleNamespace(
                workflow="order", workflow_action="continue", confidence=0.95,
                field_updates={"item_name": "Cancel Culture Burger"},
                field_confidence={"item_name": 0.98}, detected_language="en",
            ),
        },
        runtime_available_modules=AVAILABLE,
    )
    assert frame.turn_kind == TurnKind.ACTIVE_WORKFLOW_ANSWER
    assert frame.cancel_requested is False
    assert frame.field_updates == {"item_name": "Cancel Culture Burger"}


def test_explicit_continue_is_bounded_without_mutating_workflow():
    frame = TurnInterpretationCoordinator().interpret_turn(
        _snapshot("Continue", active="booking", requested=("booking_time",)),
        evidence={"routing_decision": _route("booking", 0.8, "active_task", reason="active_task_short_continuation")},
        runtime_available_modules=AVAILABLE,
    )
    assert frame.turn_kind == TurnKind.CONTINUE
    assert frame.continue_requested is True


def test_capability_request_and_vague_unknown_are_distinct():
    coordinator = TurnInterpretationCoordinator()
    capability = coordinator.interpret_turn(
        _snapshot("What can you do?"),
        evidence={"routing_decision": _route("greeting", 1.0, "direct_answer", reason="greeting")},
        runtime_available_modules=AVAILABLE,
    )
    unknown = coordinator.interpret_turn(
        _snapshot("I was wondering about something unusual"),
        evidence={"routing_decision": _route()},
        runtime_available_modules=AVAILABLE,
    )
    assert capability.turn_kind == TurnKind.CAPABILITY_REQUEST
    assert capability.capability_request is True and capability.needs_clarification is False
    assert unknown.turn_kind == TurnKind.UNKNOWN
    assert unknown.needs_clarification is True
    assert unknown.clarification_reason == ClarificationReason.UNRECOGNIZED_REQUEST


def test_explicit_switch_and_tangent_retain_active_workflow_in_frame():
    coordinator = TurnInterpretationCoordinator()
    switch = coordinator.interpret_turn(
        _snapshot("Actually, cancel this booking and help me place an order.", active="booking"),
        evidence={"routing_decision": _route("ambiguous", 0.95, "clarification", switch=True, reason="mixed_workflow_intent")},
        runtime_available_modules=AVAILABLE,
    )
    tangent = coordinator.interpret_turn(
        _snapshot("What time do you close?", active="booking", requested=("booking_time",)),
        evidence={"routing_decision": _route("faq", 0.85, "direct_answer", tangent=True, reason="active_task_faq_tangent")},
        runtime_available_modules=AVAILABLE,
    )
    assert switch.explicit_switch is True
    assert switch.cancel_requested is True
    assert switch.candidate_workflow == "order"
    assert tangent.turn_kind == TurnKind.TANGENT
    assert tangent.active_workflow == "booking"
    assert tangent.tangent is True


@pytest.mark.parametrize(
    ("action", "kind", "confirmation"),
    [
        ("confirm", TurnKind.CONFIRMATION, ConfirmationIntent.CONFIRM),
        ("cancel", TurnKind.CANCEL, ConfirmationIntent.CANCEL),
        ("change", TurnKind.CHANGE, ConfirmationIntent.CHANGE),
        ("continue", TurnKind.CONTINUE, ConfirmationIntent.NONE),
        ("tangent", TurnKind.TANGENT, ConfirmationIntent.NONE),
        ("unknown", TurnKind.UNKNOWN, ConfirmationIntent.UNCLEAR),
    ],
)
def test_pending_action_semantics_never_execute(action, kind, confirmation):
    frame = TurnInterpretationCoordinator().interpret_turn(
        _snapshot(action, active="booking", pending=True),
        evidence={"pending_decision": PendingActionDecision(action=action, confidence=0.9)},
        runtime_available_modules=AVAILABLE,
    )
    assert frame.turn_kind == kind
    assert frame.confirmation_intent == confirmation
    assert frame.source == InterpretationSource.PENDING_DECISION


@pytest.mark.parametrize(
    ("workflow", "text"),
    [
        ("order", "I want to order"),
        ("payments", "I need to make a payment"),
        ("lead_capture", "I am interested, contact me"),
        ("support", "I have a support issue"),
        ("reminders", "Remind me tomorrow"),
        ("reviews", "I want to leave a review"),
        ("human_handoff", "I want a human representative"),
    ],
)
def test_representative_cross_workflow_frames(workflow, text):
    route_intent = "support" if workflow == "human_handoff" else workflow
    frame = TurnInterpretationCoordinator().interpret_turn(
        _snapshot(text),
        evidence={"routing_decision": _route(route_intent, 0.85, "workflow", reason="deterministic_keyword_match")},
        runtime_available_modules=AVAILABLE,
    )
    assert frame.turn_kind == TurnKind.NEW_WORKFLOW_REQUEST
    assert frame.candidate_workflow == workflow


def test_channel_and_behavior_profile_neutrality():
    coordinator = TurnInterpretationCoordinator()
    evidence = {"routing_decision": _route("booking", 0.8, "active_task", reason="active_task_likely_field_value"), "field_extraction": _booking_extraction()}
    frames = [
        coordinator.interpret_turn(
            _snapshot("Teeth Cleaning", active="booking", requested=("service_name",), channel=channel),
            evidence=evidence,
            runtime_available_modules=AVAILABLE,
        )
        for channel in ("awa_chat", "awa_chat", "whatsapp", "whatsapp", "whatsapp")
    ]
    semantic = [frame.model_dump(mode="json") for frame in frames]
    assert all(item == semantic[0] for item in semantic)

    context_a = _context("Teeth Cleaning", active="booking")
    context_b = _context("Teeth Cleaning", active="booking")
    context_a.runtime_behavior_profile = SimpleNamespace(tone="formal", menu_policy="always")
    context_b.runtime_behavior_profile = SimpleNamespace(tone="friendly", menu_policy="never")
    assert coordinator.capture(context_a).text == coordinator.capture(context_b).text


def test_capture_uses_selected_config_and_shadow_does_not_mutate_state():
    coordinator = TurnInterpretationCoordinator()
    context = _context("Cavity Filling", active="booking", config_version=11)
    before = context.session_data.model_dump(mode="json")
    snapshot = coordinator.capture(context)
    frame = coordinator.interpret_turn(
        snapshot,
        evidence={"routing_decision": _route("booking", 0.8, "active_task", reason="active_task_likely_field_value"), "field_extraction": _booking_extraction()},
        runtime_available_modules=AVAILABLE,
    )
    assert snapshot.config_version == 11
    assert frame.requested_fields == ["service_name"]
    assert context.session_data.model_dump(mode="json") == before


def test_unavailable_candidate_and_provider_failure_fail_closed():
    coordinator = TurnInterpretationCoordinator()
    unavailable = coordinator.interpret_turn(
        _snapshot("I want to order"),
        evidence={"routing_decision": _route("order", 0.9, "workflow")},
        runtime_available_modules={"booking"},
    )
    failed = coordinator.interpret_turn(
        _snapshot("tomorrow", active="booking", requested=("booking_date",)),
        evidence={"semantic_failure": "semantic_provider_failed", "routing_decision": _route("booking", 0.4, "active_task", reason="active_task_continuation")},
        runtime_available_modules=AVAILABLE,
    )
    assert unavailable.candidate_workflow_available is False
    assert unavailable.needs_clarification is True
    assert unavailable.clarification_reason == ClarificationReason.UNSUPPORTED_WORKFLOW
    assert failed.needs_clarification is True
    assert failed.clarification_reason == ClarificationReason.SEMANTIC_PROVIDER_FAILED


def test_legacy_comparison_telemetry_contains_names_not_values():
    coordinator = TurnInterpretationCoordinator()
    phone = "9876543210"
    name = "Arjun"
    extraction = _booking_extraction(
        field_updates={"customer_name": name, "phone": phone},
        field_confidence={"customer_name": 0.9, "phone": 0.9},
    )
    evidence = {"routing_decision": _route("booking", 0.8, "active_task", reason="active_task_continuation"), "field_extraction": extraction}
    frame = coordinator.interpret_turn(
        _snapshot("private", active="booking"), evidence=evidence, runtime_available_modules=AVAILABLE,
    )
    comparison = coordinator.compare_with_legacy(frame, evidence=evidence, config_version=7, channel="awa_chat")
    serialized = str(comparison.model_dump(mode="json"))
    assert set(comparison.field_names) == {"customer_name", "phone"}
    assert phone not in serialized and name not in serialized
    assert "prompt" not in serialized and "raw" not in serialized


def test_emitted_shadow_events_are_pii_safe(monkeypatch):
    coordinator = TurnInterpretationCoordinator()
    context = _context("private", active="booking")
    phone = "9876543210"
    name = "Arjun"
    context.shadow_turn_evidence = {
        "routing_decision": _route("booking", 0.8, "active_task", reason="active_task_continuation"),
        "field_extraction": _booking_extraction(
            field_updates={"customer_name": name, "phone": phone},
            field_confidence={"customer_name": 0.9, "phone": 0.9},
        ),
    }
    events = []
    monkeypatch.setattr(
        "backend.logic.engine.turn_interpretation.logger.info",
        lambda message, **kwargs: events.append((message, kwargs)),
    )
    coordinator.observe(context, coordinator.capture(context))
    serialized = str(events)
    assert "turn_interpretation_created" in serialized
    assert phone not in serialized and name not in serialized
    assert "customer_name" in serialized and "phone" in serialized


@pytest.mark.asyncio
async def test_engine_shadow_creates_exactly_one_frame_without_response_or_call_count_impact(monkeypatch):
    monkeypatch.setenv("TURN_INTERPRETATION_MODE", "shadow")
    monkeypatch.setattr(settings, "TURN_INTERPRETATION_MODE", "shadow")
    engine = AgenticEngine()
    context = _context("What can you do?")
    authoritative = AsyncMock(return_value=WorkflowResult(response_text="Authoritative response", quick_replies=["Book appointment"]))

    async def authoritative_step(ctx):
        ctx.shadow_turn_evidence["routing_decision"] = _route("greeting", 1.0, "direct_answer", reason="greeting")
        return await authoritative()

    observe = Mock(wraps=TurnInterpretationCoordinator().observe)
    monkeypatch.setattr(engine, "_execute_step_inner", authoritative_step)
    monkeypatch.setattr("backend.logic.engine.core.turn_interpretation_coordinator.observe", observe)

    result = await engine.execute_step(context)
    assert result.response_text == "Authoritative response"
    assert result.quick_replies == ["Book appointment"]
    assert authoritative.await_count == 1
    assert observe.call_count == 1
    assert context.turn_interpretation.turn_kind == TurnKind.CAPABILITY_REQUEST
    assert context.session_data.state.workflow_state == SessionState().workflow_state


@pytest.mark.asyncio
async def test_disabling_shadow_restores_exact_runtime_and_orientation_creates_no_frame(monkeypatch):
    monkeypatch.setenv("TURN_INTERPRETATION_MODE", "legacy")
    monkeypatch.setattr(settings, "TURN_INTERPRETATION_MODE", "legacy")
    engine = AgenticEngine()
    context = _context("hello")
    authoritative = AsyncMock(return_value=WorkflowResult(response_text="same", quick_replies=["A"]))
    observe = Mock()
    monkeypatch.setattr(engine, "_execute_step_inner", authoritative)
    monkeypatch.setattr("backend.logic.engine.core.turn_interpretation_coordinator.observe", observe)
    monkeypatch.setattr(settings, "ENABLE_TURN_INTERPRETATION_SHADOW", False)
    result = await engine.execute_step(context)
    assert (result.response_text, result.quick_replies) == ("same", ["A"])
    observe.assert_not_called()
    assert context.turn_interpretation is None


@pytest.mark.asyncio
async def test_shadow_failure_is_safe_and_does_not_change_authoritative_result(monkeypatch):
    monkeypatch.setenv("TURN_INTERPRETATION_MODE", "shadow")
    monkeypatch.setattr(settings, "TURN_INTERPRETATION_MODE", "shadow")
    engine = AgenticEngine()
    context = _context("hello")
    initial_state = context.session_data.state.model_copy(deep=True)
    authoritative = AsyncMock(return_value=WorkflowResult(response_text="still works"))
    observe = Mock(side_effect=RuntimeError("private provider output"))
    monkeypatch.setattr(engine, "_execute_step_inner", authoritative)
    monkeypatch.setattr("backend.logic.engine.core.turn_interpretation_coordinator.observe", observe)
    result = await engine.execute_step(context)
    assert result.response_text == "still works"
    authoritative.assert_awaited_once_with(context)
    observe.assert_called_once()
    assert context.turn_interpretation.turn_kind == TurnKind.UNKNOWN
    assert context.turn_interpretation.reason_codes == ["shadow_failure"]
    assert context.session_data.state == initial_state


@pytest.mark.asyncio
async def test_snapshot_failure_still_produces_one_safe_frame(monkeypatch):
    monkeypatch.setenv("TURN_INTERPRETATION_MODE", "shadow")
    monkeypatch.setattr(settings, "TURN_INTERPRETATION_MODE", "shadow")
    engine = AgenticEngine()
    context = _context("hello")
    initial_state = context.session_data.state.model_copy(deep=True)
    authoritative = AsyncMock(return_value=WorkflowResult(response_text="unchanged"))
    capture = Mock(side_effect=RuntimeError("private snapshot failure"))
    monkeypatch.setattr(engine, "_execute_step_inner", authoritative)
    monkeypatch.setattr("backend.logic.engine.core.turn_interpretation_coordinator.capture", capture)
    result = await engine.execute_step(context)
    assert result.response_text == "unchanged"
    authoritative.assert_awaited_once_with(context)
    capture.assert_called_once_with(context)
    assert context.turn_interpretation.turn_kind == TurnKind.UNKNOWN
    assert context.turn_interpretation.reason_codes == ["shadow_failure"]
    assert context.session_data.state == initial_state


@pytest.mark.asyncio
async def test_existing_booking_extractor_result_is_reused_once(monkeypatch):
    engine = AgenticEngine()
    context = _context("Actually use an order", active="booking")
    context.config.config_data = {"modules": {"booking": {"enabled": True}}}
    extraction = _booking_extraction(workflow_action="switch", field_updates={}, field_confidence={})
    extractor = AsyncMock(return_value=extraction)
    monkeypatch.setattr(settings, "ENABLE_TURN_INTERPRETATION_SHADOW", True)
    monkeypatch.setattr("backend.logic.engine.core.extract_booking_fields", extractor)
    monkeypatch.setattr(engine.tool_registry, "get_tool", lambda _key: SimpleNamespace(module=object()))

    result = await engine._handle_semantic_booking_field_extraction(
        context,
        _route("order", 0.9, "workflow", switch=True, reason="explicit_workflow_switch"),
    )
    assert result is None
    assert extractor.await_count == 1
    assert context.shadow_turn_evidence["field_extraction"] is extraction


@pytest.mark.asyncio
async def test_public_orientation_persistence_does_not_create_shadow_frame(monkeypatch):
    save = AsyncMock(return_value={"created": True})
    observe = Mock()
    monkeypatch.setattr("backend.api.routes.public_chat.db_client.save_message", save)
    monkeypatch.setattr("backend.logic.engine.core.turn_interpretation_coordinator.observe", observe)
    await _save_public_chat_greeting(
        tenant_id=uuid4(), conversation_id=uuid4(), session_id=uuid4(),
        greeting_message="Welcome", suggestions=["Book", "Order"],
    )
    save.assert_awaited_once()
    observe.assert_not_called()
