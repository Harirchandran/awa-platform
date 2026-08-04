"""Immutable, purpose-oriented gateway policies."""
from __future__ import annotations

from dataclasses import dataclass

from backend.ai.gateway.models import (
    LLMFallbackContract,
    LLMPurpose,
    LLMTimeoutClass,
    MAX_COMBINED_MESSAGE_CHARS,
    MAX_MESSAGE_COUNT,
)


DEFAULT_PROVIDER_ORDER = ("gemini", "groq", "openai")
TIMEOUT_SECONDS = {
    LLMTimeoutClass.SEMANTIC_FAST: 4.0,
    LLMTimeoutClass.STRUCTURED_STANDARD: 4.0,
    LLMTimeoutClass.KNOWLEDGE_STANDARD: 12.0,
    LLMTimeoutClass.COMPOSITION_STANDARD: 12.0,
    LLMTimeoutClass.TOOL_SELECTION_STANDARD: 15.0,
    LLMTimeoutClass.LOCALIZATION_FAST: 4.0,
}


@dataclass(frozen=True)
class LLMPurposePolicy:
    purpose: LLMPurpose
    provider_order: tuple[str, ...]
    maximum_provider_attempts: int
    same_provider_retry_count: int
    timeout_class: LLMTimeoutClass
    structured_output: bool
    tools_allowed: bool
    history_allowed: bool
    fallback_allowed: bool
    deterministic_fallback: LLMFallbackContract
    maximum_messages: int
    maximum_context_chars: int


def _policy(
    purpose: LLMPurpose,
    *,
    timeout: LLMTimeoutClass,
    structured: bool,
    tools: bool,
    history: bool,
    fallback: LLMFallbackContract,
    attempts: int = 3,
    maximum_messages: int = MAX_MESSAGE_COUNT,
    maximum_context_chars: int = MAX_COMBINED_MESSAGE_CHARS,
) -> LLMPurposePolicy:
    return LLMPurposePolicy(
        purpose=purpose,
        provider_order=DEFAULT_PROVIDER_ORDER,
        maximum_provider_attempts=attempts,
        same_provider_retry_count=0,
        timeout_class=timeout,
        structured_output=structured,
        tools_allowed=tools,
        history_allowed=history,
        fallback_allowed=True,
        deterministic_fallback=fallback,
        maximum_messages=maximum_messages,
        maximum_context_chars=maximum_context_chars,
    )


PURPOSE_POLICIES = {
    LLMPurpose.TURN_INTERPRETATION: _policy(
        LLMPurpose.TURN_INTERPRETATION,
        timeout=LLMTimeoutClass.SEMANTIC_FAST,
        structured=True,
        tools=False,
        history=False,
        fallback=LLMFallbackContract.CLARIFY,
        maximum_messages=8,
        maximum_context_chars=8_000,
    ),
    LLMPurpose.FIELD_EXTRACTION: _policy(
        LLMPurpose.FIELD_EXTRACTION,
        timeout=LLMTimeoutClass.STRUCTURED_STANDARD,
        structured=True,
        tools=False,
        history=False,
        fallback=LLMFallbackContract.PRESERVE_STATE,
        maximum_messages=8,
        maximum_context_chars=12_000,
    ),
    LLMPurpose.PENDING_ACTION_DECISION: _policy(
        LLMPurpose.PENDING_ACTION_DECISION,
        timeout=LLMTimeoutClass.SEMANTIC_FAST,
        structured=True,
        tools=False,
        history=False,
        fallback=LLMFallbackContract.CLARIFY,
        maximum_messages=6,
        maximum_context_chars=8_000,
    ),
    LLMPurpose.KNOWLEDGE_ANSWER: _policy(
        LLMPurpose.KNOWLEDGE_ANSWER,
        timeout=LLMTimeoutClass.KNOWLEDGE_STANDARD,
        structured=False,
        tools=False,
        history=True,
        fallback=LLMFallbackContract.SAFE_NOT_ENOUGH_INFORMATION,
        maximum_messages=20,
        maximum_context_chars=24_000,
    ),
    LLMPurpose.RESPONSE_COMPOSITION: _policy(
        LLMPurpose.RESPONSE_COMPOSITION,
        timeout=LLMTimeoutClass.COMPOSITION_STANDARD,
        structured=False,
        tools=False,
        history=False,
        fallback=LLMFallbackContract.ORIGINAL_BACKEND_TEXT,
        maximum_messages=8,
        maximum_context_chars=12_000,
    ),
    LLMPurpose.LOCALIZATION: _policy(
        LLMPurpose.LOCALIZATION,
        timeout=LLMTimeoutClass.LOCALIZATION_FAST,
        structured=True,
        tools=False,
        history=False,
        fallback=LLMFallbackContract.ORIGINAL_BACKEND_TEXT,
        attempts=3,
        maximum_messages=4,
        maximum_context_chars=8_000,
    ),
    LLMPurpose.TOOL_SELECTION: _policy(
        LLMPurpose.TOOL_SELECTION,
        timeout=LLMTimeoutClass.TOOL_SELECTION_STANDARD,
        structured=False,
        tools=True,
        history=True,
        fallback=LLMFallbackContract.NO_TOOL,
        maximum_messages=24,
        maximum_context_chars=28_000,
    ),
}


def get_purpose_policy(purpose: LLMPurpose) -> LLMPurposePolicy:
    return PURPOSE_POLICIES[purpose]
