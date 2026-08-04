"""Groq gateway adapter over the supported OpenAI-compatible API."""
from __future__ import annotations

from typing import Callable

from langchain_openai import ChatOpenAI

from backend.ai.gateway.providers.base import AsyncChatClient, OpenAICompatibleGatewayAdapter, ProviderCapabilities


GROQ_CAPABILITIES = ProviderCapabilities(
    provider_native_metadata=False,
    tool_model_prefixes=("llama-3.3", "llama-3.1", "qwen", "deepseek", "openai/"),
)


class GroqGatewayAdapter(OpenAICompatibleGatewayAdapter):
    provider_name = "groq"

    def __init__(self, *, model_name: str, api_key: str | None, client_factory: Callable[[float], AsyncChatClient] | None = None) -> None:
        if client_factory is None:
            client_factory = lambda timeout: ChatOpenAI(
                model=model_name,
                api_key=api_key,
                base_url="https://api.groq.com/openai/v1",
                timeout=timeout,
                max_retries=0,
            )
        super().__init__(model_name=model_name, api_key=api_key, capabilities=GROQ_CAPABILITIES, client_factory=client_factory)


__all__ = ["GROQ_CAPABILITIES", "GroqGatewayAdapter"]
