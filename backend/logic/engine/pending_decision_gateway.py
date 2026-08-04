"""Strict, non-authoritative gateway boundary for pending-action decisions."""
from __future__ import annotations

import json
import math
import re
from enum import Enum
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class PendingDecisionGatewayMode(str, Enum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    AUTHORITATIVE = "authoritative"


PendingDecisionAction = Literal[
    "confirm", "cancel", "change", "continue", "tangent", "unknown"
]
PendingFieldValue = str | int | float | bool | None


class PendingDecisionGatewayPayload(BaseModel):
    """Caller-owned semantic schema with no execution or verification authority."""

    model_config = ConfigDict(extra="forbid")

    action: PendingDecisionAction = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    field_updates: dict[str, PendingFieldValue] = Field(default_factory=dict, max_length=12)
    clarification: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("field_updates")
    @classmethod
    def validate_field_updates(
        cls, values: dict[str, PendingFieldValue]
    ) -> dict[str, PendingFieldValue]:
        result: dict[str, PendingFieldValue] = {}
        for key, value in values.items():
            normalized = str(key or "").strip()
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", normalized):
                raise ValueError("invalid pending-decision field name")
            if isinstance(value, str) and (not value or len(value) > 500):
                raise ValueError("pending-decision field value is not bounded")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if not math.isfinite(float(value)) or abs(float(value)) > 1_000_000_000_000:
                    raise ValueError("pending-decision numeric value is not bounded")
            result[normalized] = value
        return result


def resolve_pending_decision_gateway_mode(settings: Settings) -> PendingDecisionGatewayMode:
    if not settings.LLM_GATEWAY_ENABLED:
        return PendingDecisionGatewayMode.LEGACY
    return PendingDecisionGatewayMode(settings.LLM_GATEWAY_PENDING_DECISION_MODE)


def build_pending_decision_gateway_request(
    semantic_input: dict[str, Any],
) -> LLMRequest[PendingDecisionGatewayPayload]:
    system_prompt = (
        "Classify only the meaning of one current customer-authored turn relative to a pending "
        "action. Return only the response schema. action must be confirm, cancel, change, "
        "continue, tangent, or unknown. field_updates may contain only customer-provided "
        "replacement candidates for the listed changeable fields. This is semantic evidence "
        "only: never authorize confirmation, OTP, execution, tools, persistence, or state changes."
    )
    return LLMRequest[PendingDecisionGatewayPayload](
        purpose=LLMPurpose.PENDING_ACTION_DECISION,
        messages=[
            CanonicalLLMMessage(role="system", content=system_prompt),
            CanonicalLLMMessage(
                role="user",
                content=json.dumps(semantic_input, ensure_ascii=False, separators=(",", ":")),
            ),
        ],
        safe_context={
            "pending_action_category": semantic_input.get("pending_action_category"),
            "changeable_field_names": list(semantic_input.get("changeable_field_names") or [])[:12],
            "confirmation_requested": bool(semantic_input.get("confirmation_requested")),
        },
        response_schema=PendingDecisionGatewayPayload,
        tools=[],
        language=semantic_input.get("language") or None,
        timeout_class=LLMTimeoutClass.SEMANTIC_FAST,
        deterministic_fallback=LLMFallbackContract.CLARIFY,
    )


async def invoke_pending_decision_gateway(
    semantic_input: dict[str, Any],
    *,
    gateway: LLMGateway | None,
) -> tuple[PendingDecisionGatewayPayload | None, LLMResult | None]:
    if gateway is None:
        from backend.ai.gateway.runtime import get_runtime_llm_gateway

        gateway = get_runtime_llm_gateway()
    mode = resolve_pending_decision_gateway_mode(settings)
    rollout_mode = "shadow" if mode is PendingDecisionGatewayMode.SHADOW else "authoritative"
    with llm_rollout_context(rollout_mode):
        result = await gateway.invoke(build_pending_decision_gateway_request(semantic_input))
    if (
        result.error is not None
        or result.parse_status is not LLMParseStatus.SUCCESS
        or result.tool_calls
    ):
        return None, result
    return PendingDecisionGatewayPayload.model_validate(result.content), result


def _confidence_bucket(value: float) -> str:
    if value < 0.5:
        return "low"
    if value < 0.8:
        return "medium"
    return "high"


def record_pending_decision_shadow_comparison(
    legacy: Any,
    gateway_candidate: Any | None,
    result: LLMResult | None,
    recorder: Callable[[dict[str, Any]], None] | None,
) -> None:
    legacy_fields = sorted(str(key) for key in getattr(legacy, "field_updates", {}))
    gateway_fields = sorted(str(key) for key in getattr(gateway_candidate, "field_updates", {}))
    metadata = {
        "legacy_success": legacy is not None,
        "gateway_success": gateway_candidate is not None,
        "gateway_provider": result.provider if result else None,
        "gateway_fallback_used": bool(result and result.fallback_used),
        "gateway_parse_status": result.parse_status.value if result else "failed",
        "legacy_decision": str(getattr(legacy, "action", "unknown")),
        "gateway_decision": str(getattr(gateway_candidate, "action", "unknown")),
        "decision_equal": getattr(legacy, "action", "unknown")
        == getattr(gateway_candidate, "action", "unknown"),
        "clarification_presence_equal": bool(getattr(legacy, "clarification", None))
        == bool(getattr(gateway_candidate, "clarification", None)),
        "confidence_bucket_equal": _confidence_bucket(float(getattr(legacy, "confidence", 0.0)))
        == _confidence_bucket(float(getattr(gateway_candidate, "confidence", 0.0))),
        "field_name_set_equal": legacy_fields == gateway_fields,
    }
    try:
        if recorder is not None:
            recorder(metadata)
        else:
            accepted = bool(metadata["gateway_success"])
            emit_configured_event(LLMShadowComparisonEvent(
                purpose=LLMPurpose.PENDING_ACTION_DECISION,
                decision_equal=metadata["decision_equal"],
                field_name_set_equal=metadata["field_name_set_equal"],
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
    "PendingDecisionGatewayMode",
    "PendingDecisionGatewayPayload",
    "build_pending_decision_gateway_request",
    "invoke_pending_decision_gateway",
    "record_pending_decision_shadow_comparison",
    "resolve_pending_decision_gateway_mode",
]
