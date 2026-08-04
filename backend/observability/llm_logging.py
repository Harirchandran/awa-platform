"""Strict, content-free logging for legacy LLM and provider paths."""
from __future__ import annotations

import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:/-]{1,120}$")
SafeErrorCode = Literal[
    "rate_limit", "timeout", "network", "provider_error", "provider_unavailable",
    "authentication", "configuration", "invalid_model", "invalid_request",
    "all_providers_failed", "unexpected",
]


class PromptSectionCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform_rules_chars: int = Field(ge=0)
    tenant_config_chars: int = Field(ge=0)
    history_chars: int = Field(ge=0)
    agentic_history_chars: int = Field(ge=0)
    rag_chars: int = Field(ge=0)
    tools_chars: int = Field(ge=0)
    ledger_chars: int = Field(ge=0)


class SafeLegacyLLMLogRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    event: Literal[
        "legacy_provider_mode", "legacy_provider_invocation", "legacy_provider_failure",
        "legacy_fallback_attempt", "legacy_fallback_success", "legacy_fallback_failure",
        "legacy_fallback_exhausted", "legacy_core_model_call",
        "legacy_core_provider_unavailable", "legacy_core_inference_failure",
    ]
    purpose: Literal[
        "localization", "field_extraction", "pending_action_decision", "tool_selection",
        "knowledge_answer", "response_composition", "routing", "unknown",
    ] = "unknown"
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=120)
    error_code: SafeErrorCode | None = None
    status_category: Literal["4xx", "5xx"] | None = None
    provider_request_id: str | None = Field(default=None, max_length=120)
    attempt_number: int | None = Field(default=None, ge=1, le=10)
    latency_ms: int | None = Field(default=None, ge=0)
    fallback_provider: str | None = Field(default=None, max_length=40)
    outcome: Literal["started", "success", "error", "mock", "unavailable"]
    trace_id: str | None = Field(default=None, max_length=120)
    tenant_correlation: str | None = Field(default=None, max_length=80)
    conversation_correlation: str | None = Field(default=None, max_length=80)
    message_count: int | None = Field(default=None, ge=0, le=64)
    tool_call_count: int | None = Field(default=None, ge=0, le=64)

    @field_validator("provider", "model", "provider_request_id", "fallback_provider", "trace_id")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER.fullmatch(value):
            raise ValueError("legacy LLM log identifier contains unsupported characters")
        return value

    @field_validator("tenant_correlation", "conversation_correlation")
    @classmethod
    def validate_correlation(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"hmac-sha256:[0-9a-f]{64}", value):
            raise ValueError("legacy LLM correlation must be a keyed HMAC pseudonym")
        return value


class SafeLegacyLLMTraceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    event: Literal["legacy_llm_input_trace"] = "legacy_llm_input_trace"
    trace_id: str = Field(min_length=1, max_length=120)
    purpose: Literal[
        "localization", "field_extraction", "pending_action_decision", "tool_selection",
        "knowledge_answer", "response_composition", "routing", "unknown",
    ] = "unknown"
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=120)
    integration_mode: Literal["mock", "test", "live", "real"] | None = None
    is_fake_provider: bool
    prompt_sections: PromptSectionCounts
    total_prompt_chars: int = Field(ge=0)
    approximate_input_tokens: int = Field(ge=0)
    tool_count: int = Field(ge=0, le=64)
    history_message_count: int = Field(ge=0, le=64)
    agentic_history_count: int = Field(ge=0, le=64)
    rag_chunk_count: int = Field(ge=0, le=64)
    catalog_item_count: int = Field(ge=0, le=10000)
    has_pending_action: bool
    quick_reply_count: int | None = Field(default=None, ge=0, le=20)
    direct_path_bypassed_llm: bool
    scoped_tools_used: bool
    unscoped_tool_count: int = Field(ge=0, le=64)
    scoped_tool_count: int = Field(ge=0, le=64)
    rag_skipped: bool
    rag_skip_reason: Literal[
        "greeting", "thanks_or_goodbye", "deterministic_direct",
        "transactional_field_collection", "protected_pending_action",
        "post_tool_success", "other",
    ] | None = None
    latency_ms: int = Field(ge=0)
    fallback_used: bool
    error_code: SafeErrorCode | None = None
    final_output_type: Literal["text", "tool_call", "safe_error", "empty"]
    tenant_correlation: str | None = Field(default=None, max_length=80)
    conversation_correlation: str | None = Field(default=None, max_length=80)

    @field_validator("provider", "model", "trace_id")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER.fullmatch(value):
            raise ValueError("legacy LLM trace identifier contains unsupported characters")
        return value

    @field_validator("tenant_correlation", "conversation_correlation")
    @classmethod
    def validate_correlation(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"hmac-sha256:[0-9a-f]{64}", value):
            raise ValueError("legacy LLM correlation must be a keyed HMAC pseudonym")
        return value


def safe_identifier(value: object, *, maximum: int = 120) -> str | None:
    rendered = str(value or "").strip()[:maximum]
    return rendered if rendered and _IDENTIFIER.fullmatch(rendered) else None


def safe_status_category(status_code: object) -> Literal["4xx", "5xx"] | None:
    if isinstance(status_code, int) and 400 <= status_code <= 499:
        return "4xx"
    if isinstance(status_code, int) and 500 <= status_code <= 599:
        return "5xx"
    return None


def safe_llm_error_code(
    *, error_type: object = None, exception_type: object = None, status_code: object = None
) -> SafeErrorCode:
    evidence = f"{str(error_type or '').lower()} {str(exception_type or '').lower()}"
    if status_code == 429 or "rate" in evidence and "limit" in evidence or "resourceexhausted" in evidence:
        return "rate_limit"
    if status_code == 408 or "timeout" in evidence or "deadline" in evidence:
        return "timeout"
    if status_code in {401, 403} or "auth" in evidence or "permission" in evidence:
        return "authentication"
    if "config" in evidence or "missing" in evidence:
        return "configuration"
    if "network" in evidence or "connect" in evidence:
        return "network"
    if "unavailable" in evidence:
        return "provider_unavailable"
    if "all_fallback" in evidence or "all_providers" in evidence:
        return "all_providers_failed"
    if "provider" in evidence or isinstance(status_code, int):
        return "provider_error"
    return "unexpected"


def current_safe_trace_id() -> str:
    try:
        from backend.ops.logger import _trace_id_ctx

        current = safe_identifier(_trace_id_ctx.get())
        if current and current != "unknown":
            return current
    except Exception:
        pass
    return uuid4().hex


def _correlations() -> dict[str, str | None]:
    try:
        from backend.ai.gateway.telemetry import safe_correlation_fields
        from backend.core.config import settings

        return safe_correlation_fields(settings.LLM_TELEMETRY_CORRELATION_KEY)
    except Exception:
        return {"trace_id": None, "tenant_correlation": None, "conversation_correlation": None}


def emit_safe_llm_log(*, level: Literal["INFO", "WARNING", "ERROR"] = "INFO", **fields) -> None:
    """Validate then emit one safe record; logging failure is deliberately ignored."""
    try:
        correlations = _correlations()
        fields.setdefault("trace_id", correlations["trace_id"] or current_safe_trace_id())
        fields.setdefault("tenant_correlation", correlations["tenant_correlation"])
        fields.setdefault("conversation_correlation", correlations["conversation_correlation"])
        record = SafeLegacyLLMLogRecord(**fields)
        from backend.ops.logger import logger

        logger.log(
            {"INFO": 20, "WARNING": 30, "ERROR": 40}[level],
            record.event,
            extra={"extra_data": record.model_dump(mode="json"), "llm_safe_event": True},
        )
    except Exception:
        return None


def emit_legacy_llm_trace(fields: dict) -> None:
    try:
        correlations = _correlations()
        payload = dict(fields)
        payload.setdefault("tenant_correlation", correlations["tenant_correlation"])
        payload.setdefault("conversation_correlation", correlations["conversation_correlation"])
        record = SafeLegacyLLMTraceRecord.model_validate(payload)
        from backend.ops.logger import logger

        logger.info(
            "LLM input trace",
            extra={"extra_data": record.model_dump(mode="json"), "llm_safe_event": True},
        )
    except Exception:
        return None


__all__ = [
    "PromptSectionCounts", "SafeLegacyLLMLogRecord", "SafeLegacyLLMTraceRecord", "current_safe_trace_id",
    "emit_legacy_llm_trace", "emit_safe_llm_log", "safe_identifier", "safe_llm_error_code",
    "safe_status_category",
]
