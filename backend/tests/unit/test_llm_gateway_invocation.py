import pytest
from pydantic import BaseModel, ConfigDict

from backend.ai.gateway.errors import LLMInvalidRequestError
from backend.ai.gateway.gateway import LLMGateway
from backend.ai.gateway.models import (
    CanonicalLLMMessage,
    CanonicalToolDefinition,
    LLMFallbackContract,
    LLMPurpose,
    LLMRequest,
    LLMTimeoutClass,
    ProviderInvocationResult,
)
from backend.ai.gateway.registry import LLMProviderRegistry
from backend.tests.fakes.fake_llm_gateway import (
    RecordingTelemetry,
    ScriptedLLMProviderAdapter,
)


class IntentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: str
    confidence: float


def _structured_request(**overrides):
    values = {
        "purpose": LLMPurpose.FIELD_EXTRACTION,
        "messages": [CanonicalLLMMessage(role="user", content="book tomorrow")],
        "safe_context": {"active_workflow": "booking"},
        "response_schema": IntentResult,
        "timeout_class": LLMTimeoutClass.STRUCTURED_STANDARD,
        "deterministic_fallback": LLMFallbackContract.PRESERVE_STATE,
    }
    values.update(overrides)
    return LLMRequest(**values)


def _text_request(**overrides):
    values = {
        "purpose": LLMPurpose.KNOWLEDGE_ANSWER,
        "messages": [CanonicalLLMMessage(role="user", content="When are you open?")],
        "safe_context": {"rag": "Open Monday"},
        "timeout_class": LLMTimeoutClass.KNOWLEDGE_STANDARD,
        "deterministic_fallback": LLMFallbackContract.SAFE_NOT_ENOUGH_INFORMATION,
    }
    values.update(overrides)
    return LLMRequest(**values)


def _gateway(scripts, *, telemetry=None):
    registry = LLMProviderRegistry()
    adapters = {}
    for provider, outcomes in scripts.items():
        adapter = ScriptedLLMProviderAdapter(provider, outcomes)
        registry.register(provider, adapter)
        adapters[provider] = adapter
    return LLMGateway(registry, telemetry=telemetry), adapters


@pytest.mark.asyncio
async def test_gemini_success_stops_fallback():
    gateway, adapters = _gateway({
        "gemini": [ProviderInvocationResult(content="answer")],
        "groq": [ProviderInvocationResult(content="unused")],
    })
    result = await gateway.invoke(_text_request())
    assert result.content == "answer"
    assert result.provider == "gemini"
    assert not result.fallback_used
    assert not adapters["groq"].calls


@pytest.mark.asyncio
async def test_rate_limit_falls_back_to_groq_success():
    gateway, adapters = _gateway({"gemini": ["rate_limit"], "groq": ["groq answer"]})
    result = await gateway.invoke(_text_request())
    assert result.content == "groq answer"
    assert [attempt.provider for attempt in result.attempts] == ["gemini", "groq"]
    assert result.fallback_used


@pytest.mark.asyncio
async def test_two_timeouts_then_openai_success_without_duplicate_attempts():
    gateway, adapters = _gateway({
        "gemini": ["timeout"],
        "groq": ["timeout"],
        "openai": ["openai answer"],
    })
    result = await gateway.invoke(_text_request())
    assert result.content == "openai answer"
    assert [attempt.provider for attempt in result.attempts] == ["gemini", "groq", "openai"]
    assert [attempt.attempt_number for attempt in result.attempts] == [1, 2, 3]
    assert all(len(adapter.calls) == 1 for adapter in adapters.values())


@pytest.mark.asyncio
async def test_all_transient_failures_return_typed_exhaustion():
    gateway, _ = _gateway({"gemini": ["network"], "groq": ["timeout"], "openai": ["rate_limit"]})
    result = await gateway.invoke(_text_request())
    assert result.error.code == "all_providers_failed"
    assert len(result.attempts) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["safety_rejection"])
async def test_terminal_safety_rejection_stops_immediately(terminal):
    gateway, adapters = _gateway({"gemini": [terminal], "groq": ["unused"]})
    result = await gateway.invoke(_text_request())
    assert result.error.code == terminal
    assert len(result.attempts) == 1
    assert not adapters["groq"].calls


@pytest.mark.asyncio
async def test_invalid_request_stops_before_provider_invocation():
    gateway, adapters = _gateway({"gemini": ["unused"]})
    request = _text_request(timeout_class=LLMTimeoutClass.COMPOSITION_STANDARD)
    result = await gateway.invoke(request)
    assert result.error.code == LLMInvalidRequestError.code
    assert result.attempts == []
    assert not adapters["gemini"].calls


@pytest.mark.asyncio
async def test_malformed_structured_output_falls_back_to_valid_groq_result():
    gateway, _ = _gateway({
        "gemini": ["malformed_structured_output"],
        "groq": [ProviderInvocationResult(content='{"intent":"booking","confidence":0.9}')],
    })
    result = await gateway.invoke(_structured_request())
    assert isinstance(result.content, IntentResult)
    assert result.content.intent == "booking"
    assert result.attempts[0].parse_status.value == "failed"
    assert result.attempts[1].parse_status.value == "success"


@pytest.mark.asyncio
async def test_side_effect_boundary_prevents_fallback():
    gateway, adapters = _gateway({"gemini": ["timeout"], "groq": ["must not run"]})
    result = await gateway.invoke(_text_request(authoritative_side_effects_occurred=True))
    assert result.error.code == "timeout"
    assert not adapters["groq"].calls


@pytest.mark.asyncio
async def test_telemetry_contains_metadata_only_and_one_logical_request_id():
    telemetry = RecordingTelemetry()
    gateway, _ = _gateway({"gemini": ["rate_limit"], "groq": ["safe answer"]}, telemetry=telemetry)
    result = await gateway.invoke(_text_request())
    assert [event.event_type.value for event in telemetry.events] == [
        "llm_request_started",
        "llm_attempt_completed",
        "llm_fallback_transition",
        "llm_attempt_completed",
        "llm_request_completed",
    ]
    assert len({event.gateway_request_id for event in telemetry.events}) == 1
    serialized = "\n".join(event.model_dump_json() for event in telemetry.events)
    assert "When are you open" not in serialized
    assert "safe answer" not in serialized
    assert "tool arguments" not in serialized
    transition = telemetry.events[2]
    assert transition.from_provider == "gemini"
    assert transition.to_provider == "groq"
    assert telemetry.events[-1].selected_provider == "groq"
    assert telemetry.events[-1].fallback_used is True


@pytest.mark.asyncio
async def test_response_composition_rejects_tools_before_invocation():
    gateway, adapters = _gateway({"gemini": ["unused"]})
    request = LLMRequest(
        purpose=LLMPurpose.RESPONSE_COMPOSITION,
        messages=[CanonicalLLMMessage(role="user", content="compose")],
        safe_context={"fact": "safe"},
        tools=[CanonicalToolDefinition(name="unsafe.write", input_schema={"type": "object"})],
        timeout_class=LLMTimeoutClass.COMPOSITION_STANDARD,
        deterministic_fallback=LLMFallbackContract.ORIGINAL_BACKEND_TEXT,
    )
    result = await gateway.invoke(request)
    assert result.error.code == "invalid_request"
    assert not adapters["gemini"].calls


@pytest.mark.asyncio
async def test_injected_provider_order_is_respected():
    registry = LLMProviderRegistry()
    openai = ScriptedLLMProviderAdapter("openai", ["first"])
    gemini = ScriptedLLMProviderAdapter("gemini", ["must not run"])
    registry.register("openai", openai)
    registry.register("gemini", gemini)
    gateway = LLMGateway(registry, provider_order=("openai", "gemini"))
    result = await gateway.invoke(_text_request())
    assert result.provider == "openai"
    assert not gemini.calls


@pytest.mark.asyncio
async def test_tool_protocol_fallback_is_limited_to_pre_dispatch_tool_selection():
    tool = CanonicalToolDefinition(name="catalog.search", input_schema={"type": "object"})
    gateway, _ = _gateway({"gemini": ["tool_protocol"], "groq": ["no tool selected"]})
    request = LLMRequest(
        purpose=LLMPurpose.TOOL_SELECTION,
        messages=[CanonicalLLMMessage(role="user", content="find a service")],
        safe_context={},
        tools=[tool],
        timeout_class=LLMTimeoutClass.TOOL_SELECTION_STANDARD,
        deterministic_fallback=LLMFallbackContract.NO_TOOL,
    )
    result = await gateway.invoke(request)
    assert result.provider == "groq"

    stopped_gateway, adapters = _gateway({"gemini": ["tool_protocol"], "groq": ["must not run"]})
    stopped = await stopped_gateway.invoke(_text_request())
    assert stopped.error.code == "tool_protocol"
    assert not adapters["groq"].calls
