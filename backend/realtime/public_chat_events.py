import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from backend.core.redis_client import get_redis_client
logger = logging.getLogger(__name__)
def get_public_chat_channel(conversation_id: Any) -> str:
    return f"public_chat:conversation:{conversation_id}"
def _extract_quick_replies(raw_payload: Optional[Dict[str, Any]]) -> list[str]:
    if not raw_payload:
        return []
    options = []
    action = raw_payload.get("action") or {}
    if not isinstance(action, dict):
        action = {}
    for button in action.get("buttons") or []:
        if isinstance(button, dict):
            title = str(button.get("reply", {}).get("title") or "").strip()
            if title:
                options.append(title)
    for section in action.get("sections") or []:
        if isinstance(section, dict):
            for row in section.get("rows") or []:
                if isinstance(row, dict):
                    title = str(row.get("title") or "").strip()
                    if title:
                        options.append(title)
    deduped = []
    seen = set()
    for opt in options:
        if opt.lower() not in seen:
            seen.add(opt.lower())
            deduped.append(opt)
    return deduped[:10]

def build_public_chat_message_event(
    conversation_id: Any,
    message_id: Any,
    content: str,
    created_at: Optional[Any] = None,
    raw_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    if hasattr(created_at, "isoformat"):
        created_at_str = created_at.isoformat()
    else:
        created_at_str = str(created_at)

    event = {
        "event_type": "message",
        "conversation_id": str(conversation_id),
        "message_id": str(message_id),
        "role": "assistant",
        "content": content,
        "created_at": created_at_str,
    }

    if raw_payload:
        from backend.pipeline.channel_output import extract_safe_cards, redact_url_tokens
        clean_raw_payload = dict(raw_payload)
        clean_raw_payload.pop("provider_error", None)
        clean_raw_payload.pop("metrics", None)

        event["cards"] = extract_safe_cards(clean_raw_payload)
        event["raw_payload"] = redact_url_tokens(clean_raw_payload)
        event["quick_replies"] = _extract_quick_replies(clean_raw_payload)
        event["quick_replies_state"] = "active"
        conversation_status = str(
            clean_raw_payload.get("conversation_status") or ""
        ).strip().lower()
        if conversation_status in {"active", "escalated"}:
            event["conversation_status"] = conversation_status
    else:
        event["cards"] = []
        event["raw_payload"] = {}
        event["quick_replies"] = []
        event["quick_replies_state"] = "none"

    return event

async def publish_public_chat_message_event(
    conversation_id: Any,
    message_id: Any,
    content: str,
    created_at: Optional[Any] = None,
    raw_payload: Optional[Dict[str, Any]] = None,
) -> bool:
    try:
        client = get_redis_client()
        channel = get_public_chat_channel(conversation_id)
        event = build_public_chat_message_event(
            conversation_id=conversation_id,
            message_id=message_id,
            content=content,
            created_at=created_at,
            raw_payload=raw_payload,
        )
        payload_str = json.dumps(event)
        await client.xadd(channel, {"payload": payload_str}, maxlen=100)
        await client.publish(channel, payload_str)
        return True
    except Exception as e:
        logger.warning(f"Failed to publish public chat realtime event to Redis: {e}")
        return False

async def publish_public_chat_terminal_failure(
    *,
    conversation_id: Any,
    message_id: Any,
    content: str,
    created_at: Optional[Any],
    source_event_id: str,
) -> bool:
    try:
        client = get_redis_client()
        channel = get_public_chat_channel(conversation_id)

        event = build_public_chat_message_event(
            conversation_id=conversation_id,
            message_id=message_id,
            content=content,
            created_at=created_at,
        )

        event["processing_status"] = "terminal_failed"
        event["failure_code"] = "processing_retries_exhausted"
        event["recoverable"] = False
        event["source_event_id"] = source_event_id
        event["quick_replies"] = []

        # Remove extra keys that build_public_chat_message_event adds by default that are not in the contract
        event.pop("cards", None)
        event.pop("raw_payload", None)
        event.pop("quick_replies_state", None)

        payload_str = json.dumps(event)
        await client.xadd(channel, {"payload": payload_str}, maxlen=100)
        await client.publish(channel, payload_str)
        return True
    except Exception as e:
        logger.warning(f"Failed to publish public chat terminal failure event to Redis: {e}")
        return False
