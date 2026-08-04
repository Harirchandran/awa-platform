"""Privacy-safe, fail-open observability contracts for all LLM inference paths."""
from __future__ import annotations

import hashlib
import hmac
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Annotated, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from backend.ai.gateway.models import LLMPurpose


_CORRELATION = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:/-]{1,120}$")
SafeProvider = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.:/-]+$")]
SafeModel = Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:/-]+$")]
_rollout_mode: ContextVar[str] = ContextVar("llm_rollout_mode", default="authoritative")
_inference_path: ContextVar[str] = ContextVar("llm_inference_path", default="gateway")


class LLMTelemetryEventType(str, Enum):
    REQUEST_STARTED = "llm_request_started"
    ATTEMPT_COMPLETED = "llm_attempt_completed"
    FALLBACK_TRANSITION = "llm_fallback_transition"
    REQUEST_COMPLETED = "llm_request_completed"
    SHADOW_COMPARISON = "llm_shadow_comparison"
    LEGACY_INVOCATION = "llm_legacy_invocation"
    POLICY_REJECTION = "llm_policy_rejection"


class LLMTelemetryParseStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    SUCCESS = "success"
    MALFORMED_JSON = "malformed_json"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    OVERSIZED_OUTPUT = "oversized_output"
    TOOL_PROTOCOL_FAILED = "tool_protocol_failed"


class LLMFinalOutcome(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class LLMNormalizedErrorCode(str, Enum):
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    NETWORK = "network"
    PROVIDER_5XX = "provider_server_error"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    AUTHENTICATION = "authentication"
    INVALID_MODEL = "invalid_model"
    INVALID_REQUEST = "invalid_request"
    SAFETY_REJECTION = "safety_rejection"
    CONTEXT_LENGTH = "context_length"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_PROTOCOL = "tool_protocol"
    ALL_PROVIDERS_FAILED = "all_providers_failed"
    CANCELLED = "cancelled"
    LLM_ERROR = "llm_error"


class LLMCallerValidationOutcome(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    ACCEPTED = "accepted"
    REJECTED_BUSINESS_VALIDATION = "rejected_business_validation"
    REJECTED_TRUTH_CLAIM = "rejected_truth_claim"
    REJECTED_PROTECTED_TERM = "rejected_protected_term"
    REJECTED_NUMERIC_TOKEN = "rejected_numeric_token"
    REJECTED_QUICK_REPLY_SHAPE = "rejected_quick_reply_shape"
    REJECTED_STALE_SNAPSHOT = "rejected_stale_snapshot"
    REJECTED_UNKNOWN_FIELD = "rejected_unknown_field"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    event_type: LLMTelemetryEventType
    occurred_at: datetime = Field(default_factory=_utcnow)
    gateway_request_id: str | None = Field(default=None, min_length=1, max_length=64)
    trace_id: str | None = Field(default=None, min_length=1, max_length=120)
    purpose: LLMPurpose
    tenant_correlation: str | None = Field(default=None, max_length=80)
    conversation_correlation: str | None = Field(default=None, max_length=80)

    @field_validator("tenant_correlation", "conversation_correlation")
    @classmethod
    def validate_correlation(cls, value: str | None) -> str | None:
        if value is not None and not _CORRELATION.fullmatch(value):
            raise ValueError("telemetry correlations must be keyed HMAC pseudonyms")
        return value

    @field_validator("trace_id")
    @classmethod
    def validate_trace(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("trace identifier contains unsupported characters")
        return value

    @field_validator("gateway_request_id")
    @classmethod
    def validate_request_id(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("gateway request identifier contains unsupported characters")
        return value


class LLMRequestStartedEvent(_EventBase):
    event_type: Literal[LLMTelemetryEventType.REQUEST_STARTED] = LLMTelemetryEventType.REQUEST_STARTED
    rollout_mode: Literal["legacy", "shadow", "authoritative"]
    path: Literal["gateway"] = "gateway"
    timeout_class: str = Field(max_length=40)
    tools_allowed: bool
    tool_count: int = Field(ge=0, le=32)
    history_allowed: bool
    message_count: int = Field(ge=1, le=32)
    side_effect_boundary_declared: bool
    started_at: datetime


class LLMAttemptCompletedEvent(_EventBase):
    event_type: Literal[LLMTelemetryEventType.ATTEMPT_COMPLETED] = LLMTelemetryEventType.ATTEMPT_COMPLETED
    provider: SafeProvider
    model: SafeModel | None = None
    attempt_number: int = Field(ge=1, le=10)
    latency_ms: int = Field(ge=0)
    outcome: Literal["success", "error"]
    normalized_error_code: LLMNormalizedErrorCode | None = None
    parse_status: LLMTelemetryParseStatus
    input_token_count: int | None = Field(default=None, ge=0)
    output_token_count: int | None = Field(default=None, ge=0)
    total_token_count: int | None = Field(default=None, ge=0)
    usage_estimated: bool = False
    tool_call_count: int = Field(default=0, ge=0, le=32)
    fallback_next_provider: SafeProvider | None = None


class LLMFallbackTransitionEvent(_EventBase):
    event_type: Literal[LLMTelemetryEventType.FALLBACK_TRANSITION] = LLMTelemetryEventType.FALLBACK_TRANSITION
    attempt_number: int = Field(ge=1, le=10)
    from_provider: SafeProvider
    to_provider: SafeProvider
    normalized_reason: LLMNormalizedErrorCode


class LLMRequestCompletedEvent(_EventBase):
    event_type: Literal[LLMTelemetryEventType.REQUEST_COMPLETED] = LLMTelemetryEventType.REQUEST_COMPLETED
    rollout_mode: Literal["legacy", "shadow", "authoritative"]
    path: Literal["gateway"] = "gateway"
    completed_at: datetime
    total_latency_ms: int = Field(ge=0)
    selected_provider: SafeProvider | None = None
    selected_model: SafeModel | None = None
    attempt_count: int = Field(ge=0, le=10)
    fallback_used: bool
    final_outcome: LLMFinalOutcome
    final_error_code: LLMNormalizedErrorCode | None = None
    parse_status: LLMTelemetryParseStatus
    side_effect_boundary_declared: bool


class LLMShadowComparisonEvent(_EventBase):
    event_type: Literal[LLMTelemetryEventType.SHADOW_COMPARISON] = LLMTelemetryEventType.SHADOW_COMPARISON
    rollout_mode: Literal["shadow"] = "shadow"
    path: Literal["gateway"] = "gateway"
    workflow_category: Literal["review", "support", "lead_capture", "reminder", "booking", "order", "payment"] | None = None
    legacy_result_kind: Literal["text", "tool_call", "empty", "error"] | None = None
    gateway_result_kind: Literal["text", "tool_call", "empty", "error"] | None = None
    result_kind_equal: bool | None = None
    decision_equal: bool | None = None
    field_name_set_equal: bool | None = None
    tool_name_set_equal: bool | None = None
    tool_count_equal: bool | None = None
    argument_key_set_equal: bool | None = None
    quick_reply_count_equal: bool | None = None
    required_terms_preserved: bool | None = None
    protected_terms_preserved: bool | None = None
    candidate_valid: bool | None = None
    response_hash_equal: bool | None = None
    confidence_bucket_equal: bool | None = None
    caller_validation: LLMCallerValidationOutcome = LLMCallerValidationOutcome.NOT_APPLICABLE


class LLMLegacyInvocationEvent(_EventBase):
    event_type: Literal[LLMTelemetryEventType.LEGACY_INVOCATION] = LLMTelemetryEventType.LEGACY_INVOCATION
    rollout_mode: Literal["legacy", "shadow"]
    path: Literal["legacy"] = "legacy"
    provider: SafeProvider | None = None
    model: SafeModel | None = None
    latency_ms: int = Field(ge=0)
    outcome: LLMFinalOutcome
    normalized_error_code: LLMNormalizedErrorCode | None = None
    input_token_count: int | None = Field(default=None, ge=0)
    output_token_count: int | None = Field(default=None, ge=0)
    total_token_count: int | None = Field(default=None, ge=0)
    usage_estimated: bool = False


class LLMPolicyRejectionEvent(_EventBase):
    event_type: Literal[LLMTelemetryEventType.POLICY_REJECTION] = LLMTelemetryEventType.POLICY_REJECTION
    rollout_mode: Literal["legacy", "shadow", "authoritative"]
    path: Literal["gateway"] = "gateway"
    normalized_error_code: Literal["invalid_request", "timeout_policy_mismatch"]


LLMTelemetryEvent = Annotated[
    LLMRequestStartedEvent
    | LLMAttemptCompletedEvent
    | LLMFallbackTransitionEvent
    | LLMRequestCompletedEvent
    | LLMShadowComparisonEvent
    | LLMLegacyInvocationEvent
    | LLMPolicyRejectionEvent,
    Field(discriminator="event_type"),
]
# Backwards-compatible import name; the contract is now a strict event union.
LLMGatewayTelemetryEvent = LLMTelemetryEvent


class LLMGatewayTelemetryRecorder(Protocol):
    def record(self, event: LLMTelemetryEvent) -> None: ...


class NoOpLLMGatewayTelemetryRecorder:
    def record(self, event: LLMTelemetryEvent) -> None:
        return None


class InMemoryLLMTelemetryRecorder:
    """Test-local recorder. Callers own the instance and its lifetime."""

    def __init__(self) -> None:
        self.events: list[LLMTelemetryEvent] = []

    def record(self, event: LLMTelemetryEvent) -> None:
        self.events.append(event)


class StructuredLogLLMTelemetryRecorder:
    """One bounded logger record per already-validated event."""

    def record(self, event: LLMTelemetryEvent) -> None:
        try:
            from backend.ops.logger import logger

            logger.info(
                event.event_type.value,
                extra={
                    "extra_data": event.model_dump(mode="json"),
                    "llm_safe_event": True,
                },
            )
        except Exception:
            # Avoid recursive logging: exporter failure must never affect inference.
            return None


def pseudonymous_correlation(value: str, *, key: str | SecretStr) -> str:
    """Return a stable keyed pseudonym without retaining the source identifier."""
    secret = key.get_secret_value() if isinstance(key, SecretStr) else str(key)
    if not secret:
        raise ValueError("a non-empty telemetry correlation key is required")
    digest = hmac.new(secret.encode("utf-8"), str(value).encode("utf-8"), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def deterministic_sample(gateway_request_id: str, sample_rate: float) -> bool:
    if sample_rate <= 0.0:
        return False
    if sample_rate >= 1.0:
        return True
    bucket = int.from_bytes(hashlib.sha256(gateway_request_id.encode("ascii")).digest()[:8], "big")
    return bucket / float(2**64) < sample_rate


@contextmanager
def llm_rollout_context(mode: str, *, path: str = "gateway"):
    mode_token = _rollout_mode.set(mode)
    path_token = _inference_path.set(path)
    try:
        yield
    finally:
        _inference_path.reset(path_token)
        _rollout_mode.reset(mode_token)


def current_rollout_mode() -> str:
    value = _rollout_mode.get()
    return value if value in {"legacy", "shadow", "authoritative"} else "authoritative"


def safe_correlation_fields(correlation_key: SecretStr | None) -> dict[str, str | None]:
    trace_id: str | None = None
    tenant: str | None = None
    conversation: str | None = None
    try:
        from backend.ops.logger import _trace_id_ctx

        raw_trace = str(_trace_id_ctx.get() or "")
        if raw_trace and raw_trace != "unknown" and _SAFE_IDENTIFIER.fullmatch(raw_trace):
            trace_id = raw_trace
    except Exception:
        pass
    if correlation_key is not None and correlation_key.get_secret_value():
        try:
            from backend.core.context import active_context_var

            context = active_context_var.get()
            if context is not None:
                tenant = pseudonymous_correlation(context.tenant.id, key=correlation_key)
                conversation = pseudonymous_correlation(context.session_data.conversation_id, key=correlation_key)
        except Exception:
            pass
    return {"trace_id": trace_id, "tenant_correlation": tenant, "conversation_correlation": conversation}


def safe_operational_identifier(value: object, *, maximum: int = 120) -> str | None:
    rendered = str(value or "").strip()
    return rendered[:maximum] if rendered and _SAFE_IDENTIFIER.fullmatch(rendered[:maximum]) else None


def normalize_legacy_error(exc: BaseException) -> str:
    """Class-name-only normalization; exception messages are never inspected."""
    name = type(exc).__name__.lower()
    if "cancel" in name:
        return "cancelled"
    if "timeout" in name or "deadline" in name:
        return "timeout"
    if "rate" in name and "limit" in name:
        return "rate_limit"
    if "auth" in name or "permission" in name:
        return "authentication"
    if "connect" in name or "network" in name:
        return "network"
    return "provider_unavailable"


_recorder_lock = Lock()
_configured_recorder: LLMGatewayTelemetryRecorder | None = None


def get_configured_llm_telemetry_recorder(settings) -> LLMGatewayTelemetryRecorder:
    global _configured_recorder
    if _configured_recorder is None:
        with _recorder_lock:
            if _configured_recorder is None:
                _configured_recorder = (
                    StructuredLogLLMTelemetryRecorder()
                    if settings.LLM_TELEMETRY_ENABLED
                    else NoOpLLMGatewayTelemetryRecorder()
                )
    return _configured_recorder


def reset_configured_llm_telemetry_recorder() -> None:
    global _configured_recorder
    with _recorder_lock:
        _configured_recorder = None


def emit_configured_event(event: LLMTelemetryEvent, settings) -> None:
    """Apply the same platform enablement and deterministic sampling to bridge events."""
    if not settings.LLM_TELEMETRY_ENABLED:
        return
    sampling_id = event.gateway_request_id or uuid4().hex
    if not deterministic_sample(sampling_id, settings.LLM_TELEMETRY_SAMPLE_RATE):
        return
    record_safely(get_configured_llm_telemetry_recorder(settings), event)


def record_safely(recorder: LLMGatewayTelemetryRecorder, event: LLMTelemetryEvent) -> None:
    try:
        recorder.record(event)
    except Exception:
        return None


__all__ = [name for name in globals() if name.startswith("LLM") or name in {
    "InMemoryLLMTelemetryRecorder", "NoOpLLMGatewayTelemetryRecorder",
    "StructuredLogLLMTelemetryRecorder", "current_rollout_mode", "deterministic_sample",
    "get_configured_llm_telemetry_recorder", "llm_rollout_context", "pseudonymous_correlation",
    "emit_configured_event", "normalize_legacy_error", "record_safely",
    "reset_configured_llm_telemetry_recorder", "safe_correlation_fields",
    "safe_operational_identifier",
}]
