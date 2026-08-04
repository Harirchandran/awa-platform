import json
import re
from typing import Any, Dict, List
from urllib.parse import urlsplit, urlunsplit
from backend.ai.security import CONTROL_CHAR_PATTERN
WHATSAPP_TEXT_LIMIT = 3900
AWA_CHAT_TEXT_LIMIT = 8000
INTERNAL_TOOL_KEYS = {"tool_key", "denied_reason", "validation_errors", "created_resource_type", "created_resource_id"}
CARD_TYPES = {"catalog_item", "service", "category"}
SAFE_RESPONSE_FALLBACK = "I couldn't complete that request right now. Please try again in a moment or ask to talk to a team member."

def redact_url_tokens(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact_url_tokens(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_url_tokens(item) for item in value]
    if not isinstance(value, str):
        return value
    if "://" not in value and "?" not in value:
        return value
    try:
        parsed = urlsplit(value)
    except Exception:
        return value
    if not parsed.query:
        return value
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "[redacted]", parsed.fragment))

def parse_response_envelope(response_text: Any) -> Dict[str, Any] | None:
    from backend.pipeline.interactive_parser import sanitize_quick_replies

    payload = _payload_dict(response_text)
    if not isinstance(payload, dict):
        return None
    if "response_text" not in payload and "quick_replies" not in payload:
        return None
    quick_replies = payload.get("quick_replies")
    if not isinstance(quick_replies, list):
        quick_replies = []
    return {
        "response_text": str(payload.get("response_text") or "").strip(),
        "quick_replies": sanitize_quick_replies([str(option) for option in quick_replies]),
    }

def looks_like_response_envelope(response_text: Any) -> bool:
    if not isinstance(response_text, str):
        return False
    raw = response_text.strip().lower()
    if not raw.startswith("{"):
        return False
    return '"response_text"' in raw or "'response_text'" in raw or '"quick_replies"' in raw or "'quick_replies'" in raw

def normalize_channel_payload(response_text: Any, channel_type: str, quick_replies: List[str] | None = None) -> Dict[str, Any]:
    from backend.pipeline.interactive_parser import (
        build_interactive_payload_from_options,
        parse_interactive_hints,
        sanitize_quick_replies,
        strip_interactive_hints,
    )

    # Extract customer text first
    envelope = parse_response_envelope(response_text)
    if envelope:
        raw_text = envelope["response_text"]
        explicit_quick_replies = sanitize_quick_replies(quick_replies or envelope["quick_replies"])
    elif looks_like_response_envelope(response_text):
        raw_text = SAFE_RESPONSE_FALLBACK
        explicit_quick_replies = sanitize_quick_replies(quick_replies or [])
    else:
        raw_text = _customer_text_from_tool_payload(response_text)
        explicit_quick_replies = sanitize_quick_replies(quick_replies or [])

    # Parse interactive hints on the raw text BEFORE truncation, only when no explicit options exist.
    clean_text = raw_text
    interactive_data = None
    if explicit_quick_replies:
        clean_text = strip_interactive_hints(raw_text) or raw_text
        interactive_data = build_interactive_payload_from_options(clean_text, explicit_quick_replies)
    elif str(channel_type).lower() == "awa_chat":
        clean_text, interactive_data = parse_interactive_hints(raw_text)

    # Normalize and truncate the clean text
    normalized_text = normalize_channel_response(clean_text, channel_type)

    payload = {
        "text": normalized_text,
        "cards": extract_safe_cards(response_text),
    }
    if interactive_data:
        payload["interactive_data"] = interactive_data
    return payload

def extract_safe_cards(response_text: Any) -> List[Dict[str, Any]]:
    payload = _payload_dict(response_text)
    if not payload:
        return []
    cards = payload.get("cards")
    if not isinstance(cards, list):
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        cards = data.get("cards") if isinstance(data, dict) else None
    if not isinstance(cards, list):
        return []
    safe_cards: List[Dict[str, Any]] = []
    for card in cards[:5]:
        safe = _safe_card(card)
        if safe:
            safe_cards.append(safe)
    return safe_cards

def _payload_dict(response_text: Any) -> Dict[str, Any] | None:
    if isinstance(response_text, dict):
        return response_text
    if not isinstance(response_text, str):
        return None
    raw = response_text.strip()
    if not (raw.startswith("{") and raw.endswith("}")):
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None

def _safe_card(card: Any) -> Dict[str, Any] | None:
    if not isinstance(card, dict):
        return None
    card_type = str(card.get("type") or "").strip()
    if card_type not in CARD_TYPES:
        return None
    image = card.get("image") if isinstance(card.get("image"), dict) else {}
    image_url = str(card.get("image_url") or image.get("url") or "").strip()
    thumbnail_url = str(card.get("thumbnail_url") or image.get("thumbnail_url") or image_url).strip()
    safe = {
        "type": card_type,
        "id": str(card.get("id") or "")[:80],
        "title": sanitize_customer_visible_output(str(card.get("title") or card.get("name") or "")[:160]),
        "subtitle": sanitize_customer_visible_output(str(card.get("subtitle") or card.get("price") or "")[:120]),
        "description": sanitize_customer_visible_output(str(card.get("description") or "")[:500]),
        "image_url": image_url,
        "thumbnail_url": thumbnail_url,
        "image_alt": sanitize_customer_visible_output(str(card.get("image_alt") or image.get("alt_text") or card.get("title") or "")[:300]),
        "actions": [
            str(action)[:40]
            for action in (card.get("actions") or [])
            if isinstance(action, str)
        ][:3],
    }
    if safe["image_url"] and not safe["image_url"].startswith(("/api/v1/media/assets/", "http://", "https://")):
        safe["image_url"] = ""
    if safe["thumbnail_url"] and not safe["thumbnail_url"].startswith(("/api/v1/media/assets/", "http://", "https://")):
        safe["thumbnail_url"] = ""
    return safe
def is_high_risk_content(text: str) -> bool:
    lowered = text.lower()
    # 1. Direct high-risk substrings
    high_risk_substrings = [
        "(function=",
        "<function=",
        "booking_execute",
        "order_execute",
        "payment_execute",
        "reminder_execute",
        "review_execute",
        "tool_call",
        "toolregistry",
        "raw_payload",
        "begin_system",
        "system prompt",
        "system prompts",
        "developer message",
        "developer messages",
        "internal instruction",
        "internal instructions",
        "tool detail",
        "tool details",
        "tool schema",
        "tool schemas",
    ]
    for sub in high_risk_substrings:
        if sub in lowered:
            return True
    # 2. Stack trace / Exception classes
    if "traceback (most recent call last)" in lowered:
        return True
    if re.search(r'file\s+["\'][^"\']+\.py["\'],\s+line\s+\d+', lowered):
        return True
    if re.search(r'\b(valueerror|keyerror|typeerror|runtimeerror|attributeerror|indexerror|zerodivisionerror|exception|dispatcherror|dbclienterror|toolerror)\b:', lowered):
        return True
    # 3. Raw JSON tool argument patterns
    json_keys = [
        "patient_name", "phone", "service_id", "preferred_date", "preferred_time", "customer_name",
        "item_id", "quantity", "fulfillment_type", "consent_status", "email", "reason", "time", "date"
    ]
    no_spaces = lowered.replace(" ", "").replace("\n", "").replace("\r", "")
    for key in json_keys:
        if f'"{key}":' in no_spaces or f"'{key}':" in no_spaces:
            return True
    # 4. Internal schema / debug text
    if "properties" in lowered and "required" in lowered and "type" in lowered:
        return True
    return False
def is_internal_json_pattern(text: str) -> bool:
    cleaned = str(text or "").strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            keys = set(parsed.keys())
            if "intent" in keys or "confidence" in keys:
                return True
            if "tool" in keys or "action" in keys or "classification" in keys:
                return True
            if "tool_key" in keys or "denied_reason" in keys:
                return True
            # Check for tool arguments
            tool_args = {"service_id", "preferred_date", "preferred_time", "customer_name", "quantity", "fulfillment_type", "amount", "reminder_type", "time_offset", "feedback", "rating"}
            if keys & tool_args and len(keys) >= 2:
                return True
    except Exception:
        pass

    cleaned_lower = cleaned.lower()
    if '{"intent":' in cleaned_lower or '{"confidence":' in cleaned_lower:
        return True
    if '{"tool":' in cleaned_lower or '{"action":' in cleaned_lower or '{"classification":' in cleaned_lower:
        return True
    return False

def sanitize_customer_visible_output(text: str) -> str:
    text = re.sub(r"(?i)\b(?:BEGIN|END)_UNTRUSTED_CONTEXT\b", "", str(text or ""))
    # Collapse multiple horizontal spaces, but preserve newlines for Markdown lists
    text = re.sub(r"[ \t]{2,}", " ", text).strip()

    # Redact UUID patterns
    text = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "[redacted]", text)

    if looks_like_response_envelope(text):
        return SAFE_RESPONSE_FALLBACK
    if is_internal_json_pattern(text):
        return SAFE_RESPONSE_FALLBACK
    if is_high_risk_content(text):
        return "I can help with that, but I need to handle the details safely. Please send the details again or choose one of the available options."
    return text
def normalize_channel_response(response_text: Any, channel_type: str) -> str:
    """Return customer-visible text that is safe for the outbound channel."""
    text = _customer_text_from_tool_payload(response_text)
    text = CONTROL_CHAR_PATTERN.sub("", str(text or "")).strip()
    text = _strip_internal_debug_lines(text)
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    if not text:
        text = SAFE_RESPONSE_FALLBACK
    text = sanitize_customer_visible_output(text)
    channel = str(channel_type or "").lower()
    limit = WHATSAPP_TEXT_LIMIT if channel == "whatsapp" else AWA_CHAT_TEXT_LIMIT
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 40)].rstrip() + "\n\n...[message shortened]"
def _customer_text_from_tool_payload(response_text: Any) -> str:
    if isinstance(response_text, dict):
        return _extract_user_message(response_text)
    raw = str(response_text or "").strip()
    if not raw:
        return ""
    if raw.startswith("{") and raw.endswith("}"):
        try:
            payload = json.loads(raw)
        except Exception:
            if looks_like_response_envelope(raw):
                return SAFE_RESPONSE_FALLBACK
            return raw
        if isinstance(payload, dict):
            return _extract_user_message(payload)
    if looks_like_response_envelope(raw):
        return SAFE_RESPONSE_FALLBACK
    return raw
def _extract_user_message(payload: Dict[str, Any]) -> str:
    response_text = payload.get("response_text")
    if response_text:
        return str(response_text)
    user_message = payload.get("user_message")
    if user_message:
        return str(user_message)

    tool_key = payload.get("tool_key")
    if tool_key and isinstance(tool_key, str):
        module_name = tool_key.split(".")[0]
        fallback = f"I'm having trouble with the {module_name.replace('_', ' ')} system right now. Please try again in a moment or ask to talk to a team member."
    else:
        fallback = SAFE_RESPONSE_FALLBACK

    if payload.get("success") is False:
        return fallback
    if payload.get("success") is True:
        return "Done."
    if "intent" in payload or "confidence" in payload:
        return fallback
    return fallback
def _strip_internal_debug_lines(text: str) -> str:
    safe_lines = []
    for line in text.splitlines():
        lowered = line.strip().lower()
        if not lowered:
            safe_lines.append("")
            continue
        if lowered.startswith(("tool_key:", "denied_reason:", "validation_errors:", "trace_id:", "debug:")):
            continue
        safe_lines.append(line)
    return "\n".join(safe_lines).strip()
