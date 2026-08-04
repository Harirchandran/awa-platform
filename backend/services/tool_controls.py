from __future__ import annotations
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Tuple
from backend.schemas.config import normalize_tenant_config
DEFAULT_TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "key": "booking",
        "label": "Bookings",
        "category": "appointment",
        "description": "Collect service, date, time, and customer details for appointments.",
        "icon_key": "calendar_check",
        "route_path": "/dashboard/bookings",
        "actions": [],
        "sort_order": 10,
    },
    {
        "key": "order",
        "label": "Orders",
        "category": "commerce",
        "description": "Collect item, quantity, fulfillment, and customer details for orders.",
        "icon_key": "shopping_bag",
        "route_path": "/dashboard/orders",
        "actions": [
            {"label": "Orders", "href": "/dashboard/orders"},
            {"label": "Catalog", "href": "/dashboard/catalog"},
        ],
        "sort_order": 20,
    },
    {
        "key": "payments",
        "label": "Payments",
        "category": "commerce",
        "description": "Create payment requests, deposits, and manual collection records.",
        "icon_key": "wallet",
        "route_path": "/dashboard/payments",
        "actions": [],
        "sort_order": 30,
    },
    {
        "key": "reminders",
        "label": "Reminders",
        "category": "automation",
        "description": "Schedule booking, payment, order, and follow-up messages.",
        "icon_key": "bell",
        "route_path": "/dashboard/reminders",
        "actions": [],
        "sort_order": 40,
    },
    {
        "key": "reviews",
        "label": "Review Collection",
        "category": "growth",
        "description": "Collect private ratings and route happy customers to public reviews.",
        "icon_key": "star",
        "route_path": "/dashboard/reviews",
        "actions": [],
        "sort_order": 50,
    },
    {
        "key": "lead_capture",
        "label": "Lead Capture",
        "category": "sales",
        "description": "Collect prospect details before they are ready to book or order.",
        "icon_key": "megaphone",
        "route_path": None,
        "actions": [],
        "sort_order": 60,
    },
    {
        "key": "support",
        "label": "Support",
        "category": "service",
        "description": "Collect issue details and route support requests.",
        "icon_key": "headphones",
        "route_path": None,
        "actions": [],
        "sort_order": 70,
    },
    {
        "key": "faq",
        "label": "FAQ Answers",
        "category": "knowledge",
        "description": "Answer common questions from the tenant knowledge setup.",
        "icon_key": "circle_help",
        "route_path": None,
        "actions": [],
        "sort_order": 80,
    },
    {
        "key": "human_handoff",
        "label": "Human Handoff",
        "category": "handoff",
        "description": "Let customers ask for a person when automation is not enough.",
        "icon_key": "user_round_check",
        "route_path": None,
        "actions": [],
        "sort_order": 90,
    },
]
TOOL_DEFINITIONS = DEFAULT_TOOL_DEFINITIONS
TOOL_KEYS: Tuple[str, ...] = tuple(tool["key"] for tool in DEFAULT_TOOL_DEFINITIONS)
BUSINESS_TOOL_PRESETS: Dict[str, Dict[str, Any]] = {}
def is_tool_allowed(tool_key: str, allowed_modules: Iterable[str]) -> bool:
    allowed = set(allowed_modules or [])
    return "*" in allowed or tool_key in allowed
def normalize_tool_definitions(tool_definitions: Optional[Iterable[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    definitions = list(tool_definitions or DEFAULT_TOOL_DEFINITIONS)
    if not definitions:
        definitions = DEFAULT_TOOL_DEFINITIONS
    normalized: List[Dict[str, Any]] = []
    for index, definition in enumerate(definitions):
        key = str(definition.get("key") or "").strip()
        if not key:
            continue
        actions = definition.get("actions") or definition.get("secondary_actions") or []
        if not isinstance(actions, list):
            actions = []
        normalized.append({
            "key": key,
            "label": str(definition.get("label") or key.replace("_", " ").title()),
            "category": str(definition.get("category") or "workflow"),
            "description": str(definition.get("description") or ""),
            "icon_key": str(definition.get("icon_key") or key),
            "route_path": definition.get("route_path"),
            "actions": actions,
            "sort_order": int(definition.get("sort_order") or ((index + 1) * 10)),
        })
    return sorted(normalized, key=lambda item: (item["sort_order"], item["label"].lower()))
def build_tool_control_state(
    config_json: Dict[str, Any],
    allowed_modules: Iterable[str],
    presets: Optional[Dict[str, Dict[str, Any]]] = None,
    tool_definitions: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    config = normalize_tenant_config(config_json or {})
    modules = config.get("modules", {})
    tools = []
    for definition in normalize_tool_definitions(tool_definitions):
        key = definition["key"]
        allowed = is_tool_allowed(key, allowed_modules)
        module_config = modules.get(key, {})
        tools.append({
            **definition,
            "enabled": bool(module_config.get("enabled", False)),
            "plan_allowed": allowed,
            "field_count": len(module_config.get("fields", []) or []),
            "locked_reason": None if allowed else "Not available on this tenant plan",
        })
    return {
        "business_type": config.get("business_type", "business"),
        "tools": tools,
        "active_modules": [tool["key"] for tool in tools if tool["enabled"]],
        "presets": presets if presets is not None else BUSINESS_TOOL_PRESETS,
    }
def apply_tool_control_update(
    config_json: Dict[str, Any],
    enabled_modules: List[str],
    allowed_modules: Iterable[str],
    business_type: str | None = None,
    tool_definitions: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    tool_keys = tuple(tool["key"] for tool in normalize_tool_definitions(tool_definitions))
    unknown = sorted(set(enabled_modules) - set(tool_keys))
    if unknown:
        raise ValueError(f"Unknown tools: {', '.join(unknown)}")
    blocked = sorted(tool for tool in enabled_modules if not is_tool_allowed(tool, allowed_modules))
    if blocked:
        raise PermissionError(f"Plan does not allow: {', '.join(blocked)}")
    updated = normalize_tenant_config(deepcopy(config_json or {}))
    if business_type:
        updated["business_type"] = business_type
    modules = updated.setdefault("modules", {})
    enabled_set = set(enabled_modules)
    for key in tool_keys:
        module_config = modules.setdefault(key, {"enabled": False, "fields": []})
        module_config["enabled"] = key in enabled_set
    return normalize_tenant_config(updated)