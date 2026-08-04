from dataclasses import replace
from pathlib import Path

import pytest

from backend.core.config_manifest import CONFIG_PRECEDENCE, get_config_domain
from backend.core.feature_flag_registry import (
    FEATURE_FLAG_CONSUMER_TYPES,
    FEATURE_FLAG_OWNERS,
    FEATURE_FLAG_REGISTRY,
    FEATURE_FLAG_SAFETY_LEVELS,
    FEATURE_FLAG_STATUSES,
    REGISTRY_RUNTIME_IMPORT_RULE,
    FeatureFlagDefinition,
    get_feature_flag_definition,
    get_feature_flag_registry,
    list_feature_flags_by_owner,
    list_feature_flags_by_status,
    list_runtime_consumed_feature_flags,
    list_safety_sensitive_feature_flags,
    list_unknown_or_placeholder_feature_flags,
    validate_feature_flag_definition,
)


def _keys(definitions: tuple[FeatureFlagDefinition, ...]) -> set[str]:
    return {definition.key for definition in definitions}


def test_registry_exists_and_declares_dynamic_tenant_feature_flags():
    registry = get_feature_flag_registry()

    assert registry
    assert "tenant_feature_flags_dynamic" in _keys(registry)


def test_every_registry_entry_has_required_metadata_and_controlled_values():
    seen = set()
    for definition in FEATURE_FLAG_REGISTRY:
        assert definition.key
        assert definition.key not in seen
        seen.add(definition.key)
        assert definition.owner in FEATURE_FLAG_OWNERS
        assert definition.status in FEATURE_FLAG_STATUSES
        assert definition.storage
        assert definition.api_routes
        assert definition.runtime_consumers
        assert definition.consumer_types
        assert set(definition.consumer_types) <= FEATURE_FLAG_CONSUMER_TYPES
        assert definition.safety_level in FEATURE_FLAG_SAFETY_LEVELS
        assert isinstance(definition.tenant_editable, bool)
        assert isinstance(definition.super_admin_editable, bool)
        assert definition.notes


def test_invalid_owner_status_safety_and_consumer_values_are_rejected():
    valid = get_feature_flag_definition("tenant_feature_flags_dynamic")

    with pytest.raises(ValueError, match="Invalid feature flag owner"):
        validate_feature_flag_definition(replace(valid, owner="bad_owner"))

    with pytest.raises(ValueError, match="Invalid feature flag status"):
        validate_feature_flag_definition(replace(valid, status="bad_status"))

    with pytest.raises(ValueError, match="Invalid feature flag safety level"):
        validate_feature_flag_definition(replace(valid, safety_level="bad_safety"))

    with pytest.raises(ValueError, match="Invalid feature flag consumer type"):
        validate_feature_flag_definition(replace(valid, consumer_types=("bad_consumer",)))

    with pytest.raises(ValueError, match="cannot be active without a runtime consumer"):
        validate_feature_flag_definition(replace(valid, status="active", consumer_types=("api_only",)))


def test_helper_functions_return_expected_lists_and_reject_unknown_filters():
    dynamic = get_feature_flag_definition("tenant_feature_flags_dynamic")

    assert dynamic in list_feature_flags_by_owner("tenant_admin")
    assert dynamic in list_feature_flags_by_status("unknown")
    assert dynamic in list_unknown_or_placeholder_feature_flags()
    assert list_runtime_consumed_feature_flags() == ()
    assert list_safety_sensitive_feature_flags() == ()

    with pytest.raises(KeyError):
        get_feature_flag_definition("missing_flag")

    with pytest.raises(ValueError):
        list_feature_flags_by_owner("bad_owner")

    with pytest.raises(ValueError):
        list_feature_flags_by_status("bad_status")


def test_dynamic_flags_are_api_only_unknown_and_not_active_runtime_enforcement():
    dynamic = get_feature_flag_definition("tenant_feature_flags_dynamic")

    assert dynamic.status == "unknown"
    assert dynamic.storage == (
        "tenant_feature_flags.feature_key",
        "tenant_feature_flags.is_enabled",
        "tenant_feature_flags.metadata",
    )
    assert "/api/v1/feature-flags" in dynamic.api_routes
    assert "/api/v1/feature-flags/{feature_key}" in dynamic.api_routes
    assert dynamic.runtime_consumers == ("none_verified",)
    assert "api_only" in dynamic.consumer_types
    assert "runtime_enforcement" not in dynamic.consumer_types
    assert "workflow_behavior" not in dynamic.consumer_types
    assert "provider_behavior" not in dynamic.consumer_types


def test_any_future_runtime_consumed_flag_must_be_marked_active():
    runtime_types = {"runtime_enforcement", "workflow_behavior", "provider_behavior", "prompt_context"}

    for definition in get_feature_flag_registry():
        if runtime_types.intersection(definition.consumer_types):
            assert definition.status == "active"
            assert definition.runtime_consumers != ("none_verified",)


def test_feature_flags_do_not_claim_safety_sensitive_authority_or_bypasses():
    for definition in get_feature_flag_registry():
        assert definition.can_affect_provider_authority is False
        assert definition.can_affect_payment is False
        assert definition.can_affect_tool_access is False
        assert definition.can_affect_prompt_safety is False
        assert definition.can_bypass_usage_guard is False
        assert definition.can_bypass_capability_gateway is False
        assert definition.can_bypass_protected_confirmation is False
        assert definition.can_bypass_otp_verification is False
        assert definition.can_bypass_tenant_isolation is False
        assert definition.can_bypass_idempotency is False
        assert definition.can_bypass_no_fake_success is False
        assert definition.can_expose_internal_ids is False


def test_registry_declares_metadata_only_rule_and_runtime_modules_do_not_import_it():
    assert "documentation/testable metadata only" in REGISTRY_RUNTIME_IMPORT_RULE
    assert "Only tests may import it" in REGISTRY_RUNTIME_IMPORT_RULE

    backend_root = Path(__file__).resolve().parents[2]
    import_markers = (
        "from backend.core.feature_flag_registry",
        "import backend.core.feature_flag_registry",
        "import feature_flag_registry",
    )
    offenders = []
    for path in backend_root.rglob("*.py"):
        normalized = path.as_posix()
        if normalized.endswith("core/feature_flag_registry.py") or "/tests/" in normalized:
            continue
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in import_markers):
            offenders.append(path.relative_to(backend_root).as_posix())

    assert offenders == []


def test_usage_guard_capability_gateway_worker_webhook_and_workflows_do_not_import_registry():
    backend_root = Path(__file__).resolve().parents[2]
    checked_paths = [
        "billing/usage_guard.py",
        "capabilities/gateway.py",
        "tools/registry.py",
        "pipeline/worker.py",
        "pipeline/burst_worker.py",
        "api/webhook.py",
        "api/routes/public_chat.py",
        "logic/engine/core.py",
        "logic/engine/prompt.py",
        "core/config_loader.py",
    ]

    for relative_path in checked_paths:
        text = (backend_root / relative_path).read_text(encoding="utf-8")
        assert "feature_flag_registry" not in text


def test_config_manifest_alignment_for_feature_flags_and_payment_authority():
    feature_flags = get_config_domain("feature_flags")
    payment_provider = get_config_domain("payment_provider_authority")

    assert feature_flags.status == "partial"
    assert feature_flags.runtime_consumers == ("needs_verification",)
    assert "feature_flag_registry" in feature_flags.notes
    assert "runtime consumers remain unverified" in feature_flags.notes

    assert CONFIG_PRECEDENCE == (
        "environment_provider_settings",
        "backend_safety_invariants",
        "super_admin_plan_policy",
        "tenant_row_state",
        "active_tenant_config_version",
        "runtime_session_workflow_state",
        "domain_db_state",
        "llm_semantic_extraction",
        "tool_handler_repository_validation",
    )
    assert payment_provider.owner == "env_provider"
    assert payment_provider.tenant_editable is False
    assert payment_provider.llm_visible is False
