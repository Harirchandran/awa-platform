"""Gateway boundary for caller-owned semantic field-extraction contracts."""
from __future__ import annotations

import json
from enum import Enum
from typing import Any, Callable, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from backend.ai.gateway.gateway import LLMGateway
from backend.ai.gateway.models import (
    CanonicalLLMMessage,
    LLMFallbackContract,
    LLMParseStatus,
    LLMPurpose,
    LLMRequest,
    LLMResult,
    LLMTimeoutClass,
)
from backend.ai.gateway.telemetry import (
    LLMCallerValidationOutcome,
    LLMShadowComparisonEvent,
    emit_configured_event,
    llm_rollout_context,
    safe_correlation_fields,
)
from backend.core.config import Settings, settings


class FieldExtractionGatewayMode(str, Enum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    AUTHORITATIVE = "authoritative"


class SupportGatewayFieldUpdates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, min_length=1, max_length=80)
    email: str | None = Field(default=None, min_length=1, max_length=320)
    issue_summary: str | None = Field(default=None, min_length=1, max_length=2_000)
    support_reason: str | None = Field(default=None, min_length=1, max_length=1_000)
    urgency: str | None = Field(default=None, min_length=1, max_length=80)
    preferred_contact_time: str | None = Field(default=None, min_length=1, max_length=200)
    order_reference: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, min_length=1, max_length=2_000)


class SupportGatewayFieldConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_name: float | None = Field(default=None, ge=0.0, le=1.0)
    phone: float | None = Field(default=None, ge=0.0, le=1.0)
    email: float | None = Field(default=None, ge=0.0, le=1.0)
    issue_summary: float | None = Field(default=None, ge=0.0, le=1.0)
    support_reason: float | None = Field(default=None, ge=0.0, le=1.0)
    urgency: float | None = Field(default=None, ge=0.0, le=1.0)
    preferred_contact_time: float | None = Field(default=None, ge=0.0, le=1.0)
    order_reference: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: float | None = Field(default=None, ge=0.0, le=1.0)


SupportFieldName = Literal[
    "customer_name", "phone", "email", "issue_summary", "support_reason", "urgency",
    "preferred_contact_time", "order_reference", "notes",
]


class SupportGatewayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detected_language: str | None = Field(default=None, min_length=1, max_length=80)
    workflow: Literal["support", "unknown"] = "unknown"
    workflow_action: Literal[
        "continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"
    ] = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    field_updates: SupportGatewayFieldUpdates = Field(default_factory=SupportGatewayFieldUpdates)
    field_confidence: SupportGatewayFieldConfidence = Field(default_factory=SupportGatewayFieldConfidence)
    clarification_needed: bool = True
    clarification_fields: list[SupportFieldName] = Field(default_factory=list, max_length=8)
    safe_response_hint: str | None = Field(default=None, min_length=1, max_length=300)


class LeadGatewayFieldUpdates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, min_length=1, max_length=80)
    email: str | None = Field(default=None, min_length=1, max_length=320)
    interest: str | None = Field(default=None, min_length=1, max_length=500)
    message: str | None = Field(default=None, min_length=1, max_length=2_000)
    preferred_contact_time: str | None = Field(default=None, min_length=1, max_length=200)
    source: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, min_length=1, max_length=2_000)


class LeadGatewayFieldConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_name: float | None = Field(default=None, ge=0.0, le=1.0)
    phone: float | None = Field(default=None, ge=0.0, le=1.0)
    email: float | None = Field(default=None, ge=0.0, le=1.0)
    interest: float | None = Field(default=None, ge=0.0, le=1.0)
    message: float | None = Field(default=None, ge=0.0, le=1.0)
    preferred_contact_time: float | None = Field(default=None, ge=0.0, le=1.0)
    source: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: float | None = Field(default=None, ge=0.0, le=1.0)


LeadFieldName = Literal[
    "customer_name", "phone", "email", "interest", "message",
    "preferred_contact_time", "source", "notes",
]


class LeadGatewayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detected_language: str | None = Field(default=None, min_length=1, max_length=80)
    workflow: Literal["lead_capture", "unknown"] = "unknown"
    workflow_action: Literal[
        "continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"
    ] = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    field_updates: LeadGatewayFieldUpdates = Field(default_factory=LeadGatewayFieldUpdates)
    field_confidence: LeadGatewayFieldConfidence = Field(default_factory=LeadGatewayFieldConfidence)
    clarification_needed: bool = True
    clarification_fields: list[LeadFieldName] = Field(default_factory=list, max_length=8)
    safe_response_hint: str | None = Field(default=None, min_length=1, max_length=300)


class ReminderGatewayFieldUpdates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reminder_message: str | None = Field(default=None, min_length=1, max_length=2_000)
    message: str | None = Field(default=None, min_length=1, max_length=2_000)
    scheduled_for: str | None = Field(default=None, min_length=1, max_length=120)
    reminder_date: str | None = Field(default=None, min_length=1, max_length=40)
    reminder_time: str | None = Field(default=None, min_length=1, max_length=40)
    channel: str | None = Field(default=None, min_length=1, max_length=80)
    customer_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, min_length=1, max_length=80)
    email: str | None = Field(default=None, min_length=1, max_length=320)
    notes: str | None = Field(default=None, min_length=1, max_length=2_000)


class ReminderGatewayFieldConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reminder_message: float | None = Field(default=None, ge=0.0, le=1.0)
    message: float | None = Field(default=None, ge=0.0, le=1.0)
    scheduled_for: float | None = Field(default=None, ge=0.0, le=1.0)
    reminder_date: float | None = Field(default=None, ge=0.0, le=1.0)
    reminder_time: float | None = Field(default=None, ge=0.0, le=1.0)
    channel: float | None = Field(default=None, ge=0.0, le=1.0)
    customer_name: float | None = Field(default=None, ge=0.0, le=1.0)
    phone: float | None = Field(default=None, ge=0.0, le=1.0)
    email: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: float | None = Field(default=None, ge=0.0, le=1.0)


ReminderFieldName = Literal[
    "reminder_message", "message", "scheduled_for", "reminder_date", "reminder_time",
    "channel", "customer_name", "phone", "email", "notes",
]


class ReminderGatewayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detected_language: str | None = Field(default=None, min_length=1, max_length=80)
    workflow: Literal["reminder", "unknown"] = "unknown"
    workflow_action: Literal[
        "continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"
    ] = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    field_updates: ReminderGatewayFieldUpdates = Field(default_factory=ReminderGatewayFieldUpdates)
    field_confidence: ReminderGatewayFieldConfidence = Field(default_factory=ReminderGatewayFieldConfidence)
    clarification_needed: bool = True
    clarification_fields: list[ReminderFieldName] = Field(default_factory=list, max_length=8)
    safe_response_hint: str | None = Field(default=None, min_length=1, max_length=300)


class BookingGatewayFieldUpdates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_name: str | None = Field(default=None, min_length=1, max_length=120)
    booking_date: str | None = Field(default=None, min_length=1, max_length=40)
    booking_time: str | None = Field(default=None, min_length=1, max_length=40)
    customer_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, min_length=1, max_length=80)
    email: str | None = Field(default=None, min_length=1, max_length=320)
    notes: str | None = Field(default=None, min_length=1, max_length=2_000)
    special_requests: str | None = Field(default=None, min_length=1, max_length=2_000)


class BookingGatewayFieldConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_name: float | None = Field(default=None, ge=0.0, le=1.0)
    booking_date: float | None = Field(default=None, ge=0.0, le=1.0)
    booking_time: float | None = Field(default=None, ge=0.0, le=1.0)
    customer_name: float | None = Field(default=None, ge=0.0, le=1.0)
    phone: float | None = Field(default=None, ge=0.0, le=1.0)
    email: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: float | None = Field(default=None, ge=0.0, le=1.0)
    special_requests: float | None = Field(default=None, ge=0.0, le=1.0)


BookingFieldName = Literal[
    "service_name", "booking_date", "booking_time", "customer_name", "phone", "email",
    "notes", "special_requests",
]


class BookingGatewayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detected_language: str | None = Field(default=None, min_length=1, max_length=80)
    workflow: Literal["booking", "unknown"] = "unknown"
    workflow_action: Literal[
        "continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"
    ] = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    field_updates: BookingGatewayFieldUpdates = Field(default_factory=BookingGatewayFieldUpdates)
    field_confidence: BookingGatewayFieldConfidence = Field(default_factory=BookingGatewayFieldConfidence)
    clarification_needed: bool = True
    clarification_fields: list[BookingFieldName] = Field(default_factory=list, max_length=8)
    safe_response_hint: str | None = Field(default=None, min_length=1, max_length=300)


class OrderGatewayItem(BaseModel):
    """A customer-requested catalog reference; prices and IDs stay backend-owned."""

    model_config = ConfigDict(extra="forbid")

    item_name: str | None = Field(default=None, min_length=1, max_length=120)
    item_sku: str | None = Field(default=None, min_length=1, max_length=60)
    quantity: int = Field(default=1, ge=1, le=10_000)
    notes: str | None = Field(default=None, min_length=1, max_length=2_000)


class OrderGatewayFieldUpdates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_name: str | None = Field(default=None, min_length=1, max_length=120)
    item_sku: str | None = Field(default=None, min_length=1, max_length=60)
    items: list[OrderGatewayItem] | None = Field(default=None, min_length=1, max_length=50)
    quantity: int | None = Field(default=None, ge=1, le=10_000)
    fulfillment_type: str | None = Field(default=None, min_length=1, max_length=80)
    customer_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, min_length=1, max_length=80)
    delivery_address: str | None = Field(default=None, min_length=1, max_length=1_000)
    notes: str | None = Field(default=None, min_length=1, max_length=2_000)
    payment_method: str | None = Field(default=None, min_length=1, max_length=80)


class OrderGatewayFieldConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_name: float | None = Field(default=None, ge=0.0, le=1.0)
    item_sku: float | None = Field(default=None, ge=0.0, le=1.0)
    items: float | None = Field(default=None, ge=0.0, le=1.0)
    quantity: float | None = Field(default=None, ge=0.0, le=1.0)
    fulfillment_type: float | None = Field(default=None, ge=0.0, le=1.0)
    customer_name: float | None = Field(default=None, ge=0.0, le=1.0)
    phone: float | None = Field(default=None, ge=0.0, le=1.0)
    delivery_address: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: float | None = Field(default=None, ge=0.0, le=1.0)
    payment_method: float | None = Field(default=None, ge=0.0, le=1.0)


OrderFieldName = Literal[
    "item_name", "item_sku", "quantity", "fulfillment_type", "customer_name", "phone",
    "delivery_address", "notes", "payment_method",
]


class OrderGatewayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detected_language: str | None = Field(default=None, min_length=1, max_length=80)
    workflow: Literal["order", "unknown"] = "unknown"
    workflow_action: Literal[
        "continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"
    ] = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    field_updates: OrderGatewayFieldUpdates = Field(default_factory=OrderGatewayFieldUpdates)
    field_confidence: OrderGatewayFieldConfidence = Field(default_factory=OrderGatewayFieldConfidence)
    clarification_needed: bool = True
    clarification_fields: list[OrderFieldName] = Field(default_factory=list, max_length=8)
    safe_response_hint: str | None = Field(default=None, min_length=1, max_length=300)


class PaymentGatewayFieldUpdates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_purpose: str | None = Field(default=None, min_length=1, max_length=500)
    customer_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, min_length=1, max_length=80)
    email: str | None = Field(default=None, min_length=1, max_length=320)
    order_reference: str | None = Field(default=None, min_length=1, max_length=200)
    booking_reference: str | None = Field(default=None, min_length=1, max_length=200)
    invoice_reference: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, min_length=1, max_length=2_000)


class PaymentGatewayFieldConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_purpose: float | None = Field(default=None, ge=0.0, le=1.0)
    customer_name: float | None = Field(default=None, ge=0.0, le=1.0)
    phone: float | None = Field(default=None, ge=0.0, le=1.0)
    email: float | None = Field(default=None, ge=0.0, le=1.0)
    order_reference: float | None = Field(default=None, ge=0.0, le=1.0)
    booking_reference: float | None = Field(default=None, ge=0.0, le=1.0)
    invoice_reference: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: float | None = Field(default=None, ge=0.0, le=1.0)


PaymentFieldName = Literal[
    "payment_purpose", "customer_name", "phone", "email", "order_reference",
    "booking_reference", "invoice_reference", "notes",
]


class PaymentGatewayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detected_language: str | None = Field(default=None, min_length=1, max_length=80)
    workflow: Literal["payment", "unknown"] = "unknown"
    workflow_action: Literal[
        "continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"
    ] = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    field_updates: PaymentGatewayFieldUpdates = Field(default_factory=PaymentGatewayFieldUpdates)
    field_confidence: PaymentGatewayFieldConfidence = Field(default_factory=PaymentGatewayFieldConfidence)
    clarification_needed: bool = True
    clarification_fields: list[PaymentFieldName] = Field(default_factory=list, max_length=8)
    safe_response_hint: str | None = Field(default=None, min_length=1, max_length=300)


GatewayPayload = (
    SupportGatewayPayload | LeadGatewayPayload | ReminderGatewayPayload |
    BookingGatewayPayload | OrderGatewayPayload | PaymentGatewayPayload
)
PayloadT = TypeVar("PayloadT", bound=BaseModel)

_PROMPTS = {
    "support": (
        "Extract semantic evidence from one current customer turn for an active support workflow. "
        "Return only the response schema. Use only allowed fields and configured choices. Null means "
        "no evidence and must not clear existing state. Never create a ticket, escalate, persist, or call a tool."
    ),
    "lead_capture": (
        "Extract semantic evidence from one current customer turn for an active lead_capture workflow. "
        "Return only the response schema. Do not invent contact values or fields. Use only configured "
        "choices. Null means no evidence and must not clear existing state. Never save a lead or call a tool."
    ),
    "reminder": (
        "Extract semantic date, time, message, and allowed-field evidence from one current customer turn "
        "for an active reminder workflow. Return only the response schema. The backend owns timezone, "
        "timestamp, future-time, and scheduling validation. Never claim scheduling, persist, or call a tool."
    ),
    "booking": (
        "Extract semantic evidence from one current customer turn for an active booking workflow. "
        "Return every explicit service, date, time, customer name, and phone value in separate canonical "
        "field_updates entries; do not omit a valid supplied value. Return only the response schema. "
        "Service labels must come from configured choices. The backend "
        "owns service IDs, timezone parsing, working hours, availability, confirmation, and creation. "
        "Never claim availability, confirmation, persistence, or call a tool."
    ),
    "order": (
        "Extract semantic evidence from one current customer turn for an active order workflow. "
        "Return only the response schema. Items, fulfillment, and payment-method labels must come from "
        "configured choices. The backend owns catalog resolution, quantities, prices, totals, confirmation, "
        "and creation. Never set price, total, status, persistence, or call a tool."
    ),
    "payment": (
        "Extract only non-sensitive customer-provided descriptive evidence for an active payment workflow. "
        "Return only the response schema. Never extract provider, payment type, amount, currency, status, "
        "success, confirmation, OTP, credential, payment link, execution, or persistence authority. "
        "Never call a tool."
    ),
    "global": (
        "Extract semantic evidence from one complete customer turn for an active workflow. "
        "Gather missing fields, available workflows, configured labels, and tenant context. "
        "Identify primary intent, configured fields, domain entities, and secondary objectives. "
        "Do not mutate state or execute tools."
    ),
}

FieldExtractionWorkflow = Literal[
    "support", "lead_capture", "reminder", "booking", "order", "payment", "global"
]


def resolve_field_extraction_gateway_mode(
    settings: Settings,
    workflow: FieldExtractionWorkflow,
) -> FieldExtractionGatewayMode:
    if not settings.LLM_GATEWAY_ENABLED:
        return FieldExtractionGatewayMode.LEGACY
    if workflow not in settings.llm_gateway_field_extraction_workflows:
        return FieldExtractionGatewayMode.LEGACY
    return FieldExtractionGatewayMode(settings.LLM_GATEWAY_FIELD_EXTRACTION_MODE)


def build_field_extraction_gateway_request(
    workflow: FieldExtractionWorkflow,
    semantic_input: dict[str, Any],
    response_schema: type[PayloadT],
) -> LLMRequest[PayloadT]:
    return LLMRequest[PayloadT](
        purpose=LLMPurpose.FIELD_EXTRACTION,
        messages=[
            CanonicalLLMMessage(role="system", content=_PROMPTS[workflow]),
            CanonicalLLMMessage(
                role="user",
                content=json.dumps(semantic_input, ensure_ascii=False, separators=(",", ":")),
            ),
        ],
        safe_context={
            "workflow": workflow,
            "missing_field_names": list(semantic_input.get("missing_field_names") or [])[:12],
            "collected_field_names": list(semantic_input.get("collected_field_names") or [])[:12],
        },
        response_schema=response_schema,
        tools=[],
        language=semantic_input.get("language") or None,
        timeout_class=LLMTimeoutClass.STRUCTURED_STANDARD,
        deterministic_fallback=LLMFallbackContract.PRESERVE_STATE,
    )


async def invoke_field_extraction_gateway(
    workflow: FieldExtractionWorkflow,
    semantic_input: dict[str, Any],
    response_schema: type[PayloadT],
    *,
    gateway: LLMGateway | None,
) -> tuple[PayloadT | None, LLMResult | None]:
    if gateway is None:
        from backend.ai.gateway.runtime import get_runtime_llm_gateway

        gateway = get_runtime_llm_gateway()
    mode = resolve_field_extraction_gateway_mode(settings, workflow)
    rollout_mode = "shadow" if mode is FieldExtractionGatewayMode.SHADOW else "authoritative"
    with llm_rollout_context(rollout_mode):
        result = await gateway.invoke(
            build_field_extraction_gateway_request(workflow, semantic_input, response_schema)
        )
    if result.error is not None or result.parse_status is not LLMParseStatus.SUCCESS:
        return None, result
    return response_schema.model_validate(result.content), result


def gateway_payload_to_legacy_dict(payload: GatewayPayload) -> dict[str, Any]:
    value = payload.model_dump()
    value["field_updates"] = payload.field_updates.model_dump(exclude_none=True)
    value["field_confidence"] = payload.field_confidence.model_dump(exclude_none=True)
    return value


def _confidence_bucket(value: float) -> str:
    if value < 0.5:
        return "low"
    if value < 0.8:
        return "medium"
    return "high"


def record_field_extraction_shadow_comparison(
    workflow: str,
    legacy: Any,
    gateway_candidate: Any | None,
    result: LLMResult | None,
    recorder: Callable[[dict[str, Any]], None] | None,
) -> None:
    legacy_fields = sorted(str(key) for key in getattr(legacy, "field_updates", {}))
    gateway_fields = sorted(str(key) for key in getattr(gateway_candidate, "field_updates", {}))
    metadata = {
        "workflow": workflow,
        "legacy_success": getattr(legacy, "workflow", "unknown") == workflow,
        "gateway_success": gateway_candidate is not None
        and getattr(gateway_candidate, "workflow", "unknown") == workflow,
        "gateway_provider": result.provider if result else None,
        "gateway_fallback_used": bool(result and result.fallback_used),
        "gateway_parse_status": result.parse_status.value if result else "failed",
        "legacy_field_names": legacy_fields,
        "gateway_field_names": gateway_fields,
        "field_presence_equal": legacy_fields == gateway_fields,
        "clarification_equal": bool(getattr(legacy, "clarification_needed", True))
        == bool(getattr(gateway_candidate, "clarification_needed", True)),
        "confidence_bucket_equal": _confidence_bucket(float(getattr(legacy, "confidence", 0.0)))
        == _confidence_bucket(float(getattr(gateway_candidate, "confidence", 0.0))),
    }
    if workflow == "reminder":
        for name in ("reminder_date", "reminder_time"):
            metadata[f"{name.removeprefix('reminder_')}_presence_equal"] = (
                (name in legacy_fields) == (name in gateway_fields)
            )
        metadata["timezone_presence_equal"] = True
    elif workflow == "booking":
        for name in ("service_name", "booking_date", "booking_time"):
            metadata[f"{name.removesuffix('_name').removeprefix('booking_')}_presence_equal"] = (
                (name in legacy_fields) == (name in gateway_fields)
            )
        contact_fields = {"customer_name", "phone", "email"}
        metadata["contact_presence_equal"] = bool(contact_fields.intersection(legacy_fields)) == bool(
            contact_fields.intersection(gateway_fields)
        )
    elif workflow == "order":
        metadata["item_count_bucket_equal"] = bool(
            {"item_name", "item_sku"}.intersection(legacy_fields)
        ) == bool({"item_name", "item_sku"}.intersection(gateway_fields))
        for name in ("fulfillment_type", "payment_method"):
            metadata[f"{name}_presence_equal"] = (
                (name in legacy_fields) == (name in gateway_fields)
            )
    try:
        if recorder is not None:
            recorder(metadata)
        else:
            accepted = bool(metadata["gateway_success"])
            emit_configured_event(LLMShadowComparisonEvent(
                purpose=LLMPurpose.FIELD_EXTRACTION,
                workflow_category=workflow,
                field_name_set_equal=metadata["field_presence_equal"],
                confidence_bucket_equal=metadata["confidence_bucket_equal"],
                candidate_valid=accepted,
                caller_validation=(
                    LLMCallerValidationOutcome.ACCEPTED
                    if accepted else LLMCallerValidationOutcome.REJECTED_BUSINESS_VALIDATION
                ),
                **safe_correlation_fields(settings.LLM_TELEMETRY_CORRELATION_KEY),
            ), settings)
    except Exception:
        return None


class GlobalConfiguredFieldCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_key: str = Field(min_length=1, max_length=64)
    extracted_value: Any
    confidence: float = Field(ge=0.0, le=1.0)


class GlobalDomainEntityCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_key: str = Field(min_length=1, max_length=64)
    match_label: str | None = Field(default=None, min_length=1, max_length=120)
    match_id: str | None = Field(default=None, min_length=1, max_length=120)
    confidence: float = Field(ge=0.0, le=1.0)


class GlobalSecondaryObjective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective_type: Literal["faq", "human_handoff"] = "faq"
    question_text: str | None = Field(default=None, min_length=1, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)


class GlobalWorkflowTurnGatewayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow: str = Field(min_length=1, max_length=64)
    turn_intent: Literal["continue", "change", "cancel", "switch", "clarify"] = "continue"
    confidence: float = Field(ge=0.0, le=1.0)

    configured_fields: list[GlobalConfiguredFieldCandidate] = Field(default_factory=list, max_length=20)
    domain_entities: list[GlobalDomainEntityCandidate] = Field(default_factory=list, max_length=20)
    secondary_objectives: list[GlobalSecondaryObjective] = Field(default_factory=list, max_length=5)

    clarification_needed: bool = False
    clarification_reason: str | None = Field(default=None, max_length=120)


# ---------------------------------------------------------------------------
# Iteration 2: Dynamic, tenant-schema-aware, complete-turn semantic contract.
#
# This contract is non-authoritative. It inspects the complete customer
# utterance and proposes candidates for every relevant configured field.
# It never mutates state, persists values, executes tools, or produces
# customer-visible text. It is additive to the existing gateway models.
# ---------------------------------------------------------------------------

# Exact configured-field type vocabulary already supported by the repository.
DYNAMIC_TURN_SUPPORTED_SCALAR_TYPES = frozenset({
    "string", "number", "date", "time", "email", "phone", "select",
})
_DYNAMIC_TURN_PROMPT = (
    "Extract semantic field evidence from one COMPLETE customer utterance for an active workflow. "
    "The semantic input contains the complete configured-field workspace for this workflow: every "
    "configured field key, its display label, optional semantic description, data type, required "
    "status, allowed options, and safe validation hints. It also contains currently collected field "
    "keys, missing required field keys, customer text, channel, language, and tenant timezone. "
    "Inspect the complete customer utterance and return every clear candidate for any configured "
    "field. Each candidate must use an exact configured field key, a supported scalar or list of "
    "scalar values, a confidence between 0.0 and 1.0, and the exact customer wording as evidence. "
    "Do not require punctuation, sentence length, temporal signal counts, or a richness heuristic to "
    "return multiple candidates. Nested objects are forbidden. Unknown properties are forbidden. "
    "Do not mutate state, persist values, execute tools, or produce a customer-visible response. "
    "The backend owns availability, working hours, prices, stock, OTP, and confirmation validation."
)


class DynamicTurnConfiguredFieldSpec(BaseModel):
    """Safe, read-only description of one configured tenant field sent to the gateway."""

    model_config = ConfigDict(extra="forbid")

    field_key: str = Field(min_length=1, max_length=64)
    display_label: str = Field(min_length=1, max_length=200)
    data_type: str = Field(min_length=1, max_length=40)
    required: bool = False
    semantic_description: str | None = Field(default=None, max_length=500)
    allowed_options: tuple[str, ...] | None = Field(default=None, max_length=40)
    validation_hint: str | None = Field(default=None, max_length=300)


class DynamicTurnFieldCandidate(BaseModel):
    """One strict field candidate returned by the dynamic turn contract."""

    model_config = ConfigDict(extra="forbid")

    field_key: str = Field(min_length=1, max_length=64)
    candidate_value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_text: str = Field(min_length=1, max_length=1000)
    ambiguity_reason: str | None = Field(default=None, max_length=300)


class DynamicTurnContractPayload(BaseModel):
    """Strict complete-turn semantic output. Rejects unknown properties."""

    model_config = ConfigDict(extra="forbid")

    candidates: tuple[DynamicTurnFieldCandidate, ...] = Field(
        default_factory=tuple, max_length=64
    )
    clarification_needed: bool = False
    clarification_reason: str | None = Field(default=None, max_length=300)


class DynamicTurnValidationResult(BaseModel):
    """Typed, fail-closed result of the dynamic turn contract."""

    model_config = ConfigDict(extra="forbid")

    accepted_candidates: list[DynamicTurnFieldCandidate] = Field(default_factory=list)
    rejected: bool = False
    rejection_reason: str | None = Field(default=None, max_length=300)
    gateway_unavailable: bool = False
    clarification_needed: bool = False
    clarification_reason: str | None = Field(default=None, max_length=300)


def build_dynamic_turn_gateway_request(
    workflow: str,
    semantic_input: dict[str, Any],
    response_schema: type[PayloadT],
) -> LLMRequest[PayloadT]:
    """Build a centralized gateway request for the dynamic complete-turn contract.

    The focus field, when supplied, is a hint and must not reduce the configured
    field list nor restrict extraction. The prompt states this explicitly.
    """
    focus = semantic_input.get("focus_field_key")
    if focus:
        system_content = (
            _DYNAMIC_TURN_PROMPT + "\n\n"
            "The focus field indicates the information currently being discussed. "
            "It does not restrict extraction. "
            "Inspect the complete customer utterance and return every clear candidate "
            "for any configured field."
        )
    else:
        system_content = _DYNAMIC_TURN_PROMPT
    safe_context: dict[str, Any] = {
        "workflow": workflow,
        "collected_field_keys": list(semantic_input.get("collected_field_keys") or [])[:32],
        "missing_required_field_keys": list(
            semantic_input.get("missing_required_field_keys") or []
        )[:32],
    }
    if focus:
        safe_context["focus_field_key"] = str(focus)[:64]
    return LLMRequest[PayloadT](
        purpose=LLMPurpose.FIELD_EXTRACTION,
        messages=[
            CanonicalLLMMessage(role="system", content=system_content),
            CanonicalLLMMessage(
                role="user",
                content=json.dumps(semantic_input, ensure_ascii=False, separators=(",", ":")),
            ),
        ],
        safe_context=safe_context,
        response_schema=response_schema,
        tools=[],
        language=semantic_input.get("language") or None,
        timeout_class=LLMTimeoutClass.STRUCTURED_STANDARD,
        deterministic_fallback=LLMFallbackContract.PRESERVE_STATE,
    )


def validate_dynamic_turn_candidates(
    payload: DynamicTurnContractPayload,
    configured_keys: set[str],
) -> DynamicTurnValidationResult:
    """Deterministically validate complete-turn candidates against exact configured keys.

    Rejects the whole payload safely when it contains an unknown field key, a
    duplicate field key, a malformed candidate value, an out-of-range confidence,
    a nested object value, or a candidate key absent from the configured schema.
    """
    seen: set[str] = set()
    accepted: list[DynamicTurnFieldCandidate] = []
    for candidate in payload.candidates:
        if candidate.field_key not in configured_keys:
            return DynamicTurnValidationResult(
                rejected=True,
                rejection_reason=(
                    "candidate field key not configured: " + candidate.field_key
                ),
            )
        if candidate.field_key in seen:
            return DynamicTurnValidationResult(
                rejected=True,
                rejection_reason="duplicate candidate field key: " + candidate.field_key,
            )
        seen.add(candidate.field_key)
        if not _dynamic_turn_value_is_valid(candidate.candidate_value):
            return DynamicTurnValidationResult(
                rejected=True,
                rejection_reason="malformed candidate value for: " + candidate.field_key,
            )
        if not (0.0 <= candidate.confidence <= 1.0):
            return DynamicTurnValidationResult(
                rejected=True,
                rejection_reason="confidence out of range for: " + candidate.field_key,
            )
        accepted.append(candidate)
    return DynamicTurnValidationResult(
        accepted_candidates=accepted,
        rejected=False,
        clarification_needed=payload.clarification_needed,
        clarification_reason=payload.clarification_reason,
    )


def _dynamic_turn_value_is_valid(value: Any) -> bool:
    """Allowed values are scalars or lists of scalars. Nested objects are forbidden."""
    if isinstance(value, (str, int, float, bool)) and not isinstance(value, type(None)):
        return True
    if isinstance(value, list):
        return all(
            isinstance(item, (str, int, float, bool)) for item in value
        )
    return False


__all__ = [
    "FieldExtractionGatewayMode",
    "BookingGatewayPayload",
    "LeadGatewayPayload",
    "OrderGatewayPayload",
    "PaymentGatewayPayload",
    "ReminderGatewayPayload",
    "SupportGatewayPayload",
    "GlobalConfiguredFieldCandidate",
    "GlobalDomainEntityCandidate",
    "GlobalSecondaryObjective",
    "GlobalWorkflowTurnGatewayPayload",
    "DynamicTurnConfiguredFieldSpec",
    "DynamicTurnFieldCandidate",
    "DynamicTurnContractPayload",
    "DynamicTurnValidationResult",
    "build_field_extraction_gateway_request",
    "build_dynamic_turn_gateway_request",
    "gateway_payload_to_legacy_dict",
    "invoke_field_extraction_gateway",
    "record_field_extraction_shadow_comparison",
    "resolve_field_extraction_gateway_mode",
    "validate_dynamic_turn_candidates",
]
