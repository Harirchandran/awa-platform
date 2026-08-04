from __future__ import annotations

import asyncio
import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.ai.gateway.models import LLMParseStatus, LLMResult
from backend.core.config import Settings, settings
from backend.logic.engine.response_composer import (
    ComposedCustomerResponse,
    ResponseComposerMode,
    ResponseCompositionInput,
    ResponseKind,
    build_response_composer_request,
    compose_customer_response,
    resolve_response_composer_mode,
    response_kind_policy,
    validate_composed_candidate,
)


BASE = "Please confirm the booking for Cleaning at 4 PM. Reference ABC123."
REPLIES = ["Confirm", "Change", "Cancel"]


def _source(**updates):
    values = {
        "base_text": BASE,
        "response_kind": ResponseKind.PENDING_CONFIRMATION,
        "safe_facts": {"workflow": "booking"},
        "quick_replies": REPLIES,
        "required_terms": ["confirm"],
        "protected_terms": ["Cleaning", "ABC123", *REPLIES],
        "protected_numeric_tokens": ["4 PM"],
        "allow_completion_claims": False,
        "allow_failure_claims": False,
        "target_language": "en",
    }
    values.update(updates)
    return ResponseCompositionInput(**values)


def _result(payload, *, provider="gemini", error=None):
    return LLMResult(
        content=json.dumps(payload) if isinstance(payload, dict) else payload,
        provider=provider,
        model=f"{provider}-test",
        attempts=[],
        latency_ms=1,
        parse_status=LLMParseStatus.NOT_REQUESTED,
        error=error,
    )


class CapturingGateway:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        return self.result


@pytest.fixture(autouse=True)
def composer_flags(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False)
    monkeypatch.setattr(settings, "LLM_GATEWAY_RESPONSE_COMPOSER_MODE", "legacy")


def test_mode_global_kill_switch_default_and_unknown_rejection():
    base = Settings(_env_file=None)
    assert resolve_response_composer_mode(base) is ResponseComposerMode.AUTHORITATIVE
    assert resolve_response_composer_mode(base.model_copy(update={
        "LLM_GATEWAY_ENABLED": False,
        "LLM_GATEWAY_RESPONSE_COMPOSER_MODE": "authoritative",
    })) is ResponseComposerMode.LEGACY
    assert resolve_response_composer_mode(base.model_copy(update={
        "LLM_GATEWAY_ENABLED": True,
        "LLM_GATEWAY_RESPONSE_COMPOSER_MODE": "shadow",
    })) is ResponseComposerMode.SHADOW
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LLM_GATEWAY_RESPONSE_COMPOSER_MODE="unknown")


def test_response_kinds_are_strict_and_unknown_fails_closed():
    assert response_kind_policy("missing_field").eligible is True
    assert response_kind_policy("security_refusal") is None
    with pytest.raises(ValidationError):
        _source(response_kind="unknown")
    for safe_facts in ({"otp": "123456"}, {"action_nonce": "hidden"}, {"provider_id": "hidden"}):
        with pytest.raises(ValidationError):
            _source(safe_facts=safe_facts)


def test_request_is_bounded_tool_free_history_free_and_side_effect_aware():
    request = build_response_composer_request(_source(), authoritative_side_effects_occurred=True)
    assert request.purpose.value == "response_composition"
    assert request.tools == []
    assert request.response_schema is None
    assert len(request.messages) == 2
    assert request.timeout_class.value == "composition_standard"
    assert request.deterministic_fallback.value == "original_backend_text"
    assert request.authoritative_side_effects_occurred is True
    serialized = "\n".join(message.content for message in request.messages)
    for prohibited in ("ToolRegistry", "conversation_history", "pending_action", "api_key", "OTP 123456"):
        assert prohibited not in serialized


@pytest.mark.asyncio
async def test_legacy_and_global_disable_make_zero_calls(monkeypatch):
    gateway = CapturingGateway(exc=AssertionError("must not call"))
    result = await compose_customer_response(_source(), gateway=gateway)
    assert result.response_text == BASE and result.quick_replies == REPLIES
    assert gateway.requests == []

    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    result = await compose_customer_response(_source(), gateway=gateway)
    assert result.response_text == BASE and gateway.requests == []


@pytest.mark.asyncio
async def test_shadow_calls_once_records_bounded_metadata_and_returns_base(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_RESPONSE_COMPOSER_MODE", "shadow")
    gateway = CapturingGateway(_result({
        "response_text": "Could you confirm Cleaning at 4 PM? Reference ABC123.",
        "quick_replies": REPLIES,
    }))
    records = []
    result = await compose_customer_response(_source(), gateway=gateway, shadow_recorder=records.append)
    assert result.response_text == BASE and result.quick_replies == REPLIES
    assert len(gateway.requests) == len(records) == 1
    rendered = json.dumps(records[0])
    for secret_value in ("Cleaning", "ABC123", "4 PM", "Confirm"):
        assert secret_value not in rendered
    assert set(records[0]) == {
        "response_kind", "gateway_provider", "gateway_fallback_used", "gateway_parse_status",
        "candidate_valid", "response_hash_equal", "response_length_bucket_equal",
        "quick_reply_count_equal", "required_terms_preserved", "protected_terms_preserved",
        "completion_claim_valid", "failure_claim_valid",
    }


@pytest.mark.asyncio
async def test_authoritative_accepts_valid_wording_only(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_RESPONSE_COMPOSER_MODE", "authoritative")
    gateway = CapturingGateway(_result({
        "response_text": "Please confirm Cleaning at 4 PM using reference ABC123.",
        "quick_replies": REPLIES,
    }))
    result = await compose_customer_response(_source(), gateway=gateway)
    assert result.composed is True
    assert result.response_text.startswith("Please confirm Cleaning")
    assert result.quick_replies == REPLIES


@pytest.mark.asyncio
async def test_provider_parse_schema_terminal_and_cancellation_behavior(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_RESPONSE_COMPOSER_MODE", "authoritative")
    invalids = [
        "{not-json",
        {"response_text": "ok", "quick_replies": REPLIES, "success": True},
        {"response_text": "ok", "quick_replies": REPLIES, "state_updates": {}},
        {"response_text": "<b>confirm</b>", "quick_replies": REPLIES},
    ]
    for payload in invalids:
        result = await compose_customer_response(_source(), gateway=CapturingGateway(_result(payload)))
        assert result.response_text == BASE and result.quick_replies == REPLIES
    failed = await compose_customer_response(_source(), gateway=CapturingGateway(exc=RuntimeError("provider")))
    assert failed.response_text == BASE
    with pytest.raises(asyncio.CancelledError):
        await compose_customer_response(_source(), gateway=CapturingGateway(exc=asyncio.CancelledError()))


@pytest.mark.parametrize("text", [
    "Your booking is confirmed for Cleaning at 4 PM. Reference ABC123.",
    "Cleaning at 4 PM is booked. Reference ABC123. Please confirm.",
    "Please confirm Cleaning at 4 PM. Reference ABC123. OTP verified.",
    "Please confirm Cleaning at 4 PM. Reference ABC123. tool executed.",
    "Please confirm Cleaning at 4 PM. Reference ABC123. success=true",
    "Please confirm Cleaning at 5 PM. Reference ABC123.",
    "Please confirm Cleaning at 4 PM. Reference XYZ999.",
    "Please confirm Cleaning at 4 PM for ₹1,250. Reference ABC123.",
    "Please confirm Cleaning at 4 PM. Reference ABC123. A confirmation email was sent.",
])
def test_injection_claim_numeric_and_reference_changes_are_rejected(text):
    try:
        candidate = ComposedCustomerResponse(response_text=text, quick_replies=REPLIES)
    except ValidationError:
        return
    validation = validate_composed_candidate(_source(), candidate)
    assert validation.candidate_valid is False


@pytest.mark.parametrize("replies", [
    ["Confirm", "Change"],
    ["Change", "Confirm", "Cancel"],
    ["Confirm", "Change", "Cancel", "Pay now"],
    ["Confirm", "Cancel", "Change"],
])
def test_quick_reply_count_order_add_remove_and_semantics_are_rejected(replies):
    validation = validate_composed_candidate(
        _source(protected_terms=["Cleaning", "ABC123"]),
        ComposedCustomerResponse(
            response_text="Please confirm Cleaning at 4 PM. Reference ABC123.",
            quick_replies=replies,
        ),
    )
    assert validation.candidate_valid is False


def test_output_schema_rejects_oversize_html_markdown_controls_and_extra_fields():
    invalids = [
        {"response_text": "x" * 1801, "quick_replies": []},
        {"response_text": "[click](https://example.com)", "quick_replies": []},
        {"response_text": "execute=true", "quick_replies": []},
        {"response_text": "ok", "quick_replies": ["x" * 25]},
        {"response_text": "ok", "quick_replies": [], "provider": "gemini"},
    ]
    for payload in invalids:
        with pytest.raises(Exception):
            ComposedCustomerResponse.model_validate(payload)


def test_tool_success_and_failure_truth_permissions():
    success_source = _source(
        base_text="I've placed the order. Total USD 20.",
        response_kind=ResponseKind.TOOL_SUCCESS,
        safe_facts={"workflow": "order", "total_amount": 20, "currency": "USD"},
        quick_replies=[], required_terms=["USD 20"], protected_terms=["USD 20"],
        protected_numeric_tokens=["20"], allow_completion_claims=True,
    )
    assert validate_composed_candidate(
        success_source,
        ComposedCustomerResponse(response_text="The order was placed. Total USD 20.", quick_replies=[]),
    ).candidate_valid
    assert not validate_composed_candidate(
        success_source,
        ComposedCustomerResponse(response_text="The order failed. Total USD 20.", quick_replies=[]),
    ).candidate_valid

    failure_source = _source(
        base_text="The item is unavailable. Nothing was completed.",
        response_kind=ResponseKind.TOOL_FAILURE,
        safe_facts={"workflow": "order", "failure_message": "The item is unavailable."},
        quick_replies=[], required_terms=[], protected_terms=[], protected_numeric_tokens=[],
        allow_failure_claims=True,
    )
    assert validate_composed_candidate(
        failure_source,
        ComposedCustomerResponse(response_text="The item is unavailable, so the order failed.", quick_replies=[]),
    ).candidate_valid
    assert not validate_composed_candidate(
        failure_source,
        ComposedCustomerResponse(response_text="The order was created successfully.", quick_replies=[]),
    ).candidate_valid


def test_payment_link_success_never_authorizes_paid_claim():
    source = _source(
        base_text="I've created the payment link for INR 500.",
        response_kind=ResponseKind.TOOL_SUCCESS,
        safe_facts={"workflow": "payments", "amount": 500, "currency": "INR", "status": "sent"},
        quick_replies=[], required_terms=["INR 500"], protected_terms=["INR 500"],
        protected_numeric_tokens=["500"], allow_completion_claims=True,
    )
    validation = validate_composed_candidate(
        source,
        ComposedCustomerResponse(response_text="The INR 500 payment link is ready and paid.", quick_replies=[]),
    )
    assert validation.candidate_valid is False


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_composer_architecture_has_no_execution_state_persistence_or_domain_imports():
    import backend.logic.engine.response_composer as module

    path = Path(module.__file__)
    source = path.read_text(encoding="utf-8")
    imports = _imports(path)
    assert "ToolRegistry" not in source
    assert "execute_tool" not in source
    assert "ExecutionContext" not in source
    assert not any(name.startswith("backend.db") for name in imports)
    assert not any(name.startswith("backend.domains") for name in imports)
    assert not any(name.startswith("backend.logic.engine.core") for name in imports)


@pytest.mark.asyncio
async def test_actual_core_hook_composes_before_localization_and_bypasses_security_and_general(monkeypatch):
    from backend.logic.engine import core as core_module
    from backend.logic.engine.core import AgenticEngine, WorkflowResult
    from backend.tests.unit.test_conversation_ux import make_context

    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_RESPONSE_COMPOSER_MODE", "authoritative")
    gateway = CapturingGateway(_result({
        "response_text": "Please share the booking time.",
        "quick_replies": [],
    }))
    monkeypatch.setattr("backend.ai.gateway.runtime.get_runtime_llm_gateway", lambda: gateway)
    localization_inputs = []

    async def localize(text, replies, context):
        localization_inputs.append((text, replies, context))
        from backend.logic.engine.response_localization import LocalizedCustomerResponse
        return LocalizedCustomerResponse(text, replies, "en", False)

    monkeypatch.setattr(core_module, "localize_customer_response", localize)
    engine = object.__new__(AgenticEngine)

    async def missing(_context):
        return WorkflowResult(
            response_text="What time would you like for the booking?",
            prompt_focus_workflow="booking",
            prompt_focus_fields=["preferred_time"],
            prompt_focus_kind="missing_fields",
        )

    monkeypatch.setattr(engine, "_execute_step_inner", missing)
    context = make_context(message={"text": "Book Cleaning"})
    context.session_data.current_module = "booking"
    context.session_data.state.action_ledger.current_task = "booking"
    result = await engine.execute_step(context)
    assert result.response_text == "Please share the booking time."
    assert len(gateway.requests) == 1
    assert localization_inputs[0][0] == "Please share the booking time."

    gateway.requests.clear()

    async def otp(_context):
        return WorkflowResult(response_text="OTP verification failed. Please try again.")

    monkeypatch.setattr(engine, "_execute_step_inner", otp)
    await engine.execute_step(context)
    assert gateway.requests == []

    async def general(_context):
        return WorkflowResult(response_text="A knowledge answer.", composer_eligible=False)

    monkeypatch.setattr(engine, "_execute_step_inner", general)
    await engine.execute_step(context)
    assert gateway.requests == []
