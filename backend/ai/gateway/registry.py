"""Injectable provider registry contracts; no production adapters are registered yet."""
from __future__ import annotations

from typing import Protocol

from backend.ai.gateway.models import ProviderInvocationRequest, ProviderInvocationResult
from backend.ai.gateway.providers.capabilities import ProviderCapabilities


class LLMProviderAdapter(Protocol):
    provider_name: str
    model_name: str | None
    capabilities: ProviderCapabilities

    async def invoke(self, request: ProviderInvocationRequest) -> ProviderInvocationResult:
        ...


def normalize_provider_name(provider_name: str) -> str:
    normalized = str(provider_name or "").strip().lower()
    if not normalized or not normalized.replace("_", "").replace("-", "").isalnum():
        raise ValueError("invalid provider name")
    return normalized


class LLMProviderRegistry:
    """An instance-owned registry suitable for deterministic test injection."""

    def __init__(self) -> None:
        self._adapters: dict[str, LLMProviderAdapter] = {}

    def register(self, provider_name: str, adapter: LLMProviderAdapter) -> None:
        normalized = normalize_provider_name(provider_name)
        adapter_name = normalize_provider_name(adapter.provider_name)
        if normalized != adapter_name:
            raise ValueError("adapter provider name does not match registration name")
        if normalized in self._adapters:
            raise ValueError(f"provider already registered: {normalized}")
        self._adapters[normalized] = adapter

    def get(self, provider_name: str) -> LLMProviderAdapter | None:
        return self._adapters.get(normalize_provider_name(provider_name))

    def registered_names(self) -> tuple[str, ...]:
        return tuple(self._adapters)

    def ordered(self, provider_order: tuple[str, ...]) -> tuple[tuple[str, LLMProviderAdapter | None], ...]:
        return tuple((normalize_provider_name(name), self.get(name)) for name in provider_order)
