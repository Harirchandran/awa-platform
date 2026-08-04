"""Platform-owned customer assurance policy.

This module deliberately does not read tenant configuration.  Assurance is a
security property of a protected action, not a tenant preference.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping

from backend.core.config import settings


class AssuranceLevel(StrEnum):
    NONE = "none"
    PHONE_POSSESSION = "phone_possession"
    PAYMENT_PROVIDER = "payment_provider"


class AssuranceSource(StrEnum):
    ANONYMOUS = "anonymous"
    WHATSAPP_PHONE_POSSESSION = "whatsapp_phone_possession"
    AWA_CHAT_OTP_PHONE_POSSESSION = "awa_chat_otp_phone_possession"
    SIGNED_INVITATION = "signed_invitation"
    PAYMENT_PROVIDER = "payment_provider"


def required_assurance(tool_key: str, *, requires_verified_customer: bool = False) -> AssuranceLevel:
    """Return the authoritative requirement without consulting tenant config."""
    if tool_key == "booking.execute":
        return AssuranceLevel.PHONE_POSSESSION
    # Compatibility bridge: preserve existing protected workflows while their
    # explicit classifications are migrated in later stages.
    if requires_verified_customer:
        return AssuranceLevel.PHONE_POSSESSION
    return AssuranceLevel.NONE


def policy_source(tool_key: str) -> str:
    return "platform.booking.stage1" if tool_key == "booking.execute" else "platform.compatibility"


def phone_identity_hash(phone: str) -> str:
    """Stable keyed identifier; raw customer phones never enter session evidence."""
    normalized = "".join(ch for ch in str(phone or "") if ch.isdigit() or ch == "+")
    return hmac.new(
        settings.PUBLIC_CHAT_SESSION_SECRET.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def trusted_whatsapp_phone_possession(context: Any) -> bool:
    """Only accept server-created canonical WhatsApp webhook provenance."""
    incoming = getattr(context, "incoming_message", None) or {}
    session = getattr(context, "session_data", None)
    tenant = getattr(context, "tenant", None)
    return bool(
        str(incoming.get("channel_type") or "").lower() == "whatsapp"
        and str(incoming.get("source_type") or "").lower() == "webhook"
        and incoming.get("channel_account_id")
        and incoming.get("whatsapp_account_id")
        and str(incoming.get("channel_account_id")) == str(incoming.get("whatsapp_account_id"))
        and tenant is not None
        and session is not None
        and str(incoming.get("tenant_id")) == str(getattr(tenant, "id", ""))
        and str(incoming.get("conversation_id")) == str(getattr(session, "conversation_id", ""))
    )


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def valid_awa_chat_phone_possession(context: Any) -> bool:
    """Validate bounded browser-session evidence, never legacy boolean flags."""
    incoming = getattr(context, "incoming_message", None) or {}
    if str(incoming.get("channel_type") or "").lower() != "awa_chat":
        return False
    session = getattr(context, "session_data", None)
    tenant = getattr(context, "tenant", None)
    if session is None or tenant is None:
        return False
    verification = getattr(getattr(getattr(session, "state", None), "action_ledger", None), "verification", None)
    evidence_list = getattr(verification, "otp_verifications", []) or []
    return has_valid_awa_chat_evidence(
        evidence_list,
        tenant_id=tenant.id,
        conversation_id=session.conversation_id,
        public_chat_session_id=incoming.get("channel_session_id") or session.id,
        config_version=session.config_version,
    )


def has_valid_awa_chat_evidence(
    evidence_list: Any,
    *,
    tenant_id: Any,
    conversation_id: Any,
    public_chat_session_id: Any,
    config_version: int,
) -> bool:
    """Mapping/object tolerant validator for persisted public-chat evidence."""
    now = datetime.now(timezone.utc)
    for evidence in evidence_list:
        get = evidence.get if isinstance(evidence, Mapping) else lambda key, default=None: getattr(evidence, key, default)
        source = str(get("assurance_source", "") or "")
        if source != AssuranceSource.AWA_CHAT_OTP_PHONE_POSSESSION:
            continue
        if not get("phone_identity_hash"):
            continue
        expires_at = _as_utc(get("expires_at"))
        if expires_at is None or expires_at <= now:
            continue
        if (
            str(get("tenant_id", "")) == str(tenant_id)
            and str(get("conversation_id", "")) == str(conversation_id)
            and str(get("channel_session_id", "")) == str(public_chat_session_id)
            and int(get("config_version", 0)) == int(config_version)
        ):
            return True
    return False


def has_required_assurance(context: Any, tool_key: str, *, requires_verified_customer: bool = False) -> bool:
    requirement = required_assurance(tool_key, requires_verified_customer=requires_verified_customer)
    if requirement == AssuranceLevel.NONE:
        return True
    if requirement == AssuranceLevel.PHONE_POSSESSION:
        return trusted_whatsapp_phone_possession(context) or valid_awa_chat_phone_possession(context)
    return False


WORKFLOW_ASSURANCE_AUDIT: dict[str, str] = {
    "booking creation": "phone_possession",
    "booking update/cancel": "phone_possession + ownership",
    "order placement": "phone_possession",
    "payment link/request": "phone_possession (customer-specific)",
    "payment completion": "payment_provider",
    "reminders": "staff_authenticated",
    "reviews": "signed_invitation or workflow-specific",
    "support": "none",
    "lead capture": "none",
}
