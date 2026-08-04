from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional

from langchain_core.messages import HumanMessage, SystemMessage
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
    LLMFinalOutcome,
    LLMLegacyInvocationEvent,
    LLMShadowComparisonEvent,
    emit_configured_event,
    llm_rollout_context,
    normalize_legacy_error,
    safe_correlation_fields,
)
from backend.ai.security import sanitize_untrusted_text
from backend.core.config import settings
from backend.core.context import ExecutionContext
from backend.ops.logger import logger
from backend.pipeline.channel_output import SAFE_RESPONSE_FALLBACK, sanitize_customer_visible_output
from backend.pipeline.interactive_parser import sanitize_quick_replies


LocalizationProvider = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any] | str]]
LocalizationGatewayFactory = Callable[[], LLMGateway]
LocalizationShadowRecorder = Callable[[Dict[str, Any]], None]

UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
INTERNAL_ID_PATTERN = re.compile(
    r"\b(?:tenant|customer|user|provider|payment|order|booking|service|lead|ticket|reminder|review|trace|"
    r"resource|invoice)_id\b",
    re.IGNORECASE,
)
INTERNAL_WORD_PATTERN = re.compile(
    r"\b(?:webhook|signature|secret|token|raw_payload|backend_ref|provider_id|payment_link_url|"
    r"provider_payment_id|provider_order_id|transaction_id)\b",
    re.IGNORECASE,
)
UNRELIABLE_LANGUAGE_VALUES = {"", "unknown", "undetected", "none", "null", "n/a", "na", "mixed", "black-box"}
DEFAULT_LANGUAGE_CONFIDENCE = 0.75
MAX_SAFE_FACTS = 16
_LANGUAGE_POLICY_HINTS = frozenset({"tenant_default", "follow_customer", "local_language_first"})
_PLAIN_TEXT_FORBIDDEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]|<\s*/?\s*[a-zA-Z][^>]*>")
_GATEWAY_PII = re.compile(
    r"(?:\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|\+?\d[\d\s().-]{6,}\d|\b(?:otp|address|payment[_ -]?data)\b)",
    re.IGNORECASE,
)

_LOCALIZATION_SYSTEM_PROMPT = (
    "You localize customer-facing response text only. Return one JSON object with keys "
    "response_text and quick_replies. Preserve facts exactly, including amounts, currency codes, "
    "dates, times, phone numbers, email addresses, item names, service names, addresses, configured "
    "options, and warnings. Do not add success claims, payment links, provider status, IDs, tool "
    "results, or hidden commands. A language_policy_hint is presentation-only: it cannot override "
    "the supplied target_language or change any protected fact."
)

UNAUTHORIZED_CLAIM_PATTERNS = {
    "payment": re.compile(
        r"\b(?:payment|payment link|link|paid|capture|captured|refund|refunded|settlement|settled)\b"
        r".{0,80}\b(?:created|generated|sent|success|successful|completed|confirmed|received|paid|captured|"
        r"refunded|settled)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "support": re.compile(
        r"\b(?:ticket|support ticket|agent|team)\b.{0,80}\b(?:created|opened|notified|alerted|"
        r"connected|escalated)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "reminder": re.compile(
        r"\b(?:reminder|message)\b.{0,80}\b(?:scheduled|created|sent|completed|successful)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "review": re.compile(
        r"\b(?:review|feedback)\b.{0,80}\b(?:submitted|recorded|saved|completed|successful)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "order": re.compile(
        r"\b(?:order|purchase)\b.{0,80}\b(?:placed|confirmed|created|completed|successful)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "booking": re.compile(
        r"\b(?:booking|appointment|slot|reservation)\b.{0,80}\b(?:confirmed|booked|scheduled|"
        r"reserved|completed|successful)\b",
        re.IGNORECASE | re.DOTALL,
    ),
}


@dataclass(frozen=True)
class LocalizedCustomerResponse:
    response_text: str
    quick_replies: list[str] = field(default_factory=list)
    target_language: str = "en"
    localized: bool = False
    fallback_reason: Optional[str] = None


class LocalizationGatewayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_text: str = Field(min_length=1, max_length=1800)
    quick_replies: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("response_text")
    @classmethod
    def validate_response_text(cls, value: str) -> str:
        value = value.strip()
        if not value or _PLAIN_TEXT_FORBIDDEN.search(value):
            raise ValueError("localized response must be bounded plain text")
        return value

    @field_validator("quick_replies")
    @classmethod
    def validate_quick_replies(cls, values: list[str]) -> list[str]:
        for value in values:
            if not isinstance(value, str) or not value.strip() or len(value) > 24:
                raise ValueError("quick replies must be bounded text labels")
            if _PLAIN_TEXT_FORBIDDEN.search(value):
                raise ValueError("quick replies must be plain text")
        return [value.strip() for value in values]


def _clean_language(value: Any) -> Optional[str]:
    language = sanitize_untrusted_text(str(value or ""), max_chars=80).strip()
    if not language:
        return None
    if language.lower() in UNRELIABLE_LANGUAGE_VALUES:
        return None
    return language


def select_target_language(
    *,
    detected_customer_language: Any = None,
    tenant_language: Any = None,
    confidence: Optional[float] = None,
) -> str:
    detected = _clean_language(detected_customer_language)
    if detected and confidence is not None:
        try:
            if float(confidence) >= DEFAULT_LANGUAGE_CONFIDENCE:
                return detected
        except (TypeError, ValueError):
            pass
    tenant = _clean_language(tenant_language)
    return tenant or "en"


def target_language_for_context(
    context: ExecutionContext,
    *,
    detected_customer_language: Any = None,
    confidence: Optional[float] = None,
) -> str:
    incoming = context.incoming_message or {}
    config = getattr(getattr(context, "config", None), "config_data", {}) or {}
    detected = (
        detected_customer_language
        or incoming.get("detected_customer_language")
        or incoming.get("customer_language")
        or incoming.get("detected_language")
    )
    if confidence is None:
        raw_confidence = incoming.get("detected_language_confidence") or incoming.get("language_confidence")
        try:
            confidence = float(raw_confidence) if raw_confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
    tenant_language = (
        config.get("default_language")
        or config.get("tenant_language")
        or config.get("language")
        or config.get("locale")
    )
    return select_target_language(
        detected_customer_language=detected,
        tenant_language=tenant_language,
        confidence=confidence,
    )


def note_detected_customer_language(
    context: ExecutionContext,
    detected_language: Any,
    *,
    confidence: Optional[float],
) -> None:
    language = _clean_language(detected_language)
    if not language or confidence is None:
        return
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        return
    if confidence_value < DEFAULT_LANGUAGE_CONFIDENCE:
        return
    if context.incoming_message is None:
        context.incoming_message = {}
    context.incoming_message["detected_language"] = language
    context.incoming_message["detected_language_confidence"] = confidence_value


def _safe_text_list(values: Iterable[Any], *, max_items: int = MAX_SAFE_FACTS, max_chars: int = 120) -> list[str]:
    safe: list[str] = []
    for value in values or []:
        text = sanitize_customer_visible_output(str(value or "")[:max_chars]).strip()
        if not text or text == SAFE_RESPONSE_FALLBACK:
            continue
        if UUID_PATTERN.search(text) or INTERNAL_ID_PATTERN.search(text) or INTERNAL_WORD_PATTERN.search(text):
            continue
        safe.append(text)
        if len(safe) >= max_items:
            break
    return safe


def _safe_context_payload(context: Dict[str, Any]) -> Dict[str, Any]:
    safe_facts = context.get("safe_facts") if isinstance(context.get("safe_facts"), dict) else {}
    safe_fact_payload: dict[str, Any] = {}
    for key, value in list(safe_facts.items())[:MAX_SAFE_FACTS]:
        key_text = sanitize_untrusted_text(str(key or ""), max_chars=40)
        if not key_text or INTERNAL_ID_PATTERN.search(key_text) or INTERNAL_WORD_PATTERN.search(key_text):
            continue
        if isinstance(value, (dict, list, tuple, set)):
            continue
        value_text = sanitize_customer_visible_output(str(value or "")[:160]).strip()
        if not value_text or value_text == SAFE_RESPONSE_FALLBACK:
            continue
        if UUID_PATTERN.search(value_text) or INTERNAL_ID_PATTERN.search(value_text) or INTERNAL_WORD_PATTERN.search(value_text):
            continue
        safe_fact_payload[key_text] = value_text
    language_policy_hint = str(context.get("language_policy_hint") or "").strip()
    if language_policy_hint not in _LANGUAGE_POLICY_HINTS:
        language_policy_hint = ""
    return {
        "target_language": _clean_language(context.get("target_language")) or "en",
        "tenant_language": _clean_language(context.get("tenant_language")),
        "detected_customer_language": _clean_language(context.get("detected_customer_language")),
        "workflow": sanitize_untrusted_text(str(context.get("workflow") or "general"), max_chars=40) or "general",
        "response_kind": sanitize_untrusted_text(str(context.get("response_kind") or "unknown"), max_chars=40) or "unknown",
        "safe_facts": safe_fact_payload,
        "required_terms": _safe_text_list(context.get("required_terms") or []),
        "protected_terms": _safe_text_list(context.get("protected_terms") or []),
        "allow_completion_claims": bool(context.get("allow_completion_claims")),
        "language_policy_hint": language_policy_hint,
    }


def _is_english(language: str) -> bool:
    return str(language or "").strip().lower() in {"en", "eng", "english", "en-us", "en-gb"}


async def _llm_localization_provider(payload: Dict[str, Any]) -> Dict[str, Any]:
    from backend.logic.engine.llm import get_llm

    messages = [
        SystemMessage(content=_LOCALIZATION_SYSTEM_PROMPT),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ]
    timeout_seconds = float(getattr(settings, "RESPONSE_LOCALIZATION_TIMEOUT_SECONDS", 4) or 4)
    llm = get_llm()
    response = await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout_seconds)
    content = str(getattr(response, "content", "") or "").strip()
    return json.loads(content)


def _gateway_localization_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Exclude execution context and optional safe_facts from the gateway boundary."""
    def portable_terms(values: Any) -> list[str]:
        return [str(value) for value in list(values or [])[:MAX_SAFE_FACTS] if not _GATEWAY_PII.search(str(value))]

    return {
        "base_text": str(payload.get("base_text") or "")[:1800],
        "quick_replies": list(payload.get("quick_replies") or [])[:10],
        "target_language": str(payload.get("target_language") or "en")[:80],
        "workflow": str(payload.get("workflow") or "general")[:40],
        "response_kind": str(payload.get("response_kind") or "unknown")[:40],
        "required_terms": portable_terms(payload.get("required_terms")),
        "protected_terms": portable_terms(payload.get("protected_terms")),
        "allow_completion_claims": bool(payload.get("allow_completion_claims")),
        "language_policy_hint": str(payload.get("language_policy_hint") or "")[:40],
    }


def _build_localization_gateway_request(payload: Dict[str, Any]) -> LLMRequest[LocalizationGatewayPayload]:
    gateway_payload = _gateway_localization_payload(payload)
    return LLMRequest[LocalizationGatewayPayload](
        purpose=LLMPurpose.LOCALIZATION,
        messages=[
            CanonicalLLMMessage(role="system", content=_LOCALIZATION_SYSTEM_PROMPT),
            CanonicalLLMMessage(
                role="user",
                content=json.dumps(gateway_payload, ensure_ascii=False, separators=(",", ":")),
            ),
        ],
        safe_context={
            "target_language": gateway_payload["target_language"],
            "workflow": gateway_payload["workflow"],
            "response_kind": gateway_payload["response_kind"],
            "quick_reply_count": len(gateway_payload["quick_replies"]),
        },
        response_schema=LocalizationGatewayPayload,
        tools=[],
        timeout_class=LLMTimeoutClass.LOCALIZATION_FAST,
        deterministic_fallback=LLMFallbackContract.ORIGINAL_BACKEND_TEXT,
    )


async def _gateway_localization_provider(
    payload: Dict[str, Any],
    *,
    gateway: LLMGateway | None,
    gateway_factory: LocalizationGatewayFactory | None,
) -> tuple[Dict[str, Any] | None, LLMResult]:
    if gateway is None:
        if gateway_factory is not None:
            gateway = gateway_factory()
        else:
            from backend.ai.gateway.runtime import get_runtime_llm_gateway

            gateway = get_runtime_llm_gateway()
    result = await gateway.invoke(_build_localization_gateway_request(payload))
    if result.error is not None or result.parse_status is not LLMParseStatus.SUCCESS:
        return None, result
    parsed = LocalizationGatewayPayload.model_validate(result.content)
    return parsed.model_dump(), result


def _coerce_provider_output(output: Dict[str, Any] | str) -> tuple[str, list[str]]:
    payload: Any = output
    if isinstance(output, str):
        payload = json.loads(output)
    if not isinstance(payload, dict):
        raise ValueError("localization provider must return an object")
    response_text = str(payload.get("response_text") or "").strip()
    quick_replies = payload.get("quick_replies")
    if not isinstance(quick_replies, list):
        quick_replies = []
    return response_text, [str(option) for option in quick_replies]


def _contains_required_terms(text: str, quick_replies: list[str], terms: list[str]) -> bool:
    haystack = f"{text}\n" + "\n".join(quick_replies)
    return all(term in haystack for term in terms)


def _protected_terms_preserved(base_quick_replies: list[str], localized_quick_replies: list[str], terms: list[str]) -> bool:
    if not terms:
        return True
    for term in terms:
        for index, original in enumerate(base_quick_replies):
            if term in original:
                if index >= len(localized_quick_replies) or term not in localized_quick_replies[index]:
                    return False
    return True


def _has_unauthorized_claim(text: str, quick_replies: list[str], context: Dict[str, Any]) -> bool:
    if context.get("allow_completion_claims"):
        return False
    workflow = str(context.get("workflow") or "").strip().lower()
    response_kind = str(context.get("response_kind") or "").strip().lower()
    if response_kind in {"success", "backend_success", "tool_success"}:
        return False
    candidate = f"{text}\n" + "\n".join(quick_replies)
    pattern = UNAUTHORIZED_CLAIM_PATTERNS.get(workflow)
    return bool(pattern and pattern.search(candidate))


def _invalid_localized_output(
    response_text: str,
    quick_replies: list[str],
    base_quick_replies: list[str],
    context: Dict[str, Any],
) -> Optional[str]:
    if not response_text.strip():
        return "empty_response"
    combined = f"{response_text}\n" + "\n".join(quick_replies)
    if UUID_PATTERN.search(combined):
        return "internal_uuid"
    if INTERNAL_ID_PATTERN.search(combined) or INTERNAL_WORD_PATTERN.search(combined):
        return "internal_identifier"
    if len(quick_replies) != len(base_quick_replies):
        return "quick_reply_count_changed"
    if not _contains_required_terms(response_text, quick_replies, context.get("required_terms") or []):
        return "required_terms_missing"
    if not _protected_terms_preserved(base_quick_replies, quick_replies, context.get("protected_terms") or []):
        return "protected_terms_changed"
    if _has_unauthorized_claim(response_text, quick_replies, context):
        return "unauthorized_claim"
    return None


def _fallback_response(
    base_text: str,
    quick_replies: list[str],
    target_language: str,
    *,
    reason: str | None = None,
) -> LocalizedCustomerResponse:
    return LocalizedCustomerResponse(
        response_text=base_text,
        quick_replies=quick_replies,
        target_language=target_language,
        localized=False,
        fallback_reason=reason,
    )


def _validate_localization_candidate(
    output: Dict[str, Any] | str,
    *,
    base_text: str,
    base_quick_replies: list[str],
    safe_context: Dict[str, Any],
) -> tuple[LocalizedCustomerResponse, Dict[str, bool]]:
    target_language = safe_context["target_language"]
    response_text, localized_quick_replies = _coerce_provider_output(output)
    raw_combined = f"{response_text}\n" + "\n".join(localized_quick_replies)
    if UUID_PATTERN.search(raw_combined):
        return _fallback_response(base_text, base_quick_replies, target_language, reason="internal_uuid"), {
            "fact_validation_passed": False,
            "quick_reply_shape_passed": len(localized_quick_replies) == len(base_quick_replies),
        }
    response_text = sanitize_customer_visible_output(response_text).strip()
    localized_quick_replies = sanitize_quick_replies(localized_quick_replies)
    reason = _invalid_localized_output(response_text, localized_quick_replies, base_quick_replies, safe_context)
    quick_shape = len(localized_quick_replies) == len(base_quick_replies)
    fact_valid = reason not in {
        "internal_uuid",
        "internal_identifier",
        "required_terms_missing",
        "protected_terms_changed",
        "unauthorized_claim",
    }
    if reason:
        return _fallback_response(base_text, base_quick_replies, target_language, reason=reason), {
            "fact_validation_passed": fact_valid,
            "quick_reply_shape_passed": quick_shape,
        }
    return LocalizedCustomerResponse(
        response_text=response_text,
        quick_replies=localized_quick_replies,
        target_language=target_language,
        localized=True,
    ), {"fact_validation_passed": True, "quick_reply_shape_passed": True}


async def _run_legacy_localization(
    payload: Dict[str, Any],
    *,
    provider: LocalizationProvider,
    base_text: str,
    base_quick_replies: list[str],
    safe_context: Dict[str, Any],
    rollout_mode: str,
) -> tuple[LocalizedCustomerResponse, Dict[str, bool]]:
    started = perf_counter()
    try:
        result = _validate_localization_candidate(
            await provider(payload),
            base_text=base_text,
            base_quick_replies=base_quick_replies,
            safe_context=safe_context,
        )
        emit_configured_event(LLMLegacyInvocationEvent(
            purpose=LLMPurpose.LOCALIZATION,
            rollout_mode=rollout_mode,
            latency_ms=int((perf_counter() - started) * 1000),
            outcome=LLMFinalOutcome.SUCCESS,
            **safe_correlation_fields(settings.LLM_TELEMETRY_CORRELATION_KEY),
        ), settings)
        return result
    except asyncio.CancelledError:
        emit_configured_event(LLMLegacyInvocationEvent(
            purpose=LLMPurpose.LOCALIZATION,
            rollout_mode=rollout_mode,
            latency_ms=int((perf_counter() - started) * 1000),
            outcome=LLMFinalOutcome.CANCELLED,
            **safe_correlation_fields(settings.LLM_TELEMETRY_CORRELATION_KEY),
        ), settings)
        raise
    except Exception as exc:
        emit_configured_event(LLMLegacyInvocationEvent(
            purpose=LLMPurpose.LOCALIZATION,
            rollout_mode=rollout_mode,
            latency_ms=int((perf_counter() - started) * 1000),
            outcome=LLMFinalOutcome.ERROR,
            normalized_error_code=normalize_legacy_error(exc),
            **safe_correlation_fields(settings.LLM_TELEMETRY_CORRELATION_KEY),
        ), settings)
        logger.warning(
            "Response localization failed closed",
            extra={"extra_data": {"workflow": safe_context.get("workflow"), "error_type": type(exc).__name__}},
        )
        return _fallback_response(
            base_text,
            base_quick_replies,
            safe_context["target_language"],
            reason="provider_failure",
        ), {"fact_validation_passed": False, "quick_reply_shape_passed": False}


async def _run_gateway_localization(
    payload: Dict[str, Any],
    *,
    gateway: LLMGateway | None,
    gateway_factory: LocalizationGatewayFactory | None,
    base_text: str,
    base_quick_replies: list[str],
    safe_context: Dict[str, Any],
    rollout_mode: str,
) -> tuple[LocalizedCustomerResponse, Dict[str, bool], LLMResult | None]:
    try:
        with llm_rollout_context(rollout_mode):
            output, result = await _gateway_localization_provider(
                payload,
                gateway=gateway,
                gateway_factory=gateway_factory,
            )
        if output is None:
            return _fallback_response(
                base_text,
                base_quick_replies,
                safe_context["target_language"],
                reason="provider_failure",
            ), {"fact_validation_passed": False, "quick_reply_shape_passed": False}, result
        candidate, validation = _validate_localization_candidate(
            output,
            base_text=base_text,
            base_quick_replies=base_quick_replies,
            safe_context=safe_context,
        )
        return candidate, validation, result
    except Exception as exc:
        logger.warning(
            "Gateway response localization failed closed",
            extra={"extra_data": {"workflow": safe_context.get("workflow"), "error_type": type(exc).__name__}},
        )
        return _fallback_response(
            base_text,
            base_quick_replies,
            safe_context["target_language"],
            reason="provider_failure",
        ), {"fact_validation_passed": False, "quick_reply_shape_passed": False}, None


def _record_shadow_comparison(
    legacy: LocalizedCustomerResponse,
    gateway_candidate: LocalizedCustomerResponse,
    gateway_result: LLMResult | None,
    validation: Dict[str, bool],
    recorder: LocalizationShadowRecorder | None,
) -> None:
    metadata = {
        "legacy_success": legacy.localized,
        "gateway_success": gateway_candidate.localized,
        "gateway_provider": gateway_result.provider if gateway_result else None,
        "gateway_fallback_used": bool(gateway_result and gateway_result.fallback_used),
        "parse_status": gateway_result.parse_status.value if gateway_result else "failed",
        "fact_validation_passed": validation["fact_validation_passed"],
        "quick_reply_shape_passed": validation["quick_reply_shape_passed"],
        "response_hash_equal": hashlib.sha256(legacy.response_text.encode("utf-8")).digest()
        == hashlib.sha256(gateway_candidate.response_text.encode("utf-8")).digest(),
        "quick_reply_count_equal": len(legacy.quick_replies) == len(gateway_candidate.quick_replies),
    }
    try:
        if recorder is not None:
            recorder(metadata)
        else:
            if not validation["fact_validation_passed"]:
                caller_validation = LLMCallerValidationOutcome.REJECTED_PROTECTED_TERM
            elif not validation["quick_reply_shape_passed"]:
                caller_validation = LLMCallerValidationOutcome.REJECTED_QUICK_REPLY_SHAPE
            else:
                caller_validation = LLMCallerValidationOutcome.ACCEPTED
            emit_configured_event(LLMShadowComparisonEvent(
                purpose=LLMPurpose.LOCALIZATION,
                candidate_valid=gateway_candidate.localized,
                response_hash_equal=metadata["response_hash_equal"],
                quick_reply_count_equal=metadata["quick_reply_count_equal"],
                required_terms_preserved=validation["fact_validation_passed"],
                protected_terms_preserved=validation["fact_validation_passed"],
                caller_validation=caller_validation,
                **safe_correlation_fields(settings.LLM_TELEMETRY_CORRELATION_KEY),
            ), settings)
    except Exception:
        return None


async def localize_customer_response(
    base_text: str,
    quick_replies: list[str],
    response_context: Dict[str, Any],
    *,
    provider: Optional[LocalizationProvider] = None,
    gateway: LLMGateway | None = None,
    gateway_factory: LocalizationGatewayFactory | None = None,
    shadow_recorder: LocalizationShadowRecorder | None = None,
) -> LocalizedCustomerResponse:
    safe_base_text = sanitize_customer_visible_output(str(base_text or "")).strip()
    if not safe_base_text or safe_base_text == SAFE_RESPONSE_FALLBACK:
        safe_base_text = str(base_text or "").strip()
    safe_quick_replies = sanitize_quick_replies([str(option) for option in (quick_replies or [])])
    safe_context = _safe_context_payload(response_context or {})
    target_language = safe_context["target_language"]
    fallback = _fallback_response(safe_base_text, safe_quick_replies, target_language)
    if not safe_base_text or _is_english(target_language):
        return fallback

    payload = {
        "base_text": safe_base_text[:1800],
        "quick_replies": safe_quick_replies,
        **safe_context,
    }
    legacy_provider = provider or _llm_localization_provider
    if not settings.LLM_GATEWAY_ENABLED:
        legacy, _ = await _run_legacy_localization(
            payload,
            provider=legacy_provider,
            base_text=safe_base_text,
            base_quick_replies=safe_quick_replies,
            safe_context=safe_context,
            rollout_mode="legacy",
        )
        return legacy

    if settings.LLM_GATEWAY_SHADOW_MODE:
        legacy, _ = await _run_legacy_localization(
            payload,
            provider=legacy_provider,
            base_text=safe_base_text,
            base_quick_replies=safe_quick_replies,
            safe_context=safe_context,
            rollout_mode="shadow",
        )
        gateway_candidate, validation, gateway_result = await _run_gateway_localization(
            payload,
            gateway=gateway,
            gateway_factory=gateway_factory,
            base_text=safe_base_text,
            base_quick_replies=safe_quick_replies,
            safe_context=safe_context,
            rollout_mode="shadow",
        )
        _record_shadow_comparison(legacy, gateway_candidate, gateway_result, validation, shadow_recorder)
        return legacy

    gateway_candidate, _, _ = await _run_gateway_localization(
        payload,
        gateway=gateway,
        gateway_factory=gateway_factory,
        base_text=safe_base_text,
        base_quick_replies=safe_quick_replies,
        safe_context=safe_context,
        rollout_mode="authoritative",
    )
    return gateway_candidate
