from typing import Any, Dict, List

from backend.modules.base_module import ToolMetadata


DEFAULT_SUPPORT_FIELDS: List[Dict[str, Any]] = [
    {"key": "issue_description", "label": "Issue Description", "type": "string", "required": True},
    {"key": "urgency", "label": "Urgency (low/medium/high)", "type": "string", "required": False},
]

DEFAULT_HUMAN_HANDOFF_FIELDS: List[Dict[str, Any]] = [
    {"key": "reason", "label": "Reason for Handoff", "type": "string", "required": True},
]


def get_support_tool_metadata(config: Dict[str, Any]) -> ToolMetadata:
    return ToolMetadata(
        risk_level="write",
        requires_confirmation=False,
        requires_verified_customer=False,
        idempotency_scope="conversation",
        allowed_when_paused=True,
    )


def get_support_required_fields(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    support_config = config.get("modules", {}).get("support", {})
    fields = support_config.get("fields", [])
    if fields:
        return fields
    if support_config.get("enabled", False):
        return DEFAULT_SUPPORT_FIELDS
    return []


def get_support_tool_schema(config: Dict[str, Any]) -> Dict[str, Any]:
    return _schema_from_fields(get_support_required_fields(config))


def get_human_handoff_tool_metadata(config: Dict[str, Any]) -> ToolMetadata:
    return ToolMetadata(
        risk_level="write",
        requires_confirmation=False,
        requires_verified_customer=False,
        idempotency_scope="conversation",
        allowed_when_paused=True,
    )


def get_human_handoff_required_fields(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    handoff_config = config.get("modules", {}).get("human_handoff", {})
    fields = handoff_config.get("fields", [])
    if fields:
        return fields
    if handoff_config.get("enabled", False):
        return DEFAULT_HUMAN_HANDOFF_FIELDS
    return []


def get_human_handoff_tool_schema(config: Dict[str, Any]) -> Dict[str, Any]:
    return _schema_from_fields(get_human_handoff_required_fields(config))


def _schema_from_fields(fields: List[Dict[str, Any]]) -> Dict[str, Any]:
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
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
