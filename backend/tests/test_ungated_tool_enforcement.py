from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4
import pytest
from langchain_core.messages import AIMessage

from backend.core.config import settings
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.logic.engine.core import AgenticEngine, WorkflowResult
from backend.tools.registry import ToolExecutionResult


pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def explicit_legacy_behavior_runtime(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False)


def _context(module_name: str, step_data: dict, name="Asha", phone="+919999999999"):
    tenant_id = uuid4()
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Clinic", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number=phone, name=name),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module=module_name,
            state=SessionState(
                workflow_state=ConversationState.EXECUTING,
                step_data=step_data,
                scoped_step_data={module_name: step_data}
            ),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={"modules": {module_name: {"enabled": True}}}),
        incoming_message={"text": "hi", "channel_type": "awa_chat"},
    )


@patch("backend.logic.engine.llm.get_llm")
@patch("backend.logic.engine.core.build_system_instructions")
@patch("backend.logic.engine.core.build_memory_context")
@patch("backend.logic.engine.core.usage_guard")
async def test_ungated_tool_enforcement_triggers_on_omission(
    mock_usage_guard, mock_memory, mock_sys_inst, mock_get_llm, monkeypatch
):
    # Setup mocks
    mock_usage_guard.check_message_allowed = AsyncMock()
    mock_memory.return_value = "Memory Context"
    mock_sys_inst.return_value = "System instructions"

    # Mock LLM and Graph to return a conversational response without tool calls
    mock_llm = MagicMock()
    mock_response = AIMessage(content="I'll have someone call you.")
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_get_llm.return_value = mock_llm

    # Set step data with required fields for lead_capture: name and interest
    context = _context("lead_capture", {"name": "Test User", "interest": "Cleaning"})

    # Mock the tool capture execution response
    mock_result = ToolExecutionResult(
        tool_key="lead_capture.execute",
        success=True,
        user_message="I've registered your interest for Cleaning.",
    )

    engine = AgenticEngine()
    monkeypatch.setattr(engine.tool_registry, "execute_tool", AsyncMock(return_value=mock_result))

    # Run the execution step
    result = await engine.execute_step(context)

    # Assertions
    assert result.response_text == "I'll have someone call you."
    engine.tool_registry.execute_tool.assert_not_awaited()


@patch("backend.logic.engine.llm.get_llm")
@patch("backend.logic.engine.core.build_system_instructions")
@patch("backend.logic.engine.core.build_memory_context")
@patch("backend.logic.engine.core.usage_guard")
async def test_ungated_tool_enforcement_skipped_if_tool_already_called(
    mock_usage_guard, mock_memory, mock_sys_inst, mock_get_llm, monkeypatch
):
    # Setup mocks
    mock_usage_guard.check_message_allowed = AsyncMock()
    mock_memory.return_value = "Memory Context"
    mock_sys_inst.return_value = "System instructions"

    # Mock LLM and Graph to return a response with tool call
    mock_llm = MagicMock()
    mock_response = AIMessage(
        content="I am calling the tool.",
        tool_calls=[
            {
                "name": "lead_capture_execute",
                "args": {"name": "Test User", "interest": "Cleaning"},
                "id": "call_123",
                "type": "tool_call"
            }
        ]
    )
    mock_llm.ainvoke = AsyncMock(side_effect=[
        mock_response,
        AIMessage(content="I've registered your interest for Cleaning.")
    ])
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_get_llm.return_value = mock_llm

    context = _context("lead_capture", {"name": "Test User", "interest": "Cleaning"})

    mock_result = ToolExecutionResult(
        tool_key="lead_capture.execute",
        success=True,
        user_message="I've registered your interest for Cleaning.",
    )

    engine = AgenticEngine()
    monkeypatch.setattr(engine.tool_registry, "execute_tool", AsyncMock(return_value=mock_result))

    # Run the execution step
    result = await engine.execute_step(context)

    # Assertions: LLM natively output the tool call, so engine tool executing was triggered via Graph,
    # and the enforcement fallback was NOT triggered (execute_tool called once, history is managed by LangGraph)
    engine.tool_registry.execute_tool.assert_awaited_once_with("lead_capture.execute", context)
    assert result.response_text == "I've registered your interest for Cleaning."


@patch("backend.logic.engine.llm.get_llm")
@patch("backend.logic.engine.core.build_system_instructions")
@patch("backend.logic.engine.core.build_memory_context")
@patch("backend.logic.engine.core.usage_guard")
async def test_ungated_tool_enforcement_skipped_if_slots_missing(
    mock_usage_guard, mock_memory, mock_sys_inst, mock_get_llm, monkeypatch
):
    # Setup mocks
    mock_usage_guard.check_message_allowed = AsyncMock()
    mock_memory.return_value = "Memory Context"
    mock_sys_inst.return_value = "System instructions"

    # Mock LLM to return conversational question
    mock_llm = MagicMock()
    mock_response = AIMessage(content="What are you interested in?")
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_get_llm.return_value = mock_llm

    # Missing required field "interest"
    context = _context("lead_capture", {"name": "Test User"})

    engine = AgenticEngine()
    monkeypatch.setattr(engine.tool_registry, "execute_tool", AsyncMock())

    result = await engine.execute_step(context)

    # Assertions: slots missing, so no tool execution enforcement
    engine.tool_registry.execute_tool.assert_not_awaited()
    assert result.response_text == "What are you interested in?"
