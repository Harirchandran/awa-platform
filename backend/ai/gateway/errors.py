"""Provider-neutral error taxonomy for the LLM gateway."""
from __future__ import annotations

from typing import Any

from backend.ai.gateway.models import LLMErrorInfo


class LLMError(Exception):
    code = "llm_error"
    retry_same_provider = False
    fallback_next_provider = False
    log_level = "ERROR"

    def __init__(self, *, safe_metadata: dict[str, Any] | None = None):
        super().__init__(self.code)
        self.safe_metadata = dict(safe_metadata or {})

    def to_info(self) -> LLMErrorInfo:
        return LLMErrorInfo(
            code=self.code,
            retry_same_provider=self.retry_same_provider,
            fallback_next_provider=self.fallback_next_provider,
            log_level=self.log_level,
            safe_metadata=self.safe_metadata,
        )


class LLMTransientError(LLMError):
    retry_same_provider = True
    fallback_next_provider = True
    log_level = "WARNING"


class LLMRateLimitError(LLMTransientError):
    code = "rate_limit"


class LLMTimeoutError(LLMTransientError):
    code = "timeout"


class LLMNetworkError(LLMTransientError):
    code = "network"


class LLMProviderUnavailableError(LLMTransientError):
    code = "provider_unavailable"
    retry_same_provider = False


class LLMProviderServerError(LLMTransientError):
    code = "provider_server_error"


class LLMAuthenticationError(LLMError):
    code = "authentication"
    fallback_next_provider = True
    log_level = "CRITICAL"


class LLMInvalidModelError(LLMError):
    code = "invalid_model"
    fallback_next_provider = True


class LLMInvalidRequestError(LLMError):
    code = "invalid_request"


class LLMSafetyRejectionError(LLMError):
    code = "safety_rejection"
    log_level = "WARNING"


class LLMContextLengthError(LLMError):
    code = "context_length"


class LLMStructuredOutputError(LLMError):
    code = "structured_output"
    fallback_next_provider = True
    log_level = "WARNING"


class LLMToolProtocolError(LLMError):
    code = "tool_protocol"
    fallback_next_provider = True
    log_level = "WARNING"


class LLMAllProvidersFailedError(LLMError):
    code = "all_providers_failed"


def require_gateway_error(exc: Exception) -> LLMError:
    """Reject accidental classification of business, tool, or database failures."""
    if not isinstance(exc, LLMError):
        raise TypeError("provider adapters must normalize only provider inference failures")
    return exc
