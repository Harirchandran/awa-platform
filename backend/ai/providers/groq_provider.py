import os
import httpx
from backend.observability.llm_logging import (
    emit_safe_llm_log,
    safe_identifier,
    safe_llm_error_code,
    safe_status_category,
)
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
class GroqProvider(BaseLLM):
    """
    Groq API Implementation using llama-3.3-70b-versatile model.
    Requires `GROQ_API_KEY` env var.
    """
    API_URL = "https://api.groq.com/openai/v1/chat/completions"
    MODEL = "llama-3.3-70b-versatile"
    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY")
        provider_env = (os.getenv("AGENT_LLM_PROVIDER") or "").lower()
        self.MODEL = (os.getenv("AGENT_LLM_MODEL") if provider_env == "groq" else None) or "llama-3.3-70b-versatile"
        if not self.api_key:
            emit_safe_llm_log(
                event="legacy_provider_mode", provider="groq",
                model=safe_identifier(self.MODEL), outcome="mock",
            )
    def get_provider_name(self) -> str:
        return "groq"
    async def generate_response(self, system_prompt: str, user_message: str, temperature: float = 0.7) -> LLMResponse:
        """
        Call Groq API with llama-3.3-70b-versatile model.
        Falls back to mock if API key not configured.
        """
        is_classification = "intent" in system_prompt.lower() and "confidence" in system_prompt.lower()
        provider_err = None

        if not self.api_key:
            if is_real_provider_required():
                raise ProviderConfigurationError("GROQ_API_KEY is required when real provider execution is required")
            emit_safe_llm_log(
                event="legacy_provider_invocation", purpose="routing" if is_classification else "unknown",
                provider="groq", model=safe_identifier(self.MODEL), outcome="mock",
            )
            if is_classification:
                normalized = user_message.lower()
                if any(kw in normalized for kw in ["book", "appointment", "schedule", "reserve"]):
                    intent = "booking"
                elif any(kw in normalized for kw in ["price", "cost", "how much"]):
                    intent = "faq"
                elif any(kw in normalized for kw in ["help", "support", "problem", "issue"]):
                    intent = "support"
                elif any(kw in normalized for kw in ["hi", "hello", "hey", "good morning"]):
                    intent = "greeting"
                else:
                    intent = "unknown"
                fallback_content = f'{{"intent": "{intent}", "confidence": 0.9}}'
            else:
                fallback_content = "[Groq Mock Response] I can help with that."
            return LLMResponse(
                content=fallback_content,
                provider="groq",
                model=self.MODEL,
                missing_usage_reason="mock_response",
            )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "temperature": temperature,
            "max_tokens": 1024
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self.API_URL, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return LLMResponse(
                    content=content,
                    raw_response=data,
                    provider="groq",
                    model=data.get("model") or self.MODEL,
                    usage_metadata=data.get("usage"),
                )
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            error_type = "provider_error"
            if status_code == 429:
                error_type = "rate_limit"
            elif status_code in (401, 403):
                error_type = "auth_failed"
            elif status_code == 408:
                error_type = "timeout"
            elif status_code in (500, 502, 503, 504):
                error_type = "provider_error"

            provider_err = {
                "provider": "groq",
                "model": self.MODEL,
                "error_type": error_type,
                "status_code": status_code,
                "customer_safe": True,
                "redacted": True,
            }
            headers = getattr(e.response, "headers", None)
            request_id = (
                headers.get("x-request-id") or headers.get("request-id")
                if headers else None
            )
            emit_safe_llm_log(
                level="WARNING", event="legacy_provider_failure", provider="groq",
                model=safe_identifier(self.MODEL), outcome="error",
                error_code=safe_llm_error_code(error_type=error_type, status_code=status_code),
                status_category=safe_status_category(status_code),
                provider_request_id=safe_identifier(request_id),
            )
        except Exception as e:
            err_msg = str(e).lower()
            error_type = "provider_error"
            status_code = 500
            if "timeout" in err_msg or "timed out" in err_msg:
                error_type = "timeout"
                status_code = 408

            provider_err = {
                "provider": "groq",
                "model": self.MODEL,
                "error_type": error_type,
                "status_code": status_code,
                "customer_safe": True,
                "redacted": True,
            }
            emit_safe_llm_log(
                level="WARNING", event="legacy_provider_failure", provider="groq",
                model=safe_identifier(self.MODEL), outcome="error",
                error_code=safe_llm_error_code(
                    error_type=error_type, exception_type=type(e).__name__, status_code=status_code,
                ),
                status_category=safe_status_category(status_code),
            )

        fallback_content = (
            '{"intent": "unknown", "confidence": 0.5}'
            if is_classification
            else "I couldn't complete that request right now. Please try again in a moment or ask to talk to a team member."
        )
        return LLMResponse(
            content=fallback_content,
            provider="groq",
            model=self.MODEL,
            missing_usage_reason="provider_error",
            provider_error=provider_err,
        )
