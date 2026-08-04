"""Explicit production registry/gateway construction; never run at module import."""
from __future__ import annotations

from collections.abc import Mapping

from backend.ai.gateway.configuration import provider_api_key, resolve_provider_config
from backend.ai.gateway.gateway import LLMGateway
from backend.ai.gateway.providers.gemini import GeminiGatewayAdapter
from backend.ai.gateway.providers.groq import GroqGatewayAdapter
from backend.ai.gateway.providers.openai import OpenAIGatewayAdapter
from backend.ai.gateway.registry import LLMProviderAdapter, LLMProviderRegistry, normalize_provider_name
from backend.ai.gateway.telemetry import (
    LLMGatewayTelemetryRecorder,
    get_configured_llm_telemetry_recorder,
)
from backend.core.config import Settings


_ADAPTER_TYPES = {
    "gemini": GeminiGatewayAdapter,
    "groq": GroqGatewayAdapter,
    "openai": OpenAIGatewayAdapter,
}


def build_gateway_registry(
    settings: Settings,
    *,
    adapter_overrides: Mapping[str, LLMProviderAdapter] | None = None,
) -> LLMProviderRegistry:
    overrides = {normalize_provider_name(name): adapter for name, adapter in (adapter_overrides or {}).items()}
    unknown = set(overrides) - set(_ADAPTER_TYPES)
    if unknown:
        raise ValueError("adapter overrides contain an unknown provider")
    real_required = settings.ENV == "production" or settings.AWA_INTEGRATION_MODE == "live" or settings.AWA_REAL_PROVIDER_REQUIRED
    if real_required and overrides:
        raise ValueError("gateway adapter overrides are prohibited when real providers are required")

    registry = LLMProviderRegistry()
    for provider in settings.llm_gateway_fallback_order:
        adapter = overrides.get(provider)
        if adapter is None:
            resolved = resolve_provider_config(settings, provider)
            adapter = _ADAPTER_TYPES[provider](
                model_name=resolved.model,
                api_key=provider_api_key(settings, provider),
            )
        registry.register(provider, adapter)
    return registry


def build_llm_gateway(
    settings: Settings,
    *,
    recorder: LLMGatewayTelemetryRecorder | None = None,
    adapter_overrides: Mapping[str, LLMProviderAdapter] | None = None,
) -> LLMGateway:
    effective_recorder = recorder or get_configured_llm_telemetry_recorder(settings)
    return LLMGateway(
        build_gateway_registry(settings, adapter_overrides=adapter_overrides),
        provider_order=settings.llm_gateway_fallback_order,
        telemetry=effective_recorder,
        telemetry_enabled=settings.LLM_TELEMETRY_ENABLED,
        telemetry_sample_rate=settings.LLM_TELEMETRY_SAMPLE_RATE,
        telemetry_include_usage=settings.LLM_TELEMETRY_INCLUDE_USAGE,
        telemetry_correlation_key=settings.LLM_TELEMETRY_CORRELATION_KEY,
    )


__all__ = ["build_gateway_registry", "build_llm_gateway"]
