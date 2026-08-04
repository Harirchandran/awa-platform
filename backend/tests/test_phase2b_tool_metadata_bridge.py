from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.billing.usage_guard import UsageGuard
from backend.capabilities.models import CapabilityDecision
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.domains.bookings.tool_definitions import get_booking_tool_metadata, get_booking_tool_schema
from backend.domains.knowledge.tool_definitions import get_faq_tool_schema
from backend.domains.leads.tool_definitions import get_lead_capture_tool_metadata, get_lead_capture_tool_schema
from backend.domains.orders.tool_definitions import get_order_tool_metadata, get_order_tool_schema
from backend.domains.payments.tool_definitions import get_payments_tool_metadata, get_payments_tool_schema
from backend.domains.reminders.tool_definitions import get_reminders_tool_metadata, get_reminders_tool_schema
from backend.domains.reviews.tool_definitions import get_reviews_tool_metadata, get_reviews_tool_schema
from backend.domains.support.tool_definitions import (
    get_human_handoff_tool_metadata,
    get_human_handoff_tool_schema,
    get_support_tool_metadata,
    get_support_tool_schema,
)
from backend.modules.booking_module import booking_module
from backend.modules.faq_module import faq_module
from backend.modules.human_handoff_module import human_handoff_module
from backend.modules.lead_capture_module import lead_capture_module
from backend.modules.order_module import order_module
from backend.modules.payments_module import payments_module
from backend.modules.reminders_module import reminders_module
from backend.modules.reviews_module import reviews_module
from backend.modules.support_module import support_module
from backend.tools.registry import ToolRegistry


pytestmark = pytest.mark.anyio

MODULE_BRIDGES = [
    ("booking", "booking.execute", "booking_execute", booking_module, get_booking_tool_metadata, get_booking_tool_schema),
    ("order", "order.execute", "order_execute", order_module, get_order_tool_metadata, get_order_tool_schema),
    ("payments", "payments.execute", "payments_execute", payments_module, get_payments_tool_metadata, get_payments_tool_schema),
    ("reminders", "reminders.execute", "reminders_execute", reminders_module, get_reminders_tool_metadata, get_reminders_tool_schema),
    ("reviews", "reviews.execute", "reviews_execute", reviews_module, get_reviews_tool_metadata, get_reviews_tool_schema),
    ("support", "support.execute", "support_execute", support_module, get_support_tool_metadata, get_support_tool_schema),
    (
        "human_handoff",
        "human_handoff.execute",
        "human_handoff_execute",
        human_handoff_module,
        get_human_handoff_tool_metadata,
        get_human_handoff_tool_schema,
    ),
    ("lead_capture", "lead_capture.execute", "lead_capture_execute", lead_capture_module, get_lead_capture_tool_metadata, get_lead_capture_tool_schema),
]

FAQ_BRIDGE = ("faq", "faq.execute", "faq_execute", faq_module, None, get_faq_tool_schema)
SENSITIVE_TOOLS = {"booking", "order", "payments", "reminders", "reviews"}


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
                "allowed_modules": ["*"],
                "allowed_ai_modes": ["standard", "concierge"],
            },
        }

    async def upsert_cost_daily(self, **kwargs):
        return True


def make_context(modules):
    tenant_id = uuid4()
    return ExecutionContext(
        tenant=Tenant(
            id=tenant_id,
            name="Phase 2B Tenant",
            plan_code="enterprise",
            status="active",
            trial_ends_at=datetime.now(timezone.utc) + timedelta(days=1),
        ),
        user=User(id=uuid4(), phone_number="+15550000000", name="Customer"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module="support",
            state=SessionState(workflow_state=ConversationState.EXECUTING),
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data={
                "business_name": "Phase 2B Tenant",
                "modules": modules,
            },
        ),
        incoming_message={"text": "test", "channel_type": "awa_chat"},
    )


def register_phase2b_modules():
    registry = ToolRegistry(guard=UsageGuard(AllowAllUsageDb()))
    registry._write_audit = _noop_audit
    for _, _, _, module, _, _ in MODULE_BRIDGES:
        registry.register_module(module)
    registry.register_module(faq_module)
    return registry


def enabled_config():
    return {
        module_name: {"enabled": True, "fields": []}
        for module_name, *_ in MODULE_BRIDGES
    } | {"faq": {"enabled": True, "fields": []}}


def test_registered_tool_keys_and_llm_names_are_unchanged():
    registry = register_phase2b_modules()
    expected_keys = {tool_key for _, tool_key, *_ in MODULE_BRIDGES} | {"faq.execute"}
    expected_llm_names = {llm_name for _, _, llm_name, *_ in MODULE_BRIDGES} | {"faq_execute"}

    assert set(registry._tools) == expected_keys
    assert {tool.llm_name for tool in registry._tools.values()} == expected_llm_names
    for module_name, tool_key, llm_name, *_ in MODULE_BRIDGES + [FAQ_BRIDGE]:
        assert registry.get_tool_key_for_module(module_name) == tool_key
        assert registry.get_tool_key_for_llm_name(llm_name) == tool_key


@pytest.mark.parametrize(("module_name", "tool_key", "llm_name", "module", "metadata_fn", "schema_fn"), MODULE_BRIDGES)
def test_modules_consume_domain_metadata_and_schema(module_name, tool_key, llm_name, module, metadata_fn, schema_fn):
    config = {"modules": enabled_config()}

    assert module.get_module_name() == module_name
    assert module.get_tool_metadata(config) == metadata_fn(config)
    assert module.get_tool_schema(config) == schema_fn(config)

    metadata = module.get_tool_metadata(config)
    if module_name in SENSITIVE_TOOLS:
        assert metadata.risk_level == "sensitive_write"
        assert metadata.requires_confirmation is True
        assert metadata.requires_verified_customer is (module_name != "reviews")
    else:
        assert metadata.risk_level == "write"
        assert metadata.requires_confirmation is False
        assert metadata.requires_verified_customer is False


def test_private_review_feedback_requires_confirmation_but_not_otp_by_default():
    metadata = get_reviews_tool_metadata({"modules": {"reviews": {"enabled": True}}})
    assert metadata.requires_confirmation is True
    assert metadata.requires_verified_customer is False

    verified_metadata = get_reviews_tool_metadata({
        "modules": {"reviews": {"enabled": True, "requires_verified_customer": True}}
    })
    assert verified_metadata.requires_verified_customer is True


def test_faq_schema_is_bridged_but_stays_hidden_from_llm_tools():
    config = {"modules": enabled_config()}
    context = make_context(enabled_config())
    registry = register_phase2b_modules()

    assert faq_module.get_module_name() == "faq"
    assert faq_module.get_tool_schema(config) == get_faq_tool_schema(config)
    assert faq_module.is_llm_tool() is False
    assert "faq_execute" not in [tool["function"]["name"] for tool in registry.get_llm_tools(context)]


async def test_capability_gateway_receives_bridged_metadata(monkeypatch):
    captured = {}

    async def capture_evaluate(self, request, context, tool_def, metadata):
        captured["tool_key"] = tool_def.key
        captured["metadata"] = metadata
        return CapabilityDecision(status="allowed", reason_code="allowed", safe_to_execute=True)

    monkeypatch.setattr("backend.capabilities.gateway.CapabilityGateway.evaluate", capture_evaluate)
    registry = register_phase2b_modules()
    context = make_context({"support": {"enabled": True, "fields": []}})

    decision = await registry.validate_tool_call("support.execute", context, args={})

    assert decision.allowed is True
    assert captured["tool_key"] == "support.execute"
    assert captured["metadata"] == get_support_tool_metadata(context.config.config_data)


async def test_disabled_module_behavior_still_fails_correctly():
    registry = register_phase2b_modules()
    context = make_context({"support": {"enabled": False, "fields": []}})

    decision = await registry.validate_tool_call("support.execute", context, args={})

    assert decision.allowed is False
    assert decision.result is not None
    assert decision.result.denied_reason == "module_disabled:support"


async def test_unknown_tool_behavior_still_fails_correctly():
    registry = register_phase2b_modules()
    context = make_context(enabled_config())

    with pytest.raises(KeyError, match="Unknown tool: catalog.execute"):
        await registry.validate_tool_call("catalog.execute", context, args={})


def test_no_catalog_or_search_execute_tool_is_registered():
    registry = register_phase2b_modules()

    assert "catalog.execute" not in registry._tools
    assert "catalog_search.execute" not in registry._tools
    assert "search.execute" not in registry._tools
    assert registry.get_tool_key_for_llm_name("catalog_execute") is None
    assert registry.get_tool_key_for_llm_name("catalog_search_execute") is None


async def _noop_audit(*args, **kwargs):
    return None
