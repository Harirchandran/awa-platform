import re
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from backend.billing.usage_guard import DEFAULT_PLAN_POLICIES
from backend.core.config import settings
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.logic.engine import agentic_engine
from backend.logic.engine.capability_menu import build_matched_business_context
from backend.logic.engine.history import HISTORY_LIMIT, SMALL_HISTORY_LIMIT, build_memory_context
from backend.logic.engine.llm import build_llm_input_trace

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def profile_neutral_optimization_fixture(monkeypatch):
    """Optimization tests isolate routing/trace behavior from DB presentation presets."""
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False)
    async def resolve_legacy_profile(**kwargs):
        return None

    monkeypatch.setattr(
        "backend.logic.engine.core.resolve_runtime_behavior_profile",
        resolve_legacy_profile,
    )

UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


class CapturingLLM:
    def __init__(self, content="I can help with that."):
        self.content = content
        self.bound_tools = None
        self.calls = []

    def bind_tools(self, tools):
        self.bound_tools = list(tools or [])
        return self

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return AIMessage(content=self.content)


def make_context(
    message: str = "hello",
    *,
    state: ConversationState = ConversationState.NEW,
    current_module: str | None = None,
) -> ExecutionContext:
    tenant_id = uuid4()
    session_state = SessionState(workflow_state=state)
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Smile Clinic", plan_code="enterprise", status="active", ai_mode="standard"),
        user=User(id=uuid4(), phone_number="+919999999999", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module=current_module,
            state=session_state,
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data={
                "business_name": "Smile Clinic",
                "business_type": "clinic",
                "services": [
                    {"id": "cleaning", "name": "Dental Cleaning", "duration_mins": 45, "price": 800},
                    {"id": "checkup", "name": "General Checkup", "duration_mins": 30, "price": 500},
                ],
                "working_hours": {
                    "monday": {"is_open": True, "open_time": "09:00", "close_time": "17:00"},
                },
                "faqs": [
                    {"question": "Do you accept walk-ins?", "answer": "Yes, before 5 PM."},
                    {"question": "What is the refund policy?", "answer": "Refunds need team confirmation."},
                ],
                "modules": {
                    "booking": {"enabled": True, "fields": []},
                    "order": {
                        "enabled": True,
                        "fields": [],
                        "categories": [
                            {
                                "id": "food",
                                "name": "Food",
                                "items": [{"id": "tea", "name": "Masala Tea", "price": 30, "enabled": True}],
                            }
                        ],
                    },
                    "payments": {"enabled": True, "fields": []},
                    "reminders": {"enabled": True, "fields": []},
                    "faq": {"enabled": True, "fields": []},
                    "human_handoff": {"enabled": True, "fields": []},
                    "support": {"enabled": True, "fields": []},
                },
            },
        ),
        incoming_message={
            "text": message,
            "channel_type": "awa_chat",
            "route_path": f"/api/v1/public-chat/sessions/{uuid4()}/messages",
        },
    )


async def install_engine_doubles(monkeypatch, llm: CapturingLLM, rag_text: str = "No reviewed escalation knowledge matched this message."):
    async def fake_check_message_allowed(context):
        return DEFAULT_PLAN_POLICIES["enterprise"]

    async def fake_visibility_denial(context, tool_key):
        return None

    class FakeFieldLLM:
        async def ainvoke(self, messages):
            import json
            system_prompt = ""
            for msg in messages:
                if getattr(msg, "type", "") == "system":
                    system_prompt = str(getattr(msg, "content", ""))
                    break

            workflow = "unknown"
            if "booking" in system_prompt.lower():
                workflow = "booking"
            elif "order" in system_prompt.lower():
                workflow = "order"
            elif "support" in system_prompt.lower():
                workflow = "support"
            elif "reminder" in system_prompt.lower():
                workflow = "reminder"
            elif "review" in system_prompt.lower():
                workflow = "review"
            elif "payment" in system_prompt.lower():
                workflow = "payment"
            elif "lead" in system_prompt.lower():
                workflow = "lead_capture"

            return AIMessage(
                content=json.dumps(
                    {
                        "workflow": workflow,
                        "workflow_action": "continue",
                        "confidence": 0.9,
                        "field_updates": {},
                        "field_confidence": {},
                        "clarification_needed": False,
                        "clarification_fields": [],
                        "safe_response_hint": None,
                    }
                )
            )

    traces = []
    rag_mock = AsyncMock(return_value=SimpleNamespace(context_text=rag_text))
    monkeypatch.setattr(agentic_engine, "_hydrate_order_catalog", AsyncMock())
    monkeypatch.setattr("backend.logic.engine.llm.get_llm", lambda: llm)
    monkeypatch.setattr(
        "backend.logic.engine.field_extraction._legacy_llm_or_default",
        lambda _injected: FakeFieldLLM(),
    )
    monkeypatch.setattr("backend.logic.engine.core.usage_guard.check_message_allowed", fake_check_message_allowed)
    monkeypatch.setattr(agentic_engine.tool_registry.guard, "get_tool_visibility_denial", fake_visibility_denial)
    monkeypatch.setattr("backend.logic.engine.core.review_service.get_review_settings", AsyncMock(return_value=None))
    monkeypatch.setattr("backend.logic.engine.core.log_llm_input_trace", lambda trace: traces.append(trace))
    monkeypatch.setattr(
        "backend.logic.engine.history.db_client.list_messages",
        AsyncMock(return_value={"items": [], "next_cursor": None}),
    )
    monkeypatch.setattr(
        "backend.knowledge.orchestrator.knowledge_orchestrator.build_saved_knowledge_context",
        rag_mock,
    )
    return traces, rag_mock


async def test_greeting_uses_direct_bypass_trace_and_skips_rag(monkeypatch):
    llm = CapturingLLM("This should not be called.")
    traces, rag_mock = await install_engine_doubles(monkeypatch, llm)

    result = await agentic_engine.execute_step(make_context("hello"))

    assert result.response_text == "Hi! How can I help you today?"
    assert result.quick_replies == []
    assert llm.calls == []
    rag_mock.assert_not_called()
    assert traces[-1]["direct_path_bypassed_llm"] is True
    assert traces[-1]["rag_skipped"] is True
    assert traces[-1]["rag_skip_reason"] == "greeting"
    assert traces[-1]["quick_reply_count"] == len(result.quick_replies)


async def test_simple_history_uses_smaller_limit_and_active_workflow_keeps_full_limit(monkeypatch):
    list_messages = AsyncMock(return_value={"items": [], "next_cursor": None})
    monkeypatch.setattr("backend.logic.engine.history.db_client.list_messages", list_messages)
    simple_context = make_context("thanks")
    await build_memory_context(
        simple_context,
        routing_decision=SimpleNamespace(intent="thanks", route_type="direct_answer", is_tangent=False),
    )
    assert list_messages.await_args.kwargs["limit"] == SMALL_HISTORY_LIMIT

    active_context = make_context("tomorrow", state=ConversationState.COLLECTING_DATA, current_module="booking")
    active_context.session_data.state.action_ledger.current_task = "booking"
    active_context.session_data.state.action_ledger.task_status = "collecting"
    await build_memory_context(
        active_context,
        routing_decision=SimpleNamespace(intent="booking", route_type="active_task", is_tangent=False),
    )
    assert list_messages.await_args.kwargs["limit"] == HISTORY_LIMIT


async def test_strict_active_booking_field_collection_skips_rag_but_keeps_llm_context(monkeypatch):
    llm = CapturingLLM("What time should I use?")
    traces, rag_mock = await install_engine_doubles(monkeypatch, llm)
    context = make_context("tomorrow", state=ConversationState.COLLECTING_DATA, current_module="booking")
    context.session_data.state.action_ledger.current_task = "booking"
    context.session_data.state.action_ledger.task_status = "collecting"
    context.session_data.state.scoped_step_data["booking"] = {"service_name": "Dental Cleaning"}

    result = await agentic_engine.execute_step(context)

    assert result.response_text == "What time should I use?"
    rag_mock.assert_not_called()
    assert llm.calls
    assert traces[-1]["rag_skipped"] is True
    assert traces[-1]["rag_skip_reason"] == "transactional_field_collection"
    assert traces[-1]["prompt_sections"]["history_chars"] > 0


async def test_faq_price_route_keeps_rag_grounding_and_records_tool_reduction(monkeypatch):
    llm = CapturingLLM("Dental Cleaning is INR 800.")
    traces, rag_mock = await install_engine_doubles(
        monkeypatch,
        llm,
        rag_text="Knowledge reference title=Prices | source=manual | chunk=1\nTenant answer: Dental Cleaning is INR 800.",
    )

    result = await agentic_engine.execute_step(make_context("How much is Dental Cleaning?"))

    assert result.response_text == "Dental Cleaning is INR 800."
    rag_mock.assert_awaited_once()
    assert traces[-1]["rag_skipped"] is False
    assert traces[-1]["rag_skip_reason"] is None
    assert traces[-1]["unscoped_tool_count"] > traces[-1]["scoped_tool_count"]
    assert traces[-1]["tool_count"] == traces[-1]["scoped_tool_count"]
    assert traces[-1]["scoped_tools_used"] is True
    assert "tool_names" not in traces[-1]
    assert "Dental Cleaning - INR 800 - 45 mins" in llm.calls[-1][0].content


async def test_tangent_factual_question_during_active_workflow_does_not_skip_rag(monkeypatch):
    llm = CapturingLLM("Dental Cleaning is INR 800.")
    traces, rag_mock = await install_engine_doubles(monkeypatch, llm)
    context = make_context("how much is it?", state=ConversationState.COLLECTING_DATA, current_module="booking")
    context.session_data.state.action_ledger.current_task = "booking"
    context.session_data.state.action_ledger.task_status = "collecting"
    context.session_data.state.scoped_step_data["booking"] = {"service_name": "Dental Cleaning"}

    await agentic_engine.execute_step(context)

    rag_mock.assert_awaited_once()
    assert traces[-1]["rag_skipped"] is False
    assert "route_intent" not in traces[-1]


def test_matched_context_compacts_to_current_task_and_preserves_unknown_fallback():
    context = make_context("Dental Cleaning and Masala Tea")

    booking_context = build_matched_business_context(
        context.config.config_data,
        context.incoming_message["text"],
        intent="booking",
        route_type="active_task",
        current_task="booking",
    )
    assert "Relevant services" in booking_context
    assert "Dental Cleaning" in booking_context
    assert "Masala Tea" not in booking_context

    faq_context = build_matched_business_context(
        context.config.config_data,
        "Laser whitening availability",
        intent="faq",
        route_type="direct_answer",
    )
    assert faq_context == ""


def test_trace_metrics_are_uuid_and_pii_safe():
    context = make_context("My phone is +919999999999 and email is asha@example.com")
    trace = build_llm_input_trace(
        context=context,
        provider="openai",
        model="gpt-test",
        integration_mode="real",
        system_instructions="RAW PROMPT +919999999999 asha@example.com",
        memory_context="Customer: +919999999999 asha@example.com",
        knowledge_context="Knowledge reference title=FAQ\nHours",
        route_intent="faq",
        route_type="direct_answer",
        rag_skipped=False,
        quick_reply_count=2,
    )
    dumped = str(trace)

    assert not UUID_RE.search(dumped)
    assert "+919999999999" not in dumped
    assert "asha@example.com" not in dumped
    assert "RAW PROMPT" not in dumped
    assert trace["prompt_sections"]["history_chars"] > 0
    assert trace["prompt_sections"]["rag_chars"] > 0
    assert "route_path" not in trace
