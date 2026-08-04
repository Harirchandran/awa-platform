import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from backend.ai.gateway.models import (
    CanonicalToolCall,
    LLMPurpose,
    LLMParseStatus,
    LLMResult,
)
from backend.ai.providers.gemini_chat_adapter import PROVIDER_METADATA_KEY
from backend.core.config import Settings
from backend.logic.engine.core_agent_gateway import (
    CoreAgentGatewayMode,
    build_core_agent_gateway_request,
    gateway_result_to_ai_message,
    invoke_core_agent_model,
    langchain_messages_to_canonical,
    langchain_tools_to_canonical,
    record_core_agent_shadow_comparison,
    resolve_core_agent_gateway_mode,
)


pytestmark = pytest.mark.anyio


def _result(*, content="gateway", calls=(), provider="gemini", metadata=None, error=None):
    return LLMResult(
        content=content,
        provider=provider,
        model="model",
        attempts=[],
        latency_ms=1,
        parse_status=LLMParseStatus.NOT_REQUESTED,
        error=error,
        tool_calls=list(calls),
        provider_metadata=metadata or {},
    )


class FakeGateway:
    def __init__(self, result=None, exc=None):
        self.result = result or _result()
        self.exc = exc
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        if self.exc:
            raise self.exc
        return self.result


class FakeLegacy:
    def __init__(self, response=None):
        self.response = response or AIMessage(content="legacy")
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return self.response


def _messages():
    return [SystemMessage(content="bounded rules"), HumanMessage(content="hello")]


def _tools():
    return [{
        "type": "function",
        "function": {
            "name": "booking_execute",
            "description": "Propose a booking",
            "parameters": {
                "type": "object",
                "properties": {"service": {"type": "string", "enum": ["Cleaning"]}},
                "required": ["service"],
                "additionalProperties": False,
            },
        },
    }]


def test_core_mode_has_global_kill_switch_and_rejects_unknown_values():
    base = Settings(_env_file=None)
    assert resolve_core_agent_gateway_mode(base.model_copy(update={
        "LLM_GATEWAY_ENABLED": False,
        "LLM_GATEWAY_CORE_AGENT_MODE": "authoritative",
    })) is CoreAgentGatewayMode.LEGACY
    assert resolve_core_agent_gateway_mode(base.model_copy(update={
        "LLM_GATEWAY_ENABLED": True,
        "LLM_GATEWAY_CORE_AGENT_MODE": "shadow",
    })) is CoreAgentGatewayMode.SHADOW
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LLM_GATEWAY_CORE_AGENT_MODE="unknown")


def test_request_classifies_tool_knowledge_and_post_tool_invocations():
    tool_request = build_core_agent_gateway_request(_messages(), _tools(), authoritative_tool_result=False)
    assert tool_request.purpose is LLMPurpose.TOOL_SELECTION
    assert len(tool_request.tools) == 1

    knowledge_request = build_core_agent_gateway_request(_messages(), [], authoritative_tool_result=False)
    assert knowledge_request.purpose is LLMPurpose.KNOWLEDGE_ANSWER
    assert knowledge_request.tools == []

    post_messages = _messages() + [
        AIMessage(content="", tool_calls=[{"id": "call_1", "name": "booking_execute", "args": {"service": "Cleaning"}}]),
        ToolMessage(content=json.dumps({"success": True, "user_message": "Booked."}), tool_call_id="call_1"),
    ]
    post_request = build_core_agent_gateway_request(post_messages, _tools(), authoritative_tool_result=True)
    assert post_request.purpose is LLMPurpose.RESPONSE_COMPOSITION
    assert post_request.tools == []
    assert post_request.authoritative_side_effects_occurred is True
    assert post_request.messages[-1].tool_result.tool_call_id == "call_1"


def test_message_conversion_preserves_correlation_and_only_gemini_owned_signature():
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "call_1", "name": "booking_execute", "args": {"service": "Cleaning"}}],
            additional_kwargs={PROVIDER_METADATA_KEY: {
                "provider": "gemini",
                "tool_call_thought_signatures": {"call_1": "opaque", "other": "strip"},
                "unsafe": "strip",
            }},
        ),
        ToolMessage(content='{"success":true}', tool_call_id="call_1"),
    ]
    canonical = langchain_messages_to_canonical(messages)
    assert canonical[0].provider_metadata == {
        "gemini": {"tool_call_thought_signatures": {"call_1": "opaque"}}
    }
    assert canonical[1].tool_result.tool_call_id == canonical[0].tool_calls[0].id
    assert canonical[1].tool_result.tool_name == "booking_execute"


def test_tool_conversion_preserves_json_schema_and_fails_closed():
    converted = langchain_tools_to_canonical(_tools())
    assert converted[0].input_schema["required"] == ["service"]
    assert converted[0].input_schema["additionalProperties"] is False
    with pytest.raises(ValueError, match="duplicate"):
        langchain_tools_to_canonical(_tools() + _tools())
    with pytest.raises(ValueError, match="unsupported"):
        langchain_tools_to_canonical([{"type": "python", "function": {}}])


async def test_legacy_mode_invokes_only_legacy_and_returns_graph_message():
    legacy = FakeLegacy(AIMessage(content="legacy authoritative"))
    gateway = FakeGateway(exc=AssertionError("must not invoke"))
    response = await invoke_core_agent_model(
        _messages(), _tools(), mode=CoreAgentGatewayMode.LEGACY,
        authoritative_tool_result=False, fallback_text="fallback",
        legacy_model=legacy, gateway=gateway,
    )
    assert response.content == "legacy authoritative"
    assert len(legacy.calls) == 1
    assert gateway.requests == []


async def test_shadow_mode_returns_legacy_and_never_exposes_gateway_tool_call():
    legacy = FakeLegacy(AIMessage(content="legacy text"))
    gateway = FakeGateway(_result(content="", calls=[
        CanonicalToolCall(id="call_shadow", name="booking_execute", arguments={"service": "private"})
    ]))
    comparisons = []
    response = await invoke_core_agent_model(
        _messages(), _tools(), mode=CoreAgentGatewayMode.SHADOW,
        authoritative_tool_result=False, fallback_text="fallback",
        legacy_model=legacy, gateway=gateway, shadow_recorder=comparisons.append,
    )
    assert response.content == "legacy text"
    assert response.tool_calls == []
    assert len(legacy.calls) == len(gateway.requests) == 1
    assert comparisons[0]["gateway_tool_name_set"] == ["booking_execute"]
    assert "private" not in json.dumps(comparisons)


async def test_shadow_failure_and_recorder_failure_are_fail_open():
    legacy = FakeLegacy()
    response = await invoke_core_agent_model(
        _messages(), [], mode=CoreAgentGatewayMode.SHADOW,
        authoritative_tool_result=False, fallback_text="fallback",
        legacy_model=legacy, gateway=FakeGateway(exc=RuntimeError("provider")),
        shadow_recorder=lambda _metadata: (_ for _ in ()).throw(RuntimeError("recorder")),
    )
    assert response.content == "legacy"


async def test_authoritative_text_uses_gateway_without_legacy():
    gateway = FakeGateway(_result(content="gateway answer", provider="groq"))
    response = await invoke_core_agent_model(
        _messages(), [], mode=CoreAgentGatewayMode.AUTHORITATIVE,
        authoritative_tool_result=False, fallback_text="safe insufficient information",
        legacy_model=None, gateway=gateway,
    )
    assert response.content == "gateway answer"
    assert gateway.requests[0].purpose is LLMPurpose.KNOWLEDGE_ANSWER
    assert response.response_metadata["awa_gateway_provider"] == "groq"


async def test_authoritative_tool_proposal_preserves_id_name_and_arguments():
    call = CanonicalToolCall(
        id="call_selected", name="booking_execute", arguments={"service": "Cleaning"}
    )
    response = await invoke_core_agent_model(
        _messages(), _tools(), mode=CoreAgentGatewayMode.AUTHORITATIVE,
        authoritative_tool_result=False, fallback_text="no tool",
        gateway=FakeGateway(_result(content="", calls=[call])),
    )
    assert response.tool_calls == [{
        "name": "booking_execute", "args": {"service": "Cleaning"},
        "id": "call_selected", "type": "tool_call",
    }]


async def test_authoritative_rejects_parallel_calls_and_post_tool_injection():
    calls = [
        CanonicalToolCall(id="call_1", name="booking_execute", arguments={}),
        CanonicalToolCall(id="call_2", name="order_execute", arguments={}),
    ]
    parallel = await invoke_core_agent_model(
        _messages(), _tools(), mode=CoreAgentGatewayMode.AUTHORITATIVE,
        authoritative_tool_result=False, fallback_text="no dispatch",
        gateway=FakeGateway(_result(content="", calls=calls)),
    )
    assert parallel.content == "no dispatch"
    assert parallel.tool_calls == []

    injected = await invoke_core_agent_model(
        _messages(), _tools(), mode=CoreAgentGatewayMode.AUTHORITATIVE,
        authoritative_tool_result=True, fallback_text="tool-derived fallback",
        gateway=FakeGateway(_result(content="", calls=[calls[0]])),
    )
    assert injected.content == "tool-derived fallback"
    assert injected.tool_calls == []


async def test_authoritative_exhaustion_uses_fallback_and_cancellation_propagates():
    failed = await invoke_core_agent_model(
        _messages(), [], mode=CoreAgentGatewayMode.AUTHORITATIVE,
        authoritative_tool_result=False, fallback_text="safe fallback",
        gateway=FakeGateway(exc=RuntimeError("exhausted")),
    )
    assert failed.content == "safe fallback"
    with pytest.raises(asyncio.CancelledError):
        await invoke_core_agent_model(
            _messages(), [], mode=CoreAgentGatewayMode.AUTHORITATIVE,
            authoritative_tool_result=False, fallback_text="safe fallback",
            gateway=FakeGateway(exc=asyncio.CancelledError()),
        )


def test_gateway_gemini_metadata_returns_to_graph_without_entering_text():
    call = CanonicalToolCall(id="call_1", name="booking_execute", arguments={})
    message = gateway_result_to_ai_message(
        _result(
            content="",
            calls=[call],
            metadata={"gemini": {"tool_call_thought_signatures": {"call_1": "opaque"}}},
        ),
        fallback_text="fallback",
    )
    assert message.additional_kwargs[PROVIDER_METADATA_KEY]["tool_call_thought_signatures"] == {"call_1": "opaque"}
    assert "opaque" not in str(message.content)


def test_shadow_metadata_contains_no_text_or_argument_values():
    captured = []
    record_core_agent_shadow_comparison(
        AIMessage(content="CUSTOMER_SENTINEL", tool_calls=[{
            "id": "l", "name": "booking_execute", "args": {"phone": "PHONE_SENTINEL"}
        }]),
        AIMessage(content="GATEWAY_SENTINEL", tool_calls=[]),
        _result(),
        captured.append,
    )
    rendered = json.dumps(captured)
    assert "CUSTOMER_SENTINEL" not in rendered
    assert "GATEWAY_SENTINEL" not in rendered
    assert "PHONE_SENTINEL" not in rendered


def test_architecture_bridge_has_no_execution_or_domain_imports():
    from pathlib import Path
    import backend.logic.engine.core_agent_gateway as bridge

    source = Path(bridge.__file__).read_text(encoding="utf-8")
    assert "execute_tool" not in source
    assert "backend.tools.registry" not in source
    assert "backend.domains" not in source
    assert "backend.logic.engine.turn_interpretation" not in source


async def test_real_graph_authoritative_proposal_dispatches_once_then_composes_without_tools(monkeypatch):
    from backend.logic.engine import agentic_engine
    from backend.logic.engine import core as core_module
    from backend.tests.unit.test_conversation_ux import make_context, install_agent_test_doubles
    from backend.tools.registry import ToolExecutionResult

    await install_agent_test_doubles(monkeypatch, FakeLegacy())
    monkeypatch.setattr(core_module.settings, "TURN_INTERPRETATION_MODE", "legacy")

    async def neutral_profile(**_kwargs):
        return None

    monkeypatch.setattr(core_module, "resolve_runtime_behavior_profile", neutral_profile)
    monkeypatch.setattr(core_module.settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(core_module.settings, "LLM_GATEWAY_CORE_AGENT_MODE", "authoritative")
    monkeypatch.setattr(core_module.settings, "LLM_GATEWAY_RESPONSE_COMPOSER_MODE", "authoritative")
    monkeypatch.setattr(
        "backend.logic.engine.llm.get_llm",
        lambda: (_ for _ in ()).throw(AssertionError("legacy model must not be constructed")),
    )

    proposal = CanonicalToolCall(
        id="call_graph",
        name="booking_execute",
        arguments={
            "service_name": "Dental Cleaning",
            "preferred_date": "tomorrow",
            "preferred_time": "10 AM",
            "customer_name": "Asha",
        },
    )
    gateway = FakeGateway()
    gateway.results = [
        _result(content="", calls=[proposal], provider="groq"),
        _result(content="Your booking was completed.", provider="gemini"),
    ]

    async def invoke_sequence(request):
        gateway.requests.append(request)
        return gateway.results.pop(0)

    gateway.invoke = invoke_sequence
    monkeypatch.setattr(
        "backend.ai.gateway.runtime.get_runtime_llm_gateway",
        lambda: gateway,
    )

    dispatches = []

    async def execute_once(tool_key, _context):
        dispatches.append(tool_key)
        return ToolExecutionResult(
            tool_key=tool_key,
            success=True,
            user_message="Your booking was completed.",
            created_resource_type="booking",
            created_resource_id="BOOK-1",
        )

    monkeypatch.setattr(agentic_engine.tool_registry, "execute_tool", execute_once)
    context = make_context(message={"text": "Book cleaning tomorrow at 10 AM for Asha"})
    response = await agentic_engine.execute_step(context)

    assert dispatches == ["booking.execute"]
    assert response.response_text == "Your booking was completed."
    assert [request.purpose for request in gateway.requests] == [
        LLMPurpose.TOOL_SELECTION,
        LLMPurpose.RESPONSE_COMPOSITION,
    ]
    assert gateway.requests[1].tools == []
    assert gateway.requests[1].authoritative_side_effects_occurred is True
