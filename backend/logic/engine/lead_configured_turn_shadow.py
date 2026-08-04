"""Lead-only configured-turn shadow observation (Iteration 7B).

This module is NON-AUTHORITATIVE and OBSERVATIONAL ONLY. It invokes the
Iteration 2 dynamic configured-turn contract (``extract_global_workflow_turn``)
in lead-only shadow mode for an *already-active* lead_capture continuation, then
deterministically dry-run validates the safe tenant-configured lead candidates
and compares their exact keys with the field keys actually changed by the
existing authoritative lead runtime.

The shadow:
  * observes a pre-existing active lead_capture continuation only;
  * never activates lead capture;
  * never changes lead routing, first-message activation, or tool execution;
  * never mutates lead or customer-visible state;
  * never sends candidate values to the lead tool, collector, or response;
  * never logs candidate/existing/customer values;
  * never performs a fresh ``lead_capture`` router intent eligible;
  * never alters confirmation, OTP, customer assurance, or consent behavior.

It must never call ``update_task_step_data``, ``set_task_step_data``,
``activate_task_step_data``, ``clear_task``, ``set_prompt_focus``,
``prepare_pending_action``, ``tool_registry.execute_tool``, ``lead_capture.execute``,
or ``capture_lead``. The only permitted mutation is the ephemeral
``context.shadow_turn_evidence["lead_configured_turn_shadow"]`` location.

The existing lead runtime remains fully authoritative.

Supported safe configured-field types (existing lead backend validation is
established for these):
    string, textarea, phone, email

For semantic input only:
    textarea -> string

The original configured type is preserved for deterministic validation.

Explicitly excluded types (no new semantics added this iteration):
    number, select, date, time, boolean, object, array, nested values
"""
from __future__ import annotations

import copy
import re
from typing import Any, Optional

from backend.core.config import settings
from backend.core.context import ExecutionContext
from backend.logic.engine.field_extraction import extract_global_workflow_turn
from backend.modules.lead_capture_module import lead_capture_module
from backend.ops.logger import logger

SHADOW_EVIDENCE_KEY = "lead_configured_turn_shadow"

# Internal/sensitive configured-field keys the shadow must never forward. These
# are matched by exact key and by unsafe patterns (see _UNSAFE_KEY_PATTERNS).
_PROTECTED_LEAD_KEYS: frozenset[str] = frozenset({
    "lead_id",
    "tenant_id",
    "conversation_id",
    "customer_id",
    "assigned_to",
    "lead_status",
    "status",
    "source",
    "source_channel",
    "idempotency_key",
    "created_at",
    "updated_at",
    "captured_at",
    "metadata",
    "tool_name",
    "tool_args",
})

# Consent-sensitive concepts the shadow must never forward or log by name.
_CONSENT_LEAD_KEYS: frozenset[str] = frozenset({
    "consent",
    "marketing_consent",
    "privacy_consent",
    "privacy_acceptance",
    "contact_consent",
    "opt_in",
    "opt_out",
})

# Internal/sensitive configured-field key patterns the shadow must never forward.
_UNSAFE_KEY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"^id$",
        r".*_id$",
        r"^uuid$",
        r".*uuid.*$",
        r"^token$",
        r".*token.*$",
        r"^secret$",
        r".*secret.*$",
        r"^trace$",
        r".*trace.*$",
        r"^raw$",
        r".*raw.*$",
        r"^payload$",
        r".*payload.*$",
        r"^backend$",
        r".*backend.*$",
        r"^otp$",
        r".*otp.*$",
        r"^password$",
        r".*password.*$",
        r"^metadata$",
        r".*metadata.*$",
        r"^status$",
    ]
)

# Allowed bounded metadata properties forwarded to the semantic contract.
_ALLOWED_FIELD_PROPERTIES = (
    "key",
    "label",
    "type",
    "required",
    "description",
    "semantic_description",
    "validation_hint",
)

_KEY_LIMIT = 64
_LABEL_LIMIT = 200
_DESCRIPTION_LIMIT = 500
_VALIDATION_HINT_LIMIT = 300

# Lead configured-field types supported by the deterministic shadow validator.
# Unsupported types are excluded (not coerced). textarea maps to string only at
# the semantic boundary; the backend type is preserved separately.
_SUPPORTED_SEMANTIC_TYPES: frozenset[str] = frozenset({
    "string",
    "textarea",
    "phone",
    "email",
})

_COMPARISON_STATUSES = {
    "match",
    "shadow_superset",
    "legacy_superset",
    "disagreement",
    "no_candidates_no_legacy_change",
    "gateway_unavailable",
    "invalid_shadow_result",
    "workflow_transitioned",
    "validated_match",
    "validated_shadow_superset",
    "validated_legacy_superset",
    "validated_disagreement",
    "validated_empty",
    "validation_rejected",
}


def _is_unsafe_configured_key(key: str) -> bool:
    return any(pattern.search(key) for pattern in _UNSAFE_KEY_PATTERNS)


def _is_protected_lead_key(key: str) -> bool:
    return key in _PROTECTED_LEAD_KEYS or _is_unsafe_configured_key(key)


def _is_consent_lead_key(key: str) -> bool:
    if key in _CONSENT_LEAD_KEYS:
        return True
    lowered = key.casefold()
    return any(consent in lowered for consent in (
        "consent", "opt_in", "opt_out", "privacy_accept", "contact_consent",
    )) and lowered not in {"consent_form_url"}


def _bound_string(value: Any, maximum: int) -> str:
    if not isinstance(value, str):
        value = str(value)
    return value[:maximum]


def _shadow_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_LEAD_CONFIGURED_TURN_SHADOW", False))


def _customer_authored_source(context: ExecutionContext) -> bool:
    incoming = context.incoming_message or {}
    source_type_value = incoming.get("source_type")
    source_type = str(
        getattr(source_type_value, "value", source_type_value) or ""
    ).strip().casefold()
    return source_type in {"awa_chat", "webhook"}


def _is_trusted_internal_event(context: ExecutionContext) -> bool:
    incoming = context.incoming_message or {}
    source_type_value = incoming.get("source_type")
    source_type = str(
        getattr(source_type_value, "value", source_type_value) or ""
    ).strip().casefold()
    return source_type in {"internal_event"}


def _active_workflow_is_lead(context: ExecutionContext) -> bool:
    state = context.session_data.state
    ledger = state.action_ledger
    active_task = ledger.current_task or context.session_data.current_module
    return active_task == "lead_capture"


def _lead_module_enabled(context: ExecutionContext) -> bool:
    modules = (
        context.config.config_data.get("modules", {})
        if isinstance(context.config.config_data, dict)
        else {}
    )
    lead = modules.get("lead_capture") if isinstance(modules, dict) else None
    return isinstance(lead, dict) and bool(lead.get("enabled", False))


def _lead_scoped_state_exists(context: ExecutionContext) -> bool:
    state = context.session_data.state
    ledger = state.action_ledger
    if ledger.current_task == "lead_capture" or context.session_data.current_module == "lead_capture":
        return True
    scoped = state.scoped_step_data.get("lead_capture")
    return isinstance(scoped, dict) and bool(scoped)


def _lead_state_terminal(context: ExecutionContext) -> bool:
    ledger = context.session_data.state.action_ledger
    return ledger.task_status in {"completed", "cancelled"}


def _configured_lead_fields(context: ExecutionContext) -> list[dict]:
    """Resolve the configured lead field authority used by the lead tool definition.

    This uses the existing lead module's ``get_required_fields_for_context`` which
    merges tenant-configured ``modules.lead_capture.fields`` with the established
    default lead fields (name, phone, email, interest, preferred_contact_time).
    The hardcoded ``LeadGatewayPayload`` is deliberately not used as the authority.
    """
    try:
        fields = lead_capture_module.get_required_fields_for_context(context)
    except Exception:
        return []
    return fields if isinstance(fields, list) else []


def _lead_field_defs(context: ExecutionContext) -> dict[str, dict]:
    raw_fields = _configured_lead_fields(context)
    defs: dict[str, dict] = {}
    for field in raw_fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        if not key or key in defs:
            continue
        if _is_protected_lead_key(key) or _is_consent_lead_key(key):
            continue
        defs[key] = field
    return defs


def _safe_field_keys(context: ExecutionContext) -> tuple[list[str], int]:
    """Return (allowed configured field keys, excluded unsafe/consent key count)."""
    raw_fields = _configured_lead_fields(context)
    keys: list[str] = []
    excluded = 0
    for field in raw_fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        if not key:
            continue
        if key in keys:
            continue
        if _is_protected_lead_key(key) or _is_consent_lead_key(key):
            excluded += 1
            continue
        keys.append(key)
    return keys, excluded


def _has_pending_protected_action(context: ExecutionContext) -> bool:
    return context.session_data.state.action_ledger.pending_action is not None


def _is_otp_or_assurance_continuation(context: ExecutionContext) -> bool:
    """Exclude OTP and customer-assurance continuations from shadow eligibility."""
    incoming = context.incoming_message or {}
    source_type_value = incoming.get("source_type")
    source_type = str(
        getattr(source_type_value, "value", source_type_value) or ""
    ).strip().casefold()
    if source_type in {"internal_event"}:
        # Trusted internal events (OTP verified, assurance) are excluded by
        # _is_trusted_internal_event as well; this is an explicit secondary guard.
        return True
    message_text = str(incoming.get("text") or "").strip().casefold()
    assurance_markers = (
        "enter the code", "verify the code", "otp", "one-time password",
        "verification code", "i confirm", "confirmed",
    )
    return any(marker in message_text for marker in assurance_markers)


def _current_collected_and_missing(context: ExecutionContext) -> tuple[list[str], list[str]]:
    state = context.session_data.state
    current = state.get_task_step_data("lead_capture") or {}
    # Apply the existing name/customer_name compatibility shim for state-reading
    # only: a configured key may treat the other alias as already collected.
    def _effective_value(key: str) -> Any:
        value = current.get(key)
        if value not in (None, ""):
            return value
        if key == "name" and str(current.get("customer_name") or "").strip():
            return current.get("customer_name")
        if key == "customer_name" and str(current.get("name") or "").strip():
            return current.get("name")
        return None

    defs = _lead_field_defs(context)
    collected: list[str] = []
    missing: list[str] = []
    for key, field in defs.items():
        value = _effective_value(key)
        if value not in (None, "", [], {}):
            collected.append(key)
        else:
            required = field.get("required", True)
            if isinstance(required, bool) and required:
                missing.append(key)
    return collected, missing


def _focus_field_key(context: ExecutionContext) -> Optional[str]:
    focus = context.session_data.state.prompt_focus
    if focus is None or getattr(focus, "workflow", None) != "lead_capture":
        return None
    requested = getattr(focus, "requested_fields", None) or ()
    safe_keys = set(_lead_field_defs(context).keys())
    for field in requested:
        key = str(field or "").strip()
        if key and key in safe_keys:
            return key
    return None


def _build_bounded_field_schema(context: ExecutionContext) -> tuple[list[dict[str, Any]], int]:
    defs = _lead_field_defs(context)
    schema: list[dict[str, Any]] = []
    excluded_unsupported = 0
    for key, field in defs.items():
        backend_type = str(field.get("type") or "string").strip().lower()
        semantic_type = backend_type
        # textarea -> string only at the semantic boundary.
        if semantic_type == "textarea":
            semantic_type = "string"
        if semantic_type not in _SUPPORTED_SEMANTIC_TYPES:
            excluded_unsupported += 1
            continue
        allowed: dict[str, Any] = {"field_key": _bound_string(key, _KEY_LIMIT)}
        label = field.get("label") or field.get("name")
        if label:
            allowed["display_label"] = _bound_string(label, _LABEL_LIMIT)
        allowed["semantic_description"] = _bound_string(
            field.get("semantic_description") or field.get("description") or "",
            _DESCRIPTION_LIMIT,
        )
        allowed["data_type"] = _bound_string(semantic_type, 40)
        required = field.get("required")
        if isinstance(required, bool):
            allowed["required"] = required
        validation_hint = field.get("validation_hint")
        if validation_hint:
            allowed["validation_hint"] = _bound_string(
                validation_hint, _VALIDATION_HINT_LIMIT
            )
        # The original configured type is preserved for deterministic validation
        # and is never forwarded to the semantic contract.
        schema.append(allowed)
    return schema, excluded_unsupported


def is_shadow_eligible(context: ExecutionContext) -> bool:
    """Lead-only eligibility for the pre-mutation configured-turn shadow."""
    if not _shadow_enabled():
        return False
    incoming = context.incoming_message or {}
    customer_text = str(incoming.get("text") or "").strip()
    if not customer_text:
        return False
    if not _customer_authored_source(context):
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
    if _is_trusted_internal_event(context):
        return False
    if _is_otp_or_assurance_continuation(context):
        return False
    safe_keys, _ = _safe_field_keys(context)
    if not safe_keys:
        return False
    collected, missing = _current_collected_and_missing(context)
    if not missing:
        return False
    return True


def _build_semantic_input(context: ExecutionContext) -> dict[str, Any]:
    incoming = context.incoming_message or {}
    channel = str(incoming.get("channel_type") or "unknown").strip().lower()
    language = (
        str(incoming.get("detected_language") or "").strip().lower() or "en"
    )
    timezone = (
        context.config.config_data.get("timezone")
        if isinstance(context.config.config_data, dict)
        else None
    )
    if not timezone:
        timezone = "UTC"
    collected, missing = _current_collected_and_missing(context)
    focus = _focus_field_key(context)
    safe_schema, _ = _build_bounded_field_schema(context)
    safe_keys, _ = _safe_field_keys(context)
    config_version = getattr(context.session_data, "config_version", None)
    semantic_input: dict[str, Any] = {
        "customer_text": str(incoming.get("text") or "").strip(),
        "workflow": "lead_capture",
        "channel_category": channel,
        "language": language,
        "tenant_timezone": str(timezone),
        "configured_fields": safe_schema,
        "collected_field_keys": list(collected[:32]),
        "missing_required_field_keys": list(missing[:32]),
    }
    if focus is not None and focus in set(safe_keys):
        semantic_input["focus_field_key"] = focus
    return semantic_input


def _current_lead_step_data(context: ExecutionContext) -> dict[str, Any]:
    state = context.session_data.state
    return copy.deepcopy(state.get_task_step_data("lead_capture") or {})


def _make_snapshot(context: ExecutionContext, semantic_input: dict[str, Any]) -> dict[str, Any]:
    focus = _focus_field_key(context)
    safe_keys, excluded_unsafe = _safe_field_keys(context)
    safe_schema, excluded_unsupported = _build_bounded_field_schema(context)
    missing = list(semantic_input.get("missing_required_field_keys") or [])
    return {
        "workflow": "lead_capture",
        "config_version": getattr(context.session_data, "config_version", None),
        "channel": str(
            (context.incoming_message or {}).get("channel_type") or "unknown"
        ).strip().lower(),
        "focus_field_key": focus,
        "safe_configured_field_keys": list(safe_keys),
        "missing_required_field_keys": missing,
        "excluded_unsafe_field_count": excluded_unsafe,
        "excluded_unsupported_type_count": excluded_unsupported,
        "pre_turn_safe_field_values": _current_lead_step_data(context),
        "semantic_result": None,
        "validated_result": None,
    }


async def capture_lead_configured_turn_shadow(context: ExecutionContext) -> None:
    """Pre-mutation, non-authoritative shadow capture for an eligible lead turn.

    The result is stored only in the ephemeral ``context.shadow_turn_evidence``
    location. No authoritative state is mutated. Any failure is fail-open.
    """
    if not is_shadow_eligible(context):
        return
    try:
        semantic_input = _build_semantic_input(context)
        snapshot = _make_snapshot(context, semantic_input)
        shadow_result = await extract_global_workflow_turn(semantic_input)
        snapshot["semantic_result"] = shadow_result
        _capture_validated_shadow(context, snapshot)
        context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY] = snapshot
    except Exception as exc:  # pragma: no cover - defensive fail-open
        _emit_failure_event(context, stage="capture", error_type=type(exc).__name__)


def _extract_shadow_candidate_keys(snapshot: dict[str, Any]) -> list[str]:
    result = snapshot.get("semantic_result")
    if result is None:
        return []
    rejected = bool(getattr(result, "rejected", False))
    if rejected:
        return []
    candidates = getattr(result, "accepted_candidates", None) or ()
    keys = [
        str(getattr(candidate, "field_key", "") or "").strip()
        for candidate in candidates
        if str(getattr(candidate, "field_key", "") or "").strip()
    ]
    return sorted(set(keys))


def _build_candidate_map_from_shadow(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Map exact candidate key to candidate value from an accepted semantic result."""
    result = snapshot.get("semantic_result")
    if result is None:
        return {}
    if bool(getattr(result, "rejected", False)):
        return {}
    candidates = getattr(result, "accepted_candidates", None) or ()
    mapping: dict[str, Any] = {}
    for candidate in candidates:
        key = str(getattr(candidate, "field_key", "") or "").strip()
        if not key:
            continue
        value = getattr(candidate, "candidate_value", None)
        if value is None:
            continue
        mapping[key] = value
    return mapping


def _capture_validated_shadow(context: ExecutionContext, snapshot: dict[str, Any]) -> None:
    """Run the additive dry-run deterministic validation and store only ephemeral evidence."""
    candidate_map = _build_candidate_map_from_shadow(snapshot)
    accepted_keys: list[str] = []
    rejected_keys: list[str] = []
    reason_code: str | None = None
    accepted_patch: dict[str, Any] = {}
    try:
        validation = lead_capture_module.validate_external_configured_candidates(
            context, candidate_map
        )
        accepted_patch = dict(validation.accepted_patch)
        accepted_keys = list(validation.accepted_field_keys)
        rejected_keys = list(validation.rejected_field_keys)
        reason_code = validation.reason_code
    except Exception as exc:  # pragma: no cover - defensive fail-open
        _emit_failure_event(context, stage="deterministic_validation", error_type=type(exc).__name__)
    snapshot["validated_result"] = {
        "accepted_patch": accepted_patch,
        "accepted_field_keys": sorted(set(accepted_keys)),
        "rejected_field_keys": sorted(set(rejected_keys)),
        "reason_code": reason_code,
    }


def _compute_changed_keys(
    snapshot: dict[str, Any],
    context: ExecutionContext,
) -> list[str]:
    pre_values = snapshot.get("pre_turn_safe_field_values") or {}
    safe_keys = set(snapshot.get("safe_configured_field_keys") or [])
    post_values = context.session_data.state.get_task_step_data("lead_capture") or {}
    changed: list[str] = []
    for key in safe_keys:
        pre = pre_values.get(key)
        post = post_values.get(key)
        pre_empty = pre in (None, "", [], {})
        post_empty = post in (None, "", [], {})
        if pre_empty and not post_empty:
            changed.append(key)
        elif (not pre_empty) and (not post_empty) and pre != post:
            changed.append(key)
    return sorted(set(changed))


def _workflow_transitioned(context: ExecutionContext) -> bool:
    state = context.session_data.state
    ledger = state.action_ledger
    active_task = ledger.current_task or context.session_data.current_module
    if active_task != "lead_capture":
        return True
    if ledger.task_status in {"completed", "executing", "cancelled", "failed"}:
        return True
    return False


def _classify_comparison(
    shadow_candidate_keys: list[str],
    legacy_changed_keys: list[str],
    gateway_unavailable: bool,
    invalid_shadow_result: bool,
    workflow_transitioned: bool,
) -> str:
    if workflow_transitioned:
        return "workflow_transitioned"
    if invalid_shadow_result:
        return "invalid_shadow_result"
    if gateway_unavailable and not shadow_candidate_keys:
        return "gateway_unavailable"
    shadow_set = set(shadow_candidate_keys)
    legacy_set = set(legacy_changed_keys)
    if not shadow_set and not legacy_set:
        return "no_candidates_no_legacy_change"
    if shadow_set == legacy_set and shadow_set:
        return "match"
    if legacy_set < shadow_set:
        return "shadow_superset"
    if shadow_set < legacy_set:
        return "legacy_superset"
    return "disagreement"


def _classify_validated_comparison(
    validated_shadow_candidate_keys: list[str],
    legacy_changed_keys: list[str],
    gateway_unavailable: bool,
    invalid_shadow_result: bool,
    workflow_transitioned: bool,
    has_rejected: bool,
) -> str:
    if workflow_transitioned:
        return "workflow_transitioned"
    if invalid_shadow_result:
        return "invalid_shadow_result"
    if gateway_unavailable and not validated_shadow_candidate_keys:
        return "gateway_unavailable"
    if has_rejected and not validated_shadow_candidate_keys:
        return "validation_rejected"
    validated_set = set(validated_shadow_candidate_keys)
    legacy_set = set(legacy_changed_keys)
    if not validated_set and not legacy_set:
        return "validated_empty"
    if validated_set == legacy_set and validated_set:
        return "validated_match"
    if legacy_set < validated_set:
        return "validated_shadow_superset"
    if validated_set < legacy_set:
        return "validated_legacy_superset"
    return "validated_disagreement"


def _emit_observed_event(
    context: ExecutionContext,
    *,
    status: str,
    validated_status: str,
    focus_field_key: Optional[str],
    configured_field_count: int,
    missing_required_count: int,
    excluded_unsafe_field_count: int,
    excluded_unsupported_type_count: int,
    raw_shadow_candidate_keys: list[str],
    validated_shadow_candidate_keys: list[str],
    legacy_changed_keys: list[str],
    raw_matched_keys: list[str],
    raw_shadow_only_keys: list[str],
    raw_legacy_only_keys: list[str],
    validated_matched_keys: list[str],
    validated_shadow_only_keys: list[str],
    validated_legacy_only_keys: list[str],
    validation_rejected_keys: list[str],
    raw_candidate_count: int,
    validated_candidate_count: int,
    legacy_changed_count: int,
    gateway_unavailable: bool,
    shadow_rejected: bool,
    rejection_reason_code: str | None,
    validation_reason_code: str | None,
) -> None:
    logger.info(
        "lead_configured_turn_shadow_observed",
        extra={
            "extra_data": {
                "event": "lead_configured_turn_shadow_observed",
                "workflow": "lead_capture",
                "config_version": getattr(
                    context.session_data, "config_version", None
                ),
                "channel": str(
                    (context.incoming_message or {}).get("channel_type") or "unknown"
                ).strip().lower(),
                "status": status,
                "validated_status": validated_status,
                "focus_field_key": focus_field_key,
                "configured_field_count": configured_field_count,
                "missing_required_count": missing_required_count,
                "excluded_unsafe_field_count": excluded_unsafe_field_count,
                "excluded_unsupported_type_count": excluded_unsupported_type_count,
                "raw_shadow_candidate_keys": list(raw_shadow_candidate_keys),
                "validated_shadow_candidate_keys": list(validated_shadow_candidate_keys),
                "legacy_changed_keys": list(legacy_changed_keys),
                "raw_matched_keys": list(raw_matched_keys),
                "raw_shadow_only_keys": list(raw_shadow_only_keys),
                "raw_legacy_only_keys": list(raw_legacy_only_keys),
                "validated_matched_keys": list(validated_matched_keys),
                "validated_shadow_only_keys": list(validated_shadow_only_keys),
                "validated_legacy_only_keys": list(validated_legacy_only_keys),
                "validation_rejected_keys": list(validation_rejected_keys),
                "raw_candidate_count": int(raw_candidate_count),
                "validated_candidate_count": int(validated_candidate_count),
                "legacy_changed_count": int(legacy_changed_count),
                "gateway_unavailable": bool(gateway_unavailable),
                "shadow_rejected": bool(shadow_rejected),
                "rejection_reason_code": rejection_reason_code,
                "validation_reason_code": validation_reason_code,
            }
        },
    )


def _emit_failure_event(
    context: ExecutionContext,
    *,
    stage: str,
    error_type: str,
) -> None:
    logger.warning(
        "lead_configured_turn_shadow_failed",
        extra={
            "extra_data": {
                "event": "lead_configured_turn_shadow_failed",
                "workflow": "lead_capture",
                "stage": stage,
                "error_type": error_type,
            }
        },
    )


def _emit_lead_applied_event(
    context: ExecutionContext,
    *,
    config_version: Any,
    channel: str,
    applied_field_keys: list[str],
    remaining_required_field_keys: list[str],
    focus_field_key_before: Optional[str],
    focus_field_key_after: Optional[str],
) -> None:
    """Key-only telemetry for authoritative non-terminal lead field application.

    No customer text, candidate values, normalized values, existing values, labels,
    descriptions, validation hints, or tenant/conversation/session identifiers are
    logged. Sorted unique exact safe configured keys only.
    """
    logger.info(
        "lead_configured_turn_applied",
        extra={
            "extra_data": {
                "event": "lead_configured_turn_applied",
                "workflow": "lead_capture",
                "config_version": config_version,
                "channel": channel,
                "applied_field_keys": list(sorted(set(applied_field_keys))),
                "applied_field_count": len(set(applied_field_keys)),
                "remaining_required_field_keys": list(
                    sorted(set(remaining_required_field_keys))
                ),
                "remaining_required_count": len(set(remaining_required_field_keys)),
                "focus_field_key_before": focus_field_key_before,
                "focus_field_key_after": focus_field_key_after,
            }
        },
    )


async def observe_lead_configured_turn_shadow(
    context: ExecutionContext,
    result: Any,
) -> Any:
    """Post-runtime, fail-open comparison observer.

    Runs before response composition and localization. Never modifies the
    authoritative result. Returns the original result unchanged.
    """
    snapshot = context.shadow_turn_evidence.get(SHADOW_EVIDENCE_KEY)
    if snapshot is None:
        return result
    try:
        raw_shadow_candidate_keys = _extract_shadow_candidate_keys(snapshot)
        legacy_changed_keys = _compute_changed_keys(snapshot, context)
        shadow_set = set(raw_shadow_candidate_keys)
        legacy_set = set(legacy_changed_keys)
        raw_matched_keys = sorted(shadow_set & legacy_set)
        raw_shadow_only_keys = sorted(shadow_set - legacy_set)
        raw_legacy_only_keys = sorted(legacy_set - shadow_set)

        semantic_result = snapshot.get("semantic_result")
        gateway_unavailable = bool(
            getattr(semantic_result, "gateway_unavailable", False)
        )
        shadow_rejected = bool(getattr(semantic_result, "rejected", False))
        rejection_reason = getattr(semantic_result, "rejection_reason", None)
        invalid_shadow_result = shadow_rejected and not gateway_unavailable

        workflow_transitioned = _workflow_transitioned(context)
        status = _classify_comparison(
            raw_shadow_candidate_keys,
            legacy_changed_keys,
            gateway_unavailable,
            invalid_shadow_result,
            workflow_transitioned,
        )

        validated = snapshot.get("validated_result") or {}
        validated_shadow_candidate_keys = sorted(
            set(validated.get("accepted_field_keys") or [])
        )
        validation_rejected_keys = sorted(
            set(validated.get("rejected_field_keys") or [])
        )
        validation_reason_code = validated.get("reason_code")
        validated_set = set(validated_shadow_candidate_keys)
        validated_matched_keys = sorted(validated_set & legacy_set)
        validated_shadow_only_keys = sorted(validated_set - legacy_set)
        validated_legacy_only_keys = sorted(legacy_set - validated_set)
        validated_status = _classify_validated_comparison(
            validated_shadow_candidate_keys,
            legacy_changed_keys,
            gateway_unavailable,
            invalid_shadow_result,
            workflow_transitioned,
            bool(validation_rejected_keys) and not validated_shadow_candidate_keys,
        )

        _, excluded_unsafe = _safe_field_keys(context)
        _emit_observed_event(
            context,
            status=status,
            validated_status=validated_status,
            focus_field_key=snapshot.get("focus_field_key"),
            configured_field_count=len(
                snapshot.get("safe_configured_field_keys") or []
            ),
            missing_required_count=len(
                snapshot.get("missing_required_field_keys") or []
            ),
            excluded_unsafe_field_count=excluded_unsafe,
            excluded_unsupported_type_count=snapshot.get(
                "excluded_unsupported_type_count", 0
            ),
            raw_shadow_candidate_keys=raw_shadow_candidate_keys,
            validated_shadow_candidate_keys=validated_shadow_candidate_keys,
            legacy_changed_keys=legacy_changed_keys,
            raw_matched_keys=raw_matched_keys,
            raw_shadow_only_keys=raw_shadow_only_keys,
            raw_legacy_only_keys=raw_legacy_only_keys,
            validated_matched_keys=validated_matched_keys,
            validated_shadow_only_keys=validated_shadow_only_keys,
            validated_legacy_only_keys=validated_legacy_only_keys,
            validation_rejected_keys=validation_rejected_keys,
            raw_candidate_count=len(raw_shadow_candidate_keys),
            validated_candidate_count=len(validated_shadow_candidate_keys),
            legacy_changed_count=len(legacy_changed_keys),
            gateway_unavailable=gateway_unavailable,
            shadow_rejected=shadow_rejected,
            rejection_reason_code=rejection_reason,
            validation_reason_code=validation_reason_code,
        )
    except Exception as exc:  # pragma: no cover - defensive fail-open
        _emit_failure_event(
            context, stage="observe", error_type=type(exc).__name__
        )
    return result
