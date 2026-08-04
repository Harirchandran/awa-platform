from typing import Any, Dict, List

from backend.modules.base_module import ToolMetadata


DEFAULT_PAYMENT_FIELDS: List[Dict[str, Any]] = [
    {"key": "amount", "label": "Amount to Pay", "type": "number", "required": True},
    {"key": "reference", "label": "Reference ID (e.g. Order ID)", "type": "string", "required": False},
]


def get_payments_tool_metadata(config: Dict[str, Any]) -> ToolMetadata:
    return ToolMetadata(
        risk_level="sensitive_write",
        requires_confirmation=True,
        requires_verified_customer=True,
        idempotency_scope="conversation",
    )


def get_payments_required_fields(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    payment_config = config.get("modules", {}).get("payments", {})
    fields = payment_config.get("fields", [])
    if fields:
        return fields
    if payment_config.get("enabled", False):
        return DEFAULT_PAYMENT_FIELDS
    return []


def get_payments_tool_schema(config: Dict[str, Any]) -> Dict[str, Any]:
    fields = get_payments_required_fields(config)
    properties = {}
    required = []

    FORBIDDEN_FIELDS = {
        "provider", "status", "payment_status", "paid", "success", "captured",
        "failed", "refunded", "payment_link_url", "provider_payment_id",
        "provider_order_id", "transaction_id", "signature", "webhook_payload",
        "refund_id", "settlement_id", "order_id", "booking_id", "invoice_id",
        "tenant_id", "user_id", "customer_id", "payment_id", "payment_type", "type"
    }

    for field in fields:
        key = field.get("key")
        if not key or key in FORBIDDEN_FIELDS:
            continue
        properties[key] = {
            "type": "string" if field.get("type") != "number" else "integer",
            "description": field.get("label", key),
        }
        if field.get("required"):
            required.append(key)

    properties.setdefault("currency", {"type": "string", "description": "Payment currency code."})
    properties.setdefault("description", {"type": "string", "description": "Customer-visible payment description."})
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
