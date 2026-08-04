from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.api.entitlements import (
    TENANT_ADMIN_ROUTE_CLASSIFICATION,
    assert_config_update_allowed,
    build_tenant_admin_entitlement_summary,
    require_module_config_write,
    require_module_operational_write,
)
from backend.core.runtime_capabilities import KNOWN_RUNTIME_MODULES
from backend.schemas.config import build_default_tenant_config


def _config(*enabled: str, **extra):
    modules = {module: {"enabled": module in enabled, "fields": []} for module in KNOWN_RUNTIME_MODULES}
    return {
        "business_name": "Tenant",
        "modules": modules,
        **extra,
    }


def _tenant(tenant_id, plan_code="custom_plan"):
    return {
        "id": tenant_id,
        "name": "Tenant",
        "slug": "tenant",
        "plan_code": plan_code,
        "status": "active",
    }


def _db(tenant_id, config, allowed_modules, plan_code="custom_plan"):
    db = AsyncMock()
    db.get_tenant.return_value = _tenant(tenant_id, plan_code=plan_code)
    db.get_tenant_config.return_value = {"version": 1, "config_json": config}
    db.get_subscription_plan_by_code.return_value = {
        "plan_code": plan_code,
        "limits_json": {"allowed_modules": allowed_modules, "allowed_ai_modes": ["standard"]},
    }
    return db


@contextmanager
def _patch_db(db):
    with (
        patch("backend.api.entitlements.db_client", db),
        patch("backend.db.client.db_client", db),
    ):
        yield


@pytest.mark.asyncio
async def test_tenant_disabled_plan_allowed_module_can_be_configured_but_not_operationally_mutated():
    tenant_id = uuid4()
    db = _db(tenant_id, _config(), ["order"])

    with _patch_db(db):
        access = await require_module_config_write(tenant_id, "order")

        assert access.allowed_by_plan is True
        assert access.enabled_by_tenant is False
        assert access.can_edit_config is True
        assert access.can_enable_module is True

        with pytest.raises(HTTPException) as exc:
            await require_module_operational_write(tenant_id, "order")

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "module_tenant_disabled"


@pytest.mark.asyncio
async def test_plan_disallowed_module_cannot_be_enabled_or_configured():
    tenant_id = uuid4()
    current = _config()
    db = _db(tenant_id, current, ["booking"])
    proposed_enable = _config("order")
    proposed_edit = _config()
    proposed_edit["modules"]["order"]["fields"] = [{"key": "size", "type": "string", "required": True}]

    with _patch_db(db):
        with pytest.raises(HTTPException) as enable_exc:
            await assert_config_update_allowed(tenant_id, proposed_enable)
        with pytest.raises(HTTPException) as edit_exc:
            await assert_config_update_allowed(tenant_id, proposed_edit)
        with pytest.raises(HTTPException) as write_exc:
            await require_module_config_write(tenant_id, "order")

    assert enable_exc.value.detail["code"] == "module_plan_disallowed"
    assert edit_exc.value.detail["code"] == "module_plan_disallowed"
    assert write_exc.value.detail["code"] == "module_plan_disallowed"


@pytest.mark.asyncio
async def test_plan_disallowed_module_can_be_disabled_without_editing_feature_config():
    tenant_id = uuid4()
    current = _config("payments")
    db = _db(tenant_id, current, ["booking"])
    proposed = _config()

    with _patch_db(db):
        await assert_config_update_allowed(tenant_id, proposed)


@pytest.mark.asyncio
async def test_trial_booking_version_can_change_with_inherited_disabled_reminders():
    tenant_id = uuid4()
    current = build_default_tenant_config(
        "Trial Clinic",
        business_type="clinic",
        enabled_modules=["booking", "faq", "human_handoff"],
    )
    proposed = build_default_tenant_config(
        "Trial Clinic",
        business_type="clinic",
        enabled_modules=["booking", "faq", "human_handoff"],
    )
    proposed["services"] = [{"id": "consultation", "name": "Consultation", "duration_mins": 30}]
    db = _db(tenant_id, current, ["booking", "faq", "human_handoff"], plan_code="trial")

    with _patch_db(db):
        await assert_config_update_allowed(tenant_id, proposed)

    assert proposed["modules"]["reminders"]["enabled"] is False
    assert proposed["modules"]["booking"]["enabled"] is True


@pytest.mark.asyncio
async def test_trial_still_rejects_enabling_inherited_reminders():
    tenant_id = uuid4()
    current = build_default_tenant_config(
        "Trial Clinic",
        business_type="clinic",
        enabled_modules=["booking", "faq", "human_handoff"],
    )
    proposed = build_default_tenant_config(
        "Trial Clinic",
        business_type="clinic",
        enabled_modules=["booking", "faq", "human_handoff"],
    )
    proposed["modules"]["reminders"]["enabled"] = True
    db = _db(tenant_id, current, ["booking", "faq", "human_handoff"], plan_code="trial")

    with _patch_db(db):
        with pytest.raises(HTTPException) as exc:
            await assert_config_update_allowed(tenant_id, proposed)

    assert exc.value.detail["code"] == "module_plan_disallowed"


@pytest.mark.asyncio
async def test_plan_disallowed_top_level_faq_and_booking_setup_cannot_be_changed():
    tenant_id = uuid4()
    current = _config(faqs=[{"question": "Question", "answer": "Answer"}], services=[])
    db = _db(tenant_id, current, ["order"])
    proposed_faq = _config(faqs=[{"question": "Question 2", "answer": "Answer 2"}], services=[])
    proposed_booking = _config(faqs=current["faqs"], services=[{"id": "svc", "name": "Cut", "duration_mins": 30}])

    with _patch_db(db):
        with pytest.raises(HTTPException) as faq_exc:
            await assert_config_update_allowed(tenant_id, proposed_faq)
        with pytest.raises(HTTPException) as booking_exc:
            await assert_config_update_allowed(tenant_id, proposed_booking)

    assert faq_exc.value.detail["module"] == "faq"
    assert booking_exc.value.detail["module"] == "booking"


@pytest.mark.asyncio
async def test_tenant_limits_and_feature_flags_do_not_grant_admin_entitlement():
    tenant_id = uuid4()
    config = _config(
        "payments",
        limits={"allowed_modules": ["payments"]},
        feature_flags={"payments": True},
    )
    db = _db(tenant_id, config, ["booking"])

    with _patch_db(db):
        summary = await build_tenant_admin_entitlement_summary(tenant_id)

    payments = summary.module("payments")
    assert payments.enabled_by_tenant is True
    assert payments.allowed_by_plan is False
    assert payments.runtime_available is False
    assert payments.can_edit_config is False
    assert payments.can_mutate_runtime_data is False


@pytest.mark.asyncio
async def test_wildcard_custom_plan_allows_known_disabled_module_configuration():
    tenant_id = uuid4()
    db = _db(tenant_id, _config(), ["*"], plan_code="partner_all_access")

    with _patch_db(db):
        summary = await build_tenant_admin_entitlement_summary(tenant_id)
        access = await require_module_config_write(tenant_id, "reviews")

    assert summary.module("reviews").allowed_by_plan is True
    assert summary.module("reviews").runtime_available is False
    assert access.can_enable_module is True


def test_all_runtime_modules_have_admin_route_classification():
    classified = {
        module
        for value in TENANT_ADMIN_ROUTE_CLASSIFICATION.values()
        for module in value["modules"]
    }
    assert KNOWN_RUNTIME_MODULES <= classified


def test_operational_route_groups_are_explicitly_classified():
    operational = {
        key
        for key, value in TENANT_ADMIN_ROUTE_CLASSIFICATION.items()
        if value["category"] == "operational/runtime write"
    }
    assert {
        "bookings",
        "orders",
        "payments",
        "scheduled_messages",
        "reviews",
        "leads",
        "support_conversations",
    } <= operational
