from dataclasses import dataclass, field, replace
from typing import Optional, List, Any, Mapping, TypedDict, Annotated
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import operator
import json
import hashlib
import hmac
import re
from time import perf_counter
from langgraph.graph import StateGraph, END
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage, messages_from_dict
from backend.core.context import ExecutionContext, OtpVerificationEvidence, protected_action_binding
from backend.core.customer_assurance import has_required_assurance
from backend.core.runtime_capabilities import (
    build_runtime_capability_snapshot,
    normalize_runtime_module_key,
)
from backend.core.runtime_behavior_profile import resolve_runtime_behavior_profile
from backend.core.config import settings
from backend.core.states import ConversationState
from backend.ops.logger import logger
from backend.observability.llm_logging import (
    current_safe_trace_id,
    emit_safe_llm_log,
    safe_identifier,
    safe_llm_error_code,
)
from backend.tools.registry import ToolRegistry
from backend.db.client import db_client
from backend.domains.reviews.service import review_service
from backend.billing.usage_guard import usage_guard
from backend.ai.security import SECURITY_REFUSAL_MESSAGE, is_prompt_security_request
from backend.logic.engine.history import build_memory_context, serialize_agentic_history, sanitize_agentic_history_dicts
from backend.logic.engine.prompt import build_system_instructions
from backend.logic.engine.capability_menu import (
    build_capability_menu,
    build_catalog_choices_response,
    build_direct_action_response,
    build_hours_response,
    build_matched_business_context,
    build_service_choices_response,
    is_explicit_capability_request,
    should_send_discovery_menu,
)
from backend.logic.engine.nba import append_next_best_actions, ensure_booking_reminder_offer
from backend.logic.engine.response_localization import (
    localize_customer_response,
    note_detected_customer_language,
    target_language_for_context,
)
from backend.logic.engine.response_composer import (
    ResponseCompositionInput,
    ResponseKind,
    compose_customer_response,
)
from backend.logic.engine.response_style import (
    grouped_clarification_text,
    response_style_from_resolution,
    style_receptionist_response,
)
from backend.logic.engine.field_extraction import (
    BookingFieldExtraction,
    LeadFieldExtraction,
    OrderFieldExtraction,
    PaymentFieldExtraction,
    ReminderFieldExtraction,
    ReviewFieldExtraction,
    SupportFieldExtraction,
    BOOKING_FIELD_ALIASES,
    canonical_booking_field,
    extract_booking_fields,
    extract_lead_fields,
    extract_order_fields,
    extract_payment_fields,
    extract_reminder_fields,
    extract_review_fields,
    extract_support_fields,
)
from backend.logic.engine.configured_field_collection import (
    configured_field_collection_coordinator,
)
from backend.logic.engine.global_configured_turn_shadow import (
    SHADOW_EVIDENCE_KEY,
    _emit_booking_applied_event,
    _emit_failure_event,
    capture_global_configured_turn_shadow,
    observe_global_configured_turn_shadow,
)
from backend.logic.engine.order_configured_turn_shadow import (
    SHADOW_EVIDENCE_KEY as ORDER_SHADOW_EVIDENCE_KEY,
    _emit_failure_event as _emit_order_apply_failure_event,
    _emit_order_applied_event,
    capture_order_configured_turn_shadow,
    observe_order_configured_turn_shadow,
)
from backend.logic.engine.lead_configured_turn_shadow import (
    SHADOW_EVIDENCE_KEY as LEAD_SHADOW_EVIDENCE_KEY,
    _build_candidate_map_from_shadow,
    _emit_failure_event as _emit_lead_apply_failure_event,
    _emit_lead_applied_event,
    _focus_field_key as _lead_focus_field_key,
    capture_lead_configured_turn_shadow,
    observe_lead_configured_turn_shadow,
)
from backend.modules.lead_capture_module import lead_capture_module as lead_capture_module
from backend.modules.order_module import order_module as order_module
from backend.logic.engine.pending_decision import PendingActionDecision, decide_pending_action
from backend.logic.engine.canonical_control import (
    CanonicalControlAction,
    decide_canonical_control,
    validate_canonical_state,
)
from backend.logic.engine.configured_option_resolver import resolve_configured_option
from backend.logic.engine.turn_interpretation import (
    InterpretationSource,
    InterpretationReason,
    TurnKind,
    TurnInterpretationMode,
    TurnPlanner,
    has_meaningful_field_extraction,
    resolve_turn_interpretation_mode,
    turn_interpretation_coordinator,
)
from backend.logic.engine.turn_interpretation_gateway import (
    _SEMANTIC_CONFIDENCE_THRESHOLD,
    build_turn_interpretation_gateway_evidence,
    invoke_turn_interpretation_gateway,
    validate_turn_interpretation_gateway_payload,
)
from backend.pipeline.message_router import RoutingDecision, message_router
from backend.pipeline.channel_output import (
    SAFE_RESPONSE_FALLBACK,
    looks_like_response_envelope,
    parse_response_envelope,
    sanitize_customer_visible_output,
)
from backend.pipeline.interactive_parser import quick_reply_labels_from_text, strip_interactive_hints
from uuid import uuid4
from backend.core.redis_client import (
    get_redis_client,
    reset_redis_clients,
    claim_protected_action_execution,
)
from backend.tools.registry import ToolExecutionResult
from backend.logic.engine.llm import (
    build_llm_input_trace,
    final_output_type,
    log_llm_input_trace,
    record_langchain_usage,
)
from backend.logic.engine.core_agent_gateway import (
    CoreAgentGatewayMode,
    invoke_core_agent_model,
    resolve_core_agent_gateway_mode,
)
try:
    from backend.ai.tools import set_tool_context
except ModuleNotFoundError:
    def set_tool_context(context: ExecutionContext):
        return None
MAX_AGENT_RECURSION_LIMIT = 8
MAX_TOOL_CALLS_PER_TURN = 4
PENDING_DECISION_CONFIDENCE_THRESHOLD = 0.65
ORDER_FIELD_EXTRACTION_CONFIDENCE_THRESHOLD = 0.65
ORDER_FIELD_CONFIDENCE_THRESHOLD = 0.7
BOOKING_FIELD_EXTRACTION_CONFIDENCE_THRESHOLD = 0.65
BOOKING_FIELD_CONFIDENCE_THRESHOLD = 0.7
LEAD_FIELD_EXTRACTION_CONFIDENCE_THRESHOLD = 0.65
LEAD_FIELD_CONFIDENCE_THRESHOLD = 0.7
SUPPORT_FIELD_EXTRACTION_CONFIDENCE_THRESHOLD = 0.65
SUPPORT_FIELD_CONFIDENCE_THRESHOLD = 0.7
REMINDER_FIELD_EXTRACTION_CONFIDENCE_THRESHOLD = 0.65
REMINDER_FIELD_CONFIDENCE_THRESHOLD = 0.7
REVIEW_FIELD_EXTRACTION_CONFIDENCE_THRESHOLD = 0.65
REVIEW_FIELD_CONFIDENCE_THRESHOLD = 0.7
# Payment uses higher thresholds than other workflows (payment is sensitive)
PAYMENT_FIELD_EXTRACTION_CONFIDENCE_THRESHOLD = 0.70
PAYMENT_FIELD_CONFIDENCE_THRESHOLD = 0.75
TRANSACTIONAL_INTENTS = {"booking", "order", "payments", "payment", "reminders", "reminder"}
RAG_EMPTY_CONTEXT = "No retrieved knowledge context loaded for this route."
SAFE_RAG_SKIP_REASONS = {
    "greeting",
    "thanks_or_goodbye",
    "deterministic_direct",
    "transactional_field_collection",
    "protected_pending_action",
    "post_tool_success",
}


def _turn_interpretation_mode() -> TurnInterpretationMode:
    return resolve_turn_interpretation_mode(
        settings.TURN_INTERPRETATION_MODE,
        legacy_shadow_enabled=bool(settings.ENABLE_TURN_INTERPRETATION_SHADOW),
    )


def _should_invoke_turn_interpretation_gateway(
    *,
    mode: TurnInterpretationMode,
    gateway_enabled: bool,
    customer_text: str,
    customer_authored: bool,
    orientation_message: bool,
    trusted_internal_event: bool,
    trusted_otp_continuation: bool,
    pending_action_exists: bool,
    exact_configured_option_resolved: bool,
    meaningful_field_extraction_exists: bool,
    exact_safe_control_resolved: bool,
) -> bool:
    """Return whether one customer turn may request semantic gateway evidence."""

    if mode == TurnInterpretationMode.LEGACY or not gateway_enabled:
        return False

    if not customer_authored or not str(customer_text or "").strip():
        return False

    if (
        orientation_message
        or trusted_internal_event
        or trusted_otp_continuation
    ):
        return False

    if (
        pending_action_exists
        or exact_configured_option_resolved
        or meaningful_field_extraction_exists
        or exact_safe_control_resolved
    ):
        return False

    return True


def _build_turn_interpretation_semantic_input(
    *,
    customer_text: str,
    channel_category: str,
    active_workflow: Optional[str],
    requested_fields: list[str],
    workflow_status: Optional[str],
    pending_action_exists: bool,
    runtime_available_modules: set[str],
    configured_public_labels: list[str],
    routing_decision: Any,
) -> dict[str, Any]:
    """Build caller-owned bounded-context facts for semantic interpretation."""

    normalized_active_workflow = (
        str(active_workflow).strip()
        if active_workflow not in (None, "")
        else None
    )
    normalized_requested_fields = list(
        dict.fromkeys(
            str(value).strip()
            for value in requested_fields
            if str(value).strip()
        )
    )
    normalized_available_workflows = sorted({
        str(value).strip()
        for value in runtime_available_modules
        if str(value).strip()
    })
    normalized_public_labels = list(
        dict.fromkeys(
            str(value).strip()
            for value in configured_public_labels
            if str(value).strip()
        )
    )

    route_category_value = getattr(
        routing_decision,
        "intent",
        None,
    )
    deterministic_route_category = (
        str(route_category_value).strip()
        if route_category_value not in (None, "")
        else None
    )

    route_confidence_value = getattr(
        routing_decision,
        "confidence",
        None,
    )
    deterministic_route_confidence = (
        float(route_confidence_value)
        if (
            isinstance(route_confidence_value, (int, float))
            and not isinstance(route_confidence_value, bool)
        )
        else None
    )

    return {
        "customer_text": str(customer_text or ""),
        "channel_category": str(
            channel_category or "unknown"
        ),
        "active_workflow": normalized_active_workflow,
        "requested_fields": normalized_requested_fields,
        "workflow_status": (
            str(workflow_status).strip()
            if workflow_status not in (None, "")
            else None
        ),
        "pending_action_exists": bool(
            pending_action_exists
        ),
        "available_workflows": (
            normalized_available_workflows
        ),
        "configured_public_labels": (
            normalized_public_labels
        ),
        "deterministic_route_category": (
            deterministic_route_category
        ),
        "deterministic_route_confidence": (
            deterministic_route_confidence
        ),
    }


def _collect_turn_interpretation_public_labels(
    *,
    config_data: dict[str, Any],
    runtime_available_modules: set[str],
    active_workflow: Optional[str],
) -> list[str]:
    """Collect safe configured labels with canonical workflow provenance."""

    config = (
        config_data
        if isinstance(config_data, dict)
        else {}
    )
    modules_value = config.get("modules")
    modules = (
        modules_value
        if isinstance(modules_value, dict)
        else {}
    )
    available = {
        str(value).strip().casefold()
        for value in runtime_available_modules
        if str(value).strip()
    }
    active = (
        str(active_workflow).strip().casefold()
        if active_workflow not in (None, "")
        else None
    )

    groups: dict[str, list[str]] = {}

    booking_config = modules.get("booking")
    if (
        "booking" in available
        and isinstance(booking_config, dict)
        and bool(booking_config.get("enabled", False))
    ):
        booking_labels: list[str] = []
        services = config.get("services")
        if isinstance(services, list):
            for service in services:
                if (
                    not isinstance(service, dict)
                    or service.get("enabled", True) is False
                ):
                    continue
                name = service.get("name")
                if (
                    isinstance(name, (str, int, float, bool))
                    and str(name).strip()
                ):
                    booking_labels.append(
                        f"booking service: {str(name).strip()}"
                    )
        if booking_labels:
            groups["booking"] = booking_labels

    order_config = modules.get("order")
    if (
        "order" in available
        and isinstance(order_config, dict)
        and bool(order_config.get("enabled", False))
    ):
        order_labels: list[str] = []
        categories = order_config.get("categories")
        if isinstance(categories, list):
            for category in categories:
                if (
                    not isinstance(category, dict)
                    or category.get("enabled", True) is False
                ):
                    continue
                items = category.get("items")
                if not isinstance(items, list):
                    continue
                for item in items:
                    if (
                        not isinstance(item, dict)
                        or item.get("enabled", True) is False
                    ):
                        continue
                    name = item.get("name")
                    if (
                        isinstance(name, (str, int, float, bool))
                        and str(name).strip()
                    ):
                        order_labels.append(
                            f"order item: {str(name).strip()}"
                        )
                    sku = item.get("sku")
                    if (
                        isinstance(sku, (str, int, float, bool))
                        and str(sku).strip()
                    ):
                        order_labels.append(
                            f"order sku: {str(sku).strip()}"
                        )

        fulfillment_types = order_config.get(
            "fulfillment_types"
        )
        if isinstance(fulfillment_types, list):
            for option in fulfillment_types:
                if (
                    isinstance(option, (str, int, float, bool))
                    and str(option).strip()
                ):
                    order_labels.append(
                        "order fulfillment: "
                        f"{str(option).strip()}"
                    )

        payment_methods = order_config.get(
            "payment_methods"
        )
        if isinstance(payment_methods, list):
            for option in payment_methods:
                if (
                    isinstance(option, (str, int, float, bool))
                    and str(option).strip()
                ):
                    order_labels.append(
                        "order payment method: "
                        f"{str(option).strip()}"
                    )

        if order_labels:
            groups["order"] = order_labels

    for workflow in ("support", "lead_capture"):
        workflow_config = modules.get(workflow)
        if (
            workflow not in available
            or not isinstance(workflow_config, dict)
            or not bool(
                workflow_config.get("enabled", False)
            )
        ):
            continue

        workflow_labels: list[str] = []
        fields = workflow_config.get("fields")
        if isinstance(fields, list):
            for field in fields:
                if (
                    not isinstance(field, dict)
                    or field.get("enabled", True) is False
                ):
                    continue
                field_key_value = (
                    field.get("key")
                    or field.get("name")
                    or field.get("label")
                    or "option"
                )
                field_key = str(
                    field_key_value
                ).strip().replace("_", " ")
                options = field.get("options")
                if not isinstance(options, list):
                    continue
                for option in options:
                    if (
                        isinstance(
                            option,
                            (str, int, float, bool),
                        )
                        and str(option).strip()
                    ):
                        workflow_labels.append(
                            f"{workflow.replace('_', ' ')} "
                            f"{field_key}: "
                            f"{str(option).strip()}"
                        )

        if workflow_labels:
            groups[workflow] = workflow_labels

    group_order: list[str] = []
    if active in groups:
        group_order.append(active)

    for workflow in (
        "booking",
        "order",
        "support",
        "lead_capture",
    ):
        if (
            workflow in groups
            and workflow not in group_order
        ):
            group_order.append(workflow)

    for workflow in sorted(groups):
        if workflow not in group_order:
            group_order.append(workflow)

    positions = {
        workflow: 0
        for workflow in group_order
    }
    labels: list[str] = []
    seen: set[str] = set()

    while True:
        emitted = False

        for workflow in group_order:
            values = groups[workflow]
            position = positions[workflow]

            while position < len(values):
                candidate = values[position]
                position += 1
                key = candidate.casefold()

                if key in seen:
                    continue

                seen.add(key)
                labels.append(candidate)
                emitted = True
                break

            positions[workflow] = position

        if not emitted:
            break

    return labels


def _build_turn_interpretation_gateway_eligibility_facts(
    *,
    mode: TurnInterpretationMode,
    gateway_enabled: bool,
    customer_text: str,
    customer_authored: bool,
    orientation_message: bool,
    trusted_internal_event: bool,
    trusted_otp_continuation: bool,
    pending_action_exists: bool,
    exact_configured_option_resolved: bool,
    field_extraction: Any,
    exact_safe_control_resolved: bool,
) -> dict[str, Any]:
    """Build explicit caller-owned facts for semantic gateway eligibility."""

    return {
        "mode": mode,
        "gateway_enabled": bool(gateway_enabled),
        "customer_text": str(customer_text or ""),
        "customer_authored": bool(customer_authored),
        "orientation_message": bool(
            orientation_message
        ),
        "trusted_internal_event": bool(
            trusted_internal_event
        ),
        "trusted_otp_continuation": bool(
            trusted_otp_continuation
        ),
        "pending_action_exists": bool(
            pending_action_exists
        ),
        "exact_configured_option_resolved": bool(
            exact_configured_option_resolved
        ),
        "meaningful_field_extraction_exists": (
            has_meaningful_field_extraction(
                field_extraction
            )
        ),
        "exact_safe_control_resolved": bool(
            exact_safe_control_resolved
        ),
    }


def _has_exact_safe_turn_control(
    frame: Any,
) -> bool:
    """Return whether validated deterministic evidence already owns the turn."""

    if frame is None:
        return False

    reason_values = {
        str(getattr(reason, "value", reason))
        for reason in (
            getattr(frame, "reason_codes", ()) or ()
        )
    }

    return bool(
        {
            InterpretationReason.EXPLICIT_CAPABILITY_REQUEST.value,
            InterpretationReason.EXPLICIT_CANCEL.value,
            InterpretationReason.EXPLICIT_CONTINUE.value,
        }
        & reason_values
    )


def _shadow_interpretation_enabled() -> bool:
    return _turn_interpretation_mode() == TurnInterpretationMode.SHADOW


def _canonical_interpretation_enabled() -> bool:
    return _turn_interpretation_mode() != TurnInterpretationMode.LEGACY


def _record_shadow_evidence(context: ExecutionContext, key: str, value: Any) -> None:
    if _canonical_interpretation_enabled():
        context.shadow_turn_evidence[key] = value
    if _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE:
        incoming = context.incoming_message or {}
        snapshot = incoming.get("_turn_interpretation_snapshot")
        runtime_modules = incoming.get("_runtime_available_modules") or ()
        if snapshot is not None:
            # TurnInterpretation is deliberately scalar-only.  Keep the full
            # cart for execution, but present its legacy scalar projection to
            # the coordinator so a rich order turn cannot fail before the
            # pending action is prepared.
            interpretation_evidence = context.shadow_turn_evidence
            if isinstance(value, OrderFieldExtraction) and isinstance(value.field_updates.get("items"), list):
                projected_updates = dict(value.field_updates)
                projected_updates.pop("items", None)
                first_item = value.field_updates["items"][0] if value.field_updates["items"] else {}
                if isinstance(first_item, dict):
                    projected_updates.setdefault("item_name", first_item.get("item_name") or first_item.get("item_sku"))
                    projected_updates.setdefault("quantity", first_item.get("quantity"))
                projected_confidence = dict(value.field_confidence)
                projected_confidence.pop("items", None)
                interpretation_evidence = dict(context.shadow_turn_evidence)
                interpretation_evidence[key] = replace(
                    value,
                    field_updates={name: item for name, item in projected_updates.items() if item not in (None, "")},
                    field_confidence=projected_confidence,
                )
            frame = turn_interpretation_coordinator.interpret_turn(
                snapshot,
                evidence=interpretation_evidence,
                runtime_available_modules=runtime_modules,
            )
            context.turn_interpretation = frame
            context.turn_plan = TurnPlanner().plan(frame, runtime_modules)


def _configured_option_extraction(
    context: ExecutionContext,
    runtime_available_modules: set[str] | frozenset[str],
) -> Any | None:
    """Build existing extraction evidence for one exact, prompt-scoped configured option."""
    state = context.session_data.state
    active_workflow = state.action_ledger.current_task or context.session_data.current_module
    prompt_focus = state.prompt_focus
    if not active_workflow or not prompt_focus or prompt_focus.workflow != active_workflow:
        return None
    match = resolve_configured_option(
        customer_text=str((context.incoming_message or {}).get("text") or ""),
        active_workflow=active_workflow,
        requested_fields=tuple(prompt_focus.requested_fields),
        config_data=context.config.config_data,
        runtime_available_modules=runtime_available_modules,
    )
    if match is None:
        return None
    extraction_types = {
        "booking": (BookingFieldExtraction, "booking"),
        "order": (OrderFieldExtraction, "order"),
        "support": (SupportFieldExtraction, "support"),
        "reviews": (ReviewFieldExtraction, "review"),
    }
    extraction_entry = extraction_types.get(match.workflow)
    if extraction_entry is None:
        return None
    extraction_type, extraction_workflow = extraction_entry
    detected_language = (context.incoming_message or {}).get("detected_language")
    updates = dict(match.field_updates)
    if match.workflow == "booking":
        # Booking's established extraction/validation schema owns the public
        # service field; the resolver may additionally expose a stable config
        # identifier without widening that schema here.
        updates.pop("service_id", None)
    return extraction_type(
        detected_language=detected_language,
        workflow=extraction_workflow,
        workflow_action="continue",
        confidence=1.0,
        field_updates=updates,
        field_confidence={field_name: 1.0 for field_name in updates},
        clarification_needed=False,
        clarification_fields=[],
    )


TOOL_SUCCESS_CLAIM_PATTERNS = [
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in [
        r"\b(?:booking|appointment|slot|reservation)\b.{0,80}\b(?:confirmed|booked|scheduled|reserved|completed|successful)\b",
        r"\b(?:confirmed|booked|scheduled|reserved)\b.{0,80}\b(?:booking|appointment|slot|reservation)\b",
        r"\byou(?:'re| are)\s+booked\b",
        r"\b(?:order|purchase)\b.{0,80}\b(?:placed|confirmed|created|completed|successful)\b",
        r"\b(?:placed|confirmed|created|completed)\b.{0,80}\b(?:order|purchase)\b",
        r"\b(?:payment|payment link|link)\b.{0,80}\b(?:created|generated|sent|paid|completed|confirmed|successful)\b",
        r"\b(?:created|generated|sent)\b.{0,80}\b(?:payment link)\b",
        r"\b(?:reminder|message)\b.{0,80}\b(?:scheduled|created|sent|completed|successful)\b",
        r"\b(?:review|feedback)\b.{0,80}\b(?:submitted|recorded|saved|completed|successful)\b",
        r"\b(?:handoff|support ticket|ticket|team|person|agent)\b.{0,80}\b(?:created|opened|notified|alerted|connected|escalated)\b",
        r"\b(?:all set|done successfully|successfully completed|completed successfully)\b",
    ]
]
SUCCESS_NEGATION_PATTERN = re.compile(
        r"\b(?:not|never|cannot|can't|couldn't|could not|didn't|did not|unable|failed|failure|"
    r"wasn't|was not|isn't|is not|nothing|needs|requires|before|please confirm)\b",
    re.IGNORECASE,
)
@dataclass
class WorkflowResult:
    """Result of a workflow step execution."""
    response_text: str
    quick_replies: List[str] = field(default_factory=list)
    next_state: Optional[ConversationState] = None
    requires_response: bool = True
    prompt_focus_workflow: Optional[str] = None
    prompt_focus_fields: List[str] = field(default_factory=list)
    prompt_focus_kind: Optional[str] = None
    composer_eligible: bool = True

    def __post_init__(self):
        envelope = parse_response_envelope(self.response_text)
        if envelope:
            self.response_text = envelope["response_text"]
            if not self.quick_replies:
                self.quick_replies = list(envelope["quick_replies"])
        elif looks_like_response_envelope(self.response_text):
            self.response_text = SAFE_RESPONSE_FALLBACK
            self.quick_replies = []
class AgentState(TypedDict):
    messages: Annotated[List[Any], operator.add]
    context: ExecutionContext
    tool_call_count: int
    tool_call_signatures: List[str]


def _response_style_for_context(context: ExecutionContext):
    """Resolve bounded presentation policy without exposing it to execution code."""
    return response_style_from_resolution(getattr(context, "runtime_behavior_profile", None))


def _response_kind_for_context(context: ExecutionContext) -> str:
    ledger = context.session_data.state.action_ledger
    if ledger.pending_action:
        return "confirmation"
    if ledger.last_tool_result and ledger.last_tool_result.success is False:
        return "failure"
    if ledger.last_tool_result and ledger.last_tool_result.success is True:
        return "tool_success"
    return "clarification"


_COMPOSER_SECURITY_EXCLUSION = re.compile(
    r"\b(?:otp|one[- ]time password|credential|card number|cvv|upi pin|authentication|captcha|"
    r"security refusal|api key|provider secret|webhook secret|verification failed)\b",
    re.IGNORECASE,
)
_COMPOSER_NUMERIC_TOKEN = re.compile(
    r"(?<!\w)(?:[₹$€£]\s*)?\d[\d,]*(?:\.\d+)?(?:\s*(?:AM|PM|USD|INR|EUR|GBP))?(?!\w)",
    re.IGNORECASE,
)
_PUBLIC_WORKFLOW_NAMES = {
    "booking": "booking",
    "order": "order",
    "lead_capture": "contact request",
    "support": "support request",
    "reminders": "reminder",
    "reviews": "review",
    "payments": "payment request",
    "payment": "payment request",
    "human_handoff": "team handoff",
}
_PUBLIC_FIELD_LABELS = {
    "service_name": "service",
    "service": "service",
    "booking_date": "date",
    "preferred_date": "date",
    "date": "date",
    "booking_time": "time",
    "preferred_time": "time",
    "reminder_time": "time",
    "scheduled_for": "time",
    "schedule_at": "time",
    "quantity": "quantity",
    "item_name": "item",
    "item_sku": "item",
    "phone": "phone number",
    "email": "email address",
    "customer_name": "name",
    "name": "name",
    "rating": "rating",
    "feedback": "feedback",
    "feedback_text": "feedback",
    "amount": "amount",
    "currency": "currency",
    "urgency": "urgency",
    "fulfillment_type": "fulfillment option",
    "delivery_address": "delivery address",
    "channel": "delivery channel",
}
_TOOL_SAFE_DATA_FIELDS = {
    "booking": {"service_name", "booking_date", "booking_time", "status", "new_date", "new_time", "action"},
    "order": {"item_count", "subtotal_amount", "total_amount", "currency", "fulfillment_type", "payment_status"},
    "payments": {"amount", "currency", "status", "payment_url", "reference"},
    "reminders": {"scheduled_for", "channel", "status"},
    "reviews": {"rating", "status", "public_review_url"},
    "lead_capture": {"interest", "status"},
    "support": {"status"},
    "human_handoff": {"status"},
}


def _backend_response_composition_kind(
    context: ExecutionContext,
    result: WorkflowResult,
    *,
    previous_tool_result: Any,
    pending_before_turn: bool,
    was_active_before_turn: bool,
) -> ResponseKind | None:
    """Fail-closed provenance classification for source-proven backend responses."""
    text = str(result.response_text or "").strip()
    if not result.composer_eligible or not text or text == SECURITY_REFUSAL_MESSAGE:
        return None
    if _COMPOSER_SECURITY_EXCLUSION.search(text):
        return None
    ledger = context.session_data.state.action_ledger
    last_result = ledger.last_tool_result
    if last_result is not None and last_result is not previous_tool_result:
        source_text = str(last_result.user_message or "").strip()
        if source_text and (text == source_text or text.startswith(source_text)):
            return ResponseKind.TOOL_SUCCESS if last_result.success is True else ResponseKind.TOOL_FAILURE
        return None
    if result.prompt_focus_workflow and result.prompt_focus_fields:
        if all(field in _PUBLIC_FIELD_LABELS for field in result.prompt_focus_fields):
            return ResponseKind.MISSING_FIELD
        return None
    if pending_before_turn and ledger.pending_action is None and re.search(r"\bcancel", text, re.IGNORECASE):
        return ResponseKind.PENDING_CANCEL
    if ledger.pending_action is not None:
        if re.search(r"\b(?:updated|what would you like to change)\b", text, re.IGNORECASE):
            return ResponseKind.PENDING_CHANGE
        if re.search(r"\bconfirm\b", text, re.IGNORECASE):
            return ResponseKind.PENDING_CONFIRMATION
        return None
    if was_active_before_turn and re.search(r"\bcancelled this (?:request|order request|booking request|payment request|support request|reminder request|review request)\b", text, re.IGNORECASE):
        return ResponseKind.PENDING_CANCEL
    if text in {
        "Would you like to continue this request, change something, or cancel it?",
        "What would you like to change in this request?",
    }:
        return ResponseKind.CLARIFICATION
    if was_active_before_turn and "I kept your" in text and re.search(r"\b(?:continue|share|choose|what|which|please)\b", text, re.IGNORECASE):
        return ResponseKind.WORKFLOW_RESUME
    return None


def _safe_composer_scalar(value: Any, *, maximum: int = 240) -> Any | None:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return None
    safe = sanitize_customer_visible_output(value[:maximum]).strip()
    if not safe or safe == SAFE_RESPONSE_FALLBACK or re.search(r"\b(?:otp|secret|credential|provider_id|tenant_id)\b", safe, re.IGNORECASE):
        return None
    if re.fullmatch(r"[0-9a-fA-F-]{36}", safe):
        return None
    return safe


def _response_composition_input(
    context: ExecutionContext,
    result: WorkflowResult,
    response_kind: ResponseKind,
) -> ResponseCompositionInput:
    ledger = context.session_data.state.action_ledger
    workflow_key = ledger.current_task or context.session_data.current_module or "general"
    safe_facts: dict[str, Any] = {"workflow": _PUBLIC_WORKFLOW_NAMES.get(workflow_key, "request")}
    required_terms: list[str] = []
    protected_terms: list[str] = []

    if response_kind is ResponseKind.MISSING_FIELD:
        labels = [
            _PUBLIC_FIELD_LABELS[field]
            for field in result.prompt_focus_fields
            if field in _PUBLIC_FIELD_LABELS
        ][:5]
        if labels:
            safe_facts["missing_field_labels"] = labels
            required_terms.extend(labels)
        if result.quick_replies:
            safe_facts["allowed_public_choices"] = list(result.quick_replies[:5])
    elif response_kind in {ResponseKind.PENDING_CONFIRMATION, ResponseKind.PENDING_CHANGE}:
        pending = ledger.pending_action
        if pending and pending.tool_key:
            category = pending.tool_key.split(".", 1)[0]
            safe_facts["action_category"] = _PUBLIC_WORKFLOW_NAMES.get(category, "request")
            changeable = [
                _PUBLIC_FIELD_LABELS[key]
                for key in (pending.args or {})
                if key in _PUBLIC_FIELD_LABELS
            ][:8]
            if changeable:
                safe_facts["changeable_fields"] = changeable
        summary = _customer_safe_pending_summary(context)
        if summary:
            safe_facts["confirmation_summary"] = summary[:600]
        required_terms.append("confirm" if response_kind is ResponseKind.PENDING_CONFIRMATION else "change")
    elif response_kind is ResponseKind.PENDING_CANCEL:
        safe_facts["cancellation_scope"] = _PUBLIC_WORKFLOW_NAMES.get(workflow_key, "request")
        required_terms.append("cancel")
    elif response_kind in {ResponseKind.CLARIFICATION, ResponseKind.WORKFLOW_RESUME}:
        lowered = result.response_text.lower()
        required_terms.extend(
            term for term in ("continue", "change", "cancel", "share", "choose") if term in lowered
        )
    elif response_kind in {ResponseKind.TOOL_SUCCESS, ResponseKind.TOOL_FAILURE}:
        last_result = ledger.last_tool_result
        if last_result is not None:
            category = str(last_result.tool_key or "").split(".", 1)[0]
            safe_facts["action_category"] = _PUBLIC_WORKFLOW_NAMES.get(category, "request")
            if response_kind is ResponseKind.TOOL_FAILURE:
                safe_facts["failure_message"] = _safe_tool_failure_message(context)[:600]
                safe_facts["recovery_action"] = "use the backend-provided retry, change, cancel, or support options"
            data = last_result.data if isinstance(last_result.data, dict) else {}
            for key in _TOOL_SAFE_DATA_FIELDS.get(category, set()):
                if key not in data:
                    continue
                safe_value = _safe_composer_scalar(data[key])
                if safe_value not in (None, ""):
                    safe_facts[key] = safe_value
                    rendered = str(safe_value)
                    if rendered in result.response_text:
                        protected_terms.append(rendered)

    protected_numeric_tokens = list(dict.fromkeys(
        match.group(0).strip() for match in _COMPOSER_NUMERIC_TOKEN.finditer(result.response_text)
    ))[:24]
    protected_terms.extend(option for option in result.quick_replies if option not in protected_terms)
    return ResponseCompositionInput(
        base_text=result.response_text,
        response_kind=response_kind,
        safe_facts=safe_facts,
        quick_replies=list(result.quick_replies or []),
        required_terms=list(dict.fromkeys(required_terms)),
        protected_terms=list(dict.fromkeys(protected_terms))[:24],
        protected_numeric_tokens=protected_numeric_tokens,
        allow_completion_claims=response_kind is ResponseKind.TOOL_SUCCESS,
        allow_failure_claims=response_kind is ResponseKind.TOOL_FAILURE,
        target_language="en",
    )


def _style_receptionist_result(context: ExecutionContext, result: WorkflowResult) -> WorkflowResult:
    """Fail closed to the backend-safe result if any presentation decoration fails."""
    try:
        styled = style_receptionist_response(
            result.response_text,
            list(result.quick_replies or []),
            style=_response_style_for_context(context),
            response_kind=_response_kind_for_context(context),
            runtime_available_modules=_runtime_available_modules_for_context(context) or (),
        )
        if styled.response_text == result.response_text and styled.quick_replies == list(result.quick_replies or []):
            return result
        return WorkflowResult(
            response_text=styled.response_text,
            quick_replies=styled.quick_replies,
            next_state=result.next_state,
            requires_response=result.requires_response,
            prompt_focus_workflow=result.prompt_focus_workflow,
            prompt_focus_fields=list(result.prompt_focus_fields),
            prompt_focus_kind=result.prompt_focus_kind,
            composer_eligible=result.composer_eligible,
        )
    except Exception as exc:
        logger.warning(
            "Receptionist response styling failed closed",
            extra={"extra_data": {"error_type": type(exc).__name__}},
        )
        return result
def _tool_call_signature(tool_key: str, args: dict) -> str:
    canonical = json.dumps(args or {}, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{tool_key}:{digest}"


def _contains_definitive_success_claim(text: str) -> bool:
    candidate = str(text or "")
    for pattern in TOOL_SUCCESS_CLAIM_PATTERNS:
        for match in pattern.finditer(candidate):
            claim_window = candidate[max(0, match.start() - 36):match.end()]
            if SUCCESS_NEGATION_PATTERN.search(claim_window):
                continue
            return True
    return False


def _safe_tool_failure_message(context: ExecutionContext) -> str:
    ledger = context.session_data.state.action_ledger
    last_result = ledger.last_tool_result
    safe_text = ""
    if last_result and last_result.user_message:
        safe_text = last_result.user_message
    elif ledger.last_error and ledger.last_error.safe_message:
        safe_text = ledger.last_error.safe_message
    if not safe_text:
        safe_text = "I couldn't complete that action right now. Please try again or ask to talk to the team."
    safe_text = sanitize_customer_visible_output(safe_text)
    if safe_text == SAFE_RESPONSE_FALLBACK:
        return "I couldn't complete that action right now. Please try again or ask to talk to the team."
    return safe_text


def _core_model_fallback_text(
    context: ExecutionContext,
    *,
    authoritative_tool_result: bool,
) -> str:
    """Use current backend truth after a tool; otherwise retain the safe provider fallback."""
    if not authoritative_tool_result:
        return "I couldn't complete that request right now. Please try again in a moment or ask to talk to a team member."
    last_result = context.session_data.state.action_ledger.last_tool_result
    if last_result and last_result.success is False:
        return _safe_tool_failure_message(context)
    if last_result and last_result.user_message:
        safe_text = sanitize_customer_visible_output(last_result.user_message)
        if safe_text != SAFE_RESPONSE_FALLBACK:
            return safe_text
    if last_result and last_result.success is True:
        return "That action was completed successfully."
    return "I couldn't complete that action right now, and it has not been submitted. Please try again or contact the business."


def _customer_safe_pending_summary(context: ExecutionContext) -> str:
    """Return the existing backend summary without internal implementation fields."""
    pending = context.session_data.state.action_ledger.pending_action
    if not pending or not pending.summary:
        return ""
    # The generic sanitizer correctly rejects a Python representation of a
    # nested cart.  Render the backend-owned cart as ordinary customer text
    # before that sanitizer sees it, so confirmation remains informative.
    if pending.tool_key == "order.execute" and isinstance(pending.args, dict):
        items = pending.args.get("items")
        if isinstance(items, list) and items:
            lines = ["Please review your order:"]
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = item.get("item_name") or item.get("item_sku")
                quantity = item.get("quantity")
                if isinstance(name, str) and name.strip() and isinstance(quantity, int) and quantity > 0:
                    lines.append(f"- {name.strip()}: quantity {quantity}")
            if len(lines) > 1:
                fulfillment = pending.args.get("fulfillment_type")
                customer = pending.args.get("customer_name")
                if isinstance(fulfillment, str) and fulfillment.strip():
                    lines.append(f"- Fulfillment: {fulfillment.strip()}")
                if isinstance(customer, str) and customer.strip():
                    lines.append(f"- Customer: {customer.strip()}")
                return "\n".join(lines)
    summary = sanitize_customer_visible_output(pending.summary)
    if summary == SAFE_RESPONSE_FALLBACK:
        return ""

    blocked_keys = {
        "id",
        "provider",
        "provider id",
        "provider_id",
        "payment link",
        "payment link url",
        "payment_link_url",
        "payment status",
        "payment_status",
        "status",
        "token",
        "secret",
        "trace",
        "payload",
        "tool",
    }
    visible_lines: list[str] = []
    for line in summary.splitlines():
        stripped = line.strip()
        key, separator, _value = stripped.lstrip("-").strip().partition(":")
        normalized_key = key.strip().lower()
        if separator and (
            normalized_key in blocked_keys
            or normalized_key.startswith("provider")
            or normalized_key.endswith("_id")
            or normalized_key.endswith(" id")
        ):
            continue
        visible_lines.append(line)
    return "\n".join(visible_lines).strip()


def _tool_failure_recovery_response(context: ExecutionContext, result: WorkflowResult) -> WorkflowResult:
    """Keep a backend failure truthful while leaving a recoverable request actionable."""
    ledger = context.session_data.state.action_ledger
    last_result = ledger.last_tool_result
    if not last_result or last_result.success is not False:
        return result

    last_error = ledger.last_error
    if last_error and last_error.code in {"agent_tool_call_limit", "duplicate_tool_call"}:
        return WorkflowResult(
            response_text=last_error.safe_message or _safe_tool_failure_message(context),
            next_state=result.next_state,
            requires_response=result.requires_response,
        )
    if str(last_result.denied_reason or "").startswith((
        "confirmation_required:",
        "customer_verification_required:",
    )):
        return result

    safe_text = _safe_tool_failure_message(context)
    if ledger.pending_action:
        recovery = "Nothing was completed. You can confirm again, change a detail, or cancel this request."
        quick_replies = ["Confirm", "Change", "Cancel"]
    else:
        recovery = "Nothing was completed. Please check the details and try again, or ask the team for help."
        quick_replies = list(result.quick_replies or [])
    return WorkflowResult(
        response_text=f"{safe_text}\n\n{recovery}",
        quick_replies=quick_replies,
        next_state=result.next_state,
        requires_response=result.requires_response,
    )


def _apply_tool_failure_truth_guard(context: ExecutionContext, result: WorkflowResult) -> WorkflowResult:
    ledger = context.session_data.state.action_ledger
    last_result = ledger.last_tool_result
    if not last_result or last_result.success is not False:
        return result
    if not _contains_definitive_success_claim(result.response_text):
        return result
    safe_text = _safe_tool_failure_message(context)
    return WorkflowResult(
        response_text=strip_interactive_hints(safe_text) or safe_text,
        quick_replies=quick_reply_labels_from_text(safe_text),
        next_state=result.next_state,
        requires_response=result.requires_response,
    )


def _has_current_authoritative_success(
    context: ExecutionContext,
    previous_tool_result: Any,
) -> bool:
    last_result = context.session_data.state.action_ledger.last_tool_result
    if (
        last_result is None
        or last_result is previous_tool_result
        or last_result.success is not True
        or not last_result.tool_key
    ):
        return False
    if last_result.created_resource_type and not last_result.created_resource_id:
        return False
    if last_result.action_nonce is not None and not last_result.args_hash:
        return False
    return True


def _apply_authoritative_success_truth_guard(
    context: ExecutionContext,
    result: WorkflowResult,
    *,
    previous_tool_result: Any,
) -> WorkflowResult:
    """Allow completion wording only for a successful dispatch recorded this turn."""
    if not _contains_definitive_success_claim(result.response_text):
        return result
    if _has_current_authoritative_success(context, previous_tool_result):
        return result

    ledger = context.session_data.state.action_ledger
    if ledger.last_tool_result is not previous_tool_result and ledger.last_tool_result is not None:
        safe_text = _safe_tool_failure_message(context)
        quick_replies = ["Confirm", "Change", "Cancel"] if ledger.pending_action else []
    elif ledger.pending_action:
        safe_text = "That action has not been completed yet. You can confirm it, change a detail, or cancel."
        quick_replies = ["Confirm", "Change", "Cancel"]
    else:
        safe_text = "I don't have a successful backend result for that action, so nothing was completed. Please review the details and try again."
        quick_replies = []
    return WorkflowResult(
        response_text=safe_text,
        quick_replies=quick_replies,
        next_state=result.next_state,
        requires_response=result.requires_response,
    )


def _collecting_state(current_state: ConversationState) -> ConversationState:
    if current_state == ConversationState.NEW:
        return ConversationState.COLLECTING_DATA
    return current_state


def _switch_active_task(context: ExecutionContext, task_key: str) -> None:
    state = context.session_data.state
    if state.action_ledger.pending_action:
        logger.info(
            "Blocked active task switch while protected pending action exists: %s -> %s.",
            state.action_ledger.current_task or context.session_data.current_module,
            task_key,
        )
        return
    logger.info(
        "Confirmed active task switch: %s -> %s. Preserving scoped task context.",
        state.action_ledger.current_task or context.session_data.current_module,
        task_key,
    )
    state.action_ledger.current_task = task_key
    state.clear_prompt_focus()
    context.session_data.current_module = task_key
    state.action_ledger.last_error = None
    state.action_ledger.task_status = "idle"
    state.step_data = dict(state.scoped_step_data.get(task_key) or {})


def _cancel_active_task(context: ExecutionContext) -> None:
    state = context.session_data.state
    active_task = state.action_ledger.current_task or context.session_data.current_module
    if active_task:
        state.scoped_step_data.pop(active_task, None)
    state.step_data = {}
    state.action_ledger.current_task = None
    state.action_ledger.pending_action = None
    state.action_ledger.verification.otp_verified_actions = []
    state.action_ledger.verification.otp_verifications = []
    state.action_ledger.last_error = None
    state.action_ledger.task_status = "idle"
    state.clear_prompt_focus()
    context.session_data.current_module = None


def _pending_action_clarification(context: ExecutionContext) -> WorkflowResult:
    summary = _customer_safe_pending_summary(context)
    text = "Please confirm this request, change a detail, or cancel it."
    if summary:
        text = f"{summary}\n\n{text}"
    return WorkflowResult(
        response_text=text,
        quick_replies=["Confirm", "Change", "Cancel"],
        next_state=context.session_data.state.workflow_state,
    )


_PROTECTED_EXECUTION_BLOCKING_INVARIANTS = frozenset({
    "config_binding_mismatch",
    "pending_action_task_mismatch",
    "task_module_mismatch",
    "prompt_focus_workflow_mismatch",
})
_FIELD_MUTATION_BLOCKING_INVARIANTS = frozenset({
    "config_binding_mismatch",
    "prompt_focus_workflow_mismatch",
})


def _blocking_canonical_violations(
    context: ExecutionContext,
    blocked_codes: frozenset[str],
) -> tuple[Any, ...]:
    return tuple(item for item in validate_canonical_state(context) if item.code in blocked_codes)


def _log_canonical_gate_block(event: str, violations: tuple[Any, ...]) -> None:
    logger.warning(
        event,
        extra={
            "extra_data": {
                "event": event,
                "violation_codes": sorted({item.code for item in violations}),
                "workflows": sorted({item.workflow for item in violations if item.workflow}),
            }
        },
    )


def _field_mutation_is_blocked(context: ExecutionContext) -> bool:
    blocking = _blocking_canonical_violations(context, _FIELD_MUTATION_BLOCKING_INVARIANTS)
    if not blocking:
        return False
    _log_canonical_gate_block("active_field_mutation_invariant_blocked", blocking)
    return True


def _trusted_otp_continuation_matches(
    context: ExecutionContext,
) -> Optional[OtpVerificationEvidence]:
    incoming = context.incoming_message or {}
    if str(incoming.get("source_type") or "").lower() != "internal_event":
        return None
    provider_id = str(incoming.get("provider_message_id") or "")
    match = re.fullmatch(
        r"awa-internal:otp_verified_pending_action:([0-9a-f]{64}):([0-9a-f]{32}):([0-9a-f-]{36})",
        provider_id,
    )
    ledger = context.session_data.state.action_ledger
    pending = context.session_data.state.action_ledger.pending_action
    if (
        not match
        or not pending
        or not pending.tool_key
        or not pending.args_hash
        or not pending.action_nonce
        or ledger.task_status in {"completed", "executing"}
    ):
        return None
    binding, verification_ref, channel_session_id = match.groups()
    expected_binding = protected_action_binding(
        tenant_id=context.tenant.id,
        conversation_id=context.session_data.conversation_id,
        channel_session_id=channel_session_id,
        config_version=context.session_data.config_version,
        action_nonce=pending.action_nonce,
        tool_key=pending.tool_key,
        args_hash=pending.args_hash,
    )
    if not hmac.compare_digest(binding, expected_binding):
        return None
    now = datetime.now(timezone.utc)
    for evidence in ledger.verification.otp_verifications or []:
        expires_at = evidence.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if (
            not evidence.consumed
            and str(getattr(evidence, "assurance_source", "")) == "awa_chat_otp_phone_possession"
            and bool(getattr(evidence, "phone_identity_hash", None))
            and expires_at > now
            and hmac.compare_digest(evidence.binding, binding)
            and hmac.compare_digest(evidence.verification_ref, verification_ref)
            and hmac.compare_digest(evidence.action_nonce, pending.action_nonce)
            and evidence.tenant_id == str(context.tenant.id)
            and evidence.conversation_id == str(context.session_data.conversation_id)
            and evidence.channel_session_id == channel_session_id
            and evidence.config_version == context.session_data.config_version
            and evidence.tool_key == pending.tool_key
            and evidence.args_hash == pending.args_hash
        ):
            return evidence
    return None


def _active_workflow_clarification(context: ExecutionContext) -> WorkflowResult:
    return WorkflowResult(
        response_text="Would you like to continue this request, change something, or cancel it?",
        quick_replies=["Continue", "Change", "Cancel"],
        next_state=context.session_data.state.workflow_state,
    )


def _active_workflow_switch_confirmation(context: ExecutionContext) -> WorkflowResult:
    return WorkflowResult(
        response_text=(
            "I kept your current request details ready. Please confirm if you want to cancel this request "
            "before starting something else, or continue with it."
        ),
        quick_replies=["Continue", "Cancel", "Change"],
        next_state=context.session_data.state.workflow_state,
    )


def _has_active_workflow_state(context: ExecutionContext) -> bool:
    state = context.session_data.state
    ledger = state.action_ledger
    if ledger.pending_action:
        return True
    active_task = ledger.current_task or context.session_data.current_module
    if active_task:
        return True
    if state.step_data:
        return True
    return any(bool(value) for value in (state.scoped_step_data or {}).values() if isinstance(value, dict))


def _active_workflow_key(context: ExecutionContext) -> str:
    ledger = context.session_data.state.action_ledger
    task = ledger.current_task or context.session_data.current_module or ""
    if task == "payments":
        return "payment"
    if task == "reminders":
        return "reminder"
    if task == "reviews":
        return "review"
    return task or "general"


def _preserve_active_workflow_direct_response(
    context: ExecutionContext,
    result: WorkflowResult,
    routing_decision,
    *,
    resume_result: Optional[WorkflowResult] = None,
) -> WorkflowResult:
    if not _has_active_workflow_state(context):
        return result
    if getattr(routing_decision, "intent", None) in {"cancel", "thanks"}:
        return result
    if resume_result:
        result.response_text = f"{result.response_text}\n\n{resume_result.response_text}"
        result.quick_replies = list(resume_result.quick_replies or result.quick_replies or [])
        result.next_state = resume_result.next_state or result.next_state
        result.prompt_focus_workflow = resume_result.prompt_focus_workflow
        result.prompt_focus_fields = list(resume_result.prompt_focus_fields)
        result.prompt_focus_kind = resume_result.prompt_focus_kind
        return result
    if context.session_data.state.action_ledger.pending_action:
        return result
    reminder = "I kept your current request details ready. Please continue, change it, or cancel it when you're ready."
    if reminder in result.response_text:
        return result
    result.response_text = f"{result.response_text}\n\n{reminder}"
    if not result.quick_replies:
        result.quick_replies = ["Continue", "Change", "Cancel"]
    return result


def _runtime_available_modules_for_context(context: ExecutionContext) -> Optional[set[str]]:
    incoming = context.incoming_message or {}
    modules = incoming.get("_runtime_available_modules")
    if isinstance(modules, (list, tuple, set, frozenset)):
        return {str(module) for module in modules}
    return None


def _set_runtime_available_modules(context: ExecutionContext, modules: set[str] | frozenset[str]) -> None:
    if context.incoming_message is None:
        context.incoming_message = {}
    context.incoming_message["_runtime_available_modules"] = sorted(str(module) for module in modules)


def _quick_replies_for_available_modules(available_modules: Optional[set[str]]) -> list[str]:
    if available_modules is None:
        return ["Book appointment", "Show menu"]
    replies: list[str] = []
    if "booking" in available_modules:
        replies.append("Book appointment")
    if "order" in available_modules:
        replies.append("Show menu")
    return replies


def _normalized_customer_text(context: ExecutionContext) -> str:
    text = str((context.incoming_message or {}).get("text") or "").lower()
    return re.sub(r"\s+", " ", text).strip()


def _asks_for_service_list(text: str) -> bool:
    return bool(
        re.search(r"\b(?:show|list|view|see|what|which|available)\b.{0,36}\bservices?\b", text)
        or re.fullmatch(r"(?:the )?services?(?: and prices)?", text)
    )


def _asks_for_current_status(text: str) -> bool:
    return bool(re.search(
        r"\b(?:what(?:'s| is)? happening|what is going on|where are we|current status|status of (?:this|my) request|what have you got)\b",
        text,
    ))


def _asks_for_opening_hours(text: str) -> bool:
    return bool(re.search(r"\b(?:opening hours?|business hours?|hours?|timings?|open|close[ds]?)\b", text))


def _is_grounded_active_tangent(context: ExecutionContext) -> bool:
    text = _normalized_customer_text(context)
    return _asks_for_service_list(text) or _asks_for_current_status(text) or _asks_for_opening_hours(text)


def _active_workflow_status_response(context: ExecutionContext) -> WorkflowResult:
    state = context.session_data.state
    ledger = state.action_ledger
    task = ledger.current_task or context.session_data.current_module or "request"
    display_task = {
        "booking": "booking",
        "order": "order",
        "payments": "payment request",
        "payment": "payment request",
        "lead_capture": "contact request",
        "reminders": "reminder",
        "reviews": "review",
        "support": "support request",
    }.get(task, "request")
    collected = state.get_task_step_data(task)
    labels = {
        "service_name": "service",
        "preferred_date": "date",
        "booking_date": "date",
        "preferred_time": "time",
        "booking_time": "time",
        "item_name": "item",
        "quantity": "quantity",
        "fulfillment_type": "fulfillment",
        "amount": "amount",
        "currency": "currency",
        "rating": "rating",
        "scheduled_for": "schedule",
    }
    facts: list[str] = []
    for key, label in labels.items():
        value = collected.get(key) if isinstance(collected, dict) else None
        if value in (None, "", [], {}):
            continue
        safe_value = sanitize_customer_visible_output(str(value)[:160])
        if safe_value != SAFE_RESPONSE_FALLBACK:
            facts.append(f"{label}: {safe_value}")
    text = f"Your {display_task} is still active."
    if facts:
        text += " I have " + "; ".join(facts[:5]) + "."
    if ledger.pending_action:
        status = str(ledger.task_status or "awaiting_confirmation").replace("_", " ")
        text += f" It is {status} and has not been completed yet."
    elif state.prompt_focus and state.prompt_focus.requested_fields:
        requested = ", ".join(field.replace("_", " ") for field in state.prompt_focus.requested_fields[:3])
        text += f" I am currently waiting for: {requested}."
    else:
        text += " Nothing has been submitted yet."
    return WorkflowResult(response_text=text, next_state=state.workflow_state)


def _module_unavailable_response(context: ExecutionContext, module_name: str) -> WorkflowResult:
    state = context.session_data.state
    canonical = normalize_runtime_module_key(module_name)
    if canonical:
        state.scoped_step_data.pop(canonical, None)
    state.step_data = {}
    state.action_ledger.pending_action = None
    state.action_ledger.last_error = None
    state.action_ledger.task_status = "idle"
    state.action_ledger.current_task = None
    state.clear_prompt_focus()
    context.session_data.current_module = None

    available_modules = _runtime_available_modules_for_context(context) or set()
    quick_replies: list[str] = []
    if "support" in available_modules or "human_handoff" in available_modules:
        quick_replies.append("Contact support")
    return WorkflowResult(
        response_text="That option is currently unavailable. Nothing was completed.",
        quick_replies=quick_replies,
        next_state=context.session_data.state.workflow_state,
    )


def _unavailable_active_workflow_response(context: ExecutionContext, available_modules: set[str]) -> Optional[WorkflowResult]:
    ledger = context.session_data.state.action_ledger
    module_name = ""
    if ledger.pending_action and ledger.pending_action.tool_key:
        module_name = ledger.pending_action.tool_key
    else:
        module_name = ledger.current_task or context.session_data.current_module or ""
    canonical = normalize_runtime_module_key(module_name)
    if canonical and canonical not in available_modules:
        return _module_unavailable_response(context, canonical)
    return None


def _direct_response_for_route(
    context: ExecutionContext,
    routing_decision,
    available_modules: Optional[set[str] | frozenset[str]] = None,
) -> Optional[WorkflowResult]:
    if available_modules is None:
        available_modules = _runtime_available_modules_for_context(context)
    available = set(available_modules) if available_modules is not None else None
    state = context.session_data.state
    next_state = _collecting_state(state.workflow_state)
    raw_customer_text = str((context.incoming_message or {}).get("text") or "").strip()
    if (
        raw_customer_text
        and not _has_active_workflow_state(context)
        and re.fullmatch(r"[^A-Za-z0-9]+", raw_customer_text)
    ):
        return WorkflowResult(
            response_text="I’m not sure what you mean yet. Could you tell me what you’d like help with?",
            next_state=next_state,
        )
    if routing_decision.intent == "greeting" and routing_decision.reason in {"greeting", "empty_message_greeting"}:
        return WorkflowResult(
            response_text="Hi! How can I help you today?",
            next_state=next_state,
        )
    if routing_decision.intent == "cancel" and routing_decision.reason == "explicit_cancellation":
        if routing_decision.should_clear_active_task:
            _cancel_active_task(context)
            return WorkflowResult(
                response_text="Okay, I've cancelled this request. What would you like to do next?",
                next_state=ConversationState.CANCELLED,
            )
        return WorkflowResult(
            response_text="There isn't an active request to cancel. What would you like to do?",
            next_state=next_state,
        )
    if routing_decision.intent == "thanks":
        return WorkflowResult(response_text="You're welcome.", next_state=next_state)
    if routing_decision.route_type == "clarification" and routing_decision.intent == "ambiguous":
        replies = _quick_replies_for_available_modules(available)
        if replies == ["Book appointment"]:
            text = "I can help with one thing at a time. Would you like to book an appointment?"
        elif replies == ["Show menu"]:
            text = "I can help with one thing at a time. Would you like to see the menu?"
        elif replies:
            text = "I can help with one thing at a time. Would you like to book an appointment or see the menu?"
        else:
            text = "I can help with one thing at a time. What would you like to ask?"
        return WorkflowResult(
            response_text=text,
            quick_replies=replies,
            next_state=next_state,
        )
    if routing_decision.reason in {"exact_catalog_request", "natural_catalog_request"}:
        if available is not None and "order" not in available:
            return WorkflowResult(
                response_text="Ordering is not available right now.",
                quick_replies=_quick_replies_for_available_modules(available),
                next_state=next_state,
            )
        response = build_catalog_choices_response(context.config.config_data, ordering_mode=False)
        return WorkflowResult(
            response_text=response.text,
            quick_replies=response.quick_replies,
            next_state=next_state,
        )
    if routing_decision.reason == "exact_opening_hours_request":
        response = build_hours_response(context.config.config_data)
        return WorkflowResult(
            response_text=response.text,
            quick_replies=response.quick_replies,
            next_state=next_state,
        )
    return None


def _rag_skip_decision(
    context: ExecutionContext,
    routing_decision,
    *,
    current_turn_tool_success: bool = False,
) -> tuple[bool, str | None]:
    """Return a safe RAG skip decision from deterministic route and ledger state."""
    ledger = context.session_data.state.action_ledger
    intent = str(getattr(routing_decision, "intent", "") or "").lower()
    route_type = str(getattr(routing_decision, "route_type", "") or "").lower()
    reason = str(getattr(routing_decision, "reason", "") or "").lower()
    if getattr(routing_decision, "is_tangent", False):
        return False, None
    if intent in {"faq", "unknown", "support", "ambiguous"}:
        return False, None
    if reason == "active_task_faq_tangent":
        return False, None
    if intent == "greeting" and route_type == "direct_answer":
        return True, "greeting"
    if intent == "thanks":
        return True, "thanks_or_goodbye"
    if reason in {"exact_catalog_request", "natural_catalog_request"}:
        return True, "deterministic_direct"
    if getattr(ledger, "pending_action", None):
        return True, "protected_pending_action"
    if current_turn_tool_success:
        return True, "post_tool_success"
    if (
        route_type == "active_task"
        and intent in TRANSACTIONAL_INTENTS
        and reason in {"active_task_likely_field_value", "active_task_continuation", "active_task_short_continuation"}
    ):
        return True, "transactional_field_collection"
    return False, None


def _trace_direct_bypass(
    context: ExecutionContext,
    routing_decision,
    result: WorkflowResult,
    *,
    rag_skip_reason: str = "deterministic_direct",
) -> None:
    if rag_skip_reason not in SAFE_RAG_SKIP_REASONS:
        rag_skip_reason = "deterministic_direct"
    log_llm_input_trace(build_llm_input_trace(
        context=context,
        provider=None,
        model=None,
        integration_mode=settings.AWA_INTEGRATION_MODE,
        system_instructions="",
        tools=[],
        memory_context="",
        knowledge_context="",
        route_intent=getattr(routing_decision, "intent", None),
        route_type=getattr(routing_decision, "route_type", None),
        rag_skipped=True,
        rag_skip_reason=rag_skip_reason,
        direct_path_bypassed_llm=True,
        quick_reply_count=len(result.quick_replies or []),
        final_output_type="direct_response",
    ))


def _order_config(context: ExecutionContext) -> dict:
    modules = context.config.config_data.get("modules", {}) if isinstance(context.config.config_data, dict) else {}
    order_config = modules.get("order", {}) if isinstance(modules, dict) else {}
    return order_config if isinstance(order_config, dict) else {}


def _order_module_enabled(context: ExecutionContext) -> bool:
    return _order_config(context).get("enabled") is True


def _has_pending_order_disambiguation(context: ExecutionContext) -> bool:
    state = context.session_data.state
    scoped = state.scoped_step_data.get("order")
    candidates = [state.step_data]
    if isinstance(scoped, dict):
        candidates.append(scoped)
    for candidate in candidates:
        pending = (candidate or {}).get("pending_disambiguation") if isinstance(candidate, dict) else None
        if isinstance(pending, dict) and pending.get("options"):
            return True
    return False


def _has_active_order_scope(context: ExecutionContext) -> bool:
    state = context.session_data.state
    ledger = state.action_ledger
    pending = ledger.pending_action
    if pending and str(pending.tool_key or "").startswith("order."):
        return True
    if ledger.current_task == "order" or context.session_data.current_module == "order":
        return True
    scoped = state.scoped_step_data.get("order")
    if isinstance(scoped, dict) and scoped and ledger.task_status in {
        "collecting",
        "failed",
        "blocked",
        "awaiting_confirmation",
        "prepared",
    }:
        return True
    return False


def _recent_order_field_error(context: ExecutionContext, field_name: str) -> bool:
    ledger = context.session_data.state.action_ledger
    last_result = ledger.last_tool_result
    if not last_result or not isinstance(last_result.validation_errors, dict):
        return False
    return field_name in last_result.validation_errors


def _prompt_focus_result_kwargs(
    context: ExecutionContext,
    workflow: str,
    requested_fields: list[Optional[str]],
    *,
    reason_field: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "prompt_focus_workflow": workflow,
        "prompt_focus_fields": [str(field) for field in requested_fields if field],
        "prompt_focus_kind": "invalid_field" if reason_field else "missing_fields",
    }


def _order_field_defs(tool_def: Any, context: ExecutionContext) -> dict[str, dict]:
    try:
        fields = tool_def.module.get_required_fields_for_context(context)
    except AttributeError:
        fields = tool_def.module.get_required_fields(context.config.config_data)
    return {str(field.get("key")): field for field in fields if field.get("key")}


def _existing_order_field_invalid(tool_def: Any, context: ExecutionContext, field_name: str, value: Any) -> bool:
    field_def = _order_field_defs(tool_def, context).get(field_name)
    if value in (None, ""):
        return True
    if not field_def:
        return False
    validation = tool_def.module.validate_input(field_def, value, tenant_config=context.config.config_data)
    return not validation.is_valid


def _order_missing_required_fields(tool_def: Any, context: ExecutionContext) -> list[str]:
    current = context.session_data.state.get_task_step_data("order")
    missing: list[str] = []
    for field in _order_field_defs(tool_def, context).values():
        if not field.get("required", True):
            continue
        key = str(field.get("key") or "")
        if key == "item_id":
            key = "item_name"
        if key == "item_name" and isinstance(current.get("items"), list) and current["items"]:
            continue
        if key and current.get(key) in (None, ""):
            missing.append(key)
    return missing


def _order_fulfillment_options(context: ExecutionContext, module: Any) -> list[str]:
    try:
        return list(module._configured_fulfillment_options(context.config.config_data))
    except Exception:
        raw = _order_config(context).get("fulfillment_types") or []
        return [str(value).strip().lower() for value in raw if str(value).strip()]


def _order_focus_field_key(context: ExecutionContext) -> Optional[str]:
    """First currently-requested order field in the safe configured key set."""
    focus = context.session_data.state.prompt_focus
    if focus is None or getattr(focus, "workflow", None) != "order":
        return None
    requested = getattr(focus, "requested_fields", None) or ()
    try:
        defs = set(_order_field_defs(None, context).keys())
    except Exception:
        defs = set()
    if not defs:
        modules = context.config.config_data.get("modules", {}) if isinstance(context.config.config_data, dict) else {}
        order = modules.get("order", {}) if isinstance(modules, dict) else {}
        fields = order.get("fields") if isinstance(order, dict) else None
        if isinstance(fields, list):
            defs = {
                str(field.get("key") or "").strip()
                for field in fields
                if isinstance(field, dict) and str(field.get("key") or "").strip()
            }
    for field in requested:
        key = str(field or "").strip()
        if key and key in defs:
            return key
    return None


def _order_missing_required_fields_from_defs(context: ExecutionContext) -> list[str]:
    """Missing required order field keys derived directly from tenant config."""
    modules = context.config.config_data.get("modules", {}) if isinstance(context.config.config_data, dict) else {}
    order = modules.get("order", {}) if isinstance(modules, dict) else {}
    fields = order.get("fields") if isinstance(order, dict) else None
    current = context.session_data.state.get_task_step_data("order")
    missing: list[str] = []
    if isinstance(fields, list):
        for field in fields:
            if not isinstance(field, dict):
                continue
            if field.get("enabled", True) is False:
                continue
            key = str(field.get("key") or "").strip()
            if not key:
                continue
            if not field.get("required", True):
                continue
            if key and current.get(key) in (None, ""):
                missing.append(key)
    return missing


def _order_field_clarification_response(
    context: ExecutionContext,
    tool_def: Any,
    *,
    reason_field: Optional[str] = None,
) -> WorkflowResult:
    missing = _order_missing_required_fields(tool_def, context)
    module = tool_def.module
    fulfillment_options = _order_fulfillment_options(context, module)
    quick_replies: list[str] = []

    target = reason_field or (missing[0] if missing else None)
    if target == "fulfillment_type":
        if fulfillment_options:
            options = ", ".join(fulfillment_options)
            quick_replies = fulfillment_options[:5]
            prefix = "That fulfillment option is not available. " if _recent_order_field_error(context, target) else ""
            text = f"{prefix}Please choose one of the available fulfillment options: {options}."
        else:
            text = "Please choose an available fulfillment option for this order."
    elif target in {"item_name", "item_sku"}:
        text = "Which item would you like to order?"
    elif target == "quantity":
        text = "What quantity would you like?"
    elif target == "phone":
        text = "Please share a valid phone number for this order."
    elif target == "customer_name":
        text = "Please share the name for this order."
    elif target == "delivery_address":
        text = "Please share the delivery address for this order."
    else:
        text = "I kept your order details. Please share the next order detail you want to provide."

    text = grouped_clarification_text(
        text,
        workflow="order",
        missing_fields=missing,
        style=_response_style_for_context(context),
    )
    focus_kwargs = _prompt_focus_result_kwargs(context, "order", [reason_field] if reason_field else missing, reason_field=reason_field)
    return WorkflowResult(
        response_text=text,
        quick_replies=quick_replies,
        next_state=context.session_data.state.workflow_state,
        **focus_kwargs,
    )


def _booking_config(context: ExecutionContext) -> dict:
    modules = context.config.config_data.get("modules", {}) if isinstance(context.config.config_data, dict) else {}
    booking_config = modules.get("booking", {}) if isinstance(modules, dict) else {}
    return booking_config if isinstance(booking_config, dict) else {}


def _booking_module_enabled(context: ExecutionContext) -> bool:
    return _booking_config(context).get("enabled") is True


def _authoritative_booking_apply_enabled() -> bool:
    return bool(
        getattr(settings, "ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW", False)
        and getattr(settings, "ENABLE_GLOBAL_CONFIGURED_TURN_BOOKING_APPLY", False)
    )


def _authoritative_booking_apply_eligible(
    context: ExecutionContext,
    *,
    require_missing: bool = True,
) -> bool:
    """Bounded eligibility for Iteration 5 authoritative booking continuation.

    ``require_missing`` gates the ordinary apply path on at least one required
    field still being open. The idempotent already-satisfied replay path
    (Outcome B) sets it False: every required field may already be complete, yet
    the same batch must still be handled write-free from authoritative state
    instead of falling back to the legacy collector.
    """
    from backend.logic.engine.global_configured_turn_shadow import (
        _active_workflow_is_booking,
        _booking_module_enabled as _shadow_booking_enabled,
        _customer_authored_source,
        _is_trusted_internal_event,
    )

    if not _authoritative_booking_apply_enabled():
        return False
    incoming = context.incoming_message or {}
    if not _customer_authored_source(context):
        return False
    if _is_trusted_internal_event(context):
        return False
    if not _active_workflow_is_booking(context):
        return False
    if not _shadow_booking_enabled(context):
        return False
    state = context.session_data.state
    ledger = state.action_ledger
    if ledger.pending_action is not None:
        return False
    # A configured booking service must already be selected before this turn.
    current = state.get_task_step_data("booking")
    selected_service = str(current.get("service_name") or "").strip()
    service_match = resolve_configured_option(
        customer_text=selected_service,
        active_workflow="booking",
        requested_fields=("service_name",),
        config_data=context.config.config_data,
        runtime_available_modules={"booking"},
    ) if selected_service else None
    if service_match is None:
        return False
    if not require_missing:
        return True
    # At least one configured booking field must remain missing.
    fields = configured_field_collection_coordinator.resolve_fields(context)
    missing = tuple(
        item for item in fields
        if item.required and current.get(item.key) in (None, "")
    )
    if not missing:
        return False
    return True


def _authoritative_order_apply_enabled() -> bool:
    return bool(
        getattr(settings, "ENABLE_ORDER_CONFIGURED_TURN_SHADOW", False)
        and getattr(settings, "ENABLE_ORDER_CONFIGURED_TURN_APPLY", False)
    )


def _authoritative_order_apply_eligible(context: ExecutionContext) -> bool:
    """Bounded eligibility for Iteration 6C authoritative order continuation.

    Mirrors the Iteration 6B shadow boundary plus the authoritative flags. The
    order must already be active with a customer-facing item_name, no pending
    disambiguation, no pending protected action, no pending confirmation/OTP/
    customer-assurance continuation, and a valid Iteration 6B shadow snapshot
    whose semantic result is usable.
    """
    from backend.logic.engine.order_configured_turn_shadow import (
        SHADOW_EVIDENCE_KEY,
        _active_workflow_is_order,
        _customer_authored_source,
        _has_pending_order_disambiguation,
        _has_pending_protected_action,
        _is_trusted_internal_event,
        _order_module_enabled,
        _order_has_item_name,
    )

    if not _authoritative_order_apply_enabled():
        return False
    if not _customer_authored_source(context):
        return False
    if _is_trusted_internal_event(context):
        return False
    if not _active_workflow_is_order(context):
        return False
    if not _order_module_enabled(context):
        return False
    if not _order_has_item_name(context):
        return False
    if _has_pending_order_disambiguation(context):
        return False
    if _has_pending_protected_action(context):
        return False
    state = context.session_data.state
    if state.action_ledger.task_status in {"completed", "cancelled"}:
        return False
    if context.session_data.state.action_ledger.pending_action is not None:
        return False
    snapshot = context.shadow_turn_evidence.get(SHADOW_EVIDENCE_KEY)
    if not isinstance(snapshot, dict):
        return False
    semantic_result = snapshot.get("semantic_result")
    if semantic_result is None:
        return False
    if bool(getattr(semantic_result, "rejected", False)):
        return False
    if bool(getattr(semantic_result, "gateway_unavailable", False)) and not getattr(semantic_result, "accepted_candidates", None):
        return False
    return True


def _authoritative_lead_apply_enabled() -> bool:
    return bool(
        getattr(settings, "ENABLE_LEAD_CONFIGURED_TURN_SHADOW", False)
        and getattr(settings, "ENABLE_LEAD_CONFIGURED_TURN_APPLY", False)
    )


def _authoritative_lead_terminal_apply_enabled() -> bool:
    """All three lead flags are required; unsafe to enable before 7D-D."""
    return bool(
        getattr(settings, "ENABLE_LEAD_CONFIGURED_TURN_SHADOW", False)
        and getattr(settings, "ENABLE_LEAD_CONFIGURED_TURN_APPLY", False)
        and getattr(settings, "ENABLE_LEAD_CONFIGURED_TURN_TERMINAL_APPLY", False)
    )


def _authoritative_lead_apply_eligible(context: ExecutionContext) -> bool:
    """Bounded eligibility for Iteration 7C authoritative lead continuation.

    Mirrors the Iteration 7B shadow boundary plus the authoritative apply flag. The
    lead must be an already-active customer-authored continuation, the lead module
    enabled, lead-scoped state present, lead not cancelled/completed, no pending
    protected action, no OTP/customer-assurance continuation, a valid Iteration 7B
    shadow snapshot whose semantic result is usable, and at least one safe configured
    required field still missing before application.
    """
    from backend.logic.engine.lead_configured_turn_shadow import (
        _active_workflow_is_lead,
        _customer_authored_source,
        _has_pending_protected_action,
        _is_otp_or_assurance_continuation,
        _is_trusted_internal_event,
        _lead_module_enabled,
        _lead_scoped_state_exists,
        _lead_state_terminal,
        _safe_field_keys,
    )

    if not _authoritative_lead_apply_enabled():
        return False
    if not _customer_authored_source(context):
        return False
    if _is_trusted_internal_event(context):
        return False
    if not _active_workflow_is_lead(context):
        return False
    if not _lead_module_enabled(context):
        return False
    if not _lead_scoped_state_exists(context):
        return False
    if _lead_state_terminal(context):
        return False
    if _has_pending_protected_action(context):
        return False
    if _is_otp_or_assurance_continuation(context):
        return False
    state = context.session_data.state
    if state.action_ledger.pending_action is not None:
        return False
    snapshot = context.shadow_turn_evidence.get(LEAD_SHADOW_EVIDENCE_KEY)
    if not isinstance(snapshot, dict):
        return False
    semantic_result = snapshot.get("semantic_result")
    if semantic_result is None:
        return False
    if bool(getattr(semantic_result, "rejected", False)):
        return False
    if bool(getattr(semantic_result, "gateway_unavailable", False)) and not getattr(semantic_result, "accepted_candidates", None):
        return False
    safe_keys, _ = _safe_field_keys(context)
    if not safe_keys:
        return False
    return True


def _lead_shadow_candidate_map(snapshot: dict) -> dict:
    """Rebuild the raw configured candidate map from an Iteration 7B lead snapshot."""
    return _build_candidate_map_from_shadow(snapshot) or {}


def _order_shadow_candidate_map(snapshot: dict) -> dict:
    """Rebuild the raw configured candidate map from an Iteration 6B snapshot."""
    from backend.logic.engine.order_configured_turn_shadow import (
        _build_candidate_map_from_shadow,
    )

    return _build_candidate_map_from_shadow(snapshot) or {}


def _focus_field_key(context: ExecutionContext) -> Optional[str]:
    focus = context.session_data.state.prompt_focus
    if focus is None or getattr(focus, "workflow", None) != "booking":
        return None
    requested = getattr(focus, "requested_fields", None) or ()
    fields = configured_field_collection_coordinator.resolve_fields(context)
    safe_keys = {item.key for item in fields}
    for field in requested:
        key = str(field or "").strip()
        if key and key in safe_keys:
            return key
    return None


def _has_active_booking_scope(context: ExecutionContext) -> bool:
    state = context.session_data.state
    ledger = state.action_ledger
    pending = ledger.pending_action
    if pending and str(pending.tool_key or "").startswith("booking."):
        return True
    if ledger.current_task == "booking" or context.session_data.current_module == "booking":
        return True
    scoped = state.scoped_step_data.get("booking")
    if isinstance(scoped, dict) and scoped and ledger.task_status in {
        "collecting",
        "failed",
        "blocked",
        "awaiting_confirmation",
        "prepared",
    }:
        return True
    return False


def _configured_booking_service_mention(
    context: ExecutionContext,
    customer_text: str,
) -> dict[str, Any] | None:
    """Resolve one full tenant-owned service label embedded in a rich turn."""
    normalized_text = " ".join(customer_text.casefold().split())
    if not normalized_text:
        return None
    services = context.config.config_data.get("services")
    if not isinstance(services, (list, tuple)):
        return None
    matches = []
    for service in services:
        if not isinstance(service, Mapping) or service.get("enabled", True) is False:
            continue
        name = str(service.get("name") or "").strip()
        normalized_name = " ".join(name.casefold().split())
        if normalized_name and re.search(
            r"(?<!\\w)" + re.escape(normalized_name) + r"(?!\\w)", normalized_text
        ):
            matches.append(service)
    if len(matches) != 1:
        return None
    service = matches[0]
    patch: dict[str, Any] = {"service_name": str(service["name"])}
    stable_id = service.get("id")
    if (
        isinstance(stable_id, str) and bool(stable_id.strip())
    ) or (
        isinstance(stable_id, int) and not isinstance(stable_id, bool)
    ):
        patch["service_id"] = stable_id
    return patch


def _recent_booking_field_error(context: ExecutionContext, field_name: str) -> bool:
    ledger = context.session_data.state.action_ledger
    last_result = ledger.last_tool_result
    if not last_result or not isinstance(last_result.validation_errors, dict):
        return False
    aliases = {
        "booking_date": {"booking_date", "preferred_date", "date", "booking"},
        "booking_time": {"booking_time", "preferred_time", "time", "booking"},
        "preferred_date": {"booking_date", "preferred_date", "date", "booking"},
        "preferred_time": {"booking_time", "preferred_time", "time", "booking"},
    }.get(field_name, {field_name})
    return bool(aliases & set(last_result.validation_errors.keys()))


def _booking_field_defs(tool_def: Any, context: ExecutionContext) -> dict[str, dict]:
    fields = tool_def.module.get_required_fields(context.config.config_data)
    return {str(field.get("key")): field for field in fields if field.get("key")}


def _booking_name_field_key(field_defs: dict[str, dict], properties: dict[str, Any]) -> Optional[str]:
    for candidate in ("customer_name", *[name for name, canonical in BOOKING_FIELD_ALIASES.items() if canonical == "customer_name"]):
        if candidate in properties or candidate in field_defs:
            return candidate
    for key in field_defs:
        if "name" in key.lower():
            return key
    return None


def _booking_field_key_for_update(raw_key: str, field_defs: dict[str, dict], properties: dict[str, Any]) -> Optional[str]:
    key = canonical_booking_field(raw_key)
    if key == "booking_date":
        if "preferred_date" in properties or "preferred_date" in field_defs:
            return "preferred_date"
        if "date" in properties or "date" in field_defs:
            return "date"
        configured_dates = [
            name for name, field in field_defs.items()
            if name in properties and str(field.get("type") or "").lower() == "date"
        ]
        return configured_dates[0] if len(configured_dates) == 1 else None
    if key == "booking_time":
        if "preferred_time" in properties or "preferred_time" in field_defs:
            return "preferred_time"
        if "time" in properties or "time" in field_defs:
            return "time"
        configured_times = [
            name for name, field in field_defs.items()
            if name in properties and str(field.get("type") or "").lower() == "time"
        ]
        return configured_times[0] if len(configured_times) == 1 else None
    if key == "customer_name":
        return _booking_name_field_key(field_defs, properties)
    if key in properties or key in field_defs:
        return key
    return None


def _existing_booking_field_invalid(tool_def: Any, context: ExecutionContext, field_name: str, value: Any) -> bool:
    field_def = _booking_field_defs(tool_def, context).get(field_name)
    if value in (None, ""):
        return True
    if not field_def:
        return False
    validation = tool_def.module.validate_input(field_def, value, tenant_config=context.config.config_data)
    if not validation.is_valid:
        return True
    payload_error = tool_def.module.validate_payload(context)
    return bool(payload_error and _recent_booking_field_error(context, field_name))


def _booking_missing_required_fields(tool_def: Any, context: ExecutionContext) -> list[str]:
    current = context.session_data.state.get_task_step_data("booking")
    missing: list[str] = []
    # A valid configured service is a booking-domain prerequisite, separate
    # from tenant-configured customer fields.  Do not let its absence shift the
    # custom collection focus to an arbitrary string/date/time key.
    selected_service = str(current.get("service_name") or "").strip()
    service_match = resolve_configured_option(
        customer_text=selected_service,
        active_workflow="booking",
        requested_fields=("service_name",),
        config_data=context.config.config_data,
        runtime_available_modules={"booking"},
    ) if selected_service else None
    if service_match is None:
        missing.append("service_name")
    for field in _booking_field_defs(tool_def, context).values():
        if not field.get("required", True):
            continue
        key = str(field.get("key") or "")
        if key in {"service_id", "service_name"}:
            continue
        canonical_key = canonical_booking_field(key)
        equivalent_keys = [name for name in (key, canonical_key, *BOOKING_FIELD_ALIASES.keys()) if canonical_booking_field(name) == canonical_key]
        if key and all(current.get(candidate) in (None, "") for candidate in equivalent_keys):
            missing.append(key)
    return missing


def _booking_clarification_response(
    context: ExecutionContext,
    tool_def: Any,
    *,
    reason_field: Optional[str] = None,
) -> WorkflowResult:
    missing = _booking_missing_required_fields(tool_def, context)
    target = reason_field or (missing[0] if missing else None)
    if target in {"service_name", "service"}:
        service_choices = build_service_choices_response(
            context.config.config_data,
            booking_mode=True,
        )
        text = service_choices.text
        quick_replies = service_choices.quick_replies
    elif target in {"booking_date", "preferred_date", "date"}:
        prefix = "That date is not available. " if _recent_booking_field_error(context, target) else ""
        text = f"{prefix}Which date would you like for the booking?"
    elif target in {"booking_time", "preferred_time", "time"}:
        prefix = "That time is not available. " if _recent_booking_field_error(context, target) else ""
        text = f"{prefix}What other time would you like for the booking?"
    elif target in {"customer_name", "patient_name", "name"}:
        text = "Please share the name for this booking."
    elif target == "phone":
        text = "Please share a valid phone number for this booking."
    elif target == "email":
        text = "Please share a valid email address for this booking."
    else:
        text = "I kept your booking details. Please share the next booking detail you want to provide."
    if target not in {"service_name", "service"}:
        quick_replies = []
    text = grouped_clarification_text(
        text,
        workflow="booking",
        missing_fields=missing,
        style=_response_style_for_context(context),
    )
    # Booking collection is deliberately one-field focused.  The coordinator
    # resolves the first missing configured key in tenant-defined order; do not
    # expose every remaining key as simultaneous deterministic targets.
    focus_kwargs = _prompt_focus_result_kwargs(context, "booking", [target] if target else [], reason_field=reason_field)
    return WorkflowResult(
        response_text=text,
        quick_replies=quick_replies,
        next_state=context.session_data.state.workflow_state,
        **focus_kwargs,
    )


def _module_config(context: ExecutionContext, module_name: str) -> dict:
    modules = context.config.config_data.get("modules", {}) if isinstance(context.config.config_data, dict) else {}
    module_config = modules.get(module_name, {}) if isinstance(modules, dict) else {}
    return module_config if isinstance(module_config, dict) else {}


def _module_enabled(context: ExecutionContext, module_name: str) -> bool:
    return _module_config(context, module_name).get("enabled") is True


def _has_active_module_scope(context: ExecutionContext, module_name: str) -> bool:
    state = context.session_data.state
    ledger = state.action_ledger
    pending = ledger.pending_action
    if pending and str(pending.tool_key or "").startswith(f"{module_name}."):
        return True
    if ledger.current_task == module_name or context.session_data.current_module == module_name:
        return True
    scoped = state.scoped_step_data.get(module_name)
    if isinstance(scoped, dict) and scoped and ledger.task_status in {
        "collecting",
        "failed",
        "blocked",
        "awaiting_confirmation",
        "prepared",
    }:
        return True
    return False


def _module_field_defs(tool_def: Any, context: ExecutionContext) -> dict[str, dict]:
    try:
        fields = tool_def.module.get_required_fields_for_context(context)
    except AttributeError:
        fields = tool_def.module.get_required_fields(context.config.config_data)
    return {str(field.get("key")): field for field in fields if field.get("key")}


def _recent_module_field_error(context: ExecutionContext, field_name: str) -> bool:
    ledger = context.session_data.state.action_ledger
    last_result = ledger.last_tool_result
    if not last_result or not isinstance(last_result.validation_errors, dict):
        return False
    return field_name in last_result.validation_errors


def _existing_module_field_invalid(tool_def: Any, context: ExecutionContext, field_name: str, value: Any) -> bool:
    field_def = _module_field_defs(tool_def, context).get(field_name)
    if value in (None, ""):
        return True
    if not field_def:
        return False
    validation = tool_def.module.validate_input(field_def, value, tenant_config=context.config.config_data)
    return not validation.is_valid


def _module_missing_required_fields(tool_def: Any, context: ExecutionContext, module_name: str) -> list[str]:
    current = context.session_data.state.get_task_step_data(module_name)
    missing: list[str] = []
    for field in _module_field_defs(tool_def, context).values():
        if not field.get("required", True):
            continue
        key = str(field.get("key") or "")
        if key and current.get(key) in (None, ""):
            missing.append(key)
    return missing


def _module_missing_required_fields_from_defs(context: ExecutionContext, module_name: str) -> list[str]:
    """Missing required field keys derived directly from the tenant module config.

    Used by the authoritative continuation path when no tool definition is available
    (e.g. a disabled or unresolvable module). Never touches runtime candidates.
    """
    import copy as _copy

    modules = context.config.config_data.get("modules", {}) if isinstance(context.config.config_data, dict) else {}
    module_cfg = modules.get(module_name, {}) if isinstance(modules, dict) else {}
    fields = module_cfg.get("fields") if isinstance(module_cfg, dict) else None
    current = context.session_data.state.get_task_step_data(module_name)
    missing: list[str] = []
    if isinstance(fields, list):
        for field in fields:
            if not isinstance(field, dict):
                continue
            if field.get("enabled", True) is False:
                continue
            key = str(field.get("key") or "").strip()
            if not key:
                continue
            if not field.get("required", True):
                continue
            if key and current.get(key) in (None, ""):
                missing.append(key)
    return missing


def _lead_field_key_for_update(raw_key: str, field_defs: dict[str, dict], properties: dict[str, Any]) -> Optional[str]:
    key = str(raw_key)
    if key == "customer_name":
        if "name" in properties or "name" in field_defs:
            return "name"
        return "customer_name" if "customer_name" in properties or "customer_name" in field_defs else None
    if key in {"source", "notes", "message"}:
        return key if key in properties or key in field_defs else None
    if key in properties or key in field_defs:
        return key
    return None


def _support_field_key_for_update(raw_key: str, field_defs: dict[str, dict], properties: dict[str, Any]) -> Optional[str]:
    key = str(raw_key)
    if key == "customer_name":
        if "customer_name" in properties or "customer_name" in field_defs:
            return "customer_name"
        return "name" if "name" in properties or "name" in field_defs else None
    if key in {"issue_summary", "support_reason"}:
        for candidate in (key, "issue_description"):
            if candidate in properties or candidate in field_defs:
                return candidate
        return None
    if key in {"order_reference", "notes", "preferred_contact_time", "phone", "email"}:
        return key if key in properties or key in field_defs else None
    if key in properties or key in field_defs:
        return key
    return None


def _reminder_field_key_for_update(raw_key: str, field_defs: dict[str, dict], properties: dict[str, Any]) -> Optional[str]:
    key = str(raw_key)
    if key in {"reminder_message", "message"}:
        for candidate in ("message_text", "message", "note"):
            if candidate in properties or candidate in field_defs:
                return candidate
        return None
    if key == "customer_name":
        for candidate in ("customer_name", "name"):
            if candidate in properties or candidate in field_defs:
                return candidate
        return None
    if key in {"scheduled_for", "channel", "phone", "email", "notes"}:
        return key if key in properties or key in field_defs else None
    return None


def _review_field_key_for_update(raw_key: str, field_defs: dict[str, dict], properties: dict[str, Any]) -> Optional[str]:
    key = str(raw_key)
    if key == "feedback":
        for candidate in ("feedback", "feedback_text"):
            if candidate in properties or candidate in field_defs:
                return candidate
        return None
    if key == "customer_name":
        for candidate in ("customer_name", "name"):
            if candidate in properties or candidate in field_defs:
                return candidate
        return None
    if key in {"rating", "phone", "email", "order_reference", "service_name", "notes"}:
        return key if key in properties or key in field_defs else None
    return None


# Payment: only the explicit safe canonical keys are accepted; anything else is rejected.
_PAYMENT_SAFE_CANONICAL_KEYS: frozenset = frozenset({
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

# Absolute payment blocklist: never apply these, even if schema has them.
_PAYMENT_BLOCKED_FIELDS: frozenset = frozenset({
    "provider", "payment_type", "type",
    "payment_status", "status", "paid", "success", "captured", "failed", "refunded",
    "payment_link_url", "provider_payment_id", "provider_order_id", "transaction_id",
    "signature", "webhook_payload", "refund_id", "settlement_id",
    "tenant_id", "user_id", "customer_id", "order_id", "booking_id", "invoice_id",
    "card_number", "cvv", "upi_pin", "otp", "bank_password",
    "provider_credential", "api_key", "webhook_secret",
})


def _payment_field_key_for_update(
    raw_key: str,
    field_defs: dict[str, dict],
    properties: dict[str, Any],
) -> Optional[str]:
    """Map a payment extraction key to the canonical schema key, or None if not allowed."""
    key = str(raw_key).lower()
    # Absolute block: never apply these fields regardless of schema
    if key in _PAYMENT_BLOCKED_FIELDS:
        return None
    # Only safe canonical keys are permitted
    if key not in _PAYMENT_SAFE_CANONICAL_KEYS:
        return None
    # customer_name may be stored as "name" in some schemas
    if key == "customer_name":
        for candidate in ("customer_name", "name"):
            if candidate in properties or candidate in field_defs:
                return candidate
        return None
    return key if (key in properties or key in field_defs) else None


def _reminder_schedule_from_updates(
    context: ExecutionContext,
    updates: dict[str, Any],
    current: dict[str, Any],
) -> tuple[Optional[str], Optional[str]]:
    """Build a canonical local ISO reminder timestamp from semantic date/time fields."""
    date_value = updates.get("reminder_date")
    time_value = updates.get("reminder_time")
    if date_value in (None, "") and time_value in (None, ""):
        return None, None
    base_value = updates.get("scheduled_for") or current.get("scheduled_for") or current.get("schedule_at")
    try:
        if base_value not in (None, ""):
            base = datetime.fromisoformat(str(base_value).strip().replace("Z", "+00:00"))
            if date_value not in (None, ""):
                base = base.replace(year=datetime.fromisoformat(f"{date_value}T00:00").year,
                                    month=datetime.fromisoformat(f"{date_value}T00:00").month,
                                    day=datetime.fromisoformat(f"{date_value}T00:00").day)
            if time_value not in (None, ""):
                parsed_time = datetime.fromisoformat(f"1970-01-01T{time_value}").time()
                base = base.replace(hour=parsed_time.hour, minute=parsed_time.minute, second=parsed_time.second, microsecond=0)
            return base.isoformat(), None
        if date_value in (None, "") or time_value in (None, ""):
            return None, "scheduled_for"
        return datetime.fromisoformat(f"{date_value}T{time_value}").replace(microsecond=0).isoformat(), None
    except (TypeError, ValueError):
        return None, "scheduled_for"


def _is_future_reminder_schedule(context: ExecutionContext, value: Any) -> bool:
    try:
        scheduled_for = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    timezone_name = str(context.config.config_data.get("timezone") or "UTC")
    try:
        tenant_timezone = ZoneInfo(timezone_name)
    except Exception:
        tenant_timezone = timezone.utc
    if scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=tenant_timezone)
    return scheduled_for.astimezone(timezone.utc) > datetime.now(timezone.utc)


def _lead_clarification_response(
    context: ExecutionContext,
    tool_def: Any,
    *,
    reason_field: Optional[str] = None,
) -> WorkflowResult:
    missing = _module_missing_required_fields(tool_def, context, "lead_capture")
    target = reason_field or (missing[0] if missing else None)
    if target in {"name", "customer_name"}:
        text = "Please share your name so I can keep this request moving."
    elif target == "phone":
        text = "Please share a valid phone number, or an email address if that is better."
    elif target == "email":
        text = "Please share a valid email address, or a phone number if that is better."
    elif target == "interest":
        text = "What are you interested in?"
    elif target == "preferred_contact_time":
        text = "When would you prefer to be contacted?"
    else:
        text = "I kept your details. Please share the next detail for this request."
    focus_kwargs = _prompt_focus_result_kwargs(context, "lead_capture", [reason_field] if reason_field else missing, reason_field=reason_field)
    return WorkflowResult(
        response_text=text,
        next_state=context.session_data.state.workflow_state,
        **focus_kwargs,
    )


def _support_clarification_response(
    context: ExecutionContext,
    tool_def: Any,
    *,
    reason_field: Optional[str] = None,
) -> WorkflowResult:
    missing = _module_missing_required_fields(tool_def, context, "support")
    target = reason_field or (missing[0] if missing else None)
    if target in {"issue_description", "issue_summary", "support_reason"}:
        text = "I can help with that. Please describe the issue in a little more detail."
    elif target == "urgency":
        text = "Please choose the urgency for this support request: low, medium, or high."
    elif target == "phone":
        text = "Please share a valid phone number for the support team."
    elif target == "email":
        text = "Please share a valid email address for the support team."
    elif target in {"name", "customer_name"}:
        text = "Please share your name for the support request."
    else:
        text = "I kept your support details. Please share the next detail for this request."
    quick_replies = ["low", "medium", "high"] if target == "urgency" else []
    focus_kwargs = _prompt_focus_result_kwargs(context, "support", [reason_field] if reason_field else missing, reason_field=reason_field)
    return WorkflowResult(
        response_text=text,
        quick_replies=quick_replies,
        next_state=context.session_data.state.workflow_state,
        **focus_kwargs,
    )


def _reminder_clarification_response(
    context: ExecutionContext,
    tool_def: Any,
    *,
    reason_field: Optional[str] = None,
) -> WorkflowResult:
    missing = _module_missing_required_fields(tool_def, context, "reminders")
    target = reason_field or (missing[0] if missing else None)
    if target in {"scheduled_for", "schedule_at", "reminder_date", "reminder_time", "time_offset"}:
        prefix = "That reminder time has passed or is not available. " if _recent_module_field_error(context, target) else ""
        text = f"{prefix}Please share a valid future time for the reminder."
    elif target in {"reminder_message", "message_text", "message", "note"}:
        text = "What message would you like included in the reminder?"
    elif target == "channel":
        text = "Please choose a supported reminder delivery channel."
    elif target == "phone":
        text = "Please share a valid phone number for this reminder."
    elif target == "email":
        text = "Please share a valid email address for this reminder."
    else:
        text = "I kept the reminder details. Please share the next detail needed to schedule it."
    focus_kwargs = _prompt_focus_result_kwargs(context, "reminders", [reason_field] if reason_field else missing, reason_field=reason_field)
    return WorkflowResult(response_text=text, next_state=context.session_data.state.workflow_state, **focus_kwargs)


def _review_clarification_response(
    context: ExecutionContext,
    tool_def: Any,
    *,
    reason_field: Optional[str] = None,
) -> WorkflowResult:
    missing = _module_missing_required_fields(tool_def, context, "reviews")
    target = reason_field or (missing[0] if missing else None)
    if target == "rating":
        prefix = "That rating is not valid. " if _recent_module_field_error(context, target) else ""
        text = f"{prefix}Please share a rating from 1 to 5."
    elif target in {"feedback", "feedback_text"}:
        text = "Please share your feedback for this review."
    elif target == "phone":
        text = "Please share a valid phone number for this review."
    elif target == "email":
        text = "Please share a valid email address for this review."
    else:
        text = "I kept the review details. Please share the next detail needed to submit it."
    focus_kwargs = _prompt_focus_result_kwargs(context, "reviews", [reason_field] if reason_field else missing, reason_field=reason_field)
    return WorkflowResult(response_text=text, next_state=context.session_data.state.workflow_state, **focus_kwargs)


def _payment_clarification_response(
    context: ExecutionContext,
    tool_def: Any,
    *,
    reason_field: Optional[str] = None,
) -> WorkflowResult:
    missing = _module_missing_required_fields(tool_def, context, "payments")
    target = reason_field or (missing[0] if missing else None)
    if target == "amount":
        prefix = "That amount is not valid. " if _recent_module_field_error(context, target) else ""
        text = f"{prefix}Please share the payment amount."
    elif target == "currency":
        prefix = "That currency is not available. " if _recent_module_field_error(context, target) else ""
        text = f"{prefix}Please share the currency for this payment."
    elif target == "phone":
        text = "Please share a valid phone number."
    elif target == "email":
        text = "Please share a valid email address."
    elif target in {"order_reference", "booking_reference", "invoice_reference"}:
        text = "Please share the reference for this payment."
    else:
        text = "I kept the payment details. Please share the next detail needed to proceed."
    focus_kwargs = _prompt_focus_result_kwargs(context, "payments", [reason_field] if reason_field else missing, reason_field=reason_field)
    return WorkflowResult(response_text=text, next_state=context.session_data.state.workflow_state, **focus_kwargs)


def global_collection_owns_turn(context: ExecutionContext, workflow: str) -> bool:
    """Return True if the V2 global coordinator has authority over the turn."""
    from backend.logic.engine.workflow_field_policies import WORKFLOW_FIELD_POLICIES

    if workflow not in WORKFLOW_FIELD_POLICIES:
        return False

    state = context.session_data.state
    if state.action_ledger.pending_action:
        return False

    return True


class AgenticEngine:
    def __init__(self):
        from backend.modules import get_all_modules
        self.tool_registry = ToolRegistry()
        for module in get_all_modules():
            self.tool_registry.register_module(module)

    def _active_workflow_resume_response(self, context: ExecutionContext) -> WorkflowResult:
        """Ask for the one next backend-defined detail after a tangent or direct reply."""
        ledger = context.session_data.state.action_ledger
        if ledger.pending_action:
            return _pending_action_clarification(context)

        task_key = ledger.current_task or context.session_data.current_module
        response_builders = {
            "order": ("order.execute", _order_field_clarification_response),
            "booking": ("booking.execute", _booking_clarification_response),
            "lead_capture": ("lead_capture.execute", _lead_clarification_response),
            "support": ("support.execute", _support_clarification_response),
            "reminders": ("reminders.execute", _reminder_clarification_response),
            "reviews": ("reviews.execute", _review_clarification_response),
            "payments": ("payments.execute", _payment_clarification_response),
            "payment": ("payments.execute", _payment_clarification_response),
        }
        entry = response_builders.get(task_key or "")
        if not entry:
            return _active_workflow_clarification(context)
        tool_key, build_response = entry
        try:
            tool_def = self.tool_registry.get_tool(tool_key)
        except Exception:
            return _active_workflow_clarification(context)
        return build_response(context, tool_def)

    def _active_workflow_tangent_response(
        self,
        context: ExecutionContext,
        routing_decision,
    ) -> Optional[WorkflowResult]:
        resume_result = self._active_workflow_resume_response(context)
        customer_text = _normalized_customer_text(context)
        if _asks_for_service_list(customer_text):
            service_response = build_service_choices_response(context.config.config_data, booking_mode=False)
            return _preserve_active_workflow_direct_response(
                context,
                WorkflowResult(
                    response_text=service_response.text,
                    quick_replies=service_response.quick_replies,
                    next_state=context.session_data.state.workflow_state,
                ),
                routing_decision,
                resume_result=resume_result,
            )
        if _asks_for_current_status(customer_text):
            return _preserve_active_workflow_direct_response(
                context,
                _active_workflow_status_response(context),
                routing_decision,
                resume_result=resume_result,
            )
        direct_response = _direct_response_for_route(context, routing_decision)
        if direct_response:
            return _preserve_active_workflow_direct_response(
                context,
                direct_response,
                routing_decision,
                resume_result=resume_result,
            )
        # A factual question without a deterministic backend answer must
        # continue to bounded knowledge/RAG; opaque tangent evidence retains
        # the established safe resume response.
        text = str((context.incoming_message or {}).get("text") or "").strip().lower()
        if "?" in text or re.search(r"\b(?:how much|what (?:is|are|does)|price|cost)\b", text):
            return None
        return resume_result

    async def _execute_confirmed_pending_action(
        self,
        context: ExecutionContext,
        *,
        trusted_verified_continuation: bool = False,
        trusted_verification: Optional[OtpVerificationEvidence] = None,
    ) -> WorkflowResult:
        state = context.session_data.state
        ledger = state.action_ledger
        pending_action = ledger.pending_action
        if not pending_action or not pending_action.tool_key:
            return _pending_action_clarification(context)
        try:
            tool = self.tool_registry.get_tool(pending_action.tool_key)
        except Exception:
            logger.warning(
                "Pending action references unavailable tool",
                extra={"extra_data": {"tool_key": pending_action.tool_key}},
            )
            return _pending_action_clarification(context)

        if (
            pending_action.requires_verified_customer
            and str(ledger.channel or (context.incoming_message or {}).get("channel_type") or "").lower() == "awa_chat"
            and not trusted_verified_continuation
            and not has_required_assurance(
                context,
                pending_action.tool_key,
                requires_verified_customer=True,
            )
        ):
            return _pending_action_clarification(context)

        blocking = _blocking_canonical_violations(context, _PROTECTED_EXECUTION_BLOCKING_INVARIANTS)
        if blocking:
            _log_canonical_gate_block("protected_execution_invariant_blocked", blocking)
            return WorkflowResult(
                response_text="I couldn't safely complete that request yet. Please review or cancel it and try again.",
                next_state=state.workflow_state,
            )

        if pending_action.requires_confirmation and ledger.task_status not in {"confirmed", "executing"}:
            if not state.confirm_pending_action(pending_action.tool_key, pending_action.args):
                return _pending_action_clarification(context)

        execution_identity = protected_action_binding(
            tenant_id=context.tenant.id,
            conversation_id=context.session_data.conversation_id,
            channel_session_id=(
                trusted_verification.channel_session_id
                if trusted_verification is not None
                else str((context.incoming_message or {}).get("channel_type") or "non_public")
            ),
            config_version=context.session_data.config_version,
            action_nonce=pending_action.action_nonce,
            tool_key=pending_action.tool_key,
            args_hash=pending_action.args_hash or state.action_args_hash(pending_action.args),
        )
        execution_claim_key = (
            f"protected-action:execution:{context.tenant.id}:"
            f"{context.session_data.conversation_id}:{pending_action.action_nonce}:{execution_identity}"
        )
        try:
            claim_value = (
                trusted_verification.verification_ref
                if trusted_verification is not None
                else uuid4().hex
            )
            try:
                execution_claimed = await claim_protected_action_execution(
                    get_redis_client(),
                    claim_key=execution_claim_key,
                    claim_value=claim_value,
                    ttl_seconds=max(86400, settings.CUSTOMER_OTP_VERIFIED_TTL_SECONDS),
                )
            except RuntimeError:
                reset_redis_clients()
                execution_claimed = await claim_protected_action_execution(
                    get_redis_client(),
                    claim_key=execution_claim_key,
                    claim_value=claim_value,
                    ttl_seconds=max(86400, settings.CUSTOMER_OTP_VERIFIED_TTL_SECONDS),
                )
        except Exception as exc:
            logger.error(
                "Protected action execution claim unavailable",
                extra={"extra_data": {"tool_key": pending_action.tool_key, "error_type": type(exc).__name__}},
            )
            return WorkflowResult(
                response_text="I couldn't safely start that request yet. Please try again.",
                next_state=state.workflow_state,
            )
        if not execution_claimed:
            return WorkflowResult(
                response_text="Your request is already being processed or was already handled.",
                next_state=state.workflow_state,
            )

        if trusted_verification is not None:
            trusted_verification.consumed = True
            trusted_verification.consumption_ref = execution_identity
        ledger.task_status = "executing"

        logger.info(
            "Executing semantically confirmed pending action",
            extra={"extra_data": {"tool_key": pending_action.tool_key}},
        )
        module_name = pending_action.tool_key.split(".", 1)[0]
        state.update_task_step_data(module_name, pending_action.args)

        try:
            result = await self.tool_registry.execute_tool(pending_action.tool_key, context)
        except Exception as exc:
            logger.error(
                "Pending action execution failed",
                extra={"extra_data": {"tool_key": pending_action.tool_key, "error_type": type(exc).__name__}},
                exc_info=True,
            )
            result = ToolExecutionResult(
                tool_key=pending_action.tool_key,
                success=False,
                denied_reason="tool_failed",
                user_message="I couldn't complete that action right now.",
            )

        channel_type = (context.incoming_message or {}).get("channel_type")
        state.record_tool_result(
            result,
            channel_type=channel_type,
            action_nonce=pending_action.action_nonce,
            args_hash=pending_action.args_hash,
        )
        # The one-time continuation is consumed above, while its bounded
        # phone-possession evidence remains available for later protected
        # actions in this same browser session.
        ledger.verification.otp_verified_actions = []
        if result.success is True:
            state.action_ledger.pending_action = None
        elif state.action_ledger.pending_action is not None:
            # A dispatched failure consumes this incarnation. A later explicit
            # retry receives a new nonce and therefore cannot reuse its claim.
            state.action_ledger.pending_action.action_nonce = uuid4().hex

        tool_call_id = f"call_{uuid4().hex[:16]}"
        state.agentic_history = (state.agentic_history or []) + [
            {
                "type": "ai",
                "data": {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": tool.llm_name,
                            "args": pending_action.args,
                            "id": tool_call_id,
                            "type": "tool_call",
                        }
                    ],
                },
            },
            {
                "type": "tool",
                "data": {
                    "content": result.to_tool_message(),
                    "tool_call_id": tool_call_id,
                    "name": tool.llm_name,
                },
            },
        ]
        return WorkflowResult(
            response_text=result.user_message or "I couldn't complete that action right now.",
            next_state=state.workflow_state,
        )

    def _apply_safe_pending_field_updates(
        self,
        context: ExecutionContext,
        decision: PendingActionDecision,
    ) -> bool:
        blocking = _blocking_canonical_violations(context, _FIELD_MUTATION_BLOCKING_INVARIANTS)
        if blocking:
            _log_canonical_gate_block("active_field_mutation_invariant_blocked", blocking)
            return False
        updates = decision.field_updates
        if not updates:
            return False
        pending = context.session_data.state.action_ledger.pending_action
        if not pending or not pending.tool_key:
            return False
        try:
            tool = self.tool_registry.get_tool(pending.tool_key)
            schema = ToolRegistry._strict_tool_schema(tool.module.get_tool_schema(context.config.config_data))
        except Exception:
            return False
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        if not properties:
            return False
        safe_updates: dict[str, Any] = {}
        for key, value in updates.items():
            key = str(key)
            if key not in properties:
                continue
            if isinstance(value, (dict, list)):
                continue
            safe_updates[key] = value
        if not safe_updates:
            return False
        module_name = pending.tool_key.split(".", 1)[0]
        new_args = dict(pending.args or {})
        new_args.update(safe_updates)
        context.session_data.state.update_task_step_data(module_name, new_args)
        context.session_data.state.prepare_pending_action(
            tool_key=pending.tool_key,
            args=new_args,
            summary=pending.summary,
            requires_confirmation=pending.requires_confirmation,
            requires_verified_customer=pending.requires_verified_customer,
            verification_action=pending.verification_action,
            channel_type=(context.incoming_message or {}).get("channel_type"),
        )
        verification = context.session_data.state.action_ledger.verification
        verification.customer_verified = False
        verification.phone_verified = False
        verification.otp_verified_actions = []
        verification.otp_verifications = []
        return True

    def _apply_safe_order_field_updates(
        self,
        context: ExecutionContext,
        extraction: OrderFieldExtraction,
        tool_def: Any,
    ) -> tuple[bool, Optional[str]]:
        if global_collection_owns_turn(context, "order") or _field_mutation_is_blocked(context):
            return False, None
        updates = extraction.field_updates
        if not isinstance(updates, dict) or not updates:
            return False, None

        schema = ToolRegistry._strict_tool_schema(tool_def.module.get_tool_schema(context.config.config_data))
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        order_cfg = _order_config(context)
        allowed_fields = {
            "item_name",
            "item_sku",
            "items",
            "quantity",
            "fulfillment_type",
            "customer_name",
            "phone",
            "delivery_address",
            "notes",
        }
        if order_cfg.get("payment_methods") and "payment_method" in properties:
            allowed_fields.add("payment_method")

        field_defs = _order_field_defs(tool_def, context)
        current = context.session_data.state.get_task_step_data("order")
        safe_updates: dict[str, Any] = {}
        changed = False

        for raw_key, raw_value in updates.items():
            key = str(raw_key)
            if key not in allowed_fields or key not in properties:
                return False, key
            if raw_value in (None, ""):
                return False, key

            if key == "items":
                if not isinstance(raw_value, list) or not raw_value:
                    return False, key
                normalized_items: list[dict[str, Any]] = []
                for raw_item in raw_value:
                    if not isinstance(raw_item, dict):
                        return False, key
                    name = raw_item.get("item_name") or raw_item.get("name")
                    sku = raw_item.get("item_sku") or raw_item.get("sku")
                    if not isinstance(name or sku, str) or not str(name or sku).strip():
                        return False, key
                    try:
                        quantity = int(float(str(raw_item.get("quantity", 1))))
                    except (TypeError, ValueError):
                        return False, key
                    if quantity < 1 or quantity > 99:
                        return False, key
                    normalized_item = {"quantity": quantity}
                    if name:
                        normalized_item["item_name"] = str(name).strip()
                    if sku:
                        normalized_item["item_sku"] = str(sku).strip()
                    if raw_item.get("notes"):
                        normalized_item["notes"] = str(raw_item["notes"]).strip()
                    normalized_items.append(normalized_item)
                raw_value = normalized_items
            elif isinstance(raw_value, (dict, list)):
                return False, key

            field_confidence = extraction.field_confidence.get(key, extraction.confidence)
            if field_confidence < ORDER_FIELD_CONFIDENCE_THRESHOLD:
                continue

            value = raw_value
            if key == "fulfillment_type":
                resolved = tool_def.module.resolve_fulfillment_type(value, context.config.config_data)
                if not resolved:
                    return False, "fulfillment_type"
                value = resolved
            elif key == "quantity":
                try:
                    value = int(float(str(value)))
                except (TypeError, ValueError):
                    return False, "quantity"

            field_def = field_defs.get(key)
            if field_def:
                validation = tool_def.module.validate_input(field_def, value, tenant_config=context.config.config_data)
                if not validation.is_valid:
                    return False, key

            existing = current.get(key)
            if existing not in (None, "") and str(existing) != str(value):
                can_overwrite = (
                    extraction.workflow_action == "change"
                    or _recent_order_field_error(context, key)
                    or _existing_order_field_invalid(tool_def, context, key, existing)
                )
                if not can_overwrite:
                    continue

            safe_updates[key] = value
            if str(current.get(key, "")) != str(value):
                changed = True

        if not safe_updates:
            return False, None
        # Keep the legacy required scalar populated for strict tool validation;
        # execution prefers the complete cart in ``items``.
        if "items" in safe_updates and current.get("item_name") in (None, ""):
            first_item = safe_updates["items"][0]
            safe_updates["item_name"] = first_item.get("item_name") or first_item.get("item_sku")
            safe_updates["quantity"] = first_item["quantity"]
        if changed:
            merged = dict(current)
            merged.update(safe_updates)
            context.session_data.state.update_task_step_data("order", merged)
        return changed, None

    def _order_tangent_response(self, context: ExecutionContext, routing_decision) -> WorkflowResult:
        return self._active_workflow_tangent_response(context, routing_decision)

    async def _handle_semantic_order_field_extraction(
        self,
        context: ExecutionContext,
        routing_decision,
    ) -> Optional[WorkflowResult]:
        if not _has_active_order_scope(context):
            return None
        if not _order_module_enabled(context):
            return None
        if _has_pending_order_disambiguation(context):
            return None

        try:
            tool_def = self.tool_registry.get_tool("order.execute")
        except Exception:
            return None
        if not tool_def.module:
            return None

        try:
            extraction = (
                context.shadow_turn_evidence["field_extraction"]
                if _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE
                and "field_extraction" in context.shadow_turn_evidence
                else await extract_order_fields(context)
            )
        except Exception:
            _record_shadow_evidence(context, "semantic_failure", "semantic_provider_failed")
            return _order_field_clarification_response(context, tool_def)
        _record_shadow_evidence(context, "field_extraction", extraction)
        note_detected_customer_language(context, extraction.detected_language, confidence=extraction.confidence)

        if (
            _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE
            and context.turn_plan is not None
            and context.turn_plan.tangent_requested
            and ("?" in user_text if (user_text := str((context.incoming_message or {}).get("text") or "")) else False)
        ):
            return None

        if (
            extraction.workflow != "order"
            or extraction.confidence < ORDER_FIELD_EXTRACTION_CONFIDENCE_THRESHOLD
            or extraction.workflow_action in {"unknown", "switch", "clarify"}
        ):
            return _order_field_clarification_response(context, tool_def)

        if extraction.workflow_action == "cancel":
            _cancel_active_task(context)
            return WorkflowResult(
                response_text="Okay, I've cancelled this order request. What would you like to do next?",
                next_state=ConversationState.CANCELLED,
            )

        if extraction.workflow_action == "tangent":
            return self._order_tangent_response(context, routing_decision)

        applied, invalid_field = self._apply_safe_order_field_updates(context, extraction, tool_def)
        if invalid_field:
            return _order_field_clarification_response(context, tool_def, reason_field=invalid_field)
        if extraction.clarification_needed and not applied:
            fields = extraction.clarification_fields
            return _order_field_clarification_response(
                context,
                tool_def,
                reason_field=str(fields[0]) if fields else None,
            )
        if (
            extraction.workflow_action in {"continue", "change"}
            and not _order_missing_required_fields(tool_def, context)
            and context.session_data.state.action_ledger.pending_action is None
            and context.session_data.state.action_ledger.task_status
            in {"collecting", "failed", "blocked"}
        ):
            result = await self.tool_registry.execute_tool("order.execute", context)
            channel_type = (context.incoming_message or {}).get("channel_type")
            context.session_data.state.record_tool_result(result, channel_type=channel_type)
            logger.info(
                "Deterministic complete order preparation",
                extra={"extra_data": {
                    "tool_key": "order.execute",
                    "success": result.success,
                    "denied_reason": result.denied_reason,
                    "conversation_id": str(context.session_data.conversation_id),
                }},
            )
            if context.session_data.state.action_ledger.pending_action is not None:
                return _pending_action_clarification(context)
        return None

    def _apply_safe_booking_field_updates(
        self,
        context: ExecutionContext,
        extraction: BookingFieldExtraction,
        tool_def: Any,
    ) -> tuple[bool, Optional[str]]:
        if global_collection_owns_turn(context, "booking") or _field_mutation_is_blocked(context):
            return False, None
        updates = extraction.field_updates
        if not isinstance(updates, dict) or not updates:
            return False, None

        schema = ToolRegistry._strict_tool_schema(tool_def.module.get_tool_schema(context.config.config_data))
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        field_defs = _booking_field_defs(tool_def, context)
        allowed_canonical = {
            "service_name",
            "booking_date",
            "booking_time",
            "preferred_date",
            "preferred_time",
            "customer_name",
            "phone",
            "email",
            "notes",
        }
        # Tenant configuration owns collection keys. Canonical booking names
        # are semantic aliases only; exact configured keys are valid updates.
        allowed_canonical.update(field_defs)
        if "special_requests" in properties or "special_requests" in field_defs:
            allowed_canonical.add("special_requests")

        current = context.session_data.state.get_task_step_data("booking")
        safe_updates: dict[str, Any] = {}
        changed = False

        for raw_key, raw_value in updates.items():
            canonical_key = str(raw_key)
            if canonical_key not in allowed_canonical:
                return False, canonical_key
            key = _booking_field_key_for_update(canonical_key, field_defs, properties)
            if not key or key not in properties:
                return False, canonical_key
            if raw_value in (None, "") or isinstance(raw_value, (dict, list)):
                return False, canonical_key

            field_confidence = extraction.field_confidence.get(canonical_key, extraction.field_confidence.get(key, extraction.confidence))
            if field_confidence < BOOKING_FIELD_CONFIDENCE_THRESHOLD:
                continue

            value = raw_value
            field_def = field_defs.get(key)
            if field_def:
                validation = tool_def.module.validate_input(field_def, value, tenant_config=context.config.config_data)
                if not validation.is_valid:
                    return False, canonical_key
                value = tool_def.module.normalize_configured_field_value(
                    field_def,
                    value,
                    tenant_config=context.config.config_data,
                )

            existing = current.get(key)
            if existing not in (None, "") and str(existing) != str(value):
                can_overwrite = (
                    extraction.workflow_action == "change"
                    or _recent_booking_field_error(context, key)
                    or _existing_booking_field_invalid(tool_def, context, key, existing)
                )
                if not can_overwrite:
                    continue

            safe_updates[key] = value
            if str(current.get(key, "")) != str(value):
                changed = True

        if not safe_updates:
            return False, None

        merged = dict(current)
        merged.update(safe_updates)
        original_step_data = dict(context.session_data.state.step_data or {})
        original_scoped = dict(context.session_data.state.scoped_step_data.get("booking") or {})
        context.session_data.state.update_task_step_data("booking", merged)
        payload_error = tool_def.module.validate_payload(context)
        if payload_error:
            if original_scoped:
                context.session_data.state.scoped_step_data["booking"] = original_scoped
            elif "booking" in context.session_data.state.scoped_step_data:
                context.session_data.state.scoped_step_data.pop("booking", None)
            context.session_data.state.step_data = original_step_data
            invalid = "booking_time" if "time" in payload_error.lower() else "booking_date"
            return False, invalid
        return changed, None

    def _booking_tangent_response(self, context: ExecutionContext, routing_decision) -> WorkflowResult:
        return self._active_workflow_tangent_response(context, routing_decision)

    async def _handle_global_collection(
        self,
        context: ExecutionContext,
        routing_decision: Any = None,
    ) -> Optional[WorkflowResult]:
        active_workflow = _active_workflow_key(context)
        if not global_collection_owns_turn(context, active_workflow):
            return None

        from backend.logic.engine.configured_field_collection import collect_global

        result = await collect_global(context, workflow=active_workflow)
        if not result.handled:
            return None

        if getattr(result, "control_disposition", "") == "cancel":
            _cancel_active_task(context)
            return WorkflowResult(
                response_text="Okay, I've cancelled this request. What would you like to do next?",
                next_state=ConversationState.CANCELLED,
            )

        # In this minimal integration, we let the existing pipeline (via returning None)
        # proceed to execute the active task or return clarification if needed.
        return None

    async def _try_authoritative_booking_apply(
        self,
        context: ExecutionContext,
    ) -> Optional[WorkflowResult]:
        """Iteration 5 authoritative multi-field booking continuation.

        Eligible only when both platform flags are enabled, the booking is an
        active customer-authored continuation with a service already selected,
        at least one configured field remains missing, and no protected action
        or confirmation/OTP continuation is pending. The candidate batch must
        have passed Iteration 4 deterministic validation.

        Returns a WorkflowResult when it applied at least one field, otherwise
        None so the existing focused collector runs unchanged.
        """
        if not _authoritative_booking_apply_enabled():
            return None
        # Allow the already-satisfied replay path even when every required field
        # is complete; apply_external_candidates decides write vs. no-write and
        # returns a handled result either way.
        if not _authoritative_booking_apply_eligible(context, require_missing=False):
            return None

        snapshot = context.shadow_turn_evidence.get(SHADOW_EVIDENCE_KEY)
        if not isinstance(snapshot, dict):
            return None
        validated_patch = snapshot.get("validated_shadow_patch")
        if not isinstance(validated_patch, dict) or not validated_patch:
            return None

        focus_before = _focus_field_key(context)

        booking_before = dict(context.session_data.state.get_task_step_data("booking") or {})
        try:
            applied = configured_field_collection_coordinator.apply_external_candidates(
                context, validated_patch
            )
        except Exception as exc:  # pragma: no cover - defensive
            _emit_failure_event(context, stage="authoritative_booking_apply", error_type=type(exc).__name__)
            # An uncertain write (state already mutated before the error) must
            # NOT fall back to the old collector; the outer failure handler owns
            # it. Only a pre-update exception falls back to the existing collector.
            booking_after = dict(context.session_data.state.get_task_step_data("booking") or {})
            if booking_after != booking_before:
                raise
            return None

        # Outcome C: nothing safely applicable -> the existing focused collector
        # owns the turn. This is the ONLY branch that returns None so that
        # collect() may run.
        if not applied.handled:
            return None

        # Outcomes A and B are both "handled": this path now owns conversational
        # mutation for the turn. The legacy collector and a second semantic
        # extraction must never run for this same customer message, whether a
        # write occurred (applied) or the batch was already satisfied.
        try:
            tool_def = self.tool_registry.get_tool("booking.execute")
        except Exception:
            tool_def = None

        if applied.update_performed:
            focus_after = _focus_field_key(context)
            try:
                _emit_booking_applied_event(
                    context,
                    config_version=getattr(context.session_data, "config_version", None),
                    channel=str((context.incoming_message or {}).get("channel_type") or "unknown").strip().lower(),
                    applied_field_keys=list(applied.accepted_field_keys),
                    remaining_required_field_keys=_booking_missing_required_fields(tool_def, context) if tool_def else [],
                    focus_field_key_before=focus_before,
                    focus_field_key_after=focus_after,
                )
            except Exception:
                # Telemetry failure must never invalidate the authoritative result.
                pass

            # Retain the established in-memory extraction evidence shape so the
            # existing canonical-control observability sees exactly one patch.
            # Only emit evidence when a write actually happened; an idempotent
            # already-satisfied replay performs no interpretation at all. The
            # values come from authoritative persisted state (already validated),
            # never from the raw utterance.
            booking_state = context.session_data.state.get_task_step_data("booking")
            applied_updates = {
                key: booking_state.get(key)
                for key in applied.accepted_field_keys
                if key in booking_state
            }
            context.shadow_turn_evidence["field_extraction"] = BookingFieldExtraction(
                workflow="booking",
                workflow_action="continue",
                confidence=1.0,
                field_updates=applied_updates,
                field_confidence={key: 1.0 for key in applied.accepted_field_keys},
                clarification_needed=False,
            )

        # State-based continuation (Sections 7 and 8). Whether we wrote new
        # values or the batch was already satisfied, the next response is
        # derived entirely from the authoritative updated booking state -- never
        # from the raw utterance and never by calling collect().
        return await self._continue_from_authoritative_booking_state(context, tool_def)

    async def _continue_from_authoritative_booking_state(
        self,
        context: ExecutionContext,
        tool_def: Any,
    ) -> "WorkflowResult":
        """Derive the next response from authoritative booking state only.

        Ask for the first genuinely missing required field, or -- when every
        required field is complete -- continue to the same existing
        post-collection proposal/confirmation path that the focused collector
        uses. This never calls collect() and never claims booking success.
        """
        if tool_def is None:
            missing = [
                item.key for item in configured_field_collection_coordinator.resolve_fields(context)
                if item.required and context.session_data.state.get_task_step_data("booking").get(item.key) in (None, "")
            ]
            target = missing[0] if missing else None
            focus_kwargs = _prompt_focus_result_kwargs(
                context, "booking", [target] if target else [], reason_field=None
            )
            return WorkflowResult(
                response_text="I kept your booking details. Please share the next booking detail you want to provide.",
                next_state=context.session_data.state.workflow_state,
                **focus_kwargs,
            )
        remaining = _booking_missing_required_fields(tool_def, context)
        if remaining:
            # Fields still missing: ask only for the first genuinely missing
            # field, computed from authoritative state (not the utterance).
            return _booking_clarification_response(context, tool_def)
        # Every required configured field is complete. Route to the SAME
        # existing post-collection proposal/confirmation path the focused
        # collector uses: booking.execute prepares the proposal and sets the
        # confirmation requirement; it does not create a booking record here.
        # This is not a direct success -- confirmation is still required.
        prepared = await self.tool_registry.execute_tool("booking.execute", context)
        channel_type = (context.incoming_message or {}).get("channel_type")
        context.session_data.state.record_tool_result(prepared, channel_type=channel_type)
        if context.session_data.state.action_ledger.pending_action is not None:
            return _pending_action_clarification(context)
        return _booking_clarification_response(context, tool_def)

    async def _try_authoritative_order_configured_turn_apply(
        self,
        context: ExecutionContext,
    ) -> Optional[WorkflowResult]:
        """Iteration 6C authoritative multi-field order continuation.

        Eligible only when both authoritative order flags are enabled, the order
        is an active customer-authored continuation with a customer-facing
        item_name, no pending disambiguation, no pending protected action, no
        pending confirmation/OTP/customer-assurance continuation, and a valid
        Iteration 6B shadow snapshot whose semantic result is usable. The
        candidate batch must have passed Iteration 6B deterministic validation.

        Returns a WorkflowResult when it handled the turn (applied or
        already-satisfied), otherwise None so the existing order semantic
        extraction and prefill run unchanged.
        """
        if not _authoritative_order_apply_eligible(context):
            return None

        snapshot = context.shadow_turn_evidence.get(ORDER_SHADOW_EVIDENCE_KEY)
        if not isinstance(snapshot, dict):
            return None
        validated = snapshot.get("validated_result") or {}
        validated_patch = validated.get("accepted_patch")
        # Build the complete candidate map from the usable semantic result so
        # the apply method can revalidate at application time. This is required
        # for the already-satisfied replay (Outcome B) where the validated patch
        # is empty because every candidate key is already collected.
        candidate_map = _order_shadow_candidate_map(snapshot)
        if not isinstance(validated_patch, dict):
            return None
        if not validated_patch and not candidate_map:
            return None

        focus_before = _order_focus_field_key(context)

        order_before = dict(context.session_data.state.get_task_step_data("order") or {})
        try:
            applied = order_module.apply_external_configured_candidates(
                context, candidate_map if candidate_map else validated_patch
            )
        except Exception as exc:  # pragma: no cover - defensive
            _emit_order_apply_failure_event(
                context, stage="authoritative_order_apply", error_type=type(exc).__name__
            )
            # Outcome D: an uncertain write (state already mutated before the
            # error) must NOT fall back to the legacy extractor; the outer
            # failure handler owns it. Only a pre-update exception falls back.
            order_after = dict(context.session_data.state.get_task_step_data("order") or {})
            if order_after != order_before:
                raise
            return None

        # Outcome C: nothing safely applicable -> the existing legacy extractor
        # and prefill own the turn. This is the ONLY branch that returns None so
        # extract_order_fields / prefill_from_message may run.
        if not applied.handled:
            return None

        # Outcomes A and B are both "handled": this path owns conversational
        # mutation for the turn. The legacy semantic extractor and a second
        # order-scoped update must never run for this same customer message,
        # whether a write occurred (applied) or the batch was already satisfied.
        try:
            tool_def = self.tool_registry.get_tool("order.execute")
        except Exception:
            tool_def = None

        if applied.update_performed:
            focus_after = _order_focus_field_key(context)
            try:
                _emit_order_applied_event(
                    context,
                    config_version=getattr(context.session_data, "config_version", None),
                    channel=str((context.incoming_message or {}).get("channel_type") or "unknown").strip().lower(),
                    applied_field_keys=list(applied.accepted_field_keys),
                    remaining_required_field_keys=_order_missing_required_fields(tool_def, context) if tool_def else [],
                    focus_field_key_before=focus_before,
                    focus_field_key_after=focus_after,
                )
            except Exception:
                # Telemetry failure must never invalidate the authoritative result.
                pass

        # State-based continuation. Whether we wrote new values or the batch was
        # already satisfied, the next response is derived entirely from the
        # authoritative updated order state -- never from the raw utterance and
        # never by calling extract_order_fields / prefill_from_message again.
        return await self._continue_from_authoritative_order_state(context, tool_def)

    async def _continue_from_authoritative_order_state(
        self,
        context: ExecutionContext,
        tool_def: Any,
    ) -> "WorkflowResult":
        """Derive the next response from authoritative order state only.

        Ask for the first genuinely missing required field, or -- when every
        required field is complete -- continue to the same existing
        post-collection proposal/confirmation path that the legacy extractor
        uses. This never calls extract_order_fields and never claims order
        success.
        """
        if tool_def is None or not getattr(tool_def, "module", None):
            remaining = _order_missing_required_fields_from_defs(context)
            target = remaining[0] if remaining else None
            focus_kwargs = _prompt_focus_result_kwargs(
                context, "order", [target] if target else [], reason_field=None
            )
            return WorkflowResult(
                response_text="I kept your order details. Please share the next order detail you want to provide.",
                next_state=context.session_data.state.workflow_state,
                **focus_kwargs,
            )
        remaining = _order_missing_required_fields(tool_def, context)
        if remaining:
            # Fields still missing: ask only for the first genuinely missing
            # field, computed from authoritative state (not the utterance).
            return _order_field_clarification_response(context, tool_def)
        # Every required configured field is complete. Route to the SAME
        # existing post-collection proposal/confirmation path the legacy
        # extractor uses: order.execute prepares the proposal and sets the
        # confirmation requirement; it does not create an order record here.
        # This is not a direct success -- confirmation is still required and
        # customer assurance remains authoritative.
        prepared = await self.tool_registry.execute_tool("order.execute", context)
        channel_type = (context.incoming_message or {}).get("channel_type")
        context.session_data.state.record_tool_result(prepared, channel_type=channel_type)
        if context.session_data.state.action_ledger.pending_action is not None:
            return _pending_action_clarification(context)
        return _order_field_clarification_response(context, tool_def)

    async def _handle_configured_booking_collection(
        self,
        context: ExecutionContext,
        routing_decision: Any = None,
    ) -> Optional[WorkflowResult]:
        """Apply the booking coordinator before canonical resume/general control.

        Explicit cancel is the only control that preempts a focused candidate
        here.  A switch/tangent/general classification is evaluated only when
        the coordinator has no candidate for the one focused configured field.
        """
        # Canonical-state violations must block this new authority just as they
        # block the legacy booking mutation path.
        if _field_mutation_is_blocked(context):
            return None
        # Explicit workflow switches and grounded tangents are controls, not
        # free-text candidates for a pending string field.  They retain their
        # established control precedence; a normal focused value still reaches
        # the coordinator before canonical resume/general classification.
        if (
            routing_decision is not None
            and (
                getattr(routing_decision, "should_clear_active_task", False)
                or getattr(routing_decision, "is_tangent", False)
                or (
                    getattr(routing_decision, "intent", None) == "faq"
                    and getattr(routing_decision, "route_type", None) == "direct_answer"
                )
            )
        ):
            return None
        # Iteration 5 authoritative multi-field booking continuation. When both
        # platform flags are enabled and the configured-turn shadow already
        # produced a backend-validated candidate batch, apply every accepted
        # field atomically and continue through the existing post-collection
        # flow. This path owns mutation for the turn; the focused collector is
        # skipped so the same message cannot be stored twice.
        authoritative_result = await self._try_authoritative_booking_apply(context)
        if authoritative_result is not None:
            return authoritative_result
        result = await configured_field_collection_coordinator.collect(context, semantic_extractor=extract_booking_fields)
        if not result.handled:
            return None
        if result.control_disposition == "cancel":
            _cancel_active_task(context)
            return WorkflowResult(
                response_text="Okay, I've cancelled this booking request. What would you like to do next?",
                next_state=ConversationState.CANCELLED,
            )
        try:
            tool_def = self.tool_registry.get_tool("booking.execute")
        except Exception:
            return None
        if not tool_def.module:
            return None
        if not result.accepted:
            return _booking_clarification_response(
                context,
                tool_def,
                reason_field=result.focused_field.key if result.focused_field else None,
            )
        # Retain the established in-memory extraction evidence shape for
        # canonical-control observability.  The coordinator remains the sole
        # authority that produced its exact-key patch.
        context.shadow_turn_evidence["field_extraction"] = BookingFieldExtraction(
            workflow="booking",
            workflow_action="continue",
            confidence=1.0,
            field_updates=dict(result.patch),
            field_confidence={key: 1.0 for key in result.patch},
            clarification_needed=False,
        )
        if result.ready_for_confirmation:
            prepared = await self.tool_registry.execute_tool("booking.execute", context)
            channel_type = (context.incoming_message or {}).get("channel_type")
            context.session_data.state.record_tool_result(prepared, channel_type=channel_type)
            if context.session_data.state.action_ledger.pending_action is not None:
                return _pending_action_clarification(context)
        return _booking_clarification_response(context, tool_def)

    async def _handle_booking_service_selection(
        self,
        context: ExecutionContext,
        routing_decision: Any = None,
    ) -> Optional[WorkflowResult]:
        """Resolve the booking-domain service before custom-field collection."""
        if not _has_active_booking_scope(context) or not _booking_module_enabled(context):
            return None
        if _field_mutation_is_blocked(context):
            return None
        # Preserve the established semantic grouped-input path for legacy
        # tenants that still declare a service field in their booking field
        # list.  Corrected configurations omit that domain prerequisite from
        # custom fields, so they take the authoritative handoff below.
        configured_fields = _booking_config(context).get("fields") or []
        legacy_service_field_config = any(
            isinstance(field, Mapping) and str(field.get("key") or "") in {"service_name", "service_id"}
            for field in configured_fields
        )
        current = context.session_data.state.get_task_step_data("booking")
        existing_name = str(current.get("service_name") or "").strip()
        existing = resolve_configured_option(
            customer_text=existing_name,
            active_workflow="booking",
            requested_fields=("service_name",),
            config_data=context.config.config_data,
            runtime_available_modules={"booking"},
        ) if existing_name else None
        if existing is not None:
            return None
        # Cancel must work while service is unresolved.  Switches and supported
        # tangents retain canonical control authority below this handoff.
        text = str((context.incoming_message or {}).get("text") or "").strip()
        if re.match(r"^\s*(?:cancel|stop|never mind|nevermind)\b", text, re.I):
            _cancel_active_task(context)
            return WorkflowResult(
                response_text="Okay, I've cancelled this booking request. What would you like to do next?",
                next_state=ConversationState.CANCELLED,
            )
        if routing_decision is not None and (
            getattr(routing_decision, "should_clear_active_task", False)
            or getattr(routing_decision, "is_tangent", False)
            or (
                getattr(routing_decision, "intent", None) == "faq"
                and getattr(routing_decision, "route_type", None) == "direct_answer"
            )
        ):
            return None
        selected = resolve_configured_option(
            customer_text=text,
            active_workflow="booking",
            requested_fields=("service_name",),
            config_data=context.config.config_data,
            runtime_available_modules={"booking"},
        )
        rich_service_patch = (
            _configured_booking_service_mention(context, text)
            if selected is None else None
        )
        # Legacy configurations retain their established semantic grouped-input
        # fallback for non-exact service prose.  An exact configured label is
        # still resolved here so it cannot reach that generic extractor.
        if selected is None and rich_service_patch is None and legacy_service_field_config:
            return None
        try:
            tool_def = self.tool_registry.get_tool("booking.execute")
        except Exception:
            return None
        if selected is None and rich_service_patch is None:
            # Invalid, ambiguous, disabled, and deleted labels do not touch
            # custom state and are clarified by the existing service UI path.
            return _booking_clarification_response(context, tool_def, reason_field="service_name")
        patch = rich_service_patch or {
            key: value for key, value in selected.field_updates.items()
            if key in {"service_name", "service_id"}
        }
        if not patch.get("service_name"):
            return _booking_clarification_response(context, tool_def, reason_field="service_name")
        # One transition: persist only authoritative service identity/name.
        context.session_data.state.update_task_step_data("booking", patch)
        context.shadow_turn_evidence["field_extraction"] = BookingFieldExtraction(
            workflow="booking", workflow_action="continue", confidence=1.0,
            field_updates=dict(patch), field_confidence={key: 1.0 for key in patch},
            clarification_needed=False,
        )
        if rich_service_patch is not None:
            # The service was resolved only from the tenant configuration.
            # Keep that identity, then collect the independent date/time and
            # contact values from this same rich customer turn.
            return await self._handle_configured_booking_collection(context, routing_decision)
        return _booking_clarification_response(context, tool_def)

    async def _handle_semantic_booking_field_extraction(
        self,
        context: ExecutionContext,
        routing_decision,
    ) -> Optional[WorkflowResult]:
        if not _has_active_booking_scope(context):
            return None
        if (
            getattr(routing_decision, "intent", None) == "faq"
            and getattr(routing_decision, "route_type", None) == "direct_answer"
        ):
            return None
        # A short answer can be a configured booking field (for example a
        # patient name).  It must reach the bounded extractor and the
        # configuration-aware merge path; skipping it leaves prompt focus
        # stale after an FAQ tangent/Continue cycle.
        if not _booking_module_enabled(context):
            return None

        try:
            tool_def = self.tool_registry.get_tool("booking.execute")
        except Exception:
            return None
        if not tool_def.module:
            return None

        try:
            extraction = (
                context.shadow_turn_evidence["field_extraction"]
                if _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE
                and "field_extraction" in context.shadow_turn_evidence
                else await extract_booking_fields(context)
            )
        except Exception:
            _record_shadow_evidence(context, "semantic_failure", "semantic_provider_failed")
            if (
                _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE
                and context.turn_plan is not None
                and context.turn_plan.tangent_requested
                and "?" in str((context.incoming_message or {}).get("text") or "")
            ):
                return None
            return _booking_clarification_response(context, tool_def)
        _record_shadow_evidence(context, "field_extraction", extraction)
        note_detected_customer_language(context, extraction.detected_language, confidence=extraction.confidence)

        if (
            _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE
            and context.turn_plan is not None
            and context.turn_plan.tangent_requested
            and "?" in str((context.incoming_message or {}).get("text") or "")
        ):
            return None

        if (
            getattr(routing_decision, "should_clear_active_task", False)
            and (
                extraction.workflow != "booking"
                or extraction.workflow_action in {"unknown", "switch", "clarify", "tangent"}
            )
        ):
            return None

        if (
            extraction.workflow != "booking"
            or extraction.confidence < BOOKING_FIELD_EXTRACTION_CONFIDENCE_THRESHOLD
            or extraction.workflow_action in {"unknown", "clarify"}
        ):
            return _booking_clarification_response(context, tool_def)

        if extraction.workflow_action == "switch":
            return None

        if extraction.workflow_action == "cancel":
            _cancel_active_task(context)
            return WorkflowResult(
                response_text="Okay, I've cancelled this booking request. What would you like to do next?",
                next_state=ConversationState.CANCELLED,
            )

        if extraction.workflow_action == "tangent":
            return self._booking_tangent_response(context, routing_decision)

        applied, invalid_field = self._apply_safe_booking_field_updates(context, extraction, tool_def)
        if invalid_field:
            return _booking_clarification_response(context, tool_def, reason_field=invalid_field)
        if extraction.clarification_needed and not applied:
            fields = extraction.clarification_fields
            return _booking_clarification_response(
                context,
                tool_def,
                reason_field=str(fields[0]) if fields else None,
            )
        if (
            extraction.workflow_action in {"continue", "change"}
            and not _booking_missing_required_fields(tool_def, context)
            and context.session_data.state.action_ledger.pending_action is None
            and context.session_data.state.action_ledger.task_status
            in {"collecting", "failed", "blocked"}
        ):
            result = await self.tool_registry.execute_tool("booking.execute", context)
            channel_type = (context.incoming_message or {}).get("channel_type")
            context.session_data.state.record_tool_result(result, channel_type=channel_type)
            logger.info(
                "Deterministic complete booking preparation",
                extra={"extra_data": {
                    "tool_key": "booking.execute",
                    "success": result.success,
                    "denied_reason": result.denied_reason,
                    "conversation_id": str(context.session_data.conversation_id),
                }},
            )
            if context.session_data.state.action_ledger.pending_action is not None:
                return _pending_action_clarification(context)
        return None

    def _apply_safe_lead_field_updates(
        self,
        context: ExecutionContext,
        extraction: LeadFieldExtraction,
        tool_def: Any,
    ) -> tuple[bool, Optional[str]]:
        if global_collection_owns_turn(context, "lead_capture") or _field_mutation_is_blocked(context):
            return False, None
        updates = extraction.field_updates
        if not isinstance(updates, dict) or not updates:
            return False, None

        schema = ToolRegistry._strict_tool_schema(tool_def.module.get_tool_schema(context.config.config_data))
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        field_defs = _module_field_defs(tool_def, context)
        allowed_canonical = {
            "customer_name",
            "phone",
            "email",
            "interest",
            "message",
            "preferred_contact_time",
        }
        if "source" in properties or "source" in field_defs:
            allowed_canonical.add("source")
        if "notes" in properties or "notes" in field_defs:
            allowed_canonical.add("notes")

        current = context.session_data.state.get_task_step_data("lead_capture")
        safe_updates: dict[str, Any] = {}
        changed = False

        for raw_key, raw_value in updates.items():
            canonical_key = str(raw_key)
            if canonical_key not in allowed_canonical:
                return False, canonical_key
            key = _lead_field_key_for_update(canonical_key, field_defs, properties)
            if not key or (key not in properties and key not in field_defs):
                return False, canonical_key
            if raw_value in (None, "") or isinstance(raw_value, (dict, list)):
                return False, canonical_key

            field_confidence = extraction.field_confidence.get(
                canonical_key,
                extraction.field_confidence.get(key, extraction.confidence),
            )
            if field_confidence < LEAD_FIELD_CONFIDENCE_THRESHOLD:
                continue

            value = raw_value
            field_def = field_defs.get(key)
            if field_def:
                validation = tool_def.module.validate_input(field_def, value, tenant_config=context.config.config_data)
                if not validation.is_valid:
                    return False, canonical_key

            existing = current.get(key)
            if existing not in (None, "") and str(existing) != str(value):
                can_overwrite = (
                    extraction.workflow_action == "change"
                    or _recent_module_field_error(context, key)
                    or _existing_module_field_invalid(tool_def, context, key, existing)
                )
                if not can_overwrite:
                    continue

            safe_updates[key] = value
            if str(current.get(key, "")) != str(value):
                changed = True

        if not safe_updates:
            return False, None
        if changed:
            merged = dict(current)
            merged.update(safe_updates)
            context.session_data.state.update_task_step_data("lead_capture", merged)
        return changed, None

    def _apply_safe_support_field_updates(
        self,
        context: ExecutionContext,
        extraction: SupportFieldExtraction,
        tool_def: Any,
    ) -> tuple[bool, Optional[str]]:
        if global_collection_owns_turn(context, "support") or _field_mutation_is_blocked(context):
            return False, None
        updates = extraction.field_updates
        if not isinstance(updates, dict) or not updates:
            return False, None

        schema = ToolRegistry._strict_tool_schema(tool_def.module.get_tool_schema(context.config.config_data))
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        field_defs = _module_field_defs(tool_def, context)
        allowed_canonical = {
            "customer_name",
            "phone",
            "email",
            "issue_summary",
            "support_reason",
            "urgency",
        }
        for optional_key in ("preferred_contact_time", "order_reference", "notes"):
            if optional_key in properties or optional_key in field_defs:
                allowed_canonical.add(optional_key)

        current = context.session_data.state.get_task_step_data("support")
        safe_updates: dict[str, Any] = {}
        changed = False

        for raw_key, raw_value in updates.items():
            canonical_key = str(raw_key)
            if canonical_key not in allowed_canonical:
                return False, canonical_key
            key = _support_field_key_for_update(canonical_key, field_defs, properties)
            if not key or (key not in properties and key not in field_defs):
                return False, canonical_key
            if raw_value in (None, "") or isinstance(raw_value, (dict, list)):
                return False, canonical_key

            field_confidence = extraction.field_confidence.get(
                canonical_key,
                extraction.field_confidence.get(key, extraction.confidence),
            )
            if field_confidence < SUPPORT_FIELD_CONFIDENCE_THRESHOLD:
                continue

            value = raw_value
            if key == "urgency":
                value = str(value).strip().lower()
            field_def = field_defs.get(key)
            if field_def:
                validation = tool_def.module.validate_input(field_def, value, tenant_config=context.config.config_data)
                if not validation.is_valid:
                    return False, canonical_key

            existing = current.get(key)
            if existing not in (None, "") and str(existing) != str(value):
                can_overwrite = (
                    extraction.workflow_action == "change"
                    or _recent_module_field_error(context, key)
                    or _existing_module_field_invalid(tool_def, context, key, existing)
                )
                if not can_overwrite:
                    continue

            safe_updates[key] = value
            if str(current.get(key, "")) != str(value):
                changed = True

        if not safe_updates:
            return False, None
        if changed:
            merged = dict(current)
            merged.update(safe_updates)
            context.session_data.state.update_task_step_data("support", merged)
        return changed, None

    def _apply_safe_reminder_field_updates(
        self,
        context: ExecutionContext,
        extraction: ReminderFieldExtraction,
        tool_def: Any,
    ) -> tuple[bool, Optional[str]]:
        if global_collection_owns_turn(context, "reminder") or _field_mutation_is_blocked(context):
            return False, None
        updates = extraction.field_updates
        if not isinstance(updates, dict) or not updates:
            return False, None

        schema = ToolRegistry._strict_tool_schema(tool_def.module.get_tool_schema(context.config.config_data))
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        field_defs = _module_field_defs(tool_def, context)
        allowed_canonical = {"reminder_message", "message", "scheduled_for", "reminder_date", "reminder_time", "channel"}
        for optional_key in ("customer_name", "phone", "email", "notes"):
            if optional_key in properties or optional_key in field_defs:
                allowed_canonical.add(optional_key)

        current = context.session_data.state.get_task_step_data("reminders")
        normalized_updates = dict(updates)
        date_or_time_present = "reminder_date" in normalized_updates or "reminder_time" in normalized_updates
        if date_or_time_present:
            for key in ("reminder_date", "reminder_time"):
                if key in normalized_updates and extraction.field_confidence.get(key, extraction.confidence) < REMINDER_FIELD_CONFIDENCE_THRESHOLD:
                    return False, key
            scheduled_for, schedule_error = _reminder_schedule_from_updates(context, normalized_updates, current)
            normalized_updates.pop("reminder_date", None)
            normalized_updates.pop("reminder_time", None)
            if schedule_error or not scheduled_for:
                return False, schedule_error or "scheduled_for"
            normalized_updates["scheduled_for"] = scheduled_for

        safe_updates: dict[str, Any] = {}
        changed = False
        for raw_key, raw_value in normalized_updates.items():
            canonical_key = str(raw_key)
            if canonical_key not in allowed_canonical:
                return False, canonical_key
            key = _reminder_field_key_for_update(canonical_key, field_defs, properties)
            if not key or (key not in properties and key not in field_defs):
                return False, canonical_key
            if raw_value in (None, "") or isinstance(raw_value, (dict, list)):
                return False, canonical_key
            field_confidence = extraction.field_confidence.get(canonical_key, extraction.field_confidence.get(key, extraction.confidence))
            if field_confidence < REMINDER_FIELD_CONFIDENCE_THRESHOLD:
                continue

            value = raw_value
            field_def = field_defs.get(key) or {
                "key": key,
                "type": properties.get(key, {}).get("type", "string"),
                "required": False,
            }
            validation = tool_def.module.validate_input(field_def, value, tenant_config=context.config.config_data)
            if not validation.is_valid:
                return False, canonical_key
            if key == "scheduled_for" and not _is_future_reminder_schedule(context, value):
                return False, "scheduled_for"
            if key == "channel":
                channel_error = tool_def.module._validate_channel_for_concierge(str(value), context)
                if channel_error:
                    return False, "channel"

            existing = current.get(key)
            if existing not in (None, "") and str(existing) != str(value):
                can_overwrite = (
                    extraction.workflow_action == "change"
                    or _recent_module_field_error(context, key)
                    or _existing_module_field_invalid(tool_def, context, key, existing)
                )
                if not can_overwrite:
                    continue
            safe_updates[key] = value
            if str(current.get(key, "")) != str(value):
                changed = True

        if not safe_updates:
            return False, None
        if changed:
            merged = dict(current)
            merged.update(safe_updates)
            context.session_data.state.update_task_step_data("reminders", merged)
        return changed, None

    def _apply_safe_review_field_updates(
        self,
        context: ExecutionContext,
        extraction: ReviewFieldExtraction,
        tool_def: Any,
    ) -> tuple[bool, Optional[str]]:
        if global_collection_owns_turn(context, "review") or _field_mutation_is_blocked(context):
            return False, None
        updates = extraction.field_updates
        if not isinstance(updates, dict) or not updates:
            return False, None

        schema = ToolRegistry._strict_tool_schema(tool_def.module.get_tool_schema(context.config.config_data))
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        field_defs = _module_field_defs(tool_def, context)
        allowed_canonical = {"rating", "feedback"}
        for optional_key in ("customer_name", "phone", "email", "order_reference", "service_name", "notes"):
            if optional_key in properties or optional_key in field_defs:
                allowed_canonical.add(optional_key)

        current = context.session_data.state.get_task_step_data("reviews")
        safe_updates: dict[str, Any] = {}
        changed = False
        for raw_key, raw_value in updates.items():
            canonical_key = str(raw_key)
            if canonical_key not in allowed_canonical:
                return False, canonical_key
            key = _review_field_key_for_update(canonical_key, field_defs, properties)
            if not key or (key not in properties and key not in field_defs):
                return False, canonical_key
            if raw_value in (None, "") or isinstance(raw_value, (dict, list)):
                return False, canonical_key
            field_confidence = extraction.field_confidence.get(canonical_key, extraction.field_confidence.get(key, extraction.confidence))
            if field_confidence < REVIEW_FIELD_CONFIDENCE_THRESHOLD:
                continue

            value = raw_value
            field_def = field_defs.get(key) or {
                "key": key,
                "type": properties.get(key, {}).get("type", "string"),
                "required": False,
            }
            validation = tool_def.module.validate_input(field_def, value, tenant_config=context.config.config_data)
            if not validation.is_valid:
                return False, canonical_key

            existing = current.get(key)
            if existing not in (None, "") and str(existing) != str(value):
                can_overwrite = (
                    extraction.workflow_action == "change"
                    or _recent_module_field_error(context, key)
                    or _existing_module_field_invalid(tool_def, context, key, existing)
                )
                if not can_overwrite:
                    continue
            safe_updates[key] = value
            if str(current.get(key, "")) != str(value):
                changed = True

        if not safe_updates:
            return False, None
        if changed:
            merged = dict(current)
            merged.update(safe_updates)
            context.session_data.state.update_task_step_data("reviews", merged)
        return changed, None

    def _apply_safe_payment_field_updates(
        self,
        context: ExecutionContext,
        extraction: PaymentFieldExtraction,
        tool_def: Any,
    ) -> tuple[bool, Optional[str]]:
        if global_collection_owns_turn(context, "payment") or _field_mutation_is_blocked(context):
            return False, None
        updates = extraction.field_updates
        """Apply safe payment candidate fields from LLM extraction to workflow state.

        Payment safety rules:
        - Any unsafe/blocked field in updates rejects the ENTIRE result (fail-closed).
        - Amount must be a positive numeric value.
        - Currency must be allowed by tenant payment config if configured.
        - Fields not in the safe canonical set are rejected.
        - Only applies fields with sufficient per-field confidence.
        - Does not overwrite stable existing fields unless workflow_action is 'change'.
        - Never mutates payment_status, provider IDs, link URLs, or any provider result.
        """
        if _field_mutation_is_blocked(context):
            return False, None
        updates = extraction.field_updates
        if not isinstance(updates, dict) or not updates:
            return False, None

        schema = ToolRegistry._strict_tool_schema(tool_def.module.get_tool_schema(context.config.config_data))
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        field_defs = _module_field_defs(tool_def, context)

        # Strict: reject entire extraction if any blocked field or UUID is present
        from backend.logic.engine.field_extraction import UUID_PATTERN
        for raw_key, raw_val in updates.items():
            raw_key_str = str(raw_key)
            raw_val_str = str(raw_val or "")
            if raw_key_str.lower() in _PAYMENT_BLOCKED_FIELDS:
                return False, raw_key_str
            if UUID_PATTERN.search(raw_key_str) or UUID_PATTERN.search(raw_val_str):
                return False, raw_key_str

        # Determine allowed optional fields from schema/config
        config_data = context.config.config_data or {}
        payment_config = (config_data.get("modules") or {}).get("payments") or {}
        allowed_currencies = [
            str(c).upper() for c in (payment_config.get("allowed_currencies") or []) if c
        ]

        current = context.session_data.state.get_task_step_data("payments")
        safe_updates: dict[str, Any] = {}
        changed = False

        for raw_key, raw_value in updates.items():
            canonical_key = str(raw_key).lower()

            # Hard block — double-check against blocklist
            if canonical_key in _PAYMENT_BLOCKED_FIELDS:
                return False, canonical_key

            # Only safe canonical keys are permitted
            if canonical_key not in _PAYMENT_SAFE_CANONICAL_KEYS:
                return False, canonical_key

            key = _payment_field_key_for_update(canonical_key, field_defs, properties)
            if not key:
                # Field not allowed or not in schema — reject if it was expected, skip if optional
                if canonical_key in {"amount", "currency"}:
                    return False, canonical_key
                continue

            if raw_value in (None, "") or isinstance(raw_value, (dict, list)):
                return False, canonical_key

            field_confidence = extraction.field_confidence.get(
                canonical_key,
                extraction.field_confidence.get(key, extraction.confidence),
            )
            if field_confidence < PAYMENT_FIELD_CONFIDENCE_THRESHOLD:
                continue

            # --- Amount validation ---
            if canonical_key == "amount":
                try:
                    amt = float(raw_value)
                except (TypeError, ValueError):
                    return False, "amount"
                if amt <= 0:
                    return False, "amount"
                value: Any = amt
            # --- Currency validation ---
            elif canonical_key == "currency":
                currency_str = str(raw_value).strip().upper()
                if not currency_str or len(currency_str) < 3:
                    return False, "currency"
                if allowed_currencies and currency_str not in allowed_currencies:
                    return False, "currency"
                value = currency_str
            else:
                value = raw_value

            # Do not overwrite stable existing fields unless action is change or field is invalid
            existing = current.get(key)
            if existing not in (None, "") and str(existing) != str(value):
                can_overwrite = (
                    extraction.workflow_action == "change"
                    or _recent_module_field_error(context, key)
                    or _existing_module_field_invalid(tool_def, context, key, existing)
                )
                if not can_overwrite:
                    continue

            safe_updates[key] = value
            if str(current.get(key, "")) != str(value):
                changed = True

        if not safe_updates:
            return False, None
        if changed:
            merged = dict(current)
            merged.update(safe_updates)
            context.session_data.state.update_task_step_data("payments", merged)
        return changed, None

    def _payment_tangent_response(self, context: ExecutionContext, routing_decision) -> WorkflowResult:
        return self._active_workflow_tangent_response(context, routing_decision)

    async def _handle_semantic_payment_field_extraction(
        self,
        context: ExecutionContext,
        routing_decision,
    ) -> Optional[WorkflowResult]:
        """Integrate LLM-backed semantic field extraction for active payment workflows only.

        Activation conditions:
        - current_task or current_module is payments/payment, OR
        - scoped_step_data has active payment state.

        Safety invariants:
        - LLM only extracts candidate fields. It never executes tools, creates links,
          sets payment status, or bypasses validation.
        - Any unsafe/blocked field in LLM output rejects the entire extraction result.
        - Credential-shape detection failure, low confidence, invalid amount/currency,
          provider/config issues, and disabled module all preserve state and ask clarification.
        - Active payment state is NEVER cleared on extraction failure.
        """
        # Only activate for active payment workflow scope
        if not (_has_active_module_scope(context, "payments") or _has_active_module_scope(context, "payment")):
            return None
        # Do not run for pure FAQ direct answers
        if (
            getattr(routing_decision, "intent", None) == "faq"
            and getattr(routing_decision, "route_type", None) == "direct_answer"
        ):
            return None
        # Payment module must be enabled in tenant config
        if not (_module_enabled(context, "payments") or _module_enabled(context, "payment")):
            return None
        try:
            tool_def = self.tool_registry.get_tool("payments.execute")
        except Exception:
            return None
        if not tool_def.module:
            return None

        try:
            extraction = (
                context.shadow_turn_evidence["field_extraction"]
                if _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE
                and "field_extraction" in context.shadow_turn_evidence
                else await extract_payment_fields(context)
            )
        except Exception:
            _record_shadow_evidence(context, "semantic_failure", "semantic_provider_failed")
            # Credential-shape detection, timeout, provider error — preserve state, ask clarification
            return _payment_clarification_response(context, tool_def)
        _record_shadow_evidence(context, "field_extraction", extraction)
        note_detected_customer_language(context, extraction.detected_language, confidence=extraction.confidence)

        # Fail-closed on low confidence or unknown/unclear workflow
        if (
            extraction.workflow != "payment"
            or extraction.confidence < PAYMENT_FIELD_EXTRACTION_CONFIDENCE_THRESHOLD
            or extraction.workflow_action in {"unknown", "switch", "clarify"}
        ):
            return _payment_clarification_response(context, tool_def)

        if extraction.workflow_action == "cancel":
            _cancel_active_task(context)
            return WorkflowResult(
                response_text="Okay, I've cancelled this payment request. What would you like to do next?",
                next_state=ConversationState.CANCELLED,
            )

        if extraction.workflow_action == "tangent":
            return self._payment_tangent_response(context, routing_decision)

        applied, invalid_field = self._apply_safe_payment_field_updates(context, extraction, tool_def)
        if invalid_field:
            return _payment_clarification_response(context, tool_def, reason_field=invalid_field)
        if extraction.clarification_needed and not applied:
            fields = extraction.clarification_fields
            return _payment_clarification_response(
                context,
                tool_def,
                reason_field=str(fields[0]) if fields else None,
            )
        if extraction.field_updates and not applied:
            return _payment_clarification_response(context, tool_def)
        return None

    def _lead_tangent_response(self, context: ExecutionContext, routing_decision) -> WorkflowResult:
        return self._active_workflow_tangent_response(context, routing_decision)

    def _support_tangent_response(self, context: ExecutionContext, routing_decision) -> WorkflowResult:
        return self._active_workflow_tangent_response(context, routing_decision)

    async def _activate_fresh_lead_from_semantic_contract(
        self,
        context: ExecutionContext,
        routing_decision: RoutingDecision,
        canonical_control: Any,
        runtime_available_modules: set[str] | frozenset[str],
    ) -> tuple[RoutingDecision, bool]:
        """Activate a fresh lead only from validated canonical semantic evidence."""
        frame = context.turn_interpretation
        plan = context.turn_plan
        ledger = context.session_data.state.action_ledger
        if (
            _turn_interpretation_mode() != TurnInterpretationMode.AUTHORITATIVE
            or frame is None
            or plan is None
            or getattr(frame, "source", None) != InterpretationSource.SEMANTIC_GATEWAY
            or getattr(frame, "turn_kind", None) != TurnKind.NEW_WORKFLOW_REQUEST
            or getattr(frame, "candidate_workflow", None) != "lead_capture"
            or getattr(frame, "candidate_workflow_available", None) is not True
            or float(getattr(frame, "confidence", 0.0) or 0.0) < _SEMANTIC_CONFIDENCE_THRESHOLD
            or bool(getattr(frame, "needs_clarification", True))
            # Gateway validation normalizes a fresh non-switch request to continue.
            or str(getattr(getattr(frame, "workflow_action", ""), "value", getattr(frame, "workflow_action", ""))) != "continue"
            or getattr(plan, "candidate_workflow", None) != "lead_capture"
            or bool(getattr(plan, "tangent_requested", False))
            or bool(getattr(plan, "switch_requested", False))
            or bool(getattr(frame, "cancel_requested", False))
            or bool(getattr(frame, "explicit_switch", False))
            or canonical_control is None
            or canonical_control.action != CanonicalControlAction.HANDLE_NEW_WORKFLOW
            or canonical_control.workflow != "lead_capture"
            or _has_active_workflow_state(context)
            or ledger.pending_action is not None
            or "lead_capture" not in runtime_available_modules
        ):
            return routing_decision, False

        tool = await self.tool_registry.get_pre_route_eligible_tool(
            context, "lead_capture.execute"
        )
        if tool is None or tool.module_name != "lead_capture":
            return routing_decision, False
        if not context.session_data.activate_workflow_task("lead_capture"):
            return routing_decision, False

        logger.info(
            "fresh_lead_semantic_activation",
            extra={"extra_data": {
                "workflow": "lead_capture",
                "turn_kind": str(frame.turn_kind),
                "candidate_workflow": "lead_capture",
                "reason": "fresh_lead_semantic_activation",
            }},
        )
        return RoutingDecision(
            intent="lead_capture",
            confidence=float(getattr(frame, "confidence", routing_decision.confidence)),
            route_type="active_task",
            is_tangent=False,
            should_clear_active_task=False,
            reason="fresh_lead_semantic_activation",
        ), True

    async def _handle_semantic_lead_field_extraction(
        self,
        context: ExecutionContext,
        routing_decision,
    ) -> Optional[WorkflowResult]:
        if not _has_active_module_scope(context, "lead_capture"):
            return None
        if (
            getattr(routing_decision, "intent", None) == "faq"
            and getattr(routing_decision, "route_type", None) == "direct_answer"
        ):
            return None
        if getattr(routing_decision, "reason", None) == "active_task_short_continuation":
            return None
        if not _module_enabled(context, "lead_capture"):
            return None

        # Iteration 7C authoritative non-terminal lead continuation. Runs after the
        # Iteration 7B shadow capture and active-lead eligibility establishment, and
        # before any legacy semantic lead extraction, safe-update application, the
        # LangGraph agent, or any other lead mutation. When it handles the turn it
        # owns the single lead-scoped state mutation and the same customer message
        # never reaches the legacy extractor. Terminal and invalid batches return
        # None so the existing lead runtime owns the message unchanged.
        authoritative_result = await self._try_authoritative_lead_configured_turn_apply(context)
        if authoritative_result is not None:
            return authoritative_result

        # Iteration 7D-C deterministic terminal lead registry handoff. Runs after the
        # 7C non-terminal apply deferred a terminal batch and before any legacy semantic
        # lead extraction, safe-update application, the LangGraph agent, or any other
        # lead mutation. When it owns the turn it executes lead_capture.execute through
        # the ToolRegistry exactly once and the same customer message never reaches the
        # legacy extractor.
        terminal_result = await self._try_authoritative_lead_terminal_registry_handoff(context)
        if terminal_result is not None:
            return terminal_result

        try:
            tool_def = self.tool_registry.get_tool("lead_capture.execute")
        except Exception:
            return None
        if not tool_def.module:
            return None

        try:
            extraction = (
                context.shadow_turn_evidence["field_extraction"]
                if _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE
                and "field_extraction" in context.shadow_turn_evidence
                else await extract_lead_fields(context)
            )
        except Exception:
            _record_shadow_evidence(context, "semantic_failure", "semantic_provider_failed")
            return _lead_clarification_response(context, tool_def)
        _record_shadow_evidence(context, "field_extraction", extraction)
        note_detected_customer_language(context, extraction.detected_language, confidence=extraction.confidence)

        if (
            extraction.workflow != "lead_capture"
            or extraction.confidence < LEAD_FIELD_EXTRACTION_CONFIDENCE_THRESHOLD
            or extraction.workflow_action in {"unknown", "switch", "clarify"}
        ):
            return _lead_clarification_response(context, tool_def)

        if extraction.workflow_action == "cancel":
            _cancel_active_task(context)
            return WorkflowResult(
                response_text="Okay, I've cancelled this request. What would you like to do next?",
                next_state=ConversationState.CANCELLED,
            )

        if extraction.workflow_action == "tangent":
            return self._lead_tangent_response(context, routing_decision)

        applied, invalid_field = self._apply_safe_lead_field_updates(context, extraction, tool_def)
        if invalid_field:
            return _lead_clarification_response(context, tool_def, reason_field=invalid_field)
        if extraction.clarification_needed and not applied:
            fields = extraction.clarification_fields
            return _lead_clarification_response(
                context,
                tool_def,
                reason_field=str(fields[0]) if fields else None,
            )
        if extraction.field_updates and not applied:
            return _lead_clarification_response(context, tool_def)
        return None

    async def _try_authoritative_lead_configured_turn_apply(
        self,
        context: ExecutionContext,
    ) -> Optional[WorkflowResult]:
        """Iteration 7C authoritative non-terminal multi-field lead continuation.

        Eligible only when both lead platform flags are enabled, the lead is an
        already-active customer-authored continuation, the Iteration 7B shadow
        produced a backend-validated candidate batch, and at least one safe
        configured required field remains missing before application. The candidate
        batch is revalidated at application time and persisted through exactly one
        lead-scoped update -- unless applying it would complete every applicable
        required configured field, in which case the terminal batch is deferred to
        the existing lead runtime with zero authoritative writes.

        Returns a WorkflowResult when it handled the turn (applied or already-
        satisfied), otherwise None so the existing lead semantic extraction and
        safe-update path run unchanged.
        """
        if not _authoritative_lead_apply_eligible(context):
            return None

        snapshot = context.shadow_turn_evidence.get(LEAD_SHADOW_EVIDENCE_KEY)
        if not isinstance(snapshot, dict):
            return None
        # Rebuild the raw candidate map from the usable semantic result so the apply
        # method revalidates at application time (it must not trust the shadow's
        # earlier validation without revalidation). This is also required for the
        # already-satisfied replay (Outcome B) where the validated patch is empty.
        candidate_map = _lead_shadow_candidate_map(snapshot)
        if not candidate_map:
            return None

        focus_before = _lead_focus_field_key(context)
        lead_before = dict(context.session_data.state.get_task_step_data("lead_capture") or {})
        try:
            applied = lead_capture_module.apply_external_configured_candidates(
                context, candidate_map
            )
        except Exception as exc:  # pragma: no cover - defensive
            _emit_lead_apply_failure_event(
                context, stage="authoritative_lead_apply", error_type=type(exc).__name__
            )
            # Outcome E: an uncertain write (state already mutated before the error)
            # must NOT fall back to the legacy extractor; the outer failure handler
            # owns it. Only a pre-update exception falls back to the existing runtime.
            lead_after = dict(context.session_data.state.get_task_step_data("lead_capture") or {})
            if lead_after != lead_before:
                raise
            return None

        # Outcome C/D: nothing safely applicable (or terminal batch deferred) -> the
        # existing legacy extractor owns the turn. This is the ONLY branch that
        # returns None so extract_lead_fields / _apply_safe_lead_field_updates may run.
        if not applied.handled:
            return None

        # Outcomes A and B are both "handled": this path now owns conversational
        # mutation for the turn. The legacy semantic extractor and a second
        # lead-scoped update must never run for this same customer message, whether
        # a write occurred (applied) or the batch was already satisfied.
        try:
            tool_def = self.tool_registry.get_tool("lead_capture.execute")
        except Exception:
            tool_def = None

        if applied.update_performed:
            focus_after = _lead_focus_field_key(context)
            try:
                _emit_lead_applied_event(
                    context,
                    config_version=getattr(context.session_data, "config_version", None),
                    channel=str((context.incoming_message or {}).get("channel_type") or "unknown").strip().lower(),
                    applied_field_keys=list(applied.accepted_field_keys),
                    remaining_required_field_keys=_module_missing_required_fields(tool_def, context, "lead_capture") if tool_def else [],
                    focus_field_key_before=focus_before,
                    focus_field_key_after=focus_after,
                )
            except Exception:
                # Telemetry failure must never invalidate the authoritative result.
                pass

            # Retain the established in-memory extraction evidence shape so the
            # existing canonical-control observability sees exactly one patch. Only
            # emit evidence when a write actually happened; an already-satisfied
            # replay performs no interpretation at all. Values come from authoritative
            # persisted state (already validated), never from the raw utterance.
            lead_state = context.session_data.state.get_task_step_data("lead_capture")
            applied_updates = {
                key: lead_state.get(key)
                for key in applied.accepted_field_keys
                if key in lead_state
            }
            context.shadow_turn_evidence["field_extraction"] = LeadFieldExtraction(
                workflow="lead_capture",
                workflow_action="continue",
                confidence=1.0,
                field_updates=applied_updates,
                field_confidence={key: 1.0 for key in applied.accepted_field_keys},
                clarification_needed=False,
            )

        # State-based continuation. Whether we wrote new values or the batch was
        # already satisfied, the next response is derived entirely from the
        # authoritative updated lead state -- never from the raw utterance and never
        # by calling extract_lead_fields / _apply_safe_lead_field_updates again.
        return await self._continue_from_authoritative_lead_state(context, tool_def)

    async def _continue_from_authoritative_lead_state(
        self,
        context: ExecutionContext,
        tool_def: Any,
    ) -> "WorkflowResult":
        """Derive the next response from authoritative lead state only.

        Ask for the first genuinely missing required field, computed from
        authoritative state (not the utterance). The lead application path is
        non-terminal: a handled batch always leaves at least one required field
        missing unless the batch was already satisfied with a field still open.
        This never calls extract_lead_fields and never claims lead success.
        """
        if tool_def is None or not getattr(tool_def, "module", None):
            missing = _module_missing_required_fields_from_defs(context, "lead_capture")
            target = missing[0] if missing else None
            focus_kwargs = _prompt_focus_result_kwargs(
                context, "lead_capture", [target] if target else [], reason_field=None
            )
            return WorkflowResult(
                response_text="I kept your details. Please share the next detail for this request.",
                next_state=context.session_data.state.workflow_state,
                **focus_kwargs,
            )
        return _lead_clarification_response(context, tool_def)

    async def _try_authoritative_lead_terminal_registry_handoff(
        self,
        context: ExecutionContext,
    ) -> Optional[WorkflowResult]:
        """Iteration 7D-C deterministic same-turn terminal lead registry handoff.

        Eligible only when all three lead platform flags are enabled, the lead is an
        already-active customer-authored continuation that was just classified by 7C as
        a terminal (complete) batch, the candidate batch revalidates at application time,
        and the merged payload passes tenant-aware terminal payload validation. The
        engine then replaces the lead-scoped state once with the exact normalized
        terminal payload and executes ``lead_capture.execute`` through the ToolRegistry
        exactly once, recording the authoritative tool result.

        Returns a WorkflowResult owning the turn (success or explicit tool failure), or
        None so the existing legacy lead extraction uses the same customer message.
        """
        if not _authoritative_lead_terminal_apply_enabled():
            return None
        # Reuse the established non-terminal lead authority boundary: customer-authored
        # active lead, lead module enabled, scoped state present, not completed/cancelled,
        # no pending protected action, no OTP/assurance continuation, valid 7B snapshot.
        if not _authoritative_lead_apply_eligible(context):
            return None

        snapshot = context.shadow_turn_evidence.get(LEAD_SHADOW_EVIDENCE_KEY)
        if not isinstance(snapshot, dict):
            return None
        candidate_map = _lead_shadow_candidate_map(snapshot)
        if not candidate_map:
            return None
            return None

        # 7C must have classified this batch as terminal_batch_deferred. Re-running the
        # non-terminal apply on the same candidate map is write-free for a terminal batch.
        try:
            prior_apply = lead_capture_module.apply_external_configured_candidates(
                context, candidate_map
            )
        except Exception as exc:  # pragma: no cover - defensive fail-open
            _emit_lead_apply_failure_event(
                context, stage="authoritative_lead_terminal_apply_check", error_type=type(exc).__name__
            )
            return None
        if getattr(prior_apply, "reason_code", None) != "terminal_batch_deferred":
            return None

        # Application-time revalidation of the raw semantic candidate map. Use only the
        # normalized accepted patch; do not trust the prior shadow validation.
        try:
            candidate_validation = lead_capture_module.validate_external_configured_candidates(
                context, candidate_map
            )
        except Exception as exc:  # pragma: no cover - defensive fail-open
            _emit_lead_apply_failure_event(
                context, stage="authoritative_lead_terminal_revalidation", error_type=type(exc).__name__
            )
            return None
        accepted_patch = dict(getattr(candidate_validation, "accepted_patch", {}) or {})
        if not accepted_patch:
            # No newly accepted terminal candidate -> do not own the turn.
            return None

        # Build an in-memory merged terminal payload from current scoped state + the
        # normalized accepted patch, then run tenant-aware terminal payload validation.
        # Only prior configured field keys are carried into the merged projection; any
        # unrelated prior keys (sentinels, internal markers, legacy metadata) are dropped
        # here so they never reach the terminal validator, the normalized payload, or the
        # registry-visible state. The exact replacement below then projects only the
        # normalized terminal payload.
        raw_current = dict(context.session_data.state.get_task_step_data("lead_capture") or {})
        configured_fields = lead_capture_module.get_effective_configured_fields_for_context(context)
        configured_keys = {
            str(f.get("key") or "").strip()
            for f in configured_fields
            if isinstance(f, dict) and str(f.get("key") or "").strip()
        }
        current = {k: v for k, v in raw_current.items() if k in configured_keys}
        merged_payload = dict(current)
        merged_payload.update(accepted_patch)

        try:
            terminal_validation = lead_capture_module.validate_terminal_configured_payload(
                context, merged_payload
            )
        except Exception as exc:  # pragma: no cover - defensive fail-open
            _emit_lead_apply_failure_event(
                context, stage="authoritative_lead_terminal_validation", error_type=type(exc).__name__
            )
            return None

        if not getattr(terminal_validation, "valid", False):
            return None
        if getattr(terminal_validation, "missing_required_field_keys", ()):
            return None
        if getattr(terminal_validation, "invalid_field_keys", ()):
            return None
        if getattr(terminal_validation, "rejected_field_keys", ()):
            return None

        normalized_payload = dict(terminal_validation.normalized_payload or {})

        state = context.session_data.state

        # Save the complete pre-execution lead state boundary for explicit failure
        # recovery: scope-presence, scoped values, global step_data, current_task.
        prior_scope_existed = "lead_capture" in state.scoped_step_data
        prior_lead_scoped = dict(
            state.scoped_step_data.get("lead_capture", {})
        )
        prior_global_step_data = dict(state.step_data)
        prior_current_task = state.action_ledger.current_task

        # Exactly one exact-replacement of the lead-scoped state with the normalized
        # terminal payload. This is an exact projection, not a merge: any prior unrelated
        # scoped keys are dropped so only the normalized terminal payload reaches the
        # registry-visible state. The registry's activate_task_step_data mirrors this
        # into global step_data at execution time. The engine never mutates session
        # dictionaries directly; SessionState.replace_task_step_data owns the write.
        state.replace_task_step_data(
            "lead_capture",
            dict(normalized_payload),
        )

        # Key-only internal telemetry (fail-open).
        try:
            logger.info(
                "lead_configured_turn_terminal_handoff",
                extra={
                    "extra_data": {
                        "event": "lead_configured_turn_terminal_handoff",
                        "workflow": "lead_capture",
                        "config_version": getattr(context.session_data, "config_version", None),
                        "channel": str(
                            (context.incoming_message or {}).get("channel_type") or "unknown"
                        ).strip().lower(),
                        "status": "success",
                        "configured_field_keys": list(
                            sorted(set(terminal_validation.configured_field_keys))
                        ),
                        "required_field_keys": list(
                            sorted(set(terminal_validation.required_field_keys))
                        ),
                        "applied_field_keys": list(sorted(set(accepted_patch.keys()))),
                        "configured_field_count": len(set(terminal_validation.configured_field_keys)),
                        "required_field_count": len(set(terminal_validation.required_field_keys)),
                        "applied_field_count": len(set(accepted_patch.keys())),
                        "tool_success": None,
                        "created_resource_present": None,
                    }
                },
            )
        except Exception:
            # Telemetry failure must never invalidate the authoritative result.
            pass

        # Exactly one registry execution through the guarded tool registry. No direct
        # module/handler/service/repository/event call, no manual result fabrication.
        try:
            result = await self.tool_registry.execute_tool(
                "lead_capture.execute",
                context,
            )
        except Exception as exc:
            # An exception after execution is an uncertain state: propagate without a
            # second execution, without restoring state (uncertain), and without a
            # fabricated normal WorkflowResult.
            _emit_lead_apply_failure_event(
                context, stage="authoritative_lead_terminal_handoff", error_type=type(exc).__name__
            )
            raise

        success = bool(getattr(result, "success", False))
        channel_type = (context.incoming_message or {}).get("channel_type")
        context.session_data.state.record_tool_result(result, channel_type=channel_type)

        if success:
            # Update telemetry with the authoritative outcome (fail-open).
            try:
                logger.info(
                    "lead_configured_turn_terminal_handoff",
                    extra={
                        "extra_data": {
                            "event": "lead_configured_turn_terminal_handoff",
                            "workflow": "lead_capture",
                            "config_version": getattr(context.session_data, "config_version", None),
                            "channel": str(
                                (context.incoming_message or {}).get("channel_type") or "unknown"
                            ).strip().lower(),
                            "status": "success",
                            "configured_field_keys": list(
                                sorted(set(terminal_validation.configured_field_keys))
                            ),
                            "required_field_keys": list(
                                sorted(set(terminal_validation.required_field_keys))
                            ),
                            "applied_field_keys": list(sorted(set(accepted_patch.keys()))),
                            "configured_field_count": len(set(terminal_validation.configured_field_keys)),
                            "required_field_count": len(set(terminal_validation.required_field_keys)),
                            "applied_field_count": len(set(accepted_patch.keys())),
                            "tool_success": True,
                            "created_resource_present": bool(
                                getattr(result, "created_resource_type", None)
                                and getattr(result, "created_resource_id", None)
                            ),
                        }
                    },
                )
            except Exception:
                pass
            # Use the existing authoritative tool result's customer message, guarded so a
            # purported success without a created resource id cannot produce saved-success
            # language. No manual task_status assignment; record_tool_result owns it.
            response = WorkflowResult(
                response_text=getattr(result, "user_message", None)
                or "That request was completed successfully.",
                next_state=context.session_data.state.workflow_state,
            )
            response = _apply_authoritative_success_truth_guard(
                context, response, previous_tool_result=None
            )
            return response

        # Explicit tool failure: exactly one snapshot restoration through the
        # SessionState-owned restore_task_step_data_snapshot primitive. This restores
        # scope-presence, scoped values, global step_data, and current_task in a single
        # call without additional replace_task_step_data or activate_task_step_data.
        state.restore_task_step_data_snapshot(
            "lead_capture",
            scope_existed=prior_scope_existed,
            scoped_values=prior_lead_scoped,
            global_values=prior_global_step_data,
            current_task=prior_current_task,
        )

        # Update telemetry with the tool-failure outcome (fail-open).
        try:
            logger.info(
                "lead_configured_turn_terminal_handoff",
                extra={
                    "extra_data": {
                        "event": "lead_configured_turn_terminal_handoff",
                        "workflow": "lead_capture",
                        "config_version": getattr(context.session_data, "config_version", None),
                        "channel": str(
                            (context.incoming_message or {}).get("channel_type") or "unknown"
                        ).strip().lower(),
                        "status": "tool_failure",
                        "configured_field_keys": list(
                            sorted(set(terminal_validation.configured_field_keys))
                        ),
                        "required_field_keys": list(
                            sorted(set(terminal_validation.required_field_keys))
                        ),
                        "applied_field_keys": list(sorted(set(accepted_patch.keys()))),
                        "configured_field_count": len(set(terminal_validation.configured_field_keys)),
                        "required_field_count": len(set(terminal_validation.required_field_keys)),
                        "applied_field_count": len(set(accepted_patch.keys())),
                        "tool_success": False,
                        "created_resource_present": False,
                    }
                },
            )
        except Exception:
            pass

        failure_response = WorkflowResult(
            response_text=getattr(result, "user_message", None)
            or _safe_tool_failure_message(context),
            next_state=context.session_data.state.workflow_state,
        )
        return _tool_failure_recovery_response(context, failure_response)

    async def _handle_semantic_support_field_extraction(
        self,
        context: ExecutionContext,
        routing_decision,
    ) -> Optional[WorkflowResult]:
        if not _has_active_module_scope(context, "support"):
            return None
        if (
            getattr(routing_decision, "intent", None) == "faq"
            and getattr(routing_decision, "route_type", None) == "direct_answer"
        ):
            return None
        if getattr(routing_decision, "reason", None) == "active_task_short_continuation":
            return None
        if not _module_enabled(context, "support"):
            return None

        try:
            tool_def = self.tool_registry.get_tool("support.execute")
        except Exception:
            return None
        if not tool_def.module:
            return None

        try:
            extraction = (
                context.shadow_turn_evidence["field_extraction"]
                if _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE
                and "field_extraction" in context.shadow_turn_evidence
                else await extract_support_fields(context)
            )
        except Exception:
            _record_shadow_evidence(context, "semantic_failure", "semantic_provider_failed")
            return _support_clarification_response(context, tool_def)
        _record_shadow_evidence(context, "field_extraction", extraction)
        note_detected_customer_language(context, extraction.detected_language, confidence=extraction.confidence)

        if (
            extraction.workflow != "support"
            or extraction.confidence < SUPPORT_FIELD_EXTRACTION_CONFIDENCE_THRESHOLD
            or extraction.workflow_action in {"unknown", "switch", "clarify"}
        ):
            return _support_clarification_response(context, tool_def)

        if extraction.workflow_action == "cancel":
            _cancel_active_task(context)
            return WorkflowResult(
                response_text="Okay, I've cancelled this support request. What would you like to do next?",
                next_state=ConversationState.CANCELLED,
            )

        if extraction.workflow_action == "tangent":
            return self._support_tangent_response(context, routing_decision)

        applied, invalid_field = self._apply_safe_support_field_updates(context, extraction, tool_def)
        if invalid_field:
            return _support_clarification_response(context, tool_def, reason_field=invalid_field)
        if extraction.clarification_needed and not applied:
            fields = extraction.clarification_fields
            return _support_clarification_response(
                context,
                tool_def,
                reason_field=str(fields[0]) if fields else None,
            )
        if extraction.field_updates and not applied:
            return _support_clarification_response(context, tool_def)
        return None

    def _reminder_tangent_response(self, context: ExecutionContext, routing_decision) -> WorkflowResult:
        return self._active_workflow_tangent_response(context, routing_decision)

    def _review_tangent_response(self, context: ExecutionContext, routing_decision) -> WorkflowResult:
        return self._active_workflow_tangent_response(context, routing_decision)

    async def _handle_semantic_reminder_field_extraction(
        self,
        context: ExecutionContext,
        routing_decision,
    ) -> Optional[WorkflowResult]:
        if not _has_active_module_scope(context, "reminders"):
            return None
        if (
            getattr(routing_decision, "intent", None) == "faq"
            and getattr(routing_decision, "route_type", None) == "direct_answer"
        ):
            return None
        if not _module_enabled(context, "reminders"):
            return None
        try:
            tool_def = self.tool_registry.get_tool("reminders.execute")
        except Exception:
            return None
        if not tool_def.module:
            return None
        try:
            extraction = (
                context.shadow_turn_evidence["field_extraction"]
                if _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE
                and "field_extraction" in context.shadow_turn_evidence
                else await extract_reminder_fields(context)
            )
        except Exception:
            _record_shadow_evidence(context, "semantic_failure", "semantic_provider_failed")
            return _reminder_clarification_response(context, tool_def)
        _record_shadow_evidence(context, "field_extraction", extraction)
        note_detected_customer_language(context, extraction.detected_language, confidence=extraction.confidence)
        if (
            extraction.workflow != "reminder"
            or extraction.confidence < REMINDER_FIELD_EXTRACTION_CONFIDENCE_THRESHOLD
            or extraction.workflow_action in {"unknown", "switch", "clarify"}
        ):
            return _reminder_clarification_response(context, tool_def)
        if extraction.workflow_action == "cancel":
            _cancel_active_task(context)
            return WorkflowResult(
                response_text="Okay, I've cancelled this reminder request. What would you like to do next?",
                next_state=ConversationState.CANCELLED,
            )
        if extraction.workflow_action == "tangent":
            return self._reminder_tangent_response(context, routing_decision)
        applied, invalid_field = self._apply_safe_reminder_field_updates(context, extraction, tool_def)
        if invalid_field:
            return _reminder_clarification_response(context, tool_def, reason_field=invalid_field)
        if extraction.clarification_needed and not applied:
            fields = extraction.clarification_fields
            return _reminder_clarification_response(
                context,
                tool_def,
                reason_field=str(fields[0]) if fields else None,
            )
        if extraction.field_updates and not applied:
            return _reminder_clarification_response(context, tool_def)
        return None

    async def _handle_semantic_review_field_extraction(
        self,
        context: ExecutionContext,
        routing_decision,
    ) -> Optional[WorkflowResult]:
        if not _has_active_module_scope(context, "reviews"):
            return None
        if (
            getattr(routing_decision, "intent", None) == "faq"
            and getattr(routing_decision, "route_type", None) == "direct_answer"
        ):
            return None
        if not _module_enabled(context, "reviews"):
            return None
        try:
            tool_def = self.tool_registry.get_tool("reviews.execute")
        except Exception:
            return None
        if not tool_def.module:
            return None
        try:
            extraction = (
                context.shadow_turn_evidence["field_extraction"]
                if _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE
                and "field_extraction" in context.shadow_turn_evidence
                else await extract_review_fields(context)
            )
        except Exception:
            _record_shadow_evidence(context, "semantic_failure", "semantic_provider_failed")
            return _review_clarification_response(context, tool_def)
        _record_shadow_evidence(context, "field_extraction", extraction)
        note_detected_customer_language(context, extraction.detected_language, confidence=extraction.confidence)
        if (
            extraction.workflow != "review"
            or extraction.confidence < REVIEW_FIELD_EXTRACTION_CONFIDENCE_THRESHOLD
            or extraction.workflow_action in {"unknown", "switch", "clarify"}
        ):
            return _review_clarification_response(context, tool_def)
        if extraction.workflow_action == "cancel":
            _cancel_active_task(context)
            return WorkflowResult(
                response_text="Okay, I've cancelled this review request. What would you like to do next?",
                next_state=ConversationState.CANCELLED,
            )
        if extraction.workflow_action == "tangent":
            return self._review_tangent_response(context, routing_decision)
        applied, invalid_field = self._apply_safe_review_field_updates(context, extraction, tool_def)
        if invalid_field:
            return _review_clarification_response(context, tool_def, reason_field=invalid_field)
        if extraction.clarification_needed and not applied:
            fields = extraction.clarification_fields
            return _review_clarification_response(
                context,
                tool_def,
                reason_field=str(fields[0]) if fields else None,
            )
        if extraction.field_updates and not applied:
            return _review_clarification_response(context, tool_def)
        return None

    def _pending_tangent_response(self, context: ExecutionContext) -> WorkflowResult:
        user_message = (context.incoming_message or {}).get("text", "")
        routing_decision = message_router.route_message(
            user_message,
            context.config.config_data,
            current_task=context.session_data.state.action_ledger.current_task,
            current_module=context.session_data.current_module,
        )
        if _asks_for_service_list(_normalized_customer_text(context)) or _asks_for_current_status(_normalized_customer_text(context)):
            return self._active_workflow_tangent_response(context, routing_decision)
        if _asks_for_opening_hours(_normalized_customer_text(context)):
            hours_response = build_hours_response(context.config.config_data)
            return _preserve_active_workflow_direct_response(
                context,
                WorkflowResult(
                    response_text=hours_response.text,
                    quick_replies=hours_response.quick_replies,
                    next_state=context.session_data.state.workflow_state,
                ),
                routing_decision,
                resume_result=_pending_action_clarification(context),
            )
        direct_response = _direct_response_for_route(context, routing_decision)
        if direct_response and routing_decision.reason in {
            "exact_catalog_request",
            "natural_catalog_request",
            "exact_opening_hours_request",
        }:
            return _preserve_active_workflow_direct_response(
                context,
                direct_response,
                routing_decision,
                resume_result=_pending_action_clarification(context),
            )
        return _pending_action_clarification(context)

    def _pending_change_response(self, context: ExecutionContext, *, applied: bool) -> str:
        if not applied:
            return "What would you like to change before I complete this action?"
        base = "I updated that detail."
        review = "Please confirm when everything looks right, or tell me another change."
        text = str((context.incoming_message or {}).get("text") or "").lower()
        if not re.search(r"\b(?:price|cost|how much)\b", text):
            return f"{base} {review}"
        pending = context.session_data.state.action_ledger.pending_action
        if not pending or not pending.tool_key.startswith("booking."):
            return f"{base} {review}"
        args = pending.args or {}
        selected = str(args.get("service_name") or args.get("service_id") or "").strip().casefold()
        services = context.config.config_data.get("services") or []
        service = next(
            (
                item for item in services
                if isinstance(item, dict)
                and item.get("enabled", True) is not False
                and selected in {
                    str(item.get("id") or "").strip().casefold(),
                    str(item.get("name") or "").strip().casefold(),
                }
            ),
            None,
        )
        if not service or service.get("price") in (None, ""):
            return f"{base} {review}"
        booking_config = _booking_config(context)
        currency = str(booking_config.get("currency") or context.config.config_data.get("currency") or "INR")
        currency = currency if re.fullmatch(r"[A-Za-z]{3,8}", currency) else "INR"
        name = str(service.get("name") or "The selected service").strip()[:120]
        return f"{base} {name} is {currency.upper()} {service['price']}. Please confirm when everything looks right."

    async def _handle_semantic_pending_action(self, context: ExecutionContext) -> Optional[WorkflowResult]:
        ledger = context.session_data.state.action_ledger
        pending_action = ledger.pending_action
        if not pending_action:
            return None

        if ledger.task_status == "executing":
            return WorkflowResult(
                response_text="Your request is currently being processed. Please wait.",
                next_state=context.session_data.state.workflow_state,
            )
        trusted_verification = _trusted_otp_continuation_matches(context)
        if trusted_verification is not None:
            return await self._execute_confirmed_pending_action(
                context,
                trusted_verified_continuation=True,
                trusted_verification=trusted_verification,
            )
        if str((context.incoming_message or {}).get("source_type") or "").lower() == "internal_event":
            return _pending_action_clarification(context)
        if _is_grounded_active_tangent(context):
            return self._pending_tangent_response(context)

        try:
            decision = (
                context.shadow_turn_evidence["pending_decision"]
                if _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE
                and "pending_decision" in context.shadow_turn_evidence
                else await decide_pending_action(context)
            )
        except Exception:
            _record_shadow_evidence(context, "semantic_failure", "semantic_provider_failed")
            return _pending_action_clarification(context)
        _record_shadow_evidence(context, "pending_decision", decision)
        if (
            _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE
            and context.turn_interpretation is not None
            and context.turn_plan is not None
        ):
            control = decide_canonical_control(
                frame=context.turn_interpretation,
                plan=context.turn_plan,
                active_workflow=ledger.current_task or context.session_data.current_module,
                has_pending_action=True,
            )
            if control.action != CanonicalControlAction.DEFER_TO_PENDING:
                return _pending_action_clarification(context)

        if decision.action == "unknown" or decision.confidence < PENDING_DECISION_CONFIDENCE_THRESHOLD:
            return _pending_action_clarification(context)
        if decision.action == "confirm":
            return await self._execute_confirmed_pending_action(context)
        if decision.action == "cancel":
            _cancel_active_task(context)
            return WorkflowResult(
                response_text="Okay, I've cancelled this request. What would you like to do next?",
                next_state=ConversationState.CANCELLED,
            )
        if decision.action == "change":
            applied = self._apply_safe_pending_field_updates(context, decision)
            return WorkflowResult(
                response_text=self._pending_change_response(context, applied=applied),
                quick_replies=["Confirm", "Change", "Cancel"] if applied else ["Change", "Cancel"],
                next_state=context.session_data.state.workflow_state,
            )
        if decision.action == "tangent":
            return self._pending_tangent_response(context)
        return _pending_action_clarification(context)

    async def _handle_semantic_active_workflow_ambiguity(
        self,
        context: ExecutionContext,
        routing_decision,
    ) -> Optional[WorkflowResult]:
        ledger = context.session_data.state.action_ledger
        active_task = ledger.current_task or context.session_data.current_module
        if not active_task or ledger.pending_action:
            return None
        if getattr(routing_decision, "route_type", None) != "clarification":
            return None
        if getattr(routing_decision, "intent", None) != "ambiguous":
            return None
        try:
            decision = (
                context.shadow_turn_evidence["pending_decision"]
                if _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE
                and "pending_decision" in context.shadow_turn_evidence
                else await decide_pending_action(context)
            )
        except Exception:
            _record_shadow_evidence(context, "semantic_failure", "semantic_provider_failed")
            return _active_workflow_clarification(context)
        _record_shadow_evidence(context, "pending_decision", decision)
        if decision.action == "unknown" or decision.confidence < PENDING_DECISION_CONFIDENCE_THRESHOLD:
            return _active_workflow_clarification(context)
        if decision.action == "cancel":
            _cancel_active_task(context)
            return WorkflowResult(
                response_text="Okay, I've cancelled this request. What would you like to do next?",
                next_state=ConversationState.CANCELLED,
            )
        if decision.action == "change":
            return WorkflowResult(
                response_text="What would you like to change in this request?",
                next_state=context.session_data.state.workflow_state,
            )
        if decision.action == "tangent":
            return None
        if decision.action == "continue":
            return None
        return _active_workflow_clarification(context)

    async def _hydrate_order_catalog(self, context: ExecutionContext) -> None:
        """
        Dynamically hydrate order catalog categories and items from the database.
        Sets the cache in Redis with a 60-second TTL.
        """
        try:
            tenant_id = context.tenant.id
            cache_key = f"awa:catalog:cache:{tenant_id}"

            # 1. Try fetching from Redis cache
            cached_data = None
            try:
                redis_client = get_redis_client()
                cached_data = await redis_client.get(cache_key)
            except Exception as redis_exc:
                logger.warning(f"Failed to query Redis cache for catalog: {redis_exc}")

            categories_list = None
            if cached_data:
                try:
                    categories_list = json.loads(cached_data)
                    logger.info(f"Loaded hydrated order catalog from cache for tenant {tenant_id}")
                except Exception as parse_exc:
                    logger.warning(f"Failed to parse cached catalog JSON: {parse_exc}")

            # 2. If cache miss, query database and populate cache
            if not categories_list:
                from backend.domains.catalog.service import catalog_service
                db_categories = await catalog_service.list_categories(tenant_id)
                db_items = await catalog_service.list_items(tenant_id, active_only=True, available_only=True)

                # Map items to category
                category_map = {}
                for cat in db_categories:
                    if cat.get("is_active") is not False:
                        category_map[cat["id"]] = {
                            "id": str(cat["id"]),
                            "name": cat["name"],
                            "enabled": cat.get("is_active", True),
                            "items": []
                        }

                # Group items under categories
                for item in db_items:
                    cat_id = item.get("category_id")
                    if cat_id in category_map:
                        category_map[cat_id]["items"].append({
                            "id": str(item["id"]),
                            "name": item["name"],
                            "price": float(item["price"]) if item.get("price") is not None else 0.0,
                            "enabled": item.get("is_active", True),
                            "stock_quantity": item.get("stock_quantity"),
                            "stock_tracking_enabled": item.get("stock_tracking_enabled", False),
                            "image": {"url": item.get("image_url")} if item.get("image_url") else {}
                        })

                categories_list = list(category_map.values())

                # Store in Redis
                try:
                    redis_client = get_redis_client()
                    await redis_client.set(cache_key, json.dumps(categories_list), ex=60)
                    logger.info(f"Cached hydrated order catalog for tenant {tenant_id}")
                except Exception as redis_exc:
                    logger.warning(f"Failed to save catalog to Redis: {redis_exc}")

            # 3. Overwrite context config data
            config_data = context.config.config_data
            if isinstance(config_data, dict):
                modules = config_data.setdefault("modules", {})
                if isinstance(modules, dict):
                    order_config = modules.setdefault("order", {})
                    if isinstance(order_config, dict):
                        if categories_list:
                            order_config["categories"] = categories_list
                            logger.info(f"Successfully hydrated config_data with {len(categories_list)} categories")
                        else:
                            logger.info("Database order catalog is empty, falling back to configuration catalog")
        except Exception as exc:
            logger.error(f"Failed to dynamically hydrate order catalog: {exc}", exc_info=True)

    async def _localize_active_workflow_result(
        self,
        context: ExecutionContext,
        result: WorkflowResult,
        *,
        was_active_before_turn: bool,
    ) -> WorkflowResult:
        if not (was_active_before_turn or _has_active_workflow_state(context)):
            return result
        style = _response_style_for_context(context)
        target_language = target_language_for_context(context)
        workflow = _active_workflow_key(context)
        ledger = context.session_data.state.action_ledger
        response_kind = _response_kind_for_context(context)
        safe_facts: dict[str, Any] = {}
        active_task = ledger.current_task or context.session_data.current_module
        if active_task:
            safe_facts = context.session_data.state.get_task_step_data(active_task)
        try:
            localized = await localize_customer_response(
                result.response_text,
                list(result.quick_replies or []),
                {
                    "target_language": target_language,
                    "workflow": workflow,
                    "response_kind": response_kind,
                    "safe_facts": safe_facts,
                    "allow_completion_claims": response_kind == "tool_success",
                    "language_policy_hint": style.language_policy_hint if style else None,
                },
            )
        except Exception as exc:
            logger.warning(
                "Response localization integration failed closed",
                extra={"extra_data": {"error_type": type(exc).__name__}},
            )
            return result
        if not localized.localized and localized.response_text == result.response_text and localized.quick_replies == list(result.quick_replies or []):
            return result
        return WorkflowResult(
            response_text=localized.response_text,
            quick_replies=localized.quick_replies,
            next_state=result.next_state,
            requires_response=result.requires_response,
        )

    async def execute_step(self, context: ExecutionContext) -> WorkflowResult:
        """The main loop for the Agentic engine."""
        from backend.core.context import active_context_var
        token = active_context_var.set(context)
        previous_tool_result = context.session_data.state.action_ledger.last_tool_result
        pending_before_turn = context.session_data.state.action_ledger.pending_action is not None
        was_active_before_turn = _has_active_workflow_state(context)
        context.shadow_turn_evidence = {}
        context.turn_interpretation = None
        context.turn_interpretation_comparison = None
        context.turn_plan = None
        shadow_snapshot = None
        shadow_capture_error = None
        if _shadow_interpretation_enabled():
            try:
                shadow_snapshot = turn_interpretation_coordinator.capture(context)
            except Exception as exc:
                shadow_capture_error = exc
                shadow_snapshot = turn_interpretation_coordinator.fallback_snapshot(context)
                logger.warning(
                    "turn_interpretation_failed",
                    extra={"extra_data": {"event": "turn_interpretation_failed", "reason_code": "snapshot_failed", "error_type": type(exc).__name__}},
                )
            if shadow_capture_error is None:
                (context.incoming_message or {})["_turn_interpretation_snapshot"] = shadow_snapshot
        try:
            result = await self._execute_step_inner(context)
            # Non-authoritative post-runtime comparison observer for the
            # pre-mutation configured-turn shadow. Runs before response
            # composition and localization; never modifies the result.
            result = await observe_global_configured_turn_shadow(context, result)
            # Post-runtime, fail-open comparison observer for the order
            # configured-turn shadow. Runs before response composition and
            # localization; never modifies the result.
            result = await observe_order_configured_turn_shadow(context, result)
            # Post-runtime, fail-open comparison observer for the lead configured-turn
            # shadow. Runs before response composition and localization; never modifies
            # the result.
            result = await observe_lead_configured_turn_shadow(context, result)
            if shadow_snapshot is not None:
                if shadow_capture_error is not None:
                    turn_interpretation_coordinator.safe_failure(context, shadow_snapshot, shadow_capture_error)
                else:
                    try:
                        turn_interpretation_coordinator.observe(context, shadow_snapshot)
                    except Exception as exc:
                        turn_interpretation_coordinator.safe_failure(context, shadow_snapshot, exc)
            if result.prompt_focus_workflow:
                sequence = (context.incoming_message or {}).get("conversation_sequence_number")
                context.session_data.state.set_prompt_focus(
                    workflow=result.prompt_focus_workflow,
                    requested_fields=result.prompt_focus_fields,
                    prompt_kind=result.prompt_focus_kind or "missing_fields",
                    turn_sequence=sequence if isinstance(sequence, int) else None,
                )
            composition_kind = _backend_response_composition_kind(
                context,
                result,
                previous_tool_result=previous_tool_result,
                pending_before_turn=pending_before_turn,
                was_active_before_turn=was_active_before_turn,
            )
            result = _apply_tool_failure_truth_guard(context, result)
            if context.session_data.state.action_ledger.last_tool_result is not previous_tool_result:
                result = _tool_failure_recovery_response(context, result)
                result = await append_next_best_actions(context, result, self.tool_registry)
            result = _style_receptionist_result(context, result)
            result = _apply_authoritative_success_truth_guard(
                context,
                result,
                previous_tool_result=previous_tool_result,
            )
            if composition_kind is not None:
                try:
                    composition = await compose_customer_response(
                        _response_composition_input(context, result, composition_kind),
                        authoritative_side_effects_occurred=(
                            context.session_data.state.action_ledger.last_tool_result is not previous_tool_result
                        ),
                        completion_claim_detector=_contains_definitive_success_claim,
                    )
                    if composition.composed:
                        result = WorkflowResult(
                            response_text=composition.response_text,
                            quick_replies=composition.quick_replies,
                            next_state=result.next_state,
                            requires_response=result.requires_response,
                            composer_eligible=False,
                        )
                except Exception as exc:
                    logger.warning(
                        "Response composer integration failed closed",
                        extra={"extra_data": {"error_type": type(exc).__name__}},
                    )
            result = ensure_booking_reminder_offer(context, result)
            localized = await self._localize_active_workflow_result(
                context,
                result,
                was_active_before_turn=was_active_before_turn,
            )
            return _apply_authoritative_success_truth_guard(
                context,
                localized,
                previous_tool_result=previous_tool_result,
            )
        finally:
            active_context_var.reset(token)

    async def _execute_step_inner(self, context: ExecutionContext) -> WorkflowResult:
        """The main loop for the Agentic engine."""
        await usage_guard.check_message_allowed(context)
        incoming = context.incoming_message or {}
        security_value = incoming.get("security_disposition", "none")
        security_disposition = str(getattr(security_value, "value", security_value) or "").strip()
        if (
            security_disposition != "none"
            or is_prompt_security_request(str(incoming.get("text") or ""))
        ):
            return WorkflowResult(
                response_text=SECURITY_REFUSAL_MESSAGE,
                next_state=context.session_data.state.workflow_state,
            )
        runtime_capabilities = await build_runtime_capability_snapshot(
            tenant_id=context.tenant.id,
            config_data=context.config.config_data,
            plan_code=context.tenant.plan_code,
            tenant_status=context.tenant.status,
            tenant_name=context.tenant.name,
        )
        context.runtime_behavior_profile = await resolve_runtime_behavior_profile(
            tenant_id=context.tenant.id,
            config_data=context.config.config_data,
            runtime_capability_snapshot=runtime_capabilities,
            channel=(context.incoming_message or {}).get("channel_type") or "whatsapp",
            conversation_state=context.session_data.state,
        )
        runtime_available_modules = runtime_capabilities.runtime_available_modules
        _set_runtime_available_modules(context, runtime_available_modules)
        if _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE:
            try:
                canonical_snapshot = turn_interpretation_coordinator.capture(context)
            except Exception as exc:
                canonical_snapshot = turn_interpretation_coordinator.fallback_snapshot(context)
                context.shadow_turn_evidence["semantic_failure"] = True
                logger.warning(
                    "turn_interpretation_failed",
                    extra={"extra_data": {"event": "turn_interpretation_failed", "reason_code": "snapshot_failed", "error_type": type(exc).__name__}},
                )
            (context.incoming_message or {})["_turn_interpretation_snapshot"] = canonical_snapshot
        if runtime_capabilities.is_available("order"):
            await self._hydrate_order_catalog(context)
        set_tool_context(context)

        # ----------------------------------------------------
        # Auto-Execution Gate (LLM-Commit-Dependency Fix)
        # ----------------------------------------------------
        last_tool_result_before_auto = context.session_data.state.action_ledger.last_tool_result
        unavailable_workflow_result = _unavailable_active_workflow_response(
            context,
            set(runtime_available_modules),
        )
        if unavailable_workflow_result:
            return unavailable_workflow_result
        pending_action_result = await self._handle_semantic_pending_action(context)
        if pending_action_result:
            return pending_action_result
        # ----------------------------------------------------

        user_message = (context.incoming_message or {}).get("text", "")
        ledger = context.session_data.state.action_ledger
        # Detect pending disambiguation across all active modules
        _active_mod_for_disambig = ledger.current_task or context.session_data.current_module
        _has_pending_disambig = bool(
            _active_mod_for_disambig
            and isinstance(
                (context.session_data.state.scoped_step_data.get(_active_mod_for_disambig) or {}).get(
                    "pending_disambiguation"
                ),
                dict,
            )
            and (context.session_data.state.scoped_step_data.get(_active_mod_for_disambig) or {}).get(
                "pending_disambiguation", {}
            ).get("options")
        )
        routing_decision = message_router.route_message(
            user_message,
            context.config.config_data,
            current_task=ledger.current_task,
            current_module=context.session_data.current_module,
            has_pending_disambiguation=_has_pending_disambig,
        )
        _record_shadow_evidence(context, "routing_decision", routing_decision)

        # Non-authoritative, observational-only pre-mutation configured-turn
        # shadow. Runs after routing evidence and before any booking mutation,
        # but never influences routing, collection, or customer-visible output.
        # The existing booking runtime remains fully authoritative.
        await capture_global_configured_turn_shadow(context)

        # Non-authoritative, observational-only pre-mutation configured-turn
        # shadow for an already-active order with a customer-facing item_name.
        # Runs after routing evidence and before any order mutation, but never
        # influences routing, collection, item resolution, or customer-visible
        # output. The existing order runtime remains fully authoritative.
        await capture_order_configured_turn_shadow(context)

        # Non-authoritative, observational-only pre-mutation configured-turn
        # shadow for an already-active lead_capture continuation. Runs after
        # routing evidence and before any lead field mutation, but never
        # influences routing, collection, activation, or customer-visible output.
        # The existing lead runtime remains fully authoritative.
        await capture_lead_configured_turn_shadow(context)

        # Authoritative configured-field precedence.  This must stay before
        # the turn-interpretation gateway and canonical RESUME_ACTIVE /
        # DEFER_TO_GENERAL handling: a value for the current booking field is
        # owned by the configured-field coordinator, never general control.
        service_selection_response = await self._handle_booking_service_selection(context, routing_decision)
        if service_selection_response is not None:
            return service_selection_response

        global_response = await self._handle_global_collection(context, routing_decision)
        if global_response is not None:
            return global_response

        configured_booking_response = await self._handle_configured_booking_collection(context, routing_decision)
        if configured_booking_response is not None:
            return configured_booking_response

        turn_mode = _turn_interpretation_mode()
        turn_snapshot = (
            context.incoming_message or {}
        ).get("_turn_interpretation_snapshot")
        exact_extraction: Any = None

        if (
            turn_mode != TurnInterpretationMode.LEGACY
            and turn_snapshot is not None
        ):
            exact_extraction = (
                _configured_option_extraction(
                    context,
                    runtime_available_modules,
                )
            )
            if exact_extraction is not None:
                _record_shadow_evidence(
                    context,
                    "field_extraction",
                    exact_extraction,
                )

            if (
                turn_mode
                == TurnInterpretationMode.AUTHORITATIVE
            ):
                pre_semantic_frame = (
                    context.turn_interpretation
                )
            else:
                pre_semantic_frame = (
                    turn_interpretation_coordinator.interpret_turn(
                        turn_snapshot,
                        evidence=(
                            context.shadow_turn_evidence
                        ),
                        runtime_available_modules=(
                            runtime_available_modules
                        ),
                    )
                )

            incoming_message = (
                context.incoming_message or {}
            )
            source_type_value = incoming_message.get(
                "source_type"
            )
            source_type = str(
                getattr(
                    source_type_value,
                    "value",
                    source_type_value,
                )
                or ""
            ).strip().casefold()

            gateway_eligibility_facts = (
                _build_turn_interpretation_gateway_eligibility_facts(
                    mode=turn_mode,
                    gateway_enabled=bool(
                        settings.LLM_GATEWAY_ENABLED
                    ),
                    customer_text=user_message,
                    customer_authored=source_type in {
                        "awa_chat",
                        "webhook",
                    },
                    orientation_message=False,
                    trusted_internal_event=(
                        source_type == "internal_event"
                    ),
                    trusted_otp_continuation=False,
                    pending_action_exists=bool(
                        ledger.pending_action
                        or turn_snapshot.pending_action
                    ),
                    exact_configured_option_resolved=(
                        exact_extraction is not None
                    ),
                    field_extraction=exact_extraction,
                    exact_safe_control_resolved=(
                        _has_exact_safe_turn_control(
                            pre_semantic_frame
                        )
                    ),
                )
            )

            if _should_invoke_turn_interpretation_gateway(
                **gateway_eligibility_facts
            ):
                configured_public_labels = (
                    _collect_turn_interpretation_public_labels(
                        config_data=(
                            context.config.config_data
                        ),
                        runtime_available_modules=set(
                            runtime_available_modules
                        ),
                        active_workflow=(
                            turn_snapshot.active_workflow
                        ),
                    )
                )
                semantic_input = (
                    _build_turn_interpretation_semantic_input(
                        customer_text=user_message,
                        channel_category=(
                            turn_snapshot.channel
                        ),
                        active_workflow=(
                            turn_snapshot.active_workflow
                        ),
                        requested_fields=list(
                            turn_snapshot.requested_fields
                        ),
                        workflow_status=(
                            turn_snapshot.pending_status
                        ),
                        pending_action_exists=bool(
                            ledger.pending_action
                            or turn_snapshot.pending_action
                        ),
                        runtime_available_modules=set(
                            runtime_available_modules
                        ),
                        configured_public_labels=(
                            configured_public_labels
                        ),
                        routing_decision=(
                            context.shadow_turn_evidence.get(
                                "routing_decision"
                            )
                            or routing_decision
                        ),
                    )
                )

                try:
                    (
                        semantic_payload,
                        _semantic_gateway_result,
                    ) = await invoke_turn_interpretation_gateway(
                        semantic_input,
                        gateway=None,
                    )
                except Exception as exc:
                    logger.warning(
                        "turn_interpretation_gateway_failed",
                        extra={
                            "extra_data": {
                                "event": (
                                    "turn_interpretation_gateway_failed"
                                ),
                                "reason_code": (
                                    "gateway_exception"
                                ),
                                "error_type": (
                                    type(exc).__name__
                                ),
                            }
                        },
                    )
                else:
                    if semantic_payload is None:
                        logger.warning(
                            "turn_interpretation_gateway_failed",
                            extra={
                                "extra_data": {
                                    "event": (
                                        "turn_interpretation_gateway_failed"
                                    ),
                                    "reason_code": (
                                        "provider_or_parse_failure"
                                    ),
                                }
                            },
                        )
                    else:
                        (
                            validated_semantic_payload,
                            semantic_validation_reason,
                        ) = (
                            validate_turn_interpretation_gateway_payload(
                                semantic_payload,
                                semantic_input,
                            )
                        )

                        if (
                            validated_semantic_payload
                            is None
                        ):
                            logger.info(
                                "turn_interpretation_gateway_rejected",
                                extra={
                                    "extra_data": {
                                        "event": (
                                            "turn_interpretation_gateway_rejected"
                                        ),
                                        "reason_code": (
                                            semantic_validation_reason
                                        ),
                                    }
                                },
                            )
                        else:
                            semantic_gateway_evidence = (
                                build_turn_interpretation_gateway_evidence(
                                    validated_semantic_payload
                                )
                            )
                            _record_shadow_evidence(
                                context,
                                "semantic_gateway_evidence",
                                semantic_gateway_evidence,
                            )

        if _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE and context.turn_plan is not None:
            plan = context.turn_plan
            canonical_intent = plan.candidate_workflow or plan.active_workflow or routing_decision.intent
            routing_decision = RoutingDecision(
                intent=(routing_decision.intent if plan.tangent_requested else canonical_intent),
                confidence=float(getattr(context.turn_interpretation, "confidence", routing_decision.confidence)),
                route_type=(
                    "direct_answer" if plan.response_path in {"capability_menu", "greeting"}
                    else "direct_action" if plan.tangent_requested
                    else "clarification" if plan.response_path == "clarification"
                    else "active_task" if plan.active_workflow and plan.preserve_active_workflow
                    else routing_decision.route_type
                ),
                is_tangent=plan.tangent_requested,
                should_clear_active_task=plan.switch_requested,
                reason=routing_decision.reason,
            )
        routed_intent = routing_decision.intent
        secondary_workflows = tuple(
            str(workflow)
            for workflow in getattr(
                context.turn_plan,
                "secondary_workflows",
                (),
            )
            if str(workflow)
        )
        canonical_control = None
        if (
            _turn_interpretation_mode() == TurnInterpretationMode.AUTHORITATIVE
            and context.turn_interpretation is not None
            and context.turn_plan is not None
        ):
            canonical_control = decide_canonical_control(
                frame=context.turn_interpretation,
                plan=context.turn_plan,
                active_workflow=ledger.current_task or context.session_data.current_module,
                has_pending_action=False,
            )
            if canonical_control.action == CanonicalControlAction.CANCEL_ACTIVE:
                _cancel_active_task(context)
                return WorkflowResult(
                    response_text="Okay, I've cancelled this request. What would you like to do next?",
                    next_state=ConversationState.CANCELLED,
                )
            if canonical_control.action == CanonicalControlAction.RESUME_ACTIVE:
                return self._active_workflow_resume_response(context)
            if canonical_control.action == CanonicalControlAction.SHOW_CAPABILITIES:
                menu = build_capability_menu(
                    context.config.config_data,
                    business_name=context.tenant.name,
                    available_modules=runtime_available_modules,
                    behavior_profile_resolution=getattr(context, "runtime_behavior_profile", None),
                    menu_requested=True,
                )
                result = WorkflowResult(
                    response_text=menu.text,
                    quick_replies=menu.suggestions,
                    next_state=_collecting_state(context.session_data.state.workflow_state),
                )
                if canonical_control.preserve_active_workflow:
                    result = _preserve_active_workflow_direct_response(
                        context,
                        result,
                        routing_decision,
                        resume_result=self._active_workflow_resume_response(context),
                    )
                return result
            if (
                canonical_control.action == CanonicalControlAction.HANDLE_NEW_WORKFLOW
                and canonical_control.workflow == "human_handoff"
                and runtime_capabilities.is_available("human_handoff")
            ):
                handoff_state = context.session_data.state
                handoff_ledger = handoff_state.action_ledger
                active_before_handoff = handoff_ledger.current_task or context.session_data.current_module
                status_before_handoff = handoff_ledger.task_status
                error_before_handoff = handoff_ledger.last_error
                module_before_handoff = context.session_data.current_module
                step_data_before_handoff = dict(handoff_state.step_data)
                handoff_scope_before = handoff_state.scoped_step_data.get("human_handoff")
                if not active_before_handoff:
                    _switch_active_task(context, "human_handoff")
                    handoff_state.update_task_step_data(
                        "human_handoff",
                        {"reason": "Customer requested a human handoff"},
                    )
                else:
                    handoff_state.step_data = {"reason": "Customer requested a human handoff"}
                    handoff_state.scoped_step_data["human_handoff"] = dict(handoff_state.step_data)
                handoff_result = await self.tool_registry.execute_tool("human_handoff.execute", context)
                channel_type = (context.incoming_message or {}).get("channel_type")
                handoff_state.record_tool_result(handoff_result, channel_type=channel_type)
                response_text = (
                    handoff_result.user_message
                    or "I couldn't connect you to the team right now. Please try again shortly."
                )
                if active_before_handoff:
                    handoff_ledger.current_task = active_before_handoff
                    handoff_ledger.task_status = status_before_handoff
                    handoff_ledger.last_error = error_before_handoff
                    context.session_data.current_module = module_before_handoff
                    handoff_state.step_data = step_data_before_handoff
                    if handoff_scope_before is None:
                        handoff_state.scoped_step_data.pop("human_handoff", None)
                    else:
                        handoff_state.scoped_step_data["human_handoff"] = handoff_scope_before
                    response_text = f"{response_text} I kept your current request details."
                return WorkflowResult(
                    response_text=response_text,
                    quick_replies=(
                        ["Wait for agent", "Add more details"]
                        if handoff_result.success
                        else ["Try again"]
                    ),
                    next_state=context.session_data.state.workflow_state,
                )

        if _has_active_workflow_state(context) and _is_grounded_active_tangent(context):
            tangent_response = self._active_workflow_tangent_response(context, routing_decision)
            if tangent_response is not None:
                return tangent_response

        routing_decision, fresh_lead_activated = await self._activate_fresh_lead_from_semantic_contract(
            context,
            routing_decision,
            canonical_control,
            runtime_available_modules,
        )
        if fresh_lead_activated:
            # The pre-route capture above is intentionally active-lead-only. After
            # this state-owned activation, capture the current turn once using the
            # established configured-lead gateway for the newly active scope.
            await capture_lead_configured_turn_shadow(context)
        routed_intent = routing_decision.intent

        canonical_booking_activation = bool(
            canonical_control is not None
            and canonical_control.action == CanonicalControlAction.HANDLE_NEW_WORKFLOW
            and canonical_control.workflow == "booking"
        )
        provisional_booking_activation = (
            not _has_active_workflow_state(context)
            and routed_intent == "booking"
            and runtime_capabilities.is_available("booking")
            and (
                routing_decision.reason == "configured_service_booking_candidate"
                or canonical_booking_activation
            )
        )
        if provisional_booking_activation:
            context.session_data.current_module = "booking"
            context.session_data.state.activate_task_step_data("booking")
            context.session_data.state.action_ledger.task_status = "collecting"
            # A fresh rich booking can contain the configured service and all
            # remaining booking values in its first turn. Run the same
            # configuration-scoped service/field sequence now that the
            # provisional scope exists, before the legacy extractor can reduce
            # the turn to one prompt-focused field.
            fresh_service_response = await self._handle_booking_service_selection(
                context, routing_decision
            )
            if fresh_service_response is not None:
                return fresh_service_response

        provisional_order_activation = (
            not _has_active_workflow_state(context)
            and routed_intent == "order"
            and runtime_capabilities.is_available("order")
            and canonical_control is not None
            and canonical_control.action == CanonicalControlAction.HANDLE_NEW_WORKFLOW
            and canonical_control.workflow == "order"
        )
        if provisional_order_activation:
            context.session_data.current_module = "order"
            context.session_data.state.activate_task_step_data("order")
            context.session_data.state.action_ledger.task_status = "collecting"

        booking_state_before = context.session_data.state.get_task_step_data("booking")
        run_legacy_semantic_sequence = (
            canonical_control is None
            or canonical_control.action == CanonicalControlAction.HANDLE_NEW_WORKFLOW
        )
        if canonical_control is not None and canonical_control.action == CanonicalControlAction.HANDLE_ACTIVE_FIELDS:
            active_handler = {
                "order": self._handle_semantic_order_field_extraction,
                "booking": self._handle_semantic_booking_field_extraction,
                "lead_capture": self._handle_semantic_lead_field_extraction,
                "support": self._handle_semantic_support_field_extraction,
                "reminders": self._handle_semantic_reminder_field_extraction,
                "reviews": self._handle_semantic_review_field_extraction,
                "payments": self._handle_semantic_payment_field_extraction,
                "payment": self._handle_semantic_payment_field_extraction,
            }.get(ledger.current_task or context.session_data.current_module or "")
            if active_handler is not None:
                # Iteration 6C authoritative multi-field order continuation. When
                # both authoritative order flags are enabled and the Iteration 6B
                # shadow produced a backend-validated candidate batch, apply every
                # accepted non-item field atomically and continue through the
                # existing post-collection flow. This path owns mutation for the
                # turn; the legacy semantic extractor and prefill are skipped so
                # the same message cannot be stored twice.
                if ledger.current_task == "order" or context.session_data.current_module == "order":
                    order_apply_response = await self._try_authoritative_order_configured_turn_apply(context)
                    if order_apply_response is not None:
                        return order_apply_response
                active_response = await active_handler(context, routing_decision)
                if active_response:
                    return active_response
        elif run_legacy_semantic_sequence:
            active_ambiguity_response = await self._handle_semantic_active_workflow_ambiguity(context, routing_decision)
            if active_ambiguity_response:
                return active_ambiguity_response

            # Iteration 6C authoritative multi-field order continuation (legacy
            # sequence path). Apply validated non-item candidates atomically
            # before the legacy semantic extractor so the same message is never
            # interpreted twice for the active order turn.
            order_apply_response = await self._try_authoritative_order_configured_turn_apply(context)
            if order_apply_response is not None:
                return order_apply_response

            order_semantic_response = await self._handle_semantic_order_field_extraction(context, routing_decision)
            if order_semantic_response:
                if provisional_order_activation and not context.session_data.state.get_task_step_data("order"):
                    context.session_data.current_module = None
                    context.session_data.state.scoped_step_data.pop("order", None)
                    context.session_data.state.step_data = {}
                    context.session_data.state.action_ledger.current_task = None
                    context.session_data.state.action_ledger.task_status = "idle"
                else:
                    return order_semantic_response
            booking_semantic_response = await self._handle_semantic_booking_field_extraction(context, routing_decision)
            if booking_semantic_response:
                if (
                    provisional_booking_activation
                    and not canonical_booking_activation
                    and not context.session_data.state.get_task_step_data("booking")
                ):
                    context.session_data.current_module = None
                    context.session_data.state.scoped_step_data.pop("booking", None)
                    context.session_data.state.step_data = {}
                    context.session_data.state.action_ledger.current_task = None
                    context.session_data.state.action_ledger.task_status = "idle"
                elif (
                    not secondary_workflows
                    or context.session_data.state.action_ledger.pending_action
                    is not None
                ):
                    return booking_semantic_response
            lead_semantic_response = await self._handle_semantic_lead_field_extraction(context, routing_decision)
            if lead_semantic_response:
                return lead_semantic_response
            support_semantic_response = await self._handle_semantic_support_field_extraction(context, routing_decision)
            if support_semantic_response:
                return support_semantic_response
            reminder_semantic_response = await self._handle_semantic_reminder_field_extraction(context, routing_decision)
            if reminder_semantic_response:
                return reminder_semantic_response
            review_semantic_response = await self._handle_semantic_review_field_extraction(context, routing_decision)
            if review_semantic_response:
                return review_semantic_response
            payment_semantic_response = await self._handle_semantic_payment_field_extraction(context, routing_decision)
            if payment_semantic_response:
                return payment_semantic_response
        booking_interpreter_owned_turn = (
            context.session_data.state.get_task_step_data("booking") != booking_state_before
        )
        if (
            canonical_control is not None
            and (canonical_booking_activation or booking_interpreter_owned_turn)
            and _has_active_booking_scope(context)
            and context.session_data.state.action_ledger.pending_action is None
            and not secondary_workflows
        ):
            return self._active_workflow_resume_response(context)

        if (
            routing_decision.should_clear_active_task
            and routing_decision.intent != "cancel"
            and not booking_interpreter_owned_turn
        ):
            return _active_workflow_switch_confirmation(context)

        # Prefill from message / context for active module if applicable
        active_module_name = context.session_data.state.action_ledger.current_task or context.session_data.current_module
        if active_module_name:
            try:
                semantic_prefill_owned = (
                    (active_module_name == "order" and not _has_pending_order_disambiguation(context))
                    or active_module_name == "booking"
                    or active_module_name in {"lead_capture", "support"}
                    or active_module_name in {"reminders", "reviews"}
                    or active_module_name in {"payments", "payment"}
                )
                if not semantic_prefill_owned:
                    tool_key = self.tool_registry.get_tool_key_for_module(active_module_name)
                    if tool_key:
                        tool_def = self.tool_registry.get_tool(tool_key)
                        if tool_def.module and hasattr(tool_def.module, "prefill_from_message"):
                            tool_def.module.prefill_from_message(user_message, context)
            except Exception as e:
                logger.warning(f"Failed to prefill for active module {active_module_name}: {e}")

        routed_direct_response = _direct_response_for_route(context, routing_decision, runtime_available_modules)
        if routed_direct_response:
            routed_direct_response = _preserve_active_workflow_direct_response(
                context,
                routed_direct_response,
                routing_decision,
                resume_result=self._active_workflow_resume_response(context),
            )
            _skip, skip_reason = _rag_skip_decision(context, routing_decision)
            _trace_direct_bypass(
                context,
                routing_decision,
                routed_direct_response,
                rag_skip_reason=skip_reason or "deterministic_direct",
            )
            return routed_direct_response
        direct_response = build_direct_action_response(
            context,
            user_message,
            available_modules=runtime_available_modules,
        )
        if direct_response:
            next_state = context.session_data.state.workflow_state
            if next_state == ConversationState.NEW:
                next_state = ConversationState.COLLECTING_DATA
            result = WorkflowResult(
                response_text=direct_response.text,
                quick_replies=direct_response.quick_replies,
                next_state=next_state,
            )
            result = _preserve_active_workflow_direct_response(
                context,
                result,
                routing_decision,
                resume_result=self._active_workflow_resume_response(context),
            )
            _trace_direct_bypass(context, routing_decision, result)
            return result
        canonical_unknown = bool(
            canonical_control is not None
            and canonical_control.action == CanonicalControlAction.DEFER_TO_GENERAL
            and getattr(context.turn_interpretation, "turn_kind", None) == "unknown"
        )
        if not canonical_unknown and should_send_discovery_menu(context, routed_intent, user_message):
            menu = build_capability_menu(
                context.config.config_data,
                business_name=context.tenant.name,
                available_modules=runtime_available_modules,
                behavior_profile_resolution=getattr(context, "runtime_behavior_profile", None),
                menu_requested=is_explicit_capability_request(user_message),
            )
            next_state = context.session_data.state.workflow_state
            if next_state == ConversationState.NEW:
                next_state = ConversationState.COLLECTING_DATA
            result = WorkflowResult(
                response_text=menu.text,
                quick_replies=menu.suggestions,
                next_state=next_state,
            )
            _trace_direct_bypass(context, routing_decision, result, rag_skip_reason="greeting")
            return result
        memory_context = await build_memory_context(context, routing_decision=routing_decision)
        from backend.knowledge.orchestrator import knowledge_orchestrator
        current_turn_tool_success = (
            context.session_data.state.action_ledger.last_tool_result is not last_tool_result_before_auto
            and context.session_data.state.action_ledger.last_tool_result is not None
            and context.session_data.state.action_ledger.last_tool_result.success is True
        )
        rag_skipped, rag_skip_reason = _rag_skip_decision(
            context,
            routing_decision,
            current_turn_tool_success=current_turn_tool_success,
        )
        if rag_skipped:
            saved_knowledge_context = RAG_EMPTY_CONTEXT
        else:
            knowledge_result = await knowledge_orchestrator.build_saved_knowledge_context(context, user_message, limit=3)
            saved_knowledge_context = knowledge_result.context_text
        matched_business_context = build_matched_business_context(
            context.config.config_data,
            user_message,
            intent=routing_decision.intent,
            route_type=routing_decision.route_type,
            is_tangent=routing_decision.is_tangent,
            current_task=context.session_data.state.action_ledger.current_task or context.session_data.current_module,
            available_modules=runtime_available_modules,
            additional_intents=secondary_workflows,
        )
        knowledge_sections = []
        if secondary_workflows:
            knowledge_sections.append(
                "Additional customer objectives in this same message: "
                + ", ".join(secondary_workflows)
                + ". Answer grounded side questions while preserving the primary workflow."
            )
        normalized_customer_turn = str(user_message or "").casefold()
        asks_hours = any(
            token in normalized_customer_turn
            for token in ("hours", "open", "close", "timing")
        )
        asks_price = any(
            token in normalized_customer_turn
            for token in ("price", "cost", "how much", "fee")
        )
        side_faq_has_authoritative_match = (
            "Relevant configured FAQs:" in matched_business_context
            or (
                asks_hours
                and "Opening hours" in matched_business_context
            )
            or (
                asks_price
                and (
                    "Relevant services:" in matched_business_context
                    or "Relevant catalog items:" in matched_business_context
                )
            )
        )
        if "faq" in secondary_workflows and not side_faq_has_authoritative_match:
            knowledge_sections.append(
                "Grounding constraint for the customer's side question: no authoritative "
                "tenant fact matched it. Explicitly say that the requested business "
                "information is not verified in the current details and offer a safe way "
                "to check. Do not infer or invent a policy, price, schedule, coverage, or "
                "availability. Continue the primary workflow without discarding it."
            )
        if matched_business_context:
            knowledge_sections.append(
                "Live catalog/services/hours/FAQ match (source priority 3; overrides retrieved knowledge/RAG if conflicting):\n"
                f"{matched_business_context}"
            )
        knowledge_sections.append(
            "Retrieved knowledge/RAG (source priority 4; use only when live tenant data has no conflicting fact):\n"
            f"{saved_knowledge_context}"
        )
        knowledge_context = "\n\n".join(knowledge_sections)
        try:
            review_settings = await review_service.get_review_settings(context.tenant.id)
        except Exception:
            review_settings = None
        tools = await self.tool_registry.get_llm_tools_for_context(
            context,
            intent=routing_decision.intent,
            route_type=routing_decision.route_type,
            current_task=context.session_data.state.action_ledger.current_task or context.session_data.current_module,
            is_tangent=routing_decision.is_tangent,
        )
        system_instructions = build_system_instructions(
            context,
            memory_context,
            knowledge_context,
            self.tool_registry,
            review_settings,
            tools,
            runtime_available_modules=runtime_available_modules,
        )
        core_agent_mode = resolve_core_agent_gateway_mode(settings)
        core_trace_id = current_safe_trace_id()
        llm_with_tools = None
        if core_agent_mode != CoreAgentGatewayMode.AUTHORITATIVE:
            from backend.logic.engine.llm import get_llm, run_provider_fallback

            try:
                llm = get_llm()
            except Exception as exc:
                emit_safe_llm_log(
                    level="WARNING", event="legacy_core_provider_unavailable",
                    purpose="tool_selection" if tools else "knowledge_answer",
                    provider=safe_identifier(settings.AGENT_LLM_PROVIDER, maximum=40),
                    model=safe_identifier(settings.AGENT_LLM_MODEL), outcome="unavailable",
                    error_code=safe_llm_error_code(exception_type=type(exc).__name__),
                    trace_id=core_trace_id,
                )
                return await run_provider_fallback(context, system_instructions, user_message)
            if tools:
                llm_with_tools = llm.bind_tools(tools)
            else:
                llm_with_tools = llm
        builder = StateGraph(AgentState)
        async def call_model(state: AgentState):
            messages = state["messages"]
            model_start = perf_counter()
            authoritative_tool_result = int(state.get("tool_call_count", 0) or 0) > 0
            if core_agent_mode != CoreAgentGatewayMode.AUTHORITATIVE:
                emit_safe_llm_log(
                    event="legacy_core_model_call",
                    purpose=(
                        "response_composition" if authoritative_tool_result
                        else "tool_selection" if tools else "knowledge_answer"
                    ),
                    provider=safe_identifier(
                        getattr(llm_with_tools, "provider", settings.AGENT_LLM_PROVIDER), maximum=40
                    ),
                    model=safe_identifier(
                        getattr(llm_with_tools, "model_name", settings.AGENT_LLM_MODEL)
                    ),
                    message_count=min(len(messages), 64),
                    tool_call_count=min(int(state.get("tool_call_count", 0) or 0), 64),
                    outcome="started",
                    trace_id=core_trace_id,
                )
            fallback_text = _core_model_fallback_text(
                state["context"],
                authoritative_tool_result=authoritative_tool_result,
            )
            try:
                response = await invoke_core_agent_model(
                    messages,
                    tools,
                    mode=core_agent_mode,
                    authoritative_tool_result=authoritative_tool_result,
                    fallback_text=fallback_text,
                    legacy_model=llm_with_tools,
                )
            except Exception as exc:
                latency_ms = int((perf_counter() - model_start) * 1000)
                log_llm_input_trace(build_llm_input_trace(
                    context=state["context"],
                    provider=getattr(llm_with_tools, "provider", settings.AGENT_LLM_PROVIDER),
                    model=getattr(llm_with_tools, "model_name", settings.AGENT_LLM_MODEL),
                    integration_mode=settings.AWA_INTEGRATION_MODE,
                    system_instructions=system_instructions,
                    tools=tools,
                    memory_context=memory_context,
                    knowledge_context=knowledge_context,
                    route_intent=routing_decision.intent,
                    route_type=routing_decision.route_type,
                    rag_skipped=rag_skipped,
                    rag_skip_reason=rag_skip_reason,
                    latency_ms=latency_ms,
                    provider_failure={
                        "provider": getattr(llm_with_tools, "provider", settings.AGENT_LLM_PROVIDER),
                        "model": getattr(llm_with_tools, "model_name", settings.AGENT_LLM_MODEL),
                        "error_type": type(exc).__name__,
                        "redacted": True,
                    },
                    final_output_type="safe_error",
                    trace_id=core_trace_id,
                ))
                raise
            latency_ms = int((perf_counter() - model_start) * 1000)
            response_metadata = getattr(response, "response_metadata", {}) or {}
            gateway_provider = response_metadata.get("awa_gateway_provider")
            gateway_model = response_metadata.get("model_name")
            trace_tools = (
                []
                if authoritative_tool_result and core_agent_mode == CoreAgentGatewayMode.AUTHORITATIVE
                else tools
            )
            log_llm_input_trace(build_llm_input_trace(
                context=state["context"],
                provider=gateway_provider or getattr(llm_with_tools, "provider", settings.AGENT_LLM_PROVIDER),
                model=gateway_model or getattr(llm_with_tools, "model_name", settings.AGENT_LLM_MODEL),
                integration_mode=settings.AWA_INTEGRATION_MODE,
                system_instructions=system_instructions,
                tools=trace_tools,
                memory_context=memory_context,
                knowledge_context=knowledge_context,
                route_intent=routing_decision.intent,
                route_type=routing_decision.route_type,
                rag_skipped=rag_skipped,
                rag_skip_reason=rag_skip_reason,
                latency_ms=latency_ms,
                fallback_used=bool(response_metadata.get("awa_gateway_fallback_used")),
                final_output_type=final_output_type(response),
                trace_id=core_trace_id,
            ))
            if core_agent_mode == CoreAgentGatewayMode.AUTHORITATIVE:
                usage = response_metadata.get("awa_gateway_usage")
                if isinstance(usage, dict):
                    state["context"].record_llm_usage({
                        "provider": gateway_provider,
                        "model": gateway_model,
                        "prompt_tokens": usage.get("input_tokens"),
                        "completion_tokens": usage.get("output_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                        "usage_available": any(usage.get(key) is not None for key in ("input_tokens", "output_tokens", "total_tokens")),
                        "cost_available": False,
                        "source": "llm_gateway",
                        "missing_reason": None,
                    })
            else:
                record_langchain_usage(state["context"], response)
            return {"messages": [response]}
        async def call_tools(state: AgentState):
            messages = state["messages"]
            last_message = messages[-1]
            tool_responses = []
            tool_call_count = int(state.get("tool_call_count", 0) or 0)
            tool_call_signatures = list(state.get("tool_call_signatures", []) or [])
            for tool_call in getattr(last_message, "tool_calls", []):
                tool_name = tool_call["name"]
                tool_key = self.tool_registry.get_tool_key_for_llm_name(tool_name)
                if not tool_key:
                    logger.warning(f"Unknown LLM tool requested: {tool_name}")
                    tool_responses.append(
                        ToolMessage(
                            content=f"Tool {tool_name} is not available.",
                            tool_call_id=tool_call["id"],
                        )
                    )
                    continue
                args = tool_call["args"]
                signature = _tool_call_signature(tool_key, args)
                tool_call_count += 1
                max_calls = MAX_TOOL_CALLS_PER_TURN
                if tool_call_count > max_calls:
                    safe_message = "I need to pause this action for safety. Please confirm the next step or ask for a person."
                    channel_type = (state["context"].incoming_message or {}).get("channel_type")
                    state["context"].session_data.state.record_safe_error(
                        code="agent_tool_call_limit",
                        safe_message=safe_message,
                        channel_type=channel_type,
                    )
                    logger.warning(
                        "Agent tool call limit reached",
                        extra={"extra_data": {
                            "tool_key": tool_key,
                            "tool_call_count": tool_call_count,
                            "conversation_id": str(state["context"].session_data.conversation_id),
                        }},
                    )
                    tool_responses.append(ToolMessage(
                        content=json.dumps(
                            {
                                "success": False,
                                "tool_key": tool_key,
                                "denied_reason": "agent_tool_call_limit",
                                "user_message": safe_message,
                            },
                            ensure_ascii=True,
                        ),
                        tool_call_id=tool_call["id"],
                    ))
                    continue
                if signature in tool_call_signatures:
                    safe_message = "I already tried that exact action in this turn. Please change a detail, confirm, or ask for a person."
                    channel_type = (state["context"].incoming_message or {}).get("channel_type")
                    state["context"].session_data.state.record_safe_error(
                        code="duplicate_tool_call",
                        safe_message=safe_message,
                        channel_type=channel_type,
                    )
                    logger.warning(
                        "Duplicate agent tool call blocked",
                        extra={"extra_data": {
                            "tool_key": tool_key,
                            "tool_call_count": tool_call_count,
                            "conversation_id": str(state["context"].session_data.conversation_id),
                        }},
                    )
                    tool_responses.append(ToolMessage(
                        content=json.dumps(
                            {
                                "success": False,
                                "tool_key": tool_key,
                                "denied_reason": "duplicate_tool_call",
                                "user_message": safe_message,
                            },
                            ensure_ascii=True,
                        ),
                        tool_call_id=tool_call["id"],
                    ))
                    continue
                tool_call_signatures.append(signature)
                try:
                    module_name = tool_key.split(".", 1)[0]
                    state["context"].session_data.state.update_task_step_data(module_name, args)
                    pending_before_dispatch = state["context"].session_data.state.action_ledger.pending_action
                    pending_identity = (
                        pending_before_dispatch.model_copy(deep=True)
                        if pending_before_dispatch
                        and pending_before_dispatch.tool_key == tool_key
                        and pending_before_dispatch.args_hash
                        == state["context"].session_data.state.action_args_hash(args)
                        else None
                    )
                    result = await self.tool_registry.execute_tool(tool_key, state["context"])
                    channel_type = (state["context"].incoming_message or {}).get("channel_type")
                    state["context"].session_data.state.record_tool_result(
                        result,
                        channel_type=channel_type,
                        action_nonce=pending_identity.action_nonce if pending_identity else None,
                        args_hash=pending_identity.args_hash if pending_identity else None,
                    )
                    content = result.to_tool_message()
                    logger.info(
                        "Agent tool result",
                        extra={"extra_data": {
                            "tool_key": tool_key,
                            "success": result.success,
                            "denied_reason": result.denied_reason,
                            "tool_call_count": tool_call_count,
                            "conversation_id": str(state["context"].session_data.conversation_id),
                        }},
                    )
                except Exception as e:
                    logger.error(f"Error executing tool {tool_key}: {e}")
                    safe_message = "I couldn't complete that action right now."
                    channel_type = (state["context"].incoming_message or {}).get("channel_type")
                    state["context"].session_data.state.record_safe_error(
                        code="tool_failed",
                        safe_message=safe_message,
                        channel_type=channel_type,
                    )
                    content = json.dumps(
                        {
                            "success": False,
                            "tool_key": tool_key,
                            "denied_reason": "tool_failed",
                            "user_message": safe_message,
                        },
                        ensure_ascii=True,
                    )
                tool_responses.append(ToolMessage(content=content, tool_call_id=tool_call["id"]))
            return {
                "messages": tool_responses,
                "tool_call_count": tool_call_count,
                "tool_call_signatures": tool_call_signatures,
            }
        def should_continue(state: AgentState):
            messages = state["messages"]
            last_message = messages[-1]
            if getattr(last_message, "tool_calls", None):
                return "tools"
            return "__end__"
        builder.add_node("agent", call_model)
        builder.add_node("tools", call_tools)
        builder.set_entry_point("agent")
        builder.add_conditional_edges("agent", should_continue, {"tools": "tools", "__end__": END})
        builder.add_edge("tools", "agent")
        graph = builder.compile()
        history_dicts = sanitize_agentic_history_dicts(context.session_data.state.agentic_history or [])
        context.session_data.state.agentic_history = history_dicts
        history_messages = messages_from_dict(history_dicts)
        initial_state = {
            "messages": [
                SystemMessage(content=system_instructions)
            ] + history_messages + [
                HumanMessage(content=user_message)
            ],
            "context": context,
            "tool_call_count": 0,
            "tool_call_signatures": [],
        }
        try:
            recursion_limit = MAX_AGENT_RECURSION_LIMIT
            final_state = await graph.ainvoke(initial_state, config={"recursion_limit": recursion_limit})
        except Exception as exc:
            if core_agent_mode == CoreAgentGatewayMode.AUTHORITATIVE:
                emit_safe_llm_log(
                    level="WARNING", event="legacy_core_inference_failure",
                    purpose="tool_selection", outcome="error",
                    error_code=safe_llm_error_code(exception_type=type(exc).__name__),
                    trace_id=core_trace_id,
                )
                return WorkflowResult(
                    response_text=_core_model_fallback_text(
                        context,
                        authoritative_tool_result=context.session_data.state.action_ledger.last_tool_result is not None,
                    ),
                    next_state=context.session_data.state.workflow_state,
                    composer_eligible=False,
                )
            emit_safe_llm_log(
                level="WARNING", event="legacy_core_inference_failure",
                purpose="tool_selection" if tools else "knowledge_answer",
                provider=safe_identifier(settings.AGENT_LLM_PROVIDER, maximum=40),
                model=safe_identifier(settings.AGENT_LLM_MODEL), outcome="error",
                error_code=safe_llm_error_code(exception_type=type(exc).__name__),
                trace_id=core_trace_id,
            )
            from backend.logic.engine.llm import run_provider_fallback

            return await run_provider_fallback(context, system_instructions, user_message)
        messages = final_state["messages"]
        final_response = ""
        if messages:
            final_response = messages[-1].content
            context.session_data.state.agentic_history = serialize_agentic_history(messages)



        next_state = context.session_data.state.workflow_state
        if next_state == ConversationState.NEW:
            next_state = ConversationState.COLLECTING_DATA
        result = WorkflowResult(
            response_text=final_response,
            next_state=next_state,
            composer_eligible=False,
        )
        return _apply_tool_failure_truth_guard(context, result)
agentic_engine = AgenticEngine()
