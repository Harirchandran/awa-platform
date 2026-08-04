"""
Gemini Provider — Real Integration with Graceful Fallback
Uses google-generativeai if available and API key is set.
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
class GeminiProvider(BaseLLM):
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY", "")
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
        self._model = None
        if self.api_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.api_key)
                self._model = genai.GenerativeModel(self.model_name)
                logger.info("Gemini provider initialized with real API client")
            except ImportError:
                logger.warning("google-generativeai package not installed — using mock mode")
        else:
            logger.info("Gemini provider initialized in mock mode (no API key)")
    def get_provider_name(self) -> str:
        return "gemini"
    async def generate_response(
        self, system_prompt: str, user_message: str, temperature: float = 0.7
    ) -> LLMResponse:
        is_classification = "intent" in system_prompt.lower() and "confidence" in system_prompt.lower()
        provider_err = None

        if not self._model:
            if is_real_provider_required():
                raise ProviderConfigurationError("GEMINI_API_KEY is required when real provider execution is required")
            fallback_content = '{"intent": "unknown", "confidence": 0.4}' if is_classification else "[Gemini Mock Response] I can help with that."
            return LLMResponse(
                content=fallback_content,
                provider="gemini",
                model=self.model_name,
                missing_usage_reason="mock_response",
            )

        if self._model:
            try:
                import asyncio
                # google-generativeai uses sync API, run in executor
                loop = asyncio.get_event_loop()
                combined_prompt = f"{system_prompt}\n\nUser: {user_message}"
                response = await loop.run_in_executor(
                    None,
                    lambda: self._model.generate_content(
                        combined_prompt,
                        generation_config={"temperature": temperature, "max_output_tokens": 1024}
                    )
                )
                content = response.text
                return LLMResponse(
                    content=content,
                    raw_response=response,
                    provider="gemini",
                    model=self.model_name,
                    usage_metadata=getattr(response, "usage_metadata", None),
                )
            except Exception as e:
                # Classify the exception, but preserve the customer-facing safe fallback contract.
                class_name = type(e).__name__
                err_msg = str(e).lower()
                error_type = "provider_error"
                status_code = 500

                if class_name == "ResourceExhausted" or "429" in err_msg or "rate limit" in err_msg or "resource_exhausted" in err_msg:
                    error_type = "rate_limit"
                    status_code = 429
                elif class_name in ("PermissionDenied", "Unauthenticated") or "403" in err_msg or "401" in err_msg or "api key" in err_msg or "invalid credentials" in err_msg:
                    error_type = "auth_failed"
                    status_code = 403
                elif class_name == "DeadlineExceeded" or "timeout" in err_msg or "deadline_exceeded" in err_msg or "timed out" in err_msg:
                    error_type = "timeout"
                    status_code = 408
                elif "500" in err_msg or "502" in err_msg or "503" in err_msg or "504" in err_msg or "bad gateway" in err_msg or "service unavailable" in err_msg:
                    error_type = "provider_error"
                    status_code = 503

                emit_safe_llm_log(
                    level="WARNING", event="legacy_provider_failure", provider="gemini",
                    model=safe_identifier(self.model_name), outcome="error",
                    error_code=safe_llm_error_code(
                        error_type=error_type, exception_type=class_name, status_code=status_code,
                    ),
                    status_category=safe_status_category(status_code),
                )

                provider_err = {
                    "provider": "gemini",
                    "model": self.model_name,
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
                    provider="gemini",
                    model=self.model_name,
                    missing_usage_reason="provider_error",
                    provider_error=provider_err,
                )
