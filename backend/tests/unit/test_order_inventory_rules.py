from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.modules.order_module import order_module
pytestmark = pytest.mark.anyio
def _context(step_data):
    tenant_id = uuid4()
    config_data = {
        "business_name": "Cafe",
        "modules": {
            "order": {
                "enabled": True,
                "currency": "INR",
                "fulfillment_types": ["pickup", "delivery"],
                "payment_methods": ["cod", "upi_on_delivery"],
                "delivery_fee": 25,
                "categories": [],
            }
        },
    }
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Cafe", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+919999999999", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module="order",
            state=SessionState(workflow_state=ConversationState.EXECUTING, step_data=step_data),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=config_data),
        incoming_message={"text": "confirm", "channel_type": "awa_chat"},
    )
async def test_order_rejects_unsupported_fulfillment(monkeypatch):
    service = SimpleNamespace(resolve_catalog_item=AsyncMock(), create_order=AsyncMock())
    monkeypatch.setattr("backend.modules.order_module.order_service", service)
    context = _context(
        {
            "item_id": "samosa",
            "quantity": 1,
            "fulfillment_type": "shipping",
            "customer_name": "Asha",
            "phone": "+919999999999",
        }
    )
    result = await order_module.execute(context)
    assert result.success is False
    assert "fulfillment option" in result.user_message
    service.create_order.assert_not_called()
async def test_order_handles_multi_item_catalog_order(monkeypatch):
    samosa_id = uuid4()
    chai_id = uuid4()
    catalog = {
        "samosa": {
            "id": samosa_id,
            "name": "Samosa",
            "price": 20,
            "currency": "INR",
            "source": "catalog_db",
            "stock_tracking_enabled": True,
            "stock_quantity": 10,
        },
        "chai": {
            "id": chai_id,
            "name": "Chai",
            "price": 15,
            "currency": "INR",
            "source": "catalog_db",
            "stock_tracking_enabled": True,
            "stock_quantity": 10,
        },
    }
    async def resolve_catalog_item(tenant_id, value):
        return catalog[str(value)]
    order_id = uuid4()
    service = SimpleNamespace(
        resolve_catalog_item=AsyncMock(side_effect=resolve_catalog_item),
        create_order=AsyncMock(return_value={"id": order_id, "payment_status": "not_collected"}),
    )
    monkeypatch.setattr("backend.modules.order_module.order_service", service)
    monkeypatch.setattr("backend.modules.order_module.catalog_service", service)
    context = _context(
        {
            "items": [
                {"item_id": "samosa", "quantity": 2},
                {"item_id": "chai", "quantity": 3},
            ],
            "fulfillment_type": "delivery",
            "delivery_address": "12 MG Road",
            "payment_method": "cod",
            "customer_name": "Asha",
            "phone": "+919999999999",
        }
    )
    result = await order_module.execute(context)
    assert result.success is True
    assert result.created_resource_type == "order"
    call = service.create_order.call_args.kwargs
    assert call["subtotal_amount"] == 85
    assert call["total_amount"] == 110
    assert len(call["items"]) == 2
    assert call["items"][0]["catalog_item_id"] == samosa_id
    assert call["items"][1]["catalog_item_id"] == chai_id
    assert call["items"][0]["quantity"] == 2
async def test_order_returns_customer_safe_stock_failure(monkeypatch):
    item_id = uuid4()
    async def resolve_catalog_item(tenant_id, value):
        return {
            "id": item_id,
            "name": "Samosa",
            "price": 20,
            "currency": "INR",
            "source": "catalog_db",
            "stock_tracking_enabled": True,
            "stock_quantity": 0,
        }
    async def create_order(**kwargs):
        raise ValueError("One or more items are unavailable or out of stock.")
    service = SimpleNamespace(
        resolve_catalog_item=AsyncMock(side_effect=resolve_catalog_item),
        create_order=AsyncMock(side_effect=create_order),
    )
    monkeypatch.setattr("backend.modules.order_module.order_service", service)
    monkeypatch.setattr("backend.modules.order_module.catalog_service", service)
    context = _context(
        {
            "item_id": "samosa",
            "quantity": 2,
            "fulfillment_type": "pickup",
            "customer_name": "Asha",
            "phone": "+919999999999",
        }
    )
    result = await order_module.execute(context)
    assert result.success is False
    assert result.user_message == "One or more items are unavailable or out of stock."
