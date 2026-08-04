"""Provider-neutral message normalization at the future adapter boundary."""
from __future__ import annotations

from typing import Any

from backend.ai.gateway.models import CanonicalLLMMessage, KNOWN_PROVIDERS


GEMINI_SIGNATURES_KEY = "tool_call_thought_signatures"


def _gemini_metadata_for_message(message: CanonicalLLMMessage, metadata: dict[str, Any]) -> dict[str, Any]:
    clean = dict(metadata)
    signatures = clean.get(GEMINI_SIGNATURES_KEY)
    if isinstance(signatures, dict):
        allowed_ids = {call.id for call in message.tool_calls}
        clean[GEMINI_SIGNATURES_KEY] = {
            str(call_id): signature
            for call_id, signature in signatures.items()
            if str(call_id) in allowed_ids and isinstance(signature, str) and signature
        }
        if not clean[GEMINI_SIGNATURES_KEY]:
            clean.pop(GEMINI_SIGNATURES_KEY)
    return clean


def normalize_messages_for_provider(
    messages: list[CanonicalLLMMessage],
    selected_provider: str,
) -> list[CanonicalLLMMessage]:
    """Keep canonical facts while retaining metadata only for its owning provider."""
    provider = str(selected_provider or "").strip().lower()
    if provider not in KNOWN_PROVIDERS:
        raise ValueError("unknown provider for message normalization")

    normalized: list[CanonicalLLMMessage] = []
    for message in messages:
        owned = message.provider_metadata.get(provider)
        provider_metadata: dict[str, dict[str, Any]] = {}
        if isinstance(owned, dict):
            clean = _gemini_metadata_for_message(message, owned) if provider == "gemini" else dict(owned)
            if clean:
                provider_metadata[provider] = clean
        normalized.append(message.model_copy(deep=True, update={"provider_metadata": provider_metadata}))
    return normalized
