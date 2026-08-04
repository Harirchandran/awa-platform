from __future__ import annotations

"""Pure, bounded presentation helpers for backend-safe receptionist responses.

This module deliberately receives already-resolved presentation values and safe
customer text.  It has no access to execution context, tools, persistence, or
workflow mutation APIs.
"""

from dataclasses import dataclass
from typing import Any, Iterable, Optional


_ALLOWED_TONES = frozenset({"neutral", "friendly", "formal", "concise", "premium"})
_ALLOWED_CLARIFICATION_STYLES = frozenset({"one_question_at_a_time", "compact", "detailed"})
_ALLOWED_LANGUAGE_POLICIES = frozenset({"tenant_default", "follow_customer", "local_language_first"})
_ALLOWED_HANDOFF_POLICIES = frozenset({"never_proactive", "when_stuck", "early_offer", "during_off_hours"})
_ALLOWED_FALLBACK_POLICIES = frozenset({"faq_first", "clarify_first", "handoff_if_available"})
_ALLOWED_QUESTION_BATCHING = frozenset({"single", "grouped"})
_HANDOFF_MODULES = frozenset({"support", "human_handoff"})
_SAFE_HANDOFF_REPLY = "Contact support"

_GROUPED_FIELD_LABELS = {
    "booking": {
        "service_name": "service",
        "service": "service",
        "preferred_date": "date",
        "booking_date": "date",
        "date": "date",
        "preferred_time": "time",
        "booking_time": "time",
        "time": "time",
        "customer_name": "name",
        "patient_name": "name",
        "name": "name",
        "phone": "phone number",
        "email": "email address",
    },
    "order": {
        "item_name": "item",
        "item_sku": "item",
        "quantity": "quantity",
        "fulfillment_type": "fulfillment preference",
        "customer_name": "name",
        "phone": "phone number",
        "delivery_address": "delivery address",
    },
}


@dataclass(frozen=True)
class ReceptionistResponseStyle:
    tone: str
    clarification_style: str
    question_batching: str
    language_policy_hint: str
    handoff_policy: str
    fallback_policy: str


@dataclass(frozen=True)
class StyledReceptionistResponse:
    response_text: str
    quick_replies: list[str]


def _allowed(value: Any, allowed_values: frozenset[str]) -> Optional[str]:
    candidate = str(value or "").strip()
    return candidate if candidate in allowed_values else None


def response_style_from_resolution(resolution: Any) -> Optional[ReceptionistResponseStyle]:
    """Return a bounded style only for a successfully resolved profile.

    Legacy fallback resolution intentionally leaves existing customer wording
    unchanged.  Any malformed resolution becomes a no-style result.
    """
    try:
        if getattr(resolution, "source", None) not in {"active", "pinned"}:
            return None
        profile = getattr(resolution, "effective_profile", None)
        if profile is None:
            return None
        tone = _allowed(getattr(profile, "tone", None), _ALLOWED_TONES)
        clarification_style = _allowed(
            getattr(profile, "clarification_style", None),
            _ALLOWED_CLARIFICATION_STYLES,
        )
        question_batching = _allowed(
            getattr(profile, "question_batching", None),
            _ALLOWED_QUESTION_BATCHING,
        )
        language_policy_hint = _allowed(
            getattr(profile, "language_policy", None),
            _ALLOWED_LANGUAGE_POLICIES,
        )
        handoff_policy = _allowed(
            getattr(profile, "handoff_policy", None),
            _ALLOWED_HANDOFF_POLICIES,
        )
        fallback_policy = _allowed(
            getattr(profile, "fallback_policy", None),
            _ALLOWED_FALLBACK_POLICIES,
        )
        if not all((tone, clarification_style, question_batching, language_policy_hint, handoff_policy, fallback_policy)):
            return None
        return ReceptionistResponseStyle(
            tone=tone,
            clarification_style=clarification_style,
            question_batching=question_batching,
            language_policy_hint=language_policy_hint,
            handoff_policy=handoff_policy,
            fallback_policy=fallback_policy,
        )
    except Exception:
        return None


def grouped_clarification_text(
    base_text: str,
    *,
    workflow: str,
    missing_fields: Iterable[str],
    style: Optional[ReceptionistResponseStyle],
) -> str:
    """Ask safely collectable booking/order fields together without recording them.

    The existing deterministic next-field question remains intact.  The added
    sentence is limited to known, customer-visible labels and is only emitted
    for the established booking and order collection workflows.
    """
    try:
        if style is None or style.question_batching != "grouped":
            return base_text
        labels_by_field = _GROUPED_FIELD_LABELS.get(str(workflow or ""))
        if not labels_by_field:
            return base_text
        labels: list[str] = []
        for raw_field in missing_fields:
            label = labels_by_field.get(str(raw_field or ""))
            if label and label not in labels:
                labels.append(label)
        if len(labels) < 2:
            return base_text
        joined = ", ".join(labels[:-1]) + f", and {labels[-1]}" if len(labels) > 2 else " and ".join(labels)
        return f"{base_text}\n\nYou can share the {joined} together when convenient."
    except Exception:
        return base_text


def _tone_prefix(tone: str, response_kind: str) -> str:
    if tone == "friendly":
        return "I’m happy to help. "
    if tone == "formal":
        return "Please note: "
    if tone == "concise":
        return "Next: "
    if tone == "premium":
        return "Certainly. "
    return ""


def _clarification_suffix(style: ReceptionistResponseStyle, response_kind: str) -> str:
    if response_kind != "clarification" or style.clarification_style != "detailed":
        return ""
    return " Please share that detail so I can check it safely."


def _may_offer_handoff(
    style: ReceptionistResponseStyle,
    *,
    response_kind: str,
    runtime_available_modules: Iterable[str],
) -> bool:
    if response_kind != "failure":
        return False
    if style.handoff_policy != "early_offer" and style.fallback_policy != "handoff_if_available":
        return False
    available = {str(module) for module in runtime_available_modules or ()}
    return bool(available & _HANDOFF_MODULES)


def style_receptionist_response(
    base_text: str,
    quick_replies: Iterable[str],
    *,
    style: Optional[ReceptionistResponseStyle],
    response_kind: str,
    runtime_available_modules: Iterable[str] = (),
) -> StyledReceptionistResponse:
    """Apply wording-only presentation choices to already-safe response content."""
    original_text = str(base_text or "")
    original_replies = [str(reply) for reply in (quick_replies or [])]
    if style is None:
        return StyledReceptionistResponse(original_text, original_replies)

    response_text = f"{_tone_prefix(style.tone, response_kind)}{original_text}".strip()
    response_text = f"{response_text}{_clarification_suffix(style, response_kind)}"
    replies = list(original_replies)
    if _may_offer_handoff(
        style,
        response_kind=response_kind,
        runtime_available_modules=runtime_available_modules,
    ) and all(reply.casefold() != _SAFE_HANDOFF_REPLY.casefold() for reply in replies):
        replies.append(_SAFE_HANDOFF_REPLY)
    return StyledReceptionistResponse(response_text, replies)
