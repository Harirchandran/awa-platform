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
from backend.ai.gateway.models import LLMParseStatus, LLMResult
from backend.ai.gateway.registry import LLMProviderRegistry
from backend.core.config import Settings, settings
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.logic.engine.field_extraction import (
    extract_booking_fields,
    extract_lead_fields,
    extract_order_fields,
    extract_payment_fields,
    extract_reminder_fields,
    extract_support_fields,
)
from backend.logic.engine.field_extraction_gateway import (
    LeadGatewayPayload,
    ReminderGatewayPayload,
    SupportGatewayPayload,
    build_field_extraction_gateway_request,
)
from backend.tests.fakes.fake_llm_gateway import RecordingTelemetry, ScriptedLLMProviderAdapter


WORKFLOWS = {
    "support": (extract_support_fields, SupportGatewayPayload, "support"),
    "lead_capture": (extract_lead_fields, LeadGatewayPayload, "lead_capture"),
    "reminder": (extract_reminder_fields, ReminderGatewayPayload, "reminders"),
}


def _config():
    return {
        "modules": {
            "support": {"enabled": True, "fields": [
                {"key": "issue_summary", "type": "string", "required": True},
                {"key": "urgency", "type": "select", "required": False,
                 "options": ["normal", "urgent"]},
            ]},
            "lead_capture": {"enabled": True, "fields": [
                {"key": "name", "type": "string", "required": True},
                {"key": "phone", "type": "phone", "required": False},
                {"key": "email", "type": "email", "required": False},
                {"key": "interest", "type": "select", "required": False,
                 "options": ["demo", "quote"]},
            ]},
            "reminders": {"enabled": True, "fields": [
                {"key": "message_text", "type": "string", "required": True},
                {"key": "scheduled_for", "type": "string", "required": True},
                {"key": "channel", "type": "select", "required": False,
                 "options": ["awa_chat", "whatsapp"]},
            ]},
            "reviews": {"enabled": True, "fields": []},
            "booking": {"enabled": True, "fields": []},
            "order": {"enabled": True, "fields": []},
            "payments": {"enabled": True, "fields": []},
        },
        "timezone": "Asia/Kolkata",
    }


def _context(workflow: str, text: str = "current customer turn", *, current=None):
    module = WORKFLOWS[workflow][2]
    tenant_id = uuid4()
    current = dict(current or {})
    state = SessionState(
        workflow_state=ConversationState.COLLECTING_DATA,
        step_data=current,
        scoped_step_data={module: current} if current else {},
    )
    state.action_ledger.current_task = module
    state.action_ledger.task_status = "collecting"
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Tenant", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+10000000000", name="User"),
        session_data=ConversationSession(
            id=uuid4(), tenant_id=tenant_id, conversation_id=uuid4(), config_version=1,
            current_module=module, state=state,
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=_config()),
        incoming_message={"text": text, "channel_type": "awa_chat", "detected_language": "en"},
    )


def _payload(workflow: str, updates=None, *, confidence=0.95, **overrides):
    updates = dict(updates or {})
    value = {
        "detected_language": "en",
        "workflow": workflow,
        "workflow_action": "continue",
        "confidence": confidence,
        "field_updates": updates,
        "field_confidence": {key: confidence for key in updates},
        "clarification_needed": not bool(updates),
        "clarification_fields": [],
        "safe_response_hint": None,
    }
    value.update(overrides)
    return value


def _legacy_json(workflow: str):
    updates = {
        "support": {"issue_summary": "Legacy support"},
        "lead_capture": {"customer_name": "Legacy name"},
        "reminder": {"reminder_message": "Legacy reminder"},
    }[workflow]
    return json.dumps(_payload(workflow, updates))


class LegacyLLM:
    def __init__(self, workflow: str):
        self.workflow = workflow
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return AIMessage(content=_legacy_json(self.workflow))


class CapturingGateway:
    def __init__(self, payload, *, error=None):
        self.payload = payload
        self.error = error
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return LLMResult(
            content=self.payload,
            provider="gemini",
            model="test",
            latency_ms=1,
            parse_status=LLMParseStatus.SUCCESS,
        )


def _gateway(scripts, *, telemetry=None):
    registry = LLMProviderRegistry()
    adapters = {}
    for provider in ("gemini", "groq", "openai"):
        adapter = ScriptedLLMProviderAdapter(provider, scripts.get(provider, ["unused"]))
        registry.register(provider, adapter)
        adapters[provider] = adapter
    return LLMGateway(registry, telemetry=telemetry), adapters


@pytest.fixture(autouse=True)
def rollout(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "legacy")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", "")


def test_rollout_allowlist_is_canonical_bounded_and_independent():
    default = Settings(_env_file=None)
    assert default.LLM_GATEWAY_ENABLED is True
    assert default.LLM_GATEWAY_FIELD_EXTRACTION_MODE == "authoritative"
    assert default.llm_gateway_field_extraction_workflows == (
        "review", "support", "lead_capture", "reminder", "booking", "order", "payment",
    )
    assert Settings(
        _env_file=None,
        LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS=(
            "review,support,lead_capture,reminder,booking,order,payment"
        ),
    ).llm_gateway_field_extraction_workflows == (
        "review", "support", "lead_capture", "reminder", "booking", "order", "payment"
    )
    for invalid in ("lead", "payments", "unknown"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS=invalid)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS="support,support")


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", WORKFLOWS)
async def test_legacy_precedence_calls_legacy_once_and_never_gateway(monkeypatch, workflow):
    function, schema, _ = WORKFLOWS[workflow]
    for enabled, allowlist, mode in (
        (False, workflow, "authoritative"),
        (True, "", "authoritative"),
        (True, workflow, "legacy"),
    ):
        monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", enabled)
        monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", allowlist)
        monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", mode)
        legacy = LegacyLLM(workflow)
        forbidden = CapturingGateway(schema.model_validate(_payload(workflow)), error=AssertionError())
        result = await function(_context(workflow), legacy_llm=legacy, gateway=forbidden)
        assert result.workflow == workflow
        assert len(legacy.calls) == 1 and forbidden.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", WORKFLOWS)
async def test_shadow_runs_each_once_returns_legacy_and_records_names_only(monkeypatch, workflow):
    function, schema, _ = WORKFLOWS[workflow]
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "shadow")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", workflow)
    sentinels = {
        "support": "PRIVATE SUPPORT DESCRIPTION 7301",
        "lead_capture": "PRIVATE LEAD NAME PHONE EMAIL 7302",
        "reminder": "PRIVATE REMINDER TEXT DATE TIME 7303",
    }
    gateway_updates = {
        "support": {"issue_summary": sentinels[workflow]},
        "lead_capture": {"customer_name": sentinels[workflow]},
        "reminder": {"reminder_message": sentinels[workflow]},
    }[workflow]
    legacy = LegacyLLM(workflow)
    gateway = CapturingGateway(schema.model_validate(_payload(workflow, gateway_updates)))
    records = []
    result = await function(
        _context(workflow, sentinels[workflow]),
        legacy_llm=legacy,
        gateway=gateway,
        shadow_recorder=records.append,
    )
    assert result.field_updates != gateway_updates
    assert len(legacy.calls) == len(gateway.requests) == len(records) == 1
    assert sentinels[workflow] not in json.dumps(records)
    assert records[0]["workflow"] == workflow
    assert gateway.requests[0].tools == []


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", WORKFLOWS)
async def test_shadow_gateway_and_recorder_failures_are_fail_open(monkeypatch, workflow):
    function, schema, _ = WORKFLOWS[workflow]
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "shadow")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", workflow)
    legacy = LegacyLLM(workflow)
    result = await function(
        _context(workflow),
        legacy_llm=legacy,
        gateway=CapturingGateway(schema.model_validate(_payload(workflow)), error=RuntimeError("down")),
        shadow_recorder=lambda _: (_ for _ in ()).throw(RuntimeError("recorder down")),
    )
    assert result.workflow == workflow and len(legacy.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow,updates", [
    ("support", {"issue_summary": "Service unavailable", "urgency": "urgent"}),
    ("lead_capture", {"customer_name": "Arjun", "phone": "+15555550123",
                      "email": "arjun@example.test", "interest": "demo"}),
    ("reminder", {"reminder_message": "Call the clinic", "reminder_date": "2030-01-02",
                  "reminder_time": "18:00", "channel": "awa_chat"}),
])
async def test_authoritative_uses_gateway_only_and_preserves_contract(monkeypatch, workflow, updates):
    function, schema, _ = WORKFLOWS[workflow]
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "authoritative")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", workflow)
    legacy = LegacyLLM(workflow)
    gateway = CapturingGateway(schema.model_validate(_payload(workflow, updates)))
    result = await function(_context(workflow), gateway=gateway, legacy_llm=legacy)
    assert result.field_updates == updates
    assert len(gateway.requests) == 1 and legacy.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow,field,bad_value", [
    ("support", "urgency", "nuclear"),
    ("lead_capture", "interest", "administrator"),
    ("reminder", "channel", "root_console"),
])
async def test_configured_unknown_choices_are_removed_without_second_provider(
    monkeypatch, workflow, field, bad_value
):
    function, schema, _ = WORKFLOWS[workflow]
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "authoritative")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", workflow)
    gateway = CapturingGateway(schema.model_validate(_payload(workflow, {field: bad_value})))
    result = await function(_context(workflow), gateway=gateway, legacy_llm=LegacyLLM(workflow))
    assert field not in result.field_updates and len(gateway.requests) == 1


def test_workflow_schemas_are_strict_bounded_and_reject_authority_fields():
    for workflow, (_, schema, _) in WORKFLOWS.items():
        with pytest.raises(ValidationError):
            schema.model_validate({**_payload(workflow), "scheduled": True})
        with pytest.raises(ValidationError):
            schema.model_validate(_payload(workflow, {"customer_id": "admin"}))
        with pytest.raises(ValidationError):
            schema.model_validate(_payload(workflow, {}, confidence=1.5))
    with pytest.raises(ValidationError):
        LeadGatewayPayload.model_validate(_payload("lead_capture", {"notes": "x" * 2001}))


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_requests_are_current_turn_only_tool_free_bounded_and_privacy_safe(workflow):
    _, schema, _ = WORKFLOWS[workflow]
    sentinel = f"PRIVATE-{workflow}-TURN-9182"
    request = build_field_extraction_gateway_request(workflow, {
        "customer_turn": sentinel,
        "missing_field_names": ["phone"],
        "collected_field_names": ["customer_name"],
        "configured_field_schema": [],
        "allowed_choices": {},
        "language": "en",
    }, schema)
    assert request.purpose.value == "field_extraction"
    assert request.tools == [] and len(request.messages) == 2
    assert request.timeout_class.value == "structured_standard"
    assert request.deterministic_fallback.value == "preserve_state"
    assert sentinel in request.messages[1].content
    assert sentinel not in json.dumps(request.safe_context)


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow,updates", [
    ("support", {"issue_summary": "PRIVATE-SUPPORT-DESCRIPTION-11"}),
    ("lead_capture", {"customer_name": "PRIVATE-LEAD-NAME-12", "phone": "+15555550124",
                      "email": "private12@example.test"}),
    ("reminder", {"reminder_message": "PRIVATE-REMINDER-TEXT-13",
                  "reminder_date": "2031-02-03", "reminder_time": "19:45"}),
])
async def test_gateway_telemetry_never_contains_sensitive_turn_or_values(
    monkeypatch, workflow, updates
):
    function, _, _ = WORKFLOWS[workflow]
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "authoritative")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", workflow)
    telemetry = RecordingTelemetry()
    gateway, _ = _gateway({"gemini": [json.dumps(_payload(workflow, updates))]}, telemetry=telemetry)
    await function(_context(workflow, " ".join(str(value) for value in updates.values())), gateway=gateway)
    rendered = json.dumps([event.model_dump(mode="json") for event in telemetry.events])
    for value in updates.values():
        assert str(value) not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", WORKFLOWS)
async def test_authoritative_provider_fallback_success_and_all_failure(monkeypatch, workflow):
    function, _, _ = WORKFLOWS[workflow]
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "authoritative")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", workflow)
    success_updates = {
        "support": {"issue_summary": "Help"},
        "lead_capture": {"customer_name": "Arjun"},
        "reminder": {"reminder_message": "Call"},
    }[workflow]
    valid = json.dumps(_payload(workflow, success_updates))
    gateway, adapters = _gateway({"gemini": ["malformed_structured_output"], "groq": [valid]})
    result = await function(_context(workflow), gateway=gateway, legacy_llm=LegacyLLM(workflow))
    assert result.field_updates == success_updates
    assert [len(adapters[name].calls) for name in ("gemini", "groq", "openai")] == [1, 1, 0]
    failed, adapters = _gateway({
        "gemini": ["timeout"], "groq": ["network"], "openai": ["rate_limit"],
    })
    result = await function(_context(workflow), gateway=failed, legacy_llm=LegacyLLM(workflow))
    assert result.field_updates == {} and result.clarification_needed is True
    assert [len(adapters[name].calls) for name in ("gemini", "groq", "openai")] == [1, 1, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", WORKFLOWS)
async def test_safety_invalid_request_and_cancellation_are_terminal(monkeypatch, workflow):
    function, schema, _ = WORKFLOWS[workflow]
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "authoritative")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", workflow)
    for scripts in (
        {"gemini": ["safety_rejection"]},
        {"gemini": [LLMInvalidRequestError(safe_metadata={"reason": "invalid"})]},
    ):
        gateway, adapters = _gateway(scripts)
        result = await function(_context(workflow), gateway=gateway, legacy_llm=LegacyLLM(workflow))
        assert result.field_updates == {}
        assert [len(adapters[name].calls) for name in ("gemini", "groq", "openai")] == [1, 0, 0]
    with pytest.raises(asyncio.CancelledError):
        await function(
            _context(workflow),
            gateway=CapturingGateway(schema.model_validate(_payload(workflow)),
                                     error=asyncio.CancelledError()),
        )


@pytest.mark.asyncio
async def test_mixed_allowlists_do_not_activate_other_migrated_workflows(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "authoritative")
    for active in WORKFLOWS:
        monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", active)
        for workflow, (function, schema, _) in WORKFLOWS.items():
            legacy = LegacyLLM(workflow)
            gateway = CapturingGateway(schema.model_validate(_payload(workflow)))
            await function(_context(workflow), gateway=gateway, legacy_llm=legacy)
            assert len(gateway.requests) == (1 if workflow == active else 0)
            assert len(legacy.calls) == (0 if workflow == active else 1)


@pytest.mark.asyncio
async def test_booking_order_payment_remain_legacy_with_all_migrated_allowlisted(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "authoritative")
    monkeypatch.setattr(
        settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS",
        "review,support,lead_capture,reminder",
    )
    calls = []

    class MultiLegacy:
        async def ainvoke(self, messages):
            system = str(messages[0].content)
            workflow = next(name for name in ("booking", "order", "payment") if f"active {name}" in system)
            calls.append(workflow)
            return AIMessage(content=json.dumps(_payload(workflow)))

    legacy = MultiLegacy()
    monkeypatch.setattr(
        "backend.logic.engine.field_extraction._legacy_llm_or_default",
        lambda _injected: legacy,
    )
    context = _context("support")
    await extract_booking_fields(context)
    await extract_order_fields(context)
    await extract_payment_fields(context)
    assert calls == ["booking", "order", "payment"]


def test_architecture_boundaries_and_prohibited_imports_remain_unchanged():
    root = Path(__file__).resolve().parents[3]
    for relative in (
        "backend/logic/engine/core.py",
        "backend/logic/engine/llm.py",
        "backend/logic/engine/pending_decision.py",
        "backend/logic/engine/response_localization.py",
    ):
        assert "field_extraction_gateway" not in (root / relative).read_text(encoding="utf-8")
    for domain in ("support", "leads", "reminders"):
        for path in (root / "backend/domains" / domain).rglob("*.py"):
            assert "backend.ai.gateway" not in path.read_text(encoding="utf-8")
