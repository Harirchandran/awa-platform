from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.billing.usage_guard import PlanPolicy, UsageGuard
from backend.core.context import Tenant
from backend.core.runtime_capabilities import (
    KNOWN_RUNTIME_MODULES,
    build_runtime_capability_snapshot,
)


def _config(*enabled: str, **extra):
    modules = {module: {"enabled": module in enabled} for module in KNOWN_RUNTIME_MODULES}
    return {"modules": modules, **extra}


class _Guard:
    def __init__(self, allowed_modules):
        self.policy = SimpleNamespace(allowed_modules=tuple(allowed_modules))

    async def get_policy_async(self, _tenant):
        return self.policy


@pytest.mark.asyncio
async def test_snapshot_combines_tenant_and_plan_module_permissions():
    tenant_id = uuid4()
    snapshot = await build_runtime_capability_snapshot(
        tenant_id=tenant_id,
        config_data=_config("order"),
        plan_code="custom-retail-plan",
        tenant_status="active",
        guard=_Guard(["order", "booking"]),
    )

    assert snapshot.capability_for("order").runtime_available is True
    assert snapshot.capability_for("booking").reason == "tenant_disabled"
    assert snapshot.capability_for("payments").reason == "tenant_disabled"


@pytest.mark.asyncio
async def test_snapshot_distinguishes_plan_denial_from_tenant_disablement():
    snapshot = await build_runtime_capability_snapshot(
        tenant_id=uuid4(),
        config_data=_config("order", "booking"),
        plan_code="custom-plan",
        tenant_status="active",
        guard=_Guard(["booking"]),
    )

    assert snapshot.capability_for("booking").runtime_available is True
    assert snapshot.capability_for("order").reason == "plan_disallowed"


@pytest.mark.asyncio
async def test_snapshot_wildcard_allows_known_modules_but_not_unknown_modules():
    snapshot = await build_runtime_capability_snapshot(
        tenant_id=uuid4(),
        config_data=_config("order", "booking"),
        plan_code="custom-all-access",
        tenant_status="active",
        guard=_Guard(["*"]),
    )

    assert snapshot.is_available("order") is True
    assert snapshot.is_available("booking") is True
    assert snapshot.is_available("invented_module") is False
    assert snapshot.capability_for("invented_module").reason == "unknown_module"


@pytest.mark.asyncio
async def test_snapshot_ignores_tenant_limits_and_feature_flags():
    config = _config(
        "order",
        limits={"allowed_modules": ["order", "payments"]},
        feature_flags={"payments": True},
    )
    snapshot = await build_runtime_capability_snapshot(
        tenant_id=uuid4(),
        config_data=config,
        plan_code="restricted-custom-plan",
        tenant_status="active",
        guard=_Guard(["order"]),
    )

    assert snapshot.is_available("order") is True
    assert snapshot.is_available("payments") is False
    assert snapshot.capability_for("payments").reason == "tenant_disabled"


@pytest.mark.asyncio
async def test_snapshot_fails_closed_when_guard_cannot_resolve_entitlement():
    class FailingGuard:
        async def get_policy_async(self, _tenant):
            raise RuntimeError("plan database unavailable")

    snapshot = await build_runtime_capability_snapshot(
        tenant_id=uuid4(),
        config_data=_config("order"),
        plan_code="custom-plan",
        tenant_status="active",
        guard=FailingGuard(),
    )

    assert snapshot.is_available("order") is False
    assert snapshot.capability_for("order").reason == "unavailable"


@pytest.mark.asyncio
async def test_usage_guard_reads_db_entitlements_for_custom_plan_codes():
    class PlanDb:
        async def get_subscription_plan_by_code(self, plan_code):
            assert plan_code == "partner_special"
            return {"plan_code": plan_code, "limits_json": {"allowed_modules": ["order"]}}

    guard = UsageGuard(db_client_override=PlanDb())
    policy = await guard.get_policy_async(Tenant(
        id=uuid4(), name="Tenant", plan_code="partner_special", status="active"
    ))

    assert policy.plan_code == "partner_special"
    assert policy.allowed_modules == ("order",)
