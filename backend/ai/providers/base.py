from abc import ABC, abstractmethod
import os
from typing import List, Optional, Dict, Any


def is_real_provider_required() -> bool:
    if str(os.getenv("AWA_REAL_PROVIDER_REQUIRED") or "").strip().lower() in {"1", "true", "yes", "y", "on"}:
        return True
    if str(os.getenv("AWA_INTEGRATION_MODE") or "").strip().lower() == "live":
        return True
    try:
        from backend.core.config import settings
        return bool(getattr(settings, "AWA_REAL_PROVIDER_REQUIRED", False)) or getattr(settings, "AWA_INTEGRATION_MODE", None) == "live"
    except Exception:
        return False
def _int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
def _read_attr_or_key(source: Any, *keys: str) -> Any:
    for key in keys:
        if isinstance(source, dict) and key in source:
            return source[key]
        if hasattr(source, key):
            return getattr(source, key)
    return None
def normalize_llm_usage(
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    usage_metadata: Any = None,
    raw_response: Any = None,
    cost_usd: Optional[float] = None,
    missing_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Normalize provider token metadata without estimating missing cost."""
    usage = usage_metadata
    source = "usage_metadata" if usage_metadata is not None else None
    if usage is None and raw_response is not None:
        usage = _read_attr_or_key(raw_response, "usage", "usage_metadata")
        source = "raw_response"
    if usage is None and raw_response is not None:
        response_metadata = _read_attr_or_key(raw_response, "response_metadata")
        usage = _read_attr_or_key(response_metadata, "token_usage", "usage")
        source = "response_metadata"
        model = model or _read_attr_or_key(response_metadata, "model_name", "model")
    if usage is None:
        usage = {}
    provider = provider or _read_attr_or_key(raw_response, "provider") or None
    model = model or _read_attr_or_key(raw_response, "model", "model_name") or None
    prompt_tokens = _int_or_none(_read_attr_or_key(
        usage,
        "prompt_tokens",
        "input_tokens",
        "prompt_token_count",
    ))
    completion_tokens = _int_or_none(_read_attr_or_key(
        usage,
        "completion_tokens",
        "output_tokens",
        "candidates_token_count",
    ))
    total_tokens = _int_or_none(_read_attr_or_key(usage, "total_tokens", "total_token_count"))
    if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
    normalized_cost = _float_or_none(cost_usd)
    usage_available = any(value is not None for value in (prompt_tokens, completion_tokens, total_tokens))
    return {
        "provider": str(provider) if provider else None,
        "model": str(model) if model else None,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_usd": normalized_cost,
        "usage_available": usage_available,
        "cost_available": normalized_cost is not None,
        "source": source or "unavailable",
        "missing_reason": None if usage_available else (missing_reason or "provider_usage_missing"),
    }
class LLMResponse:
    def __init__(
        self,
        content: str,
        raw_response: Any = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        usage_metadata: Any = None,
        cost_usd: Optional[float] = None,
        missing_usage_reason: Optional[str] = None,
        provider_error: Optional[Dict[str, Any]] = None,
    ):
        self.content = content
        self.raw_response = raw_response
        self.usage_metadata = normalize_llm_usage(
            provider=provider,
            model=model,
            usage_metadata=usage_metadata,
            raw_response=raw_response,
            cost_usd=cost_usd,
            missing_reason=missing_usage_reason,
        )
        self.provider_error = provider_error

class BaseLLM(ABC):
    """
    Abstract Interface for all LLM Providers (Groq, Gemini, OpenAI).
    """
    @abstractmethod
    def get_provider_name(self) -> str:
        """Returns provider identifier (e.g., 'groq', 'gemini')"""
        pass
    @abstractmethod
    async def generate_response(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7
    ) -> LLMResponse:
        """
        Generates a chat completion.
        """
        pass


class ProviderError(Exception):
    pass


class ProviderRetryableError(ProviderError):
    pass


class ProviderRateLimitError(ProviderRetryableError):
    pass


class ProviderTimeoutError(ProviderRetryableError):
    pass


class ProviderConfigurationError(ProviderError):
    pass


class ProviderAuthError(ProviderError):
    pass
