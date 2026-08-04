"""Scripted provider adapters for deterministic gateway tests."""
from __future__ import annotations

from dataclasses import dataclass

from backend.ai.gateway.errors import (
    LLMAuthenticationError,
    LLMError,
    LLMInvalidModelError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMSafetyRejectionError,
    LLMTimeoutError,
    LLMToolProtocolError,
)
from backend.ai.gateway.models import ProviderInvocationRequest, ProviderInvocationResult


SCRIPTED_ERRORS: dict[str, type[LLMError]] = {
    "rate_limit": LLMRateLimitError,
    "timeout": LLMTimeoutError,
    "network": LLMNetworkError,
    "authentication": LLMAuthenticationError,
    "invalid_model": LLMInvalidModelError,
    "safety_rejection": LLMSafetyRejectionError,
    "tool_protocol": LLMToolProtocolError,
}


@dataclass(frozen=True)
class ScriptedCallRecord:
    provider: str
    attempt: int
    purpose: str
    message_count: int
    tool_count: int


class ScriptedLLMProviderAdapter:
    def __init__(self, provider_name: str, outcomes: list[ProviderInvocationResult | LLMError | str]):
        self.provider_name = provider_name
        self.model_name = f"{provider_name}-test-model"
        self._outcomes = list(outcomes)
        self.calls: list[ScriptedCallRecord] = []

    async def invoke(self, request: ProviderInvocationRequest) -> ProviderInvocationResult:
        self.calls.append(ScriptedCallRecord(
            provider=self.provider_name,
            attempt=request.attempt_number,
            purpose=request.purpose.value,
            message_count=len(request.messages),
            tool_count=len(request.tools),
        ))
        if not self._outcomes:
            raise AssertionError("scripted adapter invoked more times than expected")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, LLMError):
            raise outcome
        if isinstance(outcome, str) and outcome in SCRIPTED_ERRORS:
            raise SCRIPTED_ERRORS[outcome]()
        if isinstance(outcome, str) and outcome == "malformed_structured_output":
            return ProviderInvocationResult(content="{not-json", model=self.model_name)
        if isinstance(outcome, str):
            return ProviderInvocationResult(content=outcome, model=self.model_name)
        return outcome


class RecordingTelemetry:
    def __init__(self) -> None:
        self.events = []

    def record(self, event) -> None:
        self.events.append(event)
