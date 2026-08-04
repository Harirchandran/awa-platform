from typing import Any, Dict, List

from backend.modules.base_module import ToolMetadata


DEFAULT_LEAD_CAPTURE_FIELDS: List[Dict[str, Any]] = [
    {"key": "name", "label": "Name", "type": "string", "required": True},
    {"key": "interest", "label": "Interested In", "type": "string", "required": True},
]


def get_lead_capture_tool_metadata(config: Dict[str, Any]) -> ToolMetadata:
    return ToolMetadata(
        risk_level="write",
        requires_confirmation=False,
        requires_verified_customer=False,
        idempotency_scope="customer",
    )


def get_lead_capture_required_fields(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    lead_config = config.get("modules", {}).get("lead_capture", {})
    return lead_config.get("fields", DEFAULT_LEAD_CAPTURE_FIELDS)


def get_lead_capture_tool_schema(config: Dict[str, Any]) -> Dict[str, Any]:
    fields = get_lead_capture_required_fields(config)
    properties = {}
    required = []
    for field in fields:
        key = field.get("key")
        if not key:
            continue
        field_type = "integer" if field.get("type") == "number" else "string"
        properties[key] = {
            "type": field_type,
            "description": field.get("label", key),
        }
        options = field.get("options")
        if isinstance(options, list) and options:
            properties[key]["enum"] = [str(option) for option in options if str(option).strip()]
        if field.get("required"):
            required.append(key)

    properties.setdefault(
        "name",
        {
            "type": "string",
            "description": (
                "The real full name of the customer/lead. This MUST be extracted from the conversation "
                "history or customer context. Do not hallucinate or use placeholder values like "
                "'Valued Patient', 'Valued Customer', 'Patient', 'Guest', or 'Unknown'. If the user "
                "has not provided their name in the conversation, the agent must ask the user for "
                "their name before invoking this tool."
            ),
        },
    )
    properties.setdefault("interest", {"type": "string", "description": "Product/Service interest or requirement"})
    properties.setdefault("phone", {"type": "string", "description": "Phone number"})
    properties.setdefault("email", {"type": "string", "description": "Email address"})
    properties.setdefault("preferred_contact_time", {"type": "string", "description": "Preferred contact time"})
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
