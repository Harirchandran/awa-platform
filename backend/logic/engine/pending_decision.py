from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from backend.core.context import ExecutionContext
from backend.core.config import settings
from backend.logic.engine.llm import record_langchain_usage
from backend.ops.logger import logger

PendingActionLabel = Literal["confirm", "cancel", "change", "continue", "tangent", "unknown"]

VALID_PENDING_ACTIONS = {"confirm", "cancel", "change", "continue", "tangent", "unknown"}


@dataclass(frozen=True)
class PendingActionDecision:
    action: PendingActionLabel = "unknown"
    confidence: float = 0.0
    field_updates: Dict[str, Any] = field(default_factory=dict)
    clarification: Optional[str] = None


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
        key: value
        for key in language_keys
        for value in [incoming.get(key) or config_data.get(key)]
        if value not in (None, "", [], {})
    }


def _decision_payload(context: ExecutionContext) -> Dict[str, Any]:
    state = context.session_data.state
    ledger = state.action_ledger
    pending = ledger.pending_action
    active_task = ledger.current_task or context.session_data.current_module
    scoped = state.scoped_step_data.get(active_task) if active_task else None
    return {
        "customer_message": str((context.incoming_message or {}).get("text") or ""),
        "language_context": _safe_language_context(context),
        "workflow_state": str(state.workflow_state),
        "active_task": active_task,
        "task_status": ledger.task_status,
        "pending_action": {
            "tool_key": pending.tool_key,
            "summary": pending.summary,
            "requires_confirmation": pending.requires_confirmation,
            "requires_verified_customer": pending.requires_verified_customer,
        } if pending else None,
        "active_workflow_state": scoped if isinstance(scoped, dict) else state.step_data,
    }


_PROTECTED_CHANGE_FIELDS = {
    "action_nonce", "amount", "args", "args_hash", "arguments_hash", "confirmed",
    "currency", "execute", "id", "idempotency_key", "otp", "otp_verified",
    "payment_link", "payment_provider", "payment_status", "price", "provider",
    "status", "success", "tool_key", "tool_name", "total", "transaction",
    "verification_action", "verification_ref", "workflow_state",
}


def _gateway_semantic_input(context: ExecutionContext) -> Dict[str, Any]:
    pending = context.session_data.state.action_ledger.pending_action
    category = "unknown"
    changeable_fields: list[str] = []
    if pending is not None:
        category = str(pending.tool_key or "unknown").split(".", 1)[0][:40]
        for raw_key in (pending.args or {}):
            key = str(raw_key or "").strip()
            if (
                re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key)
                and key not in _PROTECTED_CHANGE_FIELDS
                and key not in changeable_fields
            ):
                changeable_fields.append(key)
            if len(changeable_fields) >= 12:
                break
    language_context = _safe_language_context(context)
    language = next(
        (
            normalized
            for value in language_context.values()
            for normalized in [str(value or "").strip().lower()]
            if re.fullmatch(r"[a-z]{2,8}(?:-[a-z0-9]{2,8})?", normalized)
        ),
        None,
    )
    return {
        "customer_message": str((context.incoming_message or {}).get("text") or "")[:4_000],
        "pending_action_category": category,
        "safe_action_description": f"{category} confirmation" if category != "unknown" else "pending action",
        "changeable_field_names": changeable_fields,
        "confirmation_requested": bool(pending and pending.requires_confirmation),
        "language": language,
    }


def _pending_action_identity(context: ExecutionContext) -> tuple[str, str, str] | None:
    pending = context.session_data.state.action_ledger.pending_action
    if pending is None:
        return None
    return (
        str(pending.action_nonce or ""),
        str(pending.tool_key or ""),
        str(pending.args_hash or ""),
    )


def _decision_for_current_action(
    context: ExecutionContext,
    expected_identity: tuple[str, str, str] | None,
    decision: PendingActionDecision,
) -> PendingActionDecision:
    if _pending_action_identity(context) != expected_identity:
        logger.warning("Stale pending-action semantic evidence discarded")
        return PendingActionDecision(
            clarification="The pending request changed. Please review and confirm it again."
        )
    return decision


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
        raise ValueError("pending action decision must be a JSON object")
    return parsed


def _coerce_decision(payload: Dict[str, Any]) -> PendingActionDecision:
    action = str(payload.get("action") or "unknown").strip().lower()
    if action not in VALID_PENDING_ACTIONS:
        action = "unknown"
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))
    field_updates = payload.get("field_updates")
    if not isinstance(field_updates, dict):
        field_updates = {}
    clarification = payload.get("clarification")
    if clarification is not None:
        clarification = str(clarification)[:500]
    return PendingActionDecision(
        action=action,  # type: ignore[arg-type]
        confidence=confidence,
        field_updates=field_updates,
        clarification=clarification,
    )


async def _decide_pending_action_legacy(
    context: ExecutionContext,
    *,
    legacy_llm: Any | None = None,
) -> PendingActionDecision:
    """
    Interpret the latest customer message relative to a pending action or active workflow.

    This only classifies intent. Backend state validation and CapabilityGateway still
    decide whether anything can execute.
    """
    system_prompt = (
        "You classify a customer's latest message for a backend workflow. "
        "Return only one JSON object with keys: action, confidence, field_updates, clarification. "
        "action must be exactly one of: confirm, cancel, change, continue, tangent, unknown. "
        "Use the customer's message, language context, pending action summary, and workflow state. "
        "Do not execute actions, grant permission, or invent missing backend state. "
        "If intent is uncertain, use unknown with low confidence."
    )
    payload = _decision_payload(context)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ]
    if legacy_llm is None:
        from backend.logic.engine.llm import get_llm

        legacy_llm = get_llm()
    llm = legacy_llm
    timeout_seconds = float(getattr(settings, "PENDING_ACTION_DECISION_TIMEOUT_SECONDS", 4) or 4)
    try:
        response = await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout_seconds)
    except Exception:
        logger.warning(
            "Semantic pending-action decision failed",
            extra={"extra_data": {"has_pending_action": bool(payload.get("pending_action"))}},
        )
        raise
    record_langchain_usage(context, response)
    parsed = _extract_json_object(str(getattr(response, "content", "") or ""))
    decision = _coerce_decision(parsed)
    logger.info(
        "Semantic pending-action decision",
        extra={"extra_data": {"action": decision.action, "confidence": decision.confidence}},
    )
    return decision


def _gateway_payload_to_decision(
    payload: Any,
    semantic_input: Dict[str, Any],
) -> PendingActionDecision:
    allowed_fields = set(semantic_input.get("changeable_field_names") or [])
    updates = {
        str(key): value
        for key, value in payload.field_updates.items()
        if str(key) in allowed_fields
    }
    return PendingActionDecision(
        action=payload.action,
        confidence=payload.confidence,
        field_updates=updates,
        clarification=payload.clarification,
    )


async def decide_pending_action(
    context: ExecutionContext,
    *,
    gateway: Any | None = None,
    legacy_llm: Any | None = None,
    shadow_recorder: Callable[[Dict[str, Any]], None] | None = None,
) -> PendingActionDecision:
    """Return semantic evidence only; deterministic consumers retain all authority."""
    from backend.logic.engine.pending_decision_gateway import (
        PendingDecisionGatewayMode,
        invoke_pending_decision_gateway,
        record_pending_decision_shadow_comparison,
        resolve_pending_decision_gateway_mode,
    )

    mode = resolve_pending_decision_gateway_mode(settings)
    expected_identity = _pending_action_identity(context)
    if mode is PendingDecisionGatewayMode.LEGACY:
        legacy = await _decide_pending_action_legacy(context, legacy_llm=legacy_llm)
        return _decision_for_current_action(context, expected_identity, legacy)

    semantic_input = _gateway_semantic_input(context)
    if mode is PendingDecisionGatewayMode.SHADOW:
        legacy = await _decide_pending_action_legacy(context, legacy_llm=legacy_llm)
        candidate = None
        gateway_result = None
        try:
            payload, gateway_result = await invoke_pending_decision_gateway(
                semantic_input, gateway=gateway
            )
            if payload is not None:
                candidate = _gateway_payload_to_decision(payload, semantic_input)
        except Exception:
            logger.warning("Shadow pending-decision gateway failed")
        record_pending_decision_shadow_comparison(
            legacy, candidate, gateway_result, shadow_recorder
        )
        return _decision_for_current_action(context, expected_identity, legacy)

    try:
        payload, _ = await invoke_pending_decision_gateway(semantic_input, gateway=gateway)
        if payload is None:
            return PendingActionDecision(
                clarification="Please confirm, change, or cancel this request."
            )
        return _decision_for_current_action(
            context,
            expected_identity,
            _gateway_payload_to_decision(payload, semantic_input),
        )
    except Exception:
        logger.warning("Authoritative pending-decision gateway failed closed")
        return PendingActionDecision(
            clarification="Please confirm, change, or cancel this request."
        )
