from typing import Any, Dict, List

from backend.modules.base_module import ToolMetadata


DEFAULT_ORDER_FIELDS: List[Dict[str, Any]] = [
    {"key": "item_name", "label": "Item Name", "type": "string", "required": True},
    {"key": "quantity", "label": "Quantity", "type": "number", "required": True},
    {
        "key": "fulfillment_type",
        "label": "Pickup or delivery",
        "type": "select",
        "options_from": "fulfillment_types",
        "required": True,
    },
    {"key": "customer_name", "label": "Name", "type": "string", "required": True},
    {"key": "phone", "label": "Phone number", "type": "phone", "required": True},
    {
        "key": "delivery_address",
        "label": "Delivery address",
        "type": "textarea",
        "required": True,
        "applies_when": "fulfillment_type == delivery",
    },
]


def get_order_tool_metadata(config: Dict[str, Any]) -> ToolMetadata:
    return ToolMetadata(
        risk_level="sensitive_write",
        requires_confirmation=True,
        requires_verified_customer=True,
        idempotency_scope="conversation",
    )


def get_order_required_fields(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    order_config = config.get("modules", {}).get("order", {})
    fields = order_config.get("fields", [])
    if fields:
        return fields
    if order_config.get("enabled", False):
        return DEFAULT_ORDER_FIELDS
    return []


def get_order_tool_schema(config: Dict[str, Any]) -> Dict[str, Any]:
    fields = get_order_required_fields(config)
    order_config = config.get("modules", {}).get("order", {})
    properties = {}
    required = []
    for field in fields:
        key = field.get("key")
        if not key:
            continue
        if key == "item_id":
            key = "item_name"
        properties[key] = {
            "type": "string" if field.get("type") != "number" else "integer",
            "description": "Name of the item to order (do not provide internal database IDs or UUIDs)" if key == "item_name" else field.get("label", key),
        }
        options = field.get("options")
        if isinstance(options, list) and options:
            properties[key]["enum"] = [str(option) for option in options if str(option).strip()]
        if field.get("required") and not field.get("applies_when"):
            required.append(key)

    if "item_id" in required:
        required.remove("item_id")
    if "item_name" not in required and any(f.get("key") == "item_id" or f.get("key") == "item_name" for f in fields):
        required.append("item_name")

    properties.setdefault("item_sku", {"type": "string", "description": "Optional SKU of the item to order."})
    properties.setdefault(
        "items",
        {
            "type": "array",
            "description": "Optional cart items, each with item_name (do not provide database UUIDs), optional item_sku, and quantity.",
        },
    )
    properties.setdefault("notes", {"type": "string", "description": "Optional order notes."})
    properties.setdefault("payment_method", {"type": "string", "description": "Requested payment method."})
    fulfillment_options = [
        str(option) for option in (order_config.get("fulfillment_types") or []) if str(option).strip()
    ]
    if fulfillment_options and "fulfillment_type" in properties:
        properties["fulfillment_type"]["enum"] = fulfillment_options
    payment_methods = [
        str(option) for option in (order_config.get("payment_methods") or []) if str(option).strip()
    ]
    if payment_methods:
        properties["payment_method"]["enum"] = payment_methods
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
