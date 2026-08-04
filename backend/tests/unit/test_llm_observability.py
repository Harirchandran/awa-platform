from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, TypeAdapter, ValidationError

from backend.ai.gateway.errors import LLMRateLimitError, LLMSafetyRejectionError, LLMTimeoutError
from backend.ai.gateway.gateway import LLMGateway
from backend.ai.gateway.models import (
    CanonicalLLMMessage,
    CanonicalToolCall,
    CanonicalToolDefinition,
    LLMFallbackContract,
    LLMPurpose,
    LLMRequest,
    LLMTimeoutClass,
    LLMUsage,
    ProviderInvocationResult,
)
from backend.ai.gateway.registry import LLMProviderRegistry
from backend.ai.gateway.telemetry import (
    InMemoryLLMTelemetryRecorder,
    LLMAttemptCompletedEvent,
    LLMFinalOutcome,
    LLMRequestCompletedEvent,
    LLMRequestStartedEvent,
    LLMTelemetryEvent,
    LLMTelemetryParseStatus,
    NoOpLLMGatewayTelemetryRecorder,
    StructuredLogLLMTelemetryRecorder,
    deterministic_sample,
    pseudonymous_correlation,
    reset_configured_llm_telemetry_recorder,
)
from backend.core.config import Settings
from backend.tests.fakes.fake_llm_gateway import ScriptedLLMProviderAdapter


SENTINELS = [
    "CUSTOMER-MESSAGE-X91", "PHONE-X92", "EMAIL-X93@example.invalid",
    "ADDRESS-X94", "SERVICE-X95", "PRODUCT-X96", "BOOKING-TIME-X97",
    "DELIVERY-X98", "AMOUNT-X99", "CURRENCY-X100", "REFERENCE-X101",
    "OTP-X102", "CARD-X103", "PIN-X104", "API-KEY-X105",
    "TOOL-ARG-X106", "RAG-X107", "ASSISTANT-X108", "PROVIDER-BODY-X109",
]


def _request(*, structured=False, tools=False, side_effect=False) -> LLMRequest:
    purpose = LLMPurpose.FIELD_EXTRACTION if structured else (
        LLMPurpose.TOOL_SELECTION if tools else LLMPurpose.KNOWLEDGE_ANSWER
    )
    return LLMRequest(
        purpose=purpose,
        messages=[CanonicalLLMMessage(role="user", content=" ".join(SENTINELS))],
        safe_context={},
        response_schema=TinyPayload if structured else None,
        tools=[CanonicalToolDefinition(name="catalog.search", input_schema={"type": "object"})] if tools else [],
        timeout_class=(
            LLMTimeoutClass.STRUCTURED_STANDARD if structured else
            LLMTimeoutClass.TOOL_SELECTION_STANDARD if tools else
            LLMTimeoutClass.KNOWLEDGE_STANDARD
        ),
        deterministic_fallback=(
            LLMFallbackContract.PRESERVE_STATE if structured else
            LLMFallbackContract.NO_TOOL if tools else
            LLMFallbackContract.SAFE_NOT_ENOUGH_INFORMATION
        ),
        authoritative_side_effects_occurred=side_effect,
    )


from pydantic import BaseModel, ConfigDict


class TinyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: str


def _gateway(scripts, *, recorder=None, enabled=None, include_usage=True):
    registry = LLMProviderRegistry()
    for provider in ("gemini", "groq", "openai"):
        if provider in scripts:
            registry.register(provider, ScriptedLLMProviderAdapter(provider, scripts[provider]))
    return LLMGateway(
        registry,
        telemetry=recorder,
        telemetry_enabled=enabled,
        telemetry_include_usage=include_usage,
    )


def _types(recorder):
    return [event.event_type.value for event in recorder.events]


def test_event_models_are_frozen_strict_and_have_versioned_discriminators():
    event = LLMRequestStartedEvent(
        gateway_request_id="abc", purpose=LLMPurpose.LOCALIZATION,
        rollout_mode="authoritative", timeout_class="localization_fast",
        tools_allowed=False, tool_count=0, history_allowed=True, message_count=2,
        side_effect_boundary_declared=False, started_at=datetime.now(timezone.utc),
    )
    assert event.schema_version == "1" and event.event_type.value == "llm_request_started"
    with pytest.raises(ValidationError):
        LLMRequestStartedEvent(**event.model_dump(), metadata={"unsafe": "value"})
    with pytest.raises(ValidationError):
        event.tool_count = 2
    adapter = TypeAdapter(LLMTelemetryEvent)
    assert type(adapter.validate_python(event.model_dump())) is LLMRequestStartedEvent


def test_keyed_correlation_is_stable_non_raw_and_requires_a_key():
    first = pseudonymous_correlation("tenant-raw-X201", key=SecretStr("key-one"))
    assert first == pseudonymous_correlation("tenant-raw-X201", key="key-one")
    assert first != pseudonymous_correlation("tenant-raw-X201", key="key-two")
    assert "tenant-raw-X201" not in first and first.startswith("hmac-sha256:")
    with pytest.raises(ValueError):
        pseudonymous_correlation("tenant-raw-X201", key="")


def test_settings_validate_sampling_and_redact_correlation_key():
    configured = Settings(LLM_TELEMETRY_CORRELATION_KEY="CORRELATION-SECRET-X202")
    assert "CORRELATION-SECRET-X202" not in repr(configured)
    assert configured.model_dump(mode="json")["LLM_TELEMETRY_CORRELATION_KEY"] == "**********"
    with pytest.raises(ValidationError):
        Settings(LLM_TELEMETRY_SAMPLE_RATE=1.01)
    with pytest.raises(ValidationError):
        Settings(LLM_TELEMETRY_SAMPLE_RATE=-0.01)


def test_sampling_is_deterministic_and_bounded():
    assert deterministic_sample("request-a", 0.0) is False
    assert deterministic_sample("request-a", 1.0) is True
    assert deterministic_sample("request-a", 0.42) == deterministic_sample("request-a", 0.42)


@pytest.mark.asyncio
async def test_single_success_has_one_coherent_lifecycle_usage_and_no_content():
    recorder = InMemoryLLMTelemetryRecorder()
    response = ProviderInvocationResult(
        content=SENTINELS[-2], model="gemini-safe-model",
        usage=LLMUsage(input_tokens=11, output_tokens=7, total_tokens=18),
    )
    result = await _gateway({"gemini": [response]}, recorder=recorder).invoke(_request())
    assert result.content == SENTINELS[-2]
    assert _types(recorder) == ["llm_request_started", "llm_attempt_completed", "llm_request_completed"]
    attempt = recorder.events[1]
    assert (attempt.input_token_count, attempt.output_token_count, attempt.total_token_count) == (11, 7, 18)
    assert attempt.input_token_count + attempt.output_token_count == attempt.total_token_count
    rendered = "\n".join(event.model_dump_json() for event in recorder.events)
    assert all(sentinel not in rendered for sentinel in SENTINELS)


@pytest.mark.asyncio
async def test_rate_limit_fallback_and_exhaustion_sequences_are_exact():
    recorder = InMemoryLLMTelemetryRecorder()
    result = await _gateway(
        {"gemini": [LLMRateLimitError()], "groq": ["safe"]}, recorder=recorder
    ).invoke(_request())
    assert result.provider == "groq"
    assert _types(recorder) == [
        "llm_request_started", "llm_attempt_completed", "llm_fallback_transition",
        "llm_attempt_completed", "llm_request_completed",
    ]
    assert recorder.events[-1].fallback_used and recorder.events[-1].attempt_count == 2
    assert recorder.events[-1].selected_provider == "groq"

    failed = InMemoryLLMTelemetryRecorder()
    result = await _gateway({
        "gemini": [LLMTimeoutError()], "groq": [LLMTimeoutError()],
        "openai": [LLMTimeoutError()],
    }, recorder=failed).invoke(_request())
    assert result.error.code == "all_providers_failed"
    assert _types(failed).count("llm_attempt_completed") == 3
    assert _types(failed).count("llm_fallback_transition") == 2
    assert failed.events[-1].final_error_code.value == "all_providers_failed"
    assert failed.events[-1].selected_provider is None


@pytest.mark.asyncio
async def test_structured_parse_fallback_and_terminal_safety_rejection():
    recorder = InMemoryLLMTelemetryRecorder()
    result = await _gateway({
        "gemini": ["malformed_structured_output"],
        "groq": [json.dumps({"intent": "booking"})],
    }, recorder=recorder).invoke(_request(structured=True))
    assert result.provider == "groq"
    attempts = [event for event in recorder.events if isinstance(event, LLMAttemptCompletedEvent)]
    assert attempts[0].parse_status in {
        LLMTelemetryParseStatus.MALFORMED_JSON,
        LLMTelemetryParseStatus.SCHEMA_VALIDATION_FAILED,
    }
    assert attempts[1].parse_status is LLMTelemetryParseStatus.SUCCESS

    terminal = InMemoryLLMTelemetryRecorder()
    result = await _gateway({"gemini": [LLMSafetyRejectionError()]}, recorder=terminal).invoke(_request())
    assert result.error.code == "safety_rejection"
    assert _types(terminal).count("llm_attempt_completed") == 1
    assert "llm_fallback_transition" not in _types(terminal)
    assert terminal.events[-1].final_outcome is LLMFinalOutcome.ERROR


@pytest.mark.asyncio
async def test_tool_proposals_record_count_only_and_side_effect_boundary_stops_fallback():
    recorder = InMemoryLLMTelemetryRecorder()
    proposal = CanonicalToolCall(
        id="call-1", name="catalog.search", arguments={"query": SENTINELS[-3]}
    )
    result = await _gateway({
        "gemini": [ProviderInvocationResult(content="", tool_calls=[proposal])]
    }, recorder=recorder).invoke(_request(tools=True))
    assert result.tool_calls[0].arguments["query"] == SENTINELS[-3]
    attempt = next(event for event in recorder.events if isinstance(event, LLMAttemptCompletedEvent))
    assert attempt.tool_call_count == 1
    assert SENTINELS[-3] not in "\n".join(event.model_dump_json() for event in recorder.events)

    stopped = InMemoryLLMTelemetryRecorder()
    result = await _gateway({
        "gemini": [LLMTimeoutError()], "groq": ["must-not-run"],
    }, recorder=stopped).invoke(_request(side_effect=True))
    assert result.error.code == "timeout"
    assert stopped.events[0].side_effect_boundary_declared is True
    assert stopped.events[-1].side_effect_boundary_declared is True
    assert "llm_fallback_transition" not in _types(stopped)


@pytest.mark.asyncio
async def test_recorder_failure_is_fail_open_at_every_lifecycle_point():
    class RaisingRecorder:
        def record(self, event):
            raise RuntimeError(SENTINELS[0])

    result = await _gateway(
        {"gemini": [LLMRateLimitError()], "groq": ["unchanged"]}, recorder=RaisingRecorder()
    ).invoke(_request())
    assert result.content == "unchanged" and result.provider == "groq"


@pytest.mark.asyncio
async def test_cancellation_propagates_without_fallback_or_success_completion():
    class CancellingAdapter:
        provider_name = "gemini"
        model_name = "cancel-model"

        async def invoke(self, request):
            raise asyncio.CancelledError()

    registry = LLMProviderRegistry()
    registry.register("gemini", CancellingAdapter())
    recorder = InMemoryLLMTelemetryRecorder()
    gateway = LLMGateway(registry, telemetry=recorder)
    with pytest.raises(asyncio.CancelledError):
        await gateway.invoke(_request())
    assert _types(recorder) == ["llm_request_started", "llm_request_completed"]
    assert recorder.events[-1].final_outcome is LLMFinalOutcome.CANCELLED
    assert recorder.events[-1].selected_provider is None


@pytest.mark.asyncio
async def test_disabled_and_sampled_out_telemetry_record_nothing():
    recorder = InMemoryLLMTelemetryRecorder()
    result = await _gateway({"gemini": ["safe"]}, recorder=recorder, enabled=False).invoke(_request())
    assert result.content == "safe" and recorder.events == []
    recorder = InMemoryLLMTelemetryRecorder()
    registry = LLMProviderRegistry()
    registry.register("gemini", ScriptedLLMProviderAdapter("gemini", ["safe"] ))
    gateway = LLMGateway(registry, telemetry=recorder, telemetry_sample_rate=0.0)
    assert (await gateway.invoke(_request())).content == "safe"
    assert recorder.events == []


def test_structured_logger_is_bounded_and_fail_open(monkeypatch):
    event = LLMRequestCompletedEvent(
        gateway_request_id="request-safe", purpose=LLMPurpose.KNOWLEDGE_ANSWER,
        rollout_mode="authoritative", completed_at=datetime.now(timezone.utc),
        total_latency_ms=1, attempt_count=1, fallback_used=False,
        final_outcome=LLMFinalOutcome.SUCCESS,
        parse_status=LLMTelemetryParseStatus.NOT_REQUESTED,
        side_effect_boundary_declared=False,
    )
    from backend.ops import logger as logger_module

    captured = []
    monkeypatch.setattr(logger_module.logger, "info", lambda message, **kwargs: captured.append((message, kwargs)))
    StructuredLogLLMTelemetryRecorder().record(event)
    assert captured[0][0] == "llm_request_completed"
    assert "extra_data" in captured[0][1]["extra"]
    monkeypatch.setattr(logger_module.logger, "info", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("logger")))
    StructuredLogLLMTelemetryRecorder().record(event)


def test_noop_and_runtime_reset_are_isolated():
    NoOpLLMGatewayTelemetryRecorder().record(None)
    reset_configured_llm_telemetry_recorder()
    reset_configured_llm_telemetry_recorder()


def test_observability_has_no_domain_authority_or_arbitrary_metrics_labels():
    source = open("backend/ai/gateway/telemetry.py", encoding="utf-8").read()
    prohibited = ["ToolRegistry", "backend.persistence", "backend.logic.workflows", "tool arguments"]
    assert all(token not in source for token in prohibited)
    assert "metadata: dict" not in source


def test_all_shadow_bridges_route_production_records_through_strict_events(monkeypatch):
    import backend.ai.gateway.telemetry as telemetry_module
    from langchain_core.messages import AIMessage
    from backend.logic.engine.core_agent_gateway import record_core_agent_shadow_comparison
    from backend.logic.engine.field_extraction_gateway import record_field_extraction_shadow_comparison
    from backend.logic.engine.pending_decision_gateway import record_pending_decision_shadow_comparison
    from backend.logic.engine.review_extraction_gateway import record_review_shadow_comparison

    recorder = InMemoryLLMTelemetryRecorder()
    monkeypatch.setattr(telemetry_module, "_configured_recorder", recorder)
    record_core_agent_shadow_comparison(AIMessage(content="legacy-secret"), AIMessage(content="gateway-secret"), None)
    record_field_extraction_shadow_comparison(
        "booking", SimpleNamespace(workflow="booking", field_updates={"service_name": "SECRET"}, confidence=.9),
        SimpleNamespace(workflow="booking", field_updates={"service_name": "OTHER"}, confidence=.9), None, None,
    )
    record_pending_decision_shadow_comparison(
        SimpleNamespace(action="change", field_updates={"date": "SECRET"}, confidence=.9, clarification=None),
        SimpleNamespace(action="change", field_updates={"date": "OTHER"}, confidence=.9, clarification=None),
        None, None,
    )
    record_review_shadow_comparison(
        SimpleNamespace(workflow="review", field_updates={"feedback": "SECRET"}, confidence=.9),
        SimpleNamespace(workflow="review", field_updates={"feedback": "OTHER"}, confidence=.9),
        None, None,
    )
    assert [event.event_type.value for event in recorder.events] == ["llm_shadow_comparison"] * 4
    rendered = "\n".join(event.model_dump_json() for event in recorder.events)
    assert "legacy-secret" not in rendered and "gateway-secret" not in rendered
    assert "SECRET" not in rendered and "OTHER" not in rendered


@pytest.mark.asyncio
async def test_core_legacy_invocation_is_typed_fail_open_and_cancellation_safe(monkeypatch):
    import backend.ai.gateway.telemetry as telemetry_module
    from langchain_core.messages import AIMessage, HumanMessage
    from backend.logic.engine.core_agent_gateway import CoreAgentGatewayMode, invoke_core_agent_model

    recorder = InMemoryLLMTelemetryRecorder()
    monkeypatch.setattr(telemetry_module, "_configured_recorder", recorder)

    class Legacy:
        provider = "groq"
        model_name = "legacy-safe-model"

        async def ainvoke(self, messages):
            return AIMessage(content=SENTINELS[-2])

    response = await invoke_core_agent_model(
        [HumanMessage(content=SENTINELS[0])], [], mode=CoreAgentGatewayMode.LEGACY,
        authoritative_tool_result=False, fallback_text="fallback", legacy_model=Legacy(),
    )
    assert response.content == SENTINELS[-2]
    assert recorder.events[-1].event_type.value == "llm_legacy_invocation"
    assert recorder.events[-1].outcome is LLMFinalOutcome.SUCCESS
    assert all(sentinel not in recorder.events[-1].model_dump_json() for sentinel in SENTINELS)

    class CancellingLegacy(Legacy):
        async def ainvoke(self, messages):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await invoke_core_agent_model(
            [HumanMessage(content="cancel")], [], mode=CoreAgentGatewayMode.LEGACY,
            authoritative_tool_result=False, fallback_text="fallback", legacy_model=CancellingLegacy(),
        )
    assert recorder.events[-1].outcome is LLMFinalOutcome.CANCELLED
