"""Bounded wording-only composition for backend-owned customer responses."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from backend.ai.gateway.gateway import LLMGateway
from backend.ai.gateway.models import (
    CanonicalLLMMessage,
    LLMFallbackContract,
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
from backend.ops.logger import logger


class ResponseComposerMode(str, Enum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    AUTHORITATIVE = "authoritative"


class ResponseKind(str, Enum):
    MISSING_FIELD = "missing_field"
    WORKFLOW_RESUME = "workflow_resume"
    CLARIFICATION = "clarification"
    PENDING_CONFIRMATION = "pending_confirmation"
    PENDING_CHANGE = "pending_change"
    PENDING_CANCEL = "pending_cancel"
    TOOL_SUCCESS = "tool_success"
    TOOL_FAILURE = "tool_failure"


@dataclass(frozen=True)
class ResponseKindPolicy:
    eligible: bool
    quick_replies_may_change: bool
    completion_claims_allowed: bool
    failure_claims_allowed: bool
    maximum_length: int = 1800
    fallback: str = "original_backend_text"


RESPONSE_KIND_POLICIES: dict[ResponseKind, ResponseKindPolicy] = {
    ResponseKind.MISSING_FIELD: ResponseKindPolicy(True, False, False, False),
    ResponseKind.WORKFLOW_RESUME: ResponseKindPolicy(True, False, False, False),
    ResponseKind.CLARIFICATION: ResponseKindPolicy(True, False, False, False),
    ResponseKind.PENDING_CONFIRMATION: ResponseKindPolicy(True, False, False, False),
    ResponseKind.PENDING_CHANGE: ResponseKindPolicy(True, False, False, False),
    ResponseKind.PENDING_CANCEL: ResponseKindPolicy(True, False, False, False),
    ResponseKind.TOOL_SUCCESS: ResponseKindPolicy(True, False, True, False),
    ResponseKind.TOOL_FAILURE: ResponseKindPolicy(True, False, False, True),
}


class ResponseCompositionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_text: str = Field(min_length=1, max_length=1800)
    response_kind: ResponseKind
    safe_facts: dict[str, JsonValue] = Field(default_factory=dict)
    quick_replies: list[str] = Field(default_factory=list, max_length=10)
    required_terms: list[str] = Field(default_factory=list, max_length=24)
    protected_terms: list[str] = Field(default_factory=list, max_length=24)
    protected_numeric_tokens: list[str] = Field(default_factory=list, max_length=24)
    allow_completion_claims: bool = False
    allow_failure_claims: bool = False
    target_language: str | None = Field(default=None, max_length=32)

    @field_validator("safe_facts")
    @classmethod
    def validate_safe_facts(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        prohibited = re.compile(
            r"(?:otp|nonce|args?[_-]?hash|tool[_-]?name|api[_-]?key|secret|credential|password|"
            r"cvv|upi[_-]?pin|database|exception|stack|raw[_-]?(?:payload|response)|provider[_-]?id)",
            re.IGNORECASE,
        )

        def inspect(item: JsonValue) -> None:
            if isinstance(item, dict):
                for key, nested in item.items():
                    if prohibited.search(str(key)):
                        raise ValueError("safe facts contain a prohibited internal or sensitive field")
                    inspect(nested)
            elif isinstance(item, list):
                for nested in item:
                    inspect(nested)

        inspect(value)
        if len(json.dumps(value, ensure_ascii=True, sort_keys=True)) > 4_000:
            raise ValueError("safe facts exceed the bounded composition contract")
        return value

    @field_validator("quick_replies", "required_terms", "protected_terms", "protected_numeric_tokens")
    @classmethod
    def validate_bounded_strings(cls, values: list[str]) -> list[str]:
        maximum = 80 if values and all(len(str(value)) <= 80 for value in values) else 240
        cleaned: list[str] = []
        for value in values:
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError("composition terms must be bounded non-empty strings")
            cleaned.append(value.strip())
        return cleaned


_FORBIDDEN_PLAIN_TEXT = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]|<\s*/?\s*[a-zA-Z][^>]*>|\[[^\]]+\]\([^)]+\)"
)
_HIDDEN_CONTROL = re.compile(
    r"\b(?:success|execute|state_updates?|tool_calls?|provider|model)\s*[:=]\s*(?:true|false|\{|\[|[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_NUMBER_TOKEN = re.compile(
    r"(?<!\w)(?:[₹$€£]\s*)?\d[\d,]*(?:\.\d+)?(?:\s*(?:AM|PM|USD|INR|EUR|GBP))?(?!\w)",
    re.IGNORECASE,
)
_REFERENCE_TOKEN = re.compile(r"\b(?=[A-Za-z0-9-]{4,40}\b)(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9-]+\b")
_FAILURE_CLAIM = re.compile(
    r"\b(?:failed|failure|unable|unavailable|couldn['’]?t|could not|not completed|was not saved|wasn['’]?t created)\b",
    re.IGNORECASE,
)
_DEFAULT_COMPLETION_CLAIM = re.compile(
    r"\b(?:booked|confirmed|placed|paid|completed|saved|scheduled|created|successful|done|submitted|recorded|sent)\b",
    re.IGNORECASE,
)
_PROHIBITED_ASSERTION = re.compile(
    r"\b(?:otp\s+verified|tool\s+executed|price\s+changed|total\s+changed|provider\s+changed)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_PROMISE = re.compile(
    r"\b(?:email|sms|whatsapp|team|agent|delivery|discount|refund)\b.{0,60}"
    r"\b(?:sent|call|contact|deliver|applied|issued|notified|guaranteed)\b",
    re.IGNORECASE | re.DOTALL,
)


class ComposedCustomerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_text: str = Field(min_length=1, max_length=1800)
    quick_replies: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("response_text")
    @classmethod
    def validate_response_text(cls, value: str) -> str:
        value = value.strip()
        if not value or _FORBIDDEN_PLAIN_TEXT.search(value) or _HIDDEN_CONTROL.search(value):
            raise ValueError("composed response must be bounded plain text")
        return value

    @field_validator("quick_replies")
    @classmethod
    def validate_quick_replies(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            if not isinstance(value, str) or not value.strip() or len(value) > 24:
                raise ValueError("quick replies must be bounded non-empty strings")
            if _FORBIDDEN_PLAIN_TEXT.search(value) or _HIDDEN_CONTROL.search(value):
                raise ValueError("quick replies must be plain text")
            cleaned.append(value.strip())
        return cleaned


@dataclass(frozen=True)
class CompositionValidation:
    candidate_valid: bool = False
    required_terms_preserved: bool = False
    protected_terms_preserved: bool = False
    completion_claim_valid: bool = False
    failure_claim_valid: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class CustomerResponseComposition:
    response_text: str
    quick_replies: list[str] = field(default_factory=list)
    composed: bool = False
    fallback_reason: str | None = None


CompositionGatewayFactory = Callable[[], LLMGateway]
CompositionShadowRecorder = Callable[[dict[str, Any]], None]
CompletionClaimDetector = Callable[[str], bool]


_SYSTEM_PROMPT = (
    "You reword one backend-owned customer response. Return exactly one JSON object with keys "
    "response_text and quick_replies. Change wording only. Treat base_text, safe_facts, required_terms, "
    "protected_terms, numeric tokens, truth permissions, and quick-reply order as authoritative. Do not "
    "add, remove, or alter facts, actions, availability, prices, totals, payment status, references, "
    "workflow state, confirmation state, OTP state, or quick-reply meaning. Never output HTML, Markdown "
    "links, metadata, control fields, tool calls, state updates, provider names, or model names."
)


def resolve_response_composer_mode(config: Settings) -> ResponseComposerMode:
    if not config.LLM_GATEWAY_ENABLED:
        return ResponseComposerMode.LEGACY
    return ResponseComposerMode(config.LLM_GATEWAY_RESPONSE_COMPOSER_MODE)


def response_kind_policy(kind: ResponseKind | str) -> ResponseKindPolicy | None:
    try:
        return RESPONSE_KIND_POLICIES.get(ResponseKind(kind))
    except (TypeError, ValueError):
        return None


def _request_payload(source: ResponseCompositionInput) -> dict[str, Any]:
    return {
        "base_text": source.base_text,
        "response_kind": source.response_kind.value,
        "safe_facts": source.safe_facts,
        "quick_replies": source.quick_replies,
        "required_terms": source.required_terms,
        "protected_terms": source.protected_terms,
        "protected_numeric_tokens": source.protected_numeric_tokens,
        "allow_completion_claims": source.allow_completion_claims,
        "allow_failure_claims": source.allow_failure_claims,
        "target_language": source.target_language,
    }


def build_response_composer_request(
    source: ResponseCompositionInput,
    *,
    authoritative_side_effects_occurred: bool,
) -> LLMRequest:
    payload = _request_payload(source)
    return LLMRequest(
        purpose=LLMPurpose.RESPONSE_COMPOSITION,
        messages=[
            CanonicalLLMMessage(role="system", content=_SYSTEM_PROMPT),
            CanonicalLLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        ],
        safe_context={
            "response_kind": source.response_kind.value,
            "quick_reply_count": len(source.quick_replies),
            "allow_completion_claims": source.allow_completion_claims,
            "allow_failure_claims": source.allow_failure_claims,
        },
        response_schema=None,
        tools=[],
        language=source.target_language,
        timeout_class=LLMTimeoutClass.COMPOSITION_STANDARD,
        deterministic_fallback=LLMFallbackContract.ORIGINAL_BACKEND_TEXT,
        authoritative_side_effects_occurred=authoritative_side_effects_occurred,
    )


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _terms_preserved(candidate: str, terms: list[str]) -> bool:
    haystack = _normalized(candidate)
    return all(_normalized(term) in haystack for term in terms)


def _number_tokens(value: str) -> set[str]:
    return {_normalized(match.group(0)) for match in _NUMBER_TOKEN.finditer(value)}


def _reference_tokens(value: str) -> set[str]:
    return {_normalized(match.group(0)) for match in _REFERENCE_TOKEN.finditer(value)}


def _parse_candidate(content: Any) -> ComposedCustomerResponse:
    payload = json.loads(content) if isinstance(content, str) else content
    return ComposedCustomerResponse.model_validate(payload)


def validate_composed_candidate(
    source: ResponseCompositionInput,
    candidate: ComposedCustomerResponse,
    *,
    completion_claim_detector: CompletionClaimDetector | None = None,
) -> CompositionValidation:
    policy = response_kind_policy(source.response_kind)
    if policy is None or not policy.eligible:
        return CompositionValidation(reason="ineligible_kind")
    combined = f"{candidate.response_text}\n" + "\n".join(candidate.quick_replies)
    required_ok = _terms_preserved(combined, source.required_terms)
    protected_ok = _terms_preserved(combined, source.protected_terms)
    if not required_ok:
        return CompositionValidation(required_terms_preserved=False, reason="required_terms_missing")
    if not protected_ok:
        return CompositionValidation(required_terms_preserved=True, reason="protected_terms_changed")
    if candidate.quick_replies != source.quick_replies:
        return CompositionValidation(required_terms_preserved=True, protected_terms_preserved=True, reason="quick_replies_changed")
    if any(token not in candidate.response_text for token in source.protected_numeric_tokens):
        return CompositionValidation(required_terms_preserved=True, protected_terms_preserved=True, reason="numeric_token_changed")
    if _UUID.search(combined) and not any(_UUID.search(term) for term in source.protected_terms):
        return CompositionValidation(required_terms_preserved=True, protected_terms_preserved=True, reason="internal_uuid")
    if _PROHIBITED_ASSERTION.search(combined):
        return CompositionValidation(required_terms_preserved=True, protected_terms_preserved=True, reason="prohibited_assertion")
    if _UNSUPPORTED_PROMISE.search(candidate.response_text) and not _UNSUPPORTED_PROMISE.search(source.base_text):
        return CompositionValidation(required_terms_preserved=True, protected_terms_preserved=True, reason="unsupported_promise")
    if not _number_tokens(candidate.response_text).issubset(_number_tokens(source.base_text)):
        return CompositionValidation(required_terms_preserved=True, protected_terms_preserved=True, reason="unsupported_numeric_fact")
    if not _reference_tokens(candidate.response_text).issubset(_reference_tokens(source.base_text)):
        return CompositionValidation(required_terms_preserved=True, protected_terms_preserved=True, reason="unsupported_reference")
    has_completion = (
        completion_claim_detector(candidate.response_text)
        if completion_claim_detector is not None
        else bool(_DEFAULT_COMPLETION_CLAIM.search(candidate.response_text))
    )
    source_has_completion = (
        completion_claim_detector(source.base_text)
        if completion_claim_detector is not None
        else bool(_DEFAULT_COMPLETION_CLAIM.search(source.base_text))
    )
    completion_ok = source.allow_completion_claims or not has_completion
    if not completion_ok:
        return CompositionValidation(
            required_terms_preserved=True,
            protected_terms_preserved=True,
            completion_claim_valid=False,
            reason="unauthorized_completion_claim",
        )
    has_failure = bool(_FAILURE_CLAIM.search(candidate.response_text))
    if source.response_kind is ResponseKind.TOOL_SUCCESS and source_has_completion and not has_completion:
        return CompositionValidation(
            required_terms_preserved=True,
            protected_terms_preserved=True,
            completion_claim_valid=False,
            reason="completion_claim_missing",
        )
    if source.response_kind is ResponseKind.TOOL_FAILURE and not has_failure:
        return CompositionValidation(
            required_terms_preserved=True,
            protected_terms_preserved=True,
            completion_claim_valid=completion_ok,
            failure_claim_valid=False,
            reason="failure_claim_missing",
        )
    failure_ok = source.allow_failure_claims or not has_failure
    if not failure_ok:
        return CompositionValidation(
            required_terms_preserved=True,
            protected_terms_preserved=True,
            completion_claim_valid=completion_ok,
            failure_claim_valid=False,
            reason="unauthorized_failure_claim",
        )
    workflow = str(source.safe_facts.get("workflow") or source.safe_facts.get("action_category") or "").lower()
    status = str(source.safe_facts.get("payment_status") or source.safe_facts.get("status") or "").lower()
    if workflow in {"payment", "payments"} and re.search(r"\bpaid\b", candidate.response_text, re.IGNORECASE) and status != "paid":
        return CompositionValidation(
            required_terms_preserved=True,
            protected_terms_preserved=True,
            completion_claim_valid=completion_ok,
            failure_claim_valid=failure_ok,
            reason="unauthorized_payment_claim",
        )
    return CompositionValidation(
        candidate_valid=True,
        required_terms_preserved=True,
        protected_terms_preserved=True,
        completion_claim_valid=completion_ok,
        failure_claim_valid=failure_ok,
    )


def _fallback(source: ResponseCompositionInput, reason: str | None = None) -> CustomerResponseComposition:
    return CustomerResponseComposition(source.base_text, list(source.quick_replies), False, reason)


def _length_bucket(value: str) -> str:
    length = len(value)
    if length <= 120:
        return "short"
    if length <= 400:
        return "medium"
    return "long"


def _record_shadow(
    source: ResponseCompositionInput,
    candidate: CustomerResponseComposition,
    result: LLMResult | None,
    validation: CompositionValidation,
    recorder: CompositionShadowRecorder | None,
) -> None:
    metadata = {
        "response_kind": source.response_kind.value,
        "gateway_provider": result.provider if result else None,
        "gateway_fallback_used": bool(result and result.fallback_used),
        "gateway_parse_status": "success" if validation.candidate_valid else "failed",
        "candidate_valid": validation.candidate_valid,
        "response_hash_equal": hashlib.sha256(source.base_text.encode()).digest()
        == hashlib.sha256(candidate.response_text.encode()).digest(),
        "response_length_bucket_equal": _length_bucket(source.base_text) == _length_bucket(candidate.response_text),
        "quick_reply_count_equal": len(source.quick_replies) == len(candidate.quick_replies),
        "required_terms_preserved": validation.required_terms_preserved,
        "protected_terms_preserved": validation.protected_terms_preserved,
        "completion_claim_valid": validation.completion_claim_valid,
        "failure_claim_valid": validation.failure_claim_valid,
    }
    try:
        if recorder is not None:
            recorder(metadata)
        else:
            if validation.reason in {"protected_terms_changed", "required_terms_missing"}:
                caller_validation = LLMCallerValidationOutcome.REJECTED_PROTECTED_TERM
            elif validation.reason in {"numeric_token_changed", "unsupported_numeric_fact"}:
                caller_validation = LLMCallerValidationOutcome.REJECTED_NUMERIC_TOKEN
            elif validation.reason == "quick_replies_changed":
                caller_validation = LLMCallerValidationOutcome.REJECTED_QUICK_REPLY_SHAPE
            elif validation.reason and "claim" in validation.reason:
                caller_validation = LLMCallerValidationOutcome.REJECTED_TRUTH_CLAIM
            elif validation.candidate_valid:
                caller_validation = LLMCallerValidationOutcome.ACCEPTED
            else:
                caller_validation = LLMCallerValidationOutcome.REJECTED_BUSINESS_VALIDATION
            emit_configured_event(LLMShadowComparisonEvent(
                purpose=LLMPurpose.RESPONSE_COMPOSITION,
                candidate_valid=validation.candidate_valid,
                response_hash_equal=metadata["response_hash_equal"],
                quick_reply_count_equal=metadata["quick_reply_count_equal"],
                required_terms_preserved=validation.required_terms_preserved,
                protected_terms_preserved=validation.protected_terms_preserved,
                caller_validation=caller_validation,
                **safe_correlation_fields(settings.LLM_TELEMETRY_CORRELATION_KEY),
            ), settings)
    except Exception:
        return None


async def compose_customer_response(
    source: ResponseCompositionInput,
    *,
    authoritative_side_effects_occurred: bool = False,
    gateway: LLMGateway | None = None,
    gateway_factory: CompositionGatewayFactory | None = None,
    shadow_recorder: CompositionShadowRecorder | None = None,
    completion_claim_detector: CompletionClaimDetector | None = None,
) -> CustomerResponseComposition:
    mode = resolve_response_composer_mode(settings)
    policy = response_kind_policy(source.response_kind)
    if mode is ResponseComposerMode.LEGACY or policy is None or not policy.eligible:
        return _fallback(source)
    if not source.base_text.strip():
        return _fallback(source, "empty_response")
    if source.allow_completion_claims and not policy.completion_claims_allowed:
        return _fallback(source, "invalid_completion_authority")
    if source.allow_failure_claims and not policy.failure_claims_allowed:
        return _fallback(source, "invalid_failure_authority")

    try:
        if gateway is None:
            gateway = gateway_factory() if gateway_factory is not None else None
        if gateway is None:
            from backend.ai.gateway.runtime import get_runtime_llm_gateway

            gateway = get_runtime_llm_gateway()
        with llm_rollout_context(mode.value):
            result = await gateway.invoke(build_response_composer_request(
                source,
                authoritative_side_effects_occurred=authoritative_side_effects_occurred,
            ))
        if result.error is not None or result.content is None or result.tool_calls:
            candidate = _fallback(source, "provider_failure")
            validation = CompositionValidation(reason="provider_failure")
        else:
            parsed = _parse_candidate(result.content)
            validation = validate_composed_candidate(
                source,
                parsed,
                completion_claim_detector=completion_claim_detector,
            )
            candidate = (
                CustomerResponseComposition(parsed.response_text, parsed.quick_replies, True)
                if validation.candidate_valid
                else _fallback(source, validation.reason)
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "Response composer failed closed",
            extra={"extra_data": {"response_kind": source.response_kind.value, "error_type": type(exc).__name__}},
        )
        result = None
        candidate = _fallback(source, "provider_failure")
        validation = CompositionValidation(reason="provider_failure")

    if mode is ResponseComposerMode.SHADOW:
        _record_shadow(source, candidate, result, validation, shadow_recorder)
        return _fallback(source)
    return candidate


__all__ = [
    "ComposedCustomerResponse",
    "CustomerResponseComposition",
    "ResponseComposerMode",
    "ResponseCompositionInput",
    "ResponseKind",
    "ResponseKindPolicy",
    "build_response_composer_request",
    "compose_customer_response",
    "resolve_response_composer_mode",
    "response_kind_policy",
    "validate_composed_candidate",
]
