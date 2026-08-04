from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from backend.api.entitlements import build_tenant_admin_entitlement_summary
from backend.core.runtime_capabilities import KNOWN_RUNTIME_MODULES


def _config(*enabled: str):
    return {
        "business_name": "Tenant",
        "modules": {module: {"enabled": module in enabled, "fields": []} for module in KNOWN_RUNTIME_MODULES},
    }


def _db(tenant_id, config, allowed_modules):
    db = AsyncMock()
    db.get_tenant.return_value = {
        "id": tenant_id,
        "name": "Tenant",
        "slug": "tenant",
        "plan_code": "custom_retail",
        "status": "active",
    }
    db.get_tenant_config.return_value = {"version": 8, "config_json": config}
    db.get_subscription_plan_by_code.return_value = {
        "plan_code": "custom_retail",
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
async def test_entitlement_summary_distinguishes_disabled_from_plan_locked():
    tenant_id = uuid4()
    db = _db(tenant_id, _config("booking", "payments"), ["booking", "order"])

    with _patch_db(db):
        public = (await build_tenant_admin_entitlement_summary(tenant_id)).to_public_dict()

    booking = public["modules"]["booking"]
    order = public["modules"]["order"]
    payments = public["modules"]["payments"]

    assert booking["enabled_by_tenant"] is True
    assert booking["allowed_by_plan"] is True
    assert booking["runtime_available"] is True
    assert booking["can_mutate_runtime_data"] is True

    assert order["enabled_by_tenant"] is False
    assert order["allowed_by_plan"] is True
    assert order["runtime_available"] is False
    assert order["can_configure"] is True
    assert order["can_enable_module"] is True
    assert order["display_reason"] == "disabled_by_admin"

    assert payments["enabled_by_tenant"] is True
    assert payments["allowed_by_plan"] is False
    assert payments["runtime_available"] is False
    assert payments["can_configure"] is False
    assert payments["can_mutate_runtime_data"] is False
    assert payments["display_reason"] == "upgrade_required"


@pytest.mark.asyncio
async def test_entitlement_summary_has_every_registered_runtime_module():
    tenant_id = uuid4()
    db = _db(tenant_id, _config("booking"), ["*"])

    with _patch_db(db):
        public = (await build_tenant_admin_entitlement_summary(tenant_id)).to_public_dict()

    assert set(public["modules"]) == set(KNOWN_RUNTIME_MODULES)
    for module in KNOWN_RUNTIME_MODULES:
        entry = public["modules"][module]
        assert entry["module"] == module
        assert "enabled_by_tenant" in entry
        assert "allowed_by_plan" in entry
        assert "runtime_available" in entry
        assert "can_configure" in entry
        assert "can_mutate_runtime_data" in entry


@pytest.mark.asyncio
async def test_entitlement_summary_does_not_expose_raw_plan_limits_or_security_data():
    tenant_id = uuid4()
    db = _db(tenant_id, _config("booking"), ["booking"])

    with _patch_db(db):
        public = (await build_tenant_admin_entitlement_summary(tenant_id)).to_public_dict()

    rendered = str(public)
    assert "limits_json" not in rendered
    assert "allowed_modules" not in rendered
    assert "tenant_id" not in rendered
    assert "provider" not in rendered
    assert "secret" not in rendered.lower()
