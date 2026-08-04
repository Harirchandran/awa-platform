"""Gemini gateway adapter using its supported OpenAI-compatible endpoint."""
from __future__ import annotations

from typing import Any, Callable

from backend.ai.gateway.providers.base import AsyncChatClient, OpenAICompatibleGatewayAdapter, ProviderCapabilities
from backend.ai.providers.gemini_chat_adapter import (
    PROVIDER_METADATA_KEY,
    GeminiChatOpenAI,
    sanitize_gemini_additional_kwargs,
)


GEMINI_CAPABILITIES = ProviderCapabilities(provider_native_metadata=True)


class GeminiGatewayAdapter(OpenAICompatibleGatewayAdapter):
    provider_name = "gemini"

    def __init__(self, *, model_name: str, api_key: str | None, client_factory: Callable[[float], AsyncChatClient] | None = None) -> None:
        if client_factory is None:
            client_factory = lambda timeout: GeminiChatOpenAI(
                model=model_name,
                api_key=api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                timeout=timeout,
                max_retries=0,
            )
        super().__init__(model_name=model_name, api_key=api_key, capabilities=GEMINI_CAPABILITIES, client_factory=client_factory)

    def _provider_metadata(self, response: Any, calls: list) -> dict[str, Any]:
        additional = getattr(response, "additional_kwargs", None)
        sanitized = sanitize_gemini_additional_kwargs(additional, allowed_call_ids=[call.id for call in calls])
        wrapped = sanitized.get(PROVIDER_METADATA_KEY)
        if not isinstance(wrapped, dict):
            return {}
        signatures = wrapped.get("tool_call_thought_signatures")
        return {"gemini": {"tool_call_thought_signatures": signatures}} if signatures else {}


__all__ = ["GEMINI_CAPABILITIES", "GeminiGatewayAdapter"]
