"""Booking-only configured-turn shadow observation (Iteration 3).

This module is NON-AUTHORITATIVE and OBSERVATIONAL ONLY. It invokes the
Iteration 2 dynamic configured-turn contract (``extract_global_workflow_turn``)
in booking-only shadow mode *before* the booking-specific deterministic mutation,
then compares the contract's candidate field keys with the field keys actually
changed by the existing authoritative booking runtime.

It must never:
  * alter routing;
  * supply values to the booking collector;
  * mutate booking data;
  * alter prompt focus;
  * create or change a pending action;
  * execute tools;
  * influence confirmation, OTP, or customer assurance;
  * select a service;
  * alter a customer-visible response;
  * change workflow transitions;
  * change menu behavior;
  * become authoritative.

The existing booking runtime remains fully authoritative.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Optional

from backend.core.config import settings
from backend.core.context import ExecutionContext
from backend.logic.engine.configured_field_collection import (
    configured_field_collection_coordinator,
)
from backend.logic.engine.field_extraction import extract_global_workflow_turn
from backend.ops.logger import logger

SHADOW_EVIDENCE_KEY = "global_configured_turn_shadow"

# Internal/sensitive configured-field keys the shadow must never forward.
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
    ]
)

# Allowlisted configured-field metadata properties forwarded to the contract.
_ALLOWED_FIELD_PROPERTIES = (
    "key",
    "label",
    "type",
    "required",
    "options",
    "description",
    "semantic_description",
    "validation_hint",
)

_KEY_LIMIT = 64
_LABEL_LIMIT = 200
_DESCRIPTION_LIMIT = 500
_VALIDATION_HINT_LIMIT = 300
_OPTION_LIMIT = 40
_OPTION_VALUE_LIMIT = 200
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


_REJECTION_REASON_CODES = {
    "none",
    "gateway_unavailable",
    "invalid_payload",
    "unknown_key",
    "duplicate_key",
    "invalid_value",
    "unsupported_structure",
    "other_rejected",
    "unknown_field",
    "protected_field",
    "field_already_collected",
    "no_eligible_candidates",
}


def _is_unsafe_configured_key(key: str) -> bool:
    return any(pattern.search(key) for pattern in _UNSAFE_KEY_PATTERNS)


def _bound_string(value: Any, maximum: int) -> str:
    if not isinstance(value, str):
        value = str(value)
    return value[:maximum]


def _shadow_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_GLOBAL_CONFIGURED_TURN_SHADOW", False))


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
    return source_type == "internal_event"


def _active_workflow_is_booking(context: ExecutionContext) -> bool:
    state = context.session_data.state
    ledger = state.action_ledger
    active_task = ledger.current_task or context.session_data.current_module
    return active_task == "booking"


def _booking_module_enabled(context: ExecutionContext) -> bool:
    modules = (
        context.config.config_data.get("modules", {})
        if isinstance(context.config.config_data, dict)
        else {}
    )
    booking = modules.get("booking") if isinstance(modules, dict) else None
    return isinstance(booking, dict) and bool(booking.get("enabled", False))


def _configured_booking_fields(context: ExecutionContext) -> list[dict]:
    modules = (
        context.config.config_data.get("modules", {})
        if isinstance(context.config.config_data, dict)
        else {}
    )
    booking = modules.get("booking") if isinstance(modules, dict) else None
    fields = (
        booking.get("fields")
        if isinstance(booking, dict)
        else None
    )
    return fields if isinstance(fields, list) else []


def _safe_field_keys(context: ExecutionContext) -> tuple[list[str], int]:
    """Return (allowed configured field keys, excluded unsafe key count)."""
    raw_fields = _configured_booking_fields(context)
    keys: list[str] = []
    excluded = 0
    for field in raw_fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or field.get("name") or "").strip()
        if not key:
            continue
        if key in keys:
            continue
        if _is_unsafe_configured_key(key):
            excluded += 1
            continue
        keys.append(key)
    return keys, excluded


def _booking_field_defs(context: ExecutionContext) -> dict[str, dict]:
    raw_fields = _configured_booking_fields(context)
    defs: dict[str, dict] = {}
    for field in raw_fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or field.get("name") or "").strip()
        if not key or key in defs:
            continue
        if _is_unsafe_configured_key(key):
            continue
        defs[key] = field
    return defs


def _build_bounded_field_schema(context: ExecutionContext) -> list[dict[str, Any]]:
    defs = _booking_field_defs(context)
    schema: list[dict[str, Any]] = []
    for key, field in defs.items():
        allowed: dict[str, Any] = {"field_key": _bound_string(key, _KEY_LIMIT)}
        label = field.get("label") or field.get("name")
        if label:
            allowed["display_label"] = _bound_string(label, _LABEL_LIMIT)
        ftype = field.get("type")
        if ftype:
            allowed["data_type"] = _bound_string(ftype, 40)
        required = field.get("required")
        if isinstance(required, bool):
            allowed["required"] = required
        options = field.get("options")
        if isinstance(options, list):
            allowed["allowed_options"] = tuple(
                _bound_string(option, _OPTION_VALUE_LIMIT)
                for option in options[:_OPTION_LIMIT]
                if str(option).strip()
            )
        description = field.get("description") or field.get("semantic_description")
        if description:
            allowed["semantic_description"] = _bound_string(
                description, _DESCRIPTION_LIMIT
            )
        validation_hint = field.get("validation_hint")
        if validation_hint:
            allowed["validation_hint"] = _bound_string(
                validation_hint, _VALIDATION_HINT_LIMIT
            )
        schema.append(allowed)
    return schema


def _current_collected_and_missing(context: ExecutionContext) -> tuple[list[str], list[str]]:
    state = context.session_data.state
    current = state.get_task_step_data("booking") or {}
    defs = _booking_field_defs(context)
    collected: list[str] = []
    missing: list[str] = []
    for key, field in defs.items():
        value = current.get(key)
        if value not in (None, "", [], {}):
            collected.append(key)
        else:
            required = field.get("required", True)
            if isinstance(required, bool) and required:
                missing.append(key)
    return collected, missing


def _focus_field_key(context: ExecutionContext) -> Optional[str]:
    focus = context.session_data.state.prompt_focus
    if focus is None or getattr(focus, "workflow", None) != "booking":
        return None
    requested = getattr(focus, "requested_fields", None) or ()
    safe_keys = set(_booking_field_defs(context).keys())
    for field in requested:
        key = str(field or "").strip()
        if key and key in safe_keys:
            return key
    return None


def is_shadow_eligible(context: ExecutionContext) -> bool:
    """Booking-only eligibility for the pre-mutation configured-turn shadow."""
    if not _shadow_enabled():
        return False
    incoming = context.incoming_message or {}
    customer_text = str(incoming.get("text") or "").strip()
    if not customer_text:
        return False
    if not _customer_authored_source(context):
        return False
    if not _active_workflow_is_booking(context):
        return False
    if not _booking_module_enabled(context):
        return False
    safe_keys, _ = _safe_field_keys(context)
    if not safe_keys:
        return False
    collected, missing = _current_collected_and_missing(context)
    if not missing:
        return False
    ledger = context.session_data.state.action_ledger
    if ledger.pending_action is not None:
        return False
    if _is_trusted_internal_event(context):
        return False
    # OTP / internal continuation markers are trusted internal events; the
    # existing runtime already gates them via source_type. Nothing additional
    # is invented here.
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
    safe_schema = _build_bounded_field_schema(context)
    safe_keys, _ = _safe_field_keys(context)
    config_version = getattr(context.session_data, "config_version", None)
    semantic_input: dict[str, Any] = {
        "customer_text": str(incoming.get("text") or "").strip(),
        "workflow": "booking",
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


def _current_booking_step_data(context: ExecutionContext) -> dict[str, Any]:
    state = context.session_data.state
    return copy.deepcopy(state.get_task_step_data("booking") or {})


def _make_snapshot(context: ExecutionContext, semantic_input: dict[str, Any]) -> dict[str, Any]:
    state = context.session_data.state
    focus = _focus_field_key(context)
    safe_keys, _ = _safe_field_keys(context)
    return {
        "workflow": "booking",
        "config_version": getattr(context.session_data, "config_version", None),
        "channel_category": str(
            (context.incoming_message or {}).get("channel_type") or "unknown"
        ).strip().lower(),
        "focus_field_key": focus,
        "configured_safe_field_keys": list(safe_keys),
        "missing_required_field_keys": list(
            semantic_input.get("missing_required_field_keys") or []
        ),
        "pre_turn_configured_field_values": _current_booking_step_data(context),
        "semantic_shadow_result": None,
        "validated_shadow_patch": {},
        "validated_shadow_candidate_keys": [],
        "validation_rejected_keys": [],
        "validation_reason_code": None,
    }


async def capture_global_configured_turn_shadow(context: ExecutionContext) -> None:
    """Pre-mutation, non-authoritative shadow capture for an eligible booking turn.

    The result is stored only in the ephemeral ``context.shadow_turn_evidence``
    location. No authoritative state is mutated. Any failure is fail-open.
    """
    if not is_shadow_eligible(context):
        return
    try:
        semantic_input = _build_semantic_input(context)
        snapshot = _make_snapshot(context, semantic_input)
        shadow_result = await extract_global_workflow_turn(semantic_input)
        snapshot["semantic_shadow_result"] = shadow_result
        context.shadow_turn_evidence[SHADOW_EVIDENCE_KEY] = snapshot
        _capture_validated_shadow(context, snapshot)
    except Exception as exc:  # pragma: no cover - defensive fail-open
        _emit_failure_event(context, stage="capture", error_type=type(exc).__name__)
        logger.warning(
            "global_configured_turn_shadow_capture_failed",
            extra={
                "extra_data": {
                    "event": "global_configured_turn_shadow_failed",
                    "workflow": "booking",
                    "stage": "capture",
                    "error_type": type(exc).__name__,
                }
            },
        )


def _extract_shadow_candidate_keys(snapshot: dict[str, Any]) -> list[str]:
    result = snapshot.get("semantic_shadow_result")
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
    """Map exact candidate key to candidate value from an accepted semantic result.

    Only Iteration 2 accepted candidates contribute. The deterministic
    validator independently enforces exact-key, missing, and protected rules.
    """
    result = snapshot.get("semantic_shadow_result")
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
    """Run the additive dry-run deterministic validation and store only ephemeral evidence.

    The validated patch is stored ONLY inside the ephemeral snapshot. It is
    never written into authoritative booking state, never logged, never sent
    to a customer, never added to an event, and never supplied to a tool.
    """
    candidate_map = _build_candidate_map_from_shadow(snapshot)
    patch: dict[str, Any] = {}
    accepted_keys: list[str] = []
    rejected_keys: list[str] = []
    reason_code: str | None = None
    try:
        validation = configured_field_collection_coordinator.validate_external_candidates(
            context, candidate_map
        )
        patch = dict(validation.accepted_patch)
        accepted_keys = list(validation.accepted_field_keys)
        rejected_keys = list(validation.rejected_field_keys)
        reason_code = validation.reason_code
    except Exception as exc:  # pragma: no cover - defensive fail-open
        _emit_failure_event(context, stage="deterministic_validation", error_type=type(exc).__name__)
        logger.warning(
            "global_configured_turn_shadow_validation_failed",
            extra={
                "extra_data": {
                    "event": "global_configured_turn_shadow_failed",
                    "workflow": "booking",
                    "stage": "deterministic_validation",
                    "error_type": type(exc).__name__,
                }
            },
        )
    snapshot["validated_shadow_patch"] = patch
    snapshot["validated_shadow_candidate_keys"] = sorted(set(accepted_keys))
    snapshot["validation_rejected_keys"] = sorted(set(rejected_keys))
    snapshot["validation_reason_code"] = reason_code


def _compute_changed_keys(
    snapshot: dict[str, Any],
    context: ExecutionContext,
) -> list[str]:
    pre_values = snapshot.get("pre_turn_configured_field_values") or {}
    safe_keys = set(snapshot.get("configured_safe_field_keys") or [])
    post_values = context.session_data.state.get_task_step_data("booking") or {}
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
    if active_task != "booking":
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


def _map_rejection_reason_code(reason: Optional[str]) -> str:
    if not reason:
        return "none"
    lowered = str(reason).lower()
    if "gateway" in lowered or "unavailable" in lowered:
        return "gateway_unavailable"
    if "invalid" in lowered and ("payload" in lowered or "result" in lowered):
        return "invalid_payload"
    if "not configured" in lowered or "unknown" in lowered:
        return "unknown_key"
    if "duplicate" in lowered:
        return "duplicate_key"
    if "malformed" in lowered or "out of range" in lowered or "confidence" in lowered:
        return "invalid_value"
    if "no configured fields" in lowered:
        return "unsupported_structure"
    if "rejected" in lowered:
        return "other_rejected"
    return "other_rejected"


def _emit_observed_event(
    context: ExecutionContext,
    *,
    status: str,
    focus_field_key: Optional[str],
    configured_field_count: int,
    missing_required_count: int,
    excluded_unsafe_field_count: int,
    shadow_candidate_keys: list[str],
    legacy_changed_keys: list[str],
    matched_keys: list[str],
    shadow_only_keys: list[str],
    legacy_only_keys: list[str],
    gateway_unavailable: bool,
    shadow_rejected: bool,
    rejection_reason_code: str,
    raw_shadow_candidate_keys: list[str] | None = None,
    validated_shadow_candidate_keys: list[str] | None = None,
    validated_matched_keys: list[str] | None = None,
    validated_shadow_only_keys: list[str] | None = None,
    validated_legacy_only_keys: list[str] | None = None,
    validated_shadow_candidate_count: int = 0,
    validation_rejected_keys: list[str] | None = None,
    validation_rejected_count: int = 0,
    validation_reason_code: str | None = None,
    validated_comparison_status: str | None = None,
) -> None:
    logger.info(
        "global_configured_turn_shadow_observed",
        extra={
            "extra_data": {
                "event": "global_configured_turn_shadow_observed",
                "workflow": "booking",
                "config_version": getattr(
                    context.session_data, "config_version", None
                ),
                "channel": str(
                    (context.incoming_message or {}).get("channel_type") or "unknown"
                ).strip().lower(),
                "status": status,
                "focus_field_key": focus_field_key,
                "configured_field_count": configured_field_count,
                "missing_required_count": missing_required_count,
                "excluded_unsafe_field_count": excluded_unsafe_field_count,
                "shadow_candidate_keys": list(shadow_candidate_keys),
                "legacy_changed_keys": list(legacy_changed_keys),
                "matched_keys": list(matched_keys),
                "shadow_only_keys": list(shadow_only_keys),
                "legacy_only_keys": list(legacy_only_keys),
                "shadow_candidate_count": len(shadow_candidate_keys),
                "legacy_changed_count": len(legacy_changed_keys),
                "gateway_unavailable": bool(gateway_unavailable),
                "shadow_rejected": bool(shadow_rejected),
                "rejection_reason_code": rejection_reason_code,
                "raw_shadow_candidate_keys": list(raw_shadow_candidate_keys or shadow_candidate_keys),
                "validated_shadow_candidate_keys": list(validated_shadow_candidate_keys or []),
                "validated_matched_keys": list(validated_matched_keys or []),
                "validated_shadow_only_keys": list(validated_shadow_only_keys or []),
                "validated_legacy_only_keys": list(validated_legacy_only_keys or []),
                "validated_shadow_candidate_count": int(validated_shadow_candidate_count),
                "validation_rejected_keys": list(validation_rejected_keys or []),
                "validation_rejected_count": int(validation_rejected_count),
                "validation_reason_code": validation_reason_code,
                "validated_comparison_status": validated_comparison_status,
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
        "global_configured_turn_shadow_failed",
        extra={
            "extra_data": {
                "event": "global_configured_turn_shadow_failed",
                "workflow": "booking",
                "stage": stage,
                "error_type": error_type,
            }
        },
    )


def _emit_booking_applied_event(
    context: ExecutionContext,
    *,
    config_version: Any,
    channel: str,
    applied_field_keys: list[str],
    remaining_required_field_keys: list[str],
    focus_field_key_before: Optional[str],
    focus_field_key_after: Optional[str],
) -> None:
    """Key-only telemetry for authoritative multi-field booking application.

    No customer text, candidate values, normalized values, existing values,
    evidence text, labels, descriptions, option values, validation hints, or
    tenant/conversation/session identifiers are logged.
    """
    logger.info(
        "global_configured_turn_booking_applied",
        extra={
            "extra_data": {
                "event": "global_configured_turn_booking_applied",
                "workflow": "booking",
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


async def observe_global_configured_turn_shadow(
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
        shadow_candidate_keys = _extract_shadow_candidate_keys(snapshot)
        legacy_changed_keys = _compute_changed_keys(snapshot, context)
        shadow_set = set(shadow_candidate_keys)
        legacy_set = set(legacy_changed_keys)
        matched_keys = sorted(shadow_set & legacy_set)
        shadow_only_keys = sorted(shadow_set - legacy_set)
        legacy_only_keys = sorted(legacy_set - shadow_set)

        shadow_result = snapshot.get("semantic_shadow_result")
        gateway_unavailable = bool(
            getattr(shadow_result, "gateway_unavailable", False)
        )
        shadow_rejected = bool(getattr(shadow_result, "rejected", False))
        rejection_reason = getattr(shadow_result, "rejection_reason", None)
        invalid_shadow_result = shadow_rejected and not gateway_unavailable

        workflow_transitioned = _workflow_transitioned(context)
        status = _classify_comparison(
            shadow_candidate_keys,
            legacy_changed_keys,
            gateway_unavailable,
            invalid_shadow_result,
            workflow_transitioned,
        )
        reason_code = _map_rejection_reason_code(rejection_reason)

        # Validated (deterministic) shadow comparison. The Iteration 3 raw
        # semantic comparison above is preserved unchanged.
        validated_shadow_candidate_keys = sorted(
            set(snapshot.get("validated_shadow_candidate_keys") or [])
        )
        validated_rejected_keys = sorted(
            set(snapshot.get("validation_rejected_keys") or [])
        )
        validation_reason_code = snapshot.get("validation_reason_code")
        validated_set = set(validated_shadow_candidate_keys)
        validated_matched_keys = sorted(validated_set & legacy_set)
        validated_shadow_only_keys = sorted(validated_set - legacy_set)
        validated_legacy_only_keys = sorted(legacy_set - validated_set)
        validated_comparison_status = _classify_validated_comparison(
            validated_shadow_candidate_keys,
            legacy_changed_keys,
            gateway_unavailable,
            invalid_shadow_result,
            workflow_transitioned,
            bool(validated_rejected_keys),
        )

        _, excluded = _safe_field_keys(context)
        _emit_observed_event(
            context,
            status=status,
            focus_field_key=snapshot.get("focus_field_key"),
            configured_field_count=len(
                snapshot.get("configured_safe_field_keys") or []
            ),
            missing_required_count=len(
                snapshot.get("missing_required_field_keys") or []
            ),
            excluded_unsafe_field_count=excluded,
            shadow_candidate_keys=shadow_candidate_keys,
            legacy_changed_keys=legacy_changed_keys,
            matched_keys=matched_keys,
            shadow_only_keys=shadow_only_keys,
            legacy_only_keys=legacy_only_keys,
            gateway_unavailable=gateway_unavailable,
            shadow_rejected=shadow_rejected,
            rejection_reason_code=reason_code,
            raw_shadow_candidate_keys=shadow_candidate_keys,
            validated_shadow_candidate_keys=validated_shadow_candidate_keys,
            validated_matched_keys=validated_matched_keys,
            validated_shadow_only_keys=validated_shadow_only_keys,
            validated_legacy_only_keys=validated_legacy_only_keys,
            validated_shadow_candidate_count=len(validated_shadow_candidate_keys),
            validation_rejected_keys=validated_rejected_keys,
            validation_rejected_count=len(validated_rejected_keys),
            validation_reason_code=validation_reason_code,
            validated_comparison_status=validated_comparison_status,
        )
    except Exception as exc:  # pragma: no cover - defensive fail-open
        _emit_failure_event(
            context, stage="observe", error_type=type(exc).__name__
        )
        logger.warning(
            "global_configured_turn_shadow_observe_failed",
            extra={
                "extra_data": {
                    "event": "global_configured_turn_shadow_failed",
                    "workflow": "booking",
                    "stage": "observe",
                    "error_type": type(exc).__name__,
                }
            },
        )
    return result


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
