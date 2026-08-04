from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from backend.billing.usage_guard import UsageGuard, UsageLimitExceeded, get_ai_capability_policy
from backend.core.context import ConversationSession, ExecutionContext, OtpVerificationEvidence, PendingAction, SessionState, Tenant, TenantConfig, User
from backend.core.customer_assurance import phone_identity_hash
from backend.core.states import ConversationState
from backend.modules.booking_module import booking_module
from backend.modules.faq_module import faq_module
from backend.modules.human_handoff_module import human_handoff_module
from backend.modules.lead_capture_module import lead_capture_module
from backend.modules.payments_module import payments_module
from backend.modules.reminders_module import reminders_module
from backend.modules.reviews_module import reviews_module
from backend.modules.support_module import support_module
from backend.modules.base_module import BaseModule, ToolMetadata, ValidationResult
from backend.tools.registry import ToolExecutionResult, ToolRegistry
pytestmark = pytest.mark.anyio
LOW_RISK_METADATA = ToolMetadata(
    risk_level="write",
    requires_confirmation=False,
    requires_verified_customer=False,
)
class FakeUsageDb:
    def __init__(self, usage=None, limits=None):
        self.usage = usage or {}
        self.limits = limits or {}
        self.recorded = []
    async def get_tenant_usage_summary(self, tenant_id):
        return {
            "today_message_count": 0,
            "month_message_count": 0,
            "month_ai_tokens_used": 0,
            "month_credits_used": 0,
            "month_tool_action_count": 0,
            **self.usage,
        }
    async def get_subscription_plan_by_code(self, plan_code):
        return {"plan_code": plan_code, "limits_json": self.limits} if self.limits else None
    async def upsert_cost_daily(self, **kwargs):
        self.recorded.append(kwargs)
        return True
class FakeModule(BaseModule):
    def __init__(self, name="booking", succeeds=True, metadata=None):
        self.name = name
        self.succeeds = succeeds
        self.metadata = metadata
    def get_module_name(self) -> str:
        return self.name
    def get_required_fields(self, config):
        return []
    def validate_input(self, field_def, value):
        return ValidationResult(True)
    def get_tool_metadata(self, config):
        return self.metadata or super().get_tool_metadata(config)
    async def execute(self, context):
        return self.succeeds
def make_context(plan_code="trial", status="trial", modules=None, trial_ends_at=None):
    tenant_id = uuid4()
    module_config = modules if modules is not None else {
        "booking": {"enabled": True, "fields": []},
        "support": {"enabled": True, "fields": []},
    }
    return ExecutionContext(
        tenant=Tenant(
            id=tenant_id,
            name="Test Tenant",
            plan_code=plan_code,
            status=status,
            trial_ends_at=trial_ends_at or datetime.now(timezone.utc) + timedelta(days=1),
        ),
        user=User(id=uuid4(), phone_number="+15550000000", name="Customer"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module="booking",
            state=SessionState(workflow_state=ConversationState.EXECUTING),
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data={
                "business_name": "Test Tenant",
                "modules": module_config,
            },
        ),
        incoming_message={"text": "test", "channel_type": "awa_chat"},
    )
async def test_trial_plan_allows_enabled_booking_tool():
    guard = UsageGuard(FakeUsageDb())
    registry = ToolRegistry(guard=guard)
    registry._write_audit = _noop_audit
    registry.register_module(FakeModule("booking", metadata=LOW_RISK_METADATA))
    result = await registry.execute_tool("booking.execute", make_context())
    assert result.success is True
    assert result.denied_reason is None
    assert result.data is None
async def test_plan_blocks_module_not_in_allowed_modules():
    guard = UsageGuard(FakeUsageDb())
    registry = ToolRegistry(guard=guard)
    registry._write_audit = _noop_audit
    registry.register_module(FakeModule("support"))
    context = make_context(modules={"support": {"enabled": True, "fields": []}})
    result = await registry.execute_tool("support.execute", context)
    assert result.success is False
    assert result.denied_reason == "module_not_allowed:support"
async def test_config_disabled_module_is_blocked_even_on_pro():
    guard = UsageGuard(FakeUsageDb())
    registry = ToolRegistry(guard=guard)
    registry._write_audit = _noop_audit
    registry.register_module(FakeModule("support"))
    context = make_context(
        plan_code="pro",
        status="active",
        modules={"support": {"enabled": False, "fields": []}},
    )
    result = await registry.execute_tool("support.execute", context)
    assert result.success is False
    assert result.denied_reason == "module_disabled:support"
async def test_missing_module_config_is_blocked_even_when_plan_allows():
    guard = UsageGuard(FakeUsageDb())
    registry = ToolRegistry(guard=guard)
    registry._write_audit = _noop_audit
    registry.register_module(FakeModule("support"))
    context = make_context(plan_code="pro", status="active", modules={})
    result = await registry.execute_tool("support.execute", context)
    assert result.success is False
    assert result.denied_reason == "module_disabled:support"
async def test_llm_tool_name_maps_modules_with_underscores():
    registry = ToolRegistry(guard=UsageGuard(FakeUsageDb()))
    registry.register_module(FakeModule("lead_capture"))
    registry.register_module(FakeModule("human_handoff"))
    assert registry.get_tool_key_for_llm_name("lead_capture_execute") == "lead_capture.execute"
    assert registry.get_tool_key_for_llm_name("human_handoff_execute") == "human_handoff.execute"
async def test_non_executable_faq_module_is_hidden_from_llm_tools():
    registry = ToolRegistry(guard=UsageGuard(FakeUsageDb()))
    registry.register_module(faq_module)
    context = make_context(modules={"faq": {"enabled": True, "fields": []}})
    tool_names = [tool["function"]["name"] for tool in registry.get_llm_tools(context)]
    assert "faq_execute" not in tool_names
async def test_llm_tools_hide_plan_locked_enabled_modules():
    registry = ToolRegistry(guard=UsageGuard(FakeUsageDb()))
    registry.register_module(FakeModule("booking"))
    registry.register_module(FakeModule("support"))
    context = make_context(
        plan_code="trial",
        status="trial",
        modules={
            "booking": {"enabled": True, "fields": []},
            "support": {"enabled": True, "fields": []},
        },
    )
    tool_names = [tool["function"]["name"] for tool in await registry.get_llm_tools_for_context(context)]
    assert "booking_execute" in tool_names
    assert "support_execute" not in tool_names
async def test_llm_tools_hide_disabled_modules_even_when_plan_allows():
    registry = ToolRegistry(guard=UsageGuard(FakeUsageDb()))
    registry.register_module(FakeModule("support"))
    context = make_context(
        plan_code="pro",
        status="active",
        modules={"support": {"enabled": False, "fields": []}},
    )
    tool_names = [tool["function"]["name"] for tool in await registry.get_llm_tools_for_context(context)]
    assert "support_execute" not in tool_names
async def test_llm_tools_use_database_plan_overrides():
    registry = ToolRegistry(guard=UsageGuard(FakeUsageDb(limits={
        "allowed_modules": ["booking"],
        "allowed_ai_modes": ["standard", "concierge"],
    })))
    registry.register_module(FakeModule("booking"))
    registry.register_module(FakeModule("payments"))
    context = make_context(
        plan_code="pro",
        status="active",
        modules={
            "booking": {"enabled": True, "fields": []},
            "payments": {"enabled": True, "fields": []},
        },
    )
    tool_names = [tool["function"]["name"] for tool in await registry.get_llm_tools_for_context(context)]
    assert tool_names == ["booking_execute"]
@pytest.mark.parametrize(
    ("modules", "expected_tool_names"),
    [
        (
            {
                "booking": {"enabled": True, "fields": []},
                "order": {"enabled": False, "fields": []},
                "payments": {"enabled": False, "fields": []},
                "reminders": {"enabled": False, "fields": []},
                "reviews": {"enabled": False, "fields": []},
                "human_handoff": {"enabled": False, "fields": []},
            },
            ["booking_execute"],
        ),
        (
            {
                "booking": {"enabled": False, "fields": []},
                "order": {"enabled": True, "fields": []},
                "payments": {"enabled": True, "fields": []},
                "reminders": {"enabled": False, "fields": []},
                "reviews": {"enabled": False, "fields": []},
                "human_handoff": {"enabled": True, "fields": []},
            },
            ["order_execute", "payments_execute", "human_handoff_execute"],
        ),
        (
            {
                "booking": {"enabled": False, "fields": []},
                "order": {"enabled": False, "fields": []},
                "payments": {"enabled": False, "fields": []},
                "reminders": {"enabled": False, "fields": []},
                "reviews": {"enabled": False, "fields": []},
                "human_handoff": {"enabled": False, "fields": []},
            },
            [],
        ),
        (
            {
                "booking": {"enabled": True, "fields": []},
                "order": {"enabled": True, "fields": []},
                "payments": {"enabled": True, "fields": []},
                "reminders": {"enabled": True, "fields": []},
                "reviews": {"enabled": True, "fields": []},
                "human_handoff": {"enabled": True, "fields": []},
            },
            [
                "booking_execute",
                "order_execute",
                "payments_execute",
                "reminders_execute",
                "reviews_execute",
                "human_handoff_execute",
            ],
        ),
    ],
)
async def test_llm_tool_visibility_matches_tenant_enabled_matrix(modules, expected_tool_names):
    registry = ToolRegistry(
        guard=UsageGuard(
            FakeUsageDb(
                limits={
                    "allowed_modules": ["*"],
                    "allowed_ai_modes": ["standard", "concierge"],
                }
            )
        )
    )
    for module_name in ["booking", "order", "payments", "reminders", "reviews", "human_handoff"]:
        registry.register_module(FakeModule(module_name))
    context = make_context(plan_code="enterprise", status="active", modules=modules)
    tool_names = [tool["function"]["name"] for tool in await registry.get_llm_tools_for_context(context)]
    assert tool_names == expected_tool_names


def _tool_names(tools):
    return [tool["function"]["name"] for tool in tools]


def _route_registry(limits=None):
    registry = ToolRegistry(
        guard=UsageGuard(
            FakeUsageDb(
                limits=limits or {
                    "allowed_modules": ["*"],
                    "allowed_ai_modes": ["standard", "concierge"],
                }
            )
        )
    )
    for module_name in [
        "booking",
        "booking.update",
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


def _route_modules(**overrides):
    modules = {
        "booking": {"enabled": True, "fields": []},
        "order": {"enabled": True, "fields": []},
        "payments": {"enabled": True, "fields": []},
        "reminders": {"enabled": True, "fields": []},
        "reviews": {"enabled": True, "fields": []},
        "lead_capture": {"enabled": True, "fields": []},
        "support": {"enabled": True, "fields": []},
        "human_handoff": {"enabled": True, "fields": []},
    }
    modules.update(overrides)
    return modules


async def test_route_scoped_booking_exposes_booking_tools_and_handoff_only():
    registry = _route_registry()
    context = make_context(plan_code="enterprise", status="active", modules=_route_modules())
    names = _tool_names(await registry.get_llm_tools_for_context(
        context,
        intent="booking",
        route_type="workflow",
        current_task="booking",
    ))
    assert names == ["booking_execute", "booking_update_execute", "human_handoff_execute"]


async def test_route_scoped_order_exposes_order_tools_and_handoff_only():
    registry = _route_registry()
    context = make_context(plan_code="enterprise", status="active", modules=_route_modules())
    names = _tool_names(await registry.get_llm_tools_for_context(
        context,
        intent="order",
        route_type="workflow",
        current_task="order",
    ))
    assert names == ["order_execute", "human_handoff_execute"]


async def test_route_scoped_faq_greeting_thanks_expose_no_transactional_write_tools():
    registry = _route_registry()
    context = make_context(plan_code="enterprise", status="active", modules=_route_modules())
    for intent in ["faq", "greeting", "thanks"]:
        names = _tool_names(await registry.get_llm_tools_for_context(
            context,
            intent=intent,
            route_type="direct_answer",
        ))
        assert names == ["human_handoff_execute"]


async def test_route_scoped_tangent_faq_during_booking_hides_transactional_tools():
    registry = _route_registry()
    context = make_context(plan_code="enterprise", status="active", modules=_route_modules())
    names = _tool_names(await registry.get_llm_tools_for_context(
        context,
        intent="faq",
        route_type="direct_answer",
        current_task="booking",
        is_tangent=True,
    ))
    assert names == ["human_handoff_execute"]


async def test_route_scoped_active_booking_continuation_exposes_booking_tools():
    registry = _route_registry()
    context = make_context(plan_code="enterprise", status="active", modules=_route_modules())
    names = _tool_names(await registry.get_llm_tools_for_context(
        context,
        intent="booking",
        route_type="active_task",
        current_task="booking",
    ))
    assert names == ["booking_execute", "booking_update_execute", "human_handoff_execute"]


async def test_route_scoped_active_order_continuation_exposes_order_tools():
    registry = _route_registry()
    context = make_context(plan_code="enterprise", status="active", modules=_route_modules())
    names = _tool_names(await registry.get_llm_tools_for_context(
        context,
        intent="order",
        route_type="active_task",
        current_task="order",
    ))
    assert names == ["order_execute", "human_handoff_execute"]


async def test_route_scoped_disabled_tool_remains_hidden_when_route_matches():
    registry = _route_registry()
    context = make_context(
        plan_code="enterprise",
        status="active",
        modules=_route_modules(order={"enabled": False, "fields": []}),
    )
    names = _tool_names(await registry.get_llm_tools_for_context(
        context,
        intent="order",
        route_type="workflow",
        current_task="order",
    ))
    assert names == ["human_handoff_execute"]


async def test_route_scoped_plan_restricted_tool_remains_hidden_when_route_matches():
    registry = _route_registry(limits={
        "allowed_modules": ["booking", "human_handoff"],
        "allowed_ai_modes": ["standard", "concierge"],
    })
    context = make_context(plan_code="enterprise", status="active", modules=_route_modules())
    names = _tool_names(await registry.get_llm_tools_for_context(
        context,
        intent="order",
        route_type="workflow",
        current_task="order",
    ))
    assert names == ["human_handoff_execute"]


async def test_route_scoped_handoff_fallback_only_when_enabled_and_allowed():
    registry = _route_registry()
    context = make_context(
        plan_code="enterprise",
        status="active",
        modules=_route_modules(human_handoff={"enabled": False, "fields": []}),
    )
    names = _tool_names(await registry.get_llm_tools_for_context(
        context,
        intent="booking",
        route_type="workflow",
        current_task="booking",
    ))
    assert names == ["booking_execute", "booking_update_execute"]


async def test_route_scoped_unknown_route_fails_safe_to_handoff_only():
    registry = _route_registry()
    context = make_context(plan_code="enterprise", status="active", modules=_route_modules())
    names = _tool_names(await registry.get_llm_tools_for_context(
        context,
        intent="unknown",
        route_type="unknown",
    ))
    assert names == ["human_handoff_execute"]


async def test_route_scoped_escalated_chat_exposes_no_automated_tools():
    registry = _route_registry()
    context = make_context(plan_code="enterprise", status="active", modules=_route_modules())
    context.session_data.state.workflow_state = ConversationState.ESCALATED
    names = _tool_names(await registry.get_llm_tools_for_context(
        context,
        intent="booking",
        route_type="workflow",
        current_task="booking",
    ))
    assert names == []

async def test_real_booking_metadata_marks_protected_write():
    metadata = booking_module.get_tool_metadata({"modules": {"booking": {"enabled": True}}})
    assert metadata.risk_level == "sensitive_write"
    assert metadata.requires_confirmation is True
    assert metadata.requires_verified_customer is True
    assert "awa_chat" in metadata.supported_channels
    assert "whatsapp" in metadata.supported_channels
async def test_base_tool_metadata_defaults_are_conservative():
    metadata = BaseModule.get_tool_metadata(FakeModule("future_tool"), {})
    assert metadata.risk_level == "sensitive_write"
    assert metadata.requires_confirmation is True
    assert metadata.requires_verified_customer is True
async def test_real_llm_tools_declare_explicit_metadata():
    from backend.modules import get_all_modules
    modules_using_default = [
        module.get_module_name()
        for module in get_all_modules()
        if module.is_llm_tool() and type(module).get_tool_metadata is BaseModule.get_tool_metadata
    ]
    assert modules_using_default == []
async def test_low_risk_real_modules_are_explicit():
    lead_metadata = lead_capture_module.get_tool_metadata({"modules": {"lead_capture": {"enabled": True}}})
    support_metadata = support_module.get_tool_metadata({"modules": {"support": {"enabled": True}}})
    assert lead_metadata.risk_level == "write"
    assert lead_metadata.requires_confirmation is False
    assert lead_metadata.requires_verified_customer is False
    assert support_metadata.risk_level == "write"
    assert support_metadata.requires_confirmation is False
    assert support_metadata.requires_verified_customer is False
async def test_tool_execution_audit_includes_permission_metadata():
    guard = UsageGuard(FakeUsageDb())
    registry = ToolRegistry(guard=guard)
    audit_calls = []
    async def capture_audit(_context, action, tool, details):
        audit_calls.append((action, tool.key, details))
    registry._write_audit = capture_audit
    registry.register_module(FakeModule("booking", metadata=LOW_RISK_METADATA))
    result = await registry.execute_tool("booking.execute", make_context())
    assert result.success is True
    assert audit_calls[-1][0] == "tool_booking_executed"
    assert audit_calls[-1][2]["permission_decision"] == "allowed"
    assert audit_calls[-1][2]["risk_level"] == "write"
    assert audit_calls[-1][2]["requires_confirmation"] is False
    assert audit_calls[-1][2]["requires_verified_customer"] is False
async def test_tool_validation_rejects_unknown_llm_arguments():
    guard = UsageGuard(FakeUsageDb(limits={"allowed_modules": ["booking"], "allowed_ai_modes": ["standard", "concierge"]}))
    registry = ToolRegistry(guard=guard)
    audit_calls = []
    async def capture_audit(_context, action, tool, details):
        audit_calls.append((action, tool.key, details))
    registry._write_audit = capture_audit
    registry.register_module(booking_module)
    context = make_context(
        plan_code="enterprise",
        status="active",
        modules={
            "booking": {
                "enabled": True,
                "fields": [
                    {"key": "service_id", "label": "Service", "type": "string", "required": True},
                ],
            }
        },
    )
    context.session_data.state.update_task_step_data(
        "booking",
        {"service_id": "cleaning", "tenant_id": str(uuid4())},
    )
    result = await registry.execute_tool("booking.execute", context)
    assert result.success is False
    assert result.denied_reason == "argument_validation_failed:booking"
    assert "tenant_id" in result.validation_errors["extra_arguments"]
    assert audit_calls[-1][0] == "tool_blocked"
    assert audit_calls[-1][2]["permission_decision"] == "blocked"
async def test_tool_validation_blocks_context_tenant_mismatch_before_execution():
    guard = UsageGuard(FakeUsageDb(limits={"allowed_modules": ["booking"], "allowed_ai_modes": ["standard", "concierge"]}))
    registry = ToolRegistry(guard=guard)
    audit_calls = []
    module = FakeModule("booking", metadata=LOW_RISK_METADATA)
    async def capture_audit(_context, action, tool, details):
        audit_calls.append((action, tool.key, details))
    registry._write_audit = capture_audit
    registry.register_module(module)
    context = make_context(plan_code="enterprise", status="active", modules={"booking": {"enabled": True, "fields": []}})
    context.session_data.tenant_id = uuid4()
    result = await registry.execute_tool("booking.execute", context)
    assert result.success is False
    assert result.denied_reason == "context_validation_failed:booking"
    assert "session_tenant_id" in result.validation_errors
    assert module.succeeds is True
    assert audit_calls[-1][0] == "tool_blocked"
async def test_protected_tool_requires_confirmed_pending_action():
    guard = UsageGuard(FakeUsageDb(limits={"allowed_modules": ["booking"], "allowed_ai_modes": ["standard", "concierge"]}))
    registry = ToolRegistry(guard=guard)
    registry._write_audit = _noop_audit
    registry.register_module(FakeModule(
        "booking",
        metadata=ToolMetadata(
            risk_level="sensitive_write",
            requires_confirmation=True,
            requires_verified_customer=False,
        ),
    ))
    context = make_context(plan_code="enterprise", status="active", modules={"booking": {"enabled": True, "fields": []}})
    result = await registry.execute_tool("booking.execute", context)
    assert result.success is False
    assert result.denied_reason == "confirmation_required:booking"
    assert result.data["required_next_step"] == "confirm"
    assert context.session_data.state.action_ledger.task_status == "awaiting_confirmation"
    assert context.session_data.state.action_ledger.pending_action.tool_key == "booking.execute"
async def test_protected_tool_requires_verified_customer_after_confirmation():
    guard = UsageGuard(FakeUsageDb(limits={"allowed_modules": ["booking"], "allowed_ai_modes": ["standard", "concierge"]}))
    registry = ToolRegistry(guard=guard)
    registry._write_audit = _noop_audit
    registry.register_module(FakeModule(
        "booking",
        metadata=ToolMetadata(
            risk_level="sensitive_write",
            requires_confirmation=True,
            requires_verified_customer=True,
        ),
    ))
    context = make_context(plan_code="enterprise", status="active", modules={"booking": {"enabled": True, "fields": []}})
    context.session_data.state.action_ledger.pending_action = PendingAction(
        tool_key="booking.execute",
        requires_confirmation=True,
        verification_action="booking_commitment",
    )
    context.session_data.state.action_ledger.task_status = "confirmed"
    result = await registry.execute_tool("booking.execute", context)
    assert result.success is False
    assert result.denied_reason == "customer_verification_required:booking"
    assert result.data["required_next_step"] == "customer_verification"
    assert result.data["verification_available_in_channel"] is True
async def test_raw_whatsapp_channel_string_does_not_bypass_customer_assurance():
    guard = UsageGuard(FakeUsageDb(limits={"allowed_modules": ["booking"], "allowed_ai_modes": ["standard", "concierge"]}))
    registry = ToolRegistry(guard=guard)
    registry._write_audit = _noop_audit
    registry.register_module(FakeModule(
        "booking",
        metadata=ToolMetadata(
            risk_level="sensitive_write",
            requires_confirmation=True,
            requires_verified_customer=True,
        ),
    ))
    context = make_context(plan_code="enterprise", status="active", modules={"booking": {"enabled": True, "fields": []}})
    context.incoming_message["channel_type"] = "whatsapp"
    context.session_data.state.action_ledger.pending_action = PendingAction(
        tool_key="booking.execute",
        requires_confirmation=True,
        verification_action="booking_commitment",
        channel_type="whatsapp",
    )
    context.session_data.state.action_ledger.task_status = "confirmed"
    result = await registry.execute_tool("booking.execute", context)
    assert result.success is False
    assert result.denied_reason == "customer_verification_required:booking"
async def test_protected_tool_executes_after_confirmation_and_verification():
    guard = UsageGuard(FakeUsageDb(limits={"allowed_modules": ["booking"], "allowed_ai_modes": ["standard", "concierge"]}))
    registry = ToolRegistry(guard=guard)
    registry._write_audit = _noop_audit
    registry.register_module(FakeModule(
        "booking",
        metadata=ToolMetadata(
            risk_level="sensitive_write",
            requires_confirmation=True,
            requires_verified_customer=True,
        ),
    ))
    context = make_context(plan_code="enterprise", status="active", modules={"booking": {"enabled": True, "fields": []}})
    context.session_data.state.action_ledger.pending_action = PendingAction(
        tool_key="booking.execute",
        requires_confirmation=True,
        verification_action="booking_commitment",
    )
    context.session_data.state.action_ledger.task_status = "confirmed"
    now = datetime.now(timezone.utc)
    context.session_data.state.action_ledger.verification.otp_verifications = [OtpVerificationEvidence(
        binding="a" * 64, action_nonce="b" * 32, verification_ref="c" * 32,
        tenant_id=str(context.tenant.id), conversation_id=str(context.session_data.conversation_id),
        channel_session_id=str(context.session_data.id), config_version=context.session_data.config_version,
        tool_key="booking.execute", args_hash="d" * 24, verified_at=now,
        expires_at=now + timedelta(minutes=5), phone_identity_hash=phone_identity_hash("+15550000000"),
    )]
    result = await registry.execute_tool("booking.execute", context)
    assert result.success is True
    assert result.denied_reason is None
    assert context.session_data.state.action_ledger.pending_action is None
    assert context.session_data.state.action_ledger.task_status == "completed"
async def test_second_confirm_after_success_requires_fresh_pending_action():
    guard = UsageGuard(FakeUsageDb(limits={"allowed_modules": ["booking"], "allowed_ai_modes": ["standard", "concierge"]}))
    registry = ToolRegistry(guard=guard)
    registry._write_audit = _noop_audit
    registry.register_module(FakeModule(
        "booking",
        metadata=ToolMetadata(
            risk_level="sensitive_write",
            requires_confirmation=True,
            requires_verified_customer=False,
        ),
    ))
    context = make_context(plan_code="enterprise", status="active", modules={"booking": {"enabled": True, "fields": []}})
    context.session_data.state.update_task_step_data("booking", {"service_id": "cleaning", "preferred_date": "tomorrow"})
    prepared = await registry.execute_tool("booking.execute", context)
    context.incoming_message["text"] = "Confirm"
    assert context.session_data.state.confirm_pending_action(
        "booking.execute",
        context.session_data.state.get_task_step_data("booking"),
    )
    committed = await registry.execute_tool("booking.execute", context)
    duplicate_confirm = await registry.execute_tool("booking.execute", context)
    assert prepared.denied_reason == "confirmation_required:booking"
    assert committed.success is True
    assert duplicate_confirm.success is False
    assert duplicate_confirm.denied_reason == "confirmation_required:booking"
async def test_protected_tool_confirm_message_does_not_commit_without_ledger_confirmation():
    guard = UsageGuard(FakeUsageDb(limits={"allowed_modules": ["booking"], "allowed_ai_modes": ["standard", "concierge"]}))
    registry = ToolRegistry(guard=guard)
    registry._write_audit = _noop_audit
    registry.register_module(FakeModule(
        "booking",
        metadata=ToolMetadata(
            risk_level="sensitive_write",
            requires_confirmation=True,
            requires_verified_customer=False,
        ),
    ))
    context = make_context(plan_code="enterprise", status="active", modules={"booking": {"enabled": True, "fields": []}})
    context.session_data.state.step_data = {"service_id": "cleaning", "preferred_date": "tomorrow"}
    prepared = await registry.execute_tool("booking.execute", context)
    context.incoming_message["text"] = "Confirm"
    committed = await registry.execute_tool("booking.execute", context)
    assert prepared.success is False
    assert prepared.denied_reason == "confirmation_required:booking"
    assert committed.success is False
    assert committed.denied_reason == "confirmation_required:booking"
    assert context.session_data.state.action_ledger.task_status == "awaiting_confirmation"
    assert context.session_data.state.action_ledger.pending_action is not None


async def test_protected_tool_executes_after_ledger_confirmation():
    guard = UsageGuard(FakeUsageDb(limits={"allowed_modules": ["booking"], "allowed_ai_modes": ["standard", "concierge"]}))
    registry = ToolRegistry(guard=guard)
    registry._write_audit = _noop_audit
    registry.register_module(FakeModule(
        "booking",
        metadata=ToolMetadata(
            risk_level="sensitive_write",
            requires_confirmation=True,
            requires_verified_customer=False,
        ),
    ))
    context = make_context(plan_code="enterprise", status="active", modules={"booking": {"enabled": True, "fields": []}})
    context.session_data.state.step_data = {"service_id": "cleaning", "preferred_date": "tomorrow"}
    prepared = await registry.execute_tool("booking.execute", context)
    assert context.session_data.state.confirm_pending_action(
        "booking.execute",
        context.session_data.state.get_task_step_data("booking"),
    )
    committed = await registry.execute_tool("booking.execute", context)
    assert prepared.denied_reason == "confirmation_required:booking"
    assert committed.success is True
    assert context.session_data.state.action_ledger.task_status == "completed"
    assert context.session_data.state.action_ledger.pending_action is None
async def test_protected_tool_changed_details_require_new_confirmation():
    guard = UsageGuard(FakeUsageDb(limits={"allowed_modules": ["booking"], "allowed_ai_modes": ["standard", "concierge"]}))
    registry = ToolRegistry(guard=guard)
    registry._write_audit = _noop_audit
    registry.register_module(FakeModule(
        "booking",
        metadata=ToolMetadata(
            risk_level="sensitive_write",
            requires_confirmation=True,
            requires_verified_customer=False,
        ),
    ))
    context = make_context(plan_code="enterprise", status="active", modules={"booking": {"enabled": True, "fields": []}})
    context.session_data.state.step_data = {"service_id": "cleaning", "preferred_date": "tomorrow"}
    await registry.execute_tool("booking.execute", context)
    context.session_data.state.step_data["preferred_date"] = "friday"
    context.incoming_message["text"] = "Confirm"
    result = await registry.execute_tool("booking.execute", context)
    assert result.success is False
    assert result.denied_reason == "confirmation_required:booking"
    assert context.session_data.state.action_ledger.task_status == "awaiting_confirmation"
    assert context.session_data.state.action_ledger.pending_action.args["preferred_date"] == "friday"
async def test_protected_tool_confirmation_uses_task_scoped_args_not_global_step_data():
    guard = UsageGuard(FakeUsageDb(limits={"allowed_modules": ["booking"], "allowed_ai_modes": ["standard", "concierge"]}))
    registry = ToolRegistry(guard=guard)
    registry._write_audit = _noop_audit
    registry.register_module(FakeModule(
        "booking",
        metadata=ToolMetadata(
            risk_level="sensitive_write",
            requires_confirmation=True,
            requires_verified_customer=False,
        ),
    ))
    context = make_context(plan_code="enterprise", status="active", modules={"booking": {"enabled": True, "fields": []}})
    context.session_data.state.update_task_step_data("booking", {"service_id": "cleaning", "preferred_date": "tomorrow"})
    context.session_data.state.update_task_step_data("order", {"item_id": "veg_biriyani"})
    prepared = await registry.execute_tool("booking.execute", context)
    context.session_data.state.step_data = {"item_id": "veg_biriyani"}
    context.session_data.state.action_ledger.current_task = "booking"
    context.incoming_message["text"] = "Confirm"
    assert context.session_data.state.confirm_pending_action(
        "booking.execute",
        context.session_data.state.get_task_step_data("booking"),
    )
    committed = await registry.execute_tool("booking.execute", context)
    assert prepared.denied_reason == "confirmation_required:booking"
    assert committed.success is True
    assert context.session_data.state.action_ledger.pending_action is None
async def test_tool_metadata_blocks_unsupported_channel():
    registry = ToolRegistry(guard=UsageGuard(FakeUsageDb(limits={"allowed_modules": ["booking"], "allowed_ai_modes": ["standard"]})))
    registry._write_audit = _noop_audit
    registry.register_module(FakeModule("booking", metadata=ToolMetadata(supported_channels=("whatsapp",))))
    context = make_context(plan_code="enterprise", status="active", modules={"booking": {"enabled": True, "fields": []}})
    context.incoming_message["channel_type"] = "awa_chat"
    result = await registry.execute_tool("booking.execute", context)
    assert result.success is False
    assert result.denied_reason == "channel_not_supported:booking:awa_chat"
async def test_paused_chat_blocks_normal_tool_but_allows_handoff():
    registry = ToolRegistry(guard=UsageGuard(FakeUsageDb(limits={"allowed_modules": ["booking", "human_handoff"], "allowed_ai_modes": ["standard"]})))
    registry._write_audit = _noop_audit
    registry.register_module(FakeModule("booking", metadata=ToolMetadata()))
    registry.register_module(FakeModule(
        "human_handoff",
        metadata=ToolMetadata(
            risk_level="write",
            requires_confirmation=False,
            requires_verified_customer=False,
            allowed_when_paused=True,
        ),
    ))
    context = make_context(
        plan_code="enterprise",
        status="active",
        modules={
            "booking": {"enabled": True, "fields": []},
            "human_handoff": {"enabled": True, "fields": []},
        },
    )
    context.session_data.state.workflow_state = ConversationState.ESCALATED
    blocked = await registry.execute_tool("booking.execute", context)
    allowed = await registry.execute_tool("human_handoff.execute", context)
    assert blocked.success is False
    assert blocked.denied_reason == "chat_paused:booking"
    assert allowed.success is True
async def test_human_handoff_persists_escalation(monkeypatch):
    context = _simple_context({"reason": "Needs human help"})
    calls = []
    async def fake_handoff(**kwargs):
        calls.append(kwargs)
        return {"id": context.session_data.conversation_id, "status": "escalated"}, None
    monkeypatch.setattr("backend.modules.human_handoff_module.support_service.create_handoff", fake_handoff)
    result = await human_handoff_module.execute(context)
    assert result.success is True
    assert result.created_resource_type == "conversation_escalation"
    assert result.created_resource_id == str(context.session_data.conversation_id)
    assert calls[0]["tenant_id"] == context.tenant.id
    assert calls[0]["conversation_id"] == context.session_data.conversation_id
    assert calls[0]["reason"] == "Needs human help"
    assert context.session_data.state.workflow_state == ConversationState.ESCALATED
async def test_payment_module_fails_without_amount_or_order():
    context = _simple_context({})
    result = await payments_module.execute(context)
    assert result.success is False
    assert result.validation_errors == {"amount": "A positive amount is required unless an order is linked."}
async def test_payment_module_persists_request(monkeypatch):
    context = _simple_context({"amount": "49.50", "reference": "callback"})
    calls = []
    async def fake_create_payment_request(**kwargs):
        calls.append(kwargs)
        return {"id": uuid4()}
    monkeypatch.setattr("backend.modules.payments_module.payment_service.create_payment_request", fake_create_payment_request)
    result = await payments_module.execute(context)
    assert result.success is True
    assert result.created_resource_type == "payment_request"
    assert calls[0]["tenant_id"] == context.tenant.id
    assert calls[0]["customer_id"] == context.user.id
    assert calls[0]["amount"] == 49.5
    assert calls[0]["metadata"]["source"] == "concierge_tool"
async def test_reminder_module_fails_without_schedule_or_message():
    context = _simple_context({"time_offset": 15})
    result = await reminders_module.execute(context)
    assert result.success is False
    assert result.validation_errors == {"message_text": "Reminder message text is required."}
async def test_reminder_module_persists_scheduled_message(monkeypatch):
    context = _simple_context({"time_offset": 15, "message_text": "We will call you soon", "channel": "manual"})
    calls = []
    async def fake_create_scheduled_message(**kwargs):
        calls.append(kwargs)
        return {"id": uuid4()}
    monkeypatch.setattr("backend.modules.reminders_module.reminder_service.create_scheduled_message", fake_create_scheduled_message)
    result = await reminders_module.execute(context)
    assert result.success is True
    assert result.created_resource_type == "scheduled_message"
    assert calls[0]["tenant_id"] == context.tenant.id
    assert calls[0]["customer_id"] == context.user.id
    assert calls[0]["message_text"] == "We will call you soon"
    assert calls[0]["scheduled_for"] > datetime.now(timezone.utc)
async def test_review_module_persists_request_and_rating(monkeypatch):
    context = _simple_context({"rating": 5, "feedback": "Great"})
    created_id = uuid4()
    create_calls = []
    action_calls = []
    async def fake_create_review_request(**kwargs):
        create_calls.append(kwargs)
        return {"id": created_id, "metadata": {"source": "concierge_tool"}}
    async def fake_action_review_request(*args, **kwargs):
        action_calls.append((args, kwargs))
        return {"id": created_id, "rating": 5, "status": "responded"}
    monkeypatch.setattr("backend.modules.reviews_module.review_service.create_review_request", fake_create_review_request)
    monkeypatch.setattr("backend.modules.reviews_module.review_service.action_review_request", fake_action_review_request)
    result = await reviews_module.execute(context)
    assert result.success is True
    assert result.created_resource_type == "review_request"
    assert create_calls[0]["status"] == "responded"
    assert action_calls[0][0][0] == context.tenant.id
    assert action_calls[0][0][1] == created_id
    assert action_calls[0][1]["action"] == "submit_rating"
    assert action_calls[0][1]["rating"] == 5
    assert action_calls[0][1]["feedback_text"] == "Great"
async def test_structured_result_serializes_tool_message():
    result = ToolExecutionResult(
        tool_key="payments.execute",
        success=True,
        user_message="Created",
        created_resource_type="payment_request",
        created_resource_id="pay_123",
        data={"amount": 50},
    )
    content = result.to_tool_message()
    assert '"success": true' in content
    assert '"created_resource_id": "pay_123"' in content
    assert '"amount": 50' in content
async def test_trial_expiry_blocks_messages():
    guard = UsageGuard(FakeUsageDb())
    context = make_context(trial_ends_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    with pytest.raises(UsageLimitExceeded) as exc:
        await guard.check_message_allowed(context)
    assert exc.value.reason == "trial_expired"
async def test_db_limits_override_policy_for_message_cap():
    guard = UsageGuard(FakeUsageDb(
        usage={"month_message_count": 2},
        limits={"monthly_messages": 2, "allowed_modules": ["booking"], "allowed_ai_modes": ["standard"]},
    ))
    context = make_context(plan_code="starter", status="active")
    with pytest.raises(UsageLimitExceeded) as exc:
        await guard.check_message_allowed(context)
    assert exc.value.reason == "monthly_messages"
async def test_ai_capability_profiles_are_plan_gated():
    guard = UsageGuard(FakeUsageDb())
    starter = make_context(plan_code="starter", status="active")
    pro = make_context(plan_code="pro", status="active")
    assert await guard.resolve_ai_mode(starter, "concierge") == "standard"
    assert await guard.resolve_ai_mode(pro, "concierge") == "advanced"
    starter_policy = await guard.get_policy_async(starter.tenant)
    downgraded = get_ai_capability_policy("concierge", starter_policy)
    assert downgraded["requested_mode"] == "advanced"
    assert downgraded["effective_mode"] == "standard"
    assert downgraded["runtime_engine"] == "unified_agent"
    assert downgraded["downgraded"] is True
    assert "not available" in downgraded["locked_reason"]
async def _noop_audit(*args, **kwargs):
    return None
def _simple_context(step_data):
    tenant_id = uuid4()
    conversation_id = uuid4()
    return SimpleNamespace(
        tenant=SimpleNamespace(id=tenant_id),
        user=SimpleNamespace(id=uuid4()),
        config=SimpleNamespace(config_data={}),
        session_data=SimpleNamespace(
            conversation_id=conversation_id,
            state=SimpleNamespace(
                step_data=step_data,
                workflow_state=ConversationState.EXECUTING,
                last_escalation_reason=None,
            ),
        ),
    )
