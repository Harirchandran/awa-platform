from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


FEATURE_FLAG_OWNERS = frozenset(
    {
        "tenant_admin",
        "super_admin",
        "platform",
        "runtime",
        "unknown",
    }
)

FEATURE_FLAG_STATUSES = frozenset(
    {
        "active",
        "ui_only",
        "placeholder",
        "legacy",
        "unknown",
        "unsafe_to_enable",
    }
)

FEATURE_FLAG_SAFETY_LEVELS = frozenset(
    {
        "ui_only",
        "business_behavior",
        "tenant_business_behavior",
        "customer_pii",
        "protected_action",
        "payment_sensitive",
        "platform_security",
        "runtime_internal",
    }
)

FEATURE_FLAG_CONSUMER_TYPES = frozenset(
    {
        "api_only",
        "ui_only",
        "runtime_enforcement",
        "prompt_context",
        "workflow_behavior",
        "provider_behavior",
        "unknown",
    }
)

REGISTRY_RUNTIME_IMPORT_RULE = (
    "The feature flag registry is documentation/testable metadata only. No "
    "existing runtime loader, config loader, route, worker, webhook, tool "
    "handler, workflow, prompt builder, localization helper, payment/provider "
    "code, UsageGuard, or CapabilityGateway should import it in Phase 3D-5A-3B. "
    "Only tests may import it in this phase."
)


@dataclass(frozen=True)
class FeatureFlagDefinition:
    key: str
    owner: str
    status: str
    storage: tuple[str, ...]
    api_routes: tuple[str, ...]
    runtime_consumers: tuple[str, ...]
    consumer_types: tuple[str, ...]
    safety_level: str
    tenant_editable: bool
    super_admin_editable: bool
    can_affect_tool_access: bool
    can_affect_payment: bool
    can_affect_provider_authority: bool
    can_affect_prompt_safety: bool
    can_bypass_usage_guard: bool
    can_bypass_capability_gateway: bool
    can_bypass_protected_confirmation: bool
    can_bypass_otp_verification: bool
    can_bypass_tenant_isolation: bool
    can_bypass_idempotency: bool
    can_bypass_no_fake_success: bool
    can_expose_internal_ids: bool
    notes: str


def _tuple(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(values)


def _definition(
    *,
    key: str,
    owner: str,
    status: str,
    storage: Iterable[str],
    api_routes: Iterable[str],
    runtime_consumers: Iterable[str],
    consumer_types: Iterable[str],
    safety_level: str,
    tenant_editable: bool,
    super_admin_editable: bool = False,
    can_affect_tool_access: bool = False,
    can_affect_payment: bool = False,
    can_affect_provider_authority: bool = False,
    can_affect_prompt_safety: bool = False,
    can_bypass_usage_guard: bool = False,
    can_bypass_capability_gateway: bool = False,
    can_bypass_protected_confirmation: bool = False,
    can_bypass_otp_verification: bool = False,
    can_bypass_tenant_isolation: bool = False,
    can_bypass_idempotency: bool = False,
    can_bypass_no_fake_success: bool = False,
    can_expose_internal_ids: bool = False,
    notes: str,
) -> FeatureFlagDefinition:
    definition = FeatureFlagDefinition(
        key=key,
        owner=owner,
        status=status,
        storage=_tuple(storage),
        api_routes=_tuple(api_routes),
        runtime_consumers=_tuple(runtime_consumers),
        consumer_types=_tuple(consumer_types),
        safety_level=safety_level,
        tenant_editable=tenant_editable,
        super_admin_editable=super_admin_editable,
        can_affect_tool_access=can_affect_tool_access,
        can_affect_payment=can_affect_payment,
        can_affect_provider_authority=can_affect_provider_authority,
        can_affect_prompt_safety=can_affect_prompt_safety,
        can_bypass_usage_guard=can_bypass_usage_guard,
        can_bypass_capability_gateway=can_bypass_capability_gateway,
        can_bypass_protected_confirmation=can_bypass_protected_confirmation,
        can_bypass_otp_verification=can_bypass_otp_verification,
        can_bypass_tenant_isolation=can_bypass_tenant_isolation,
        can_bypass_idempotency=can_bypass_idempotency,
        can_bypass_no_fake_success=can_bypass_no_fake_success,
        can_expose_internal_ids=can_expose_internal_ids,
        notes=notes,
    )
    return validate_feature_flag_definition(definition)


def validate_feature_flag_definition(definition: FeatureFlagDefinition) -> FeatureFlagDefinition:
    if not definition.key.strip():
        raise ValueError("Feature flag key is required")
    if definition.owner not in FEATURE_FLAG_OWNERS:
        raise ValueError(f"Invalid feature flag owner: {definition.owner}")
    if definition.status not in FEATURE_FLAG_STATUSES:
        raise ValueError(f"Invalid feature flag status: {definition.status}")
    if definition.safety_level not in FEATURE_FLAG_SAFETY_LEVELS:
        raise ValueError(f"Invalid feature flag safety level: {definition.safety_level}")
    invalid_consumer_types = set(definition.consumer_types) - FEATURE_FLAG_CONSUMER_TYPES
    if invalid_consumer_types:
        raise ValueError(
            "Invalid feature flag consumer type: "
            + ", ".join(sorted(invalid_consumer_types))
        )
    if not definition.storage:
        raise ValueError(f"Feature flag {definition.key} must declare storage")
    if not definition.api_routes:
        raise ValueError(f"Feature flag {definition.key} must declare API routes")
    if not definition.consumer_types:
        raise ValueError(f"Feature flag {definition.key} must declare consumer types")
    if definition.status == "active" and "runtime_enforcement" not in definition.consumer_types:
        raise ValueError(f"Feature flag {definition.key} cannot be active without a runtime consumer")
    return definition


FEATURE_FLAG_REGISTRY = (
    _definition(
        key="tenant_feature_flags_dynamic",
        owner="tenant_admin",
        status="unknown",
        storage=("tenant_feature_flags.feature_key", "tenant_feature_flags.is_enabled", "tenant_feature_flags.metadata"),
        api_routes=("/api/v1/feature-flags", "/api/v1/feature-flags/{feature_key}"),
        runtime_consumers=("none_verified",),
        consumer_types=("api_only", "unknown"),
        safety_level="ui_only",
        tenant_editable=True,
        notes=(
            "The backend accepts arbitrary tenant-defined feature_key values and "
            "persists them in tenant_feature_flags. Current backend code verifies "
            "list/upsert API behavior only; no UsageGuard, CapabilityGateway, "
            "ToolRegistry, worker, webhook, public chat, prompt, config loader, "
            "payment/provider, or workflow consumer reads these flags in Phase "
            "3D-5A-3B."
        ),
    ),
)

_REGISTRY_BY_KEY = {definition.key: definition for definition in FEATURE_FLAG_REGISTRY}


def get_feature_flag_registry() -> tuple[FeatureFlagDefinition, ...]:
    return FEATURE_FLAG_REGISTRY


def get_feature_flag_definition(key: str) -> FeatureFlagDefinition:
    try:
        return _REGISTRY_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"Unknown feature flag definition: {key}") from exc


def list_feature_flags_by_status(status: str) -> tuple[FeatureFlagDefinition, ...]:
    if status not in FEATURE_FLAG_STATUSES:
        raise ValueError(f"Invalid feature flag status: {status}")
    return tuple(definition for definition in FEATURE_FLAG_REGISTRY if definition.status == status)


def list_feature_flags_by_owner(owner: str) -> tuple[FeatureFlagDefinition, ...]:
    if owner not in FEATURE_FLAG_OWNERS:
        raise ValueError(f"Invalid feature flag owner: {owner}")
    return tuple(definition for definition in FEATURE_FLAG_REGISTRY if definition.owner == owner)


def list_runtime_consumed_feature_flags() -> tuple[FeatureFlagDefinition, ...]:
    runtime_types = {"runtime_enforcement", "prompt_context", "workflow_behavior", "provider_behavior"}
    return tuple(
        definition
        for definition in FEATURE_FLAG_REGISTRY
        if runtime_types.intersection(definition.consumer_types)
    )


def list_unknown_or_placeholder_feature_flags() -> tuple[FeatureFlagDefinition, ...]:
    return tuple(
        definition
        for definition in FEATURE_FLAG_REGISTRY
        if definition.status in {"placeholder", "unknown"} or "unknown" in definition.consumer_types
    )


def list_safety_sensitive_feature_flags() -> tuple[FeatureFlagDefinition, ...]:
    sensitive_levels = {
        "customer_pii",
        "protected_action",
        "payment_sensitive",
        "platform_security",
        "runtime_internal",
    }
    return tuple(
        definition
        for definition in FEATURE_FLAG_REGISTRY
        if definition.safety_level in sensitive_levels
        or any(
            (
                definition.can_affect_tool_access,
                definition.can_affect_payment,
                definition.can_affect_provider_authority,
                definition.can_affect_prompt_safety,
                definition.can_bypass_usage_guard,
                definition.can_bypass_capability_gateway,
                definition.can_bypass_protected_confirmation,
                definition.can_bypass_otp_verification,
                definition.can_bypass_tenant_isolation,
                definition.can_bypass_idempotency,
                definition.can_bypass_no_fake_success,
                definition.can_expose_internal_ids,
            )
        )
    )


for _definition_entry in FEATURE_FLAG_REGISTRY:
    validate_feature_flag_definition(_definition_entry)
if len(_REGISTRY_BY_KEY) != len(FEATURE_FLAG_REGISTRY):
    raise ValueError("Feature flag registry contains duplicate keys")
