import pytest
from uuid import UUID, uuid4

from backend.billing.usage_guard import (
    PlanPolicy,
    UsageGuard,
    get_ai_capability_policy,
    validate_plan_limits,
)
from backend.core.ai_modes import normalize_ai_mode, normalize_allowed_ai_modes
from backend.core.context import ConversationSession, ExecutionContext, Tenant, TenantConfig, User
from backend.schemas.config import normalize_tenant_config


class FakePlanDb:
    async def get_subscription_plan_by_code(self, plan_code):
        return None


def test_normalize_ai_mode_values():
    assert normalize_ai_mode("standard") == "standard"
    assert normalize_ai_mode("advanced") == "advanced"
    assert normalize_ai_mode("concierge") == "advanced"
    assert normalize_ai_mode("CONCIERGE") == "advanced"
    assert normalize_ai_mode("") == "standard"
    assert normalize_ai_mode(None) == "standard"
    assert normalize_ai_mode("robot") == "standard"


def test_tenant_config_normalizes_legacy_concierge_to_advanced():
    config = normalize_tenant_config({
        "business_name": "Legacy Clinic",
        "business_type": "clinic",
        "ai_mode": "concierge",
    })
    assert config["ai_mode"] == "advanced"


def test_tenant_config_preserves_canonical_modes():
    assert normalize_tenant_config({"business_name": "Standard Biz", "ai_mode": "standard"})["ai_mode"] == "standard"
    assert normalize_tenant_config({"business_name": "Advanced Biz", "ai_mode": "advanced"})["ai_mode"] == "advanced"


def test_allowed_ai_modes_normalize_legacy_plan_limits():
    assert normalize_allowed_ai_modes(["standard", "concierge"], ("standard",)) == ("standard", "advanced")
    assert normalize_allowed_ai_modes(["standard", "advanced"], ("standard",)) == ("standard", "advanced")


def test_ai_capability_policy_uses_advanced_for_legacy_concierge_request():
    policy = PlanPolicy(
        plan_code="legacy_pro",
        monthly_messages=None,
        daily_messages=None,
        monthly_ai_tokens=None,
        monthly_credits=None,
        allowed_ai_modes=("standard", "advanced"),
        allowed_modules=("*",),
    )
    result = get_ai_capability_policy("concierge", policy)
    assert result["requested_mode"] == "advanced"
    assert result["effective_mode"] == "advanced"
    assert result["runtime_engine"] == "unified_agent"


@pytest.mark.anyio
async def test_plan_allowed_concierge_effectively_allows_advanced():
    result = await validate_plan_limits({"allowed_ai_modes": ["standard", "concierge"]}, "legacy_plan")
    assert result["allowed_ai_modes"] == ["standard", "advanced"]


@pytest.mark.anyio
async def test_requested_advanced_denied_when_plan_allows_only_standard():
    tenant_id = UUID("00000000-0000-0000-0000-000000000001")
    tenant = Tenant(id=tenant_id, name="Starter", plan_code="starter", status="active")
    context = ExecutionContext(
        tenant=tenant,
        user=User(id=uuid4(), phone_number="+10000000000"),
        session_data=ConversationSession(id=uuid4(), tenant_id=tenant_id, conversation_id=uuid4(), config_version=1),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={}),
    )
    guard = UsageGuard(FakePlanDb())
    assert await guard.resolve_ai_mode(context, "advanced") == "standard"
    assert await guard.resolve_ai_mode(context, "concierge") == "standard"
