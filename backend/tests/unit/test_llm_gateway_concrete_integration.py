from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from backend.ai.gateway.gateway import LLMGateway
from backend.ai.gateway.models import (
    CanonicalLLMMessage,
    CanonicalToolDefinition,
    LLMFallbackContract,
    LLMPurpose,
    LLMRequest,
    LLMTimeoutClass,
)
from backend.ai.gateway.providers.gemini import GeminiGatewayAdapter
from backend.ai.gateway.providers.groq import GroqGatewayAdapter
from backend.ai.gateway.providers.openai import OpenAIGatewayAdapter
from backend.ai.gateway.registry import LLMProviderRegistry


class Failure(Exception):
    def __init__(self, message="failure", status=None):
        super().__init__(message)
        self.status_code = status
        self.response = SimpleNamespace(status_code=status, headers={}) if status else None


class Client:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def ainvoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _gateway(gemini, groq, openai):
    clients = {"gemini": Client(gemini), "groq": Client(groq), "openai": Client(openai)}
    adapters = {
        "gemini": GeminiGatewayAdapter(model_name="gemini-3.1-flash-lite", api_key="key", client_factory=lambda timeout: clients["gemini"]),
        "groq": GroqGatewayAdapter(model_name="llama-3.3-70b-versatile", api_key="key", client_factory=lambda timeout: clients["groq"]),
        "openai": OpenAIGatewayAdapter(model_name="gpt-4o-mini", api_key="key", client_factory=lambda timeout: clients["openai"]),
    }
    registry = LLMProviderRegistry()
    for name, adapter in adapters.items():
        registry.register(name, adapter)
    return LLMGateway(registry), clients


def _text(**overrides):
    values = dict(
        purpose=LLMPurpose.KNOWLEDGE_ANSWER,
        messages=[CanonicalLLMMessage(role="user", content="question")],
        timeout_class=LLMTimeoutClass.KNOWLEDGE_STANDARD,
        deterministic_fallback=LLMFallbackContract.SAFE_NOT_ENOUGH_INFORMATION,
    )
    values.update(overrides)
    return LLMRequest(**values)


@pytest.mark.asyncio
async def test_concrete_provider_order_rate_limit_and_timeout_chains():
    gateway, clients = _gateway(AIMessage(content="gemini"), AIMessage(content="unused"), AIMessage(content="unused"))
    result = await gateway.invoke(_text())
    assert result.provider == "gemini" and not clients["groq"].calls and not clients["openai"].calls

    gateway, clients = _gateway(Failure("raw", 429), AIMessage(content="groq"), AIMessage(content="unused"))
    result = await gateway.invoke(_text())
    assert result.provider == "groq" and [attempt.normalized_error_code for attempt in result.attempts] == ["rate_limit", None]

    Timeout = type("ProviderTimeout", (Failure,), {})
    gateway, _ = _gateway(Timeout("raw"), Timeout("raw"), AIMessage(content="openai"))
    result = await gateway.invoke(_text())
    assert result.provider == "openai"
    assert [attempt.provider for attempt in result.attempts] == ["gemini", "groq", "openai"]


@pytest.mark.asyncio
async def test_concrete_auth_and_invalid_model_fallback_but_terminal_errors_stop():
    gateway, _ = _gateway(Failure("raw key", 401), AIMessage(content="groq"), AIMessage(content="unused"))
    assert (await gateway.invoke(_text())).provider == "groq"

    gateway, _ = _gateway(Failure("model_not_found raw", 400), AIMessage(content="groq"), AIMessage(content="unused"))
    assert (await gateway.invoke(_text())).provider == "groq"

    gateway, clients = _gateway(Failure("unknown raw", 400), AIMessage(content="unused"), AIMessage(content="unused"))
    stopped = await gateway.invoke(_text())
    assert stopped.error.code == "invalid_request" and not clients["groq"].calls

    gateway, clients = _gateway(Failure("content_filter raw", 400), AIMessage(content="unused"), AIMessage(content="unused"))
    stopped = await gateway.invoke(_text())
    assert stopped.error.code == "safety_rejection" and not clients["groq"].calls


@pytest.mark.asyncio
async def test_concrete_structured_and_tool_protocol_fallbacks():
    class Structured(BaseModel):
        value: int

    gateway, _ = _gateway(AIMessage(content="not json"), AIMessage(content='{"value":2}'), AIMessage(content="unused"))
    structured = await gateway.invoke(LLMRequest(
        purpose=LLMPurpose.FIELD_EXTRACTION,
        messages=[CanonicalLLMMessage(role="user", content="extract")],
        response_schema=Structured,
        timeout_class=LLMTimeoutClass.STRUCTURED_STANDARD,
        deterministic_fallback=LLMFallbackContract.PRESERVE_STATE,
    ))
    assert structured.provider == "groq" and structured.content == Structured(value=2)

    malformed = AIMessage(content="", invalid_tool_calls=[{"id": "bad", "name": "search", "args": "{"}])
    valid = AIMessage(content="", tool_calls=[{"id": "good", "name": "search", "args": {"q": "x"}}])
    gateway, _ = _gateway(malformed, valid, AIMessage(content="unused"))
    tool_result = await gateway.invoke(LLMRequest(
        purpose=LLMPurpose.TOOL_SELECTION,
        messages=[CanonicalLLMMessage(role="user", content="search")],
        tools=[CanonicalToolDefinition(name="search", input_schema={"type": "object"})],
        timeout_class=LLMTimeoutClass.TOOL_SELECTION_STANDARD,
        deterministic_fallback=LLMFallbackContract.NO_TOOL,
    ))
    assert tool_result.provider == "groq" and tool_result.tool_calls[0].id == "good"

    gateway, clients = _gateway(malformed, valid, AIMessage(content="unused"))
    stopped = await gateway.invoke(_text(authoritative_side_effects_occurred=True))
    assert stopped.error.code == "tool_protocol" and not clients["groq"].calls


@pytest.mark.asyncio
async def test_concrete_transient_exhaustion_is_bounded_and_safe():
    gateway, _ = _gateway(Failure("raw", 429), Failure("raw", 503), type("ConnectionError", (Failure,), {})("raw"))
    result = await gateway.invoke(_text())
    assert result.error.code == "all_providers_failed"
    assert result.error.safe_metadata == {"attempt_count": 3}
    assert len(result.attempts) == 3
    assert "raw" not in result.model_dump_json()
