"""Pure canonical turn-control decisions and privacy-safe state diagnostics."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any

from backend.logic.engine.turn_interpretation import TurnInterpretation, TurnKind, TurnPlan


class CanonicalControlAction(str, Enum):
    DEFER_TO_PENDING = "defer_to_pending"
    CANCEL_ACTIVE = "cancel_active"
    RESUME_ACTIVE = "resume_active"
    ANSWER_TANGENT = "answer_tangent"
    SHOW_CAPABILITIES = "show_capabilities"
    HANDLE_ACTIVE_FIELDS = "handle_active_fields"
    HANDLE_NEW_WORKFLOW = "handle_new_workflow"
    DEFER_TO_GENERAL = "defer_to_general"


@dataclass(frozen=True)
class CanonicalControlDecision:
    action: CanonicalControlAction
    workflow: str | None
    preserve_active_workflow: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class StateInvariantViolation:
    code: str
    workflow: str | None = None
    field_names: tuple[str, ...] = ()


def _decision(
    action: CanonicalControlAction,
    workflow: str | None,
    preserve: bool,
    reason: str,
) -> CanonicalControlDecision:
    return CanonicalControlDecision(action, workflow, preserve, (reason,))


def decide_canonical_control(
    *,
    frame: TurnInterpretation,
    plan: TurnPlan,
    active_workflow: str | None,
    has_pending_action: bool,
) -> CanonicalControlDecision:
    """Choose a bounded response path from validated semantic evidence."""
    if has_pending_action:
        return _decision(
            CanonicalControlAction.DEFER_TO_PENDING,
            active_workflow,
            bool(active_workflow),
            "pending_action",
        )

    kind = frame.turn_kind
    if active_workflow:
        mapping = {
            TurnKind.CANCEL: (CanonicalControlAction.CANCEL_ACTIVE, False, "cancel_active"),
            TurnKind.CONTINUE: (CanonicalControlAction.RESUME_ACTIVE, True, "resume_active"),
            TurnKind.ACTIVE_WORKFLOW_ANSWER: (
                CanonicalControlAction.HANDLE_ACTIVE_FIELDS,
                True,
                "active_fields",
            ),
            TurnKind.TANGENT: (CanonicalControlAction.ANSWER_TANGENT, True, "active_tangent"),
            TurnKind.CAPABILITY_REQUEST: (
                CanonicalControlAction.SHOW_CAPABILITIES,
                True,
                "capability_request",
            ),
        }
        if kind == TurnKind.NEW_WORKFLOW_REQUEST:
            return _decision(
                CanonicalControlAction.HANDLE_NEW_WORKFLOW,
                plan.candidate_workflow,
                True,
                "new_workflow_candidate",
            )
        if kind in mapping:
            action, preserve, reason = mapping[kind]
            return _decision(action, active_workflow, preserve, reason)
        return _decision(
            CanonicalControlAction.DEFER_TO_GENERAL,
            active_workflow,
            True,
            "active_general",
        )

    if kind == TurnKind.NEW_WORKFLOW_REQUEST:
        return _decision(
            CanonicalControlAction.HANDLE_NEW_WORKFLOW,
            plan.candidate_workflow,
            False,
            "new_workflow_candidate",
        )
    if kind == TurnKind.CAPABILITY_REQUEST:
        return _decision(
            CanonicalControlAction.SHOW_CAPABILITIES,
            None,
            False,
            "capability_request",
        )
    return _decision(
        CanonicalControlAction.DEFER_TO_GENERAL,
        None,
        False,
        "general",
    )


def _workflow(value: Any) -> str | None:
    normalized = str(value or "").strip().casefold()
    aliases = {"payment": "payments", "reminder": "reminders", "review": "reviews", "lead": "lead_capture"}
    normalized = aliases.get(normalized, normalized)
    allowed = {
        "booking", "order", "payments", "lead_capture", "support",
        "reminders", "reviews", "faq", "human_handoff",
    }
    return normalized if normalized in allowed else None


def _safe_field_names(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return ()
    return tuple(sorted({
        value for value in values
        if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value)
    }))


def validate_canonical_state(context: Any) -> tuple[StateInvariantViolation, ...]:
    """Report bounded invariant metadata without repairing or serializing state values."""
    session = getattr(context, "session_data", None)
    state = getattr(session, "state", None)
    if session is None or state is None:
        return ()

    ledger = getattr(state, "action_ledger", None)
    current_task = _workflow(getattr(ledger, "current_task", None))
    current_module = _workflow(getattr(session, "current_module", None))
    active = current_task or current_module
    violations: list[StateInvariantViolation] = []

    if current_task and current_module and current_task != current_module:
        violations.append(StateInvariantViolation("task_module_mismatch", current_task))

    focus = getattr(state, "prompt_focus", None)
    if focus is not None:
        focus_workflow = _workflow(getattr(focus, "workflow", None))
        focus_fields = _safe_field_names(getattr(focus, "requested_fields", ()))
        if not active:
            violations.append(
                StateInvariantViolation("prompt_focus_without_active_workflow", focus_workflow, focus_fields)
            )
        elif focus_workflow != active:
            violations.append(
                StateInvariantViolation("prompt_focus_workflow_mismatch", focus_workflow, focus_fields)
            )

    pending = getattr(ledger, "pending_action", None)
    if pending is not None and current_task:
        pending_workflow = _workflow(str(getattr(pending, "tool_key", "") or "").split(".", 1)[0])
        if pending_workflow and pending_workflow != current_task:
            violations.append(StateInvariantViolation("pending_action_task_mismatch", pending_workflow))

    config = getattr(context, "config", None)
    if config is not None and getattr(session, "config_version", None) != getattr(config, "version", None):
        violations.append(StateInvariantViolation("config_binding_mismatch", active))

    scoped = getattr(state, "scoped_step_data", None)
    mirror = getattr(state, "step_data", None)
    active_scope = scoped.get(active) if active and isinstance(scoped, dict) else None
    if isinstance(active_scope, dict) and isinstance(mirror, dict) and active_scope != mirror:
        field_names = _safe_field_names(set(active_scope) | set(mirror))
        violations.append(StateInvariantViolation("active_scope_mirror_mismatch", active, field_names))

    return tuple(violations)
