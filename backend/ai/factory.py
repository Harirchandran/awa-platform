"""
LLM Provider Factory — Production
Creates the appropriate LLM provider with fallback chain: Groq → OpenAI → Gemini
"""
import os
from typing import Any
from backend.ai.providers.base import BaseLLM, ProviderConfigurationError
from backend.ops.logger import logger
class ObservedLLM(BaseLLM):
    """
    Diagnostic wrapper for BaseLLM providers.
    Measures generate_response latency using monotonic clocks and stores it in contextvars.
    """
    def __init__(self, inner: BaseLLM):
        self.inner = inner

    def get_provider_name(self) -> str:
        return self.inner.get_provider_name()

    @property
    def api_key(self) -> str:
        return getattr(self.inner, "api_key", "")

    @property
    def model_name(self) -> str:
        return getattr(self.inner, "model_name", "") or getattr(self.inner, "model", "") or getattr(self.inner, "MODEL", "")

    @property
    def _model(self) -> Any:
        return getattr(self.inner, "_model", None)

    async def generate_response(
        self, system_prompt: str, user_message: str, temperature: float = 0.7
    ):
        from backend.observability.latency import latency_metrics_var, now_ms, elapsed_ms
        from backend.observability.provider_rate_limiter import (
            acquire_provider_capacity,
            estimate_llm_request_tokens,
            get_redis_client,
        )
        from backend.ops.logger import _tenant_id_ctx
        from backend.core.config import settings
        from backend.ai.providers.base import LLMResponse

        provider_name = self.get_provider_name()
        model_name = self.model_name
        tenant_id = _tenant_id_ctx.get()
        if tenant_id == "unknown":
            tenant_id = None

        estimated_tokens = estimate_llm_request_tokens(system_prompt, user_message)
        redis_client = await get_redis_client()

        throttle_start = now_ms()
        decision = await acquire_provider_capacity(
            redis_client=redis_client,
            provider=provider_name,
            model=model_name,
            tenant_id=tenant_id,
            estimated_tokens=estimated_tokens,
            max_wait_ms=settings.PROVIDER_RATE_LIMIT_MAX_WAIT_MS,
        )
        throttle_duration = elapsed_ms(throttle_start)

        metrics = latency_metrics_var.get()
        if metrics is not None:
            metrics["provider_llm_call_count"] = metrics.get("provider_llm_call_count", 0) + 1
            metrics["provider_total_estimated_tokens"] = metrics.get("provider_total_estimated_tokens", 0) + estimated_tokens
            metrics["provider_input_estimated_tokens"] = metrics.get("provider_input_estimated_tokens", 0) + max(0, estimated_tokens - 1024)
            if metrics.get("provider_throttle_wait_ms") is None:
                metrics["provider_throttle_wait_ms"] = 0
            metrics["provider_throttle_wait_ms"] += throttle_duration

        if not decision.allowed:
            provider_err = {
                "provider": provider_name,
                "model": model_name,
                "error_type": "rate_limit_wait_timeout",
                "status_code": None,
                "customer_safe": True,
                "redacted": True
            }
            fallback_content = "I couldn't complete that request right now. Please try again in a moment or ask to talk to a team member."
            return LLMResponse(
                content=fallback_content,
                raw_response=None,
                provider=provider_name,
                model=model_name,
                missing_usage_reason="rate_limit_wait_timeout",
                provider_error=provider_err,
            )

        start = now_ms()
        try:
            res = await self.inner.generate_response(system_prompt, user_message, temperature)
            return res
        except Exception as exc:
            exc_str = str(exc)
            if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str.upper() or "rate_limit" in exc_str.lower() or "quota" in exc_str.lower():
                if metrics is not None:
                    metrics["provider_actual_429s"] = metrics.get("provider_actual_429s", 0) + 1
            raise
        finally:
            dur = elapsed_ms(start)
            if metrics is not None:
                if metrics.get("provider_latency_ms") is None:
                    metrics["provider_latency_ms"] = 0
                metrics["provider_latency_ms"] += dur

from typing import Any

class LLMFactory:
    """Factory with fallback priority: Groq → OpenAI → Gemini"""
    @staticmethod
    def create_provider(provider_name: str = None) -> BaseLLM:
        """
        Create an LLM provider. If provider_name is not specified,
        uses LLM_PROVIDER env var. Falls through the chain on import failure.
        """
        provider = LLMFactory._create_provider_inner(provider_name)
        if isinstance(provider, ObservedLLM):
            return provider
        return ObservedLLM(provider)

    @staticmethod
    def _create_provider_inner(provider_name: str = None) -> BaseLLM:
        # Try primary provider
        provider = provider_name or os.getenv("LLM_PROVIDER", "groq")
        provider = provider.lower().strip()
        supported_providers = {"groq", "openai", "gemini", "ollama"}
        if provider not in supported_providers:
            raise ValueError(f"Unsupported LLM provider: {provider}")
        # Try primary provider
        try:
            if provider == "groq":
                from backend.ai.providers.groq_provider import GroqProvider
                return GroqProvider()
            elif provider == "openai":
                from backend.ai.providers.openai_provider import OpenAIProvider
                return OpenAIProvider()
            elif provider == "gemini":
                from backend.ai.providers.gemini_provider import GeminiProvider
                return GeminiProvider()
            elif provider == "ollama":
                from backend.ai.providers.ollama_provider import OllamaProvider
                return OllamaProvider()
        except Exception as e:
            logger.warning(f"Primary provider '{provider}' failed to initialize: {e}")
        # Fallback chain: Groq → OpenAI → Gemini
        if provider == "ollama":
            raise RuntimeError("Ollama provider failed to initialize")
        fallback_order = ["groq", "openai", "gemini"]
        for fallback in fallback_order:
            if fallback == provider:
                continue  # Skip the one we already tried
            try:
                if fallback == "groq":
                    from backend.ai.providers.groq_provider import GroqProvider
                    logger.info(f"Falling back to Groq provider")
                    return GroqProvider()
                elif fallback == "openai":
                    from backend.ai.providers.openai_provider import OpenAIProvider
                    logger.info(f"Falling back to OpenAI provider")
                    return OpenAIProvider()
                elif fallback == "gemini":
                    from backend.ai.providers.gemini_provider import GeminiProvider
                    logger.info(f"Falling back to Gemini provider")
                    return GeminiProvider()
            except Exception as e:
                logger.warning(f"Fallback provider '{fallback}' also failed: {e}")
                continue
        logger.error("All providers failed; no mock provider fallback will be returned")
        raise ProviderConfigurationError("All configured LLM providers failed to initialize")
