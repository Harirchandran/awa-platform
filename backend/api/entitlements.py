from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from uuid import UUID

from fastapi import HTTPException

from backend.core.context import Tenant
from backend.core.runtime_capabilities import KNOWN_RUNTIME_MODULES, RuntimeCapabilitySnapshot, build_runtime_capability_snapshot
from backend.db.client import db_client
from backend.schemas.config import normalize_tenant_config


CONFIG_PREPARATION_MODULES = frozenset({
    "booking",
    "order",
    "payments",
    "lead_capture",
    "support",
    "reminders",
    "reviews",
    "faq",
    "human_handoff",
})

TENANT_ADMIN_ROUTE_CLASSIFICATION: Mapping[str, Mapping[str, Any]] = {
    "config": {"category": "global/tenant setting", "modules": tuple(CONFIG_PREPARATION_MODULES)},
    "tools": {"category": "global/tenant setting", "modules": tuple(CONFIG_PREPARATION_MODULES)},
    "catalog": {"category": "configuration/preparation write", "modules": ("order",)},
    "catalog_media": {"category": "configuration/preparation write", "modules": ("order",)},
    "bookings": {"category": "operational/runtime write", "modules": ("booking",)},
    "orders": {"category": "operational/runtime write", "modules": ("order",)},
    "payments": {"category": "operational/runtime write", "modules": ("payments",)},
    "reminder_rules": {"category": "configuration/preparation write", "modules": ("reminders",)},
    "scheduled_messages": {"category": "operational/runtime write", "modules": ("reminders",)},
    "review_settings": {"category": "configuration/preparation write", "modules": ("reviews",)},
    "reviews": {"category": "operational/runtime write", "modules": ("reviews",)},
    "leads": {"category": "operational/runtime write", "modules": ("lead_capture",)},
    "support_conversations": {"category": "operational/runtime write", "modules": ("support", "human_handoff")},
    "knowledge": {"category": "configuration/preparation write", "modules": ("faq",)},
    "whatsapp_accounts": {"category": "provider/channel setup", "modules": tuple()},
}


@dataclass(frozen=True)
class TenantAdminModuleAccess:
    module: str
    enabled_by_tenant: bool
    allowed_by_plan: bool
    runtime_available: bool
    reason: str

    @property
    def locked_by_plan(self) -> bool:
        return not self.allowed_by_plan

    @property
    def can_view_config(self) -> bool:
        return self.allowed_by_plan

    @property
    def can_edit_config(self) -> bool:
        return self.allowed_by_plan

    @property
    def can_enable_module(self) -> bool:
        return self.allowed_by_plan

    @property
    def can_mutate_runtime_data(self) -> bool:
        return self.allowed_by_plan and self.enabled_by_tenant

    def to_public_dict(self) -> dict[str, Any]:
        safe_reason = None
        if not self.allowed_by_plan:
            safe_reason = "upgrade_required"
        elif not self.enabled_by_tenant:
            safe_reason = "disabled_by_admin"
        return {
            "module": self.module,
            "enabled_by_tenant": self.enabled_by_tenant,
            "enabled": self.enabled_by_tenant,
            "allowed_by_plan": self.allowed_by_plan,
            "plan_allowed": self.allowed_by_plan,
            "runtime_available": self.runtime_available,
            "locked_by_plan": self.locked_by_plan,
            "locked": self.locked_by_plan,
            "can_view_config": self.can_view_config,
            "can_edit_config": self.can_edit_config,
            "can_configure": self.can_edit_config,
            "can_enable_module": self.can_enable_module,
            "can_mutate_runtime_data": self.can_mutate_runtime_data,
            "display_reason": safe_reason,
            "locked_reason": None if self.allowed_by_plan else "Not available on your current plan",
        }


@dataclass(frozen=True)
class TenantAdminEntitlementSummary:
    tenant_id: UUID
    modules: Mapping[str, TenantAdminModuleAccess]

    def module(self, module_key: str) -> TenantAdminModuleAccess:
        access = self.modules.get(module_key)
        if access:
            return access
        return TenantAdminModuleAccess(
            module=module_key,
            enabled_by_tenant=False,
            allowed_by_plan=False,
            runtime_available=False,
            reason="unknown_module",
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "modules": {
                key: access.to_public_dict()
                for key, access in sorted(self.modules.items())
            },
        }


def _tenant_id(value: UUID | str) -> UUID:
    return UUID(value) if isinstance(value, str) else value


def _parse_config_json(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _forbidden(code: str, module: str, message: str, status_code: int = 403) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "module": module,
            "message": message,
        },
    )


async def _load_snapshot(tenant_id: UUID | str) -> RuntimeCapabilitySnapshot:
    resolved_tenant_id = _tenant_id(tenant_id)
    tenant = await db_client.get_tenant(resolved_tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    config = await db_client.get_tenant_config(resolved_tenant_id)
    config_json = _parse_config_json(config.get("config_json") if config else {})
    return await build_runtime_capability_snapshot(
        tenant_id=resolved_tenant_id,
        config_data=normalize_tenant_config(config_json),
        plan_code=tenant.get("plan_code") or "trial",
        tenant_status=tenant.get("status") or "trial",
        tenant_name=tenant.get("name") or "",
    )


async def build_tenant_admin_entitlement_summary(tenant_id: UUID | str) -> TenantAdminEntitlementSummary:
    snapshot = await _load_snapshot(tenant_id)
    modules = {
        module: TenantAdminModuleAccess(
            module=module,
            enabled_by_tenant=capability.enabled_by_tenant,
            allowed_by_plan=capability.allowed_by_plan,
            runtime_available=capability.runtime_available,
            reason=capability.reason,
        )
        for module, capability in snapshot.modules.items()
    }
    return TenantAdminEntitlementSummary(tenant_id=snapshot.tenant_id, modules=modules)


async def require_module_config_write(tenant_id: UUID | str, module_key: str) -> TenantAdminModuleAccess:
    summary = await build_tenant_admin_entitlement_summary(tenant_id)
    access = summary.module(module_key)
    if module_key not in KNOWN_RUNTIME_MODULES:
        raise _forbidden("unknown_module", module_key, "This module is not available for tenant-admin writes.")
    if not access.allowed_by_plan:
        raise _forbidden("module_plan_disallowed", module_key, "This feature is not available on your current plan.")
    return access


async def require_module_operational_write(tenant_id: UUID | str, module_key: str) -> TenantAdminModuleAccess:
    access = await require_module_config_write(tenant_id, module_key)
    if not access.enabled_by_tenant:
        raise _forbidden("module_tenant_disabled", module_key, "This feature is currently disabled for this business.")
    return access


async def require_any_module_operational_write(tenant_id: UUID | str, module_keys: Iterable[str]) -> TenantAdminModuleAccess:
    summary = await build_tenant_admin_entitlement_summary(tenant_id)
    keys = tuple(module_keys)
    for module_key in keys:
        if module_key not in KNOWN_RUNTIME_MODULES:
            raise _forbidden("unknown_module", module_key, "This module is not available for tenant-admin writes.")
    if not any(summary.module(module_key).allowed_by_plan for module_key in keys):
        raise _forbidden("module_plan_disallowed", keys[0] if keys else "unknown", "This feature is not available on your current plan.")
    for module_key in keys:
        access = summary.module(module_key)
        if access.allowed_by_plan and access.enabled_by_tenant:
            return access
    raise _forbidden("module_tenant_disabled", keys[0] if keys else "unknown", "This feature is currently disabled for this business.")


def _stable_without_enabled(module_config: Mapping[str, Any] | None) -> str:
    comparable = dict(module_config or {})
    comparable.pop("enabled", None)
    return json.dumps(comparable, sort_keys=True, separators=(",", ":"), default=str)


async def assert_config_update_allowed(tenant_id: UUID | str, new_config: Mapping[str, Any]) -> None:
    summary = await build_tenant_admin_entitlement_summary(tenant_id)
    current = await db_client.get_tenant_config(_tenant_id(tenant_id))
    current_config = normalize_tenant_config(_parse_config_json(current.get("config_json") if current else {}))
    proposed_config = normalize_tenant_config(dict(new_config or {}))
    current_modules = current_config.get("modules", {})
    proposed_modules = proposed_config.get("modules", {})

    all_module_keys = set(current_modules) | set(proposed_modules) | set(KNOWN_RUNTIME_MODULES)
    for module_key in sorted(all_module_keys):
        access = summary.module(module_key)
        current_module = current_modules.get(module_key, {})
        proposed_module = proposed_modules.get(module_key, {})
        if access.allowed_by_plan:
            continue
        if module_key not in KNOWN_RUNTIME_MODULES:
            if proposed_module != current_module:
                raise _forbidden("unknown_module", module_key, "Unknown modules cannot be changed from tenant config.")
            continue
        if isinstance(proposed_module, Mapping) and proposed_module.get("enabled") is True:
            raise _forbidden("module_plan_disallowed", module_key, "This feature cannot be enabled on your current plan.")
        if _stable_without_enabled(proposed_module) != _stable_without_enabled(current_module):
            raise _forbidden("module_plan_disallowed", module_key, "This feature cannot be configured on your current plan.")

    for module_key, config_keys in {
        "booking": ("services", "booking_settings"),
        "faq": ("faqs",),
    }.items():
        if summary.module(module_key).allowed_by_plan:
            continue
        for config_key in config_keys:
            if proposed_config.get(config_key) != current_config.get(config_key):
                raise _forbidden("module_plan_disallowed", module_key, "This feature cannot be configured on your current plan.")
