from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from backend.core.context import ConversationSession, ExecutionContext, PendingAction, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.domains.orders.tool_handlers import execute_order_tool
from backend.domains.payments.tool_handlers import execute_payments_tool
from backend.logic.engine.core import _set_runtime_available_modules, _unavailable_active_workflow_response
from backend.modules.order_module import order_module

pytestmark = pytest.mark.anyio


def _order_config(*, fulfillment_types=None, payment_methods=None):
    return {
        "modules": {
            "order": {
                "enabled": True,
                "currency": "USD",
                "fulfillment_types": fulfillment_types or ["pickup", "delivery"],
                "payment_methods": payment_methods or ["cod", "upi_on_delivery"],
                "tax_amount": 2.5,
                "delivery_fee": 4.0,
                "categories": [
                    {
                        "id": "food",
                        "name": "Food",
                        "items": [
                            {"id": "burger", "name": "Burger", "price": 10.0, "enabled": True},
                            {"id": "soldout", "name": "Sold Out", "price": 5.0, "enabled": False},
                        ],
                    }
                ],
                "fields": [
                    {"key": "item_name", "label": "Item", "type": "string", "required": True},
                    {"key": "quantity", "label": "Quantity", "type": "number", "required": True},
                    {"key": "fulfillment_type", "label": "Fulfillment", "type": "select", "required": True},
                    {
                        "key": "delivery_address",
                        "label": "Delivery address",
                        "type": "string",
                        "required": True,
                        "applies_when": "fulfillment_type == delivery",
                    },
                ],
            },
            "payments": {"enabled": True, "provider": "manual"},
        }
    }


def _context(step_data=None, config=None, current_task="order"):
    tenant_id = uuid4()
    state = SessionState(
        workflow_state=ConversationState.EXECUTING,
        step_data=dict(step_data or {}),
        scoped_step_data={current_task: dict(step_data or {})} if step_data else {},
    )
    state.action_ledger.current_task = current_task
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Order Tenant", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+15550000000", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module=current_task,
            state=state,
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=config or _order_config()),
        incoming_message={"text": "confirm", "channel_type": "awa_chat", "conversation_sequence_number": 1},
    )


async def test_order_total_uses_catalog_price_and_config_fees_not_llm_values():
    order_service = SimpleNamespace(create_order=AsyncMock(return_value={"id": "order-1", "payment_status": "not_collected"}))
    catalog_service = SimpleNamespace(list_items=AsyncMock(return_value=[]), resolve_catalog_item=AsyncMock(return_value=None))
    ctx = _context(
        {
            "item_name": "Burger",
            "quantity": 2,
            "fulfillment_type": "delivery",
            "delivery_address": "1 Test Street",
            "payment_method": "cod",
            "price": 1,
            "total_amount": 1,
        }
    )

    result = await execute_order_tool(ctx, order_module, order_service, catalog_service, MagicMock())

    assert result.success is True
    kwargs = order_service.create_order.await_args.kwargs
    assert kwargs["subtotal_amount"] == 20.0
    assert kwargs["total_amount"] == 26.5
    assert kwargs["order_data"]["tax_amount"] == 2.5
    assert kwargs["order_data"]["delivery_fee"] == 4.0


async def test_invalid_fulfillment_and_payment_method_rejected_from_config():
    order_service = SimpleNamespace(create_order=AsyncMock())
    catalog_service = SimpleNamespace(list_items=AsyncMock(return_value=[]), resolve_catalog_item=AsyncMock(return_value=None))
    cfg = _order_config(fulfillment_types=["pickup"], payment_methods=["cod"])

    invalid_fulfillment = _context(
        {"item_name": "Burger", "quantity": 1, "fulfillment_type": "delivery", "payment_method": "cod"},
        config=cfg,
    )
    result = await execute_order_tool(invalid_fulfillment, order_module, order_service, catalog_service, MagicMock())
    assert result.success is False
    assert "fulfillment option" in result.user_message

    invalid_payment = _context(
        {"item_name": "Burger", "quantity": 1, "fulfillment_type": "pickup", "payment_method": "online"},
        config=cfg,
    )
    result = await execute_order_tool(invalid_payment, order_module, order_service, catalog_service, MagicMock())
    assert result.success is False
    assert "payment method" in result.user_message
    order_service.create_order.assert_not_called()


def test_delivery_address_not_required_when_pickup_is_configured_current_choice():
    ctx = _context({"fulfillment_type": "pickup"})
    keys = [field["key"] for field in order_module.get_required_fields_for_context(ctx)]

    assert "delivery_address" not in keys


async def test_payment_handler_rejects_llm_supplied_provider_status_or_link_fields():
    ctx = _context(
        {"amount": 100, "provider": "razorpay", "payment_status": "paid", "payment_link_url": "https://example.test/pay"},
        current_task="payments",
    )
    payment_service = SimpleNamespace(create_payment_request=AsyncMock())

    result = await execute_payments_tool(ctx, payment_service, AsyncMock(), MagicMock())

    assert result.success is False
    assert "restricted" in result.user_message
    payment_service.create_payment_request.assert_not_called()


def test_stale_pending_order_or_payment_after_module_disabled_does_not_execute_or_show_confirm():
    for tool_key, available_modules in [
        ("order.execute", {"booking", "support"}),
        ("payments.execute", {"order", "support"}),
    ]:
        ctx = _context({"item_name": "Burger"}, current_task=tool_key.split(".", 1)[0])
        ctx.session_data.state.action_ledger.pending_action = PendingAction(
            tool_key=tool_key,
            args={"item_name": "Burger", "amount": 100},
            summary="Please review this request.",
            requires_confirmation=True,
        )
        _set_runtime_available_modules(ctx, available_modules)

        result = _unavailable_active_workflow_response(ctx, available_modules)

        assert result is not None
        assert "currently unavailable" in result.response_text
        assert "Confirm" not in result.quick_replies
        assert ctx.session_data.state.action_ledger.pending_action is None
