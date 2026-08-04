from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.billing.usage_guard import UsageGuard
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.modules.catalog_search_module import catalog_search_module
from backend.modules.human_handoff_module import human_handoff_module
from backend.modules.order_module import order_module
from backend.tools.registry import ToolRegistry

pytestmark = pytest.mark.anyio


class AllowAllUsageDb:
    async def get_tenant_usage_summary(self, tenant_id):
        return {
            "today_message_count": 0,
            "month_message_count": 0,
            "month_ai_tokens_used": 0,
            "month_credits_used": 0,
            "month_tool_action_count": 0,
        }

    async def get_subscription_plan_by_code(self, plan_code):
        return {
            "plan_code": plan_code,
            "limits_json": {
                "allowed_modules": ["order", "human_handoff"],
                "allowed_ai_modes": ["standard", "concierge"],
            },
        }

    async def upsert_cost_daily(self, **kwargs):
        return True


def make_context(query: str = "tea") -> ExecutionContext:
    tenant_id = uuid4()
    state = SessionState()
    state.update_task_step_data("order", {"query": query})
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Catalog Tenant", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+15550000000", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=state,
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data={
                "business_name": "Catalog Tenant",
                "modules": {
                    "order": {"enabled": True, "fields": []},
                    "human_handoff": {"enabled": True, "fields": []},
                },
            },
        ),
        incoming_message={"text": query, "channel_type": "awa_chat"},
    )


def make_registry() -> ToolRegistry:
    registry = ToolRegistry(guard=UsageGuard(AllowAllUsageDb()))
    registry._write_audit = AsyncMock()
    registry.register_module(catalog_search_module)
    registry.register_module(order_module)
    registry.register_module(human_handoff_module)
    return registry


async def test_catalog_search_returns_max_five_tenant_scoped_safe_results(monkeypatch):
    context = make_context("tea")
    other_tenant_id = uuid4()
    raw_uuid = "11111111-1111-1111-1111-111111111111"
    rows = [
        {
            "id": uuid4(),
            "tenant_id": context.tenant.id,
            "name": f"Tea Blend {idx}",
            "sku": raw_uuid if idx == 0 else f"TEA-{idx}",
            "category_name": "Beverages",
            "price": 100 + idx,
            "description": "Fresh tea https://signed.example.test/private-token",
            "is_available": True,
            "stock_tracking_enabled": idx == 0,
            "stock_quantity": 12,
        }
        for idx in range(6)
    ]
    rows.insert(
        1,
        {
            "id": uuid4(),
            "tenant_id": other_tenant_id,
            "name": "Tea From Other Tenant",
            "sku": "OTHER-1",
            "category_name": "Beverages",
            "price": 999,
            "is_available": True,
        },
    )
    service = SimpleNamespace(list_items=AsyncMock(return_value=rows))
    monkeypatch.setattr("backend.modules.catalog_search_module.catalog_service", service)

    result = await make_registry().execute_tool("order.catalog_search", context)

    assert result.success is True
    service.list_items.assert_awaited_once_with(
        context.tenant.id,
        search="tea",
        active_only=True,
        available_only=True,
    )
    assert result.data["result_count"] == 5
    assert result.data["max_results"] == 5
    dumped = str(result.to_tool_message())
    assert "Tea From Other Tenant" not in dumped
    assert "tenant_id" not in dumped
    assert "id" not in result.data["results"][0]
    assert raw_uuid not in dumped
    assert "signed.example" not in dumped
    assert "TEA-1" in dumped
    assert "I found these catalog matches" in result.user_message


async def test_catalog_search_no_match_result_is_safe(monkeypatch):
    context = make_context("dragonfruit")
    service = SimpleNamespace(list_items=AsyncMock(return_value=[]))
    monkeypatch.setattr("backend.modules.catalog_search_module.catalog_service", service)

    result = await make_registry().execute_tool("order.catalog_search", context)

    assert result.success is True
    assert result.data["results"] == []
    assert "couldn't find active available catalog items" in result.user_message
    assert "UUID" not in result.user_message
    assert "item_id" not in result.user_message


async def test_catalog_search_tool_scoping_exposes_read_only_search_without_order_write():
    registry = make_registry()
    context = make_context("Do you have jasmine tea?")

    faq_tools = await registry.get_llm_tools_for_context(
        context,
        intent="faq",
        route_type="direct_answer",
    )
    faq_names = [tool["function"]["name"] for tool in faq_tools]
    assert "catalog_search" in faq_names
    assert "order_execute" not in faq_names
    assert "human_handoff_execute" in faq_names

    order_tools = await registry.get_llm_tools_for_context(
        context,
        intent="order",
        route_type="workflow",
        current_task="order",
    )
    order_names = [tool["function"]["name"] for tool in order_tools]
    assert "catalog_search" in order_names
    assert "order_execute" in order_names


def test_catalog_search_schema_exposes_no_internal_order_ids():
    schema = catalog_search_module.get_tool_schema({"modules": {"order": {"enabled": True}}})
    serialized = str(schema)

    assert set(schema["properties"]) == {"query", "category_name"}
    assert schema["required"] == ["query"]
    assert "item_id" not in serialized
    assert "service_id" not in serialized
    assert "uuid" not in serialized.lower()
