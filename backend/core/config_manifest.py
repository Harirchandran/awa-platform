from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


OWNER_CATEGORIES = frozenset(
    {
        "tenant_config_json",
        "domain_db",
        "super_admin_policy",
        "env_provider",
        "runtime_session",
        "derived_runtime",
        "legacy",
        "missing",
    }
)

SAFETY_LEVELS = frozenset(
    {
        "public_business_data",
        "tenant_business_behavior",
        "customer_pii",
        "protected_action",
        "payment_sensitive",
        "platform_security",
        "runtime_internal",
    }
)

DOMAIN_STATUSES = frozenset(
    {
        "active",
        "partial",
        "legacy",
        "ignored",
        "missing",
        "unsafe_to_configure",
    }
)

CONFIG_PRECEDENCE = (
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

MANIFEST_RUNTIME_IMPORT_RULE = (
    "The manifest is documentation/testable metadata only. No existing runtime "
    "loader, config loader, route, worker, webhook, tool handler, workflow, "
    "prompt builder, localization helper, or payment/provider code should import "
    "it. Only tests may import it in Phase 3D-5A-2."
)


@dataclass(frozen=True)
class ConfigOwnership:
    key: str
    owner: str
    storage: tuple[str, ...]
    api_routes: tuple[str, ...]
    frontend_surfaces: tuple[str, ...]
    runtime_consumers: tuple[str, ...]
    tenant_editable: bool
    super_admin_editable: bool
    env_owned: bool
    llm_visible: bool
    safety_level: str
    precedence: str
    status: str
    notes: str


def _t(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(values)


def _domain(
    key: str,
    owner: str,
    storage: Iterable[str],
    runtime_consumers: Iterable[str],
    *,
    api_routes: Iterable[str] = (),
    frontend_surfaces: Iterable[str] = (),
    tenant_editable: bool = False,
    super_admin_editable: bool = False,
    env_owned: bool = False,
    llm_visible: bool = False,
    safety_level: str = "tenant_business_behavior",
    precedence: str = "active_tenant_config_version",
    status: str = "active",
    notes: str = "",
) -> ConfigOwnership:
    entry = ConfigOwnership(
        key=key,
        owner=owner,
        storage=_t(storage),
        api_routes=_t(api_routes),
        frontend_surfaces=_t(frontend_surfaces),
        runtime_consumers=_t(runtime_consumers),
        tenant_editable=tenant_editable,
        super_admin_editable=super_admin_editable,
        env_owned=env_owned,
        llm_visible=llm_visible,
        safety_level=safety_level,
        precedence=precedence,
        status=status,
        notes=notes,
    )
    return validate_config_domain(entry)


def validate_config_domain(domain: ConfigOwnership) -> ConfigOwnership:
    if domain.owner not in OWNER_CATEGORIES:
        raise ValueError(f"Invalid config owner: {domain.owner}")
    if domain.safety_level not in SAFETY_LEVELS:
        raise ValueError(f"Invalid config safety level: {domain.safety_level}")
    if domain.status not in DOMAIN_STATUSES:
        raise ValueError(f"Invalid config status: {domain.status}")
    if not domain.key.strip():
        raise ValueError("Config domain key is required")
    if not domain.storage:
        raise ValueError(f"Config domain {domain.key} must declare storage")
    if not domain.runtime_consumers:
        raise ValueError(f"Config domain {domain.key} must declare runtime consumers")
    if not domain.precedence.strip():
        raise ValueError(f"Config domain {domain.key} must declare precedence")
    return domain


CONFIG_MANIFEST = (
    # Tenant/business identity.
    _domain("business_name", "tenant_config_json", ("tenant_configs.config_json.business_name", "tenants.name"), ("prompt_builder", "capability_menu", "public_chat_start"), api_routes=("/api/v1/config",), frontend_surfaces=("ConfigPage", "OnboardingPage"), tenant_editable=True, llm_visible=True, safety_level="public_business_data", notes="Prompt builder falls back to tenant row name when config value is missing."),
    _domain("business_type", "tenant_config_json", ("tenant_configs.config_json.business_type", "business_types.code"), ("prompt_builder", "tool_control_state", "default_config_builder"), api_routes=("/api/v1/config", "/admin/business-types/*"), frontend_surfaces=("ConfigPage", "ToolControlsPage"), tenant_editable=True, super_admin_editable=True, llm_visible=True, safety_level="public_business_data", notes="Tenant config stores selected type; super-admin business_type presets seed enabled modules."),
    _domain("global_business_behavior_preset", "super_admin_policy", ("business_behavior_presets.profile_json", "business_behavior_presets.tenant_editable_fields", "backend.schemas.behavior_profile.BehaviorProfileDefinition"), ("behavior_profile_resolver",), api_routes=("/api/v1/admin/business-behavior-presets/*",), super_admin_editable=True, llm_visible=False, safety_level="tenant_business_behavior", precedence="backend_safety_invariants", status="active", notes="Super-admin-owned versioned presentation behavior preset storage. Not execution authority; RuntimeCapabilitySnapshot, entitlements, provider config, and backend safety invariants remain higher authority. Runtime code does not fetch presets in Phase 3D-5D-2."),
    _domain("tenant_receptionist_behavior_overrides", "tenant_config_json", ("tenant_configs.config_json.receptionist_behavior",), ("behavior_profile_resolver",), api_routes=("/api/v1/config/receptionist-behavior",), frontend_surfaces=("ConfigPage Receptionist tab",), tenant_editable=True, llm_visible=False, safety_level="tenant_business_behavior", status="active", precedence="active_tenant_config_version", notes="Tenant override API/UI is active for dashboard configuration only. Overrides are limited to allowlisted behavior fields declared editable by the super-admin preset. Custom instructions remain separate and lower authority."),
    _domain("effective_behavior_profile", "derived_runtime", ("business_behavior_presets active version", "tenant_configs.config_json.receptionist_behavior", "RuntimeCapabilitySnapshot"), ("behavior_profile_resolver", "config_preview_api"), api_routes=("/api/v1/config/receptionist-behavior",), frontend_surfaces=("ConfigPage Receptionist tab",), tenant_editable=False, super_admin_editable=False, llm_visible=False, safety_level="tenant_business_behavior", status="active", precedence="super_admin_plan_policy", notes="Derived immutable behavior profile is active for config preview only. Runtime prompt/menu/NBA/public-chat integration is deferred to Phase 3D-5D-4. RuntimeCapabilitySnapshot, entitlements, provider config, and backend safety invariants remain higher authority; this is presentation/prioritization policy, not execution authority."),
    _domain("language_locale", "tenant_config_json", ("tenant_configs.config_json.default_language", "tenant_configs.config_json.timezone"), ("prompt_builder", "booking_date_parser", "response_localization"), api_routes=("/api/v1/config",), frontend_surfaces=("ConfigPage",), tenant_editable=True, llm_visible=True, notes="Localization also uses current-turn detected language when available."),
    _domain("tone_persona", "tenant_config_json", ("tenant_configs.config_json.ai_tone", "tenant_configs.config_json.custom_instructions", "tenant_configs.config_json.ai_mode"), ("prompt_builder", "UsageGuard.resolve_ai_mode"), api_routes=("/api/v1/config",), frontend_surfaces=("AISettingsPage", "ConfigPage"), tenant_editable=True, llm_visible=True, notes="Custom instructions are lower-authority untrusted tenant preferences."),
    _domain("greeting_fallback_escalation", "tenant_config_json", ("tenant_configs.config_json.greeting_message", "tenant_configs.config_json.fallback_message", "tenant_configs.config_json.escalation_message"), ("prompt_builder", "capability_menu", "support_handoff_flows"), api_routes=("/api/v1/config",), frontend_surfaces=("ConfigPage",), tenant_editable=True, llm_visible=True, notes="Runtime response layer must still preserve no-fake-success and escalation truth."),

    # Operating schedule.
    _domain("working_hours", "tenant_config_json", ("tenant_configs.config_json.working_hours",), ("booking_rule_validation", "capability_menu", "prompt_business_context"), api_routes=("/api/v1/config",), frontend_surfaces=("ConfigPage", "BookingsPage"), tenant_editable=True, llm_visible=True, safety_level="public_business_data"),
    _domain("holidays_closed_days", "missing", ("not_implemented",), ("none_not_implemented",), tenant_editable=False, status="missing", notes="No separate holiday/closed-day config source was verified; working_hours handles weekly closed days only."),

    # Booking.
    _domain("services", "tenant_config_json", ("tenant_configs.config_json.services",), ("booking_tool_handler", "capability_menu", "prompt_business_context"), api_routes=("/api/v1/config",), frontend_surfaces=("ConfigPage", "BookingsPage"), tenant_editable=True, llm_visible=True, safety_level="public_business_data"),
    _domain("booking_fields", "tenant_config_json", ("tenant_configs.config_json.modules.booking.fields", "business_types.enabled_modules"), ("booking_tool_schema", "prompt_module_field_context"), api_routes=("/api/v1/config", "/admin/tool-controls"), frontend_surfaces=("ConfigPage", "ToolControlsPage"), tenant_editable=True, super_admin_editable=True, llm_visible=True),
    _domain("booking_rules", "tenant_config_json", ("tenant_configs.config_json.booking_settings",), ("booking_rule_validation", "booking_tool_handler"), api_routes=("/api/v1/config",), frontend_surfaces=("BookingsPage",), tenant_editable=True, llm_visible=True, safety_level="protected_action"),
    _domain("booking_availability", "derived_runtime", ("tenant_configs.config_json.working_hours", "tenant_configs.config_json.booking_settings", "bookings"), ("booking_rule_validation", "booking_service"), api_routes=("/api/v1/bookings/*",), frontend_surfaces=("BookingsPage",), tenant_editable=True, llm_visible=True, safety_level="protected_action", precedence="tool_handler_repository_validation", notes="Availability is derived at execution time from config and persisted bookings."),
    _domain("staff_resources", "missing", ("not_implemented",), ("none_not_implemented",), tenant_editable=False, status="missing", notes="No staff/resource table or tenant config field was verified."),

    # Order/catalog.
    _domain("catalog", "domain_db", ("catalog_categories", "catalog_items", "catalog_item_variants", "inventory_events", "tenant_configs.config_json.modules.order.categories"), ("order_workflow", "catalog_search", "prompt_business_context", "capability_menu"), api_routes=("/api/v1/catalog/*", "/api/v1/public-chat/*"), frontend_surfaces=("CatalogPage", "OrdersPage"), tenant_editable=True, llm_visible=True, safety_level="public_business_data", precedence="domain_db_state", notes="DB catalog is primary for catalog service/search, order workflow, and public chat. Tenant JSON order categories remain legacy/onboarding/fallback."),
    _domain("order_settings", "tenant_config_json", ("tenant_configs.config_json.modules.order",), ("order_tool_schema", "order_tool_handler"), api_routes=("/api/v1/config",), frontend_surfaces=("OrdersPage", "ConfigPage"), tenant_editable=True, llm_visible=True, safety_level="protected_action"),
    _domain("fulfillment_options", "tenant_config_json", ("tenant_configs.config_json.modules.order.fulfillment_types",), ("order_tool_handler", "order_module_validation"), api_routes=("/api/v1/config",), frontend_surfaces=("OrdersPage",), tenant_editable=True, llm_visible=True),
    _domain("order_payment_methods", "tenant_config_json", ("tenant_configs.config_json.modules.order.payment_methods",), ("order_tool_handler",), api_routes=("/api/v1/config",), frontend_surfaces=("OrdersPage",), tenant_editable=True, llm_visible=True, safety_level="payment_sensitive"),
    _domain("price_currency_tax_delivery", "tenant_config_json", ("tenant_configs.config_json.modules.order.currency", "tenant_configs.config_json.modules.order.tax_amount", "tenant_configs.config_json.modules.order.delivery_fee", "catalog_items.price", "catalog_items.currency"), ("order_tool_handler", "prompt_catalog_context"), api_routes=("/api/v1/config", "/api/v1/catalog/*"), frontend_surfaces=("OrdersPage", "CatalogPage"), tenant_editable=True, llm_visible=True, safety_level="public_business_data", precedence="domain_db_state", notes="DB item price/currency wins when a DB catalog item is resolved."),

    # Payment.
    _domain("payment_module_settings", "tenant_config_json", ("tenant_configs.config_json.modules.payments",), ("payments_tool_handler", "CapabilityGateway"), api_routes=("/api/v1/config",), frontend_surfaces=("PaymentsPage", "ConfigPage"), tenant_editable=True, llm_visible=False, safety_level="payment_sensitive", notes="Provider-like fields are treated as safety-sensitive and must not be customer or LLM supplied."),
    _domain("payment_provider_authority", "env_provider", ("backend.core.config.Settings.RAZORPAY_*", "tenant_configs.config_json.modules.payments.provider", "tenant_configs.config_json.modules.payments.use_platform_razorpay"), ("payments_tool_handler", "razorpay_webhook", "payment_provider_client"), tenant_editable=False, env_owned=True, llm_visible=False, safety_level="payment_sensitive", precedence="environment_provider_settings", notes="Backend/env credentials and provider validation own payment provider authority. Current code reads module provider config, so tenant UI ownership must remain restricted; LLM/customer cannot set provider, payment status, payment success, or link authority."),
    _domain("payment_requirement_policy", "missing", ("not_implemented",), ("none_not_implemented",), tenant_editable=False, llm_visible=False, safety_level="payment_sensitive", status="missing", notes="No required-deposit/payment-before-booking policy source was verified."),
    _domain("payment_status_link_authority", "domain_db", ("payment_requests", "payment_events", "razorpay webhook payload after sanitization"), ("payments_tool_handler", "razorpay_webhook", "payment_service"), api_routes=("/api/v1/payments/*", "/razorpay"), frontend_surfaces=("PaymentsPage",), tenant_editable=False, llm_visible=False, safety_level="payment_sensitive", precedence="backend_safety_invariants", notes="Only backend services and provider webhooks may create links or mark payment status/success."),

    # Lead/support.
    _domain("lead_fields", "tenant_config_json", ("tenant_configs.config_json.modules.lead_capture.fields",), ("lead_capture_tool_schema", "lead_capture_tool_handler"), api_routes=("/api/v1/config",), frontend_surfaces=("ConfigPage", "LeadsPage"), tenant_editable=True, llm_visible=True, safety_level="customer_pii"),
    _domain("lead_routing_policy", "tenant_config_json", ("tenant_configs.config_json.modules.lead_capture",), ("lead_capture_tool_handler",), api_routes=("/api/v1/config",), frontend_surfaces=("LeadsPage",), tenant_editable=True, llm_visible=True, safety_level="customer_pii", status="partial", notes="Detailed routing policy beyond module fields was not verified."),
    _domain("support_fields", "tenant_config_json", ("tenant_configs.config_json.modules.support.fields",), ("support_tool_schema", "support_tool_handler"), api_routes=("/api/v1/config",), frontend_surfaces=("ConfigPage", "EscalationsPage"), tenant_editable=True, llm_visible=True, safety_level="customer_pii"),
    _domain("support_escalation_policy", "tenant_config_json", ("tenant_configs.config_json.escalation_phone", "tenant_configs.config_json.escalation_email", "tenant_configs.config_json.support_notifications"), ("support_tool_handler", "notification_routing"), api_routes=("/api/v1/config",), frontend_surfaces=("EscalationsPage", "ConfigPage"), tenant_editable=True, llm_visible=True, safety_level="customer_pii", status="partial", notes="External webhook notification config is validated for production URL safety when present."),
    _domain("handoff_policy", "tenant_config_json", ("tenant_configs.config_json.modules.human_handoff", "conversations.status", "conversation_sessions.state_json"), ("human_handoff_tool_handler", "support_return_to_awa"), api_routes=("/api/v1/config", "/api/v1/public-chat/sessions/*/cancel-handoff"), frontend_surfaces=("EscalationsPage", "ConversationsPage"), tenant_editable=True, llm_visible=True, safety_level="protected_action"),

    # Reminder/review.
    _domain("reminder_rules", "domain_db", ("reminder_rules", "scheduled_messages"), ("reminder_tool_handler", "event_handlers", "reminder_scheduler"), api_routes=("/api/v1/reminders/*",), frontend_surfaces=("RemindersPage",), tenant_editable=True, llm_visible=True, safety_level="customer_pii", precedence="domain_db_state"),
    _domain("reminder_templates_channel_policy", "domain_db", ("reminder_rules.template_text", "reminder_rules.channel", "tenant_configs.config_json.modules.reminders.whatsapp_templates"), ("reminder_tool_handler", "reminder_scheduler"), api_routes=("/api/v1/reminders/*", "/api/v1/config"), frontend_surfaces=("RemindersPage",), tenant_editable=True, llm_visible=True, safety_level="customer_pii", precedence="domain_db_state"),
    _domain("review_settings", "domain_db", ("review_settings",), ("review_tool_handler", "prompt_builder", "review_service"), api_routes=("/api/v1/reviews/settings",), frontend_surfaces=("ReviewsPage",), tenant_editable=True, llm_visible=True, safety_level="tenant_business_behavior", precedence="domain_db_state"),
    _domain("review_trigger_policy", "domain_db", ("review_settings", "review_requests", "scheduled_messages.review_request_id"), ("review_tool_handler", "event_handlers", "review_service"), api_routes=("/api/v1/reviews/*",), frontend_surfaces=("ReviewsPage",), tenant_editable=True, llm_visible=True, safety_level="customer_pii", precedence="domain_db_state"),

    # Knowledge/RAG.
    _domain("faq_json", "tenant_config_json", ("tenant_configs.config_json.faqs", "knowledge_entries synced from FAQ"), ("faq_tool_handler", "knowledge_ingestion", "prompt_business_context"), api_routes=("/api/v1/config",), frontend_surfaces=("ConfigPage", "KnowledgePage"), tenant_editable=True, llm_visible=True, safety_level="public_business_data"),
    _domain("knowledge_base", "domain_db", ("knowledge_entries", "knowledge_chunks", "rag_embeddings"), ("knowledge_orchestrator", "rag_service"), api_routes=("/api/v1/knowledge/*",), frontend_surfaces=("KnowledgePage",), tenant_editable=True, llm_visible=True, safety_level="public_business_data", precedence="domain_db_state"),
    _domain("rag_grounding_policy", "derived_runtime", ("backend.logic.engine.prompt.BUSINESS_GROUNDING_DIRECTIVE", "backend.knowledge.orchestrator"), ("prompt_builder", "knowledge_orchestrator"), llm_visible=True, safety_level="platform_security", precedence="backend_safety_invariants", notes="Grounding rules place live tenant context above retrieved knowledge/RAG."),
    _domain("llm_context_capability_filtering", "derived_runtime", ("tenant_configs.config_json.modules.*.enabled", "subscription_plans.limits_json.allowed_modules", "tenants.status"), ("runtime_capabilities", "prompt_builder", "agentic_engine", "capability_menu", "next_best_action"), tenant_editable=False, super_admin_editable=True, llm_visible=True, safety_level="platform_security", precedence="super_admin_plan_policy", notes="LLM-visible workflow summaries, live catalog/service/FAQ snippets, deterministic menus, quick replies, and NBA suggestions are filtered through the backend runtime capability snapshot. Feature flags and tenant_config.limits are not runtime authority."),

    # Channels.
    _domain("public_chat_links", "domain_db", ("public_chat_links", "public_chat_sessions"), ("public_chat_routes",), api_routes=("/api/v1/awa-chat/*", "/api/v1/public-chat/*"), frontend_surfaces=("AwaChatPage", "PublicChatPage"), tenant_editable=True, llm_visible=False, safety_level="platform_security", precedence="domain_db_state"),
    _domain("public_chat_security", "env_provider", ("backend.core.config.Settings.PUBLIC_CHAT_*", "tenant_configs.config_json.security", "redis public-chat otp keys"), ("public_chat_routes", "CapabilityGateway"), api_routes=("/api/v1/public-chat/*",), frontend_surfaces=("PublicChatPage",), tenant_editable=True, env_owned=True, llm_visible=False, safety_level="platform_security", precedence="environment_provider_settings", notes="CAPTCHA/OTP provider mode and secrets are environment-owned; tenant config can request security requirements."),
    _domain("public_chat_greeting_menu_policy", "derived_runtime", ("tenant_configs.config_json", "subscription_plans.limits_json.allowed_modules", "tenants.status", "public_chat_links.display_name"), ("public_chat_start", "runtime_capabilities", "capability_menu"), api_routes=("/api/v1/public-chat/*"), frontend_surfaces=("PublicChatPage",), tenant_editable=True, llm_visible=True, safety_level="public_business_data", status="active", precedence="super_admin_plan_policy", notes="Public chat start filters menus through the backend runtime capability snapshot; order catalog is hydrated with DB catalog data only when order is available."),
    _domain("whatsapp_account", "domain_db", ("whatsapp_accounts",), ("whatsapp_webhook_router", "whatsapp_client", "reminder_scheduler"), api_routes=("/api/v1/whatsapp-accounts/*", "/webhook"), frontend_surfaces=("WhatsAppPage",), tenant_editable=True, llm_visible=False, safety_level="platform_security", precedence="domain_db_state"),
    _domain("whatsapp_ignored_numbers", "tenant_config_json", ("tenant_configs.config_json.ignored_numbers",), ("whatsapp_webhook_blocklist",), api_routes=("/api/v1/config",), frontend_surfaces=("WhatsAppPage", "ConfigPage"), tenant_editable=True, llm_visible=False, safety_level="customer_pii"),
    _domain("channel_behavior_policy", "derived_runtime", ("backend.logic.engine.prompt.format_channel_context", "backend.pipeline.channel_output"), ("prompt_builder", "whatsapp_client", "public_chat_routes"), llm_visible=True, safety_level="platform_security", precedence="backend_safety_invariants"),

    # Tools/entitlements.
    _domain("tenant_module_enablement", "tenant_config_json", ("tenant_configs.config_json.modules.*.enabled",), ("UsageGuard", "CapabilityGateway", "ToolRegistry", "runtime_capabilities", "tool_control_state"), api_routes=("/api/v1/tools", "/admin/tool-controls", "/api/v1/config"), frontend_surfaces=("ToolsPage", "ToolControlsPage", "ConfigPage"), tenant_editable=True, super_admin_editable=True, llm_visible=True, safety_level="protected_action"),
    _domain("tool_catalog", "super_admin_policy", ("tool_catalog", "backend.services.tool_controls.DEFAULT_TOOL_DEFINITIONS"), ("ToolRegistry", "tool_control_state", "validate_plan_limits"), api_routes=("/admin/tool-controls", "/api/v1/tools"), frontend_surfaces=("ToolsPage", "ToolControlsPage"), super_admin_editable=True, llm_visible=True, safety_level="protected_action", precedence="super_admin_plan_policy"),
    _domain("plan_limits", "super_admin_policy", ("subscription_plans.limits_json", "backend.billing.usage_guard.DEFAULT_PLAN_POLICIES"), ("UsageGuard", "config_routes", "admin_plan_routes"), api_routes=("/admin/plans/*", "/api/v1/config", "/api/v1/tools"), frontend_surfaces=("BillingPage", "ToolControlsPage"), super_admin_editable=True, llm_visible=False, safety_level="platform_security", precedence="super_admin_plan_policy"),
    _domain("tenant_config_limits", "legacy", ("tenant_configs.config_json.limits", "tenant_configs.config_json.usage_limits"), ("none_verified",), api_routes=("/api/v1/config",), frontend_surfaces=("ConfigPage",), tenant_editable=False, llm_visible=False, safety_level="platform_security", status="ignored", notes="Runtime enforcement comes from subscription_plans.limits_json through UsageGuard, not tenant_config.limits. Do not remove tenant_config.limits in this phase."),
    _domain("feature_flags", "domain_db", ("tenant_feature_flags",), ("needs_verification",), api_routes=("/api/v1/feature-flags/*",), frontend_surfaces=("FlagsPage",), tenant_editable=True, llm_visible=False, safety_level="platform_security", status="partial", notes="Routes persist arbitrary tenant flags. backend.core.feature_flag_registry documents the current API/UI-only surface; runtime consumers remain unverified, so this domain stays partial."),

    # Super-admin/platform.
    _domain("subscription_plans", "super_admin_policy", ("subscription_plans", "tenants.plan_code", "tenants.status"), ("UsageGuard", "subscription_routes", "admin_billing_routes"), api_routes=("/admin/plans/*", "/admin/billing/*", "/api/v1/subscriptions/*"), frontend_surfaces=("PricingPage", "BillingPage"), super_admin_editable=True, llm_visible=False, safety_level="platform_security", precedence="super_admin_plan_policy"),
    _domain("business_type_presets", "super_admin_policy", ("business_types", "backend.schemas.config.BUSINESS_TYPE_PROFILES"), ("default_config_builder", "tool_control_state"), api_routes=("/admin/business-types/*", "/admin/tool-controls"), frontend_surfaces=("ToolControlsPage", "TenantsPage"), super_admin_editable=True, llm_visible=True, safety_level="tenant_business_behavior", precedence="super_admin_plan_policy"),
    _domain("global_prompts", "super_admin_policy", ("prompt_registry", "backend.logic.engine.prompt"), ("prompt_routes", "prompt_builder"), api_routes=("/api/v1/prompts/*",), frontend_surfaces=("PromptsPage",), super_admin_editable=True, llm_visible=True, safety_level="platform_security", precedence="backend_safety_invariants"),
    _domain("provider_readiness", "env_provider", ("backend.core.config.Settings",), ("settings_validation", "provider_factory", "health_routes"), env_owned=True, llm_visible=False, safety_level="platform_security", precedence="environment_provider_settings"),
    _domain("audit_logs", "domain_db", ("audit_logs",), ("admin_audit_routes", "privacy_reaper", "service_audit_writes"), api_routes=("/admin/audit-logs", "/api/v1/audit-logs"), frontend_surfaces=("AuditPage",), tenant_editable=False, super_admin_editable=True, llm_visible=False, safety_level="platform_security", precedence="backend_safety_invariants"),
    _domain("impersonation", "super_admin_policy", ("users", "user_tenants", "audit_logs", "JWT impersonation token claims"), ("admin_impersonation_route", "auth_dependencies"), api_routes=("/admin/impersonate/*",), frontend_surfaces=("TenantsPage",), super_admin_editable=True, llm_visible=False, safety_level="platform_security", precedence="backend_safety_invariants"),
    _domain("privacy_ops", "domain_db", ("customers.privacy_erased", "privacy_erasure_requests", "retention_policies", "audit_logs"), ("privacy_routes", "privacy_reaper", "customer_repository"), api_routes=("/api/v1/privacy/*",), frontend_surfaces=("PrivacyPage",), tenant_editable=False, super_admin_editable=True, llm_visible=False, safety_level="customer_pii", precedence="backend_safety_invariants"),

    # Runtime/session.
    _domain("active_config_version", "runtime_session", ("tenant_configs.version", "tenant_configs.is_active", "canonical_messages.config_version", "conversation_sessions.config_version"), ("ConfigLoader", "whatsapp_webhook", "public_chat_routes", "session_manager"), api_routes=("/api/v1/config", "/webhook", "/api/v1/public-chat/*"), tenant_editable=False, llm_visible=False, safety_level="runtime_internal", precedence="active_tenant_config_version", notes="WhatsApp pins config_version at ingress; public chat also reads current active config around session/message creation, so version behavior is partial and needs a later decision."),
    _domain("session_workflow_state", "runtime_session", ("conversation_sessions.state_json", "conversation_sessions.current_module", "conversation_sessions.current_intent"), ("agentic_engine", "ToolRegistry", "session_manager", "public_chat_otp_gate"), tenant_editable=False, llm_visible=True, safety_level="runtime_internal", precedence="runtime_session_workflow_state"),
    _domain("pending_action_ledger", "runtime_session", ("conversation_sessions.state_json.action_ledger.pending_action",), ("CapabilityGateway", "ToolRegistry", "public_chat_otp_gate", "agentic_engine"), tenant_editable=False, llm_visible=True, safety_level="protected_action", precedence="backend_safety_invariants", notes="Backend-owned confirmation/verification ledger; do not use the obsolete pending_action_action_ledger name."),
    _domain("semantic_language_hint", "derived_runtime", ("current-turn localization context", "response_localization note_detected_customer_language"), ("response_localization", "semantic_field_extraction"), tenant_editable=False, llm_visible=True, safety_level="runtime_internal", precedence="llm_semantic_extraction", status="partial", notes="Ephemeral current-turn hint; not a persisted tenant config source."),
    _domain("localization_context", "derived_runtime", ("tenant_configs.config_json.default_language", "current-turn detected language", "channel context"), ("response_localization", "prompt_builder"), tenant_editable=False, llm_visible=True, safety_level="runtime_internal", precedence="llm_semantic_extraction", status="partial", notes="Derived per response; must not weaken active workflow state preservation."),

    # Environment/provider-owned.
    _domain("llm_provider_settings", "env_provider", ("backend.core.config.Settings.ROUTING_LLM_*", "backend.core.config.Settings.AGENT_LLM_*", "backend.core.config.Settings.*_API_KEY"), ("LLMFactory", "provider_rate_limiter", "agentic_engine"), env_owned=True, llm_visible=False, safety_level="platform_security", precedence="environment_provider_settings"),
    _domain("payment_provider_credentials", "env_provider", ("backend.core.config.Settings.RAZORPAY_*",), ("payments_tool_handler", "razorpay_webhook", "subscription_routes"), env_owned=True, llm_visible=False, safety_level="payment_sensitive", precedence="environment_provider_settings"),
    _domain("whatsapp_provider_credentials", "env_provider", ("backend.core.config.Settings.WHATSAPP_*", "backend.core.config.Settings.FACEBOOK_*", "whatsapp_accounts.access_token encrypted"), ("whatsapp_webhook", "whatsapp_client", "whatsapp_accounts_routes"), env_owned=True, llm_visible=False, safety_level="platform_security", precedence="environment_provider_settings"),
    _domain("otp_captcha_settings", "env_provider", ("backend.core.config.Settings.CUSTOMER_OTP_*", "backend.core.config.Settings.PUBLIC_CHAT_CAPTCHA_*", "redis otp keys"), ("public_chat_routes", "settings_validation"), env_owned=True, llm_visible=False, safety_level="platform_security", precedence="environment_provider_settings"),
    _domain("smtp_email_settings", "env_provider", ("not_configured", "support notification webhook settings"), ("notification_routing", "reminder_scheduler"), env_owned=True, llm_visible=False, safety_level="platform_security", status="partial", notes="SMTP-specific settings were not verified; support notification webhook settings exist."),
    _domain("redis_postgres_runtime_settings", "env_provider", ("backend.core.config.Settings.REDIS_URL", "backend.core.config.Settings.DATABASE_URL"), ("db_client", "redis_client", "queues", "session_locks", "rate_limiters"), env_owned=True, llm_visible=False, safety_level="platform_security", precedence="environment_provider_settings"),
    _domain("cors_public_url_settings", "env_provider", ("backend.core.config.Settings.CORS_ORIGINS", "backend.core.config.Settings.AWA_PUBLIC_CHAT_BASE_URL", "backend.core.config.Settings.PUBLIC_CHAT_LINK_SECRET", "backend.core.config.Settings.PUBLIC_CHAT_SESSION_SECRET"), ("settings_validation", "public_chat_routes", "api_app"), env_owned=True, llm_visible=False, safety_level="platform_security", precedence="environment_provider_settings"),
)

_MANIFEST_BY_KEY = {domain.key: domain for domain in CONFIG_MANIFEST}


def get_config_manifest() -> tuple[ConfigOwnership, ...]:
    return CONFIG_MANIFEST


def get_config_domain(key: str) -> ConfigOwnership:
    try:
        return _MANIFEST_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"Unknown config domain: {key}") from exc


def assert_known_config_domain(key: str) -> ConfigOwnership:
    return get_config_domain(key)


def list_config_domains_by_owner(owner: str) -> tuple[ConfigOwnership, ...]:
    if owner not in OWNER_CATEGORIES:
        raise ValueError(f"Invalid config owner: {owner}")
    return tuple(domain for domain in CONFIG_MANIFEST if domain.owner == owner)


def list_missing_config_domains() -> tuple[ConfigOwnership, ...]:
    return tuple(domain for domain in CONFIG_MANIFEST if domain.status == "missing" or domain.owner == "missing")


def list_legacy_or_ignored_config_domains() -> tuple[ConfigOwnership, ...]:
    return tuple(domain for domain in CONFIG_MANIFEST if domain.status in {"legacy", "ignored"} or domain.owner == "legacy")


def list_safety_sensitive_config_domains() -> tuple[ConfigOwnership, ...]:
    sensitive = {"customer_pii", "protected_action", "payment_sensitive", "platform_security"}
    return tuple(domain for domain in CONFIG_MANIFEST if domain.safety_level in sensitive)


for _domain_entry in CONFIG_MANIFEST:
    validate_config_domain(_domain_entry)
if len(_MANIFEST_BY_KEY) != len(CONFIG_MANIFEST):
    raise ValueError("Config manifest contains duplicate keys")
