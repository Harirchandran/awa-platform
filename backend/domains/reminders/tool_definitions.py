from typing import Any, Dict, List

from backend.modules.base_module import ToolMetadata


ALLOWED_TRIGGER_TYPES = {
    "manual",
    "before_booking",
    "after_booking",
    "before_payment_expiry",
    "after_payment_failed",
    "after_order_completed",
    "inactive_conversation",
    "custom",
}

CONCIERGE_REMINDER_CHANNELS = ("whatsapp", "manual")

DEFAULT_REMINDER_FIELDS: List[Dict[str, Any]] = [
    {"key": "reminder_type", "label": "Type (booking/payment/order)", "type": "string", "required": True},
    {"key": "time_offset", "label": "Minutes from now", "type": "number", "required": True},
    {
        "key": "channel",
        "label": "Delivery channel",
        "type": "select",
        "required": True,
        "options": list(CONCIERGE_REMINDER_CHANNELS),
    },
]


def get_reminders_tool_metadata(config: Dict[str, Any]) -> ToolMetadata:
    return ToolMetadata(
        risk_level="sensitive_write",
        requires_confirmation=True,
        requires_verified_customer=True,
        idempotency_scope="conversation",
    )


def get_concierge_reminder_channels(config: Dict[str, Any]) -> List[str]:
    """Return configured channels that the concierge can actually persist safely."""
    reminder_config = config.get("modules", {}).get("reminders", {})
    fields = reminder_config.get("fields", []) if isinstance(reminder_config, dict) else []
    for field in fields if isinstance(fields, list) else []:
        if not isinstance(field, dict) or str(field.get("key") or "").strip() != "channel":
            continue
        options = field.get("options")
        if not isinstance(options, list):
            break
        channels: List[str] = []
        for option in options:
            normalized = str(option or "").strip().lower()
            if normalized in CONCIERGE_REMINDER_CHANNELS and normalized not in channels:
                channels.append(normalized)
        return channels
    return list(CONCIERGE_REMINDER_CHANNELS)


def get_reminders_required_fields(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    reminder_config = config.get("modules", {}).get("reminders", {})
    raw_fields = reminder_config.get("fields", []) if isinstance(reminder_config, dict) else []
    fields = [dict(field) for field in raw_fields if isinstance(field, dict)]
    if not fields and isinstance(reminder_config, dict) and reminder_config.get("enabled", False):
        fields = [dict(field) for field in DEFAULT_REMINDER_FIELDS]
    if not fields:
        return []

    channels = get_concierge_reminder_channels(config)
    for field in fields:
        if str(field.get("key") or "").strip() == "channel":
            field.update({
                "label": field.get("label") or "Delivery channel",
                "type": "select",
                "required": True,
                "options": channels,
            })
            break
    else:
        fields.append({
            "key": "channel",
            "label": "Delivery channel",
            "type": "select",
            "required": True,
            "options": channels,
        })
    return fields


def get_reminders_tool_schema(config: Dict[str, Any]) -> Dict[str, Any]:
    fields = get_reminders_required_fields(config)
    properties = {}
    required = []
    for field in fields:
        key = field.get("key")
        if not key:
            continue
        properties[key] = {
            "type": "string" if field.get("type") != "number" else "integer",
            "description": field.get("label", key),
        }
        options = field.get("options")
        if isinstance(options, list) and options:
            properties[key]["enum"] = [str(option) for option in options if str(option).strip()]
        if field.get("required"):
            required.append(key)

    properties.setdefault("scheduled_for", {"type": "string", "description": "ISO timestamp for the reminder."})
    properties.setdefault("schedule_at", {"type": "string", "description": "ISO timestamp alias for the reminder."})
    properties.setdefault("message_text", {"type": "string", "description": "Reminder message text."})
    properties.setdefault("message", {"type": "string", "description": "Reminder message text alias."})
    properties.setdefault("note", {"type": "string", "description": "Reminder message text alias."})
    properties.setdefault("channel", {
        "type": "string",
        "description": "Delivery channel: whatsapp or manual staff follow-up.",
        "enum": get_concierge_reminder_channels(config),
    })
    properties.setdefault("reference", {"type": "string", "description": "Optional business reference for idempotency."})
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
