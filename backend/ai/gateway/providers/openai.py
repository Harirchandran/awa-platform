"""OpenAI gateway adapter."""
from __future__ import annotations

from typing import Callable

from langchain_openai import ChatOpenAI

from backend.ai.gateway.providers.base import AsyncChatClient, OpenAICompatibleGatewayAdapter, ProviderCapabilities


OPENAI_CAPABILITIES = ProviderCapabilities(provider_native_metadata=False)


class OpenAIGatewayAdapter(OpenAICompatibleGatewayAdapter):
    provider_name = "openai"

    def __init__(self, *, model_name: str, api_key: str | None, client_factory: Callable[[float], AsyncChatClient] | None = None) -> None:
        if client_factory is None:
            client_factory = lambda timeout: ChatOpenAI(
                model=model_name,
                api_key=api_key,
                timeout=timeout,
                max_retries=0,
            )
        super().__init__(model_name=model_name, api_key=api_key, capabilities=OPENAI_CAPABILITIES, client_factory=client_factory)


__all__ = ["OPENAI_CAPABILITIES", "OpenAIGatewayAdapter"]
