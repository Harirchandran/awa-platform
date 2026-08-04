from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.logic.engine.canonical_control import CanonicalControlAction, CanonicalControlDecision
from backend.logic.engine.core import AgenticEngine
from backend.logic.engine.turn_interpretation import InterpretationSource, TurnInterpretation, TurnKind, TurnPlan
from backend.logic.engine.turn_interpretation_gateway import build_turn_interpretation_gateway_request
from backend.pipeline.message_router import RoutingDecision


def _context(*, preserve_scope=True):
    tenant_id = uuid4()
    session = ConversationSession(
        id=uuid4(), tenant_id=tenant_id, conversation_id=uuid4(), config_version=1,
        state=SessionState(scoped_step_data={"booking": {"service": "retained"}} if preserve_scope else {}),
    )
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Tenant", plan_code="pro", status="active"),
        user=User(id=uuid4(), phone_number="+15550000000"),
        session_data=session,
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={"modules": {"lead_capture": {"enabled": True}}}),
        incoming_message={"text": "Please contact me", "channel_type": "awa_chat"},
    )


def _frame(kind=TurnKind.NEW_WORKFLOW_REQUEST):
    return TurnInterpretation(
        turn_kind=kind,
        candidate_workflow="lead_capture",
        candidate_workflow_available=True,
        workflow_action="continue" if kind == TurnKind.NEW_WORKFLOW_REQUEST else "none",
        capability_request=kind == TurnKind.CAPABILITY_REQUEST,
        confidence=0.95,
        needs_clarification=False,
        source=InterpretationSource.SEMANTIC_GATEWAY,
    )


def _plan(kind=TurnKind.NEW_WORKFLOW_REQUEST):
    return TurnPlan("workflow", None, "lead_capture", False, False, False, kind == TurnKind.CAPABILITY_REQUEST, False, False, "lead_capture", ())


def test_semantic_prompt_distinguishes_capability_from_action_in_every_language():
    prompt = build_turn_interpretation_gateway_request({}).messages[0].content
    assert "capability_request asks whether a workflow exists" in prompt
    assert "new_workflow_request asks the business to begin or perform" in prompt
    assert "every language" in prompt


def test_session_activation_preserves_other_scopes_and_is_idempotent():
    context = _context()
    session = context.session_data
    assert session.activate_workflow_task("lead_capture") is True
    assert session.activate_workflow_task("lead_capture") is True
    assert session.current_module == session.state.action_ledger.current_task == "lead_capture"
    assert session.state.action_ledger.task_status == "collecting"
    assert session.state.workflow_state == ConversationState.COLLECTING_DATA
    assert session.state.get_task_step_data("booking") == {"service": "retained"}


@pytest.mark.asyncio
async def test_validated_semantic_new_workflow_activates_fresh_lead(monkeypatch):
    context = _context(preserve_scope=False)
    context.turn_interpretation = _frame()
    context.turn_plan = _plan()
    engine = AgenticEngine()
    engine.tool_registry = SimpleNamespace(
        get_pre_route_eligible_tool=lambda *_args: _eligible_tool(),
    )
    monkeypatch.setattr("backend.logic.engine.core._turn_interpretation_mode", lambda: "authoritative")
    route, activated = await engine._activate_fresh_lead_from_semantic_contract(
        context, RoutingDecision("unknown", 0.1, "general", False, False, "legacy"),
        CanonicalControlDecision(CanonicalControlAction.HANDLE_NEW_WORKFLOW, "lead_capture", False, ("test",)),
        {"lead_capture"},
    )
    assert activated is True
    assert route.intent == "lead_capture" and route.route_type == "active_task"
    assert context.session_data.current_module == "lead_capture"


async def _eligible_tool():
    return SimpleNamespace(module_name="lead_capture")


@pytest.mark.asyncio
async def test_capability_request_does_not_activate_lead(monkeypatch):
    context = _context()
    context.turn_interpretation = _frame(TurnKind.CAPABILITY_REQUEST)
    context.turn_plan = _plan(TurnKind.CAPABILITY_REQUEST)
    engine = AgenticEngine()
    engine.tool_registry = SimpleNamespace(get_pre_route_eligible_tool=lambda *_args: _eligible_tool())
    monkeypatch.setattr("backend.logic.engine.core._turn_interpretation_mode", lambda: "authoritative")
    route, activated = await engine._activate_fresh_lead_from_semantic_contract(
        context, RoutingDecision("unknown", 0.1, "general", False, False, "legacy"),
        CanonicalControlDecision(CanonicalControlAction.SHOW_CAPABILITIES, None, False, ("test",)),
        {"lead_capture"},
    )
    assert activated is False
    assert route.intent == "unknown"
    assert context.session_data.current_module is None
