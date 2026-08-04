"""Provider-neutral contracts for the additive LLM gateway foundation."""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_MESSAGE_COUNT = 32
MAX_MESSAGE_CHARS = 4_000
MAX_COMBINED_MESSAGE_CHARS = 32_000
MAX_SAFE_CONTEXT_CHARS = 16_000
MAX_TOOL_COUNT = 32
MAX_TOOL_SCHEMA_CHARS = 12_000
MAX_STRUCTURED_OUTPUT_CHARS = 32_000
MAX_TOOL_ARGUMENT_CHARS = 8_000
MAX_SAFE_DATA_CHARS = 8_000
KNOWN_PROVIDERS = ("gemini", "groq", "openai")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,119}$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,12}(?:[-_][A-Za-z0-9]{2,12})?$")
_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|secret|password|credential|otp|cvv|upi[_-]?pin|access[_-]?token|raw[_-]?(?:payload|response))",
    re.IGNORECASE,
)
_CORRELATION = re.compile(r"^sha256:[0-9a-f]{64}$")


def _json_size(value: Any, *, label: str, maximum: int) -> int:
    try:
        rendered = json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON-compatible") from exc
    if len(rendered) > maximum:
        raise ValueError(f"{label} exceeds the maximum encoded size")
    return len(rendered)


def _reject_sensitive_keys(value: Any, *, label: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if _SENSITIVE_KEY.search(str(key)):
                raise ValueError(f"{label} contains a prohibited sensitive field")
            _reject_sensitive_keys(item, label=label)
    elif isinstance(value, list):
        for item in value:
            _reject_sensitive_keys(item, label=label)


class LLMPurpose(str, Enum):
    TURN_INTERPRETATION = "turn_interpretation"
    FIELD_EXTRACTION = "field_extraction"
    PENDING_ACTION_DECISION = "pending_action_decision"
    KNOWLEDGE_ANSWER = "knowledge_answer"
    RESPONSE_COMPOSITION = "response_composition"
    LOCALIZATION = "localization"
    TOOL_SELECTION = "tool_selection"


class LLMMessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LLMTimeoutClass(str, Enum):
    SEMANTIC_FAST = "semantic_fast"
    STRUCTURED_STANDARD = "structured_standard"
    KNOWLEDGE_STANDARD = "knowledge_standard"
    COMPOSITION_STANDARD = "composition_standard"
    TOOL_SELECTION_STANDARD = "tool_selection_standard"
    LOCALIZATION_FAST = "localization_fast"


class LLMFallbackContract(str, Enum):
    CLARIFY = "clarify"
    PRESERVE_STATE = "preserve_state"
    ORIGINAL_BACKEND_TEXT = "original_backend_text"
    SAFE_NOT_ENOUGH_INFORMATION = "safe_not_enough_information"
    NO_TOOL = "no_tool"
    FAIL_CLOSED = "fail_closed"


class LLMParseStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    SUCCESS = "success"
    FAILED = "failed"


class LLMAttemptOutcome(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


class CanonicalToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        value = value.strip()
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("tool call id contains unsupported characters")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not _TOOL_NAME.fullmatch(value):
            raise ValueError("tool name contains unsupported characters")
        return value

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value: dict[str, Any]) -> dict[str, Any]:
        _json_size(value, label="tool arguments", maximum=MAX_TOOL_ARGUMENT_CHARS)
        return value


class CanonicalToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str = Field(min_length=1, max_length=120)
    tool_name: str = Field(min_length=1, max_length=120)
    success: bool
    message: str | None = Field(default=None, max_length=1_000)
    public_reference: str | None = Field(default=None, max_length=240)
    safe_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_call_id")
    @classmethod
    def validate_call_id(cls, value: str) -> str:
        value = value.strip()
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("tool result call id contains unsupported characters")
        return value

    @field_validator("tool_name")
    @classmethod
    def validate_tool_name(cls, value: str) -> str:
        value = value.strip()
        if not _TOOL_NAME.fullmatch(value):
            raise ValueError("tool result name contains unsupported characters")
        return value

    @field_validator("safe_data")
    @classmethod
    def validate_safe_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        _json_size(value, label="safe tool result data", maximum=MAX_SAFE_DATA_CHARS)
        _reject_sensitive_keys(value, label="safe tool result data")
        return value


class CanonicalToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    input_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not _TOOL_NAME.fullmatch(value):
            raise ValueError("tool definition name contains unsupported characters")
        return value

    @field_validator("input_schema")
    @classmethod
    def validate_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        _json_size(value, label="tool schema", maximum=MAX_TOOL_SCHEMA_CHARS)
        return value


class CanonicalLLMMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: LLMMessageRole
    content: str = Field(default="", max_length=MAX_MESSAGE_CHARS)
    message_id: str | None = Field(default=None, min_length=1, max_length=120)
    tool_calls: list[CanonicalToolCall] = Field(default_factory=list, max_length=MAX_TOOL_COUNT)
    tool_result: CanonicalToolResult | None = None
    provider_metadata: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("message_id")
    @classmethod
    def validate_message_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("message id contains unsupported characters")
        return value

    @field_validator("provider_metadata")
    @classmethod
    def validate_provider_metadata(cls, value: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        normalized: dict[str, dict[str, Any]] = {}
        for owner, metadata in value.items():
            provider = str(owner).strip().lower()
            if provider not in KNOWN_PROVIDERS:
                raise ValueError("provider metadata must use a known provider namespace")
            if not isinstance(metadata, dict):
                raise ValueError("provider metadata namespace must contain an object")
            _json_size(metadata, label=f"{provider} provider metadata", maximum=4_000)
            _reject_sensitive_keys(metadata, label=f"{provider} provider metadata")
            normalized[provider] = metadata
        return normalized

    @model_validator(mode="after")
    def validate_role_shape(self) -> "CanonicalLLMMessage":
        if self.role in {LLMMessageRole.SYSTEM, LLMMessageRole.USER} and not self.content.strip():
            raise ValueError("system and user messages require text content")
        if self.role == LLMMessageRole.TOOL:
            if self.tool_result is None or self.tool_calls:
                raise ValueError("tool messages require exactly one canonical tool result")
        elif self.tool_result is not None:
            raise ValueError("tool results are allowed only on tool messages")
        if self.role != LLMMessageRole.ASSISTANT and self.tool_calls:
            raise ValueError("canonical tool calls are allowed only on assistant messages")
        if self.role == LLMMessageRole.ASSISTANT and not self.content.strip() and not self.tool_calls:
            raise ValueError("assistant messages require text or canonical tool calls")
        return self


class LLMTelemetryContext(BaseModel):
    """Only pre-hashed or otherwise pseudonymous correlation values are accepted."""

    model_config = ConfigDict(extra="forbid")

    tenant_correlation: str | None = Field(default=None, max_length=128)
    conversation_correlation: str | None = Field(default=None, max_length=128)
    config_version: int | None = Field(default=None, ge=1)

    @field_validator("tenant_correlation", "conversation_correlation")
    @classmethod
    def validate_correlation(cls, value: str | None) -> str | None:
        if value is not None and not _CORRELATION.fullmatch(value):
            raise ValueError("telemetry correlations must be SHA-256 pseudonyms")
        return value


T = TypeVar("T")


class LLMRequest(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    purpose: LLMPurpose
    messages: list[CanonicalLLMMessage] = Field(min_length=1, max_length=MAX_MESSAGE_COUNT)
    safe_context: dict[str, Any] = Field(default_factory=dict)
    response_schema: type[BaseModel] | None = None
    tools: list[CanonicalToolDefinition] = Field(default_factory=list, max_length=MAX_TOOL_COUNT)
    language: str | None = Field(default=None, max_length=32)
    timeout_class: LLMTimeoutClass
    deterministic_fallback: LLMFallbackContract
    telemetry_context: LLMTelemetryContext = Field(default_factory=LLMTelemetryContext)
    authoritative_side_effects_occurred: bool = False

    @field_validator("safe_context")
    @classmethod
    def validate_safe_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        _json_size(value, label="safe context", maximum=MAX_SAFE_CONTEXT_CHARS)
        return value

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not _LANGUAGE.fullmatch(value):
            raise ValueError("language must be a bounded language tag")
        return value

    @model_validator(mode="after")
    def validate_request_bounds(self) -> "LLMRequest[T]":
        combined = sum(len(message.content) for message in self.messages)
        if combined > MAX_COMBINED_MESSAGE_CHARS:
            raise ValueError("combined message content exceeds the gateway limit")
        schema_size = sum(
            _json_size(tool.input_schema, label="tool schema", maximum=MAX_TOOL_SCHEMA_CHARS)
            for tool in self.tools
        )
        if schema_size > MAX_TOOL_SCHEMA_CHARS:
            raise ValueError("combined tool schemas exceed the gateway limit")
        if self.response_schema is not None and not issubclass(self.response_schema, BaseModel):
            raise ValueError("response schema must be a Pydantic model")
        return self


class LLMUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated: bool = False


class LLMErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80)
    retry_same_provider: bool
    fallback_next_provider: bool
    log_level: str = Field(pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    safe_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("safe_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        _json_size(value, label="safe error metadata", maximum=2_000)
        _reject_sensitive_keys(value, label="safe error metadata")
        return value


class LLMAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=40)
    model: str | None = Field(default=None, max_length=120)
    attempt_number: int = Field(ge=1)
    latency_ms: int = Field(ge=0)
    outcome: LLMAttemptOutcome
    normalized_error_code: str | None = Field(default=None, max_length=80)
    parse_status: LLMParseStatus
    usage: LLMUsage | None = None


class LLMResult(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")

    content: T | str | None = None
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=120)
    attempts: list[LLMAttempt] = Field(default_factory=list)
    latency_ms: int = Field(ge=0)
    usage: LLMUsage | None = None
    parse_status: LLMParseStatus
    fallback_used: bool = False
    error: LLMErrorInfo | None = None
    tool_calls: list[CanonicalToolCall] = Field(default_factory=list, max_length=MAX_TOOL_COUNT)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderInvocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gateway_request_id: str = Field(min_length=1, max_length=64)
    purpose: LLMPurpose
    messages: list[CanonicalLLMMessage]
    safe_context: dict[str, Any]
    selected_model: str | None = Field(default=None, max_length=120)
    timeout_seconds: float = Field(gt=0, le=60)
    response_schema: dict[str, Any] | None = None
    tools: list[CanonicalToolDefinition] = Field(default_factory=list)
    language: str | None = None
    attempt_number: int = Field(ge=1)


class ProviderInvocationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | dict[str, Any] | None = None
    model: str | None = Field(default=None, max_length=120)
    usage: LLMUsage | None = None
    tool_calls: list[CanonicalToolCall] = Field(default_factory=list, max_length=MAX_TOOL_COUNT)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
