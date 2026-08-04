"""
Ollama Provider — Local Integration
Uses the OpenAI-compatible API endpoint exposed by Ollama.
"""
from backend.ai.providers.base import BaseLLM, LLMResponse
from backend.ops.logger import logger
import os

class OllamaProvider(BaseLLM):
    def __init__(self):
        is_docker = os.getenv("ENV") == "docker"
        base_url = os.getenv("OLLAMA_BASE_URL")
        if not base_url:
            base_url = "http://host.docker.internal:11434/v1" if is_docker else "http://localhost:11434/v1"
        self.model = os.getenv("AGENT_LLM_MODEL") or "gemma4:e2b"
        self._client = None
        try:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key="ollama", base_url=base_url)
            logger.info(f"Ollama provider initialized at {base_url} with model {self.model}")
        except Exception as e:
            logger.error(f"Failed to initialize Ollama provider: {e}")

    def get_provider_name(self) -> str:
        return "ollama"

    async def generate_response(
        self, system_prompt: str, user_message: str, temperature: float = 0.7
    ) -> LLMResponse:
        is_classification = "intent" in system_prompt.lower() and "confidence" in system_prompt.lower()
        provider_err = None

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
                    provider="ollama",
                    model=self.model,
                )
            except Exception as e:
                logger.error(f"Ollama API error: {e}")

                # Classify the exception
                class_name = type(e).__name__
                err_msg = str(e).lower()
                error_type = "provider_error"
                status_code = 500

                if "ratelimit" in class_name.lower() or "429" in err_msg or "rate limit" in err_msg:
                    error_type = "rate_limit"
                    status_code = 429
                elif "authentication" in class_name.lower() or "401" in err_msg or "api key" in err_msg:
                    error_type = "auth_failed"
                    status_code = 401
                elif "timeout" in class_name.lower() or "timed out" in err_msg:
                    error_type = "timeout"
                    status_code = 408

                provider_err = {
                    "provider": "ollama",
                    "model": self.model,
                    "error_type": error_type,
                    "status_code": status_code,
                    "customer_safe": True,
                    "redacted": True
                }

        fallback_content = (
            '{"intent": "unknown", "confidence": 0.5}'
            if is_classification
            else "I'm having trouble processing your request via Ollama. Please try again."
        )
        if provider_err and not is_classification:
            fallback_content = "I couldn't complete that request right now. Please try again in a moment or ask to talk to a team member."

        return LLMResponse(
            content=fallback_content,
            raw_response=None,
            provider="ollama",
            model=self.model,
            missing_usage_reason="mock_response" if not provider_err else "provider_error",
            provider_error=provider_err,
        )
