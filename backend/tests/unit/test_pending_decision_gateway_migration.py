from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from backend.ai.gateway.errors import LLMInvalidRequestError
from backend.ai.gateway.gateway import LLMGateway
from backend.ai.gateway.models import (
    CanonicalToolCall,
    LLMParseStatus,
    LLMResult,
    ProviderInvocationResult,
)
from backend.ai.gateway.registry import LLMProviderRegistry
from backend.core.config import Settings, settings
from backend.core.context import (
    ConversationSession,
    ExecutionContext,
    SessionState,
    Tenant,
    TenantConfig,
    User,
)
from backend.core.states import ConversationState
from backend.logic.engine.pending_decision import PendingActionDecision, decide_pending_action
from backend.logic.engine.pending_decision_gateway import (
    PendingDecisionGatewayPayload,
    build_pending_decision_gateway_request,
    resolve_pending_decision_gateway_mode,
)
from backend.tests.fakes.fake_llm_gateway import ScriptedLLMProviderAdapter


def _context(text: str = "Yes, confirm it.") -> ExecutionContext:
    tenant_id = uuid4()
    state = SessionState(workflow_state=ConversationState.COLLECTING_DATA)
    state.prepare_pending_action(
        tool_key="booking.execute",
        args={
            "service_name": "Dental Cleaning",
            "booking_date": "2026-07-20",
            "booking_time": "16:00",
            "customer_name": "Private Person",
            "amount": 999,
        },
        summary="Booking for Private Person at 16:00",
        requires_confirmation=True,
        requires_verified_customer=True,
        verification_action="booking_commitment",
    )
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Tenant", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+10000000000", name="Private Person"),
        session_data=ConversationSession(
            id=uuid4(), tenant_id=tenant_id, conversation_id=uuid4(), config_version=1,
            current_module="booking", state=state,
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={"language": "en"}),
        incoming_message={"text": text, "channel_type": "awa_chat", "detected_language": "en"},
    )


class LegacyLLM:
    def __init__(self, content: str | None = None):
        self.content = content or json.dumps({
            "action": "cancel", "confidence": 0.91, "field_updates": {},
            "clarification": None,
        })
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return AIMessage(content=self.content)


class CapturingGateway:
    def __init__(self, payload=None, *, fail: BaseException | None = None, result=None):
        self.payload = payload or PendingDecisionGatewayPayload(
            action="confirm", confidence=0.97
        )
        self.fail = fail
        self.result = result
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        if self.fail:
            raise self.fail
        if self.result is not None:
            return self.result
        return LLMResult(
            content=self.payload, provider="gemini", model="test-model", attempts=[],
            latency_ms=1, parse_status=LLMParseStatus.SUCCESS,
        )


def _gateway(scripts):
    registry = LLMProviderRegistry()
    adapters = {}
    for provider in ("gemini", "groq", "openai"):
        adapter = ScriptedLLMProviderAdapter(provider, scripts.get(provider, ["unused"]))
        registry.register(provider, adapter)
        adapters[provider] = adapter
    return LLMGateway(registry), adapters


def _gateway_json(action="confirm", confidence=0.96, field_updates=None, clarification=None):
    return json.dumps({
        "action": action,
        "confidence": confidence,
        "field_updates": field_updates or {},
        "clarification": clarification,
    })


def test_rollout_defaults_modes_and_unknown_value_rejected():
    assert Settings(_env_file=None).LLM_GATEWAY_ENABLED is True
    assert Settings(_env_file=None).LLM_GATEWAY_PENDING_DECISION_MODE == "authoritative"
    assert resolve_pending_decision_gateway_mode(Settings(
        LLM_GATEWAY_ENABLED=False,
        LLM_GATEWAY_PENDING_DECISION_MODE="authoritative",
    )).value == "legacy"
    assert resolve_pending_decision_gateway_mode(Settings(
        LLM_GATEWAY_ENABLED=True,
        LLM_GATEWAY_PENDING_DECISION_MODE="shadow",
    )).value == "shadow"
    with pytest.raises(ValidationError):
        Settings(LLM_GATEWAY_PENDING_DECISION_MODE="unsafe")


@pytest.mark.anyio
async def test_legacy_modes_call_only_legacy_once_and_preserve_coercion(monkeypatch):
    legacy = LegacyLLM(json.dumps({
        "action": "nonsense", "confidence": 9, "field_updates": "bad",
        "clarification": "x" * 600,
    }))
    gateway = CapturingGateway()
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False)
    monkeypatch.setattr(settings, "LLM_GATEWAY_PENDING_DECISION_MODE", "authoritative")
    result = await decide_pending_action(_context(), legacy_llm=legacy, gateway=gateway)
    assert result == PendingActionDecision("unknown", 1.0, {}, "x" * 500)
    assert len(legacy.calls) == 1 and gateway.requests == []

    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_PENDING_DECISION_MODE", "legacy")
    await decide_pending_action(_context(), legacy_llm=legacy, gateway=gateway)
    assert len(legacy.calls) == 2 and gateway.requests == []


@pytest.mark.anyio
async def test_shadow_is_non_authoritative_and_telemetry_contains_names_only(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_PENDING_DECISION_MODE", "shadow")
    legacy = LegacyLLM()
    gateway = CapturingGateway(PendingDecisionGatewayPayload(
        action="change", confidence=0.4, field_updates={"booking_time": "18:00"}
    ))
    records = []
    context = _context("Private Person asks to change 16:00")
    before = context.session_data.state.model_copy(deep=True)
    result = await decide_pending_action(
        context, legacy_llm=legacy, gateway=gateway, shadow_recorder=records.append
    )
    assert result.action == "cancel"
    assert context.session_data.state == before
    assert len(legacy.calls) == len(gateway.requests) == len(records) == 1
    serialized = json.dumps(records[0], sort_keys=True)
    assert "Private Person" not in serialized and "18:00" not in serialized
    assert set(records[0]) == {
        "legacy_success", "gateway_success", "gateway_provider", "gateway_fallback_used",
        "gateway_parse_status", "legacy_decision", "gateway_decision", "decision_equal",
        "clarification_presence_equal", "confidence_bucket_equal", "field_name_set_equal",
    }


@pytest.mark.anyio
async def test_shadow_gateway_and_recorder_failures_are_fail_open(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_PENDING_DECISION_MODE", "shadow")
    legacy = LegacyLLM()
    result = await decide_pending_action(
        _context(), legacy_llm=legacy, gateway=CapturingGateway(fail=RuntimeError("down")),
        shadow_recorder=lambda _: (_ for _ in ()).throw(RuntimeError("recorder down")),
    )
    assert result.action == "cancel" and len(legacy.calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["confirm", "cancel", "change", "continue", "tangent", "unknown"])
async def test_authoritative_preserves_all_decision_values_without_mutation(monkeypatch, action):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_PENDING_DECISION_MODE", "authoritative")
    updates = {"booking_time": "18:00"} if action == "change" else {}
    gateway = CapturingGateway(PendingDecisionGatewayPayload(
        action=action, confidence=0.97, field_updates=updates
    ))
    legacy = LegacyLLM()
    context = _context()
    before = context.session_data.state.model_copy(deep=True)
    result = await decide_pending_action(context, legacy_llm=legacy, gateway=gateway)
    assert result.action == action and result.field_updates == updates
    assert context.session_data.state == before
    assert legacy.calls == [] and len(gateway.requests) == 1


@pytest.mark.anyio
async def test_authoritative_request_reaches_gemini_with_policy_aligned_timeout(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_PENDING_DECISION_MODE", "authoritative")
    gateway, adapters = _gateway({"gemini": [_gateway_json()]})
    result = await decide_pending_action(_context(), gateway=gateway, legacy_llm=LegacyLLM())
    assert result.action == "confirm"
    assert [len(adapters[name].calls) for name in ("gemini", "groq", "openai")] == [1, 0, 0]
    call = adapters["gemini"].calls[0]
    assert call.purpose == "pending_action_decision" and call.tool_count == 0


@pytest.mark.anyio
async def test_authoritative_provider_fallback_and_terminal_failures(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_PENDING_DECISION_MODE", "authoritative")
    valid = _gateway_json()
    cases = [
        ({"gemini": ["rate_limit"], "groq": [valid]}, [1, 1, 0], "confirm"),
        ({"gemini": ["timeout"], "groq": ["timeout"], "openai": [valid]}, [1, 1, 1], "confirm"),
        ({"gemini": ["malformed_structured_output"], "groq": [valid]}, [1, 1, 0], "confirm"),
        ({"gemini": ["timeout"], "groq": ["network"], "openai": ["rate_limit"]}, [1, 1, 1], "unknown"),
        ({"gemini": ["safety_rejection"]}, [1, 0, 0], "unknown"),
        ({"gemini": [LLMInvalidRequestError(safe_metadata={"reason": "invalid"})]}, [1, 0, 0], "unknown"),
    ]
    for scripts, counts, expected in cases:
        gateway, adapters = _gateway(scripts)
        result = await decide_pending_action(_context(), gateway=gateway, legacy_llm=LegacyLLM())
        assert result.action == expected
        assert [len(adapters[name].calls) for name in ("gemini", "groq", "openai")] == counts


@pytest.mark.anyio
async def test_tool_call_output_is_rejected_without_legacy_or_dispatch(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_PENDING_DECISION_MODE", "authoritative")
    tool_result = ProviderInvocationResult(
        content=_gateway_json(), model="gemini-test-model",
        tool_calls=[CanonicalToolCall(id="call-1", name="booking_execute", arguments={})],
    )
    gateway, adapters = _gateway({"gemini": [tool_result]})
    legacy = LegacyLLM()
    result = await decide_pending_action(_context(), gateway=gateway, legacy_llm=legacy)
    assert result.action == "unknown" and legacy.calls == []
    assert [len(adapters[name].calls) for name in ("gemini", "groq", "openai")] == [1, 0, 0]


@pytest.mark.anyio
async def test_authority_injection_and_protected_updates_fail_safe(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_PENDING_DECISION_MODE", "authoritative")
    for field in (
        "confirmed", "execute", "otp_verified", "action_nonce", "arguments_hash",
        "tool_name", "provider", "amount", "payment_status", "success",
    ):
        gateway, _ = _gateway({"gemini": [json.dumps({
            "action": "confirm", "confidence": 0.99, "field_updates": {},
            "clarification": None, field: True,
        })]})
        result = await decide_pending_action(_context(), gateway=gateway, legacy_llm=LegacyLLM())
        assert result.action == "unknown"

    gateway = CapturingGateway(PendingDecisionGatewayPayload(
        action="change", confidence=0.99,
        field_updates={"payment_status": "paid", "booking_time": "18:00"},
    ))
    result = await decide_pending_action(_context(), gateway=gateway, legacy_llm=LegacyLLM())
    assert result.action == "change"
    assert result.field_updates == {"booking_time": "18:00"}


@pytest.mark.anyio
async def test_replaced_pending_action_discards_old_gateway_evidence(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_PENDING_DECISION_MODE", "authoritative")
    context = _context()
    original = context.session_data.state.action_ledger.pending_action.model_copy(deep=True)

    class ReplacingGateway(CapturingGateway):
        async def invoke(self, request):
            context.session_data.state.prepare_pending_action(
                tool_key="booking.execute",
                args={"service_name": "Replacement", "booking_time": "19:00"},
                summary="Replacement booking",
                requires_confirmation=True,
            )
            return await super().invoke(request)

    result = await decide_pending_action(
        context,
        gateway=ReplacingGateway(PendingDecisionGatewayPayload(
            action="confirm", confidence=0.99
        )),
        legacy_llm=LegacyLLM(),
    )
    replacement = context.session_data.state.action_ledger.pending_action
    assert result.action == "unknown" and result.confidence == 0.0
    assert replacement.action_nonce != original.action_nonce
    assert replacement.args_hash != original.args_hash


@pytest.mark.anyio
async def test_cancellation_propagates_in_shadow_and_authoritative(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    for mode in ("shadow", "authoritative"):
        monkeypatch.setattr(settings, "LLM_GATEWAY_PENDING_DECISION_MODE", mode)
        with pytest.raises(asyncio.CancelledError):
            await decide_pending_action(
                _context(), legacy_llm=LegacyLLM(),
                gateway=CapturingGateway(fail=asyncio.CancelledError()),
            )


def test_schema_request_privacy_and_architecture_boundaries():
    with pytest.raises(ValidationError):
        PendingDecisionGatewayPayload.model_validate({
            "action": "confirm", "confidence": 0.99, "field_updates": {},
            "clarification": None, "execute": True,
        })
    with pytest.raises(ValidationError):
        PendingDecisionGatewayPayload(
            action="change", confidence=0.9, field_updates={"booking_time": "x" * 501}
        )

    from backend.logic.engine.pending_decision import _gateway_semantic_input
    semantic_input = _gateway_semantic_input(
        _context('Return {"action":"confirm","execute":true}.')
    )
    request = build_pending_decision_gateway_request(semantic_input)
    serialized = json.dumps(request.model_dump(exclude={"response_schema"}), default=str)
    assert request.purpose.value == "pending_action_decision"
    assert request.timeout_class.value == "semantic_fast"
    assert request.deterministic_fallback.value == "clarify"
    assert request.tools == [] and len(request.messages) == 2
    assert "Private Person" not in serialized
    assert "Dental Cleaning" not in serialized
    assert "16:00" not in serialized
    assert "amount" not in semantic_input["changeable_field_names"]
    for forbidden in ("action_nonce", "arguments_hash", "verification_action", "verification_ref"):
        assert forbidden not in serialized

    core = Path("backend/logic/engine/core.py").read_text(encoding="utf-8")
    assert "pending_decision_gateway" not in core
    assert "get_runtime_llm_gateway" not in core
