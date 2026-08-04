import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from backend.ai.factory import LLMFactory
from backend.ai.providers.base import ProviderConfigurationError
from backend.ai.providers.gemini_provider import GeminiProvider
from backend.ai.providers.groq_provider import GroqProvider
from backend.ai.providers.openai_provider import OpenAIProvider
from backend.logic.engine import llm as llm_module
from backend.observability.llm_logging import SafeLegacyLLMLogRecord, emit_safe_llm_log
from backend.ops import logger as logger_module


SENTINELS = (
    "CUSTOMER-CONTENT-9F1", "PROMPT-CONTENT-8E2", "PROVIDER-BODY-7D3",
    "EXCEPTION-BODY-6C4", "TENANT-ID-5B5", "CONVERSATION-ID-4A6",
    "SESSION-ID-397", "API-KEY-288", "AUTHORIZATION-HEADER-179",
    "+919876543210", "private@example.test", "OTP-654321",
    "PAYMENT-REF-0F8", "TOOL-ARG-VALUE-FE9",
)


def _captured_text(caplog, capsys) -> str:
    streams = capsys.readouterr()
    rendered_records = []
    formatter = logger_module.JsonFormatter()
    for record in caplog.records:
        try:
            rendered_records.append(formatter.format(record))
        except Exception:
            rendered_records.append(record.getMessage())
    return "\n".join([streams.out, streams.err, *rendered_records])


def _assert_private_content_absent(text: str) -> None:
    for sentinel in SENTINELS:
        assert sentinel not in text


class _GroqClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


@pytest.mark.anyio
async def test_legacy_provider_success_requests_and_results_are_unchanged(monkeypatch):
    system_prompt = "platform prompt"
    user_message = "customer request"

    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    groq = GroqProvider()
    request = httpx.Request("POST", groq.API_URL)
    response = httpx.Response(
        200,
        json={
            "model": groq.MODEL,
            "choices": [{"message": {"content": "groq success"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
        request=request,
    )
    groq_client = _GroqClient(response)
    client_options = []

    def groq_client_factory(**kwargs):
        client_options.append(kwargs)
        return groq_client

    monkeypatch.setattr(
        "backend.ai.providers.groq_provider.httpx.AsyncClient", groq_client_factory
    )
    groq_result = await groq.generate_response(system_prompt, user_message, 0.25)

    assert client_options == [{"timeout": 30.0}]
    assert len(groq_client.calls) == 1
    args, kwargs = groq_client.calls[0]
    assert args == (groq.API_URL,)
    assert kwargs["headers"] == {
        "Authorization": "Bearer test-groq-key",
        "Content-Type": "application/json",
    }
    assert kwargs["json"] == {
        "model": groq.MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.25,
        "max_tokens": 1024,
    }
    assert groq_result.content == "groq success"
    assert groq_result.usage_metadata["model"] == groq.MODEL

    openai_calls = []

    class SuccessfulOpenAICompletions:
        async def create(self, **kwargs):
            openai_calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="openai success"))],
                model="gpt-success",
            )

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    openai = OpenAIProvider()
    openai._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SuccessfulOpenAICompletions())
    )
    openai_result = await openai.generate_response(system_prompt, user_message, 0.35)
    assert openai_calls == [{
        "model": openai.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.35,
        "max_tokens": 1024,
    }]
    assert openai_result.content == "openai success"
    assert openai_result.usage_metadata["model"] == "gpt-success"

    gemini_calls = []

    class SuccessfulGeminiModel:
        def generate_content(self, prompt, *, generation_config):
            gemini_calls.append((prompt, generation_config))
            return SimpleNamespace(
                text="gemini success",
                usage_metadata={"prompt_token_count": 4, "candidates_token_count": 2},
            )

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    gemini = GeminiProvider()
    gemini._model = SuccessfulGeminiModel()
    gemini_result = await gemini.generate_response(system_prompt, user_message, 0.45)
    assert gemini_calls == [(
        f"{system_prompt}\n\nUser: {user_message}",
        {"temperature": 0.45, "max_output_tokens": 1024},
    )]
    assert gemini_result.content == "gemini success"
    assert gemini_result.usage_metadata["model"] == gemini.model_name


@pytest.mark.anyio
async def test_groq_non_2xx_and_transport_failure_never_log_private_content(
    monkeypatch, caplog, capsys
):
    monkeypatch.setenv("GROQ_API_KEY", SENTINELS[7])
    provider = GroqProvider()
    request = httpx.Request("POST", provider.API_URL)
    response = httpx.Response(500, text=SENTINELS[2], request=request)
    monkeypatch.setattr(
        "backend.ai.providers.groq_provider.httpx.AsyncClient",
        lambda **_kwargs: _GroqClient(response),
    )

    with caplog.at_level(logging.INFO, logger="awa_backend"):
        result = await provider.generate_response(SENTINELS[1], SENTINELS[0])

    assert result.provider_error["status_code"] == 500
    assert "try again" in result.content.lower()
    _assert_private_content_absent(_captured_text(caplog, capsys))

    class ExplodingClient(_GroqClient):
        async def post(self, *_args, **_kwargs):
            raise RuntimeError(SENTINELS[3])

    caplog.clear()
    monkeypatch.setattr(
        "backend.ai.providers.groq_provider.httpx.AsyncClient",
        lambda **_kwargs: ExplodingClient(response),
    )
    with caplog.at_level(logging.INFO, logger="awa_backend"):
        result = await provider.generate_response(SENTINELS[1], SENTINELS[0])

    assert result.provider_error["error_type"] == "provider_error"
    _assert_private_content_absent(_captured_text(caplog, capsys))


class _FailingCompletions:
    async def create(self, **_kwargs):
        raise RuntimeError(" ".join(SENTINELS))


class _FailingGeminiModel:
    def generate_content(self, *_args, **_kwargs):
        raise RuntimeError(" ".join(SENTINELS))


@pytest.mark.anyio
async def test_openai_and_gemini_failures_preserve_fallback_and_redact_logs(
    monkeypatch, caplog, capsys
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    openai = OpenAIProvider()
    openai._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FailingCompletions())
    )
    gemini = GeminiProvider()
    gemini._model = _FailingGeminiModel()

    with caplog.at_level(logging.INFO, logger="awa_backend"):
        openai_result = await openai.generate_response(SENTINELS[1], SENTINELS[0])
        gemini_result = await gemini.generate_response(SENTINELS[1], SENTINELS[0])

    assert "try again" in openai_result.content.lower()
    assert "try again" in gemini_result.content.lower()
    assert openai_result.provider_error["customer_safe"] is True
    assert gemini_result.provider_error["customer_safe"] is True
    _assert_private_content_absent(_captured_text(caplog, capsys))


@pytest.mark.anyio
async def test_legacy_fallback_exhaustion_keeps_order_and_never_logs_exception_body(
    monkeypatch, caplog, capsys
):
    monkeypatch.setattr(llm_module.settings, "AWA_INTEGRATION_MODE", "live")
    monkeypatch.setattr(llm_module.settings, "AGENT_LLM_PROVIDER", "groq")
    attempts = []

    def fail_create_provider(provider_name):
        attempts.append(provider_name)
        raise ProviderConfigurationError(f"{provider_name} {SENTINELS[3]}")

    monkeypatch.setattr(LLMFactory, "create_provider", fail_create_provider)
    ledger = SimpleNamespace(pending_action=None, last_tool_result=None, last_error=None)
    context = SimpleNamespace(
        session_data=SimpleNamespace(
            state=SimpleNamespace(workflow_state="collecting_data", action_ledger=ledger)
        ),
        record_llm_usage=lambda _usage: None,
    )

    with caplog.at_level(logging.INFO, logger="awa_backend"):
        result = await llm_module.run_provider_fallback(
            context, SENTINELS[1], SENTINELS[0]
        )

    assert attempts == ["openai", "gemini"]
    assert "try again" in result.response_text.lower()
    _assert_private_content_absent(_captured_text(caplog, capsys))


def test_legacy_input_trace_omits_content_names_routes_and_raw_context(caplog, capsys):
    trace_token = logger_module._trace_id_ctx.set("safe-trace")
    tenant_token = logger_module._tenant_id_ctx.set(SENTINELS[4])
    try:
        trace = llm_module.build_llm_input_trace(
            context=None,
            provider="openai",
            model="gpt-test",
            integration_mode="test",
            system_instructions=" ".join(SENTINELS),
            tools=[{"function": {"name": SENTINELS[13], "parameters": {"secret": SENTINELS[7]}}}],
            memory_context=SENTINELS[0],
            knowledge_context=SENTINELS[2],
            route_intent="tenant-private-route",
            route_type="tenant-private-path",
            provider_failure={"error_type": "timeout", "body": SENTINELS[3]},
        )
        with caplog.at_level(logging.INFO, logger="awa_backend"):
            llm_module.log_llm_input_trace(trace)
    finally:
        logger_module._trace_id_ctx.reset(trace_token)
        logger_module._tenant_id_ctx.reset(tenant_token)

    assert "tool_names" not in trace
    assert "route_intent" not in trace
    assert "route_type" not in trace
    assert "provider_failure" not in trace
    assert trace["error_code"] == "timeout"
    _assert_private_content_absent(str(trace))
    _assert_private_content_absent(_captured_text(caplog, capsys))


@pytest.mark.parametrize("pathname", [
    "C:/repo/backend/ai/factory.py",
    "C:/repo/backend/ai/client.py",
    "C:/repo/backend/ai/providers/ollama_provider.py",
    "C:/repo/backend/ai/rag_service.py",
    "C:/repo/backend/ai/prompt_registry.py",
    "C:/repo/backend/ai/prompt_resolver.py",
    "C:/repo/backend/logic/engine/history.py",
    "C:/repo/backend/observability/provider_rate_limiter.py",
])
def test_logger_defensively_replaces_unmarked_legacy_llm_records(pathname):
    record = logging.LogRecord(
        name="awa_backend",
        level=logging.ERROR,
        pathname=pathname,
        lineno=1,
        msg=" ".join(SENTINELS),
        args=(),
        exc_info=None,
    )
    logger_module.PIIMasker().filter(record)
    rendered = logger_module.JsonFormatter().format(record)
    payload = json.loads(rendered)

    assert payload["message"] == "legacy_llm_log_redacted"
    assert "tenant_id" not in payload
    assert "trace_id" not in payload
    _assert_private_content_absent(rendered)


def test_legacy_safe_log_contract_rejects_content_and_unkeyed_correlations():
    with pytest.raises(Exception):
        SafeLegacyLLMLogRecord(
            event="legacy_provider_failure",
            provider="groq",
            outcome="error",
            prompt=SENTINELS[1],
        )
    with pytest.raises(Exception):
        SafeLegacyLLMLogRecord(
            event="legacy_provider_failure",
            provider="groq",
            outcome="error",
            tenant_correlation="unkeyed-correlation",
        )


@pytest.mark.anyio
async def test_core_legacy_inference_failure_is_structured_and_content_free(
    monkeypatch, caplog, capsys
):
    import backend.ai.gateway.telemetry as telemetry_module
    from backend.ai.gateway.telemetry import (
        InMemoryLLMTelemetryRecorder,
        StructuredLogLLMTelemetryRecorder,
    )
    from backend.logic.engine import core_agent_gateway as core_gateway_module
    from langchain_core.messages import HumanMessage

    memory = InMemoryLLMTelemetryRecorder()
    structured = StructuredLogLLMTelemetryRecorder()

    class CompositeRecorder:
        def record(self, event):
            memory.record(event)
            structured.record(event)

    monkeypatch.setattr(telemetry_module, "_configured_recorder", CompositeRecorder())
    monkeypatch.setattr(core_gateway_module.settings, "LLM_TELEMETRY_ENABLED", True)
    monkeypatch.setattr(core_gateway_module.settings, "LLM_TELEMETRY_SAMPLE_RATE", 1.0)

    class FailingLegacyModel:
        provider = "groq"
        model_name = "safe-model"

        async def ainvoke(self, _messages):
            raise RuntimeError(" ".join(SENTINELS))

    tenant_token = logger_module._tenant_id_ctx.set(SENTINELS[4])
    trace_token = logger_module._trace_id_ctx.set("safe-core-trace")
    try:
        with caplog.at_level(logging.INFO, logger="awa_backend"):
            with pytest.raises(RuntimeError):
                await core_gateway_module.invoke_core_agent_model(
                    [HumanMessage(content=" ".join(SENTINELS))],
                    [],
                    mode=core_gateway_module.CoreAgentGatewayMode.LEGACY,
                    authoritative_tool_result=False,
                    fallback_text="safe fallback",
                    legacy_model=FailingLegacyModel(),
                )
    finally:
        logger_module._trace_id_ctx.reset(trace_token)
        logger_module._tenant_id_ctx.reset(tenant_token)

    assert len(memory.events) == 1
    assert memory.events[0].outcome.value == "error"
    assert memory.events[0].trace_id == "safe-core-trace"
    serialized = memory.events[0].model_dump_json()
    _assert_private_content_absent(serialized)
    _assert_private_content_absent(_captured_text(caplog, capsys))


def test_cancelled_error_is_not_caught_by_safe_logging_contract():
    assert not isinstance(asyncio.CancelledError(), Exception)


def test_safe_logging_is_fail_open(monkeypatch):
    def fail_log(*_args, **_kwargs):
        raise RuntimeError("logging failed " + SENTINELS[3])

    monkeypatch.setattr(logger_module.logger, "log", fail_log)
    emit_safe_llm_log(
        event="legacy_provider_failure",
        provider="groq",
        outcome="error",
        error_code="unexpected",
    )
