from typing import Any, Dict, List

from backend.modules.base_module import ToolMetadata


DEFAULT_BOOKING_FIELDS: List[Dict[str, Any]] = [
    {"key": "service_name", "label": "Service Name", "type": "string", "required": True},
    {"key": "preferred_date", "label": "Preferred Date", "type": "date", "required": True},
    {"key": "preferred_time", "label": "Preferred Time", "type": "time", "required": True},
    {"key": "customer_name", "label": "Name", "type": "string", "required": True},
]


def get_booking_tool_metadata(config: Dict[str, Any]) -> ToolMetadata:
    return ToolMetadata(
        risk_level="sensitive_write",
        requires_confirmation=True,
        requires_verified_customer=True,
        idempotency_scope="conversation",
    )


def get_booking_required_fields(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    booking_config = config.get("modules", {}).get("booking", {})
    fields = booking_config.get("fields", [])
    if fields:
        return fields
    if booking_config.get("enabled", False):
        return DEFAULT_BOOKING_FIELDS
    return []


def get_booking_tool_schema(config: Dict[str, Any]) -> Dict[str, Any]:
    fields = get_booking_required_fields(config)
    properties = {}
    required = []
    for field in fields:
        key = field.get("key")
        if not key:
            continue
        if key == "service_id":
            key = "service_name"
        properties[key] = {
            "type": "string" if field.get("type") != "number" else "integer",
            "description": "Name of the service to book (do not provide internal database IDs or UUIDs)" if key == "service_name" else field.get("label", key),
        }
        if field.get("required"):
            required.append(key)

    # Service selection is a booking-domain prerequisite, not a tenant custom
    # field.  Custom field configurations may therefore omit it while the
    # public booking action still requires the resolved canonical service name.
    properties.setdefault("service_name", {
        "type": "string",
        "description": "Name of the service to book (do not provide internal database IDs or UUIDs)",
    })
    if "service_id" in required:
        required.remove("service_id")
    if "service_name" not in required:
        required.append("service_name")

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def get_booking_update_tool_metadata(config: Dict[str, Any]) -> ToolMetadata:
    return ToolMetadata(
        risk_level="sensitive_write",
        requires_confirmation=True,
        requires_verified_customer=True,
        idempotency_scope="conversation",
    )


def get_booking_update_tool_schema(config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "booking_id": {
                "type": "string",
                "description": "The unique booking ID (UUID) to update or cancel.",
            },
            "action": {
                "type": "string",
                "enum": ["reschedule", "cancel"],
                "description": "The action to perform: reschedule or cancel.",
            },
            "new_date": {
                "type": "string",
                "description": "Optional new date for rescheduling (YYYY-MM-DD or fuzzy date like tomorrow).",
            },
            "new_time": {
                "type": "string",
                "description": "Optional new time for rescheduling (HH:MM or fuzzy time like 10 AM).",
            },
        },
        "required": ["booking_id", "action"],
    }
