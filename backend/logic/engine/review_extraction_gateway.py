"""Narrow gateway boundary for review field extraction."""
from __future__ import annotations

import json
from enum import Enum
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.ai.gateway.gateway import LLMGateway
from backend.ai.gateway.models import (
    CanonicalLLMMessage,
    LLMFallbackContract,
    LLMParseStatus,
    LLMPurpose,
    LLMRequest,
    LLMResult,
    LLMTimeoutClass,
)
from backend.ai.gateway.telemetry import (
    LLMCallerValidationOutcome,
    LLMShadowComparisonEvent,
    emit_configured_event,
    llm_rollout_context,
    safe_correlation_fields,
)
from backend.core.config import Settings, settings


class FieldExtractionGatewayMode(str, Enum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    AUTHORITATIVE = "authoritative"


class ReviewGatewayFieldUpdates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: int | None = Field(default=None, ge=1, le=5)
    feedback: str | None = Field(default=None, min_length=1, max_length=2_000)
    customer_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, min_length=1, max_length=80)
    email: str | None = Field(default=None, min_length=1, max_length=320)
    order_reference: str | None = Field(default=None, min_length=1, max_length=200)
    service_name: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, min_length=1, max_length=2_000)


class ReviewGatewayFieldConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: float | None = Field(default=None, ge=0.0, le=1.0)
    feedback: float | None = Field(default=None, ge=0.0, le=1.0)
    customer_name: float | None = Field(default=None, ge=0.0, le=1.0)
    phone: float | None = Field(default=None, ge=0.0, le=1.0)
    email: float | None = Field(default=None, ge=0.0, le=1.0)
    order_reference: float | None = Field(default=None, ge=0.0, le=1.0)
    service_name: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: float | None = Field(default=None, ge=0.0, le=1.0)


ReviewFieldName = Literal[
    "rating", "feedback", "customer_name", "phone", "email",
    "order_reference", "service_name", "notes",
]


class ReviewGatewayPayload(BaseModel):
    """Strict semantic evidence only; it grants no workflow or persistence authority."""

    model_config = ConfigDict(extra="forbid")

    detected_language: str | None = Field(default=None, min_length=1, max_length=80)
    workflow: Literal["review", "unknown"] = "unknown"
    workflow_action: Literal[
        "continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"
    ] = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    field_updates: ReviewGatewayFieldUpdates = Field(default_factory=ReviewGatewayFieldUpdates)
    field_confidence: ReviewGatewayFieldConfidence = Field(default_factory=ReviewGatewayFieldConfidence)
    clarification_needed: bool = True
    clarification_fields: list[ReviewFieldName] = Field(default_factory=list, max_length=8)
    safe_response_hint: str | None = Field(default=None, min_length=1, max_length=300)


_SYSTEM_PROMPT = (
    "Extract semantic evidence from one current customer turn for an active review workflow. "
    "Return only the response schema. Do not invent values. A rating must have explicit numeric "
    "evidence and be one of the allowed choices. General praise or sentiment is not a numeric "
    "rating. Null means no evidence and must not clear an existing value. Never claim submission, "
    "persistence, success, or invoke a tool."
)


def resolve_review_gateway_mode(settings: Settings) -> FieldExtractionGatewayMode:
    if not settings.LLM_GATEWAY_ENABLED:
        return FieldExtractionGatewayMode.LEGACY
    if "review" not in settings.llm_gateway_field_extraction_workflows:
        return FieldExtractionGatewayMode.LEGACY
    return FieldExtractionGatewayMode(settings.LLM_GATEWAY_FIELD_EXTRACTION_MODE)


def build_review_gateway_request(semantic_input: dict[str, Any]) -> LLMRequest[ReviewGatewayPayload]:
    return LLMRequest[ReviewGatewayPayload](
        purpose=LLMPurpose.FIELD_EXTRACTION,
        messages=[
            CanonicalLLMMessage(role="system", content=_SYSTEM_PROMPT),
            CanonicalLLMMessage(
                role="user",
                content=json.dumps(semantic_input, ensure_ascii=False, separators=(",", ":")),
            ),
        ],
        safe_context={
            "workflow": "review",
            "missing_field_names": list(semantic_input.get("missing_field_names") or [])[:8],
            "collected_field_names": list(semantic_input.get("collected_field_names") or [])[:8],
            "allowed_rating_choices": [1, 2, 3, 4, 5],
        },
        response_schema=ReviewGatewayPayload,
        tools=[],
        language=semantic_input.get("language") or None,
        timeout_class=LLMTimeoutClass.STRUCTURED_STANDARD,
        deterministic_fallback=LLMFallbackContract.PRESERVE_STATE,
    )


async def invoke_review_gateway(
    semantic_input: dict[str, Any],
    *,
    gateway: LLMGateway | None,
) -> tuple[ReviewGatewayPayload | None, LLMResult | None]:
    if gateway is None:
        from backend.ai.gateway.runtime import get_runtime_llm_gateway

        gateway = get_runtime_llm_gateway()
    mode = resolve_review_gateway_mode(settings)
    rollout_mode = "shadow" if mode is FieldExtractionGatewayMode.SHADOW else "authoritative"
    with llm_rollout_context(rollout_mode):
        result = await gateway.invoke(build_review_gateway_request(semantic_input))
    if result.error is not None or result.parse_status is not LLMParseStatus.SUCCESS:
        return None, result
    return ReviewGatewayPayload.model_validate(result.content), result


def gateway_payload_to_legacy_dict(payload: ReviewGatewayPayload) -> dict[str, Any]:
    value = payload.model_dump()
    value["field_updates"] = payload.field_updates.model_dump(exclude_none=True)
    value["field_confidence"] = payload.field_confidence.model_dump(exclude_none=True)
    return value


def _confidence_bucket(value: float) -> str:
    if value < 0.5:
        return "low"
    if value < 0.8:
        return "medium"
    return "high"


def record_review_shadow_comparison(
    legacy: Any,
    gateway_candidate: Any | None,
    result: LLMResult | None,
    recorder: Callable[[dict[str, Any]], None] | None,
) -> None:
    legacy_fields = sorted(str(key) for key in getattr(legacy, "field_updates", {}))
    gateway_fields = sorted(str(key) for key in getattr(gateway_candidate, "field_updates", {}))
    metadata = {
        "legacy_success": getattr(legacy, "workflow", "unknown") == "review",
        "gateway_success": gateway_candidate is not None and getattr(gateway_candidate, "workflow", "unknown") == "review",
        "gateway_provider": result.provider if result else None,
        "gateway_fallback_used": bool(result and result.fallback_used),
        "gateway_parse_status": result.parse_status.value if result else "failed",
        "legacy_field_names": legacy_fields,
        "gateway_field_names": gateway_fields,
        "rating_presence_equal": ("rating" in legacy_fields) == ("rating" in gateway_fields),
        "comment_presence_equal": ("feedback" in legacy_fields) == ("feedback" in gateway_fields),
        "clarification_equal": bool(getattr(legacy, "clarification_needed", True))
        == bool(getattr(gateway_candidate, "clarification_needed", True)),
        "confidence_bucket_equal": _confidence_bucket(float(getattr(legacy, "confidence", 0.0)))
        == _confidence_bucket(float(getattr(gateway_candidate, "confidence", 0.0))),
    }
    try:
        if recorder is not None:
            recorder(metadata)
        else:
            accepted = bool(metadata["gateway_success"])
            emit_configured_event(LLMShadowComparisonEvent(
                purpose=LLMPurpose.FIELD_EXTRACTION,
                workflow_category="review",
                field_name_set_equal=(
                    metadata["rating_presence_equal"]
                    and metadata["comment_presence_equal"]
                ),
                confidence_bucket_equal=metadata["confidence_bucket_equal"],
                candidate_valid=accepted,
                caller_validation=(
                    LLMCallerValidationOutcome.ACCEPTED
                    if accepted else LLMCallerValidationOutcome.REJECTED_BUSINESS_VALIDATION
                ),
                **safe_correlation_fields(settings.LLM_TELEMETRY_CORRELATION_KEY),
            ), settings)
    except Exception:
        return None


__all__ = [
    "FieldExtractionGatewayMode",
    "ReviewGatewayPayload",
    "build_review_gateway_request",
    "gateway_payload_to_legacy_dict",
    "invoke_review_gateway",
    "record_review_shadow_comparison",
    "resolve_review_gateway_mode",
]
