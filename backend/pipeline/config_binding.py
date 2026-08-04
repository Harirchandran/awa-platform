"""Channel-neutral config binding policy for real customer turns."""
from dataclasses import dataclass
from typing import Optional

from backend.core.context import ConversationSession


class ConfigBindingError(RuntimeError):
    """Safe retriable failure while selecting authoritative config state."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ConfigBindingDecision:
    version: int
    reason_code: str
    persist_required: bool = False


def session_has_active_work(session: ConversationSession) -> bool:
    state = session.state
    ledger = state.action_ledger
    if ledger.current_task or session.current_module or ledger.pending_action or state.current_workflow_id:
        return True
    if state.prompt_focus is not None or bool(state.step_data):
        return True
    return any(bool(values) for values in (state.scoped_step_data or {}).values() if isinstance(values, dict))


def state_payload_has_active_work(
    state_payload: object,
    *,
    current_module: object = None,
    current_intent: object = None,
) -> bool:
    """Apply the pinning policy to a persisted session-state envelope.

    Public-chat intake owns the accepted-turn transaction, before a
    ``ConversationSession`` object is hydrated.  Keep this predicate aligned
    with ``session_has_active_work`` so an idle session can refresh atomically
    while workflow and protected-action state remains pinned.
    """
    if current_module or current_intent or not isinstance(state_payload, dict):
        return bool(current_module or current_intent) or not isinstance(state_payload, dict)
    state = state_payload.get("state")
    if not isinstance(state, dict):
        return True
    ledger = state.get("action_ledger")
    if isinstance(ledger, dict) and (ledger.get("current_task") or ledger.get("pending_action")):
        return True
    if state.get("current_workflow_id") or state.get("prompt_focus"):
        return True
    if state.get("step_data"):
        return True
    scoped = state.get("scoped_step_data")
    return isinstance(scoped, dict) and any(bool(value) for value in scoped.values() if isinstance(value, dict))


def resolve_config_binding(
    session: ConversationSession,
    *,
    active_version: Optional[int],
) -> ConfigBindingDecision:
    pinned = int(session.config_version or 0)
    if session.state.orientation_config_pending:
        if pinned < 1:
            raise ConfigBindingError("pinned_config_unavailable")
        return ConfigBindingDecision(pinned, "config_pinned_orientation_first_turn", True)
    if session_has_active_work(session):
        if pinned < 1:
            raise ConfigBindingError("pinned_config_unavailable")
        return ConfigBindingDecision(pinned, "config_pinned_active_workflow")
    if not active_version or int(active_version) < 1:
        raise ConfigBindingError("active_config_unavailable")
    selected = int(active_version)
    return ConfigBindingDecision(
        selected,
        "config_refreshed_idle_session" if selected != pinned else "config_retained_idle_session",
        selected != pinned,
    )
