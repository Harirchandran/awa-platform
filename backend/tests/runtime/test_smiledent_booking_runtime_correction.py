from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from backend.core.config import settings
from backend.core.states import ConversationState
from backend.logic.engine.configured_option_resolver import resolve_configured_option
from backend.logic.engine.core import _booking_missing_required_fields, agentic_engine
from backend.logic.engine.field_extraction import (
    BookingFieldExtraction,
    _deterministic_active_booking_field_extraction,
)
from backend.logic.engine.pending_decision import PendingActionDecision
from backend.logic.engine.turn_interpretation import TurnInterpretation, TurnKind, WorkflowAction
from backend.modules.booking_module import booking_module
from backend.tests.runtime.test_canonical_control_runtime import _ready_engine
from backend.tests.unit.test_conversation_ux import make_context


pytestmark = [pytest.mark.runtime, pytest.mark.customer_turn]


@pytest.mark.parametrize(("field", "answer"), [
    ({"key": "guest_name", "label": "Guest Name", "type": "string", "required": True}, "Alex"),
    ({"key": "insurance_number", "label": "Insurance", "type": "number", "required": True}, "12345"),
    ({"key": "preferred_doctor", "label": "Doctor", "type": "select", "options": ["Dr A", "Dr B"], "required": True}, "Dr A"),
])
def test_requested_configured_field_answer_uses_exact_tenant_key(field, answer):
    context = make_context(
        state=ConversationState.COLLECTING_DATA,
        module="booking",
        step_data={"service_name": "General Checkup"},
        message={"text": answer, "channel_type": "awa_chat"},
    )
    state = context.session_data.state
    state.action_ledger.current_task = "booking"
    context.config.config_data["modules"]["booking"]["fields"] = [field]
    state.set_prompt_focus(workflow="booking", requested_fields=[field["key"]])

    extraction = _deterministic_active_booking_field_extraction(context)

    assert extraction is not None
    assert extraction.field_updates == {field["key"]: answer}
    assert extraction.field_confidence == {field["key"]: 1.0}


def _booking_frame() -> TurnInterpretation:
    return TurnInterpretation(
        turn_kind=TurnKind.NEW_WORKFLOW_REQUEST,
        candidate_workflow="booking",
        candidate_workflow_available=True,
        workflow_action=WorkflowAction.CONTINUE,
        confidence=0.99,
        needs_clarification=False,
    )


@pytest.mark.anyio
async def test_canonical_booking_request_activates_without_phrase_specific_production_logic(monkeypatch):
    llm = await _ready_engine(monkeypatch, responses=[AIMessage(content="must not be used")])
    frame = _booking_frame()
    monkeypatch.setattr(
        "backend.logic.engine.core.turn_interpretation_coordinator.interpret_turn",
        lambda *_args, **_kwargs: frame,
    )
    monkeypatch.setattr(
        "backend.logic.engine.core.extract_booking_fields",
        AsyncMock(
            return_value=BookingFieldExtraction(
                workflow="booking",
                workflow_action="continue",
                confidence=0.99,
                field_updates={},
                field_confidence={},
                clarification_needed=False,
            )
        ),
    )
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = make_context(
        state=ConversationState.NEW,
        message={"text": "opaque multilingual semantic booking request", "channel_type": "awa_chat"},
        include_order=True,
    )

    result = await agentic_engine.execute_step(context)

    assert context.session_data.state.action_ledger.current_task == "booking"
    assert context.session_data.current_module == "booking"
    assert context.session_data.state.action_ledger.task_status == "collecting"
    assert context.session_data.state.prompt_focus.workflow == "booking"
    assert context.session_data.state.prompt_focus.requested_fields[0] == "service_name"
    assert result.quick_replies == ["Dental Cleaning", "General Checkup"]
    assert context.session_data.state.scoped_step_data.get("order") is None
    assert context.session_data.state.action_ledger.pending_action is None
    execute_tool.assert_not_awaited()
    assert llm.calls == []


def test_prompt_scoped_unique_service_prefix_resolves_from_tenant_configuration():
    config = {
        "services": [
            {"id": "checkup", "name": "General Checkup"},
            {"id": "cleaning", "name": "Dental Cleaning"},
        ],
        "modules": {"booking": {"enabled": True}, "order": {"enabled": True}},
    }

    match = resolve_configured_option(
        customer_text="genera",
        active_workflow="booking",
        requested_fields=("service_name",),
        config_data=config,
        runtime_available_modules={"booking", "order"},
    )

    assert match is not None
    assert match.field_updates == {"service_name": "General Checkup", "service_id": "checkup"}
    assert match.evidence_source == "unique_configured_prefix"


def test_prompt_scoped_prefix_rejects_ambiguous_and_wrong_domain_candidates():
    config = {
        "services": [
            {"name": "General Checkup"},
            {"name": "General Consultation"},
        ],
        "modules": {
            "booking": {"enabled": True},
            "order": {"enabled": True, "fulfillment_types": ["general delivery"]},
        },
    }

    ambiguous = resolve_configured_option(
        customer_text="genera",
        active_workflow="booking",
        requested_fields=("service_name",),
        config_data=config,
        runtime_available_modules={"booking", "order"},
    )
    wrong_domain = resolve_configured_option(
        customer_text="general delivery",
        active_workflow="booking",
        requested_fields=("service_name",),
        config_data=config,
        runtime_available_modules={"booking", "order"},
    )

    assert ambiguous is None
    assert wrong_domain is None


@pytest.mark.anyio
async def test_abbreviated_service_answer_uses_prompt_scoped_configuration_before_field_llm(monkeypatch):
    await _ready_engine(monkeypatch)
    extractor = AsyncMock(side_effect=AssertionError("configured service candidate reached field LLM"))
    monkeypatch.setattr("backend.logic.engine.core.extract_booking_fields", extractor)
    context = make_context(
        state=ConversationState.COLLECTING_DATA,
        module="booking",
        message={"text": "genera", "channel_type": "awa_chat"},
        include_order=True,
    )
    state = context.session_data.state
    state.action_ledger.current_task = "booking"
    state.action_ledger.task_status = "collecting"
    state.set_prompt_focus(workflow="booking", requested_fields=["service_name"])

    await agentic_engine.execute_step(context)

    assert extractor.await_count == 0
    assert state.get_task_step_data("booking")["service_name"] == "General Checkup"
    assert state.action_ledger.current_task == "booking"
    assert state.scoped_step_data.get("order") is None


@pytest.mark.anyio
async def test_tenant_local_same_day_past_time_preserves_booking_and_never_prepares(monkeypatch):
    await _ready_engine(monkeypatch)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 7, 14, 16, 35, 0)
            return value.replace(tzinfo=tz) if tz is not None else value

    monkeypatch.setattr("backend.modules.booking_module.datetime", FrozenDateTime)
    monkeypatch.setattr(settings, "TURN_INTERPRETATION_MODE", "legacy")
    extraction = BookingFieldExtraction(
        workflow="booking",
        workflow_action="continue",
        confidence=0.99,
        field_updates={"booking_date": "today", "booking_time": "2 PM"},
        field_confidence={"booking_date": 0.99, "booking_time": 0.99},
        clarification_needed=False,
    )
    monkeypatch.setattr("backend.logic.engine.core.extract_booking_fields", AsyncMock(return_value=extraction))
    execute_tool = AsyncMock()
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    preserved = {
        "service_name": "General Checkup",
        "customer_name": "Hari",
        "phone": "9000000001",
    }
    context = make_context(
        state=ConversationState.COLLECTING_DATA,
        module="booking",
        step_data=preserved,
        message={"text": "today at 2 PM", "channel_type": "awa_chat"},
    )
    context.config.config_data["timezone"] = "Asia/Kolkata"
    state = context.session_data.state
    state.action_ledger.current_task = "booking"
    state.action_ledger.task_status = "collecting"
    state.scoped_step_data["booking"] = dict(preserved)
    state.set_prompt_focus(workflow="booking", requested_fields=["preferred_date", "preferred_time"])

    result = await agentic_engine.execute_step(context)

    assert "time" in result.response_text.lower()
    step_data = state.get_task_step_data("booking")
    assert all(step_data.get(k) == v for k, v in preserved.items())
    assert state.action_ledger.current_task == "booking"
    assert state.action_ledger.pending_action is None
    assert state.action_ledger.verification.otp_verifications == []
    execute_tool.assert_not_awaited()


def test_service_quick_replies_exclude_disabled_configuration():
    from backend.logic.engine.capability_menu import build_service_choices_response

    response = build_service_choices_response(
        {
            "services": [
                {"name": "General Checkup"},
                {"name": "Retired Service", "enabled": False},
                {"name": "Dental Cleaning"},
            ]
        },
        booking_mode=True,
    )

    assert response.quick_replies == ["General Checkup", "Dental Cleaning"]
    assert "Retired Service" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("customer_text", "updates", "expected"),
    [
        ("Alex", {"customer_name": "Alex"}, {"patient_name": "Alex"}),
        ("My name is Alex", {"customer_name": "Alex"}, {"patient_name": "Alex"}),
        (
            "Alex, 2026-08-17 at 10:00 AM",
            {"customer_name": "Alex", "booking_date": "2026-08-17", "booking_time": "10:00 AM"},
            {"patient_name": "Alex", "preferred_date": "2026-08-17", "preferred_time": "10:00"},
        ),
    ],
)
async def test_active_booking_short_continuation_applies_configured_patient_fields(
    monkeypatch, customer_text, updates, expected
):
    """A short active answer must reach extraction and map canonical values to tenant keys."""
    await _ready_engine(monkeypatch)
    context = make_context(
        state=ConversationState.COLLECTING_DATA,
        module="booking",
        step_data={"service_name": "General Checkup"},
        message={"text": customer_text, "channel_type": "awa_chat"},
    )
    context.config.config_data["modules"]["booking"]["fields"] = [
        {"key": "service_name", "label": "Service", "type": "string", "required": True},
        {"key": "patient_name", "label": "Patient name", "type": "string", "required": True},
        {"key": "preferred_date", "label": "Date", "type": "date", "required": True},
        {"key": "preferred_time", "label": "Time", "type": "time", "required": True},
    ]
    state = context.session_data.state
    state.action_ledger.current_task = "booking"
    state.action_ledger.task_status = "collecting"
    state.scoped_step_data["booking"] = {"service_name": "General Checkup"}
    state.set_prompt_focus(workflow="booking", requested_fields=["patient_name", "preferred_date", "preferred_time"])
    monkeypatch.setattr(
        "backend.logic.engine.core.extract_booking_fields",
        AsyncMock(return_value=BookingFieldExtraction(
            workflow="booking", workflow_action="continue", confidence=0.99,
            field_updates=updates, field_confidence={key: 0.99 for key in updates},
            clarification_needed=False,
        )),
    )
    tool_def = agentic_engine.tool_registry.get_tool("booking.execute")
    routing = SimpleNamespace(
        intent="booking", route_type="active_task", reason="active_task_short_continuation",
        should_clear_active_task=False,
    )

    result = await agentic_engine._handle_semantic_booking_field_extraction(context, routing)

    stored = state.get_task_step_data("booking")
    for key, value in expected.items():
        assert stored[key] == value
    assert "patient_name" not in _booking_missing_required_fields(tool_def, context)
    if len(expected) == 3:
        assert result is not None
        assert state.action_ledger.pending_action is not None
    else:
        assert result is None


@pytest.mark.anyio
async def test_smiledent_regression_transcript_stays_in_booking_through_protected_confirmation(monkeypatch):
    llm = await _ready_engine(monkeypatch, responses=[AIMessage(content="unrestricted core must not run")])

    _booking_results = BookingFieldExtraction(workflow="booking", workflow_action="continue", confidence=0.99, field_updates={"booking_date": "tomorrow", "booking_time": "2 PM", "customer_name": "Hari", "phone": "9000000001"}, field_confidence={"booking_date": 0.99, "booking_time": 0.99, "customer_name": 0.99, "phone": 0.99}, clarification_needed=False)
    _empty_result = BookingFieldExtraction(workflow="booking", workflow_action="continue", confidence=0.99, field_updates={}, field_confidence={}, clarification_needed=False)

    async def _tracking_extractor(context):
        text = str((context.incoming_message or {}).get("text") or "").strip()
        if text == "tomorrow at 2 PM, Hari, 9000000001":
            result = _booking_results
        else:
            result = _empty_result
        return result

    extraction = _tracking_extractor
    monkeypatch.setattr("backend.logic.engine.core.extract_booking_fields", extraction)
    monkeypatch.setattr("backend.logic.engine.configured_field_collection.extract_booking_fields", extraction)
    original_execute = agentic_engine.tool_registry.execute_tool
    execute_tool = AsyncMock(wraps=original_execute)
    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_tool)
    context = make_context(
        state=ConversationState.NEW,
        message={"text": "book", "channel_type": "awa_chat"},
        include_order=True,
    )
    context.config.config_data["modules"]["booking"]["fields"].append(
        {"key": "phone", "label": "Phone", "type": "string", "required": True}
    )

    activation = await agentic_engine.execute_step(context)
    assert context.session_data.state.action_ledger.current_task == "booking"
    assert activation.quick_replies == ["Dental Cleaning", "General Checkup"]

    context.incoming_message["text"] = "how do I know which services you have?"
    service_tangent = await agentic_engine.execute_step(context)
    assert "Dental Cleaning" in service_tangent.response_text
    assert service_tangent.quick_replies == ["Dental Cleaning", "General Checkup"]
    assert context.session_data.state.action_ledger.current_task == "booking"
    assert context.session_data.state.prompt_focus.requested_fields[0] == "service_name"

    context.incoming_message["text"] = "genera"
    await agentic_engine.execute_step(context)
    assert context.session_data.state.get_task_step_data("booking")["service_name"] == "General Checkup"
    assert context.session_data.state.prompt_focus.requested_fields[0] == "preferred_date"

    context.incoming_message["text"] = "tomorrow at 2 PM, Hari, 9000000001"
    pending_result = await agentic_engine.execute_step(context)
    pending = context.session_data.state.action_ledger.pending_action
    assert pending is not None
    assert pending.tool_key == "booking.execute"
    assert pending.args["service_name"] == "General Checkup"
    assert pending.args["customer_name"] == "Hari"
    assert pending_result.quick_replies == ["Confirm", "Change", "Cancel"]

    monkeypatch.setattr(
        "backend.logic.engine.core.decide_pending_action",
        AsyncMock(return_value=PendingActionDecision(action="confirm", confidence=0.99)),
    )
    context.incoming_message["text"] = "ok"
    confirmation = await agentic_engine.execute_step(context)

    assert confirmation.quick_replies == ["Confirm", "Change", "Cancel"]
    assert context.session_data.state.action_ledger.pending_action == pending
    assert context.session_data.state.action_ledger.current_task == "booking"
    assert context.session_data.state.scoped_step_data.get("order") is None
    dispatched_keys = [call.args[0] for call in execute_tool.await_args_list]
    assert dispatched_keys == ["booking.execute"]
    assert "order.catalog_search" not in dispatched_keys
    assert llm.calls == []
