"""Phase 4W-5 deterministic transcript and cross-channel release contracts.

These tests deliberately reuse the runtime's canonical interpretation and planner.  They
do not call an external semantic or transport provider, and transport identifiers never
enter the semantic evidence.
"""

from dataclasses import dataclass, field
import json
from types import SimpleNamespace

import pytest

from backend.api.webhook import _extract_message_text
from backend.logic.engine.turn_interpretation import (
    ShadowTurnSnapshot,
    TurnInterpretationMode,
    TurnKind,
    TurnPlanner,
    resolve_turn_interpretation_mode,
    turn_interpretation_coordinator,
)
from backend.pipeline.channel_output import normalize_channel_response
from backend.pipeline.message_router import RoutingDecision


pytestmark = [
    pytest.mark.runtime,
    pytest.mark.customer_turn,
    pytest.mark.canonical_transcript,
]

ALL_WORKFLOWS = {
    "booking", "order", "payments", "lead_capture", "support",
    "reminders", "reviews", "faq", "human_handoff",
}
INTERNAL_MARKERS = (
    "tool_key", "function=", "system prompt", "turninterpretation",
    "candidate_workflow", "traceback", "tenant_id", "provider_secret",
)


def _route(
    intent: str,
    *,
    confidence: float = 0.96,
    route_type: str = "workflow",
    tangent: bool = False,
    clear: bool = False,
    reason: str = "transcript_evidence",
) -> RoutingDecision:
    return RoutingDecision(intent, confidence, route_type, tangent, clear, reason)


def _extraction(
    workflow: str,
    updates: dict | None = None,
    *,
    action: str = "continue",
    confidence: float = 0.97,
) -> SimpleNamespace:
    updates = dict(updates or {})
    return SimpleNamespace(
        workflow=workflow,
        workflow_action=action,
        confidence=confidence,
        field_updates=updates,
        field_confidence={key: confidence for key in updates},
        detected_language="en",
    )


def _visible_whatsapp_text(kind: str, title: str, transport_id: str = "transport-only") -> str:
    if kind == "text":
        return _extract_message_text({"text": {"body": title}}, "text")
    reply_key = "button_reply" if kind == "button" else "list_reply"
    return _extract_message_text(
        {"interactive": {"type": reply_key, reply_key: {"id": transport_id, "title": title}}},
        "interactive",
    )


@dataclass
class TranscriptHarness:
    channel: str
    available: set[str] = field(default_factory=lambda: set(ALL_WORKFLOWS))
    active_workflow: str | None = None
    requested_fields: tuple[str, ...] = ()
    scoped_fields: dict[str, dict] = field(default_factory=dict)
    config_version: int = 7
    pending_action: bool = False
    pending_status: str = "idle"

    def turn(self, text: str, *, route=None, extraction=None, pending=None, semantic_failure=False):
        snapshot = ShadowTurnSnapshot(
            text=text,
            channel=self.channel,
            active_workflow=self.active_workflow,
            requested_fields=self.requested_fields,
            config_version=self.config_version,
            pending_action=self.pending_action,
            pending_status=self.pending_status,
            scoped_field_names=tuple(sorted(self.scoped_fields.get(self.active_workflow or "", {}))),
        )
        evidence = {
            "routing_decision": route,
            "field_extraction": extraction,
            "pending_decision": pending,
            "semantic_failure": semantic_failure,
        }
        frame = turn_interpretation_coordinator.interpret_turn(
            snapshot,
            evidence=evidence,
            runtime_available_modules=self.available,
        )
        plan = TurnPlanner().plan(frame, self.available)
        return frame, plan

    def apply(self, frame, plan) -> None:
        if plan.clear_current_workflow:
            if self.active_workflow:
                self.scoped_fields.pop(self.active_workflow, None)
            self.active_workflow = None
            self.requested_fields = ()
        if plan.switch_requested:
            if self.active_workflow:
                self.scoped_fields.pop(self.active_workflow, None)
            self.active_workflow = plan.candidate_workflow
            self.requested_fields = ()
        elif not self.active_workflow and plan.candidate_workflow:
            self.active_workflow = plan.candidate_workflow
        target = plan.tool_scope_hint or self.active_workflow
        if target and frame.field_updates:
            self.scoped_fields.setdefault(target, {}).update(frame.field_updates)


def _assert_customer_safe(text: str) -> None:
    lowered = text.lower()
    assert text.strip()
    assert not any(marker in lowered for marker in INTERNAL_MARKERS)


@pytest.mark.parametrize("channel", ["awa_chat", "whatsapp"])
def test_transcript_a_natural_first_turn_booking(channel):
    harness = TranscriptHarness(channel)
    updates = {
        "service_name": "Teeth Cleaning",
        "preferred_date": "tomorrow",
        "preferred_time_period": "evening",
    }
    frame, plan = harness.turn(
        "I need teeth cleaning tomorrow evening",
        route=_route("booking", reason="configured_service_booking_candidate"),
        extraction=_extraction("booking", updates),
    )
    harness.apply(frame, plan)

    assert frame.turn_kind == TurnKind.NEW_WORKFLOW_REQUEST
    assert frame.candidate_workflow == "booking"
    assert frame.field_updates == updates
    assert plan.tool_scope_hint == "booking"
    assert plan.clear_current_workflow is False
    assert harness.active_workflow == "booking"
    assert harness.scoped_fields["booking"] == updates
    assert harness.pending_action is False


def test_transcript_b_active_multifield_continuation_merges_without_loss():
    harness = TranscriptHarness(
        "awa_chat",
        active_workflow="booking",
        requested_fields=("preferred_time", "customer_name", "phone"),
        scoped_fields={"booking": {"service_name": "Teeth Cleaning", "preferred_date": "tomorrow"}},
        pending_status="collecting",
    )
    updates = {"preferred_time": "16:00", "customer_name": "Arjun", "phone": "9876543210"}
    frame, plan = harness.turn(
        "Around 4 pm. My name is Arjun and my phone is 9876543210.",
        route=_route("booking", route_type="active_task", reason="active_task_context"),
        extraction=_extraction("booking", updates),
    )
    harness.apply(frame, plan)

    assert frame.turn_kind == TurnKind.ACTIVE_WORKFLOW_ANSWER
    assert plan.preserve_active_workflow is True
    assert plan.switch_requested is False
    assert harness.scoped_fields["booking"] == {
        "service_name": "Teeth Cleaning",
        "preferred_date": "tomorrow",
        **updates,
    }
    comparison = turn_interpretation_coordinator.compare_with_legacy(
        frame,
        evidence={"routing_decision": _route("booking"), "field_extraction": _extraction("booking", updates)},
        config_version=7,
        channel="awa_chat",
    )
    serialized = comparison.model_dump_json()
    assert "Arjun" not in serialized and "9876543210" not in serialized
    assert sorted(comparison.field_names) == sorted(updates)


@pytest.mark.parametrize(
    ("form", "visible_text"),
    [
        ("browser_typed", "Cavity Filling"),
        ("browser_clicked", "Cavity Filling"),
        ("whatsapp_text", _visible_whatsapp_text("text", "Cavity Filling")),
        ("whatsapp_button", _visible_whatsapp_text("button", "Cavity Filling", "service-uuid")),
        ("whatsapp_list", _visible_whatsapp_text("list", "Cavity Filling", "field-assignment")),
    ],
)
def test_transcript_c_service_visible_text_has_identical_semantics(form, visible_text):
    channel = "whatsapp" if form.startswith("whatsapp") else "awa_chat"
    harness = TranscriptHarness(channel, active_workflow="booking", requested_fields=("service_name",))
    frame, plan = harness.turn(
        visible_text,
        route=_route("booking", route_type="active_task", reason="active_task_service_answer"),
        extraction=_extraction("booking", {"service_name": visible_text}),
    )
    semantic = frame.model_dump(exclude={"source"})
    semantic.pop("interpretation_version")
    assert visible_text == "Cavity Filling"
    assert frame.turn_kind == TurnKind.ACTIVE_WORKFLOW_ANSWER
    assert frame.field_updates == {"service_name": "Cavity Filling"}
    assert plan.tool_scope_hint == "booking"
    assert "service-uuid" not in json.dumps(semantic)
    assert "field-assignment" not in json.dumps(semantic)


def test_transcript_d_capability_request_is_not_unknown_orientation_replay():
    harness = TranscriptHarness("awa_chat")
    capability, capability_plan = harness.turn(
        "What can you do?", route=_route("greeting", route_type="direct_answer", reason="greeting")
    )
    unknown, unknown_plan = harness.turn(
        "I was wondering about something unusual",
        route=_route("unknown", confidence=0.2, route_type="unknown", reason="no_deterministic_match"),
    )
    assert capability.turn_kind == TurnKind.CAPABILITY_REQUEST
    assert capability_plan.capability_menu_requested is True
    assert capability_plan.tool_scope_hint is None
    assert unknown.turn_kind == TurnKind.UNKNOWN
    assert unknown_plan.response_path == "general"
    assert unknown_plan.capability_menu_requested is False
    assert unknown_plan.tool_scope_hint is None


def test_transcript_e_faq_tangent_preserves_booking_then_continues():
    original = {"service_name": "Teeth Cleaning", "preferred_date": "tomorrow"}
    harness = TranscriptHarness(
        "awa_chat",
        active_workflow="booking",
        requested_fields=("preferred_time",),
        scoped_fields={"booking": dict(original)},
        pending_status="collecting",
    )
    tangent, tangent_plan = harness.turn(
        "What are your opening hours tomorrow?",
        route=_route("faq", route_type="direct_answer", tangent=True, reason="active_task_tangent"),
    )
    harness.apply(tangent, tangent_plan)
    response = normalize_channel_response(
        "We are open tomorrow from 9 AM to 6 PM. What time suits your cleaning?",
        "awa_chat",
    )
    assert tangent.turn_kind == TurnKind.TANGENT
    assert tangent_plan.preserve_active_workflow is True
    assert tangent_plan.tool_scope_hint == "faq"
    assert harness.scoped_fields["booking"] == original
    assert harness.requested_fields == ("preferred_time",)
    assert "9 AM" in response and "cleaning" in response
    _assert_customer_safe(response)

    continuation, continuation_plan = harness.turn(
        "4 pm works",
        route=_route("booking", route_type="active_task", reason="active_task_context"),
        extraction=_extraction("booking", {"preferred_time": "16:00"}),
    )
    harness.apply(continuation, continuation_plan)
    assert continuation.turn_kind == TurnKind.ACTIVE_WORKFLOW_ANSWER
    assert harness.scoped_fields["booking"]["preferred_time"] == "16:00"


@pytest.mark.parametrize("order_available", [True, False])
def test_transcript_f_explicit_switch_clears_booking_only_when_target_is_available(order_available):
    available = set(ALL_WORKFLOWS)
    if not order_available:
        available.remove("order")
    harness = TranscriptHarness(
        "awa_chat",
        available=available,
        active_workflow="booking",
        scoped_fields={"booking": {"service_name": "Teeth Cleaning", "preferred_date": "tomorrow"}},
        pending_status="collecting",
    )
    frame, plan = harness.turn(
        "Actually, cancel this booking and help me place an order.",
        route=_route("order", clear=True, reason="explicit_workflow_switch"),
        extraction=_extraction("order", action="switch"),
    )
    harness.apply(frame, plan)
    assert frame.explicit_switch is True
    assert frame.cancel_requested is True
    assert plan.switch_requested is order_available
    if order_available:
        assert harness.active_workflow == "order"
        assert "booking" not in harness.scoped_fields
    else:
        assert plan.candidate_workflow is None
        assert harness.active_workflow == "booking"
        assert harness.scoped_fields["booking"]["service_name"] == "Teeth Cleaning"


def test_transcript_g_malformed_active_answer_targets_prompt_without_mutation():
    original = {"service_name": "Teeth Cleaning", "preferred_date": "tomorrow"}
    harness = TranscriptHarness(
        "whatsapp",
        active_workflow="booking",
        requested_fields=("preferred_time",),
        scoped_fields={"booking": dict(original)},
        pending_status="collecting",
    )
    frame, plan = harness.turn(
        "purple maybe sideways",
        route=_route("booking", confidence=0.2, route_type="active_task", reason="active_task_context"),
        extraction=_extraction("booking", action="unknown", confidence=0.2),
    )
    harness.apply(frame, plan)
    assert frame.turn_kind == TurnKind.ACTIVE_WORKFLOW_ANSWER
    assert frame.needs_clarification is True
    assert frame.field_updates == {}
    assert plan.preserve_active_workflow is True
    assert plan.tool_scope_hint == "booking"
    assert harness.requested_fields == ("preferred_time",)
    assert harness.scoped_fields["booking"] == original


@pytest.mark.parametrize(
    ("text", "workflow", "updates"),
    [
        ("I need teeth cleaning tomorrow", "booking", {"service_name": "Teeth Cleaning", "preferred_date": "tomorrow"}),
        ("I want two dental kits for pickup", "order", {"item_name": "Dental Kit", "quantity": 2, "fulfillment_type": "pickup"}),
        ("Send the payment link for invoice INV-TEST", "payments", {"reference": "INV-TEST"}),
        ("Please contact me about family dental care", "lead_capture", {"interest": "family dental care"}),
        ("I need help with my order", "support", {"issue_description": "help with order"}),
        ("Remind me tomorrow", "reminders", {"reminder_date": "tomorrow"}),
        ("I want to leave feedback", "reviews", {"feedback": "pending"}),
        ("Let me talk to a person", "human_handoff", {}),
    ],
)
def test_cross_domain_customer_requests_have_bounded_plans(text, workflow, updates):
    harness = TranscriptHarness("awa_chat")
    extraction = None if workflow == "human_handoff" else _extraction(workflow, updates)
    frame, plan = harness.turn(text, route=_route(workflow), extraction=extraction)
    assert frame.turn_kind == TurnKind.NEW_WORKFLOW_REQUEST
    assert frame.candidate_workflow == workflow
    assert frame.field_updates == updates
    assert plan.tool_scope_hint == workflow
    assert plan.clear_current_workflow is False
    assert plan.switch_requested is False


@pytest.mark.parametrize("action", ["confirm", "cancel", "change", "continue", "unknown", "tangent"])
def test_protected_pending_action_matrix_never_changes_tool_scope(action):
    harness = TranscriptHarness(
        "awa_chat",
        active_workflow="booking",
        requested_fields=(),
        scoped_fields={"booking": {"service_name": "Teeth Cleaning"}},
        pending_action=True,
        pending_status="awaiting_confirmation",
    )
    pending = SimpleNamespace(action=action, confidence=0.98 if action != "unknown" else 0.2, field_updates={})
    frame, plan = harness.turn(action, route=_route("order", clear=True), pending=pending)
    assert frame.candidate_workflow is None
    assert plan.switch_requested is False
    assert plan.tool_scope_hint is None
    assert harness.scoped_fields["booking"]["service_name"] == "Teeth Cleaning"
    if action == "unknown":
        assert frame.needs_clarification is True


def test_provider_failure_preserves_active_state_and_never_claims_success():
    original = {"service_name": "Teeth Cleaning", "preferred_date": "tomorrow"}
    harness = TranscriptHarness(
        "whatsapp",
        active_workflow="booking",
        requested_fields=("preferred_time",),
        scoped_fields={"booking": dict(original)},
    )
    frame, plan = harness.turn(
        "around four",
        route=_route("unknown", confidence=0.0, route_type="unknown"),
        semantic_failure=True,
    )
    response = normalize_channel_response(
        '{"success":false,"tool_key":"booking.execute","user_message":"I could not verify availability. Please try again."}',
        "whatsapp",
    )
    assert frame.turn_kind == TurnKind.UNKNOWN
    assert frame.needs_clarification is True
    assert plan.preserve_active_workflow is True
    assert harness.scoped_fields["booking"] == original
    assert "confirmed" not in response.lower()
    assert "verify availability" in response.lower()
    _assert_customer_safe(response)


def test_authoritative_shadow_and_legacy_runtime_modes_remain_selectable():
    assert resolve_turn_interpretation_mode("authoritative") == TurnInterpretationMode.AUTHORITATIVE
    assert resolve_turn_interpretation_mode("shadow") == TurnInterpretationMode.SHADOW
    assert resolve_turn_interpretation_mode("legacy") == TurnInterpretationMode.LEGACY


def test_interpretation_and_planner_are_deterministic_and_provider_free(monkeypatch):
    def provider_must_not_run(*_args, **_kwargs):
        raise AssertionError("canonical planning made an unexpected provider call")

    monkeypatch.setattr("backend.logic.engine.llm.get_llm", provider_must_not_run)
    harness = TranscriptHarness("awa_chat")
    evidence = {
        "route": _route("booking", reason="configured_service_booking_candidate"),
        "extraction": _extraction("booking", {"service_name": "Teeth Cleaning"}),
    }
    first = harness.turn("Book teeth cleaning", **evidence)
    second = harness.turn("Book teeth cleaning", **evidence)
    assert first == second
