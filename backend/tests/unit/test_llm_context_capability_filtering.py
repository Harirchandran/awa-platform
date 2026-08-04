from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.billing.usage_guard import UsageGuard
from backend.core.context import (
    ConversationSession,
    ExecutionContext,
    PendingAction,
    SessionState,
    Tenant,
    TenantConfig,
    User,
)
from backend.core.runtime_capabilities import (
    KNOWN_RUNTIME_MODULES,
    classify_runtime_module_key,
    normalize_runtime_module_key,
    build_runtime_capability_snapshot,
)
from backend.core.runtime_behavior_profile import RuntimeBehaviorProfileResolution
from backend.core.states import ConversationState
from backend.logic.engine.capability_menu import (
    build_capability_menu,
    build_matched_business_context,
)
from backend.logic.engine.core import (
    WorkflowResult,
    _pending_action_clarification,
    _set_runtime_available_modules,
    _unavailable_active_workflow_response,
)
from backend.logic.engine.nba import append_next_best_actions
from backend.logic.engine.prompt import build_system_instructions, format_order_catalog
from backend.modules import get_all_modules
from backend.modules.base_module import BaseModule, ToolMetadata, ValidationResult
from backend.tools.registry import ToolExecutionResult, ToolRegistry
from backend.schemas.behavior_profile import EffectiveBehaviorProfile


LOW_RISK_METADATA = ToolMetadata(
    risk_level="write",
    requires_confirmation=False,
    requires_verified_customer=False,
)


class FakeUsageDb:
    def __init__(self, limits=None):
        self.limits = limits or {}
        self.recorded = []

    async def get_tenant_usage_summary(self, _tenant_id):
        return {
            "today_message_count": 0,
            "month_message_count": 0,
            "month_ai_tokens_used": 0,
            "month_credits_used": 0,
            "month_tool_action_count": 0,
        }

    async def get_subscription_plan_by_code(self, plan_code):
        return {"plan_code": plan_code, "limits_json": self.limits} if self.limits else None

    async def upsert_cost_daily(self, **kwargs):
        self.recorded.append(kwargs)
        return True


class FakeModule(BaseModule):
    def __init__(self, name: str):
        self.name = name

    def get_module_name(self) -> str:
        return self.name

    def get_required_fields(self, _config):
        return []

    def validate_input(self, _field_def, _value, tenant_config=None):
        return ValidationResult(True)

    def get_tool_metadata(self, _config):
        return LOW_RISK_METADATA

    async def execute(self, _context):
        return ToolExecutionResult(
            tool_key=f"{normalize_runtime_module_key(self.name)}.execute",
            success=True,
            user_message="Completed.",
        )


class SnapshotGuard:
    def __init__(self, allowed_modules):
        self.allowed_modules = tuple(allowed_modules)

    async def get_policy_async(self, _tenant):
        return type("Policy", (), {"allowed_modules": self.allowed_modules})()


async def _noop_audit(*_args, **_kwargs):
    return None


def _config():
    modules = {module: {"enabled": True, "fields": []} for module in KNOWN_RUNTIME_MODULES}
    modules["order"]["categories"] = [
        {"name": "Meals", "enabled": True, "items": [{"name": "Masala Dosa", "price": 120, "enabled": True}]}
    ]
    return {
        "business_name": "Runtime Cafe",
        "business_type": "restaurant",
        "services": [{"name": "Dinner booking", "price": 500, "duration_mins": 60}],
        "working_hours": {"monday": {"is_open": True, "open_time": "09:00", "close_time": "18:00"}},
        "faqs": [{"question": "Refund policy", "answer": "Refunds need manager approval."}],
        "modules": modules,
        "limits": {"allowed_modules": ["order", "booking", "payments"]},
        "feature_flags": {"payments": True},
    }


def _context(config=None, *, plan_code="custom_runtime_plan", status="active", state=None):
    tenant_id = uuid4()
    return ExecutionContext(
        tenant=Tenant(
            id=tenant_id,
            name="Runtime Cafe",
            plan_code=plan_code,
            status=status,
            trial_ends_at=datetime.now(timezone.utc) + timedelta(days=1),
        ),
        user=User(id=uuid4(), phone_number="+15550000000", name="Customer"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=state or SessionState(workflow_state=ConversationState.EXECUTING),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=config or _config()),
        incoming_message={"text": "show menu and services", "channel_type": "awa_chat"},
    )


def _behavior_profile(journey_priority):
    return RuntimeBehaviorProfileResolution(
        source="active",
        effective_profile=EffectiveBehaviorProfile(
            schema_version=1,
            profile_name="Runtime Suggestions",
            business_type_key="service_business",
            journey_priority=tuple(journey_priority),
            greeting_mode="hybrid",
            clarification_style="compact",
            tone="friendly",
            language_policy="follow_customer",
            price_visibility="show_when_available",
            service_detail_policy="brief",
            handoff_policy="when_stuck",
            fallback_policy="clarify_first",
            question_batching="single",
            menu_policy="show_available_workflows",
        ),
    )


def _registry(allowed_modules=None, modules=None):
    guard = UsageGuard(FakeUsageDb(limits={
        "allowed_modules": allowed_modules or ["*"],
        "allowed_ai_modes": ["standard", "concierge"],
    }))
    registry = ToolRegistry(guard=guard)
    registry._write_audit = _noop_audit
    for module_name in modules or [
        "booking",
        "order",
        "payments",
        "reminders",
        "reviews",
        "lead_capture",
        "support",
        "human_handoff",
    ]:
        registry.register_module(FakeModule(module_name))
    return registry


@pytest.mark.asyncio
async def test_prompt_context_uses_only_runtime_available_modules():
    config = _config()
    snapshot = await build_runtime_capability_snapshot(
        tenant_id=uuid4(),
        config_data=config,
        plan_code="partner_custom",
        tenant_status="active",
        guard=SnapshotGuard(["booking", "faq"]),
    )
    context = _context(config)
    prompt = build_system_instructions(
        context,
        memory_context="No memory.",
        knowledge_context="No knowledge.",
        tool_registry=_registry(),
        available_tools=[],
        runtime_available_modules=snapshot.runtime_available_modules,
    )

    assert "- Enabled modules: booking, faq" in prompt
    assert "- Bookable services configured: 1" in prompt
    assert "- Order catalog: 0 categories, 0 active items" in prompt
    assert "Masala Dosa" not in prompt
    assert "feature_flags" not in prompt
    assert "partner_custom" not in prompt


@pytest.mark.asyncio
async def test_tenant_disabled_and_plan_disallowed_modules_do_not_enter_prompt_context():
    config = _config()
    config["modules"]["booking"]["enabled"] = False
    snapshot = await build_runtime_capability_snapshot(
        tenant_id=uuid4(),
        config_data=config,
        plan_code="custom_order_only",
        tenant_status="active",
        guard=SnapshotGuard(["order", "booking", "faq"]),
    )
    prompt = build_system_instructions(
        _context(config),
        memory_context="No memory.",
        knowledge_context="No knowledge.",
        tool_registry=_registry(),
        available_tools=[],
        runtime_available_modules=snapshot.runtime_available_modules,
    )

    assert "booking" not in snapshot.runtime_available_modules
    assert "- Enabled modules: faq, order" in prompt
    assert "- Bookable services configured: 0" in prompt

    plan_disallowed = await build_runtime_capability_snapshot(
        tenant_id=uuid4(),
        config_data=config,
        plan_code="custom_faq_only",
        tenant_status="active",
        guard=SnapshotGuard(["faq"]),
    )
    assert "order" not in plan_disallowed.runtime_available_modules
    assert format_order_catalog(config, plan_disallowed.runtime_available_modules) == "Order catalog is not available."


@pytest.mark.asyncio
async def test_wildcard_custom_plan_ignores_tenant_limits_and_feature_flags():
    snapshot = await build_runtime_capability_snapshot(
        tenant_id=uuid4(),
        config_data=_config(),
        plan_code="bespoke_unlimited",
        tenant_status="active",
        guard=SnapshotGuard(["*"]),
    )

    assert "order" in snapshot.runtime_available_modules
    assert "payments" in snapshot.runtime_available_modules
    assert snapshot.is_available("invented") is False


def test_registered_runtime_modules_and_tool_definitions_are_classified():
    registry = ToolRegistry()
    raw_module_names = []
    for module in get_all_modules():
        raw_module_names.append(module.get_module_name())
        classify_runtime_module_key(module.get_module_name())
        registry.register_module(module)

    assert "catalog_search" in raw_module_names
    assert "booking.update" in raw_module_names
    assert {tool.module_name for tool in registry._tools.values()} <= KNOWN_RUNTIME_MODULES
    for tool in registry._tools.values():
        classify_runtime_module_key(tool.module_name)


def test_unknown_public_module_requires_runtime_classification():
    with pytest.raises(ValueError, match="Unclassified runtime module"):
        classify_runtime_module_key("new_public_workflow")


@pytest.mark.asyncio
async def test_llm_tool_exposure_tracks_runtime_entitlements():
    config = _config()
    context = _context(config)
    registry = _registry(allowed_modules=["booking", "payments"])

    names = [tool["function"]["name"] for tool in await registry.get_llm_tools_for_context(context)]

    assert "booking_execute" in names
    assert "payments_execute" in names
    assert "order_execute" not in names

    config["modules"]["booking"]["enabled"] = False
    disabled_context = _context(config)
    names = [tool["function"]["name"] for tool in await registry.get_llm_tools_for_context(disabled_context)]
    assert "booking_execute" not in names


@pytest.mark.asyncio
async def test_stale_disallowed_tool_call_remains_denied_by_execution_guards():
    registry = _registry(allowed_modules=["booking"], modules=["payments"])
    context = _context(_config())

    result = await registry.execute_tool("payments.execute", context)

    assert result.success is False
    assert result.denied_reason == "module_not_allowed:payments"


def test_business_context_omits_unavailable_catalog_services_and_faqs():
    config = _config()
    unavailable = build_matched_business_context(
        config,
        "show menu services refund policy",
        available_modules={"payments"},
    )

    assert "Masala Dosa" not in unavailable
    assert "Dinner booking" not in unavailable
    assert "Refunds need manager approval" not in unavailable

    available = build_matched_business_context(
        config,
        "show menu services refund policy",
        available_modules={"order", "booking", "faq"},
    )
    assert "Masala Dosa" in available
    assert "Dinner booking" in available
    assert "Refunds need manager approval" in available


def test_multi_intent_context_loads_only_primary_and_secondary_scopes():
    context = build_matched_business_context(
        _config(),
        "Book dinner and what is your refund policy?",
        intent="booking",
        route_type="workflow",
        is_tangent=False,
        additional_intents={"faq"},
        available_modules={"booking", "order", "faq"},
    )

    assert "Dinner booking" in context
    assert "Refunds need manager approval" in context
    assert "Masala Dosa" not in context


@pytest.mark.asyncio
async def test_quick_replies_and_nba_do_not_suggest_unavailable_workflows_without_registry():
    config = _config()
    menu = build_capability_menu(config, available_modules={"faq"})
    assert "Show menu" not in menu.suggestions
    assert "Book appointment" not in menu.suggestions

    context = _context(config)
    _set_runtime_available_modules(context, {"support"})
    context.session_data.state.record_tool_result(
        ToolExecutionResult(tool_key="order.execute", success=True, created_resource_type="order"),
        channel_type="awa_chat",
    )
    result = await append_next_best_actions(context, WorkflowResult(response_text="Done."), tool_registry=None)

    assert "Make payment" not in result.quick_replies
    assert "View menu" not in result.quick_replies


@pytest.mark.asyncio
async def test_behavior_profile_ranks_only_eligible_nba_suggestions(monkeypatch):
    config = _config()
    context = _context(config)
    context.runtime_behavior_profile = _behavior_profile(("support", "reminders", "booking"))
    _set_runtime_available_modules(context, {"booking", "reminders", "support"})
    context.session_data.state.record_tool_result(
        ToolExecutionResult(tool_key="booking.execute", success=True, created_resource_type="booking"),
        channel_type="awa_chat",
    )

    result = await append_next_best_actions(context, WorkflowResult(response_text="Booked."), tool_registry=None)

    assert result.quick_replies == ["Contact support", "Set reminder", "Reschedule"]
    assert all(isinstance(option, str) for option in result.quick_replies)
    assert all("tool" not in option.lower() and "action_id" not in option.lower() for option in result.quick_replies)

    unavailable_support = _context(config)
    unavailable_support.runtime_behavior_profile = _behavior_profile(("support", "reminders", "booking"))
    _set_runtime_available_modules(unavailable_support, {"booking", "reminders"})
    unavailable_support.session_data.state.record_tool_result(
        ToolExecutionResult(tool_key="booking.execute", success=True, created_resource_type="booking"),
        channel_type="awa_chat",
    )

    filtered = await append_next_best_actions(unavailable_support, WorkflowResult(response_text="Booked."), tool_registry=None)
    assert "Contact support" not in filtered.quick_replies
    assert filtered.quick_replies[:2] == ["Set reminder", "Reschedule"]

    failing = _context(config)
    failing.runtime_behavior_profile = _behavior_profile(("support", "reminders", "booking"))
    _set_runtime_available_modules(failing, {"booking", "reminders", "support"})
    failing.session_data.state.record_tool_result(
        ToolExecutionResult(tool_key="booking.execute", success=True, created_resource_type="booking"),
        channel_type="awa_chat",
    )
    monkeypatch.setattr(
        "backend.logic.engine.nba.rank_suggestions_by_behavior_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("profile failure")),
    )
    preserved = await append_next_best_actions(failing, WorkflowResult(response_text="Booked."), tool_registry=None)
    assert preserved.response_text == "Booked."
    assert preserved.quick_replies == ["Reschedule", "Cancel booking", "Set reminder"]


def test_confirm_replies_are_hidden_when_pending_action_module_becomes_unavailable():
    state = SessionState(workflow_state=ConversationState.EXECUTING)
    state.prepare_pending_action(
        tool_key="order.execute",
        args={"item_name": "Masala Dosa"},
        summary="Please review this order.",
        requires_confirmation=True,
    )
    context = _context(_config(), state=state)
    _set_runtime_available_modules(context, {"booking", "faq"})

    result = _unavailable_active_workflow_response(context, {"booking", "faq"})

    assert result is not None
    assert "currently unavailable" in result.response_text
    assert "Confirm" not in result.quick_replies
    assert context.session_data.state.action_ledger.pending_action is None
    assert context.session_data.state.action_ledger.last_tool_result is None


def test_confirm_change_cancel_remain_for_available_pending_action():
    state = SessionState(workflow_state=ConversationState.EXECUTING)
    state.prepare_pending_action(
        tool_key="booking.execute",
        args={"service_name": "Dinner booking"},
        summary="Please review this booking.",
        requires_confirmation=True,
    )
    context = _context(_config(), state=state)
    _set_runtime_available_modules(context, {"booking"})

    assert _unavailable_active_workflow_response(context, {"booking"}) is None
    result = _pending_action_clarification(context)

    assert result.quick_replies == ["Confirm", "Change", "Cancel"]


def test_active_workflow_unavailable_edges_clear_state_without_fake_success_or_payment_claims():
    for tool_key, available in [
        ("order.execute", {"booking"}),
        ("booking.execute", {"order"}),
        ("payments.execute", {"order", "booking"}),
    ]:
        state = SessionState(workflow_state=ConversationState.EXECUTING)
        state.prepare_pending_action(
            tool_key=tool_key,
            args={"amount": 100, "item_name": "Masala Dosa", "service_name": "Dinner booking"},
            summary="Please review this request.",
            requires_confirmation=True,
        )
        context = _context(_config(), state=state)
        _set_runtime_available_modules(context, available)

        result = _unavailable_active_workflow_response(context, available)

        assert result is not None
        combined = f"{result.response_text}\n{' '.join(result.quick_replies)}".lower()
        assert "currently unavailable" in combined
        assert "confirm" not in combined
        assert "success" not in combined
        assert "payment link" not in combined
        assert "provider" not in combined
        assert context.session_data.state.action_ledger.pending_action is None
