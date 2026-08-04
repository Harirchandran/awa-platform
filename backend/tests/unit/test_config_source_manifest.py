from dataclasses import replace
from pathlib import Path

import pytest

from backend.core.config_manifest import (
    CONFIG_MANIFEST,
    CONFIG_PRECEDENCE,
    DOMAIN_STATUSES,
    MANIFEST_RUNTIME_IMPORT_RULE,
    OWNER_CATEGORIES,
    SAFETY_LEVELS,
    ConfigOwnership,
    assert_known_config_domain,
    get_config_domain,
    get_config_manifest,
    list_config_domains_by_owner,
    list_legacy_or_ignored_config_domains,
    list_missing_config_domains,
    list_safety_sensitive_config_domains,
    validate_config_domain,
)


REQUIRED_DOMAINS = {
    "business_name",
    "business_type",
    "global_business_behavior_preset",
    "tenant_receptionist_behavior_overrides",
    "effective_behavior_profile",
    "language_locale",
    "tone_persona",
    "greeting_fallback_escalation",
    "working_hours",
    "holidays_closed_days",
    "services",
    "booking_fields",
    "booking_rules",
    "booking_availability",
    "staff_resources",
    "catalog",
    "order_settings",
    "fulfillment_options",
    "order_payment_methods",
    "price_currency_tax_delivery",
    "payment_module_settings",
    "payment_provider_authority",
    "payment_requirement_policy",
    "lead_fields",
    "lead_routing_policy",
    "support_fields",
    "support_escalation_policy",
    "handoff_policy",
    "reminder_rules",
    "reminder_templates_channel_policy",
    "review_settings",
    "review_trigger_policy",
    "faq_json",
    "knowledge_base",
    "rag_grounding_policy",
    "public_chat_links",
    "public_chat_security",
    "public_chat_greeting_menu_policy",
    "whatsapp_account",
    "whatsapp_ignored_numbers",
    "channel_behavior_policy",
    "tenant_module_enablement",
    "tool_catalog",
    "plan_limits",
    "tenant_config_limits",
    "feature_flags",
    "subscription_plans",
    "business_type_presets",
    "global_prompts",
    "provider_readiness",
    "audit_logs",
    "impersonation",
    "privacy_ops",
    "active_config_version",
    "session_workflow_state",
    "pending_action_ledger",
    "semantic_language_hint",
    "localization_context",
    "llm_provider_settings",
    "payment_provider_credentials",
    "whatsapp_provider_credentials",
    "otp_captcha_settings",
    "smtp_email_settings",
    "redis_postgres_runtime_settings",
    "cors_public_url_settings",
}


def _keys(domains: tuple[ConfigOwnership, ...]) -> set[str]:
    return {domain.key for domain in domains}


def test_manifest_contains_all_required_domains_and_corrected_names():
    keys = _keys(get_config_manifest())

    assert REQUIRED_DOMAINS <= keys
    assert "pending_action_ledger" in keys
    assert "pending_action_action_ledger" not in keys


def test_every_manifest_entry_has_required_metadata_and_controlled_values():
    keys = set()
    for domain in CONFIG_MANIFEST:
        assert domain.key
        assert domain.key not in keys
        keys.add(domain.key)
        assert domain.owner in OWNER_CATEGORIES
        assert domain.storage
        assert domain.runtime_consumers
        assert isinstance(domain.tenant_editable, bool)
        assert isinstance(domain.super_admin_editable, bool)
        assert isinstance(domain.env_owned, bool)
        assert isinstance(domain.llm_visible, bool)
        assert domain.safety_level in SAFETY_LEVELS
        assert domain.precedence
        assert domain.status in DOMAIN_STATUSES
        assert domain.notes is not None


def test_query_helpers_return_expected_domains_and_reject_unknowns():
    assert get_config_domain("catalog").key == "catalog"
    assert assert_known_config_domain("payment_provider_authority").key == "payment_provider_authority"

    tenant_json_domains = list_config_domains_by_owner("tenant_config_json")
    assert "business_name" in _keys(tenant_json_domains)

    with pytest.raises(KeyError):
        get_config_domain("does_not_exist")

    with pytest.raises(ValueError):
        list_config_domains_by_owner("invalid_owner")


def test_missing_domains_include_known_unimplemented_config_areas():
    missing = _keys(list_missing_config_domains())

    assert {"holidays_closed_days", "staff_resources", "payment_requirement_policy"} <= missing


def test_legacy_or_ignored_domains_include_tenant_config_limits():
    legacy_or_ignored = _keys(list_legacy_or_ignored_config_domains())
    limits = get_config_domain("tenant_config_limits")

    assert "tenant_config_limits" in legacy_or_ignored
    assert limits.owner == "legacy"
    assert limits.status == "ignored"
    assert limits.tenant_editable is False
    assert "subscription_plans.limits_json through UsageGuard" in limits.notes


def test_catalog_manifest_marks_db_and_tenant_json_split_with_capability_menu_consumer():
    catalog = get_config_domain("catalog")

    assert catalog.owner == "domain_db"
    assert "catalog_items" in catalog.storage
    assert "tenant_configs.config_json.modules.order.categories" in catalog.storage
    assert "DB catalog is primary" in catalog.notes
    assert "Tenant JSON order categories remain legacy" in catalog.notes
    assert "capability_menu" in catalog.runtime_consumers
    assert "public chat" in catalog.notes.lower()


def test_behavior_profile_manifest_marks_tenant_override_api_active_and_runtime_deferred():
    preset = get_config_domain("global_business_behavior_preset")
    overrides = get_config_domain("tenant_receptionist_behavior_overrides")
    effective = get_config_domain("effective_behavior_profile")

    assert preset.status == "active"
    assert preset.super_admin_editable is True
    assert overrides.status == "active"
    assert overrides.tenant_editable is True
    assert "/api/v1/config/receptionist-behavior" in overrides.api_routes
    assert effective.status == "active"
    assert "config preview only" in effective.notes
    assert "Phase 3D-5D-4" in effective.notes
    assert "not execution authority" in effective.notes


def test_payment_provider_and_status_authority_are_not_tenant_or_llm_owned():
    provider = get_config_domain("payment_provider_authority")
    status_link = get_config_domain("payment_status_link_authority")

    assert provider.owner == "env_provider"
    assert provider.env_owned is True
    assert provider.tenant_editable is False
    assert provider.llm_visible is False
    assert provider.safety_level == "payment_sensitive"

    assert status_link.tenant_editable is False
    assert status_link.llm_visible is False
    assert "provider webhooks" in status_link.notes


def test_payment_success_status_and_link_authority_are_never_llm_visible():
    sensitive_name_fragments = ("payment_provider_authority", "payment_status_link_authority")

    for key in sensitive_name_fragments:
        assert get_config_domain(key).llm_visible is False


def test_precedence_keeps_safety_policy_and_provider_config_above_llm_extraction():
    assert CONFIG_PRECEDENCE.index("environment_provider_settings") < CONFIG_PRECEDENCE.index("llm_semantic_extraction")
    assert CONFIG_PRECEDENCE.index("backend_safety_invariants") < CONFIG_PRECEDENCE.index("llm_semantic_extraction")
    assert CONFIG_PRECEDENCE.index("super_admin_plan_policy") < CONFIG_PRECEDENCE.index("llm_semantic_extraction")
    assert CONFIG_PRECEDENCE.index("tenant_row_state") < CONFIG_PRECEDENCE.index("llm_semantic_extraction")
    assert CONFIG_PRECEDENCE.index("tool_handler_repository_validation") > CONFIG_PRECEDENCE.index("llm_semantic_extraction")


def test_safety_sensitive_helper_includes_payment_provider_and_plan_limits():
    sensitive = _keys(list_safety_sensitive_config_domains())

    assert "payment_provider_authority" in sensitive
    assert "payment_status_link_authority" in sensitive
    assert "plan_limits" in sensitive
    assert "tenant_module_enablement" in sensitive


def test_invalid_manifest_entry_fails_validation():
    valid = get_config_domain("business_name")

    with pytest.raises(ValueError, match="Invalid config owner"):
        validate_config_domain(replace(valid, owner="bad_owner"))

    with pytest.raises(ValueError, match="Invalid config status"):
        validate_config_domain(replace(valid, status="bad_status"))

    with pytest.raises(ValueError, match="Invalid config safety level"):
        validate_config_domain(replace(valid, safety_level="bad_safety"))

    with pytest.raises(ValueError, match="must declare storage"):
        validate_config_domain(replace(valid, storage=()))


def test_manifest_declares_metadata_only_rule_and_is_not_runtime_imported():
    assert "documentation/testable metadata only" in MANIFEST_RUNTIME_IMPORT_RULE
    assert "Only tests may import it" in MANIFEST_RUNTIME_IMPORT_RULE

    backend_root = Path(__file__).resolve().parents[2]
    offenders = []
    for path in backend_root.rglob("*.py"):
        normalized = path.as_posix()
        if normalized.endswith("core/config_manifest.py") or "/tests/" in normalized:
            continue
        text = path.read_text(encoding="utf-8")
        if "config_manifest" in text:
            offenders.append(path.relative_to(backend_root).as_posix())

    assert offenders == []
