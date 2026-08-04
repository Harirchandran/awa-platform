import pytest
from unittest.mock import AsyncMock
from langchain_core.messages import AIMessage
from backend.core.states import ConversationState
from backend.core.context import SafeError, PendingAction
from backend.logic.engine.core import agentic_engine
from backend.logic.engine.pending_decision import PendingActionDecision
from backend.tests.unit.test_conversation_ux import make_context, install_agent_test_doubles, FakeToolCallingLLM

pytestmark = pytest.mark.anyio

async def test_active_booking_price_question_does_not_clear_booking_context(monkeypatch):
    # Setup context with active booking error and booking as current task
    context = make_context(
        state=ConversationState.COLLECTING_DATA,
        module="booking",
        step_data={"patient_name": "Hari", "preferred_date": "yesterday"},
        message={"text": "what is the price?"},
        include_order=True,
    )

    # Simulate active booking validation error in action ledger
    context.session_data.state.action_ledger.current_task = "booking"
    context.session_data.state.action_ledger.task_status = "failed"
    context.session_data.state.action_ledger.last_error = SafeError(
        code="past_date",
        safe_message="You need to choose a date today or in the future."
    )
    context.session_data.state.action_ledger.pending_action = PendingAction(
        tool_key="booking.execute",
        args={"patient_name": "Hari", "preferred_date": "yesterday"},
        args_hash="somehash",
        requires_confirmation=True,
    )

    llm = FakeToolCallingLLM([
        AIMessage(content="Toothpaste is INR 120.")
    ])
    await install_agent_test_doubles(monkeypatch, llm)
    async def fake_decide(context):
        return PendingActionDecision(action="tangent", confidence=0.9)
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", fake_decide)

    # Execute step
    result = await agentic_engine.execute_step(context)

    assert "confirm this request" in result.response_text.lower()
    assert result.quick_replies == ["Confirm", "Change", "Cancel"]
    assert context.session_data.state.action_ledger.current_task == "booking"
    assert context.session_data.current_module == "booking"
    assert context.session_data.state.action_ledger.last_error is not None
    assert context.session_data.state.action_ledger.pending_action is not None
    assert context.session_data.state.action_ledger.task_status == "failed"
    assert context.session_data.state.step_data["patient_name"] == "Hari"
    assert context.session_data.state.step_data["preferred_date"] == "yesterday"


async def test_past_booking_error_preserved_on_order_request_until_explicit_cancel(monkeypatch):
    context = make_context(
        state=ConversationState.COLLECTING_DATA,
        module="booking",
        step_data={"patient_name": "Hari", "preferred_date": "yesterday"},
        message={"text": "I want to order toothpaste"},
        include_order=True,
    )

    context.session_data.state.action_ledger.current_task = "booking"
    context.session_data.state.action_ledger.task_status = "failed"
    context.session_data.state.action_ledger.last_error = SafeError(
        code="past_date",
        safe_message="You need to choose a date today or in the future."
    )

    llm = FakeToolCallingLLM([
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "order_execute",
                    "args": {
                        "item_id": "toothpaste",
                        "quantity": 1,
                        "fulfillment_type": "pickup",
                        "customer_name": "Asha",
                        "phone": "+919999999999",
                    },
                    "id": "call_order",
                }
            ],
        ),
        AIMessage(content="I've prepared the order. Confirm? [Confirm] [Cancel]")
    ])
    await install_agent_test_doubles(monkeypatch, llm)

    result = await agentic_engine.execute_step(context)

    assert "kept your current request details" in result.response_text
    assert context.session_data.state.action_ledger.current_task == "booking"
    assert context.session_data.current_module == "booking"
    assert context.session_data.state.action_ledger.last_error is not None
    assert context.session_data.state.action_ledger.last_error.code == "past_date"
    assert context.session_data.state.step_data["patient_name"] == "Hari"
    assert context.session_data.state.step_data["preferred_date"] == "yesterday"


async def test_past_booking_error_preserved_on_human_handoff_until_explicit_cancel(monkeypatch):
    context = make_context(
        state=ConversationState.COLLECTING_DATA,
        module="booking",
        step_data={"patient_name": "Hari", "preferred_date": "yesterday"},
        message={"text": "human please"},
    )

    context.session_data.state.action_ledger.current_task = "booking"
    context.session_data.state.action_ledger.task_status = "failed"
    context.session_data.state.action_ledger.last_error = SafeError(
        code="past_date",
        safe_message="You need to choose a date today or in the future."
    )

    llm = FakeToolCallingLLM([
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "human_handoff_execute",
                    "args": {"reason": "human please"},
                    "id": "call_handoff",
                }
            ],
        ),
        AIMessage(content="I've escalated this to a human.")
    ])
    await install_agent_test_doubles(monkeypatch, llm)

    monkeypatch.setattr(
        "backend.modules.human_handoff_module.support_service.create_handoff",
        AsyncMock(return_value=({"status": "open"}, None)),
    )

    result = await agentic_engine.execute_step(context)

    assert "kept your current request details" in result.response_text
    assert context.session_data.state.action_ledger.current_task == "booking"
    assert context.session_data.current_module == "booking"
    assert context.session_data.state.action_ledger.last_error is not None
    assert context.session_data.state.action_ledger.last_error.code == "past_date"
    assert context.session_data.state.action_ledger.task_status == "failed"


async def test_past_booking_error_not_cleared_on_confirm(monkeypatch):
    # Ambiguous short confirmation should NOT clear booking context
    context = make_context(
        state=ConversationState.COLLECTING_DATA,
        module="booking",
        step_data={"patient_name": "Hari", "preferred_date": "yesterday"},
        message={"text": "confirm"},
        include_order=True,
    )

    context.session_data.state.action_ledger.current_task = "booking"
    context.session_data.state.action_ledger.task_status = "failed"
    context.session_data.state.action_ledger.last_error = SafeError(
        code="past_date",
        safe_message="You need to choose a date today or in the future."
    )
    context.session_data.state.action_ledger.pending_action = PendingAction(
        tool_key="booking.execute",
        args={"patient_name": "Hari", "preferred_date": "yesterday"},
        args_hash="somehash",
        requires_confirmation=True,
    )

    llm = FakeToolCallingLLM([
        AIMessage(content="Choose a date today or in the future.")
    ])
    await install_agent_test_doubles(monkeypatch, llm)
    async def fake_decide(context):
        return PendingActionDecision(action="unknown", confidence=0.2)
    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", fake_decide)

    result = await agentic_engine.execute_step(context)

    # Task context is NOT cleared
    assert context.session_data.state.action_ledger.current_task == "booking"
    assert context.session_data.state.action_ledger.last_error is not None
    assert context.session_data.state.action_ledger.pending_action is not None
    assert "patient_name" in context.session_data.state.step_data
