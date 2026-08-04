"""
OpenAI Provider — Real Integration with Graceful Fallback
Uses the openai package if available and API key is set.
Falls back to mock response otherwise.
"""
from backend.ai.providers.base import (
    BaseLLM,
    LLMResponse,
    ProviderError,
    ProviderRetryableError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderConfigurationError,
    ProviderAuthError,
    is_real_provider_required,
)
from backend.ops.logger import logger
from backend.observability.llm_logging import (
    emit_safe_llm_log,
    safe_identifier,
    safe_llm_error_code,
    safe_status_category,
)
import os
class OpenAIProvider(BaseLLM):
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        provider_env = (os.getenv("AGENT_LLM_PROVIDER") or "").lower()
        self.model = (os.getenv("AGENT_LLM_MODEL") if provider_env == "openai" else None) or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self._client = None
        if self.api_key:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(api_key=self.api_key)
                logger.info("OpenAI provider initialized with real API client")
            except ImportError:
                logger.warning("openai package not installed — using mock mode")
        else:
            logger.info("OpenAI provider initialized in mock mode (no API key)")
    def get_provider_name(self) -> str:
        return "openai"
    async def generate_response(
        self, system_prompt: str, user_message: str, temperature: float = 0.7
    ) -> LLMResponse:
        is_classification = "intent" in system_prompt.lower() and "confidence" in system_prompt.lower()
        provider_err = None

        if not self._client:
            if is_real_provider_required():
                raise ProviderConfigurationError("OPENAI_API_KEY is required when real provider execution is required")
            fallback_content = '{"intent": "unknown", "confidence": 0.4}' if is_classification else "[OpenAI Mock Response] I can help with that."
            return LLMResponse(
                content=fallback_content,
                provider="openai",
                model=self.model,
                missing_usage_reason="mock_response",
            )

        if self._client:
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message}
                    ],
                    temperature=temperature,
                    max_tokens=1024
                )
                content = response.choices[0].message.content
                return LLMResponse(
                    content=content,
                    raw_response=response,
                    provider="openai",
                    model=getattr(response, "model", None) or self.model,
                )
            except Exception as e:
                # Classify the exception, but preserve the customer-facing safe fallback contract.
                class_name = type(e).__name__
                err_msg = str(e).lower()
                error_type = "provider_error"
                status_code = 500

                if "ratelimit" in class_name.lower() or "429" in err_msg or "rate limit" in err_msg:
                    error_type = "rate_limit"
                    status_code = 429
                elif "authentication" in class_name.lower() or "auth" in class_name.lower() or "401" in err_msg or "api key" in err_msg or "403" in err_msg:
                    error_type = "auth_failed"
                    status_code = 401
                elif "timeout" in class_name.lower() or "timed out" in err_msg:
                    error_type = "timeout"
                    status_code = 408
                elif "500" in err_msg or "502" in err_msg or "503" in err_msg or "504" in err_msg or "bad gateway" in err_msg or "service unavailable" in err_msg:
                    error_type = "provider_error"
                    status_code = 503

                emit_safe_llm_log(
                    level="WARNING", event="legacy_provider_failure", provider="openai",
                    model=safe_identifier(self.model), outcome="error",
                    error_code=safe_llm_error_code(
                        error_type=error_type, exception_type=class_name, status_code=status_code,
                    ),
                    status_category=safe_status_category(status_code),
                )

                provider_err = {
                    "provider": "openai",
                    "model": self.model,
                    "error_type": error_type,
                    "status_code": status_code,
                    "customer_safe": True,
                    "redacted": True,
                }

                fallback_content = (
                    '{"intent": "unknown", "confidence": 0.5}'
                    if is_classification
                    else "I couldn't complete that request right now. Please try again in a moment or ask to talk to a team member."
                )
                return LLMResponse(
                    content=fallback_content,
                    raw_response=None,
                    provider="openai",
                    model=self.model,
                    missing_usage_reason="provider_error",
                    provider_error=provider_err,
                )
