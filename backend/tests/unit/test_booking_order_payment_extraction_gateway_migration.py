from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from backend.ai.gateway.gateway import LLMGateway
from backend.ai.gateway.models import LLMParseStatus, LLMResult
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
from backend.logic.engine.configured_option_resolver import resolve_exact_configured_option
from backend.logic.engine.field_extraction import (
    extract_booking_fields,
    extract_order_fields,
    extract_payment_fields,
)
from backend.logic.engine.field_extraction_gateway import (
    BookingGatewayPayload,
    OrderGatewayPayload,
    PaymentGatewayPayload,
    build_field_extraction_gateway_request,
)
from backend.tests.fakes.fake_llm_gateway import RecordingTelemetry, ScriptedLLMProviderAdapter


WORKFLOWS = {
    "booking": (extract_booking_fields, BookingGatewayPayload, "booking"),
    "order": (extract_order_fields, OrderGatewayPayload, "order"),
    "payment": (extract_payment_fields, PaymentGatewayPayload, "payments"),
}


def _config() -> dict:
    return {
        "timezone": "Asia/Kolkata",
        "services": [
            {"id": "svc-filling", "name": "Cavity Filling", "price": 900, "duration_mins": 30},
            {"id": "svc-cleaning", "name": "Teeth Cleaning", "price": 500, "duration_mins": 20},
        ],
        "modules": {
            "booking": {"enabled": True, "fields": [
                {"key": "service_id", "type": "select", "required": True},
                {"key": "preferred_date", "type": "date", "required": True},
                {"key": "preferred_time", "type": "time", "required": True},
                {"key": "customer_name", "type": "string", "required": False},
            ]},
            "order": {
                "enabled": True,
                "fulfillment_types": ["pickup", "delivery"],
                "payment_methods": ["cod", "upi_on_delivery"],
                "categories": [{"name": "Dental", "items": [
                    {"name": "Toothpaste", "sku": "TP-1", "price": 100},
                    {"name": "Toothbrush", "sku": "TB-1", "price": 50},
                ]}],
            },
            "payments": {"enabled": True, "allowed_currencies": ["INR"], "fields": [
                {"key": "amount", "type": "number", "required": True},
                {"key": "currency", "type": "string", "required": True},
                {"key": "payment_purpose", "type": "string", "required": False},
            ]},
            "reviews": {"enabled": True, "fields": []},
            "support": {"enabled": True, "fields": []},
            "lead_capture": {"enabled": True, "fields": []},
            "reminders": {"enabled": True, "fields": []},
        },
    }


def _context(workflow: str, text: str = "current customer turn", *, current=None) -> ExecutionContext:
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


def _payload(workflow: str, updates=None, **overrides) -> dict:
    updates = dict(updates or {})
    value = {
        "detected_language": "en",
        "workflow": workflow,
        "workflow_action": "continue",
        "confidence": 0.95,
        "field_updates": updates,
        "field_confidence": {key: 0.95 for key in updates},
        "clarification_needed": not bool(updates),
        "clarification_fields": [],
        "safe_response_hint": None,
    }
    value.update(overrides)
    return value


def _valid_updates(workflow: str) -> dict:
    return {
        "booking": {"service_name": "Teeth Cleaning", "booking_date": "2026-07-15"},
        "order": {"item_name": "Toothpaste", "quantity": 1},
        "payment": {"payment_purpose": "Dental appointment"},
    }[workflow]


class LegacyLLM:
    def __init__(self, workflow: str):
        self.workflow = workflow
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        updates = {
            "booking": {"service_name": "Cavity Filling"},
            "order": {"item_name": "Toothbrush", "quantity": 2},
            "payment": {"amount": 500, "currency": "INR"},
        }[self.workflow]
        return AIMessage(content=json.dumps(_payload(self.workflow, updates)))


class CapturingGateway:
    def __init__(self, content=None, *, error=None):
        self.content = content
        self.error = error
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return LLMResult(
            content=self.content,
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


def test_complete_rollout_allowlist_and_independent_combinations():
    supported = "review,support,lead_capture,reminder,booking,order,payment"
    assert Settings(
        _env_file=None, LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS=supported
    ).llm_gateway_field_extraction_workflows == tuple(supported.split(","))
    for value in (
        "booking", "order", "payment", "booking,order", "order,payment",
        "booking,order,payment", supported,
    ):
        assert Settings(
            _env_file=None, LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS=value
        ).llm_gateway_field_extraction_workflows == tuple(value.split(","))
    for value in ("payments", "booking,booking"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS=value)


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", WORKFLOWS)
async def test_legacy_precedence_calls_legacy_once_and_gateway_zero(monkeypatch, workflow):
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
        gateway = CapturingGateway(schema.model_validate(_payload(workflow)))
        result = await function(_context(workflow), legacy_llm=legacy, gateway=gateway)
        assert result.workflow == workflow
        assert len(legacy.calls) == 1
        assert gateway.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", WORKFLOWS)
async def test_shadow_calls_each_once_returns_legacy_and_records_presence_only(monkeypatch, workflow):
    function, schema, _ = WORKFLOWS[workflow]
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "shadow")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", workflow)
    sentinel = f"PRIVATE-{workflow}-SENTINEL-7301"
    gateway_updates = _valid_updates(workflow)
    gateway_updates[next(iter(gateway_updates))] = sentinel
    # Configuration-owned candidates must remain valid so the comparison sees a field.
    if workflow == "booking":
        gateway_updates["service_name"] = "Teeth Cleaning"
    elif workflow == "order":
        gateway_updates["item_name"] = "Toothpaste"
    legacy = LegacyLLM(workflow)
    gateway = CapturingGateway(schema.model_validate(_payload(workflow, gateway_updates)))
    records = []
    result = await function(
        _context(workflow, sentinel), legacy_llm=legacy, gateway=gateway,
        shadow_recorder=records.append,
    )
    assert result.field_updates != gateway_updates
    assert len(legacy.calls) == len(gateway.requests) == len(records) == 1
    assert sentinel not in json.dumps(records)
    assert set(records[0]) <= {
        "workflow", "legacy_success", "gateway_success", "gateway_provider",
        "gateway_fallback_used", "gateway_parse_status", "legacy_field_names",
        "gateway_field_names", "field_presence_equal", "clarification_equal",
        "confidence_bucket_equal", "service_presence_equal", "date_presence_equal",
        "time_presence_equal", "contact_presence_equal", "item_count_bucket_equal",
        "fulfillment_type_presence_equal", "payment_method_presence_equal",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", WORKFLOWS)
async def test_authoritative_calls_gateway_once_and_legacy_zero(monkeypatch, workflow):
    function, schema, _ = WORKFLOWS[workflow]
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "authoritative")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", workflow)
    legacy = LegacyLLM(workflow)
    updates = _valid_updates(workflow)
    gateway = CapturingGateway(schema.model_validate(_payload(workflow, updates)))
    result = await function(_context(workflow), legacy_llm=legacy, gateway=gateway)
    assert result.field_updates == updates
    assert legacy.calls == [] and len(gateway.requests) == 1
    request = gateway.requests[0]
    assert request.tools == [] and len(request.messages) == 2
    assert request.safe_context["workflow"] == workflow


@pytest.mark.asyncio
async def test_booking_gateway_canonicalizes_config_and_prompt_focus_field_aliases(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "authoritative")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", "booking")
    context = _context("booking", current={"service_name": "Teeth Cleaning"})
    context.session_data.state.set_prompt_focus(
        workflow="booking",
        requested_fields=["preferred_date", "preferred_time", "customer_name"],
    )
    gateway = CapturingGateway(
        BookingGatewayPayload.model_validate(_payload("booking", {"booking_date": "2026-07-15"}))
    )

    await extract_booking_fields(context, gateway=gateway)

    semantic_input = json.loads(gateway.requests[0].messages[1].content)
    assert semantic_input["missing_field_names"] == ["booking_date", "booking_time", "customer_name"]
    assert semantic_input["collected_field_names"] == ["service_name"]
    assert [field["field_name"] for field in semantic_input["configured_field_schema"]] == [
        "booking_date", "booking_time", "customer_name",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow,field,value", [
    ("booking", "service_name", "Quantum dental teleportation"),
    ("order", "item_name", "Fabricated product"),
    ("order", "fulfillment_type", "drone"),
    ("order", "payment_method", "unconfigured"),
])
async def test_authoritative_rejects_unknown_configuration_owned_candidates(
    monkeypatch, workflow, field, value
):
    function, schema, _ = WORKFLOWS[workflow]
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "authoritative")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", workflow)
    gateway = CapturingGateway(schema.model_validate(_payload(workflow, {field: value})))
    result = await function(_context(workflow), gateway=gateway)
    assert field not in result.field_updates


@pytest.mark.parametrize("schema,forbidden", [
    (BookingGatewayPayload, {"slot_available": True, "create_booking": True}),
    (OrderGatewayPayload, {"price": 1, "total": 1, "paid": True, "order_created": True}),
    (PaymentGatewayPayload, {
        "provider": "razorpay", "payment_type": "subscription", "amount": 1,
        "currency": "INR", "payment_status": "paid", "success": True, "otp": "123456",
    }),
])
def test_strict_workflow_schemas_forbid_authority_fields(schema, forbidden):
    workflow = {BookingGatewayPayload: "booking", OrderGatewayPayload: "order", PaymentGatewayPayload: "payment"}[schema]
    for key, value in forbidden.items():
        with pytest.raises(ValidationError):
            schema.model_validate(_payload(workflow, {key: value}))


@pytest.mark.parametrize("quantity", [0, -1, 10_001])
def test_order_schema_rejects_invalid_quantity_boundaries(quantity):
    with pytest.raises(ValidationError):
        OrderGatewayPayload.model_validate(_payload("order", {"quantity": quantity}))
    assert OrderGatewayPayload.model_validate(
        _payload("order", {"item_name": "Toothpaste", "quantity": 2})
    ).field_updates.quantity == 2


def test_order_gateway_preserves_a_multi_item_cart_without_price_authority():
    payload = OrderGatewayPayload.model_validate(_payload("order", {
        "items": [
            {"item_name": "Toothbrush", "quantity": 2},
            {"item_name": "Toothpaste", "quantity": 1},
        ],
    }))

    assert [item.model_dump() for item in payload.field_updates.items or []] == [
        {"item_name": "Toothbrush", "item_sku": None, "quantity": 2, "notes": None},
        {"item_name": "Toothpaste", "item_sku": None, "quantity": 1, "notes": None},
    ]
    with pytest.raises(ValidationError):
        OrderGatewayPayload.model_validate(_payload("order", {
            "items": [{"item_name": "Toothbrush", "quantity": 2, "price": 1}],
        }))


def test_gateway_requests_are_bounded_and_exclude_business_authority():
    cases = {
        "booking": (BookingGatewayPayload, {
            "customer_turn": "Tomorrow at 4 PM", "configured_service_labels": ["Teeth Cleaning"],
        }),
        "order": (OrderGatewayPayload, {
            "customer_turn": "One Toothpaste", "catalog_candidates": [{"name": "Toothpaste"}],
        }),
        "payment": (PaymentGatewayPayload, {
            "customer_turn": "For my dental appointment", "payment_constraints": {"credentials_forbidden": True},
        }),
    }
    for workflow, (schema, semantic_input) in cases.items():
        request = build_field_extraction_gateway_request(workflow, semantic_input, schema)
        serialized = json.dumps(request.safe_context, sort_keys=True)
        assert request.tools == [] and len(request.messages) == 2
        assert all(word not in serialized for word in (
            "Toothpaste", "Teeth Cleaning", "amount", "provider", "otp", "credential",
        ))


def test_booking_gateway_instruction_requires_each_explicit_rich_turn_field():
    request = build_field_extraction_gateway_request(
        "booking",
        {"customer_turn": "redacted rich booking request"},
        BookingGatewayPayload,
    )

    instruction = str(request.messages[0].content)
    assert "every explicit service, date, time, customer name, and phone value" in instruction
    assert "do not omit a valid supplied value" in instruction


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "card 4111111111111111", "card 4111 1111 1111 1111", "CVV 123", "PIN: 1234",
    "OTP=123456", "upi://private@bank", "API key: unit-test-not-a-real-provider-key", "secret=private-value",
])
async def test_payment_credentials_are_rejected_before_legacy_or_gateway(monkeypatch, text):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "authoritative")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", "payment")
    legacy = LegacyLLM("payment")
    gateway = CapturingGateway(PaymentGatewayPayload.model_validate(_payload("payment")))
    with pytest.raises(ValueError, match="credential-shape"):
        await extract_payment_fields(_context("payment", text), legacy_llm=legacy, gateway=gateway)
    assert legacy.calls == [] and gateway.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", WORKFLOWS)
async def test_gateway_telemetry_never_contains_customer_or_business_values(monkeypatch, workflow):
    function, _, _ = WORKFLOWS[workflow]
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "authoritative")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", workflow)
    sentinel = f"PRIVATE-{workflow}-PHONE-EMAIL-DATE-QUANTITY-AMOUNT-REFERENCE-PROVIDER-7402"
    telemetry = RecordingTelemetry()
    gateway, _ = _gateway({
        "gemini": [json.dumps(_payload(workflow, _valid_updates(workflow)))]
    }, telemetry=telemetry)
    await function(_context(workflow, sentinel), gateway=gateway)
    assert sentinel not in json.dumps(telemetry.events, default=str)


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", WORKFLOWS)
async def test_authoritative_provider_fallback_and_terminal_failure(monkeypatch, workflow):
    function, _, _ = WORKFLOWS[workflow]
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "authoritative")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", workflow)
    valid = json.dumps(_payload(workflow, _valid_updates(workflow)))
    gateway, adapters = _gateway({"gemini": ["rate_limit"], "groq": [valid]})
    result = await function(_context(workflow), gateway=gateway)
    assert result.field_updates == _valid_updates(workflow)
    assert [len(adapters[name].calls) for name in ("gemini", "groq", "openai")] == [1, 1, 0]

    failed, failed_adapters = _gateway({
        "gemini": ["timeout"], "groq": ["timeout"], "openai": ["timeout"],
    })
    result = await function(_context(workflow), gateway=failed)
    assert result.workflow == "unknown" and result.field_updates == {}
    assert [len(failed_adapters[name].calls) for name in ("gemini", "groq", "openai")] == [1, 1, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", WORKFLOWS)
async def test_cancellation_propagates(monkeypatch, workflow):
    function, _, _ = WORKFLOWS[workflow]
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_MODE", "authoritative")
    monkeypatch.setattr(settings, "LLM_GATEWAY_FIELD_EXTRACTION_WORKFLOWS", workflow)
    with pytest.raises(asyncio.CancelledError):
        await function(_context(workflow), gateway=CapturingGateway(error=asyncio.CancelledError()))


def test_exact_booking_service_resolution_remains_deterministic_and_keeps_stable_id():
    match = resolve_exact_configured_option(
        customer_text="Cavity Filling",
        active_workflow="booking",
        requested_fields=["service_name"],
        config_data=_config(),
        runtime_available_modules={"booking", "order", "payments"},
    )
    assert match is not None
    assert match.field_updates == {"service_name": "Cavity Filling", "service_id": "svc-filling"}
