import pytest

from backend.logic.engine.turn_interpretation import (
    ShadowTurnSnapshot,
    TurnInterpretation,
    TurnInterpretationMode,
    TurnKind,
    TurnPlanner,
    WorkflowAction,
    resolve_turn_interpretation_mode,
    turn_interpretation_coordinator,
)
from backend.pipeline.message_router import RoutingDecision


ALL_WORKFLOWS = {
    "booking", "order", "payments", "lead_capture", "support",
    "reminders", "reviews", "faq", "human_handoff",
}


def _snapshot(text: str, active: str | None = None) -> ShadowTurnSnapshot:
    return ShadowTurnSnapshot(
        text=text,
        channel="awa_chat",
        active_workflow=active,
        requested_fields=(),
        config_version=1,
        pending_action=False,
        pending_status="collecting" if active else "idle",
    )


def _route(intent: str, *, tangent: bool = False, clear: bool = False) -> RoutingDecision:
    return RoutingDecision(intent, 0.95, "workflow", tangent, clear, "test_evidence")


@pytest.mark.parametrize(
    ("explicit", "old_shadow", "expected"),
    [
        (None, False, TurnInterpretationMode.AUTHORITATIVE),
        (None, True, TurnInterpretationMode.SHADOW),
        ("legacy", True, TurnInterpretationMode.LEGACY),
        ("shadow", False, TurnInterpretationMode.SHADOW),
        ("authoritative", True, TurnInterpretationMode.AUTHORITATIVE),
        ("invalid", False, TurnInterpretationMode.AUTHORITATIVE),
    ],
)
def test_platform_mode_precedence_and_safe_default(explicit, old_shadow, expected):
    assert resolve_turn_interpretation_mode(explicit, legacy_shadow_enabled=old_shadow) == expected


def test_router_clear_signal_cannot_authorize_a_switch():
    frame = turn_interpretation_coordinator.interpret_turn(
        _snapshot("toothpaste", "booking"),
        evidence={"routing_decision": _route("order", clear=True)},
        runtime_available_modules=ALL_WORKFLOWS,
    )
    plan = TurnPlanner().plan(frame, ALL_WORKFLOWS)
    assert frame.explicit_switch is False
    assert plan.switch_requested is False
    assert plan.preserve_active_workflow is True


@pytest.mark.parametrize(
    ("text", "route", "workflow"),
    [
        ("Remind me tomorrow", "reminders", "reminders"),
        ("I want to leave feedback", "reviews", "reviews"),
        ("I need help with my order", "support", "support"),
        ("Let me talk to a person", "human_handoff", "human_handoff"),
        ("Please contact me", "lead_capture", "lead_capture"),
    ],
)
def test_cross_domain_followup_requests_produce_bounded_plans(text, route, workflow):
    frame = turn_interpretation_coordinator.interpret_turn(
        _snapshot(text),
        evidence={"routing_decision": _route(route)},
        runtime_available_modules=ALL_WORKFLOWS,
    )
    plan = TurnPlanner().plan(frame, ALL_WORKFLOWS)
    assert frame.turn_kind == TurnKind.NEW_WORKFLOW_REQUEST
    assert frame.candidate_workflow == workflow
    assert plan.candidate_workflow == workflow
    assert plan.clear_current_workflow is False


def test_planner_preserves_secondary_objectives_without_expanding_tool_scope():
    frame = TurnInterpretation(
        turn_kind=TurnKind.NEW_WORKFLOW_REQUEST,
        candidate_workflow="booking",
        secondary_workflows=["faq", "support", "booking"],
        workflow_action=WorkflowAction.CONTINUE,
        confidence=0.95,
        needs_clarification=False,
    )

    plan = TurnPlanner().plan(frame, ALL_WORKFLOWS)

    assert plan.candidate_workflow == "booking"
    assert plan.secondary_workflows == ("faq", "support")
    assert plan.tool_scope_hint == "booking"


def test_explicit_human_request_wins_mixed_booking_request():
    frame = turn_interpretation_coordinator.interpret_turn(
        _snapshot("Book dental cleaning, but please connect me to a human"),
        evidence={"routing_decision": _route("booking")},
        runtime_available_modules=ALL_WORKFLOWS,
    )
    plan = TurnPlanner().plan(frame, ALL_WORKFLOWS)

    assert frame.turn_kind == TurnKind.NEW_WORKFLOW_REQUEST
    assert frame.candidate_workflow == "human_handoff"
    assert plan.candidate_workflow == "human_handoff"
    assert plan.tool_scope_hint == "human_handoff"


def test_disabled_followup_is_neither_eligible_nor_in_tool_scope():
    frame = turn_interpretation_coordinator.interpret_turn(
        _snapshot("Remind me tomorrow"),
        evidence={"routing_decision": _route("reminders")},
        runtime_available_modules={"booking", "faq"},
    )
    plan = TurnPlanner().plan(frame, {"booking", "faq"})
    assert frame.candidate_workflow_available is False
    assert plan.candidate_workflow is None
    assert plan.tool_scope_hint is None
    assert plan.clarification_requested is True


def test_tangent_support_question_preserves_active_workflow_without_switch():
    frame = turn_interpretation_coordinator.interpret_turn(
        _snapshot("I need help with my order", "order"),
        evidence={"routing_decision": _route("support", tangent=True, clear=True)},
        runtime_available_modules=ALL_WORKFLOWS,
    )
    plan = TurnPlanner().plan(frame, ALL_WORKFLOWS)
    assert frame.turn_kind == TurnKind.TANGENT
    assert plan.preserve_active_workflow is True
    assert plan.switch_requested is False


def test_clicked_and_typed_visible_label_have_identical_semantics():
    evidence = {"routing_decision": _route("reviews")}
    typed = turn_interpretation_coordinator.interpret_turn(
        _snapshot("Leave feedback"), evidence=evidence, runtime_available_modules=ALL_WORKFLOWS
    )
    clicked = turn_interpretation_coordinator.interpret_turn(
        _snapshot("Leave feedback"), evidence=evidence, runtime_available_modules=ALL_WORKFLOWS
    )
    assert typed == clicked


def test_behavior_profile_cannot_change_planner_eligibility_or_execution():
    frame = TurnInterpretation(
        turn_kind=TurnKind.NEW_WORKFLOW_REQUEST,
        candidate_workflow="reviews",
        candidate_workflow_available=True,
        workflow_action=WorkflowAction.CONTINUE,
        confidence=0.95,
        needs_clarification=False,
    )
    assert TurnPlanner().plan(frame, ALL_WORKFLOWS) == TurnPlanner().plan(frame, ALL_WORKFLOWS)
