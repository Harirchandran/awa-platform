import pytest
import re
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from backend.core.context import ExecutionContext
from backend.core.states import ConversationState
from backend.domains.orders.tool_definitions import get_order_tool_schema
from backend.domains.bookings.tool_definitions import get_booking_tool_schema
from backend.domains.orders.tool_handlers import execute_order_tool
from backend.domains.bookings.tool_handlers import execute_booking_tool
from backend.pipeline.channel_output import sanitize_customer_visible_output
from backend.capabilities.gateway import CapabilityGateway
from backend.capabilities.models import CapabilityRequest

pytestmark = pytest.mark.anyio


def _build_context(tenant_id=None, customer_id=None, step_data=None):
    context = MagicMock(spec=ExecutionContext)
    context.tenant = MagicMock()
    context.tenant.id = tenant_id or uuid4()
    context.user = MagicMock()
    context.user.id = customer_id or uuid4()
    context.session_data = MagicMock()
    context.session_data.conversation_id = uuid4()
    context.session_data.id = uuid4()

    state = MagicMock()
    state.step_data = step_data or {}

    def get_task_step_data(module_name):
        return state.step_data

    state.get_task_step_data = get_task_step_data
    context.session_data.state = state

    config = MagicMock()
    config.config_data = {
        "modules": {
            "order": {
                "enabled": True,
                "categories": [
                    {
                        "id": "c1",
                        "name": "Hygiene",
                        "items": [
                            {"id": "i1", "name": "Toothpaste", "price": 120, "sku": "TP123"},
                            {"id": "i2", "name": "Toothpaste (Kids)", "price": 80, "sku": "TP123-K"},
                            {"id": "i3", "name": "Dental Floss", "price": 150}
                        ]
                    }
                ]
            },
            "booking": {"enabled": True}
        },
        "services": [
            {"id": "s1", "name": "Dental Cleaning"},
            {"id": "s2", "name": "Dental Cleaning (Senior)"},
            {"id": "s3", "name": "Haircut"}
        ]
    }
    context.config = config
    context.incoming_message = {"channel_type": "awa_chat"}
    return context


# 1. Order tool schema does not expose item_id
def test_order_tool_schema_does_not_expose_item_id():
    context = _build_context()
    schema = get_order_tool_schema(context.config.config_data)
    assert "item_id" not in schema["properties"]
    assert "item_name" in schema["properties"]
    assert "item_sku" in schema["properties"]


# 2. Booking tool schema does not expose service_id
def test_booking_tool_schema_does_not_expose_service_id():
    context = _build_context()
    schema = get_booking_tool_schema(context.config.config_data)
    assert "service_id" not in schema["properties"]
    assert "service_name" in schema["properties"]


# 3. Serialized LLM tool schema contains no item_id, service_id, or UUID-like values
def test_serialized_tool_schema_has_no_uuids_or_ids():
    context = _build_context()
    order_schema = get_order_tool_schema(context.config.config_data)
    booking_schema = get_booking_tool_schema(context.config.config_data)

    for schema in (order_schema, booking_schema):
        serialized = str(schema)
        assert "item_id" not in serialized
        assert "service_id" not in serialized
        # Ensure no standard hyphenated UUID pattern is in description or properties
        uuid_patterns = re.findall(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", serialized)
        assert not uuid_patterns


# 4. System prompt/catalog contains no UUID patterns
def test_system_prompt_contains_no_uuids():
    from backend.logic.engine.prompt import format_trusted_server_context, format_order_catalog
    context = _build_context()
    trusted_context = format_trusted_server_context(context)
    catalog_context = format_order_catalog(context.config.config_data)

    for block in (trusted_context, catalog_context):
        uuid_patterns = re.findall(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", block)
        assert not uuid_patterns, f"Found UUIDs in prompt block: {uuid_patterns}"


# 5. Order handler resolves item_name/item_sku to internal ID
async def test_order_handler_resolves_item_name_to_internal_id():
    context = _build_context(step_data={
        "item_name": "Toothpaste",
        "quantity": 2,
        "fulfillment_type": "pickup",
        "customer_name": "Asha",
        "phone": "+919999999999"
    })

    module = MagicMock()
    # Mock order module helpers
    module._order_config.return_value = context.config.config_data["modules"]["order"]
    module._requested_items.return_value = [{"item_id": "Toothpaste", "quantity": 2}]
    module._coerce_quantity.return_value = 2
    module._coerce_money.return_value = 0.0
    module._order_item_snapshot = lambda entry: {"item_id": entry["item"]["id"]}

    mock_order_service = AsyncMock()
    mock_order_service.create_order.return_value = {"id": uuid4()}

    mock_catalog = AsyncMock()
    # Mock list_items to return candidates
    mock_catalog.list_items.return_value = [
        {"id": "i1", "name": "Toothpaste", "price": 120, "sku": "TP123"},
        {"id": "i3", "name": "Dental Floss", "price": 150}
    ]

    result = await execute_order_tool(
        context,
        module=module,
        order_service=mock_order_service,
        catalog_service=mock_catalog,
        logger=MagicMock()
    )

    assert result.success is True
    # Ensure create_order was called with resolved item ID 'i1'
    call_args = mock_order_service.create_order.call_args[1]
    assert call_args["items"][0]["item_id"] == "i1"


# 6. Booking handler resolves service_name to internal ID
async def test_booking_handler_resolves_service_name_to_internal_id():
    context = _build_context(step_data={
        "service_name": "Dental Cleaning",
        "preferred_date": "tomorrow",
        "preferred_time": "10 AM",
        "customer_name": "Asha"
    })

    module = MagicMock()
    module._coerce_booking_date.return_value = MagicMock()
    module._coerce_booking_time.return_value = MagicMock()
    module._service_duration_minutes.return_value = 30
    module._validate_booking_rules.return_value = None

    mock_booking_service = AsyncMock()
    mock_booking_service.create_booking.return_value = {"id": uuid4()}

    result = await execute_booking_tool(
        context,
        module=module,
        booking_service=mock_booking_service,
        logger=MagicMock()
    )

    assert result.success is True
    call_args = mock_booking_service.create_booking.call_args[1]
    assert call_args["service_name"] == "Dental Cleaning"
    assert call_args["booking_data"]["service_id"] == "s1"


# 7. Duplicate item asks clarification with business names only
async def test_duplicate_item_asks_clarification_without_uuid():
    context = _build_context(step_data={
        "item_name": "Tooth",  # Substring matches both Toothpaste and Toothpaste (Kids)
        "quantity": 1
    })

    module = MagicMock()
    module._order_config.return_value = context.config.config_data["modules"]["order"]
    module._requested_items.return_value = [{"item_id": "Tooth", "quantity": 1}]

    mock_catalog = AsyncMock()
    mock_catalog.list_items.return_value = [
        {"id": "i1", "name": "Toothpaste", "price": 120, "sku": "TP123"},
        {"id": "i2", "name": "Toothpaste (Kids)", "price": 80, "sku": "TP123-K"}
    ]

    result = await execute_order_tool(
        context,
        module=module,
        order_service=AsyncMock(),
        catalog_service=mock_catalog,
        logger=MagicMock()
    )

    assert result.success is False
    # New format: numbered choice list instead of comma-separated names
    # Ensures both names appear as numbered options in the clarification message
    assert "Toothpaste" in result.user_message
    assert "Toothpaste (Kids)" in result.user_message
    # Numbered choices must be present (new disambiguation format)
    assert "1." in result.user_message or "2." in result.user_message
    # Verify no UUID or database ID is exposed in the prompt message
    assert not re.findall(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", result.user_message)
    assert "i1" not in result.user_message
    assert "i2" not in result.user_message


# 8. Duplicate service asks clarification with business names only
async def test_duplicate_service_asks_clarification_without_uuid():
    context = _build_context(step_data={
        "service_name": "Dental",  # Substring matches both Dental Cleaning and Dental Cleaning (Senior)
        "preferred_date": "tomorrow",
        "preferred_time": "10 AM",
        "customer_name": "Asha"
    })

    # Modify mock config to have duplicate cleaning services
    context.config.config_data["services"] = [
        {"id": "s1", "name": "Dental Cleaning"},
        {"id": "s2", "name": "Dental Cleaning (Senior)"}
    ]

    module = MagicMock()
    result = await execute_booking_tool(
        context,
        module=module,
        booking_service=AsyncMock(),
        logger=MagicMock()
    )

    assert result.success is False
    assert "multiple services matching" in result.user_message
    assert "Dental Cleaning, Dental Cleaning (Senior)" in result.user_message
    assert not re.findall(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", result.user_message)


# 9. Unknown item returns safe message without UUID
async def test_unknown_item_returns_safe_message_without_uuid():
    context = _build_context(step_data={
        "item_name": "Unknown Item",
        "quantity": 1
    })

    module = MagicMock()
    module._order_config.return_value = context.config.config_data["modules"]["order"]
    module._requested_items.return_value = [{"item_id": "Unknown Item", "quantity": 1}]

    mock_catalog = AsyncMock()
    mock_catalog.list_items.return_value = []

    result = await execute_order_tool(
        context,
        module=module,
        order_service=AsyncMock(),
        catalog_service=mock_catalog,
        logger=MagicMock()
    )

    assert result.success is False
    assert "couldn’t find that item" in result.user_message
    assert not re.findall(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", result.user_message)


# 10. Unknown service returns safe message without UUID
async def test_unknown_service_returns_safe_message_without_uuid():
    context = _build_context(step_data={
        "service_name": "Unknown Service",
        "preferred_date": "tomorrow",
        "preferred_time": "10 AM",
        "customer_name": "Asha"
    })

    module = MagicMock()
    result = await execute_booking_tool(
        context,
        module=module,
        booking_service=AsyncMock(),
        logger=MagicMock()
    )

    assert result.success is False
    assert "couldn’t find that service" in result.user_message
    assert not re.findall(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", result.user_message)


# 11. Tenant A cannot resolve tenant B item/service
async def test_tenant_isolation_on_item_resolution():
    tenant_a_id = uuid4()
    tenant_b_id = uuid4()

    # Context for Tenant A trying to resolve item
    context = _build_context(tenant_id=tenant_a_id, step_data={
        "item_name": "Toothpaste",
        "quantity": 1
    })

    module = MagicMock()
    module._order_config.return_value = context.config.config_data["modules"]["order"]
    module._requested_items.return_value = [{"item_id": "Toothpaste", "quantity": 1}]

    mock_catalog = AsyncMock()
    # list_items is only called for Tenant A, and it returns no items
    mock_catalog.list_items.return_value = []

    result = await execute_order_tool(
        context,
        module=module,
        order_service=AsyncMock(),
        catalog_service=mock_catalog,
        logger=MagicMock()
    )

    # Assert Tenant A failed to resolve it
    assert result.success is False
    assert mock_catalog.list_items.call_args[0][0] == tenant_a_id


# 12. Customer-visible output sanitizer removes UUIDs
def test_customer_visible_output_sanitizer_removes_uuids():
    raw_uuid = str(uuid4())
    input_text = f"Your booking ID is {raw_uuid}. Thank you."
    sanitized = sanitize_customer_visible_output(input_text)
    assert raw_uuid not in sanitized
    assert "[redacted]" in sanitized


# 13. Gateway schema validation remains strict and does not allow LLM-supplied service_id/item_id
async def test_gateway_schema_validation_is_strict():
    gateway = CapabilityGateway(guard=MagicMock())

    # Dummy tool definition for booking
    tool_def = MagicMock()
    tool_def.module.get_tool_schema.return_value = {
        "type": "object",
        "properties": {
            "service_name": {"type": "string"},
            "preferred_date": {"type": "string"}
        },
        "required": ["service_name"]
    }

    # Request including the forbidden 'service_id'
    request = CapabilityRequest(
        tool_name="booking.execute",
        channel="awa_chat",
        module_name="booking",
        input_payload={"service_id": "s1", "service_name": "Dental Cleaning", "preferred_date": "tomorrow"}
    )

    # Perform schema validation
    errors = gateway._schema_check(request, tool_def)
    assert "extra_arguments" in errors
    assert "Unsupported tool arguments: service_id" in errors["extra_arguments"]
