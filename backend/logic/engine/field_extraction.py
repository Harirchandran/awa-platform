from __future__ import annotations

import asyncio
import json
from types import MappingProxyType
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage

from backend.ai.security import sanitize_untrusted_text
from backend.core.config import settings
from backend.core.context import ExecutionContext
from backend.logic.engine.llm import record_langchain_usage
from backend.ops.logger import logger


def _legacy_llm_or_default(legacy_llm: Any | None) -> Any:
    """Resolve the old model only after an explicit legacy/shadow branch wins."""
    if legacy_llm is not None:
        return legacy_llm
    from backend.logic.engine.llm import get_llm

    return get_llm()

OrderWorkflow = Literal["order", "unknown"]
OrderWorkflowAction = Literal["continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"]
BookingWorkflow = Literal["booking", "unknown"]
BookingWorkflowAction = Literal["continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"]
LeadWorkflow = Literal["lead_capture", "unknown"]
LeadWorkflowAction = Literal["continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"]
SupportWorkflow = Literal["support", "unknown"]
SupportWorkflowAction = Literal["continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"]
ReminderWorkflow = Literal["reminder", "unknown"]
ReminderWorkflowAction = Literal["continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"]
ReviewWorkflow = Literal["review", "unknown"]
ReviewWorkflowAction = Literal["continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"]
PaymentWorkflow = Literal["payment", "unknown"]
PaymentWorkflowAction = Literal["continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"]

VALID_ORDER_WORKFLOWS = {"order", "unknown"}
VALID_ORDER_WORKFLOW_ACTIONS = {"continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"}
VALID_BOOKING_WORKFLOWS = {"booking", "unknown"}
VALID_BOOKING_WORKFLOW_ACTIONS = {"continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"}
VALID_LEAD_WORKFLOWS = {"lead_capture", "unknown"}
VALID_LEAD_WORKFLOW_ACTIONS = {"continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"}
VALID_SUPPORT_WORKFLOWS = {"support", "unknown"}
VALID_SUPPORT_WORKFLOW_ACTIONS = {"continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"}
VALID_REMINDER_WORKFLOWS = {"reminder", "unknown"}
VALID_REMINDER_WORKFLOW_ACTIONS = {"continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"}
VALID_REVIEW_WORKFLOWS = {"review", "unknown"}
VALID_REVIEW_WORKFLOW_ACTIONS = {"continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"}
VALID_PAYMENT_WORKFLOWS = {"payment", "unknown"}
VALID_PAYMENT_WORKFLOW_ACTIONS = {"continue", "tangent", "switch", "cancel", "clarify", "change", "unknown"}
UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
INTERNAL_KEY_PATTERN = re.compile(r"(^id$|_id$|uuid|token|secret|trace|raw|payload|backend)", re.IGNORECASE)
INTERNAL_TEXT_PATTERN = re.compile(
    r"\b(?:tenant|customer|lead|ticket|booking|order|service|staff|resource|user|reminder|review)_id\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OrderFieldExtraction:
    detected_language: Optional[str] = None
    workflow: OrderWorkflow = "unknown"
    workflow_action: OrderWorkflowAction = "unknown"
    confidence: float = 0.0
    field_updates: Dict[str, Any] = field(default_factory=dict)
    field_confidence: Dict[str, float] = field(default_factory=dict)
    clarification_needed: bool = True
    clarification_fields: List[str] = field(default_factory=list)
    safe_response_hint: Optional[str] = None


@dataclass(frozen=True)
class BookingFieldExtraction:
    detected_language: Optional[str] = None
    workflow: BookingWorkflow = "unknown"
    workflow_action: BookingWorkflowAction = "unknown"
    confidence: float = 0.0
    field_updates: Dict[str, Any] = field(default_factory=dict)
    field_confidence: Dict[str, float] = field(default_factory=dict)
    clarification_needed: bool = True
    clarification_fields: List[str] = field(default_factory=list)
    safe_response_hint: Optional[str] = None


@dataclass(frozen=True)
class LeadFieldExtraction:
    detected_language: Optional[str] = None
    workflow: LeadWorkflow = "unknown"
    workflow_action: LeadWorkflowAction = "unknown"
    confidence: float = 0.0
    field_updates: Dict[str, Any] = field(default_factory=dict)
    field_confidence: Dict[str, float] = field(default_factory=dict)
    clarification_needed: bool = True
    clarification_fields: List[str] = field(default_factory=list)
    safe_response_hint: Optional[str] = None


@dataclass(frozen=True)
class SupportFieldExtraction:
    detected_language: Optional[str] = None
    workflow: SupportWorkflow = "unknown"
    workflow_action: SupportWorkflowAction = "unknown"
    confidence: float = 0.0
    field_updates: Dict[str, Any] = field(default_factory=dict)
    field_confidence: Dict[str, float] = field(default_factory=dict)
    clarification_needed: bool = True
    clarification_fields: List[str] = field(default_factory=list)
    safe_response_hint: Optional[str] = None


@dataclass(frozen=True)
class ReminderFieldExtraction:
    detected_language: Optional[str] = None
    workflow: ReminderWorkflow = "unknown"
    workflow_action: ReminderWorkflowAction = "unknown"
    confidence: float = 0.0
    field_updates: Dict[str, Any] = field(default_factory=dict)
    field_confidence: Dict[str, float] = field(default_factory=dict)
    clarification_needed: bool = True
    clarification_fields: List[str] = field(default_factory=list)
    safe_response_hint: Optional[str] = None


@dataclass(frozen=True)
class ReviewFieldExtraction:
    detected_language: Optional[str] = None
    workflow: ReviewWorkflow = "unknown"
    workflow_action: ReviewWorkflowAction = "unknown"
    confidence: float = 0.0
    field_updates: Dict[str, Any] = field(default_factory=dict)
    field_confidence: Dict[str, float] = field(default_factory=dict)
    clarification_needed: bool = True
    clarification_fields: List[str] = field(default_factory=list)
    safe_response_hint: Optional[str] = None


@dataclass(frozen=True)
class PaymentFieldExtraction:
    detected_language: Optional[str] = None
    workflow: PaymentWorkflow = "unknown"
    workflow_action: PaymentWorkflowAction = "unknown"
    confidence: float = 0.0
    field_updates: Dict[str, Any] = field(default_factory=dict)
    field_confidence: Dict[str, float] = field(default_factory=dict)
    clarification_needed: bool = True
    clarification_fields: List[str] = field(default_factory=list)
    safe_response_hint: Optional[str] = None


def _extract_json_object(text: str) -> Dict[str, Any]:
    candidate = str(text or "").strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end < start:
            raise
        parsed = json.loads(candidate[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("field extraction response must be a JSON object")
    return parsed


def _coerce_extraction(payload: Dict[str, Any]) -> OrderFieldExtraction:
    workflow = str(payload.get("workflow") or "unknown").strip().lower()
    if workflow not in VALID_ORDER_WORKFLOWS:
        workflow = "unknown"
    action = str(payload.get("workflow_action") or "unknown").strip().lower()
    if action not in VALID_ORDER_WORKFLOW_ACTIONS:
        action = "unknown"
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))

    field_updates = payload.get("field_updates")
    if not isinstance(field_updates, dict):
        field_updates = {}

    raw_field_confidence = payload.get("field_confidence")
    field_confidence: Dict[str, float] = {}
    if isinstance(raw_field_confidence, dict):
        for key, value in raw_field_confidence.items():
            try:
                score = float(value)
            except (TypeError, ValueError):
                continue
            field_confidence[str(key)] = min(1.0, max(0.0, score))

    raw_fields = payload.get("clarification_fields")
    clarification_fields = [str(item) for item in raw_fields if item] if isinstance(raw_fields, list) else []

    hint = payload.get("safe_response_hint")
    if hint is not None:
        hint = sanitize_untrusted_text(str(hint), max_chars=300)

    detected_language = payload.get("detected_language")
    if detected_language is not None:
        detected_language = sanitize_untrusted_text(str(detected_language), max_chars=80)

    return OrderFieldExtraction(
        detected_language=detected_language or None,
        workflow=workflow,  # type: ignore[arg-type]
        workflow_action=action,  # type: ignore[arg-type]
        confidence=confidence,
        field_updates=field_updates,
        field_confidence=field_confidence,
        clarification_needed=bool(payload.get("clarification_needed", False)),
        clarification_fields=clarification_fields[:8],
        safe_response_hint=hint or None,
    )


def _coerce_booking_extraction(payload: Dict[str, Any]) -> BookingFieldExtraction:
    workflow = str(payload.get("workflow") or "unknown").strip().lower()
    if workflow not in VALID_BOOKING_WORKFLOWS:
        workflow = "unknown"
    action = str(payload.get("workflow_action") or "unknown").strip().lower()
    if action not in VALID_BOOKING_WORKFLOW_ACTIONS:
        action = "unknown"
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))

    field_updates = payload.get("field_updates")
    if not isinstance(field_updates, dict):
        field_updates = {}

    raw_field_confidence = payload.get("field_confidence")
    field_confidence: Dict[str, float] = {}
    if isinstance(raw_field_confidence, dict):
        for key, value in raw_field_confidence.items():
            try:
                score = float(value)
            except (TypeError, ValueError):
                continue
            field_confidence[str(key)] = min(1.0, max(0.0, score))

    raw_fields = payload.get("clarification_fields")
    clarification_fields = [str(item) for item in raw_fields if item] if isinstance(raw_fields, list) else []

    hint = payload.get("safe_response_hint")
    if hint is not None:
        hint = sanitize_untrusted_text(str(hint), max_chars=300)

    detected_language = payload.get("detected_language")
    if detected_language is not None:
        detected_language = sanitize_untrusted_text(str(detected_language), max_chars=80)

    return BookingFieldExtraction(
        detected_language=detected_language or None,
        workflow=workflow,  # type: ignore[arg-type]
        workflow_action=action,  # type: ignore[arg-type]
        confidence=confidence,
        field_updates=field_updates,
        field_confidence=field_confidence,
        clarification_needed=bool(payload.get("clarification_needed", False)),
        clarification_fields=clarification_fields[:8],
        safe_response_hint=hint or None,
    )


def _coerce_lead_extraction(payload: Dict[str, Any]) -> LeadFieldExtraction:
    workflow = str(payload.get("workflow") or "unknown").strip().lower()
    if workflow not in VALID_LEAD_WORKFLOWS:
        workflow = "unknown"
    action = str(payload.get("workflow_action") or "unknown").strip().lower()
    if action not in VALID_LEAD_WORKFLOW_ACTIONS:
        action = "unknown"
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))

    field_updates = payload.get("field_updates")
    if not isinstance(field_updates, dict):
        field_updates = {}

    raw_field_confidence = payload.get("field_confidence")
    field_confidence: Dict[str, float] = {}
    if isinstance(raw_field_confidence, dict):
        for key, value in raw_field_confidence.items():
            try:
                score = float(value)
            except (TypeError, ValueError):
                continue
            field_confidence[str(key)] = min(1.0, max(0.0, score))

    raw_fields = payload.get("clarification_fields")
    clarification_fields = [str(item) for item in raw_fields if item] if isinstance(raw_fields, list) else []

    hint = payload.get("safe_response_hint")
    if hint is not None:
        hint = sanitize_untrusted_text(str(hint), max_chars=300)

    detected_language = payload.get("detected_language")
    if detected_language is not None:
        detected_language = sanitize_untrusted_text(str(detected_language), max_chars=80)

    return LeadFieldExtraction(
        detected_language=detected_language or None,
        workflow=workflow,  # type: ignore[arg-type]
        workflow_action=action,  # type: ignore[arg-type]
        confidence=confidence,
        field_updates=field_updates,
        field_confidence=field_confidence,
        clarification_needed=bool(payload.get("clarification_needed", False)),
        clarification_fields=clarification_fields[:8],
        safe_response_hint=hint or None,
    )


def _coerce_support_extraction(payload: Dict[str, Any]) -> SupportFieldExtraction:
    workflow = str(payload.get("workflow") or "unknown").strip().lower()
    if workflow not in VALID_SUPPORT_WORKFLOWS:
        workflow = "unknown"
    action = str(payload.get("workflow_action") or "unknown").strip().lower()
    if action not in VALID_SUPPORT_WORKFLOW_ACTIONS:
        action = "unknown"
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))

    field_updates = payload.get("field_updates")
    if not isinstance(field_updates, dict):
        field_updates = {}

    raw_field_confidence = payload.get("field_confidence")
    field_confidence: Dict[str, float] = {}
    if isinstance(raw_field_confidence, dict):
        for key, value in raw_field_confidence.items():
            try:
                score = float(value)
            except (TypeError, ValueError):
                continue
            field_confidence[str(key)] = min(1.0, max(0.0, score))

    raw_fields = payload.get("clarification_fields")
    clarification_fields = [str(item) for item in raw_fields if item] if isinstance(raw_fields, list) else []

    hint = payload.get("safe_response_hint")
    if hint is not None:
        hint = sanitize_untrusted_text(str(hint), max_chars=300)

    detected_language = payload.get("detected_language")
    if detected_language is not None:
        detected_language = sanitize_untrusted_text(str(detected_language), max_chars=80)

    return SupportFieldExtraction(
        detected_language=detected_language or None,
        workflow=workflow,  # type: ignore[arg-type]
        workflow_action=action,  # type: ignore[arg-type]
        confidence=confidence,
        field_updates=field_updates,
        field_confidence=field_confidence,
        clarification_needed=bool(payload.get("clarification_needed", False)),
        clarification_fields=clarification_fields[:8],
        safe_response_hint=hint or None,
    )


def _coerce_reminder_extraction(payload: Dict[str, Any]) -> ReminderFieldExtraction:
    workflow = str(payload.get("workflow") or "unknown").strip().lower()
    if workflow not in VALID_REMINDER_WORKFLOWS:
        workflow = "unknown"
    action = str(payload.get("workflow_action") or "unknown").strip().lower()
    if action not in VALID_REMINDER_WORKFLOW_ACTIONS:
        action = "unknown"
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))
    field_updates = payload.get("field_updates")
    if not isinstance(field_updates, dict):
        field_updates = {}
    field_confidence = _coerce_field_confidence(payload.get("field_confidence"))
    clarification_fields = _coerce_clarification_fields(payload.get("clarification_fields"))
    return ReminderFieldExtraction(
        detected_language=_coerce_safe_text(payload.get("detected_language"), 80),
        workflow=workflow,  # type: ignore[arg-type]
        workflow_action=action,  # type: ignore[arg-type]
        confidence=confidence,
        field_updates=field_updates,
        field_confidence=field_confidence,
        clarification_needed=bool(payload.get("clarification_needed", False)),
        clarification_fields=clarification_fields,
        safe_response_hint=_coerce_safe_text(payload.get("safe_response_hint"), 300),
    )


def _coerce_review_extraction(payload: Dict[str, Any]) -> ReviewFieldExtraction:
    workflow = str(payload.get("workflow") or "unknown").strip().lower()
    if workflow not in VALID_REVIEW_WORKFLOWS:
        workflow = "unknown"
    action = str(payload.get("workflow_action") or "unknown").strip().lower()
    if action not in VALID_REVIEW_WORKFLOW_ACTIONS:
        action = "unknown"
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))
    field_updates = payload.get("field_updates")
    if not isinstance(field_updates, dict):
        field_updates = {}
    field_confidence = _coerce_field_confidence(payload.get("field_confidence"))
    clarification_fields = _coerce_clarification_fields(payload.get("clarification_fields"))
    return ReviewFieldExtraction(
        detected_language=_coerce_safe_text(payload.get("detected_language"), 80),
        workflow=workflow,  # type: ignore[arg-type]
        workflow_action=action,  # type: ignore[arg-type]
        confidence=confidence,
        field_updates=field_updates,
        field_confidence=field_confidence,
        clarification_needed=bool(payload.get("clarification_needed", False)),
        clarification_fields=clarification_fields,
        safe_response_hint=_coerce_safe_text(payload.get("safe_response_hint"), 300),
    )


# ---------------------------------------------------------------------------
# Payment extraction unsafe-field blocklist (applied before any field touches state)
# ---------------------------------------------------------------------------
_PAYMENT_UNSAFE_FIELDS: frozenset = frozenset({
    "provider", "payment_type", "type",
    "payment_status", "status", "paid", "success", "captured", "failed", "refunded",
    "payment_link_url", "provider_payment_id", "provider_order_id", "transaction_id",
    "signature", "webhook_payload", "refund_id", "settlement_id",
    "tenant_id", "user_id", "customer_id", "order_id", "booking_id", "invoice_id",
    "card_number", "cvv", "upi_pin", "otp", "bank_password",
    "provider_credential", "api_key", "webhook_secret",
})

# Conservative shape-based detector for obvious payment credentials in raw customer text.
# Deterministic patterns only — no word lists, no language assumptions.
_CREDENTIAL_SHAPE_PATTERN = re.compile(
    r"(?:"
    r"\b\d{13,19}\b"                             # 13-19 digit sequences (card-like)
    r"|\b(?:\d[ -]?){12,18}\d\b"                # grouped card-like digits
    r"|(?:cvv|cvc|csc|otp|pin)\s*[=:\-]?\s*\d{3,8}"  # label=digits patterns
    r"|(?:api[_ -]?key|secret|token|password)\s*[=:\-]\s*\S{4,}"  # labelled secrets
    r"|upi://[a-zA-Z0-9._%+\-@]+"               # UPI URI scheme
    r")",
    re.IGNORECASE,
)


def _payment_text_contains_credential_shape(text: str) -> bool:
    """Return True if the raw customer text contains obvious credential-shaped data."""
    return bool(_CREDENTIAL_SHAPE_PATTERN.search(text))


def _coerce_payment_extraction(payload: Dict[str, Any]) -> "PaymentFieldExtraction":
    workflow = str(payload.get("workflow") or "unknown").strip().lower()
    if workflow not in VALID_PAYMENT_WORKFLOWS:
        workflow = "unknown"
    action = str(payload.get("workflow_action") or "unknown").strip().lower()
    if action not in VALID_PAYMENT_WORKFLOW_ACTIONS:
        action = "unknown"
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))
    field_updates = payload.get("field_updates")
    if not isinstance(field_updates, dict):
        field_updates = {}
    # Reject entire extraction if any unsafe/sensitive/internal field or UUID is present (fail-closed)
    for key, value in field_updates.items():
        key_str = str(key)
        val_str = str(value or "")
        if key_str.lower() in _PAYMENT_UNSAFE_FIELDS or UUID_PATTERN.search(key_str) or UUID_PATTERN.search(val_str):
            return PaymentFieldExtraction(
                workflow="unknown",
                workflow_action="unknown",
                confidence=0.0,
                field_updates={},
                field_confidence={},
                clarification_needed=True,
                clarification_fields=[],
                safe_response_hint=None,
            )
    field_confidence = _coerce_field_confidence(payload.get("field_confidence"))
    clarification_fields = _coerce_clarification_fields(payload.get("clarification_fields"))
    return PaymentFieldExtraction(
        detected_language=_coerce_safe_text(payload.get("detected_language"), 80),
        workflow=workflow,  # type: ignore[arg-type]
        workflow_action=action,  # type: ignore[arg-type]
        confidence=confidence,
        field_updates=field_updates,
        field_confidence=field_confidence,
        clarification_needed=bool(payload.get("clarification_needed", False)),
        clarification_fields=clarification_fields,
        safe_response_hint=_coerce_safe_text(payload.get("safe_response_hint"), 300),
    )


def _coerce_field_confidence(value: Any) -> Dict[str, float]:
    field_confidence: Dict[str, float] = {}
    if isinstance(value, dict):
        for key, score_value in value.items():
            try:
                score = float(score_value)
            except (TypeError, ValueError):
                continue
            field_confidence[str(key)] = min(1.0, max(0.0, score))
    return field_confidence


def _coerce_clarification_fields(value: Any) -> List[str]:
    return [str(item) for item in value if item][:8] if isinstance(value, list) else []


def _coerce_safe_text(value: Any, max_chars: int) -> Optional[str]:
    if value is None:
        return None
    return sanitize_untrusted_text(str(value), max_chars=max_chars) or None


def _safe_language_context(context: ExecutionContext) -> Dict[str, Any]:
    config_data = getattr(getattr(context, "config", None), "config_data", {}) or {}
    incoming = context.incoming_message or {}
    language_keys = (
        "language",
        "customer_language",
        "tenant_language",
        "detected_language",
        "locale",
    )
    return {
        key: sanitize_untrusted_text(value, max_chars=80)
        for key in language_keys
        for value in [incoming.get(key) or config_data.get(key)]
        if value not in (None, "", [], {})
    }


def _redact_internal_text(value: Any, max_chars: int = 300) -> str:
    text = sanitize_untrusted_text(value, max_chars=max_chars) or ""
    return INTERNAL_TEXT_PATTERN.sub("[redacted]", UUID_PATTERN.sub("[redacted]", text))


def _safe_order_catalog(config_data: Dict[str, Any], limit: int = 40) -> List[Dict[str, Any]]:
    modules = config_data.get("modules", {}) if isinstance(config_data, dict) else {}
    order_config = modules.get("order", {}) if isinstance(modules, dict) else {}
    if not isinstance(order_config, dict):
        return []
    items: List[Dict[str, Any]] = []
    for category in order_config.get("categories", []) or []:
        if not isinstance(category, dict) or category.get("enabled", True) is False:
            continue
        for item in category.get("items", []) or []:
            if not isinstance(item, dict) or item.get("enabled", True) is False:
                continue
            name = _redact_internal_text(item.get("name"), max_chars=120)
            if not name:
                continue
            public_item: Dict[str, Any] = {"name": name}
            raw_sku = str(item.get("sku") or "").strip()
            sku = _redact_internal_text(raw_sku, max_chars=60)
            if sku and not UUID_PATTERN.fullmatch(raw_sku):
                public_item["sku"] = sku
            items.append(public_item)
            if len(items) >= limit:
                return items
    return items


def _safe_order_config(context: ExecutionContext) -> Dict[str, Any]:
    config_data = context.config.config_data or {}
    modules = config_data.get("modules", {}) if isinstance(config_data, dict) else {}
    order_config = modules.get("order", {}) if isinstance(modules, dict) else {}
    if not isinstance(order_config, dict):
        order_config = {}
    fulfillment_types = [
        sanitize_untrusted_text(str(value), max_chars=80)
        for value in (order_config.get("fulfillment_types") or [])
        if str(value or "").strip()
    ]
    payment_methods = [
        sanitize_untrusted_text(str(value), max_chars=80)
        for value in (order_config.get("payment_methods") or [])
        if str(value or "").strip()
    ]
    return {
        "enabled": order_config.get("enabled") is True,
        "fulfillment_types": [_redact_internal_text(value, max_chars=80) for value in fulfillment_types],
        "payment_methods": [_redact_internal_text(value, max_chars=80) for value in payment_methods],
        "catalog_items": _safe_order_catalog(config_data),
    }


def _safe_order_state(context: ExecutionContext) -> Dict[str, Any]:
    state = context.session_data.state
    current = state.get_task_step_data("order")
    allowed = {
        "item_name",
        "item_sku",
        "quantity",
        "fulfillment_type",
        "customer_name",
        "phone",
        "delivery_address",
        "notes",
        "payment_method",
        "pending_disambiguation",
    }
    safe: Dict[str, Any] = {}
    for key, value in (current or {}).items():
        if key not in allowed or value in (None, ""):
            continue
        if INTERNAL_KEY_PATTERN.search(str(key)):
            continue
        if key == "pending_disambiguation" and isinstance(value, dict):
            options = value.get("options")
            if isinstance(options, list):
                safe_options = []
                for option in options[:10]:
                    if not isinstance(option, dict):
                        continue
                    safe_option: Dict[str, Any] = {}
                    if option.get("index") is not None:
                        safe_option["index"] = option.get("index")
                    name = _redact_internal_text(option.get("name"), max_chars=120)
                    if name:
                        safe_option["name"] = name
                    raw_sku = str(option.get("sku") or "").strip()
                    sku = _redact_internal_text(raw_sku, max_chars=60)
                    if sku and not UUID_PATTERN.fullmatch(raw_sku):
                        safe_option["sku"] = sku
                    if option.get("price") is not None:
                        safe_option["price"] = option.get("price")
                    if safe_option:
                        safe_options.append(safe_option)
                safe[key] = {"options": safe_options}
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key] = _redact_internal_text(value, max_chars=300) if isinstance(value, str) else value
    return safe


def _safe_tenant_now(context: ExecutionContext) -> Dict[str, Any]:
    config_data = context.config.config_data or {}
    timezone_str = str(config_data.get("timezone") or "UTC")
    try:
        tzinfo = ZoneInfo(timezone_str)
    except Exception:
        tzinfo = timezone.utc
        timezone_str = "UTC"
    now = datetime.now(tzinfo)
    return {
        "timezone": timezone_str,
        "date": now.date().isoformat(),
        "time": now.time().replace(microsecond=0).isoformat(),
        "iso": now.replace(microsecond=0).isoformat(),
    }


def _safe_services(config_data: Dict[str, Any], limit: int = 40) -> List[Dict[str, Any]]:
    services = config_data.get("services", []) if isinstance(config_data, dict) else []
    public_services: List[Dict[str, Any]] = []
    for service in services or []:
        if not isinstance(service, dict):
            continue
        name = _redact_internal_text(service.get("name"), max_chars=120)
        if not name:
            continue
        public_service: Dict[str, Any] = {"name": name}
        price = service.get("price")
        if price not in (None, ""):
            public_service["price"] = price
        duration = service.get("duration_mins") or service.get("duration_minutes")
        if duration not in (None, ""):
            public_service["duration_mins"] = duration
        public_services.append(public_service)
        if len(public_services) >= limit:
            break
    return public_services


def _safe_booking_config(context: ExecutionContext) -> Dict[str, Any]:
    config_data = context.config.config_data or {}
    modules = config_data.get("modules", {}) if isinstance(config_data, dict) else {}
    booking_config = modules.get("booking", {}) if isinstance(modules, dict) else {}
    if not isinstance(booking_config, dict):
        booking_config = {}
    configured_fields = []
    for field in booking_config.get("fields", []) or []:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "")
        if not key or INTERNAL_KEY_PATTERN.search(key):
            continue
        configured_fields.append(
            {
                "key": _redact_internal_text(key, max_chars=80),
                "type": _redact_internal_text(field.get("type") or "string", max_chars=40),
                "required": bool(field.get("required", False)),
            }
        )
    return {
        "enabled": booking_config.get("enabled") is True,
        "configured_fields": configured_fields,
        "services": _safe_services(config_data),
        "tenant_now": _safe_tenant_now(context),
    }


def _safe_booking_state(context: ExecutionContext) -> Dict[str, Any]:
    state = context.session_data.state
    current = state.get_task_step_data("booking")
    allowed = {
        "service_name",
        "preferred_date",
        "preferred_time",
        "booking_date",
        "booking_time",
        "customer_name",
        "patient_name",
        "name",
        "phone",
        "email",
        "notes",
        "special_requests",
    }
    safe: Dict[str, Any] = {}
    for key, value in (current or {}).items():
        if key not in allowed or value in (None, ""):
            continue
        if INTERNAL_KEY_PATTERN.search(str(key)):
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key] = _redact_internal_text(value, max_chars=300) if isinstance(value, str) else value
    return safe


def _safe_module_config(context: ExecutionContext, module_name: str) -> Dict[str, Any]:
    config_data = context.config.config_data or {}
    modules = config_data.get("modules", {}) if isinstance(config_data, dict) else {}
    module_config = modules.get(module_name, {}) if isinstance(modules, dict) else {}
    if not isinstance(module_config, dict):
        module_config = {}
    configured_fields = []
    for field in module_config.get("fields", []) or []:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "")
        if not key or INTERNAL_KEY_PATTERN.search(key):
            continue
        configured_fields.append(
            {
                "key": _redact_internal_text(key, max_chars=80),
                "type": _redact_internal_text(field.get("type") or "string", max_chars=40),
                "required": bool(field.get("required", False)),
            }
        )
    return {
        "enabled": module_config.get("enabled") is True,
        "configured_fields": configured_fields,
    }


def _safe_lead_state(context: ExecutionContext) -> Dict[str, Any]:
    state = context.session_data.state
    current = state.get_task_step_data("lead_capture")
    allowed = {
        "name",
        "customer_name",
        "phone",
        "email",
        "interest",
        "message",
        "preferred_contact_time",
        "source",
        "notes",
    }
    safe: Dict[str, Any] = {}
    for key, value in (current or {}).items():
        if key not in allowed or value in (None, ""):
            continue
        if INTERNAL_KEY_PATTERN.search(str(key)):
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key] = _redact_internal_text(value, max_chars=300) if isinstance(value, str) else value
    return safe


def _safe_support_state(context: ExecutionContext) -> Dict[str, Any]:
    state = context.session_data.state
    current = state.get_task_step_data("support")
    allowed = {
        "name",
        "customer_name",
        "phone",
        "email",
        "issue_summary",
        "support_reason",
        "issue_description",
        "urgency",
        "preferred_contact_time",
        "order_reference",
        "notes",
    }
    safe: Dict[str, Any] = {}
    for key, value in (current or {}).items():
        if key not in allowed or value in (None, ""):
            continue
        if INTERNAL_KEY_PATTERN.search(str(key)):
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key] = _redact_internal_text(value, max_chars=300) if isinstance(value, str) else value
    return safe


def _safe_reminder_state(context: ExecutionContext) -> Dict[str, Any]:
    current = context.session_data.state.get_task_step_data("reminders")
    allowed = {
        "reminder_type",
        "time_offset",
        "scheduled_for",
        "schedule_at",
        "message_text",
        "message",
        "note",
        "channel",
        "reference",
        "customer_name",
        "phone",
        "email",
        "notes",
    }
    safe: Dict[str, Any] = {}
    for key, value in (current or {}).items():
        if key not in allowed or value in (None, "") or INTERNAL_KEY_PATTERN.search(str(key)):
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key] = _redact_internal_text(value, max_chars=300) if isinstance(value, str) else value
    return safe


def _safe_payment_config(context: ExecutionContext) -> Dict[str, Any]:
    """Return only public, safe payment configuration for the LLM payload."""
    config_data = context.config.config_data or {}
    modules = config_data.get("modules", {}) if isinstance(config_data, dict) else {}
    payment_config = modules.get("payments", {}) if isinstance(modules, dict) else {}
    if not isinstance(payment_config, dict):
        payment_config = {}
    # Allowed currencies from tenant config — no secrets, no provider IDs
    allowed_currencies = [
        sanitize_untrusted_text(str(c), max_chars=10)
        for c in (payment_config.get("allowed_currencies") or [])
        if str(c or "").strip()
    ]
    default_currency = sanitize_untrusted_text(
        str(payment_config.get("default_currency") or ""), max_chars=10
    ) or None
    # Configured fields (safe public labels only, no internal IDs)
    configured_fields = []
    for f in payment_config.get("fields", []) or []:
        if not isinstance(f, dict):
            continue
        key = str(f.get("key") or "")
        if not key or INTERNAL_KEY_PATTERN.search(key):
            continue
        configured_fields.append({
            "key": _redact_internal_text(key, max_chars=80),
            "type": _redact_internal_text(str(f.get("type") or "string"), max_chars=40),
            "required": bool(f.get("required", False)),
        })
    return {
        "enabled": payment_config.get("enabled") is True,
        "allowed_currencies": [_redact_internal_text(c, max_chars=10) for c in allowed_currencies],
        "default_currency": default_currency,
        "configured_fields": configured_fields,
    }


_PAYMENT_STATE_ALLOWED = frozenset({
    "amount",
    "currency",
    "payment_purpose",
    "customer_name",
    "phone",
    "email",
    "order_reference",
    "booking_reference",
    "invoice_reference",
    "notes",
})


def _safe_payment_state(context: ExecutionContext) -> Dict[str, Any]:
    """Return only safe, public payment step data (no provider IDs, no status fields)."""
    current = context.session_data.state.get_task_step_data("payments")
    safe: Dict[str, Any] = {}
    for key, value in (current or {}).items():
        if key not in _PAYMENT_STATE_ALLOWED or value in (None, ""):
            continue
        if INTERNAL_KEY_PATTERN.search(str(key)):
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key] = _redact_internal_text(value, max_chars=300) if isinstance(value, str) else value
    return safe


def _safe_review_state(context: ExecutionContext) -> Dict[str, Any]:
    current = context.session_data.state.get_task_step_data("reviews")
    allowed = {
        "rating",
        "feedback",
        "feedback_text",
        "customer_name",
        "name",
        "phone",
        "email",
        "order_reference",
        "service_name",
        "notes",
        "reference",
    }
    safe: Dict[str, Any] = {}
    for key, value in (current or {}).items():
        if key not in allowed or value in (None, "") or INTERNAL_KEY_PATTERN.search(str(key)):
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key] = _redact_internal_text(value, max_chars=300) if isinstance(value, str) else value
    return safe


def _safe_requested_focus(context: ExecutionContext, workflow: str) -> Dict[str, Any] | None:
    focus = context.session_data.state.prompt_focus
    if focus is None or focus.workflow != workflow:
        return None
    return {
        "workflow": focus.workflow,
        "requested_fields": list(focus.requested_fields),
        "prompt_kind": focus.prompt_kind,
    }


def _extraction_payload(context: ExecutionContext) -> Dict[str, Any]:
    state = context.session_data.state
    ledger = state.action_ledger
    return {
        "customer_message": _redact_internal_text((context.incoming_message or {}).get("text") or "", max_chars=1200),
        "language_context": _safe_language_context(context),
        "workflow_scope": {
            "current_task": ledger.current_task,
            "current_module": context.session_data.current_module,
            "task_status": ledger.task_status,
            "prompt_focus": _safe_requested_focus(context, "order"),
            "has_pending_order_action": bool(
                ledger.pending_action and str(ledger.pending_action.tool_key or "").startswith("order.")
            ),
        },
        "order_config": _safe_order_config(context),
        "current_order_state": _safe_order_state(context),
        "allowed_field_updates": [
            "item_name",
            "item_sku",
            "items",
            "quantity",
            "fulfillment_type",
            "customer_name",
            "phone",
            "delivery_address",
            "notes",
            "payment_method",
        ],
    }


def _booking_extraction_payload(context: ExecutionContext) -> Dict[str, Any]:
    state = context.session_data.state
    ledger = state.action_ledger
    return {
        "customer_message": _redact_internal_text((context.incoming_message or {}).get("text") or "", max_chars=1200),
        "language_context": _safe_language_context(context),
        "workflow_scope": {
            "current_task": ledger.current_task,
            "current_module": context.session_data.current_module,
            "task_status": ledger.task_status,
            "prompt_focus": _safe_requested_focus(context, "booking"),
            "has_pending_booking_action": bool(
                ledger.pending_action and str(ledger.pending_action.tool_key or "").startswith("booking.")
            ),
        },
        "booking_config": _safe_booking_config(context),
        "current_booking_state": _safe_booking_state(context),
        "allowed_field_updates": [
            "service_name",
            "booking_date",
            "booking_time",
            "customer_name",
            "phone",
            "email",
            "notes",
            "special_requests",
        ],
    }


def _lead_extraction_payload(context: ExecutionContext) -> Dict[str, Any]:
    state = context.session_data.state
    ledger = state.action_ledger
    return {
        "customer_message": _redact_internal_text((context.incoming_message or {}).get("text") or "", max_chars=1200),
        "language_context": _safe_language_context(context),
        "workflow_scope": {
            "current_task": ledger.current_task,
            "current_module": context.session_data.current_module,
            "task_status": ledger.task_status,
            "prompt_focus": _safe_requested_focus(context, "lead_capture"),
            "has_pending_lead_action": bool(
                ledger.pending_action and str(ledger.pending_action.tool_key or "").startswith("lead_capture.")
            ),
        },
        "lead_config": _safe_module_config(context, "lead_capture"),
        "current_lead_state": _safe_lead_state(context),
        "allowed_field_updates": [
            "customer_name",
            "phone",
            "email",
            "interest",
            "message",
            "preferred_contact_time",
            "source",
            "notes",
        ],
    }


def _support_extraction_payload(context: ExecutionContext) -> Dict[str, Any]:
    state = context.session_data.state
    ledger = state.action_ledger
    return {
        "customer_message": _redact_internal_text((context.incoming_message or {}).get("text") or "", max_chars=1200),
        "language_context": _safe_language_context(context),
        "workflow_scope": {
            "current_task": ledger.current_task,
            "current_module": context.session_data.current_module,
            "task_status": ledger.task_status,
            "prompt_focus": _safe_requested_focus(context, "support"),
            "has_pending_support_action": bool(
                ledger.pending_action and str(ledger.pending_action.tool_key or "").startswith("support.")
            ),
        },
        "support_config": {
            **_safe_module_config(context, "support"),
            "urgency_values": ["low", "medium", "high"],
        },
        "current_support_state": _safe_support_state(context),
        "allowed_field_updates": [
            "customer_name",
            "phone",
            "email",
            "issue_summary",
            "support_reason",
            "urgency",
            "preferred_contact_time",
            "order_reference",
            "notes",
        ],
    }


def _reminder_extraction_payload(context: ExecutionContext) -> Dict[str, Any]:
    state = context.session_data.state
    ledger = state.action_ledger
    return {
        "customer_message": _redact_internal_text((context.incoming_message or {}).get("text") or "", max_chars=1200),
        "language_context": _safe_language_context(context),
        "workflow_scope": {
            "current_task": ledger.current_task,
            "current_module": context.session_data.current_module,
            "task_status": ledger.task_status,
            "prompt_focus": _safe_requested_focus(context, "reminders"),
            "has_pending_reminder_action": bool(
                ledger.pending_action and str(ledger.pending_action.tool_key or "").startswith("reminders.")
            ),
        },
        "reminder_config": {
            **_safe_module_config(context, "reminders"),
            "tenant_now": _safe_tenant_now(context),
        },
        "current_reminder_state": _safe_reminder_state(context),
        "allowed_field_updates": [
            "reminder_message",
            "message",
            "scheduled_for",
            "reminder_date",
            "reminder_time",
            "channel",
            "customer_name",
            "phone",
            "email",
            "notes",
        ],
    }


def _review_extraction_payload(context: ExecutionContext) -> Dict[str, Any]:
    state = context.session_data.state
    ledger = state.action_ledger
    return {
        "customer_message": _redact_internal_text((context.incoming_message or {}).get("text") or "", max_chars=1200),
        "language_context": _safe_language_context(context),
        "workflow_scope": {
            "current_task": ledger.current_task,
            "current_module": context.session_data.current_module,
            "task_status": ledger.task_status,
            "prompt_focus": _safe_requested_focus(context, "reviews"),
            "has_pending_review_action": bool(
                ledger.pending_action and str(ledger.pending_action.tool_key or "").startswith("reviews.")
            ),
        },
        "review_config": {
            **_safe_module_config(context, "reviews"),
            "rating_range": {"minimum": 1, "maximum": 5},
        },
        "current_review_state": _safe_review_state(context),
        "allowed_field_updates": [
            "rating",
            "feedback",
            "customer_name",
            "phone",
            "email",
            "order_reference",
            "service_name",
            "notes",
        ],
    }


async def _extract_order_fields_legacy(
    context: ExecutionContext,
    *,
    legacy_llm: Any | None = None,
) -> OrderFieldExtraction:
    """
    Semantically interpret the latest customer message for an active order workflow.

    This only extracts canonical candidate fields. Backend validators and protected
    action gates remain responsible for deciding what can be saved or executed.
    """
    system_prompt = (
        "You extract customer meaning for an active order workflow. "
        "Return only one JSON object with keys: detected_language, workflow, workflow_action, "
        "confidence, field_updates, field_confidence, clarification_needed, clarification_fields, "
        "safe_response_hint. workflow must be order or unknown. workflow_action must be one of "
        "continue, tangent, switch, cancel, clarify, change, unknown. Extract only allowed order "
        "fields. Do not invent catalog items, internal IDs, UUIDs, tenant IDs, backend references, "
        "or actions. Use customer language, configured order options, current state, and the latest "
        "message. If uncertain, use unknown or clarify with low confidence."
    )
    payload = _extraction_payload(context)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ]
    llm = _legacy_llm_or_default(legacy_llm)
    timeout_seconds = float(getattr(settings, "ORDER_FIELD_EXTRACTION_TIMEOUT_SECONDS", 4) or 4)
    try:
        response = await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout_seconds)
    except Exception:
        logger.warning(
            "Semantic order field extraction failed",
            extra={"extra_data": {"active_order": True}},
        )
        raise
    record_langchain_usage(context, response)
    parsed = _extract_json_object(str(getattr(response, "content", "") or ""))
    extraction = _coerce_extraction(parsed)
    logger.info(
        "Semantic order field extraction",
        extra={
            "extra_data": {
                "workflow": extraction.workflow,
                "workflow_action": extraction.workflow_action,
                "confidence": extraction.confidence,
                "field_count": len(extraction.field_updates),
            }
        },
    )
    return extraction


async def _extract_booking_fields_legacy(
    context: ExecutionContext,
    *,
    legacy_llm: Any | None = None,
) -> BookingFieldExtraction:
    """
    Semantically interpret the latest customer message for an active booking workflow.

    This only extracts canonical candidate fields. Backend validators and protected
    action gates remain responsible for deciding what can be saved or executed.
    """
    system_prompt = (
        "You extract customer meaning for an active booking workflow. "
        "Return only one JSON object with keys: detected_language, workflow, workflow_action, "
        "confidence, field_updates, field_confidence, clarification_needed, clarification_fields, "
        "safe_response_hint. workflow must be booking or unknown. workflow_action must be one of "
        "continue, tangent, switch, cancel, clarify, change, unknown. Extract only allowed booking "
        "fields. Use booking_date as YYYY-MM-DD and booking_time as HH:MM in tenant local time when "
        "confident. Do not invent services, internal IDs, UUIDs, tenant IDs, booking IDs, staff IDs, "
        "resource IDs, backend references, or actions. Use customer language, public service labels, "
        "tenant-local current date/time, current state, and the latest message. If uncertain, use "
        "unknown or clarify with low confidence."
    )
    payload = _booking_extraction_payload(context)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ]
    llm = _legacy_llm_or_default(legacy_llm)
    timeout_seconds = float(getattr(settings, "BOOKING_FIELD_EXTRACTION_TIMEOUT_SECONDS", 4) or 4)
    try:
        response = await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout_seconds)
    except Exception:
        logger.warning(
            "Semantic booking field extraction failed",
            extra={"extra_data": {"active_booking": True}},
        )
        raise
    record_langchain_usage(context, response)
    parsed = _extract_json_object(str(getattr(response, "content", "") or ""))
    extraction = _coerce_booking_extraction(parsed)
    logger.info(
        "Semantic booking field extraction",
        extra={
            "extra_data": {
                "workflow": extraction.workflow,
                "workflow_action": extraction.workflow_action,
                "confidence": extraction.confidence,
                "field_count": len(extraction.field_updates),
            }
        },
    )
    return extraction


async def _extract_lead_fields_legacy(
    context: ExecutionContext,
    *,
    legacy_llm: Any | None = None,
) -> LeadFieldExtraction:
    """
    Semantically interpret the latest customer message for an active lead capture workflow.

    This only extracts canonical candidate fields. Backend validators and protected
    action gates remain responsible for deciding what can be saved or executed.
    """
    system_prompt = (
        "You extract customer meaning for an active lead_capture workflow. "
        "Return only one JSON object with keys: detected_language, workflow, workflow_action, "
        "confidence, field_updates, field_confidence, clarification_needed, clarification_fields, "
        "safe_response_hint. workflow must be lead_capture or unknown. workflow_action must be one of "
        "continue, tangent, switch, cancel, clarify, change, unknown. Extract only allowed lead_capture "
        "fields. Do not invent internal IDs, UUIDs, tenant IDs, customer IDs, lead IDs, backend references, "
        "or actions. Use customer language, tenant configuration, current state, and the latest message. "
        "If uncertain, use unknown or clarify with low confidence."
    )
    payload = _lead_extraction_payload(context)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ]
    llm = _legacy_llm_or_default(legacy_llm)
    timeout_seconds = float(getattr(settings, "LEAD_FIELD_EXTRACTION_TIMEOUT_SECONDS", 4) or 4)
    try:
        response = await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout_seconds)
    except Exception:
        logger.warning(
            "Semantic lead field extraction failed",
            extra={"extra_data": {"active_lead_capture": True}},
        )
        raise
    record_langchain_usage(context, response)
    parsed = _extract_json_object(str(getattr(response, "content", "") or ""))
    extraction = _coerce_lead_extraction(parsed)
    logger.info(
        "Semantic lead field extraction",
        extra={
            "extra_data": {
                "workflow": extraction.workflow,
                "workflow_action": extraction.workflow_action,
                "confidence": extraction.confidence,
                "field_count": len(extraction.field_updates),
            }
        },
    )
    return extraction


async def _extract_support_fields_legacy(
    context: ExecutionContext,
    *,
    legacy_llm: Any | None = None,
) -> SupportFieldExtraction:
    """
    Semantically interpret the latest customer message for an active support workflow.

    This only extracts canonical candidate fields. Backend validators and protected
    action gates remain responsible for deciding what can be saved or executed.
    """
    system_prompt = (
        "You extract customer meaning for an active support workflow. "
        "Return only one JSON object with keys: detected_language, workflow, workflow_action, "
        "confidence, field_updates, field_confidence, clarification_needed, clarification_fields, "
        "safe_response_hint. workflow must be support or unknown. workflow_action must be one of "
        "continue, tangent, switch, cancel, clarify, change, unknown. Extract only allowed support "
        "fields. urgency must be one of the configured canonical urgency values when confident. "
        "Do not invent internal IDs, UUIDs, tenant IDs, customer IDs, ticket IDs, backend references, "
        "or actions. Use customer language, tenant configuration, current state, and the latest message. "
        "If uncertain, use unknown or clarify with low confidence."
    )
    payload = _support_extraction_payload(context)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ]
    llm = _legacy_llm_or_default(legacy_llm)
    timeout_seconds = float(getattr(settings, "SUPPORT_FIELD_EXTRACTION_TIMEOUT_SECONDS", 4) or 4)
    try:
        response = await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout_seconds)
    except Exception:
        logger.warning(
            "Semantic support field extraction failed",
            extra={"extra_data": {"active_support": True}},
        )
        raise
    record_langchain_usage(context, response)
    parsed = _extract_json_object(str(getattr(response, "content", "") or ""))
    extraction = _coerce_support_extraction(parsed)
    logger.info(
        "Semantic support field extraction",
        extra={
            "extra_data": {
                "workflow": extraction.workflow,
                "workflow_action": extraction.workflow_action,
                "confidence": extraction.confidence,
                "field_count": len(extraction.field_updates),
            }
        },
    )
    return extraction


async def _extract_reminder_fields_legacy(
    context: ExecutionContext,
    *,
    legacy_llm: Any | None = None,
) -> ReminderFieldExtraction:
    """Semantically interpret the latest message for an active reminder workflow."""
    system_prompt = (
        "You extract customer meaning for an active reminder workflow. "
        "Return only one JSON object with keys: detected_language, workflow, workflow_action, "
        "confidence, field_updates, field_confidence, clarification_needed, clarification_fields, "
        "safe_response_hint. workflow must be reminder or unknown. workflow_action must be one of "
        "continue, tangent, switch, cancel, clarify, change, unknown. Extract only allowed reminder "
        "fields. Use scheduled_for as an ISO datetime, reminder_date as YYYY-MM-DD, and reminder_time "
        "as HH:MM in tenant local time when confident. Do not invent internal IDs, UUIDs, tenant IDs, "
        "customer IDs, reminder IDs, backend references, channels, or actions. Use customer language, "
        "tenant configuration, tenant-local current date/time, current state, and the latest message. "
        "If uncertain, use unknown or clarify with low confidence."
    )
    payload = _reminder_extraction_payload(context)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ]
    llm = _legacy_llm_or_default(legacy_llm)
    timeout_seconds = float(getattr(settings, "REMINDER_FIELD_EXTRACTION_TIMEOUT_SECONDS", 4) or 4)
    try:
        response = await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout_seconds)
    except Exception:
        logger.warning("Semantic reminder field extraction failed", extra={"extra_data": {"active_reminder": True}})
        raise
    record_langchain_usage(context, response)
    extraction = _coerce_reminder_extraction(_extract_json_object(str(getattr(response, "content", "") or "")))
    logger.info(
        "Semantic reminder field extraction",
        extra={"extra_data": {
            "workflow": extraction.workflow,
            "workflow_action": extraction.workflow_action,
            "confidence": extraction.confidence,
            "field_count": len(extraction.field_updates),
        }},
    )
    return extraction


_GATEWAY_CANONICAL_FIELDS: dict[str, tuple[str, ...]] = {
    "booking": (
        "service_name", "booking_date", "booking_time", "customer_name", "phone", "email",
        "notes", "special_requests",
    ),
    "order": (
        "item_name", "item_sku", "quantity", "fulfillment_type", "customer_name", "phone",
        "delivery_address", "notes", "payment_method",
    ),
    "payment": (
        "payment_purpose", "customer_name", "phone", "email", "order_reference",
        "booking_reference", "invoice_reference", "notes",
    ),
    "lead_capture": (
        "customer_name", "phone", "email", "interest", "message",
        "preferred_contact_time", "source", "notes",
    ),
    "support": (
        "customer_name", "phone", "email", "issue_summary", "support_reason", "urgency",
        "preferred_contact_time", "order_reference", "notes",
    ),
    "reminder": (
        "reminder_message", "message", "scheduled_for", "reminder_date", "reminder_time",
        "channel", "customer_name", "phone", "email", "notes",
    ),
}

BOOKING_FIELD_ALIASES = MappingProxyType({
    "patient_name": "customer_name",
    "name": "customer_name",
    "preferred_date": "booking_date",
    "date": "booking_date",
    "preferred_time": "booking_time",
    "time": "booking_time",
})


def canonical_booking_field(field_name: Any) -> str:
    key = str(field_name or "").strip()
    return BOOKING_FIELD_ALIASES.get(key, key)


def _gateway_configured_field_schema(
    context: ExecutionContext,
    module_name: str,
    workflow: str,
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    config_data = context.config.config_data or {}
    modules = config_data.get("modules", {}) if isinstance(config_data, dict) else {}
    module = modules.get(module_name, {}) if isinstance(modules, dict) else {}
    fields = module.get("fields", []) if isinstance(module, dict) else []
    canonical = set(_GATEWAY_CANONICAL_FIELDS[workflow])
    aliases = {
        "name": "customer_name",
        "issue_description": "issue_summary",
        "message_text": "reminder_message",
        "note": "reminder_message",
    }
    safe_fields: list[dict[str, Any]] = []
    allowed_choices: dict[str, list[str]] = {}
    seen_fields: set[str] = set()
    for item in fields or []:
        if not isinstance(item, dict):
            continue
        configured_key = str(item.get("key") or "").strip()
        key = canonical_booking_field(configured_key) if workflow == "booking" else aliases.get(configured_key, configured_key)
        if key not in canonical or INTERNAL_KEY_PATTERN.search(configured_key):
            continue
        if key in seen_fields:
            continue
        seen_fields.add(key)
        options = [
            _redact_internal_text(value, max_chars=120)
            for value in (item.get("options") or [])[:40]
            if isinstance(value, (str, int, float, bool)) and str(value).strip()
        ]
        field_spec: dict[str, Any] = {
            "field_name": key,
            "type": _redact_internal_text(item.get("type") or "string", max_chars=40),
            "required": bool(item.get("required", False)),
        }
        if options:
            field_spec["allowed_choices"] = options
            allowed_choices[key] = options
        safe_fields.append(field_spec)
        if len(safe_fields) >= 12:
            break
    if workflow == "support" and "urgency" not in allowed_choices:
        allowed_choices["urgency"] = ["low", "medium", "high"]
    if workflow == "booking":
        services = _safe_services(config_data)
        allowed_choices["service_name"] = [
            str(service["name"]) for service in services if service.get("name")
        ]
    elif workflow == "order":
        order_config = _safe_order_config(context)
        catalog = order_config.get("catalog_items") or []
        allowed_choices["item_name"] = [
            str(item["name"]) for item in catalog if item.get("name")
        ]
        skus = [str(item["sku"]) for item in catalog if item.get("sku")]
        if skus:
            allowed_choices["item_sku"] = skus
        allowed_choices["fulfillment_type"] = list(order_config.get("fulfillment_types") or [])
        allowed_choices["payment_method"] = list(order_config.get("payment_methods") or [])
    return safe_fields, allowed_choices


def _gateway_semantic_input(
    context: ExecutionContext,
    *,
    workflow: Literal["support", "lead_capture", "reminder", "booking", "order", "payment"],
    module_name: str,
) -> Dict[str, Any]:
    state_getter = {
        "support": _safe_support_state,
        "lead_capture": _safe_lead_state,
        "reminder": _safe_reminder_state,
        "booking": _safe_booking_state,
        "order": _safe_order_state,
        "payment": _safe_payment_state,
    }[workflow]
    current = state_getter(context)
    normalize_field = canonical_booking_field if workflow == "booking" else lambda value: str(value)
    current = {normalize_field(name): value for name, value in current.items()}
    configured_schema, allowed_choices = _gateway_configured_field_schema(
        context, module_name, workflow
    )
    canonical_fields = list(_GATEWAY_CANONICAL_FIELDS[workflow])
    focus = _safe_requested_focus(context, module_name) or {}
    requested = [
        normalize_field(value)
        for value in focus.get("requested_fields", [])
        if normalize_field(value) in canonical_fields
    ][:12]
    collected = [name for name in canonical_fields if name in current][:12]
    missing = requested or [name for name in canonical_fields if name not in current][:12]
    language = _safe_language_context(context).get("detected_language")
    semantic_input: Dict[str, Any] = {
        "customer_turn": _redact_internal_text(
            (context.incoming_message or {}).get("text") or "", max_chars=1200
        ),
        "missing_field_names": missing,
        "collected_field_names": collected,
        "configured_field_schema": configured_schema,
        "allowed_choices": allowed_choices,
        "language": str(language)[:32] if language else None,
    }
    if workflow == "reminder":
        semantic_input["tenant_now"] = _safe_tenant_now(context)
        semantic_input["schedule_constraints"] = {
            "must_be_future": True,
            "backend_resolves_timezone": True,
        }
    elif workflow == "booking":
        semantic_input["configuration_owned_fields"] = ["service_name"]
        semantic_input["configured_service_labels"] = allowed_choices.get("service_name", [])
        semantic_input["tenant_now"] = _safe_tenant_now(context)
        semantic_input["date_time_constraints"] = {
            "backend_resolves_service": True,
            "backend_validates_timezone_working_hours_and_availability": True,
        }
    elif workflow == "order":
        semantic_input["configuration_owned_fields"] = [
            "item_name", "item_sku", "fulfillment_type", "payment_method",
        ]
        semantic_input["catalog_candidates"] = _safe_order_catalog(
            context.config.config_data or {}
        )
        semantic_input["order_constraints"] = {
            "backend_resolves_catalog_and_options": True,
            "backend_validates_quantity_and_calculates_totals": True,
        }
    elif workflow == "payment":
        semantic_input["payment_constraints"] = {
            "backend_owns_provider_type_amount_currency_reference_status_confirmation_and_otp": True,
            "credentials_forbidden": True,
        }
    return semantic_input


def _validated_gateway_payload_dict(payload: Any, semantic_input: Dict[str, Any]) -> Dict[str, Any]:
    from backend.logic.engine.field_extraction_gateway import gateway_payload_to_legacy_dict

    value = gateway_payload_to_legacy_dict(payload)
    updates = dict(value.get("field_updates") or {})
    confidence = dict(value.get("field_confidence") or {})
    allowed_choices = semantic_input.get("allowed_choices") or {}
    configuration_owned = set(semantic_input.get("configuration_owned_fields") or [])
    for key, choices in allowed_choices.items():
        if key not in updates or not isinstance(choices, list):
            continue
        if not choices:
            if key in configuration_owned:
                updates.pop(key, None)
                confidence.pop(key, None)
            continue
        candidate = str(updates[key]).strip().casefold()
        normalized = {str(choice).strip().casefold(): choice for choice in choices}
        if candidate not in normalized:
            updates.pop(key, None)
            confidence.pop(key, None)
        else:
            updates[key] = normalized[candidate]
    value["field_updates"] = updates
    value["field_confidence"] = confidence
    return value


async def _extract_migrated_fields(
    context: ExecutionContext,
    *,
    workflow: Literal["support", "lead_capture", "reminder", "booking", "order", "payment"],
    module_name: str,
    response_schema: Any,
    coercer: Callable[[Dict[str, Any]], Any],
    legacy_extractor: Callable[..., Any],
    gateway: Any | None,
    legacy_llm: Any | None,
    shadow_recorder: Callable[[Dict[str, Any]], None] | None,
) -> Any:
    from backend.logic.engine.field_extraction_gateway import (
        FieldExtractionGatewayMode,
        invoke_field_extraction_gateway,
        record_field_extraction_shadow_comparison,
        resolve_field_extraction_gateway_mode,
    )

    mode = resolve_field_extraction_gateway_mode(settings, workflow)
    if mode is FieldExtractionGatewayMode.LEGACY:
        return await legacy_extractor(context, legacy_llm=legacy_llm)

    semantic_input = _gateway_semantic_input(
        context, workflow=workflow, module_name=module_name
    )
    if mode is FieldExtractionGatewayMode.SHADOW:
        legacy = await legacy_extractor(context, legacy_llm=legacy_llm)
        gateway_candidate = None
        gateway_result = None
        try:
            payload, gateway_result = await invoke_field_extraction_gateway(
                workflow, semantic_input, response_schema, gateway=gateway
            )
            if payload is not None:
                gateway_candidate = coercer(_validated_gateway_payload_dict(payload, semantic_input))
        except Exception:
            logger.warning(
                "Shadow field extraction gateway failed",
                extra={"extra_data": {"workflow": workflow}},
            )
        record_field_extraction_shadow_comparison(
            workflow, legacy, gateway_candidate, gateway_result, shadow_recorder
        )
        return legacy

    try:
        payload, _ = await invoke_field_extraction_gateway(
            workflow, semantic_input, response_schema, gateway=gateway
        )
        if payload is None:
            return coercer({"clarification_needed": True})
        return coercer(_validated_gateway_payload_dict(payload, semantic_input))
    except Exception:
        logger.warning(
            "Authoritative field extraction gateway failed closed",
            extra={"extra_data": {"workflow": workflow}},
        )
        return coercer({"clarification_needed": True})


async def extract_lead_fields(
    context: ExecutionContext,
    *,
    gateway: Any | None = None,
    legacy_llm: Any | None = None,
    shadow_recorder: Callable[[Dict[str, Any]], None] | None = None,
) -> LeadFieldExtraction:
    from backend.logic.engine.field_extraction_gateway import LeadGatewayPayload

    return await _extract_migrated_fields(
        context,
        workflow="lead_capture",
        module_name="lead_capture",
        response_schema=LeadGatewayPayload,
        coercer=_coerce_lead_extraction,
        legacy_extractor=_extract_lead_fields_legacy,
        gateway=gateway,
        legacy_llm=legacy_llm,
        shadow_recorder=shadow_recorder,
    )


async def extract_support_fields(
    context: ExecutionContext,
    *,
    gateway: Any | None = None,
    legacy_llm: Any | None = None,
    shadow_recorder: Callable[[Dict[str, Any]], None] | None = None,
) -> SupportFieldExtraction:
    from backend.logic.engine.field_extraction_gateway import SupportGatewayPayload

    return await _extract_migrated_fields(
        context,
        workflow="support",
        module_name="support",
        response_schema=SupportGatewayPayload,
        coercer=_coerce_support_extraction,
        legacy_extractor=_extract_support_fields_legacy,
        gateway=gateway,
        legacy_llm=legacy_llm,
        shadow_recorder=shadow_recorder,
    )


async def extract_reminder_fields(
    context: ExecutionContext,
    *,
    gateway: Any | None = None,
    legacy_llm: Any | None = None,
    shadow_recorder: Callable[[Dict[str, Any]], None] | None = None,
) -> ReminderFieldExtraction:
    from backend.logic.engine.field_extraction_gateway import ReminderGatewayPayload

    return await _extract_migrated_fields(
        context,
        workflow="reminder",
        module_name="reminders",
        response_schema=ReminderGatewayPayload,
        coercer=_coerce_reminder_extraction,
        legacy_extractor=_extract_reminder_fields_legacy,
        gateway=gateway,
        legacy_llm=legacy_llm,
        shadow_recorder=shadow_recorder,
    )


async def _extract_review_fields_legacy(
    context: ExecutionContext,
    *,
    legacy_llm: Any | None = None,
) -> ReviewFieldExtraction:
    """Execute the established review LLM path exactly once."""
    system_prompt = (
        "You extract customer meaning for an active review workflow. "
        "Return only one JSON object with keys: detected_language, workflow, workflow_action, "
        "confidence, field_updates, field_confidence, clarification_needed, clarification_fields, "
        "safe_response_hint. workflow must be review or unknown. workflow_action must be one of "
        "continue, tangent, switch, cancel, clarify, change, unknown. Extract only allowed review "
        "fields. rating must be a canonical numeric value in the configured range when confident. "
        "Do not invent internal IDs, UUIDs, tenant IDs, customer IDs, review IDs, order IDs, booking IDs, "
        "backend references, or actions. Use customer language, tenant configuration, current state, and "
        "the latest message. If uncertain, use unknown or clarify with low confidence."
    )
    payload = _review_extraction_payload(context)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ]
    llm = _legacy_llm_or_default(legacy_llm)
    timeout_seconds = float(getattr(settings, "REVIEW_FIELD_EXTRACTION_TIMEOUT_SECONDS", 4) or 4)
    try:
        response = await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout_seconds)
    except Exception:
        logger.warning("Semantic review field extraction failed", extra={"extra_data": {"active_review": True}})
        raise
    record_langchain_usage(context, response)
    extraction = _coerce_review_extraction(_extract_json_object(str(getattr(response, "content", "") or "")))
    logger.info(
        "Semantic review field extraction",
        extra={"extra_data": {
            "workflow": extraction.workflow,
            "workflow_action": extraction.workflow_action,
            "confidence": extraction.confidence,
            "field_count": len(extraction.field_updates),
        }},
    )
    return extraction


def _review_gateway_semantic_input(context: ExecutionContext) -> Dict[str, Any]:
    current = _safe_review_state(context)
    module = _safe_module_config(context, "reviews")
    configured = [
        str(item.get("key"))
        for item in module.get("configured_fields", [])
        if isinstance(item, dict) and item.get("key")
    ][:8]
    focus = _safe_requested_focus(context, "reviews") or {}
    requested = [str(value) for value in focus.get("requested_fields", []) if str(value) in configured][:8]
    missing = requested or [name for name in configured if name not in current][:8]
    language = _safe_language_context(context).get("detected_language")
    validated_rating = current.get("rating") if current.get("rating") in {1, 2, 3, 4, 5} else None
    return {
        "customer_review_turn": _redact_internal_text(
            (context.incoming_message or {}).get("text") or "", max_chars=1200
        ),
        "missing_field_names": missing,
        "collected_field_names": [name for name in configured if name in current][:8],
        "allowed_rating_choices": [1, 2, 3, 4, 5],
        "validated_existing_rating": validated_rating,
        "comment_constraints": {"plain_text": True, "maximum_characters": 2000},
        "language": str(language)[:32] if language else None,
    }


def _deterministic_review_rating(context: ExecutionContext) -> ReviewFieldExtraction | None:
    focus = context.session_data.state.prompt_focus
    if focus is None or focus.workflow != "reviews" or "rating" not in focus.requested_fields:
        return None
    raw = " ".join(str((context.incoming_message or {}).get("text") or "").casefold().split())
    if raw not in {"1", "2", "3", "4", "5"}:
        return None
    rating = int(raw)
    return ReviewFieldExtraction(
        detected_language=(context.incoming_message or {}).get("detected_language"),
        workflow="review",
        workflow_action="continue",
        confidence=1.0,
        field_updates={"rating": rating},
        field_confidence={"rating": 1.0},
        clarification_needed=False,
    )


async def extract_review_fields(
    context: ExecutionContext,
    *,
    gateway: Any | None = None,
    legacy_llm: Any | None = None,
    shadow_recorder: Callable[[Dict[str, Any]], None] | None = None,
) -> ReviewFieldExtraction:
    """Extract review evidence under the platform-owned gateway rollout mode."""
    deterministic = _deterministic_review_rating(context)
    if deterministic is not None:
        return deterministic

    from backend.logic.engine.review_extraction_gateway import (
        FieldExtractionGatewayMode,
        gateway_payload_to_legacy_dict,
        invoke_review_gateway,
        record_review_shadow_comparison,
        resolve_review_gateway_mode,
    )

    mode = resolve_review_gateway_mode(settings)
    if mode is FieldExtractionGatewayMode.LEGACY:
        return await _extract_review_fields_legacy(context, legacy_llm=legacy_llm)

    semantic_input = _review_gateway_semantic_input(context)
    if mode is FieldExtractionGatewayMode.SHADOW:
        legacy = await _extract_review_fields_legacy(context, legacy_llm=legacy_llm)
        gateway_candidate = None
        gateway_result = None
        try:
            payload, gateway_result = await invoke_review_gateway(semantic_input, gateway=gateway)
            if payload is not None:
                gateway_candidate = _coerce_review_extraction(gateway_payload_to_legacy_dict(payload))
        except Exception:
            logger.warning(
                "Shadow review gateway extraction failed",
                extra={"extra_data": {"active_review": True}},
            )
        record_review_shadow_comparison(legacy, gateway_candidate, gateway_result, shadow_recorder)
        return legacy

    try:
        payload, _ = await invoke_review_gateway(semantic_input, gateway=gateway)
        if payload is None:
            return ReviewFieldExtraction()
        return _coerce_review_extraction(gateway_payload_to_legacy_dict(payload))
    except Exception:
        logger.warning(
            "Authoritative review gateway extraction failed closed",
            extra={"extra_data": {"active_review": True}},
        )
        return ReviewFieldExtraction()


def _payment_extraction_payload(context: ExecutionContext) -> Dict[str, Any]:
    """Build the LLM payload for payment field extraction.

    Security rules applied:
    - No tenant/customer/internal IDs in any field.
    - No provider secrets, webhook secrets, or payment credentials.
    - No payment status, provider result fields, or raw records.
    - Only public safe config and safe current workflow state.
    """
    state = context.session_data.state
    ledger = state.action_ledger
    # UUID/internal-text redaction applied before sending to LLM
    customer_message = _redact_internal_text(
        (context.incoming_message or {}).get("text") or "", max_chars=1200
    )
    return {
        "customer_message": customer_message,
        "language_context": _safe_language_context(context),
        "workflow_scope": {
            "current_task": ledger.current_task,
            "current_module": context.session_data.current_module,
            "task_status": ledger.task_status,
            "prompt_focus": _safe_requested_focus(context, "payments"),
            "has_pending_payment_action": bool(
                ledger.pending_action
                and str(ledger.pending_action.tool_key or "").startswith("payments.")
            ),
        },
        "payment_config": _safe_payment_config(context),
        "current_payment_state": _safe_payment_state(context),
        "allowed_field_updates": [
            "amount",
            "currency",
            "payment_purpose",
            "customer_name",
            "phone",
            "email",
            "order_reference",
            "booking_reference",
            "invoice_reference",
            "notes",
        ],
    }


async def _extract_payment_fields_legacy(
    context: ExecutionContext,
    *,
    legacy_llm: Any | None = None,
) -> "PaymentFieldExtraction":
    """
    Semantically interpret the latest customer message for an active payment workflow.

    This only extracts safe candidate fields. Backend validators, payment tool logic,
    provider checks, idempotency, confirmation gates, CapabilityGateway, and UsageGuard
    remain fully deterministic and authoritative.

    The LLM must never mark a payment as paid/successful/refunded/captured/failed.
    The LLM must never create payment links. Provider flow is not touched here.
    Sensitive credentials are detected before LLM invocation and cause early failure.
    """
    # Credential-shape detection: skip LLM entirely if raw text looks like credentials.
    raw_text = (context.incoming_message or {}).get("text") or ""
    if _payment_text_contains_credential_shape(raw_text):
        logger.warning(
            "Payment field extraction skipped: credential-shape content detected",
            extra={"extra_data": {"active_payment": True}},
        )
        raise ValueError("credential-shape content detected in customer message")

    system_prompt = (
        "You extract customer meaning for an active payment workflow. "
        "Return only one JSON object with keys: detected_language, workflow, workflow_action, "
        "confidence, field_updates, field_confidence, clarification_needed, clarification_fields, "
        "safe_response_hint. "
        "workflow must be payment or unknown. workflow_action must be one of: "
        "continue, tangent, switch, cancel, clarify, change, unknown. "
        "Extract only allowed fields listed in allowed_field_updates. "
        "NEVER include in field_updates: payment_status, status, paid, success, captured, failed, "
        "refunded, payment_link_url, provider_payment_id, provider_order_id, transaction_id, "
        "signature, webhook_payload, refund_id, settlement_id, tenant_id, user_id, customer_id, "
        "order_id, booking_id, invoice_id, card_number, cvv, upi_pin, otp, bank_password, "
        "or any internal ID or provider credential. "
        "Do not create payment links or claim a payment is successful, received, verified, or refunded. "
        "Use only tenant-configured currency options when extracting currency. "
        "If amount or currency is ambiguous or missing, set confidence low and request clarification. "
        "Do not invent UUIDs, internal IDs, backend references, or provider credentials. "
        "If uncertain, use workflow unknown or workflow_action clarify with low confidence."
    )
    payload = _payment_extraction_payload(context)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ]
    llm = _legacy_llm_or_default(legacy_llm)
    timeout_seconds = float(getattr(settings, "PAYMENT_FIELD_EXTRACTION_TIMEOUT_SECONDS", 4) or 4)
    try:
        response = await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout_seconds)
    except Exception:
        logger.warning(
            "Semantic payment field extraction failed",
            extra={"extra_data": {"active_payment": True}},
        )
        raise
    record_langchain_usage(context, response)
    extraction = _coerce_payment_extraction(
        _extract_json_object(str(getattr(response, "content", "") or ""))
    )
    logger.info(
        "Semantic payment field extraction",
        extra={"extra_data": {
            "workflow": extraction.workflow,
            "workflow_action": extraction.workflow_action,
            "confidence": extraction.confidence,
            "field_count": len(extraction.field_updates),
        }},
    )
    return extraction


def _deterministic_active_booking_field_extraction(
    context: ExecutionContext,
) -> BookingFieldExtraction | None:
    """Accept an unambiguous single answer for the currently requested booking field.

    This is deliberately narrower than semantic extraction: it applies only while a
    booking is active, only to the first configured prompt-focus field, and never
    guesses a date or time.  It prevents a provider interpretation miss from
    discarding a customer answer that the runtime explicitly requested.
    """
    state = context.session_data.state
    focus = state.prompt_focus
    if (
        state.action_ledger.current_task != "booking"
        or focus is None
        or focus.workflow != "booking"
        or not focus.requested_fields
    ):
        return None
    raw = str((context.incoming_message or {}).get("text") or "").strip()
    if not raw or len(raw) > 200:
        return None
    requested = str(focus.requested_fields[0] or "").strip()
    fields = (
        ((context.config.config_data or {}).get("modules") or {}).get("booking", {}).get("fields", [])
        if isinstance(context.config.config_data, dict) else []
    )
    field = next((item for item in fields if isinstance(item, dict) and item.get("key") == requested), None)
    if not field:
        return None
    field_type = str(field.get("type") or "string").lower()
    options = [str(option) for option in field.get("options") or []]
    updates: dict[str, Any] = {}
    if field_type == "string" and raw:
        updates[requested] = raw
    elif field_type == "number" and re.fullmatch(r"[+-]?\d+(?:\.\d+)?", raw):
        updates[requested] = raw
    elif field_type == "email" and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", raw):
        updates[requested] = raw
    elif field_type == "phone" and re.fullmatch(r"\+?[0-9 ()-]{7,40}", raw):
        updates[requested] = raw
    elif field_type == "select" and raw in options:
        updates[requested] = raw
    elif field_type == "date" and (re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) or raw.casefold() in {"today", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}):
        updates[requested] = raw
    elif field_type == "time" and (
        re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", raw)
        or re.fullmatch(r"\d{1,2}(?::\d{2})?\s*(?:am|pm)", raw, re.I)
        or re.fullmatch(r"(?:morning|afternoon|evening|night|noon)", raw, re.I)
    ):
        updates[requested] = raw
    if not updates:
        return None
    return BookingFieldExtraction(
        workflow="booking",
        workflow_action="continue",
        confidence=1.0,
        field_updates=updates,
        field_confidence={key: 1.0 for key in updates},
        clarification_needed=False,
    )


async def extract_booking_fields(
    context: ExecutionContext,
    *,
    gateway: Any | None = None,
    legacy_llm: Any | None = None,
    shadow_recorder: Callable[[Dict[str, Any]], None] | None = None,
) -> BookingFieldExtraction:
    """Extract booking evidence under the platform-owned gateway rollout mode."""
    deterministic = _deterministic_active_booking_field_extraction(context)
    if deterministic is not None:
        return deterministic
    from backend.logic.engine.field_extraction_gateway import BookingGatewayPayload

    return await _extract_migrated_fields(
        context,
        workflow="booking",
        module_name="booking",
        response_schema=BookingGatewayPayload,
        coercer=_coerce_booking_extraction,
        legacy_extractor=_extract_booking_fields_legacy,
        gateway=gateway,
        legacy_llm=legacy_llm,
        shadow_recorder=shadow_recorder,
    )


async def extract_order_fields(
    context: ExecutionContext,
    *,
    gateway: Any | None = None,
    legacy_llm: Any | None = None,
    shadow_recorder: Callable[[Dict[str, Any]], None] | None = None,
) -> OrderFieldExtraction:
    """Extract order evidence under the platform-owned gateway rollout mode."""
    from backend.logic.engine.field_extraction_gateway import OrderGatewayPayload

    return await _extract_migrated_fields(
        context,
        workflow="order",
        module_name="order",
        response_schema=OrderGatewayPayload,
        coercer=_coerce_extraction,
        legacy_extractor=_extract_order_fields_legacy,
        gateway=gateway,
        legacy_llm=legacy_llm,
        shadow_recorder=shadow_recorder,
    )


async def extract_payment_fields(
    context: ExecutionContext,
    *,
    gateway: Any | None = None,
    legacy_llm: Any | None = None,
    shadow_recorder: Callable[[Dict[str, Any]], None] | None = None,
) -> PaymentFieldExtraction:
    """Extract non-sensitive payment evidence under the platform-owned rollout mode."""
    raw_text = str((context.incoming_message or {}).get("text") or "")
    if _payment_text_contains_credential_shape(raw_text):
        logger.warning(
            "Payment field extraction skipped: credential-shape content detected",
            extra={"extra_data": {"active_payment": True}},
        )
        raise ValueError("credential-shape content detected in customer message")

    from backend.logic.engine.field_extraction_gateway import PaymentGatewayPayload

    return await _extract_migrated_fields(
        context,
        workflow="payment",
        module_name="payments",
        response_schema=PaymentGatewayPayload,
        coercer=_coerce_payment_extraction,
        legacy_extractor=_extract_payment_fields_legacy,
        gateway=gateway,
        legacy_llm=legacy_llm,
        shadow_recorder=shadow_recorder,
    )


async def extract_global_workflow_turn(
    semantic_input: dict[str, Any],
) -> Any:
    """Dynamic, tenant-schema-aware, complete-turn semantic contract (Iteration 2).

    This function is NON-AUTHORITATIVE in this iteration. It inspects the complete
    customer utterance and proposes candidates for every relevant configured field.

    It must never:
      * mutate the supplied context, state, or the input structures;
      * persist field values;
      * execute tools;
      * change routing or customer-visible responses;
      * activate the global configured-field collector.

    On gateway failure, parse failure, or invalid output, it returns a safe typed
    result equivalent to: no accepted candidates, gateway unavailable or invalid,
    no mutation.
    """
    from backend.ai.gateway.runtime import get_runtime_llm_gateway
    from backend.logic.engine.field_extraction_gateway import (
        build_dynamic_turn_gateway_request,
        DynamicTurnContractPayload,
        DynamicTurnValidationResult,
        validate_dynamic_turn_candidates,
    )

    # Deep-copy all input structures; the contract must never mutate its caller.
    semantic_input = _deep_copy_semantic_turn(semantic_input)

    configured_fields = semantic_input.get("configured_fields") or []
    configured_keys: set[str] = {
        str(item.get("field_key") or "").strip()
        for item in configured_fields
        if isinstance(item, dict) and str(item.get("field_key") or "").strip()
    }

    if not configured_keys:
        return DynamicTurnValidationResult(
            rejected=True,
            rejection_reason="no configured fields supplied",
            gateway_unavailable=False,
        )

    workflow = str(semantic_input.get("workflow") or "global")

    gateway = get_runtime_llm_gateway()
    request = build_dynamic_turn_gateway_request(
        workflow, semantic_input, DynamicTurnContractPayload
    )

    try:
        result = await gateway.invoke(request)
    except Exception:
        return DynamicTurnValidationResult(
            rejected=True,
            rejection_reason="gateway unavailable or invalid",
            gateway_unavailable=True,
        )

    if result is None or result.error is not None or result.content is None:
        return DynamicTurnValidationResult(
            rejected=True,
            rejection_reason="gateway unavailable or invalid",
            gateway_unavailable=True,
        )

    if result.parse_status is None or result.parse_status.value != "success":
        return DynamicTurnValidationResult(
            rejected=True,
            rejection_reason="gateway unavailable or invalid",
            gateway_unavailable=True,
        )

    try:
        parsed = DynamicTurnContractPayload.model_validate(result.content)
    except Exception:
        return DynamicTurnValidationResult(
            rejected=True,
            rejection_reason="invalid gateway payload/result",
            gateway_unavailable=False,
        )

    return validate_dynamic_turn_candidates(parsed, configured_keys)


def _deep_copy_semantic_turn(semantic_input: dict[str, Any]) -> dict[str, Any]:
    """Best-effort deep copy of the supplied semantic input to protect caller state."""
    try:
        import copy

        copied = copy.deepcopy(dict(semantic_input))
        return copied
    except Exception:
        return dict(semantic_input)
