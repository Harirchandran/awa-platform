import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from backend.ai.gateway.errors import (
    LLMAuthenticationError,
    LLMContextLengthError,
    LLMInvalidModelError,
    LLMInvalidRequestError,
    LLMNetworkError,
    LLMProviderServerError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMSafetyRejectionError,
    LLMTimeoutError,
    LLMToolProtocolError,
)
from backend.ai.gateway.gateway import LLMGateway
from backend.ai.gateway.models import (
    CanonicalLLMMessage,
    CanonicalToolCall,
    CanonicalToolDefinition,
    CanonicalToolResult,
    LLMFallbackContract,
    LLMPurpose,
    LLMRequest,
    LLMTimeoutClass,
    ProviderInvocationRequest,
)
from backend.ai.gateway.providers.gemini import GeminiGatewayAdapter
from backend.ai.gateway.providers.groq import GroqGatewayAdapter
from backend.ai.gateway.providers.openai import OpenAIGatewayAdapter
from backend.ai.providers.gemini_chat_adapter import PROVIDER_METADATA_KEY


ADAPTERS = (GeminiGatewayAdapter, GroqGatewayAdapter, OpenAIGatewayAdapter)
MODELS = {
    GeminiGatewayAdapter: "gemini-3.1-flash-lite",
    GroqGatewayAdapter: "llama-3.3-70b-versatile",
    OpenAIGatewayAdapter: "gpt-4o-mini",
}


class FakeClient:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def ainvoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _request(*, messages=None, tools=None, timeout=4.0):
    return ProviderInvocationRequest(
        gateway_request_id="request-1",
        purpose=LLMPurpose.TOOL_SELECTION if tools else LLMPurpose.KNOWLEDGE_ANSWER,
        messages=messages or [CanonicalLLMMessage(role="user", content="hello")],
        safe_context={},
        selected_model=None,
        timeout_seconds=timeout,
        tools=tools or [],
        attempt_number=1,
    )


def _adapter(adapter_type, outcome, *, model=None, key="test-key"):
    client = FakeClient(outcome)
    timeouts = []

    def factory(timeout):
        timeouts.append(timeout)
        return client

    adapter = adapter_type(model_name=model or MODELS[adapter_type], api_key=key, client_factory=factory)
    return adapter, client, timeouts


@pytest.mark.parametrize("adapter_type", ADAPTERS)
@pytest.mark.asyncio
async def test_text_messages_usage_timeout_and_lazy_construction(adapter_type):
    response = AIMessage(
        content="safe answer",
        usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        response_metadata={"model_name": MODELS[adapter_type]},
    )
    adapter, client, timeouts = _adapter(adapter_type, response)
    assert adapter.client_construction_count == 0
    result = await adapter.invoke(_request(messages=[
        CanonicalLLMMessage(role="system", content="system"),
        CanonicalLLMMessage(role="user", content="user"),
        CanonicalLLMMessage(role="assistant", content="assistant"),
    ], timeout=3.5))
    sent, kwargs = client.calls[0]
    assert [type(item) for item in sent] == [SystemMessage, HumanMessage, AIMessage]
    assert [item.content for item in sent] == ["system", "user", "assistant"]
    assert "tools" not in kwargs
    assert timeouts == [3.5]
    assert adapter.client_construction_count == 1
    assert result.content == "safe answer"
    assert result.usage.model_dump() == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5, "estimated": False}


@pytest.mark.parametrize("adapter_type", ADAPTERS)
@pytest.mark.asyncio
async def test_tool_schema_call_and_result_correlation(adapter_type):
    response = AIMessage(content="", tool_calls=[{"id": "call-2", "name": "catalog.search", "args": {"query": "cleaning"}}])
    adapter, client, _ = _adapter(adapter_type, response)
    tool = CanonicalToolDefinition(
        name="catalog.search",
        description="Search catalog",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    messages = [
        CanonicalLLMMessage(role="assistant", tool_calls=[CanonicalToolCall(id="call-1", name="catalog.search", arguments={"query": "prior"})]),
        CanonicalLLMMessage(role="tool", tool_result=CanonicalToolResult(
            tool_call_id="call-1", tool_name="catalog.search", success=True, safe_data={"count": 1}
        )),
        CanonicalLLMMessage(role="user", content="search again"),
    ]
    result = await adapter.invoke(_request(messages=messages, tools=[tool]))
    sent, kwargs = client.calls[0]
    assert isinstance(sent[0], AIMessage) and sent[0].tool_calls[0]["id"] == "call-1"
    assert isinstance(sent[1], ToolMessage) and sent[1].tool_call_id == "call-1"
    assert kwargs["tools"][0]["function"]["name"] == "catalog.search"
    assert result.tool_calls == [CanonicalToolCall(id="call-2", name="catalog.search", arguments={"query": "cleaning"})]


@pytest.mark.parametrize("adapter_type", ADAPTERS)
@pytest.mark.asyncio
async def test_missing_key_and_malformed_tool_calls_are_typed(adapter_type):
    adapter, _, _ = _adapter(adapter_type, AIMessage(content="ok"), key=None)
    with pytest.raises(LLMProviderUnavailableError):
        await adapter.invoke(_request())
    malformed, _, _ = _adapter(adapter_type, AIMessage(content="", invalid_tool_calls=[{"id": "bad", "name": "x", "args": "{"}]))
    with pytest.raises(LLMToolProtocolError):
        await malformed.invoke(_request())


@pytest.mark.asyncio
async def test_groq_rejects_tools_for_unsupported_model_before_client_call():
    adapter, client, _ = _adapter(GroqGatewayAdapter, AIMessage(content="unused"), model="whisper-large-v3")
    with pytest.raises(LLMProviderUnavailableError) as caught:
        await adapter.invoke(_request(tools=[CanonicalToolDefinition(name="search", input_schema={"type": "object"})]))
    assert caught.value.safe_metadata["reason"] == "tools_unsupported"
    assert client.calls == []


@pytest.mark.asyncio
async def test_gemini_signature_is_bound_to_matching_call_and_other_metadata_is_absent():
    signature = "opaque-signature"
    response = AIMessage(
        content="",
        tool_calls=[{"id": "call-1", "name": "search", "args": {}}],
        additional_kwargs={PROVIDER_METADATA_KEY: {
            "provider": "gemini",
            "tool_call_thought_signatures": {"call-1": signature, "other": "must-strip"},
        }},
    )
    adapter, client, _ = _adapter(GeminiGatewayAdapter, response)
    history = CanonicalLLMMessage(
        role="assistant",
        tool_calls=[CanonicalToolCall(id="prior", name="search", arguments={})],
        provider_metadata={"gemini": {"tool_call_thought_signatures": {"prior": "prior-signature"}}},
    )
    result = await adapter.invoke(_request(messages=[history]))
    sent = client.calls[0][0][0]
    assert sent.additional_kwargs[PROVIDER_METADATA_KEY]["tool_call_thought_signatures"] == {"prior": "prior-signature"}
    assert result.provider_metadata == {"gemini": {"tool_call_thought_signatures": {"call-1": signature}}}
    assert signature not in result.content


@pytest.mark.parametrize("adapter_type", (GroqGatewayAdapter, OpenAIGatewayAdapter))
@pytest.mark.asyncio
async def test_non_gemini_adapters_strip_gemini_metadata(adapter_type):
    adapter, client, _ = _adapter(adapter_type, AIMessage(content="ok"))
    history = CanonicalLLMMessage(
        role="assistant",
        tool_calls=[CanonicalToolCall(id="prior", name="search", arguments={})],
        provider_metadata={"gemini": {"tool_call_thought_signatures": {"prior": "secret-signature"}}},
    )
    result = await adapter.invoke(_request(messages=[history]))
    assert client.calls[0][0][0].additional_kwargs == {}
    assert result.provider_metadata == {}


class ProviderFailure(Exception):
    def __init__(self, message="provider failure", *, status=None, code=None):
        super().__init__(message)
        self.status_code = status
        self.code = code
        self.response = SimpleNamespace(status_code=status, headers={"x-request-id": "req-safe"}) if status else None


ERROR_CASES = (
    (ProviderFailure("limited raw body", status=429), LLMRateLimitError),
    (type("ProviderTimeout", (ProviderFailure,), {})("secret prompt"), LLMTimeoutError),
    (type("ConnectionError", (ProviderFailure,), {})("secret prompt"), LLMNetworkError),
    (ProviderFailure("raw server response", status=503), LLMProviderServerError),
    (ProviderFailure("bad key", status=401), LLMAuthenticationError),
    (ProviderFailure("model_not_found raw", status=400), LLMInvalidModelError),
    (ProviderFailure("unknown bad request raw", status=400), LLMInvalidRequestError),
    (ProviderFailure("content_filter raw", status=400), LLMSafetyRejectionError),
    (ProviderFailure("context_length_exceeded raw", status=400), LLMContextLengthError),
)


@pytest.mark.parametrize("adapter_type", ADAPTERS)
@pytest.mark.parametrize("failure,error_type", ERROR_CASES)
@pytest.mark.asyncio
async def test_provider_error_categories_are_safe(adapter_type, failure, error_type):
    adapter, _, _ = _adapter(adapter_type, failure)
    with pytest.raises(error_type) as caught:
        await adapter.invoke(_request())
    serialized = caught.value.to_info().model_dump_json()
    assert "raw" not in serialized and "secret prompt" not in serialized and "bad key" not in serialized
    assert caught.value.safe_metadata["provider"] == adapter.provider_name
    if failure.status_code:
        assert caught.value.safe_metadata["http_status_category"] in {"4xx", "5xx"}


@pytest.mark.asyncio
async def test_gateway_central_parser_and_fail_open_telemetry_with_concrete_adapter():
    class StrictResult(BaseModel):
        value: int

    adapter, _, _ = _adapter(GeminiGatewayAdapter, AIMessage(content='```json\n{"value":7}\n```'))

    class RaisingRecorder:
        def record(self, event):
            raise RuntimeError("telemetry unavailable")

    from backend.ai.gateway.registry import LLMProviderRegistry
    registry = LLMProviderRegistry()
    registry.register("gemini", adapter)
    gateway = LLMGateway(registry, provider_order=("gemini",), telemetry=RaisingRecorder())
    result = await gateway.invoke(LLMRequest(
        purpose=LLMPurpose.FIELD_EXTRACTION,
        messages=[CanonicalLLMMessage(role="user", content="extract")],
        response_schema=StrictResult,
        timeout_class=LLMTimeoutClass.STRUCTURED_STANDARD,
        deterministic_fallback=LLMFallbackContract.PRESERVE_STATE,
    ))
    assert result.content == StrictResult(value=7)
    sent_messages = adapter._client_factory(4.0).calls[0][0]
    assert isinstance(sent_messages[0], SystemMessage)
    assert "JSON Schema" in sent_messages[0].content


@pytest.mark.asyncio
async def test_async_cancellation_propagates():
    class CancelClient:
        async def ainvoke(self, messages, **kwargs):
            raise asyncio.CancelledError

    adapter = OpenAIGatewayAdapter(model_name="gpt-4o-mini", api_key="test", client_factory=lambda timeout: CancelClient())
    with pytest.raises(asyncio.CancelledError):
        await adapter.invoke(_request())
