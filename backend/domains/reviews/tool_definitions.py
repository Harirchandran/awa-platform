from typing import Any, Dict, List

from backend.modules.base_module import ToolMetadata


DEFAULT_REVIEW_FIELDS: List[Dict[str, Any]] = [
    {"key": "rating", "label": "Rating (1-5)", "type": "number", "required": True},
    {"key": "feedback", "label": "Written Feedback", "type": "string", "required": False},
]


def get_reviews_tool_metadata(config: Dict[str, Any]) -> ToolMetadata:
    review_config = config.get("modules", {}).get("reviews", {})
    requires_verified_customer = bool(
        isinstance(review_config, dict)
        and review_config.get("requires_verified_customer") is True
    )
    return ToolMetadata(
        risk_level="sensitive_write",
        requires_confirmation=True,
        requires_verified_customer=requires_verified_customer,
        idempotency_scope="conversation",
    )


def get_reviews_required_fields(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    review_config = config.get("modules", {}).get("reviews", {})
    fields = review_config.get("fields", [])
    if fields:
        return fields
    if review_config.get("enabled", False):
        return DEFAULT_REVIEW_FIELDS
    return []


def get_reviews_tool_schema(config: Dict[str, Any]) -> Dict[str, Any]:
    fields = get_reviews_required_fields(config)
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

    properties.setdefault("feedback_text", {"type": "string", "description": "Written feedback alias."})
    properties.setdefault("reference", {"type": "string", "description": "Optional review reference."})
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
