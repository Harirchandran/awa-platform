from __future__ import annotations

import asyncio
import ast
import json
from pathlib import Path

import pytest

from backend.ai.gateway.errors import LLMInvalidRequestError
from backend.ai.gateway.gateway import LLMGateway
from backend.ai.gateway.models import (
    LLMParseStatus,
    LLMResult,
)
from backend.ai.gateway.parsing import parse_structured_output
from backend.ai.gateway.registry import LLMProviderRegistry
from backend.core.config import settings
from backend.logic.engine.response_localization import (
    LocalizationGatewayPayload,
    _build_localization_gateway_request,
    localize_customer_response,
)
from backend.tests.fakes.fake_llm_gateway import ScriptedLLMProviderAdapter


ROOT = Path(__file__).resolve().parents[3]
BASE = "Please confirm 20 USD for 2030-01-10."
REPLIES = ["Confirm", "Change"]
CONTEXT = {
    "target_language": "hi",
    "workflow": "payment",
    "response_kind": "confirmation",
    "required_terms": ["20", "USD", "2030-01-10"],
}


def _json(text="स्थिर करें 20 USD for 2030-01-10.", replies=None):
    return json.dumps({"response_text": text, "quick_replies": replies if replies is not None else ["पक्का", "बदलें"]})


def _gateway(scripts):
    registry = LLMProviderRegistry()
    adapters = {}
    for provider, outcomes in scripts.items():
        adapter = ScriptedLLMProviderAdapter(provider, outcomes)
        registry.register(provider, adapter)
        adapters[provider] = adapter
    return LLMGateway(registry), adapters


class CapturingGateway:
    def __init__(self, result=None, error=None):
        self.requests = []
        self.result = result
        self.error = error

    async def invoke(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result or LLMResult(
            content=LocalizationGatewayPayload(response_text="स्थिर करें 20 USD for 2030-01-10.", quick_replies=["पक्का", "बदलें"]),
            provider="gemini",
            model="gemini-test",
            latency_ms=1,
            parse_status=LLMParseStatus.SUCCESS,
        )


@pytest.fixture(autouse=True)
def gateway_flags(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False)
    monkeypatch.setattr(settings, "LLM_GATEWAY_SHADOW_MODE", False)


@pytest.mark.asyncio
async def test_legacy_mode_calls_only_legacy_and_never_builds_gateway(monkeypatch):
    calls = []

    async def legacy(payload):
        calls.append(payload)
        return json.loads(_json())

    def forbidden_builder():
        raise AssertionError("gateway must not be constructed")

    result = await localize_customer_response(BASE, REPLIES, CONTEXT, provider=legacy, gateway_factory=forbidden_builder)
    assert result.localized and len(calls) == 1
    assert result.quick_replies == ["पक्का", "बदलें"]


@pytest.mark.asyncio
async def test_legacy_validation_and_failure_contract_remain_fail_closed():
    async def invalid(_payload):
        return {"response_text": "Payment completed successfully.", "quick_replies": REPLIES}

    result = await localize_customer_response(BASE, REPLIES, CONTEXT, provider=invalid)
    assert result.response_text == BASE and result.quick_replies == REPLIES
    assert result.fallback_reason == "required_terms_missing"


@pytest.mark.asyncio
async def test_shadow_success_is_non_authoritative_and_records_metadata_only(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_SHADOW_MODE", True)
    legacy_calls = []
    records = []

    async def legacy(payload):
        legacy_calls.append(payload)
        return {"response_text": f"LEGACY 20 USD 2030-01-10", "quick_replies": ["Legacy A", "Legacy B"]}

    gateway = CapturingGateway()
    state = {"pending": {"amount": 20}}
    before = json.dumps(state, sort_keys=True)
    result = await localize_customer_response(BASE, REPLIES, CONTEXT, provider=legacy, gateway=gateway, shadow_recorder=records.append)
    assert result.response_text.startswith("LEGACY")
    assert result.quick_replies == ["Legacy A", "Legacy B"]
    assert len(legacy_calls) == len(gateway.requests) == len(records) == 1
    assert json.dumps(state, sort_keys=True) == before
    serialized = json.dumps(records[0], sort_keys=True)
    for forbidden in ("LEGACY", "स्थिर", "20 USD", "पक्का"):
        assert forbidden not in serialized
    assert gateway.requests[0].tools == []


@pytest.mark.asyncio
async def test_shadow_gateway_fallback_exhaustion_invalid_candidate_and_recorder_failure(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_SHADOW_MODE", True)

    async def legacy(_payload):
        return {"response_text": "LEGACY 20 USD 2030-01-10", "quick_replies": REPLIES}

    gateway, adapters = _gateway({"gemini": ["rate_limit"], "groq": [_json()], "openai": ["unused"]})
    result = await localize_customer_response(BASE, REPLIES, CONTEXT, provider=legacy, gateway=gateway)
    assert result.response_text.startswith("LEGACY")
    assert len(adapters["gemini"].calls) == len(adapters["groq"].calls) == 1
    assert not adapters["openai"].calls

    exhausted, adapters = _gateway({"gemini": ["timeout"], "groq": ["timeout"], "openai": ["network"]})
    result = await localize_customer_response(BASE, REPLIES, CONTEXT, provider=legacy, gateway=exhausted)
    assert result.response_text.startswith("LEGACY") and all(len(adapter.calls) == 1 for adapter in adapters.values())

    invalid = CapturingGateway(LLMResult(
        content=LocalizationGatewayPayload(response_text="Payment completed successfully.", quick_replies=REPLIES),
        provider="gemini", model="test", latency_ms=1, parse_status=LLMParseStatus.SUCCESS,
    ))
    result = await localize_customer_response(
        BASE, REPLIES, CONTEXT, provider=legacy, gateway=invalid,
        shadow_recorder=lambda metadata: (_ for _ in ()).throw(RuntimeError("recorder down")),
    )
    assert result.response_text.startswith("LEGACY") and result.quick_replies == REPLIES

    invalid_replies = CapturingGateway(LLMResult(
        content=LocalizationGatewayPayload(response_text="localized 20 USD 2030-01-10", quick_replies=["Only one"]),
        provider="gemini", model="test", latency_ms=1, parse_status=LLMParseStatus.SUCCESS,
    ))
    records = []
    result = await localize_customer_response(
        BASE, REPLIES, CONTEXT, provider=legacy, gateway=invalid_replies, shadow_recorder=records.append,
    )
    assert result.response_text.startswith("LEGACY") and result.quick_replies == REPLIES
    assert records[0]["quick_reply_shape_passed"] is False


@pytest.mark.asyncio
async def test_authoritative_provider_fallback_chains_and_structured_parse(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    legacy_calls = []

    async def legacy(_payload):
        legacy_calls.append(1)
        raise AssertionError("legacy must not run")

    cases = [
        ({"gemini": [_json()], "groq": ["unused"], "openai": ["unused"]}, [1, 0, 0]),
        ({"gemini": ["rate_limit"], "groq": [_json()], "openai": ["unused"]}, [1, 1, 0]),
        ({"gemini": ["timeout"], "groq": ["timeout"], "openai": [_json()]}, [1, 1, 1]),
        ({"gemini": ["malformed_structured_output"], "groq": [_json()], "openai": ["unused"]}, [1, 1, 0]),
    ]
    for scripts, counts in cases:
        gateway, adapters = _gateway(scripts)
        result = await localize_customer_response(BASE, REPLIES, CONTEXT, provider=legacy, gateway=gateway)
        assert result.localized and result.response_text.startswith("स्थिर")
        assert [len(adapters[name].calls) for name in ("gemini", "groq", "openai")] == counts
    assert legacy_calls == []


@pytest.mark.asyncio
async def test_authoritative_terminal_and_exhaustion_fail_to_original_without_legacy(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)

    async def forbidden(_payload):
        raise AssertionError("legacy must not run")

    exhausted, _ = _gateway({"gemini": ["timeout"], "groq": ["network"], "openai": ["rate_limit"]})
    assert (await localize_customer_response(BASE, REPLIES, CONTEXT, provider=forbidden, gateway=exhausted)).response_text == BASE

    safety, adapters = _gateway({"gemini": ["safety_rejection"], "groq": ["unused"], "openai": ["unused"]})
    assert (await localize_customer_response(BASE, REPLIES, CONTEXT, provider=forbidden, gateway=safety)).response_text == BASE
    assert not adapters["groq"].calls and not adapters["openai"].calls

    invalid, adapters = _gateway({
        "gemini": [LLMInvalidRequestError(safe_metadata={"reason": "invalid"})],
        "groq": ["unused"], "openai": ["unused"],
    })
    assert (await localize_customer_response(BASE, REPLIES, CONTEXT, provider=forbidden, gateway=invalid)).response_text == BASE
    assert not adapters["groq"].calls


@pytest.mark.asyncio
async def test_authoritative_deterministic_validation_and_quick_reply_failure_return_original(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    bad_fact = CapturingGateway(LLMResult(
        content=LocalizationGatewayPayload(response_text="localized without required facts", quick_replies=REPLIES),
        provider="gemini", model="test", latency_ms=1, parse_status=LLMParseStatus.SUCCESS,
    ))
    result = await localize_customer_response(BASE, REPLIES, CONTEXT, gateway=bad_fact)
    assert result.response_text == BASE and result.quick_replies == REPLIES

    bad_replies = CapturingGateway(LLMResult(
        content=LocalizationGatewayPayload(response_text="localized 20 USD 2030-01-10", quick_replies=["Only one"]),
        provider="gemini", model="test", latency_ms=1, parse_status=LLMParseStatus.SUCCESS,
    ))
    result = await localize_customer_response(BASE, REPLIES, CONTEXT, gateway=bad_replies)
    assert result.response_text == BASE and result.quick_replies == REPLIES


@pytest.mark.asyncio
async def test_bypasses_do_not_construct_gateway_and_cancellation_propagates(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    calls = []

    def factory():
        calls.append(1)
        raise AssertionError("bypass constructed gateway")

    english = await localize_customer_response("Hello", [], {"target_language": "en"}, gateway_factory=factory)
    empty = await localize_customer_response("", [], {"target_language": "hi"}, gateway_factory=factory)
    assert english.response_text == "Hello" and not empty.localized and calls == []

    cancelling = CapturingGateway(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await localize_customer_response(BASE, REPLIES, CONTEXT, gateway=cancelling)


def test_localization_schema_and_central_parser_strictness():
    valid = parse_structured_output(_json(), LocalizationGatewayPayload)
    fenced = parse_structured_output(f"```json\n{_json()}\n```", LocalizationGatewayPayload)
    assert valid.response_text and fenced.quick_replies

    invalid_payloads = [
        {"quick_replies": []},
        {"response_text": "ok", "quick_replies": "Confirm"},
        {"response_text": "ok", "quick_replies": [], "extra": True},
        {"response_text": "x" * 1801, "quick_replies": []},
        {"response_text": "ok", "quick_replies": [str(i) for i in range(11)]},
        {"response_text": "ok", "quick_replies": ["x" * 25]},
        {"response_text": "ok", "quick_replies": [{"label": "Confirm"}]},
        {"response_text": "<script>alert(1)</script>", "quick_replies": []},
        {"response_text": "ok", "quick_replies": [], "provider": "gemini", "model": "x"},
        {"response_text": "ok", "quick_replies": [], "success": True},
    ]
    for payload in invalid_payloads:
        with pytest.raises(Exception):
            parse_structured_output(payload, LocalizationGatewayPayload)


def test_gateway_request_is_bounded_tool_free_and_excludes_execution_sentinels():
    sentinel_values = [
        "+15551234567", "customer@example.com", "42 Secret Street", "OTP 123456",
        "payment_data_secret", "pending_action_argument", "provider-api-key",
    ]
    payload = {
        "base_text": BASE,
        "quick_replies": REPLIES,
        "target_language": "hi",
        "workflow": "payment",
        "response_kind": "confirmation",
        "safe_facts": {"phone": sentinel_values[0], "email": sentinel_values[1], "address": sentinel_values[2], "otp": sentinel_values[3], "payment": sentinel_values[4]},
        "required_terms": ["20", "USD", sentinel_values[1], sentinel_values[3]],
        "protected_terms": [sentinel_values[0], "public-reference"],
        "pending_action": {"amount": sentinel_values[5]},
        "provider_credentials": sentinel_values[6],
    }
    request = _build_localization_gateway_request(payload)
    serialized = json.dumps(request.safe_context, sort_keys=True) + "\n" + "\n".join(message.content for message in request.messages)
    assert request.purpose.value == "localization"
    assert request.tools == [] and len(request.messages) == 2
    assert request.response_schema is LocalizationGatewayPayload
    assert request.timeout_class.value == "localization_fast"
    for sentinel in sentinel_values:
        assert sentinel not in serialized
    assert "ToolRegistry" not in serialized and "pending_action" not in serialized and "safe_facts" not in serialized


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_allowlisted_semantic_and_core_migrations_are_only_active_gateway_callers():
    logic_root = ROOT / "backend/logic"
    gateway_importers = []
    for path in logic_root.rglob("*.py"):
        if any(name.startswith("backend.ai.gateway") for name in _imports(path)):
            gateway_importers.append(path.relative_to(ROOT).as_posix())
    assert gateway_importers == [
        "backend/logic/engine/core_agent_gateway.py",
        # Approved Iteration 2 dynamic configured-turn semantic contract:
        # extract_global_workflow_turn is the non-authoritative complete-turn
        # extractor that calls only backend.ai.gateway.runtime, has no legacy
        # provider fallback, executes no tools, and never mutates state. It is
        # the intentionally allowlisted semantic migration created in
        # Iteration 2.
        "backend/logic/engine/field_extraction.py",
        "backend/logic/engine/field_extraction_gateway.py",
        "backend/logic/engine/pending_decision_gateway.py",
        "backend/logic/engine/response_composer.py",
        "backend/logic/engine/response_localization.py",
        "backend/logic/engine/review_extraction_gateway.py",
        "backend/logic/engine/turn_interpretation_gateway.py",
    ]
    for relative in (
        "backend/logic/engine/field_extraction.py",
        "backend/logic/engine/pending_decision.py",
        "backend/logic/engine/core.py",
    ):
        assert "backend.logic.engine.llm" in _imports(ROOT / relative)
    for path in (ROOT / "backend/domains").rglob("*.py"):
        assert not any(name.startswith("backend.ai.gateway") for name in _imports(path)), path


def test_turn_interpretation_gateway_forbidden_dependencies():
    path = ROOT / "backend/logic/engine/turn_interpretation_gateway.py"
    imported = _imports(path)
    forbidden = {
        "backend.logic.engine.core",
        "backend.logic.engine.field_extraction",
        "backend.logic.engine.pending_decision",
        "backend.logic.engine.canonical_control",
        "backend.tools",
        "backend.db",
        "backend.pipeline",
        "frontend",
    }
    for name in imported:
        assert not any(name.startswith(p) for p in forbidden), f"Forbidden import {name} in turn_interpretation_gateway.py"


def test_runtime_gateway_cache_is_bounded_and_explicitly_resettable(monkeypatch):
    import backend.ai.gateway.runtime as runtime

    built = []
    first = object()
    second = object()
    outcomes = iter((first, second))

    def build(_settings):
        built.append(1)
        return next(outcomes)

    monkeypatch.setattr(runtime, "build_llm_gateway", build)
    runtime.reset_runtime_llm_gateway()
    try:
        assert runtime.get_runtime_llm_gateway() is first
        assert runtime.get_runtime_llm_gateway() is first
        assert built == [1]
        runtime.reset_runtime_llm_gateway()
        assert runtime.get_runtime_llm_gateway() is second
        assert built == [1, 1]
    finally:
        runtime.reset_runtime_llm_gateway()
