import time
import re
import html
import hmac
import json
import secrets
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from uuid import UUID, uuid4
import httpx
import redis.asyncio as redis
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from backend.api.awa_chat_security import create_public_token, hash_public_chat_token
from backend.core.config import settings
from backend.core.context import protected_action_binding
from backend.core.customer_assurance import (
    AssuranceLevel,
    AssuranceSource,
    has_valid_awa_chat_evidence,
    phone_identity_hash,
    policy_source,
    required_assurance,
)
from backend.core.redis_client import (
    claim_otp_challenge,
    restore_otp_challenge_after_enqueue_failure,
)
from backend.db.client import db_client
from backend.domains.customers.service import customer_service
from backend.ops.logger import logger
from backend.schemas.canonical_message import (
    CanonicalMessage,
    ChannelType,
    MessageDirection,
    MessageType,
    SourceType,
)
from backend.pipeline.reliable_queue import enqueue_message
from backend.logic.engine.capability_menu import (
    build_capability_menu,
    build_self_serve_suggestions,
    has_order_catalog,
)
from backend.schemas.response import APIResponse
router = APIRouter(prefix="/api/v1", tags=["Public AWA Chat"])
_rate_window: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW_MAX_KEYS = 10_000  # H-3: Cap to prevent unbounded memory growth
PUBLIC_CHAT_MAX_MESSAGE_LENGTH = 800
class CreatePublicChatSessionRequest(BaseModel):
    visitor_name: Optional[str] = Field(default=None, max_length=80)
    captcha_token: Optional[str] = Field(default=None, max_length=2048)
class PublicChatMessageRequest(BaseModel):
    session_token: str = Field(min_length=24)
    message_text: str = Field(min_length=1, max_length=PUBLIC_CHAT_MAX_MESSAGE_LENGTH)
    client_message_id: Optional[str] = Field(default=None, max_length=120)
class PublicChatContactRequest(BaseModel):
    session_token: str = Field(default=None, max_length=160)
    email: Optional[str] = Field(default=None, max_length=160)
    phone: Optional[str] = Field(default=None, max_length=40)
    name: Optional[str] = Field(default=None, max_length=80)
    whatsapp_opted_in: bool = Field(default=False)
class PublicChatEndRequest(BaseModel):
    session_token: str = Field(min_length=24)
class PublicChatOtpRequest(BaseModel):
    session_token: str = Field(min_length=24)
    phone: str = Field(min_length=7, max_length=40)
    action: str = Field(default="commitment", max_length=40)
class PublicChatOtpVerifyRequest(PublicChatOtpRequest):
    code: str = Field(min_length=4, max_length=10)
def _clean_contact_email(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    if not value:
        return None
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
        raise HTTPException(status_code=400, detail="Please provide a valid email address")
    return value
def _clean_contact_phone(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    if not value:
        return None
    normalized = re.sub(r"[^\d+]", "", value)
    if len(re.sub(r"\D", "", normalized)) < 7:
        raise HTTPException(status_code=400, detail="Please provide a valid phone number")
    return normalized
def _get_security_config(config_json: dict[str, Any]) -> dict[str, Any]:
    security = config_json.get("security", {}) if isinstance(config_json, dict) else {}
    return security if isinstance(security, dict) else {}
def _requires_public_chat_captcha(config_json: dict[str, Any]) -> bool:
    return bool(_get_security_config(config_json).get("public_chat_captcha_required", False))
def _requires_customer_otp(config_json: dict[str, Any], module_name: Optional[str]) -> bool:
    """Deprecated compatibility signature; tenant OTP config is ignored."""
    return module_name in {"booking", "order", "reminders", "payments"}
def _is_commitment_confirmation(text: str) -> bool:
    normalized = re.sub(r"[^a-z]+", " ", text.lower()).strip()
    return normalized in {"yes", "confirm", "proceed", "ok", "okay", "sure", "book it", "place order"}
def _verification_action_for_module(module_name: Optional[str]) -> str:
    return f"{module_name or 'commitment'}_commitment"
async def _verify_public_chat_captcha(token: Optional[str], request: Request) -> None:
    mode = settings.PUBLIC_CHAT_CAPTCHA_MODE
    if mode == "disabled":
        raise HTTPException(status_code=503, detail="Chat verification is required but CAPTCHA is not configured.")
    if not token:
        raise HTTPException(status_code=400, detail="Complete the verification check before starting chat.")
    if mode == "mock":
        if settings.ENV == "production":
            raise HTTPException(status_code=503, detail="CAPTCHA is not configured for production.")
        if token != "local-captcha-ok":
            raise HTTPException(status_code=400, detail="Complete the verification check before starting chat.")
        return
    if mode == "turnstile":
        if not settings.TURNSTILE_SECRET_KEY:
            raise HTTPException(status_code=503, detail="CAPTCHA is not configured.")
        async with httpx.AsyncClient(timeout=8.0) as client:
            result = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={
                    "secret": settings.TURNSTILE_SECRET_KEY,
                    "response": token,
                    "remoteip": request.client.host if request.client else None,
                },
            )
        payload = result.json()
        if not payload.get("success"):
            raise HTTPException(status_code=400, detail="Verification failed. Please try again.")
async def _get_public_config(tenant_id: UUID) -> dict[str, Any]:
    import json
    config = await db_client.get_tenant_config(tenant_id)
    config_json = config.get("config_json", {}) if config else {}
    if isinstance(config_json, str):
        return json.loads(config_json)
    return config_json if isinstance(config_json, dict) else {}
def _otp_challenge_key(session_id: UUID, action: str, phone: str) -> str:
    return f"public-chat:otp:challenge:{session_id}:{action}:{phone}"
def _otp_attempts_key(session_id: UUID, action: str, phone: str) -> str:
    return f"public-chat:otp:attempts:{session_id}:{action}:{phone}"
def _otp_verified_key(session_id: UUID, action_binding: str) -> str:
    return f"public-chat:otp:verified:{session_id}:{action_binding}"
def _otp_continuation_claim_key(session: dict[str, Any], action_nonce: str) -> str:
    return (
        f"public-chat:otp:continuation:{session['tenant_id']}:"
        f"{session['conversation_id']}:{session['id']}:{action_nonce}"
    )
async def _redis_set(key: str, value: str, ex: int) -> None:
    from backend.core.redis_client import redis_client
    await redis_client.set(key, value, ex=ex)
async def _redis_incr_with_ttl(key: str, ex: int) -> int:
    from backend.core.redis_client import redis_client
    attempts = await redis_client.incr(key)
    if attempts == 1:
        await redis_client.expire(key, ex)
    return int(attempts)
async def _redis_get(key: str) -> Optional[str]:
    from backend.core.redis_client import redis_client
    return await redis_client.get(key)
async def _redis_delete(key: str) -> None:
    from backend.core.redis_client import redis_client
    await redis_client.delete(key)

async def _is_session_verified_for_action(session_id: UUID, action_binding: str) -> bool:
    return bool(await _redis_get(_otp_verified_key(session_id, action_binding)))
def _coerce_state_json(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return value if isinstance(value, dict) else {}


def _positive_config_version(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        return int(value)
    return None


def _conversation_session_config_version(
    conversation_session: Mapping[str, Any],
) -> Optional[int]:
    """Resolve one pinned version from the persisted conversation-session envelope."""
    top_level_present = "config_version" in conversation_session
    top_level = _positive_config_version(conversation_session.get("config_version"))
    state_json = _coerce_state_json(conversation_session.get("state_json"))
    state_present = "config_version" in state_json
    persisted = _positive_config_version(state_json.get("config_version"))

    if (top_level_present and top_level is None) or (state_present and persisted is None):
        return None
    if top_level is not None and persisted is not None and top_level != persisted:
        logger.warning(
            "Conversation session config binding mismatch",
            extra={
                "extra_data": {
                    "reason_code": "conversation_session_config_version_mismatch",
                    "tenant_id": str(conversation_session.get("tenant_id") or "")[:64],
                    "conversation_id": str(conversation_session.get("conversation_id") or "")[:64],
                    "session_id": str(conversation_session.get("id") or "")[:64],
                }
            },
        )
        return None
    return top_level if top_level is not None else persisted


def _pending_action_from_state(state_json: dict[str, Any]) -> Optional[dict[str, Any]]:
    state_dict = state_json.get("state") if isinstance(state_json.get("state"), dict) else {}
    ledger = state_dict.get("action_ledger") if isinstance(state_dict.get("action_ledger"), dict) else {}
    pending = ledger.get("pending_action") if isinstance(ledger.get("pending_action"), dict) else None
    if not pending:
        return None
    task_status = str(ledger.get("task_status") or "").lower()
    if task_status not in {"awaiting_confirmation", "confirmed"}:
        return None
    return pending
def _module_from_pending_action(pending_action: dict[str, Any], fallback: Optional[str]) -> Optional[str]:
    tool_key = str(pending_action.get("tool_key") or "")
    if "." in tool_key:
        return tool_key.split(".", 1)[0]
    return fallback


def _pending_action_binding(
    pending_action: dict[str, Any],
    *,
    session: dict[str, Any],
    conversation_session: dict[str, Any],
) -> Optional[str]:
    tool_key = str(pending_action.get("tool_key") or "")
    args_hash = str(pending_action.get("args_hash") or "")
    action_nonce = str(pending_action.get("action_nonce") or "")
    config_version = _conversation_session_config_version(conversation_session)
    if (
        not tool_key
        or not re.fullmatch(r"[0-9a-f]{24}", args_hash)
        or not re.fullmatch(r"[0-9a-f]{32,64}", action_nonce)
        or config_version is None
    ):
        return None
    return protected_action_binding(
        tenant_id=session["tenant_id"],
        conversation_id=session["conversation_id"],
        channel_session_id=session["id"],
        config_version=config_version,
        action_nonce=action_nonce,
        tool_key=tool_key,
        args_hash=args_hash,
    )


async def _load_pending_action_context(
    session: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str, str, int]:
    conversation_session = await db_client.get_conversation_session(
        session["tenant_id"], session["conversation_id"]
    )
    if not conversation_session:
        raise HTTPException(status_code=409, detail="There is no action awaiting verification.")
    state_json = _coerce_state_json(conversation_session.get("state_json"))
    pending_action = _pending_action_from_state(state_json)
    if not pending_action:
        raise HTTPException(status_code=409, detail="There is no action awaiting verification.")
    config_version = _conversation_session_config_version(conversation_session)
    if config_version is None:
        raise HTTPException(status_code=409, detail="The pending action cannot be verified safely.")
    binding = _pending_action_binding(
        pending_action,
        session=session,
        conversation_session=conversation_session,
    )
    if not binding:
        raise HTTPException(status_code=409, detail="The pending action cannot be verified safely.")
    module_name = _module_from_pending_action(pending_action, conversation_session.get("current_module"))
    action = str(pending_action.get("verification_action") or _verification_action_for_module(module_name))
    return conversation_session, pending_action, action, binding, config_version


def _pending_requires_phone_possession(pending_action: Mapping[str, Any]) -> bool:
    requirement = str(pending_action.get("required_assurance") or "")
    if requirement and requirement != AssuranceLevel.NONE:
        return requirement == AssuranceLevel.PHONE_POSSESSION
    # Old pending-action records may deserialize the new default ("none")
    # while still carrying the former protected flag. Treat that as an
    # unsnapshotted compatibility record, never as a tenant-config bypass.
    return required_assurance(
        str(pending_action.get("tool_key") or ""),
        requires_verified_customer=bool(pending_action.get("requires_verified_customer")),
    ) == AssuranceLevel.PHONE_POSSESSION


def _session_has_phone_possession(
    state_json: Mapping[str, Any], session: Mapping[str, Any], conversation_session: Mapping[str, Any], session_id: UUID
) -> bool:
    state = state_json.get("state") if isinstance(state_json.get("state"), dict) else {}
    ledger = state.get("action_ledger") if isinstance(state.get("action_ledger"), dict) else {}
    verification = ledger.get("verification") if isinstance(ledger.get("verification"), dict) else {}
    return has_valid_awa_chat_evidence(
        verification.get("otp_verifications", []),
        tenant_id=session.get("tenant_id"),
        conversation_id=session.get("conversation_id"),
        public_chat_session_id=session_id,
        config_version=_conversation_session_config_version(conversation_session) or 0,
    )


async def _invalidate_assurance_if_phone_changed(
    *,
    state_json: dict[str, Any],
    session: Mapping[str, Any],
    conversation_session: Mapping[str, Any],
    phone: str,
) -> bool:
    """A new phone assertion invalidates prior browser-session assurance."""
    state = state_json.get("state") if isinstance(state_json.get("state"), dict) else {}
    ledger = state.get("action_ledger") if isinstance(state.get("action_ledger"), dict) else {}
    verification = ledger.get("verification") if isinstance(ledger.get("verification"), dict) else {}
    records = verification.get("otp_verifications") if isinstance(verification.get("otp_verifications"), list) else []
    requested_hash = phone_identity_hash(phone)
    active_hashes = {
        str(item.get("phone_identity_hash"))
        for item in records if isinstance(item, dict) and item.get("phone_identity_hash")
    }
    if not active_hashes or requested_hash in active_hashes:
        return False
    verification["otp_verifications"] = []
    verification["otp_verified_actions"] = []
    verification["phone_verified"] = False
    verification["customer_verified"] = False
    await db_client.update_conversation_session(
        session["tenant_id"], session["conversation_id"],
        state_json=state_json,
        config_version=conversation_session["config_version"],
        current_module=conversation_session.get("current_module"),
        current_intent=conversation_session.get("current_intent"),
    )
    from backend.pipeline.session_manager import session_manager
    await session_manager.invalidate_cache(session["tenant_id"], session["conversation_id"])
    return True
async def _mark_session_verified_action(
    session: dict[str, Any],
    action: str,
    phone: str,
    *,
    action_binding: str,
    verification_ref: str,
    session_id: UUID,
    config_version: int,
) -> bool:
    conversation_session = await db_client.get_conversation_session(session["tenant_id"], session["conversation_id"])
    if not conversation_session:
        return
    state_json = _coerce_state_json(conversation_session.get("state_json"))
    state_dict = state_json.setdefault("state", {})
    if not isinstance(state_dict, dict):
        state_dict = {}
        state_json["state"] = state_dict
    ledger = state_dict.setdefault("action_ledger", {})
    if not isinstance(ledger, dict):
        ledger = {}
        state_dict["action_ledger"] = ledger
    pending_action = ledger.get("pending_action") if isinstance(ledger.get("pending_action"), dict) else None
    if not pending_action or not hmac.compare_digest(
        _pending_action_binding(
            pending_action,
            session={**session, "id": session_id},
            conversation_session=conversation_session,
        ) or "",
        action_binding,
    ):
        return False
    verification = ledger.setdefault("verification", {})
    if not isinstance(verification, dict):
        verification = {}
        ledger["verification"] = verification
    pending_nonce = str(pending_action.get("action_nonce"))
    now = datetime.now(timezone.utc)
    expires_at = now.timestamp() + settings.CUSTOMER_OTP_VERIFIED_TTL_SECONDS
    evidence = {
        "binding": action_binding,
        "action_nonce": pending_nonce,
        "verification_ref": verification_ref,
        "tenant_id": str(session["tenant_id"]),
        "conversation_id": str(session["conversation_id"]),
        "channel_session_id": str(session_id),
        "config_version": config_version,
        "tool_key": str(pending_action.get("tool_key")),
        "args_hash": str(pending_action.get("args_hash")),
        "verified_at": now.isoformat(),
        "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
        "consumed": False,
        "consumption_ref": None,
        "assurance_source": AssuranceSource.AWA_CHAT_OTP_PHONE_POSSESSION,
        "phone_identity_hash": phone_identity_hash(phone),
        "verification_scope": "session_phone_possession",
    }
    verification["otp_verifications"] = [evidence]
    verification["otp_verified_actions"] = []
    verification["phone_verified"] = True
    verification["customer_verified"] = True
    verification["verification_channel"] = "awa_chat_otp"
    # Do not persist raw phone values in assurance evidence or telemetry.
    verification.pop("verified_phone", None)
    await db_client.update_conversation_session(
        session["tenant_id"],
        session["conversation_id"],
        state_json=state_json,
        config_version=conversation_session["config_version"],
        current_module=conversation_session.get("current_module"),
        current_intent=conversation_session.get("current_intent"),
    )
    from backend.pipeline.session_manager import session_manager
    await session_manager.invalidate_cache(session["tenant_id"], session["conversation_id"])
    return True


async def _remove_session_verification_evidence(
    session: dict[str, Any], *, verification_ref: str
) -> None:
    conversation_session = await db_client.get_conversation_session(
        session["tenant_id"], session["conversation_id"]
    )
    if not conversation_session:
        return
    state_json = _coerce_state_json(conversation_session.get("state_json"))
    state_dict = state_json.get("state") if isinstance(state_json.get("state"), dict) else {}
    ledger = state_dict.get("action_ledger") if isinstance(state_dict.get("action_ledger"), dict) else {}
    verification = ledger.get("verification") if isinstance(ledger.get("verification"), dict) else {}
    records = verification.get("otp_verifications")
    if not isinstance(records, list):
        return
    verification["otp_verifications"] = [
        item for item in records
        if not isinstance(item, dict) or item.get("verification_ref") != verification_ref
    ]
    await db_client.update_conversation_session(
        session["tenant_id"],
        session["conversation_id"],
        state_json=state_json,
        config_version=conversation_session["config_version"],
        current_module=conversation_session.get("current_module"),
        current_intent=conversation_session.get("current_intent"),
    )
    from backend.pipeline.session_manager import session_manager
    await session_manager.invalidate_cache(session["tenant_id"], session["conversation_id"])
async def _require_otp_before_commitment(session_id: UUID, session: dict[str, Any], message_text: str) -> None:
    if not _is_commitment_confirmation(message_text):
        return
    try:
        conversation_session = await db_client.get_conversation_session(session["tenant_id"], session["conversation_id"])
    except Exception as exc:
        logger.warning(f"Could not inspect conversation state for OTP gate: {exc}")
        return
    if not conversation_session:
        return
    state_json = _coerce_state_json(conversation_session.get("state_json"))
    pending_action = _pending_action_from_state(state_json)
    if not pending_action:
        return
    if not _pending_requires_phone_possession(pending_action):
        return
    module_name = _module_from_pending_action(
        pending_action,
        conversation_session.get("current_module"),
    )
    action = str(pending_action.get("verification_action") or _verification_action_for_module(module_name))
    action_binding = _pending_action_binding(
        pending_action,
        session={**session, "id": session_id},
        conversation_session=conversation_session,
    )
    if not action_binding:
        raise HTTPException(status_code=409, detail="The pending action cannot be verified safely.")
    if _session_has_phone_possession(state_json, session, conversation_session, session_id):
        return
    if await _is_session_verified_for_action(session_id, action_binding):
        raise HTTPException(
            status_code=403,
            detail="Verification succeeded. Please wait while the pending action is processed.",
        )
    raise HTTPException(
        status_code=403,
        detail=(
            "Verify your phone number once for this chat session before confirming this booking."
        ),
    )
async def _deliver_customer_otp(phone: str, code: str, action: str, tenant_id: UUID, session_id: UUID) -> None:
    if settings.CUSTOMER_OTP_MODE == "mock":
        return
    if settings.CUSTOMER_OTP_MODE != "live" or not settings.CUSTOMER_OTP_WEBHOOK_URL:
        raise HTTPException(status_code=503, detail="Phone verification is not configured.")
    headers = {"Content-Type": "application/json"}
    if settings.CUSTOMER_OTP_WEBHOOK_SECRET:
        headers["X-AWA-OTP-Secret"] = settings.CUSTOMER_OTP_WEBHOOK_SECRET
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            settings.CUSTOMER_OTP_WEBHOOK_URL,
            headers=headers,
            json={
                "phone": phone,
                "code": code,
                "action": action,
                "tenant_id": str(tenant_id),
                "session_id": str(session_id),
                "expires_in_seconds": settings.CUSTOMER_OTP_TTL_SECONDS,
            },
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="Could not send verification code. Please try again.")
def _json_safe_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if hasattr(value, "isoformat") else str(value) if isinstance(value, UUID) else value
        for key, value in dict(record).items()
    }
def _extract_quick_replies_from_payload(payload: Any) -> list[str]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return []
    if not isinstance(payload, dict):
        return []
    options: list[str] = []
    action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
    for button in action.get("buttons") or []:
        if not isinstance(button, dict):
            continue
        reply = button.get("reply") if isinstance(button.get("reply"), dict) else {}
        title = str(reply.get("title") or "").strip()
        if title:
            options.append(title)
    for section in action.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for row in section.get("rows") or []:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            if title:
                options.append(title)
    deduped: list[str] = []
    seen = set()
    for option in options:
        key = option.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(option)
    return deduped[:10]
def _public_chat_message_record(item: dict[str, Any], latest_outbound_id: Optional[str], session: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    record = _json_safe_record(item)
    if record.get("direction") == "outbound" and record.get("message_text"):
        from backend.pipeline.channel_output import sanitize_customer_visible_output
        record["message_text"] = sanitize_customer_visible_output(record["message_text"])

    raw_payload = item.get("raw_payload")
    if isinstance(raw_payload, str):
        try:
            raw_payload = json.loads(raw_payload)
        except Exception:
            raw_payload = {}
    elif not isinstance(raw_payload, dict):
        raw_payload = {}

    import os
    env = os.getenv("ENV", "local").lower()
    enable_diagnostics = os.getenv("ENABLE_LOCAL_SIMULATION_DIAGNOSTICS", "false").lower() == "true"
    visitor_name = str((session or {}).get("visitor_name") or "")
    is_local_sim = visitor_name.startswith("LOCAL_SIM_TENANT_") or "local-sim" in visitor_name.lower()

    diagnostics_data = None
    if is_local_sim and env != "production" and enable_diagnostics:
        diagnostics_data = {}
        if "provider_error" in raw_payload:
            diagnostics_data["provider_error"] = raw_payload["provider_error"]
        if "sanitization_redacted_reason" in raw_payload:
            diagnostics_data["sanitization_redacted_reason"] = raw_payload["sanitization_redacted_reason"]
            diagnostics_data["original_leaked_response_len"] = raw_payload["original_leaked_response_len"]
        if "metrics" in raw_payload:
            from backend.observability.latency import redact_metrics_payload
            diagnostics_data["metrics"] = redact_metrics_payload(raw_payload["metrics"])
        if not diagnostics_data:
            diagnostics_data = None

    # Pop internal fields so they never leak in normal payload
    clean_raw_payload = dict(raw_payload)
    clean_raw_payload.pop("provider_error", None)
    clean_raw_payload.pop("sanitization_redacted_reason", None)
    clean_raw_payload.pop("original_leaked_response_len", None)
    clean_raw_payload.pop("metrics", None)

    from backend.pipeline.channel_output import extract_safe_cards, redact_url_tokens
    cards = extract_safe_cards(clean_raw_payload)
    record["cards"] = cards
    record["raw_payload"] = redact_url_tokens(clean_raw_payload)

    quick_replies = _extract_quick_replies_from_payload(clean_raw_payload)
    is_latest_outbound = str(item.get("id")) == latest_outbound_id
    quick_reply_state = "none"
    if quick_replies:
        quick_reply_state = "active" if is_latest_outbound else "expired"
    record["quick_replies"] = quick_replies
    record["quick_replies_stale"] = bool(quick_replies and not is_latest_outbound)
    record["quick_replies_state"] = quick_reply_state

    if diagnostics_data:
        record["diagnostics"] = diagnostics_data

    return record
def is_private_ip(ip: str) -> bool:
    """Check if an IP address is a private or loopback IP."""
    ip = ip.strip()
    if ip.startswith("127.") or ip == "localhost" or ip == "::1":
        return True
    if ip.startswith("10."):
        return True
    if ip.startswith("192.168."):
        return True
    if ip.startswith("172."):
        parts = ip.split(".")
        if len(parts) >= 2:
            try:
                second = int(parts[1])
                if 16 <= second <= 31:
                    return True
            except ValueError:
                pass
    return False

def extract_client_ip(request: Request) -> str:
    """Secure client IP extraction preventing X-Forwarded-For spoofing (Phase 2)."""
    headers = getattr(request, "headers", None)
    if not headers:
        return request.client.host if request.client else "unknown"
    cf_ip = headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    real_ip = headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    xff = headers.get("X-Forwarded-For")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        for part in reversed(parts):
            if not is_private_ip(part):
                return part
        if parts:
            return parts[0]
    return request.client.host if request.client else "unknown"

def _check_memory_rate_limit(request: Request, key: str) -> None:
    now = time.time()
    client_ip = extract_client_ip(request)
    window_key = f"{client_ip}:{key}"
    # H-3: Prune expired entries from this bucket
    bucket = [ts for ts in _rate_window[window_key] if now - ts < 60]
    if len(bucket) >= settings.PUBLIC_CHAT_RATE_LIMIT:
        _rate_window[window_key] = bucket
        raise HTTPException(status_code=429, detail="Too many chat messages. Please wait a minute.")
    bucket.append(now)
    _rate_window[window_key] = bucket
    # H-3: Selective eviction if dict exceeds max keys (prevents OOM without resetting all limits)
    if len(_rate_window) > _RATE_WINDOW_MAX_KEYS:
        # Prune expired keys first
        expired_keys = [k for k, v in _rate_window.items() if not v or now - v[-1] >= 60]
        for k in expired_keys:
            _rate_window.pop(k, None)

        # If still exceeding, evict oldest entries
        if len(_rate_window) > _RATE_WINDOW_MAX_KEYS:
            logger.warning(f"Rate limiter fallback dict still exceeds {_RATE_WINDOW_MAX_KEYS} keys after pruning — evicting oldest keys")
            sorted_keys = sorted(_rate_window.keys(), key=lambda k: _rate_window[k][-1] if _rate_window[k] else 0)
            for k in sorted_keys[:len(_rate_window) - _RATE_WINDOW_MAX_KEYS]:
                _rate_window.pop(k, None)
async def _check_public_rate_limit(request: Request, key: str) -> None:
    from backend.core.redis_client import redis_client
    client_ip = extract_client_ip(request)
    window_key = f"public-chat:{client_ip}:{key}"
    try:
        count = await redis_client.incr(window_key)
        if count == 1:
            await redis_client.expire(window_key, 60)
        if count > settings.PUBLIC_CHAT_RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Too many chat requests. Please wait a minute.")
    except HTTPException:
        raise
    except Exception as exc:
        if settings.ENV == "production":
            logger.error("Public chat Redis rate limit unavailable in production: %s", exc)
            raise HTTPException(status_code=503, detail="Chat protection is temporarily unavailable.")
        logger.warning(f"Public chat Redis rate limit unavailable; using memory fallback: {exc}")
        _check_memory_rate_limit(request, key)
async def _get_active_link(slug: str) -> dict[str, Any]:
    link = await db_client.get_public_chat_link_by_slug(slug)
    if not link:
        raise HTTPException(status_code=404, detail="AWA Chat link not found")
    expected_hash = hash_public_chat_token(slug, "link")
    stored_hash = str(link.get("token_hash") or "")
    if not hmac.compare_digest(stored_hash, expected_hash):
        raise HTTPException(status_code=404, detail="AWA Chat link not found")
    if link.get("status") != "active" or link.get("tenant_status") not in {"trial", "active"}:
        raise HTTPException(status_code=403, detail="This AWA Chat link is not active")
    return link


async def _get_active_session(session_id: UUID, session_token: str) -> dict[str, Any]:
    token_hash = hash_public_chat_token(session_token, "session")
    session = await db_client.get_public_chat_session(session_id, token_hash)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found or expired")
    if session.get("tenant_status") not in {"trial", "active"}:
        raise HTTPException(status_code=403, detail="This business is not accepting chat right now")
    return session
async def _enqueue_internal_event_for_worker(canonical: CanonicalMessage) -> str:
    from backend.core.redis_client import redis_client
    try:
        await enqueue_message(redis_client, canonical.model_dump_json())
        return "queued"
    except Exception as exc:
        logger.warning(f"AWA Chat message saved but not queued: {exc}")
        return "received"


async def _enqueue_for_worker(canonical: CanonicalMessage) -> str:
    from backend.pipeline.public_chat_queue import enqueue_public_chat_turn
    from backend.pipeline.reliable_queue import enqueue_message
    from backend.core.redis_client import redis_client
    source_type_val = canonical.source_type.value if hasattr(canonical.source_type, "value") else canonical.source_type
    try:
        if source_type_val == "awa_chat":
            await enqueue_public_chat_turn(canonical)
        else:
            await enqueue_message(redis_client, canonical.model_dump_json())
        return "queued"
    except Exception as exc:
        logger.warning(f"AWA Chat message saved but not queued: {exc}")
        return "received"



async def _enqueue_verified_pending_action_continuation(
    *,
    session_id: UUID,
    session: dict[str, Any],
    action_binding: str,
    verification_ref: str,
    config_version: int,
) -> str:
    event_id = uuid4()
    provider_message_id = (
        f"awa-internal:otp_verified_pending_action:{action_binding}:"
        f"{verification_ref}:{session_id}"
    )
    sequence_number = await db_client.get_next_sequence_number(
        session["tenant_id"], session["conversation_id"]
    )
    canonical = CanonicalMessage(
        event_id=event_id,
        source_type=SourceType.INTERNAL_EVENT,
        channel_type=ChannelType.AWA_CHAT,
        channel_account_id=session["link_id"],
        channel_session_id=str(session_id),
        tenant_id=session["tenant_id"],
        whatsapp_account_id=None,
        conversation_id=session["conversation_id"],
        conversation_sequence_number=sequence_number,
        customer_id=session["customer_id"],
        config_version=config_version,
        worker_metadata={"worker_version": "1.0.0"},
        message={
            "message_id": uuid4(),
            "provider_message_id": provider_message_id,
            "direction": MessageDirection.INBOUND,
            "timestamp": datetime.now(timezone.utc),
            "message_type": MessageType.INTERACTIVE,
            "text": None,
        },
        processing={
            "first_received_at": datetime.now(timezone.utc),
            "retry_count": 0,
            "max_retries": 3,
        },
        trace={"trace_id": event_id, "created_at": datetime.now(timezone.utc)},
    )
    return await _enqueue_for_worker(canonical)
def _build_public_chat_suggestions(
    config_json: dict[str, Any],
    available_modules: set[str] | frozenset[str] | None = None,
    behavior_profile_resolution: Any = None,
) -> list[str]:
    return build_self_serve_suggestions(
        config_json,
        available_modules=available_modules,
        behavior_profile_resolution=behavior_profile_resolution,
    )


async def _resolve_public_chat_behavior_profile(
    *,
    tenant_id: UUID,
    config_json: dict[str, Any],
    snapshot: Any,
) -> Any:
    try:
        from backend.core.runtime_behavior_profile import resolve_runtime_behavior_profile

        return await resolve_runtime_behavior_profile(
            tenant_id=tenant_id,
            config_data=config_json,
            runtime_capability_snapshot=snapshot,
            channel="public_chat",
            conversation_state=None,
        )
    except Exception as exc:
        logger.warning(
            "Public chat behavior profile resolution failed closed",
            extra={"extra_data": {"tenant_id": str(tenant_id), "error_type": type(exc).__name__}},
        )
        return None


async def _save_public_chat_greeting(
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    session_id: UUID,
    greeting_message: str,
    suggestions: list[str],
) -> None:
    from backend.pipeline.interactive_parser import build_interactive_payload_from_options

    safe_message = str(greeting_message or "").strip() or "Hello! How can I help you today?"
    interactive_payload = build_interactive_payload_from_options(safe_message, suggestions or [])
    raw_payload = {
        "source": "greeting",
        "semantic_role": "orientation",
        "include_in_semantic_history": False,
    }
    if interactive_payload:
        raw_payload.update(interactive_payload)
    outbound_id = f"greeting_{session_id}"
    await db_client.save_message(
        message_id=uuid4(),
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        whatsapp_message_id=outbound_id,
        provider_message_id=outbound_id,
        channel_type=ChannelType.AWA_CHAT.value,
        direction="outbound",
        message_type="interactive" if interactive_payload else "text",
        message_text=safe_message,
        raw_payload=raw_payload,
    )
def _has_public_order_catalog(config_json: dict[str, Any]) -> bool:
    return has_order_catalog(config_json)
def _build_privacy_note(
    config_json: dict[str, Any],
    available_modules: set[str] | frozenset[str] | None = None,
) -> str:
    modules = config_json.get("modules", {}) if isinstance(config_json, dict) else {}
    detail_reasons: list[str] = []
    if modules.get("booking", {}).get("enabled", False) and (available_modules is None or "booking" in available_modules):
        detail_reasons.append("booking")
    if modules.get("order", {}).get("enabled", False) and (available_modules is None or "order" in available_modules):
        detail_reasons.append("orders")
    if modules.get("lead_capture", {}).get("enabled", False) and (available_modules is None or "lead_capture" in available_modules):
        detail_reasons.append("follow-up")
    if modules.get("support", {}).get("enabled", False) and (available_modules is None or "support" in available_modules):
        detail_reasons.append("support")
    if not detail_reasons:
        detail_reasons.append("helping you")
    if len(detail_reasons) == 1:
        reason_text = detail_reasons[0]
    elif len(detail_reasons) == 2:
        reason_text = f"{detail_reasons[0]} or {detail_reasons[1]}"
    else:
        reason_text = ", ".join(detail_reasons[:-1]) + f", or {detail_reasons[-1]}"
    return f"This browser chat is handled by AWA for this business. Share contact details only when needed for {reason_text}."
@router.get("/public-chat/links/{slug}")
async def get_public_chat_link(slug: str, request: Request):
    await _check_public_rate_limit(request, f"link:{slug}")
    link = await _get_active_link(slug)
    await db_client.record_public_chat_link_open(link["id"])
    suggested_replies = ["Services & prices", "Opening hours", "Ask a question"]
    privacy_note = "This browser chat is handled by AWA for this business. Share contact details only when needed."
    try:
        config_json = await _get_public_config(link["tenant_id"])
        from backend.core.runtime_capabilities import build_runtime_capability_snapshot
        from backend.logic.engine.capability_menu import hydrate_public_catalog
        snapshot = await build_runtime_capability_snapshot(
            tenant_id=link["tenant_id"], config_data=config_json,
            plan_code=link.get("plan_code"), tenant_status=link.get("tenant_status"),
            tenant_name=link.get("tenant_name") or link.get("display_name") or "",
        )
        available_modules = snapshot.runtime_available_modules
        if snapshot.is_available("order"):
            await hydrate_public_catalog(link["tenant_id"], config_json)
        behavior_profile = await _resolve_public_chat_behavior_profile(
            tenant_id=link["tenant_id"],
            config_json=config_json,
            snapshot=snapshot,
        )
        suggested_replies = _build_public_chat_suggestions(config_json, available_modules, behavior_profile)
        privacy_note = _build_privacy_note(config_json, available_modules)
        captcha_required = _requires_public_chat_captcha(config_json)
        customer_otp_required = True
    except Exception as exc:
        logger.warning(f"Could not load public chat suggestions for {slug}: {exc}")
        captcha_required = False
        customer_otp_required = True
    return APIResponse.create_success(data={
        "slug": link["slug"],
        "display_name": link["display_name"],
        "tenant_name": link["tenant_name"],
        "status": link["status"],
        "privacy_note": privacy_note,
        "suggested_replies": suggested_replies,
        "captcha_required": captcha_required,
        "captcha_mode": settings.PUBLIC_CHAT_CAPTCHA_MODE if captcha_required else "disabled",
        "customer_otp_required": customer_otp_required,
    })
@router.get("/public-chat/links/{slug}/qr")
async def get_public_chat_link_qr(slug: str, request: Request):
    await _check_public_rate_limit(request, f"qr:{slug}")
    link = await _get_active_link(slug)
    public_url = f"{settings.AWA_PUBLIC_CHAT_BASE_URL.rstrip('/')}/chat/{link['slug']}"
    try:
        import io
        import qrcode
        import qrcode.image.svg
        image = qrcode.make(public_url, image_factory=qrcode.image.svg.SvgPathImage)
        stream = io.BytesIO()
        image.save(stream)
        return Response(content=stream.getvalue(), media_type="image/svg+xml")
    except Exception:
        return Response(
            content=f"<svg xmlns='http://www.w3.org/2000/svg' width='320' height='320'><rect width='320' height='320' fill='white'/><text x='160' y='150' text-anchor='middle' font-size='18'>AWA Chat</text><text x='160' y='182' text-anchor='middle' font-size='10'>{html.escape(str(link['slug']))}</text></svg>",
            media_type="image/svg+xml",
        )
@router.post("/public-chat/links/{slug}/sessions")
async def create_public_chat_session(
    slug: str,
    body: CreatePublicChatSessionRequest,
    request: Request,
    response: Response,
):
    await _check_public_rate_limit(request, f"session:{slug}")
    response.headers["Cache-Control"] = "no-store"
    link = await _get_active_link(slug)
    config_record = await db_client.get_tenant_config(link["tenant_id"])
    config_json = config_record.get("config_json", {}) if config_record else {}
    if isinstance(config_json, str):
        config_json = json.loads(config_json)
    config_json = config_json if isinstance(config_json, dict) else {}
    pinned_version = int(config_record.get("version") or 0) if config_record else 0
    if pinned_version < 1:
        raise HTTPException(status_code=503, detail="Chat is temporarily unavailable. Please try again.")
    if _requires_public_chat_captcha(config_json):
        await _verify_public_chat_captcha(body.captcha_token, request)
    session_id = uuid4()
    session_token = create_public_token("chat")
    session_token_hash = hash_public_chat_token(session_token, "session")
    channel_subject = f"awa_chat:{link['id']}:{session_id}"
    customer = await customer_service.get_or_create_awa_chat_customer(
        tenant_id=link["tenant_id"],
        channel_subject=channel_subject,
        name=body.visitor_name,
    )
    conversation = await db_client.get_or_create_conversation(
        tenant_id=link["tenant_id"],
        customer_id=customer["id"],
        conversation_id=uuid4(),
        channel_type=ChannelType.AWA_CHAT.value,
        channel_account_id=link["id"],
        channel_session_id=str(session_id),
    )
    session = await db_client.create_public_chat_session(
        session_id=session_id,
        tenant_id=link["tenant_id"],
        link_id=link["id"],
        customer_id=customer["id"],
        conversation_id=conversation["id"],
        session_token_hash=session_token_hash,
        visitor_name=body.visitor_name,
    )
    # Pre-create/initialize the conversation session state to COLLECTING_DATA so greeting isn't repeated
    try:
        from backend.pipeline.session_manager import session_manager
        from backend.core.context import ConversationSession, SessionState
        from backend.core.states import ConversationState
        new_session = ConversationSession(
            id=uuid4(),
            tenant_id=link["tenant_id"],
            conversation_id=conversation["id"],
            config_version=pinned_version,
            state=SessionState(
                workflow_state=ConversationState.NEW,
                orientation_config_pending=True,
            ),
            expires_at=None
        )
        state_envelope = {
            "config_version": new_session.config_version,
            "state": new_session.state.model_dump(mode='json')
        }
        await db_client.create_conversation_session(
            session_id=new_session.id,
            tenant_id=new_session.tenant_id,
            conversation_id=new_session.conversation_id,
            state_json=state_envelope,
            config_version=new_session.config_version,
            current_module=new_session.current_module,
            current_intent=new_session.current_intent,
        )
        await session_manager._cache_session(new_session)
    except Exception as exc:
        logger.error("Could not initialize public conversation state", extra={"extra_data": {"reason_code": "session_create_failed", "error_type": type(exc).__name__}})
        raise HTTPException(status_code=503, detail="Chat is temporarily unavailable. Please try again.") from exc
    # Insert greeting message to DB
    try:
        from backend.core.runtime_capabilities import build_runtime_capability_snapshot
        from backend.logic.engine.capability_menu import hydrate_public_catalog
        snapshot = await build_runtime_capability_snapshot(
            tenant_id=link["tenant_id"], config_data=config_json,
            plan_code=link.get("plan_code"), tenant_status=link.get("tenant_status"),
            tenant_name=link.get("tenant_name") or link.get("display_name") or "",
        )
        if snapshot.is_available("order"):
            await hydrate_public_catalog(link["tenant_id"], config_json)
        behavior_profile = await _resolve_public_chat_behavior_profile(
            tenant_id=link["tenant_id"],
            config_json=config_json,
            snapshot=snapshot,
        )
        capability_menu = build_capability_menu(
            config_json,
            business_name=link["display_name"],
            available_modules=snapshot.runtime_available_modules,
            behavior_profile_resolution=behavior_profile,
        )
        greeting_message = capability_menu.text
        if not greeting_message:
            greeting_message = config_json.get("greeting_message") or f"Hello! Welcome to {link['display_name']}."
        await _save_public_chat_greeting(
            tenant_id=link["tenant_id"],
            conversation_id=conversation["id"],
            session_id=session_id,
            greeting_message=greeting_message,
            suggestions=capability_menu.suggestions,
        )
    except Exception as exc:
        logger.warning(f"Could not save greeting message for public chat session: {exc}")
        try:
            fallback = config_json.get("greeting_message") or f"Hello! Welcome to {link['display_name']}. How can I help you today?"
            await _save_public_chat_greeting(
                tenant_id=link["tenant_id"],
                conversation_id=conversation["id"],
                session_id=session_id,
                greeting_message=fallback,
                suggestions=[],
            )
        except Exception as fallback_exc:
            logger.warning(f"Could not save fallback greeting message for public chat session: {fallback_exc}")
    data = _json_safe_record(session)
    data.update({
        "session_token": session_token,
        "tenant_name": link["tenant_name"],
        "display_name": link["display_name"],
    })
    return APIResponse.create_success(data=data)
@router.post("/public-chat/sessions/{session_id}/messages")
async def send_public_chat_message(
    session_id: UUID,
    body: PublicChatMessageRequest,
    request: Request,
):
    import asyncio
    await _check_public_rate_limit(request, f"message:{session_id}")
    text = body.message_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if text == "__SYSTEM_AUTO_CONFIRM__":
        raise HTTPException(status_code=400, detail="This message value is reserved and cannot be submitted.")
    session = await _get_active_session(session_id, body.session_token)
    await _require_otp_before_commitment(session_id, session, text)

    # 11.1 Stable authority inputs
    provider_message_id = f"awa:{session_id}:{body.client_message_id or uuid4()}"
    trace_id = uuid4()

    # 11.2 Security disposition
    from backend.ai.security import is_prompt_security_request
    security_disposition = "none"
    db_text = text
    if is_prompt_security_request(text):
        security_disposition = "prompt_security_flagged"
        db_text = "[Content flagged for security]"

    # 11.3 Durable intake call
    from backend.db.client import PublicChatTurnIntakeError
    import inspect
    from backend.db.client import PublicChatTurnIntakeOutcome
    try:
        intake = await db_client.create_or_get_public_chat_turn_intake(
            tenant_id=session["tenant_id"],
            conversation_id=session["conversation_id"],
            customer_id=session["customer_id"],
            public_chat_session_id=session_id,
            channel_account_id=session["link_id"],
            provider_message_id=provider_message_id,
            message_text=db_text,
            message_raw_payload={"source": "awa_chat", "client_message_id": body.client_message_id},
            security_disposition=security_disposition,
            trace_id=trace_id,
            event_max_attempts=3,
        )
        if not isinstance(intake, PublicChatTurnIntakeOutcome):
            raise PublicChatTurnIntakeError("intake_result_invalid", "Invalid durable intake result")
    except (PublicChatTurnIntakeError, TypeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Service is temporarily unavailable. Please try again."
        )

    # 11.4 Duplicate response
    if not intake.created:
        return APIResponse.create_success(data={
            "message_id": str(intake.message_id),
            "conversation_id": str(session["conversation_id"]),
            "status": "duplicate",
            "duplicate": True,
            "event_id": str(intake.event_id),
            "processing_status": intake.processing_status,
            "processing_code": "duplicate",
            "processing_message": "Duplicate message ignored.",
        })

    # Intake may have atomically refreshed an idle session's configuration
    # binding.  Remove any pre-turn cache entry before the worker hydrates it,
    # otherwise it could reintroduce the stale version selected before intake.
    try:
        from backend.pipeline.session_manager import session_manager
        await session_manager.invalidate_cache(session["tenant_id"], session["conversation_id"])
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "public_chat_session_cache_invalidate_failed",
            extra={"extra_data": {"error_type": type(exc).__name__, "event_id": str(intake.event_id)}},
        )

    # 11.5 New intake immediate publication
    from backend.pipeline.public_chat_intake import build_canonical_public_chat_turn
    from backend.pipeline.public_chat_queue import enqueue_public_chat_turn
    from backend.db.client import PublicChatTurnReplayError

    canonical = build_canonical_public_chat_turn(
        intake=intake,
        tenant_id=session["tenant_id"],
        conversation_id=session["conversation_id"],
        customer_id=session["customer_id"],
        public_chat_session_id=session_id,
        channel_account_id=session["link_id"],
        message_text=db_text,
        worker_version="1.0.0",
        max_retries=3,
    )

    redis_ok = False
    try:
        queue_status = await _enqueue_for_worker(canonical)
        if queue_status == "queued":
            redis_ok = True
    except Exception as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        logger.warning(
            "public_chat_immediate_enqueue_failed",
            extra={
                "extra_data": {
                    "error_type": type(exc).__name__,
                    "event_id": str(intake.event_id),
                    "trace_id": str(intake.trace_id),
                }
            }
        )

    sync_ok = False
    if redis_ok:
        try:
            sync_res = db_client.mark_public_chat_turn_queued(
                tenant_id=session["tenant_id"],
                conversation_id=session["conversation_id"],
                message_id=intake.message_id,
                event_id=intake.event_id,
            )
            if inspect.isawaitable(sync_res):
                sync_ok = await sync_res
            else:
                sync_ok = True
        except (PublicChatTurnReplayError, Exception) as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            logger.warning(
                "public_chat_queue_sync_failed",
                extra={
                    "extra_data": {
                        "error_type": type(exc).__name__,
                        "event_id": str(intake.event_id),
                        "trace_id": str(intake.trace_id),
                    }
                }
            )

    if redis_ok and sync_ok:
        return APIResponse.create_success(data={
            "message_id": str(intake.message_id),
            "conversation_id": str(session["conversation_id"]),
            "status": "queued",
            "duplicate": False,
            "event_id": str(intake.event_id),
            "processing_status": "queued",
            "processing_code": "queued",
            "processing_message": "Message saved and queued for AWA.",
        })
    else:
        return APIResponse.create_success(data={
            "message_id": str(intake.message_id),
            "conversation_id": str(session["conversation_id"]),
            "status": getattr(intake, "processing_status", "received"),
            "duplicate": False,
            "event_id": str(intake.event_id),
            "processing_status": "delayed",
            "processing_code": "worker_degraded",
            "safe_error": {
                "code": "worker_degraded",
                "safe_message": "Message saved, but AWA processing is delayed. Replies may take longer than usual.",
            },
            "processing_message": "Message saved, but AWA processing is delayed. Replies may take longer than usual.",
        })

@router.post("/public-chat/sessions/{session_id}/contact")
async def save_public_chat_contact(
    session_id: UUID,
    body: PublicChatContactRequest,
    request: Request,
):
    await _check_public_rate_limit(request, f"contact:{session_id}")
    session = await _get_active_session(session_id, body.session_token)
    email = _clean_contact_email(body.email)
    phone = _clean_contact_phone(body.phone)
    name = (body.name or "").strip() or None
    if not any([email, phone, name]):
        raise HTTPException(status_code=400, detail="Add at least one contact detail")
    contact_metadata: dict[str, Any] = {
        "public_chat_contact": {
            "phone": phone,
            "email": email,
            "phone_verified": False,
            "email_verified": False,
            "whatsapp_opted_in": bool(body.whatsapp_opted_in),
            "source": "public_chat_contact_form",
        }
    }
    updated = await customer_service.update_customer(
        tenant_id=session["tenant_id"],
        customer_id=session["customer_id"],
        name=name,
        email=email,
        metadata=contact_metadata,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Customer not found")
    return APIResponse.create_success(data={
        "saved": True,
        "email_saved": bool(email),
        "phone_saved": bool(phone),
    })
@router.post("/public-chat/sessions/{session_id}/otp/send")
async def send_public_chat_otp(
    session_id: UUID,
    body: PublicChatOtpRequest,
    request: Request,
):
    await _check_public_rate_limit(request, f"otp-send:{session_id}")
    session = await _get_active_session(session_id, body.session_token)
    phone = _clean_contact_phone(body.phone)
    if not phone:
        raise HTTPException(status_code=400, detail="Enter a phone number to verify.")
    conversation_session, pending_action, action, action_binding, _ = await _load_pending_action_context(session)
    if not _pending_requires_phone_possession(pending_action):
        raise HTTPException(status_code=409, detail="Phone verification is not needed for this action.")
    existing_state = _coerce_state_json(conversation_session.get("state_json"))
    if _session_has_phone_possession(existing_state, session, conversation_session, session_id):
        invalidated = await _invalidate_assurance_if_phone_changed(
            state_json=existing_state, session=session, conversation_session=conversation_session, phone=phone
        )
        if not invalidated:
            raise HTTPException(status_code=409, detail="This chat session is already phone verified.")
    if settings.CUSTOMER_OTP_MODE == "disabled":
        raise HTTPException(status_code=503, detail="Phone verification is not configured.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    if settings.CUSTOMER_OTP_MODE == "mock":
        if settings.ENV == "production":
            raise HTTPException(status_code=503, detail="Phone verification is not configured for production.")
        code = "123456"
    await _deliver_customer_otp(phone, code, action, session["tenant_id"], session_id)
    await _redis_set(
        _otp_challenge_key(session_id, action_binding, phone),
        code,
        ex=settings.CUSTOMER_OTP_TTL_SECONDS,
    )
    logger.info(f"Customer OTP requested for tenant={session['tenant_id']} session={session_id} action={action}")
    data: dict[str, Any] = {
        "sent": True,
        "action": action,
        "expires_in_seconds": settings.CUSTOMER_OTP_TTL_SECONDS,
        "mode": settings.CUSTOMER_OTP_MODE,
    }
    if settings.CUSTOMER_OTP_MODE == "mock" and settings.ENV != "production":
        data["test_code"] = code
    return APIResponse.create_success(data=data)
@router.post("/public-chat/sessions/{session_id}/otp/verify")
async def verify_public_chat_otp(
    session_id: UUID,
    body: PublicChatOtpVerifyRequest,
    request: Request,
):
    await _check_public_rate_limit(request, f"otp-verify:{session_id}")
    session = await _get_active_session(session_id, body.session_token)
    phone = _clean_contact_phone(body.phone)
    if not phone:
        raise HTTPException(status_code=400, detail="Enter a phone number to verify.")
    conversation_session, pending_action, action, action_binding, config_version = await _load_pending_action_context(session)
    if not _pending_requires_phone_possession(pending_action):
        raise HTTPException(status_code=409, detail="Phone verification is not needed for this action.")
    state_json = _coerce_state_json(conversation_session.get("state_json"))
    pending_action = _pending_action_from_state(state_json) or {}
    action_nonce = str(pending_action.get("action_nonce") or "")
    verification_ref = uuid4().hex
    continuation_claim_key = _otp_continuation_claim_key(session, action_nonce)
    from backend.core.redis_client import redis_client
    claim_status, challenge_ttl_ms = await claim_otp_challenge(
        redis_client,
        challenge_key=_otp_challenge_key(session_id, action_binding, phone),
        expected_code=body.code.strip(),
        claim_key=continuation_claim_key,
        claim_value=verification_ref,
        claim_ttl_seconds=settings.CUSTOMER_OTP_VERIFIED_TTL_SECONDS,
    )
    if claim_status == "consumed":
        raise HTTPException(status_code=409, detail="This verification code was already used.")
    if claim_status != "claimed":
        attempts = await _redis_incr_with_ttl(
            _otp_attempts_key(session_id, action_binding, phone),
            settings.CUSTOMER_OTP_TTL_SECONDS,
        )
        if attempts >= settings.CUSTOMER_OTP_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="Too many verification attempts. Request a new code.")
        raise HTTPException(status_code=400, detail="The verification code is invalid or expired.")
    guest_customer_id = session["customer_id"]
    existing_customer = await customer_service.get_customer_by_phone(
        tenant_id=session["tenant_id"],
        phone_number=phone,
    )
    if existing_customer:
        verified_customer_id = existing_customer["id"]

        # Merge name/aliases logic (M-3 defense)
        existing_name = existing_customer.get("name")
        visitor_name = session.get("visitor_name")

        update_kwargs = {"consent_status": "granted"}
        if not existing_name:
            if visitor_name:
                update_kwargs["name"] = visitor_name
        elif visitor_name and visitor_name != existing_name:
            meta = existing_customer.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            elif not isinstance(meta, dict):
                meta = {}

            aliases = meta.get("aliases") or []
            if not isinstance(aliases, list):
                aliases = []
            if visitor_name not in aliases:
                aliases.append(visitor_name)
            meta["aliases"] = aliases
            update_kwargs["metadata"] = meta

        if str(verified_customer_id) != str(guest_customer_id):
            async with db_client.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE bookings SET customer_id = $1 WHERE tenant_id = $2 AND customer_id = $3",
                        verified_customer_id, session["tenant_id"], guest_customer_id
                    )
                    await conn.execute(
                        "UPDATE orders SET customer_id = $1 WHERE tenant_id = $2 AND customer_id = $3",
                        verified_customer_id, session["tenant_id"], guest_customer_id
                    )
                    await conn.execute(
                        "UPDATE leads SET customer_id = $1 WHERE tenant_id = $2 AND customer_id = $3",
                        verified_customer_id, session["tenant_id"], guest_customer_id
                    )
                    await conn.execute(
                        "UPDATE payment_requests SET customer_id = $1 WHERE tenant_id = $2 AND customer_id = $3",
                        verified_customer_id, session["tenant_id"], guest_customer_id
                    )
                    await conn.execute(
                        "UPDATE review_requests SET customer_id = $1 WHERE tenant_id = $2 AND customer_id = $3",
                        verified_customer_id, session["tenant_id"], guest_customer_id
                    )
                    await conn.execute(
                        "UPDATE customer_notes SET customer_id = $1 WHERE tenant_id = $2 AND customer_id = $3",
                        verified_customer_id, session["tenant_id"], guest_customer_id
                    )
                    await conn.execute(
                        """
                        INSERT INTO customer_tag_links (tenant_id, customer_id, tag_id)
                        SELECT tenant_id, $1, tag_id
                        FROM customer_tag_links
                        WHERE tenant_id = $2 AND customer_id = $3
                        ON CONFLICT (tenant_id, customer_id, tag_id) DO NOTHING
                        """,
                        verified_customer_id, session["tenant_id"], guest_customer_id
                    )
                    await conn.execute(
                        "DELETE FROM customer_tag_links WHERE tenant_id = $1 AND customer_id = $2",
                        session["tenant_id"], guest_customer_id
                    )
            await customer_service.update_customer(
                tenant_id=session["tenant_id"],
                customer_id=verified_customer_id,
                **update_kwargs,
            )
            await customer_service.update_public_chat_session_customer(
                tenant_id=session["tenant_id"],
                session_id=session_id,
                conversation_id=session["conversation_id"],
                customer_id=verified_customer_id,
            )
            await customer_service.delete_customer(
                tenant_id=session["tenant_id"],
                customer_id=guest_customer_id,
            )
            session["customer_id"] = verified_customer_id
        else:
            await customer_service.update_customer(
                tenant_id=session["tenant_id"],
                customer_id=guest_customer_id,
                phone_number=phone,
                **update_kwargs,
            )
    else:
        await customer_service.update_customer(
            tenant_id=session["tenant_id"],
            customer_id=guest_customer_id,
            phone_number=phone,
            consent_status="granted",
        )
    marked = await _mark_session_verified_action(
        session,
        action,
        phone,
        action_binding=action_binding,
        verification_ref=verification_ref,
        session_id=session_id,
        config_version=config_version,
    )
    if not marked:
        await restore_otp_challenge_after_enqueue_failure(
            redis_client,
            challenge_key=_otp_challenge_key(session_id, action_binding, phone),
            expected_code=body.code.strip(),
            claim_key=continuation_claim_key,
            claim_value=verification_ref,
            remaining_ttl_ms=challenge_ttl_ms,
        )
        raise HTTPException(status_code=409, detail="The pending action changed. Request a new verification code.")
    # OTP establishes phone possession only.  The customer must still make a
    # separate, explicit confirmation before a pending booking/order executes.
    # Do not enqueue an internal continuation here: that continuation would
    # otherwise be interpreted as confirmation without a customer action.
    queue_status = "verified"
    await _redis_set(
        _otp_verified_key(session_id, action_binding),
        verification_ref,
        ex=settings.CUSTOMER_OTP_VERIFIED_TTL_SECONDS,
    )
    await _redis_delete(_otp_attempts_key(session_id, action_binding, phone))
    return APIResponse.create_success(data={
        "verified": True,
        "action": action,
        "processing_status": queue_status,
        "expires_in_seconds": settings.CUSTOMER_OTP_VERIFIED_TTL_SECONDS,
    })
@router.post("/public-chat/sessions/{session_id}/end")
async def end_public_chat_session(
    session_id: UUID,
    body: PublicChatEndRequest,
    request: Request,
):
    await _check_public_rate_limit(request, f"end:{session_id}")
    session = await _get_active_session(session_id, body.session_token)
    ended = await db_client.update_public_chat_session_status(
        tenant_id=session["tenant_id"],
        session_id=session_id,
        status="closed",
    )
    if not ended:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return APIResponse.create_success(data=_json_safe_record(ended))
async def _list_public_chat_messages(session_id: UUID, session_token: str) -> list[dict[str, Any]]:
    session = await _get_active_session(session_id, session_token)
    conv = await db_client.get_conversation(session["tenant_id"], session["conversation_id"])
    conv_status = conv.get("status") if conv else "active"
    result = await db_client.list_messages(
        session["tenant_id"],
        session["conversation_id"],
        limit=100,
    )
    items = [
        item
        for item in reversed(result["items"])
        if not str(item.get("provider_message_id") or "").startswith(
            "awa-internal:otp_verified_pending_action:"
        )
    ]
    latest_outbound_id = next(
        (
            str(item.get("id"))
            for item in reversed(items)
            if item.get("direction") == "outbound"
        ),
        None,
    )
    return [
        {**_public_chat_message_record(item, latest_outbound_id, session=session), "conversation_status": conv_status}
        for item in items
    ]
@router.post("/public-chat/sessions/{session_id}/messages/list")
async def list_public_chat_messages_post(
    session_id: UUID,
    body: PublicChatEndRequest,
    request: Request,
    response: Response,
):
    await _check_public_rate_limit(request, f"list:{session_id}")
    response.headers["Cache-Control"] = "no-store"
    return APIResponse.create_success(data=await _list_public_chat_messages(session_id, body.session_token))
@router.get("/public-chat/sessions/{session_id}/events")
async def public_chat_events(
    session_id: UUID,
    token: str,
    request: Request,
):
    import asyncio
    from fastapi.responses import StreamingResponse
    from backend.realtime.public_chat_events import get_public_chat_channel
    # Validate session and verify ownership
    session = await _get_active_session(session_id, token)
    conversation_id = session.get("conversation_id")
    if not conversation_id:
        raise HTTPException(status_code=400, detail="No active conversation for this session")
    # Verify Redis is available
    try:
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    except Exception as e:
        logger.error(f"Redis unavailable for SSE route: {e}")
        raise HTTPException(status_code=503, detail="Realtime updates are temporarily unavailable")
    async def event_generator():
        channel = get_public_chat_channel(conversation_id)
        # Read Last-Event-ID from request headers to support replay after disconnect
        last_id = request.headers.get("Last-Event-ID") or request.headers.get("last-event-id") or "$"
        try:
            yield f"data: {json.dumps({'event_type': 'connected'})}\n\n"
            last_ping_time = time.time()
            ping_interval = 15.0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    streams_data = await redis_client.xread(
                        {channel: last_id},
                        count=10,
                        block=1000
                    )
                    if streams_data:
                        for stream_name, messages in streams_data:
                            for msg_id, fields in messages:
                                last_id = msg_id
                                yield f"id: {msg_id}\ndata: {fields['payload']}\n\n"
                except Exception as e:
                    logger.warning(f"Error fetching message from Redis Stream: {e}")
                    await asyncio.sleep(1)
                now = time.time()
                if now - last_ping_time >= ping_interval:
                    yield f"data: {json.dumps({'event_type': 'ping'})}\n\n"
                    last_ping_time = now
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in SSE event stream generator: {e}")
        finally:
            try:
                await redis_client.aclose()
            except Exception as e:
                logger.warning(f"Error closing Redis client: {e}")
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

class CancelHandoffRequest(BaseModel):
    session_token: str

@router.post("/public-chat/sessions/{session_id}/cancel-handoff")
async def cancel_public_chat_handoff(
    session_id: UUID,
    body: CancelHandoffRequest,
):
    session = await _get_active_session(session_id, body.session_token)
    conversation_id = session.get("conversation_id")
    tenant_id = session.get("tenant_id")
    if not conversation_id or not tenant_id:
        raise HTTPException(status_code=400, detail="Invalid session state")

    from backend.domains.support.service import support_service
    from backend.api.routes.conversations import _set_session_state
    from backend.core.states import ConversationState

    conversation = await support_service.return_to_awa(tenant_id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await _set_session_state(tenant_id, conversation_id, ConversationState.INTENT_IDENTIFIED)
    from backend.pipeline.session_manager import session_manager
    await session_manager.invalidate_cache(tenant_id, conversation_id)

    return APIResponse.create_success(data={"status": "returned_to_awa"})
