import math
import hashlib

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from backend.ai.gateway.models import (
    MAX_COMBINED_MESSAGE_CHARS,
    MAX_MESSAGE_CHARS,
    CanonicalLLMMessage,
    CanonicalToolCall,
    CanonicalToolDefinition,
    CanonicalToolResult,
    LLMFallbackContract,
    LLMErrorInfo,
    LLMMessageRole,
    LLMPurpose,
    LLMRequest,
    LLMTelemetryContext,
    LLMTimeoutClass,
)


class StructuredAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: str
    confidence: float


def _message(content: str = "hello") -> CanonicalLLMMessage:
    return CanonicalLLMMessage(role="user", content=content)


def _request(**overrides):
    values = {
        "purpose": LLMPurpose.FIELD_EXTRACTION,
        "messages": [_message()],
        "safe_context": {"active_workflow": "booking"},
        "response_schema": StructuredAnswer,
        "tools": [],
        "language": "en-IN",
        "timeout_class": LLMTimeoutClass.STRUCTURED_STANDARD,
        "deterministic_fallback": LLMFallbackContract.PRESERVE_STATE,
        "telemetry_context": LLMTelemetryContext(
            tenant_correlation=f"sha256:{hashlib.sha256(b'tenant').hexdigest()}"
        ),
    }
    values.update(overrides)
    return LLMRequest(**values)


def test_request_accepts_provider_neutral_bounded_contract():
    request = _request()
    assert request.purpose is LLMPurpose.FIELD_EXTRACTION
    assert request.response_schema is StructuredAnswer


@pytest.mark.parametrize("prohibited", ["provider", "model", "api_key", "timeout_seconds", "retry_count"])
def test_request_rejects_prohibited_provider_controls(prohibited):
    with pytest.raises(ValidationError):
        _request(**{prohibited: "unsafe"})


def test_message_and_combined_content_bounds_are_enforced():
    with pytest.raises(ValidationError):
        _message("x" * (MAX_MESSAGE_CHARS + 1))
    messages = [_message("x" * MAX_MESSAGE_CHARS) for _ in range(MAX_COMBINED_MESSAGE_CHARS // MAX_MESSAGE_CHARS + 1)]
    with pytest.raises(ValidationError):
        _request(messages=messages)


def test_tool_call_and_result_roles_are_validated():
    call = CanonicalToolCall(id="call_1", name="booking.check", arguments={"date": "2026-07-15"})
    assistant = CanonicalLLMMessage(role="assistant", tool_calls=[call])
    result = CanonicalLLMMessage(
        role="tool",
        tool_result=CanonicalToolResult(
            tool_call_id="call_1",
            tool_name="booking.check",
            success=True,
            message="Available",
            public_reference="slot-1",
            safe_data={"available": True},
        ),
    )
    assert assistant.tool_calls[0].id == result.tool_result.tool_call_id
    with pytest.raises(ValidationError):
        CanonicalLLMMessage(role="user", content="hi", tool_calls=[call])


@pytest.mark.parametrize("value", [{"bad": object()}, {"bad": math.nan}])
def test_json_compatibility_rejects_native_objects_and_non_finite_numbers(value):
    with pytest.raises(ValidationError):
        CanonicalToolCall(id="call_1", name="safe.tool", arguments=value)
    with pytest.raises(ValidationError):
        _request(safe_context=value)


def test_provider_metadata_requires_known_namespace():
    message = CanonicalLLMMessage(
        role="assistant",
        tool_calls=[CanonicalToolCall(id="call_1", name="safe.tool", arguments={})],
        provider_metadata={"gemini": {"tool_call_thought_signatures": {"call_1": "opaque"}}},
    )
    assert "gemini" in message.provider_metadata
    with pytest.raises(ValidationError):
        CanonicalLLMMessage(role="user", content="hi", provider_metadata={"unknown": {"x": 1}})
    with pytest.raises(ValidationError):
        CanonicalLLMMessage(role="user", content="hi", provider_metadata={"openai": {"api_key": "secret"}})


def test_tool_schema_and_count_are_bounded():
    tool = CanonicalToolDefinition(name="safe.tool", input_schema={"type": "object"})
    request = _request(purpose=LLMPurpose.TOOL_SELECTION, response_schema=None, tools=[tool], timeout_class="tool_selection_standard", deterministic_fallback="no_tool")
    assert request.tools == [tool]


def test_safe_tool_result_rejects_sensitive_fields_and_raw_correlations():
    with pytest.raises(ValidationError):
        CanonicalToolResult(
            tool_call_id="call_1",
            tool_name="safe.tool",
            success=False,
            safe_data={"otp": "123456"},
        )
    with pytest.raises(ValidationError):
        LLMTelemetryContext(tenant_correlation="raw-tenant-id")
    with pytest.raises(ValidationError):
        LLMErrorInfo(
            code="rate_limit",
            retry_same_provider=True,
            fallback_next_provider=True,
            log_level="WARNING",
            safe_metadata={"raw_response": "provider body"},
        )
