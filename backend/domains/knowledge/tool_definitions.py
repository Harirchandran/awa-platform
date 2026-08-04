from typing import Any, Dict, List


DEFAULT_FAQ_FIELDS: List[Dict[str, Any]] = [
    {"key": "search_query", "label": "Search Query for FAQ", "type": "string", "required": True},
]


def is_faq_llm_tool() -> bool:
    return False


def get_faq_required_fields(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    faq_config = config.get("modules", {}).get("faq", {})
    return faq_config.get("fields", DEFAULT_FAQ_FIELDS)


def get_faq_tool_schema(config: Dict[str, Any]) -> Dict[str, Any]:
    fields = get_faq_required_fields(config)
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
        if field.get("required"):
            required.append(key)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
