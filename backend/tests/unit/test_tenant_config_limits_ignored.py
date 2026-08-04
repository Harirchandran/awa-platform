import pytest
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from backend.core.context import ExecutionContext, Tenant, User, ConversationSession, SessionState, ConversationState, TenantConfig
from backend.billing.usage_guard import UsageGuard, UsageLimitExceeded
from backend.core.config_manifest import get_config_domain, list_legacy_or_ignored_config_domains
from backend.schemas.config import normalize_tenant_config

class FakeUsageDb:
    def __init__(self, limits=None, usage=None):
        self.limits = limits or {}
        self.usage = usage or {}
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
        return {"plan_code": plan_code, "limits_json": self.limits}

    async def upsert_cost_daily(self, **kwargs):
        self.recorded.append(kwargs)
        return True

def make_context(plan_code="trial", tenant_status="active", tenant_limits=None, tenant_modules=None, usage=None, ai_mode=None):
    tenant_id = uuid4()
    module_config = tenant_modules if tenant_modules is not None else {
        "booking": {"enabled": True, "fields": []},
    }

    config_data = {
        "business_name": "Test Tenant",
        "modules": module_config,
    }
    if tenant_limits is not None:
        config_data["limits"] = tenant_limits

    return ExecutionContext(
        tenant=Tenant(
            id=tenant_id,
            name="Test Tenant",
            plan_code=plan_code,
            status=tenant_status,
            ai_mode=ai_mode,
            trial_ends_at=datetime.now(timezone.utc) + timedelta(days=1),
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
            config_data=normalize_tenant_config(config_data),
        ),
        incoming_message={"text": "test", "channel_type": "awa_chat"},
    )

@pytest.mark.anyio
async def test_tenant_limits_cannot_increase_quota():
    # Plan has monthly_messages=10, tenant_config claims limits.monthly_messages=500
    plan_limits = {"monthly_messages": 10}
    tenant_limits = {"monthly_messages": 500, "daily_messages": 500, "max_ai_tokens_per_month": 50000}

    # 1. Monthly message limit: usage has 10 messages. Since plan allows 10, it should raise UsageLimitExceeded
    ctx = make_context(plan_code="starter", tenant_limits=tenant_limits)
    db = FakeUsageDb(limits=plan_limits, usage={"month_message_count": 10})
    guard = UsageGuard(db)
    with pytest.raises(UsageLimitExceeded, match="monthly_messages"):
        await guard.check_message_allowed(ctx)

    # 2. Daily message limit: Plan has daily_messages=5, usage has 5.
    plan_limits_daily = {"daily_messages": 5}
    ctx_daily = make_context(plan_code="starter", tenant_limits=tenant_limits)
    db_daily = FakeUsageDb(limits=plan_limits_daily, usage={"today_message_count": 5})
    guard_daily = UsageGuard(db_daily)
    with pytest.raises(UsageLimitExceeded, match="daily_messages"):
        await guard_daily.check_message_allowed(ctx_daily)

    # 3. Monthly AI tokens limit: Plan has monthly_ai_tokens=1000, usage has 1000.
    plan_limits_tokens = {"monthly_ai_tokens": 1000}
    ctx_tokens = make_context(plan_code="starter", tenant_limits=tenant_limits)
    db_tokens = FakeUsageDb(limits=plan_limits_tokens, usage={"month_ai_tokens_used": 1000})
    guard_tokens = UsageGuard(db_tokens)
    with pytest.raises(UsageLimitExceeded, match="monthly_ai_tokens"):
        await guard_tokens.check_message_allowed(ctx_tokens)

    # 4. Monthly credits limit: Plan has monthly_credits=50, usage has 50.
    plan_limits_credits = {"monthly_credits": 50}
    ctx_credits = make_context(plan_code="starter", tenant_limits=tenant_limits)
    db_credits = FakeUsageDb(limits=plan_limits_credits, usage={"month_credits_used": 50})
    guard_credits = UsageGuard(db_credits)
    with pytest.raises(UsageLimitExceeded, match="monthly_credits"):
        await guard_credits.check_message_allowed(ctx_credits)

@pytest.mark.anyio
async def test_tenant_limits_cannot_enable_module_disallowed_by_plan():
    # Plan does NOT allow "payments" module, but tenant config modules tries to enable it.
    plan_limits = {"allowed_modules": ["booking"]}
    tenant_modules = {"payments": {"enabled": True, "fields": []}}
    # Even if they set allowed_modules inside tenant limits, it should be ignored.
    tenant_limits = {"allowed_modules": ["payments"]}

    ctx = make_context(plan_code="starter", tenant_limits=tenant_limits, tenant_modules=tenant_modules)
    db = FakeUsageDb(limits=plan_limits)
    guard = UsageGuard(db)

    # Payments is disallowed by plan, so UsageGuard should raise UsageLimitExceeded
    with pytest.raises(UsageLimitExceeded, match="module_not_allowed:payments"):
        await guard.ensure_tool_allowed(ctx, "payments.create_request")

@pytest.mark.anyio
async def test_tenant_limits_cannot_bypass_ai_mode_plan_restriction():
    # Plan does not allow concierge mode, but tenant tries to request concierge or set it in limits.
    plan_limits = {"allowed_ai_modes": ["standard"]}
    tenant_limits = {"allowed_ai_modes": ["concierge"]}

    ctx = make_context(plan_code="starter", tenant_limits=tenant_limits, ai_mode="concierge")
    db = FakeUsageDb(limits=plan_limits)
    guard = UsageGuard(db)

    # UsageGuard.resolve_ai_mode should downgrade concierge to standard
    resolved = await guard.resolve_ai_mode(ctx, "concierge")
    assert resolved == "standard"

@pytest.mark.anyio
async def test_tenant_limits_cannot_override_credit_costs():
    # Plan has tool_credit_cost=10.0 and message_credit_cost=5.0
    # Tenant config tries to override to tool_credit_cost=0.1 and message_credit_cost=0.1
    plan_limits = {"tool_credit_cost": 10.0, "message_credit_cost": 5.0}
    tenant_limits = {"tool_credit_cost": 0.1, "message_credit_cost": 0.1}

    ctx = make_context(plan_code="starter", tenant_limits=tenant_limits)
    db = FakeUsageDb(limits=plan_limits)
    guard = UsageGuard(db)

    # Policy generated by UsageGuard should have the plan's costs, ignoring tenant config
    policy = await guard.get_policy_async(ctx.tenant)
    assert policy.tool_credit_cost == 10.0
    assert policy.message_credit_cost == 5.0

@pytest.mark.anyio
async def test_tenant_limits_cannot_bypass_payment_safety_or_confirmation():
    from backend.capabilities.gateway import CapabilityGateway
    from backend.capabilities.models import CapabilityRequest

    class FakeModuleObj:
        def get_tool_schema(self, config):
            return {"properties": {}, "required": []}
        def get_required_fields(self, config):
            return []

    class FakeToolDef:
        key = "payments.create_request"
        module_name = "payments"
        module = FakeModuleObj()

    class FakeMetadata:
        supported_channels = ("awa_chat",)
        requires_confirmation = True
        requires_verified_customer = False
        allowed_when_paused = False

    plan_limits = {"allowed_modules": ["booking"], "allowed_ai_modes": ["standard"]}
    tenant_limits = {"allowed_modules": ["payments"]}
    tenant_modules = {"payments": {"enabled": True, "fields": []}, "booking": {"enabled": True, "fields": []}}

    ctx = make_context(plan_code="starter", tenant_limits=tenant_limits, tenant_modules=tenant_modules)
    db = FakeUsageDb(limits=plan_limits)
    guard = UsageGuard(db)
    gateway = CapabilityGateway(guard)

    req = CapabilityRequest(
        tenant_id=ctx.tenant.id,
        tool_name="payments.create_request",
        channel="awa_chat",
        module_name="payments",
        tool_key="payments.create_request",
        input_payload={},
        customer_id=ctx.user.id,
        conversation_id=ctx.session_data.conversation_id,
        session_context={},
        tenant_config=ctx.config.config_data
    )

    decision = await gateway.evaluate(req, ctx, FakeToolDef(), FakeMetadata())
    assert decision.status == "denied"
    assert "module_not_allowed" in decision.reason_code

def test_manifest_ownership_precedence():
    # config manifest still marks tenant_config_limits as legacy/ignored
    limits = get_config_domain("tenant_config_limits")
    assert limits.owner == "legacy"
    assert limits.status == "ignored"

    # config manifest precedence still places super_admin_plan_policy above active_tenant_config_version
    from backend.core.config_manifest import CONFIG_PRECEDENCE
    super_admin_idx = CONFIG_PRECEDENCE.index("super_admin_plan_policy")
    tenant_config_idx = CONFIG_PRECEDENCE.index("active_tenant_config_version")
    assert super_admin_idx < tenant_config_idx

    # payment provider authority remains env/backend-owned, not tenant-owned
    provider = get_config_domain("payment_provider_authority")
    assert provider.owner == "env_provider"
    assert provider.tenant_editable is False

def test_config_normalization_preserves_ignored_limits():
    raw_config = {
        "business_name": "Old Salon",
        "limits": {
            "monthly_credits": 100,
            "daily_messages": 10,
            "max_ai_tokens_per_month": 5000
        }
    }
    normalized = normalize_tenant_config(raw_config)
    assert "limits" in normalized
    assert normalized["limits"]["monthly_credits"] == 100
    assert normalized["limits"]["daily_messages"] == 10
    assert normalized["limits"]["max_ai_tokens_per_month"] == 5000
