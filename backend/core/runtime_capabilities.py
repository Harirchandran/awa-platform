"""Backend-derived capability visibility for public runtime surfaces.

This module is deliberately a visibility snapshot only.  Tool execution remains
owned by ``UsageGuard``, ``CapabilityGateway``, and ``ToolRegistry``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from uuid import UUID

from backend.billing.usage_guard import UsageGuard, usage_guard
from backend.core.context import Tenant


# These are the module names registered by the current runtime contract.  A
# config-only or unknown module must never become customer-visible here.
KNOWN_RUNTIME_MODULES = frozenset({
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

RUNTIME_MODULE_ALIASES = {
    "booking.update": "booking",
    "catalog_search": "order",
    "payment": "payments",
    "reminder": "reminders",
    "review": "reviews",
}

INTERNAL_OR_NON_PUBLIC_MODULES: Mapping[str, str] = {}


def normalize_runtime_module_key(module: str | None) -> str:
    """Map registered tool/module aliases to the canonical runtime module key."""
    raw = str(module or "").strip()
    if not raw:
        return ""
    if raw in RUNTIME_MODULE_ALIASES:
        return RUNTIME_MODULE_ALIASES[raw]
    if "." in raw:
        raw = raw.split(".", 1)[0]
    return RUNTIME_MODULE_ALIASES.get(raw, raw)


def classify_runtime_module_key(module: str | None) -> str:
    """Return the known public module key or raise for an unclassified module."""
    normalized = normalize_runtime_module_key(module)
    if normalized in KNOWN_RUNTIME_MODULES:
        return normalized
    if normalized in INTERNAL_OR_NON_PUBLIC_MODULES:
        return normalized
    raise ValueError(f"Unclassified runtime module: {module}")


@dataclass(frozen=True)
class RuntimeCapability:
    module: str
    enabled_by_tenant: bool
    allowed_by_plan: bool
    runtime_available: bool
    reason: str


@dataclass(frozen=True)
class RuntimeCapabilitySnapshot:
    tenant_id: UUID
    modules: Mapping[str, RuntimeCapability]

    @property
    def runtime_available_modules(self) -> frozenset[str]:
        return frozenset(
            module for module, capability in self.modules.items()
            if capability.runtime_available
        )

    def is_available(self, module: str) -> bool:
        capability = self.modules.get(str(module))
        return bool(capability and capability.runtime_available)

    def capability_for(self, module: str) -> RuntimeCapability:
        normalized = str(module)
        return self.modules.get(
            normalized,
            RuntimeCapability(
                module=normalized,
                enabled_by_tenant=False,
                allowed_by_plan=False,
                runtime_available=False,
                reason="unknown_module",
            ),
        )


async def build_runtime_capability_snapshot(
    *,
    tenant_id: UUID,
    config_data: Mapping[str, Any] | None,
    plan_code: str | None,
    tenant_status: str | None,
    tenant_name: str = "",
    guard: UsageGuard = usage_guard,
) -> RuntimeCapabilitySnapshot:
    """Return the safe module view shared by public menus and greetings.

    ``UsageGuard.get_policy_async`` is the entitlement source so DB-backed
    ``subscription_plans.limits_json.allowed_modules`` retains the same
    interpretation as execution.  Its existing conservative fallback behavior
    is retained when plan lookup is unavailable.
    """
    valid_config = isinstance(config_data, Mapping)
    modules_config = config_data.get("modules") if valid_config else None
    valid_modules = isinstance(modules_config, Mapping)
    status_active = str(tenant_status or "").lower() in {"trial", "active"}

    allowed_modules: frozenset[str] = frozenset()
    policy_available = False
    if status_active:
        try:
            policy = await guard.get_policy_async(Tenant(
                id=tenant_id,
                name=tenant_name or "public-chat",
                plan_code=str(plan_code or ""),
                status=str(tenant_status or ""),
            ))
            allowed_modules = frozenset(str(module) for module in policy.allowed_modules)
            policy_available = True
        except Exception:
            # Public visibility fails closed if the guard itself cannot resolve
            # an entitlement.  Execution guards remain the final authority.
            policy_available = False

    capabilities: dict[str, RuntimeCapability] = {}
    for module in KNOWN_RUNTIME_MODULES:
        module_config = modules_config.get(module) if valid_modules else None
        enabled_by_tenant = isinstance(module_config, Mapping) and module_config.get("enabled") is True
        allowed_by_plan = policy_available and ("*" in allowed_modules or module in allowed_modules)

        if not valid_config or not valid_modules:
            reason = "missing_config"
        elif not status_active:
            reason = "tenant_inactive"
        elif not enabled_by_tenant:
            reason = "tenant_disabled"
        elif not allowed_by_plan:
            reason = "plan_disallowed" if policy_available else "unavailable"
        else:
            reason = "available"

        capabilities[module] = RuntimeCapability(
            module=module,
            enabled_by_tenant=enabled_by_tenant,
            allowed_by_plan=allowed_by_plan,
            runtime_available=reason == "available",
            reason=reason,
        )

    return RuntimeCapabilitySnapshot(tenant_id=tenant_id, modules=capabilities)
