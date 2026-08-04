from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from backend.core.ai_modes import normalize_ai_mode
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
PROMPT_INJECTION_PATTERNS = (
    re.compile(r"\b(ignore|forget|disregard|override)\s+(all\s+)?(previous|prior|above|system|developer)\s+instructions\b", re.IGNORECASE),
    re.compile(r"\b(reveal|show|print|leak|dump)\s+(the\s+)?(system|developer)\s+(prompt|message|instructions)\b", re.IGNORECASE),
    re.compile(r"(^|\n)\s*(system|developer|assistant|tool)\s*:", re.IGNORECASE),
    re.compile(r"\b(jailbreak|prompt injection|dan mode|bypass safety|bypass policy)\b", re.IGNORECASE),
    re.compile(r"\b(exfiltrate|steal)\s+(data|secrets|api keys|tokens)\b", re.IGNORECASE),
)
BUSINESS_TYPE_PROFILES: Dict[str, Dict[str, Any]] = {
    "clinic": {
        "label": "Clinic / Healthcare",
        "prompt_guidance": "Prioritize appointments, patient contact details, visit reason, and human handoff for urgent symptoms. Do not provide medical diagnosis.",
        "recommended_setup": ["Add services with duration", "Add clinic hours", "Add escalation phone"],
        "booking_fields": [
            {"key": "patient_name", "label": "Patient Name", "type": "string", "required": True},
            {"key": "service_id", "label": "Service", "type": "select", "options_from": "services", "required": True},
            {"key": "preferred_date", "label": "Preferred Date", "type": "date", "required": True},
            {"key": "preferred_time", "label": "Preferred Time", "type": "time", "required": True},
            {"key": "phone", "label": "Contact Number", "type": "phone", "required": True},
        ],
    },
    "salon": {
        "label": "Salon / Beauty Studio",
        "prompt_guidance": "Prioritize service, stylist or preference if provided, appointment date/time, and reminders. Mention product sales only when configured.",
        "recommended_setup": ["Add service menu", "Add booking buffers", "Add review link"],
    },
    "restaurant": {
        "label": "Restaurant / Cafe",
        "prompt_guidance": "Prioritize menu availability, item quantities, pickup or delivery, delivery address when needed, and honest payment status.",
        "recommended_setup": ["Add catalog items", "Set delivery/pickup rules", "Configure payment options"],
    },
    "retail": {
        "label": "Retail / Store",
        "prompt_guidance": "Prioritize product availability, variants only when configured, quantities, fulfillment method, and stock-safe order creation.",
        "recommended_setup": ["Add catalog inventory", "Set pickup/delivery options", "Configure payment options"],
    },
    "services": {
        "label": "Local Services",
        "prompt_guidance": "Prioritize service need, location or contact details, appointment timing, and human follow-up for complex jobs.",
        "recommended_setup": ["Add services", "Add service-area notes", "Add handoff contact"],
    },
    "education": {
        "label": "Education / Coaching",
        "prompt_guidance": "Prioritize inquiries, trial bookings, course or batch details, and lead capture before handoff.",
        "recommended_setup": ["Add programs or services", "Add FAQs", "Add lead capture fields"],
    },
    "minimal": {
        "label": "Minimal Assistant",
        "prompt_guidance": "Answer FAQs and route complex requests to a human. Avoid committing bookings, orders, payments, reminders, or reviews unless tools are enabled later.",
        "recommended_setup": ["Add FAQs", "Add handoff contact"],
    },
}
class DaySchedule(BaseModel):
    is_open: bool = False
    open_time: str = "09:00"
    close_time: str = "17:00"
    @field_validator("open_time", "close_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        cleaned = _clean_text(value, max_length=5, default="09:00")
        if not TIME_PATTERN.match(cleaned):
            raise ValueError("Time must be in HH:MM format")
        return cleaned
class ModuleField(BaseModel):
    model_config = ConfigDict(extra="allow")
    key: str = Field(min_length=1, max_length=64)
    label: Optional[str] = Field(default=None, max_length=120)
    type: str = "string"
    required: bool = True
    options: Optional[List[str]] = None
    options_from: Optional[str] = None
    applies_when: Optional[str] = Field(default=None, max_length=160)
    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        cleaned = _clean_text(value, max_length=64)
        if not re.match(r"^[a-zA-Z0-9_]+$", cleaned):
            raise ValueError("Field keys may contain only letters, numbers, and underscores")
        return cleaned
    @field_validator("type")
    @classmethod
    def validate_field_type(cls, value: str) -> str:
        cleaned = _clean_text(value, max_length=32, default="string")
        allowed = {"string", "number", "date", "time", "email", "phone", "select"}
        if cleaned not in allowed:
            raise ValueError("Unsupported field type")
        return cleaned
class ModuleConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = False
    fields: List[ModuleField] = Field(default_factory=list)
class RemindersModuleConfig(ModuleConfig):
    """Extended config for the reminders module.

    ``whatsapp_templates`` maps a ``trigger_type`` string (e.g. ``"booking_reminder"``)
    to the Meta-approved WhatsApp template name that must be used when the
    conversation is outside the active 24-hour messaging window.
    """
    whatsapp_templates: Dict[str, str] = Field(default_factory=dict)
class BookingSettings(BaseModel):
    confirmation_mode: Optional[str] = None
    allow_customer_reschedule: Optional[bool] = None
    allow_customer_cancel: Optional[bool] = None
    minimum_notice_minutes: Optional[int] = Field(default=None, ge=0, le=43200)
    advance_booking_days: Optional[int] = Field(default=None, ge=1, le=730)
    slot_interval_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    buffer_before_mins: Optional[int] = Field(default=None, ge=0, le=1440)
    buffer_after_mins: Optional[int] = Field(default=None, ge=0, le=1440)
    max_bookings_per_day: Optional[int] = Field(default=None, ge=1, le=10000)
    cancellation_cutoff_minutes: Optional[int] = Field(default=None, ge=0, le=43200)
    reminder_offsets_minutes: List[int] = Field(default_factory=list, max_length=10)
    approval_required_for: List[str] = Field(default_factory=list, max_length=30)
    @field_validator("confirmation_mode")
    @classmethod
    def validate_confirmation_mode(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = _clean_text(value, max_length=32)
        if cleaned not in {"auto_confirm", "approval_required"}:
            raise ValueError("Confirmation mode must be auto_confirm or approval_required")
        return cleaned
    @field_validator("reminder_offsets_minutes")
    @classmethod
    def validate_reminders(cls, value: List[int]) -> List[int]:
        for offset in value:
            if offset < 0 or offset > 43200:
                raise ValueError("Reminder offsets must be between 0 and 43200 minutes")
        return value
    @field_validator("approval_required_for")
    @classmethod
    def validate_approval_conditions(cls, value: List[str]) -> List[str]:
        return [_validate_optional_tenant_prompt_text(item, max_length=120) for item in value if str(item).strip()]
class OrderItemConfig(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=800)
    price: float = Field(default=0, ge=0)
    sku: Optional[str] = Field(default=None, max_length=80)
    enabled: bool = True
    preparation_minutes: Optional[int] = Field(default=None, ge=0, le=1440)
    variants: List[Dict[str, Any]] = Field(default_factory=list, max_length=50)
    addons: List[Dict[str, Any]] = Field(default_factory=list, max_length=50)
    @field_validator("id", "sku")
    @classmethod
    def validate_item_identifier(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _clean_text(value, max_length=80)
    @field_validator("name")
    @classmethod
    def validate_item_name(cls, value: str) -> str:
        return _validate_tenant_prompt_text(value, max_length=160)
    @field_validator("description")
    @classmethod
    def validate_item_description(cls, value: Optional[str]) -> Optional[str]:
        return _validate_optional_tenant_prompt_text(value, max_length=800)
class OrderCategoryConfig(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    items: List[OrderItemConfig] = Field(default_factory=list, max_length=500)
    @field_validator("id")
    @classmethod
    def validate_category_id(cls, value: str) -> str:
        return _clean_text(value, max_length=80)
    @field_validator("name")
    @classmethod
    def validate_category_name(cls, value: str) -> str:
        return _validate_tenant_prompt_text(value, max_length=120)
class OrderModuleConfig(ModuleConfig):
    currency: Optional[str] = Field(default="INR", max_length=8)
    fulfillment_types: List[str] = Field(default_factory=lambda: ["pickup", "delivery"], max_length=20)
    payment_methods: List[str] = Field(default_factory=lambda: ["cod", "upi_on_delivery", "pay_at_store"], max_length=20)
    requires_confirmation: bool = True
    categories: List[OrderCategoryConfig] = Field(default_factory=list, max_length=50)
    delivery_fee: float = Field(default=0.0)
    tax_amount: float = Field(default=0.0)
    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: Optional[str]) -> str:
        cleaned = _clean_text(value or "INR", max_length=8).upper()
        if not re.match(r"^[A-Z]{3,8}$", cleaned):
            raise ValueError("Currency must be an uppercase ISO-style code")
        return cleaned
    @field_validator("fulfillment_types", "payment_methods")
    @classmethod
    def validate_slug_list(cls, value: List[str]) -> List[str]:
        cleaned_values: List[str] = []
        for item in value:
            cleaned = _clean_text(item, max_length=40)
            if not re.match(r"^[a-z0-9_]+$", cleaned):
                raise ValueError("List values may contain only lowercase letters, numbers, and underscores")
            cleaned_values.append(cleaned)
        return cleaned_values
    @model_validator(mode="after")
    def validate_enabled_order_fulfillment(self) -> "OrderModuleConfig":
        if self.enabled and not self.fulfillment_types:
            raise ValueError("Order fulfillment options must include at least one option")
        return self
class FAQ(BaseModel):
    id: Optional[str] = None
    question: str = Field(min_length=2, max_length=300)
    answer: str = Field(min_length=2, max_length=1200)
    @field_validator("question", "answer")
    @classmethod
    def validate_faq_text(cls, value: str) -> str:
        return _validate_tenant_prompt_text(value, max_length=1200)
class Service(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    duration_mins: int = Field(default=30, ge=5, le=1440)
    price: Optional[float] = Field(default=None, ge=0)
    required_fields: List[str] = Field(default_factory=list)
    @field_validator("id")
    @classmethod
    def validate_service_id(cls, value: str) -> str:
        return _clean_text(value, max_length=80)
    @field_validator("name")
    @classmethod
    def validate_service_name(cls, value: str) -> str:
        return _clean_text(value, max_length=120)
class UsageLimits(BaseModel):
    monthly_credits: Optional[int] = None
    daily_messages: Optional[int] = None
    max_ai_tokens_per_month: Optional[int] = None
class SecuritySettings(BaseModel):
    """Tenant-editable anti-abuse controls (customer assurance is platform-owned)."""
    model_config = ConfigDict(extra="ignore")
    public_chat_captcha_required: bool = False
class TenantConfig(BaseModel):
    """
    Canonical tenant config_json contract used by DB seeds, API, frontend forms,
    prompt building, and workflow modules.
    """
    model_config = ConfigDict(extra="allow")
    business_name: str = Field(min_length=1, max_length=120)
    business_type: str = Field(default="other", min_length=1, max_length=80)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    default_language: str = Field(default="en", min_length=2, max_length=12)
    ai_mode: str = Field(default="standard", description="standard or advanced")
    ai_tone: str = Field(default="professional and friendly", min_length=2, max_length=160)
    greeting_message: str = Field(default="Hello! How can we help you today?", min_length=2, max_length=500)
    fallback_message: str = Field(default="I'm not sure yet. Could you share a little more detail?", min_length=2, max_length=500)
    escalation_message: str = Field(default="I'm connecting you with a human agent. They'll be with you shortly.", min_length=2, max_length=500)
    working_hours: Dict[str, DaySchedule] = Field(default_factory=dict)
    services: List[Service] = Field(default_factory=list)
    modules: Dict[str, ModuleConfig] = Field(default_factory=dict)
    faqs: List[FAQ] = Field(default_factory=list)
    escalation_phone: Optional[str] = Field(default=None, max_length=40)
    escalation_email: Optional[str] = Field(default=None, max_length=160)
    custom_instructions: Optional[str] = Field(default=None, max_length=2000)
    ignored_numbers: List[str] = Field(default_factory=list)
    # Legacy: Ignored at runtime. Enforcement comes from subscription_plans.limits_json via UsageGuard.
    limits: UsageLimits = Field(default_factory=UsageLimits)
    booking_settings: Optional[BookingSettings] = None
    security: Optional[SecuritySettings] = None
    @field_validator("business_name", "business_type", "timezone", "default_language")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _clean_text(value, max_length=160)
    @field_validator("ai_mode")
    @classmethod
    def validate_ai_mode(cls, value: str) -> str:
        cleaned = _clean_text(value, max_length=32, default="standard")
        return normalize_ai_mode(cleaned)
    @field_validator("ai_tone", "greeting_message", "fallback_message", "escalation_message")
    @classmethod
    def validate_prompt_text(cls, value: str) -> str:
        return _validate_tenant_prompt_text(value, max_length=500)
    @field_validator("custom_instructions")
    @classmethod
    def validate_custom_instructions(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _validate_optional_tenant_prompt_text(value, max_length=2000)
    @field_validator("modules")
    @classmethod
    def validate_module_payloads(cls, value: Dict[str, ModuleConfig]) -> Dict[str, ModuleConfig]:
        order_module = value.get("order")
        if order_module:
            validated = OrderModuleConfig.model_validate(order_module.model_dump() | (order_module.model_extra or {}))
            value["order"] = ModuleConfig(
                enabled=validated.enabled,
                fields=validated.fields,
                **validated.model_dump(exclude={"enabled", "fields"})
            )
        reminders_module = value.get("reminders")
        if reminders_module:
            validated = RemindersModuleConfig.model_validate(reminders_module.model_dump() | (reminders_module.model_extra or {}))
            value["reminders"] = ModuleConfig(
                enabled=validated.enabled,
                fields=validated.fields,
                **validated.model_dump(exclude={"enabled", "fields"})
            )
        return value
    @model_validator(mode="after")
    def validate_contact_lists(self) -> "TenantConfig":
        self.ignored_numbers = [_validate_phone_like_text(number, max_length=32) for number in self.ignored_numbers if str(number).strip()]
        # Validate support notification webhook URLs if present
        extra_data = self.model_extra or {}
        notification_config = extra_data.get("support_notifications")
        if not isinstance(notification_config, dict):
            notification_config = extra_data.get("notifications")
        if isinstance(notification_config, dict):
            webhook_urls = notification_config.get("webhook_urls")
            if webhook_urls:
                # Normalize string or list
                urls_list = []
                if isinstance(webhook_urls, list):
                    urls_list = [str(url).strip() for url in webhook_urls if str(url).strip()]
                elif isinstance(webhook_urls, str):
                    urls_list = [item.strip() for item in webhook_urls.split(",") if item.strip()]
                from backend.core.config import settings, is_unsafe_url_for_production
                for url in urls_list:
                    if settings.ENV == "production":
                        is_unsafe, reason = is_unsafe_url_for_production(url)
                        if is_unsafe:
                            raise ValueError(f"Webhook URL is unsafe for production: {reason} ({url})")
                    else:
                        from urllib.parse import urlparse
                        try:
                            parsed = urlparse(url)
                        except Exception:
                            raise ValueError(f"Invalid URL format: {url}")
                        if parsed.scheme not in ("http", "https"):
                            raise ValueError(f"Invalid URL scheme: {url}")
        return self
def build_default_tenant_config(
    business_name: str,
    business_type: str = "other",
    enabled_modules: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a normalized starter config for self-service signups."""
    if enabled_modules is None:
        enabled_modules = ["booking", "faq", "human_handoff"]
    enabled_set = set(enabled_modules)
    profile = BUSINESS_TYPE_PROFILES.get(str(business_type or "").lower(), {})
    return normalize_tenant_config({
        "business_name": business_name,
        "business_type": business_type,
        "timezone": "Asia/Kolkata",
        "default_language": "en",
        "working_hours": {
            "monday": "09:00-18:00",
            "tuesday": "09:00-18:00",
            "wednesday": "09:00-18:00",
            "thursday": "09:00-18:00",
            "friday": "09:00-18:00",
            "saturday": "closed",
            "sunday": "closed",
        },
        "services": [],
        "modules": {
            "booking": {
                "enabled": "booking" in enabled_set,
                "fields": profile.get("booking_fields", []),
            },
            "order": {
                "enabled": "order" in enabled_set,
                "fields": [],
                "currency": "INR",
                "fulfillment_types": ["pickup", "delivery"],
                "payment_methods": ["cod", "upi_on_delivery", "pay_at_store"],
                "requires_confirmation": True,
                "categories": [],
            },
            "lead_capture": {"enabled": "lead_capture" in enabled_set, "fields": []},
            "payments": {"enabled": "payments" in enabled_set, "fields": []},
            "reminders": {"enabled": "reminders" in enabled_set, "fields": []},
            "reviews": {"enabled": "reviews" in enabled_set, "fields": []},
            "support": {"enabled": "support" in enabled_set, "fields": []},
            "faq": {"enabled": "faq" in enabled_set, "fields": []},
            "human_handoff": {"enabled": "human_handoff" in enabled_set, "fields": []},
        },
        "ai_tone": "professional and friendly",
        "greeting_message": f"Hello! Welcome to {business_name}. How can I help you today?",
        "business_type_profile": {
            "label": profile.get("label") or _labelize(str(business_type or "business")),
            "prompt_guidance": profile.get("prompt_guidance") or "Use the saved business configuration and enabled tools. Ask for missing details before taking action.",
            "recommended_setup": profile.get("recommended_setup") or ["Review enabled tools", "Add FAQs", "Add human handoff contact"],
        },
        "faqs": [],
    })
def normalize_tenant_config(raw_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Accept legacy/editor config shapes and return the canonical config_json shape.
    This keeps old seeds and saved versions readable while preventing new drift.
    """
    config = dict(raw_config or {})
    business_name = str(config.get("business_name") or "Business").strip() or "Business"
    security = dict(config.get("security") or {}) if isinstance(config.get("security"), dict) else {}
    # Deprecated compatibility input: old versions may contain this tenant
    # preference, but platform customer-assurance policy always ignores it.
    security.pop("customer_otp_required_for_commitments", None)
    normalized = {
        "business_name": business_name,
        "business_type": str(config.get("business_type") or "other"),
        "timezone": str(config.get("timezone") or "UTC"),
        "default_language": str(config.get("default_language") or config.get("language") or "en"),
        "ai_mode": normalize_ai_mode(config.get("ai_mode")),
        "ai_tone": str(config.get("ai_tone") or "professional and friendly"),
        "greeting_message": str(config.get("greeting_message") or f"Hello! Welcome to {business_name}."),
        "fallback_message": str(config.get("fallback_message") or "I'm not sure yet. Could you share a little more detail?"),
        "escalation_message": str(config.get("escalation_message") or "I'm connecting you with a human agent. They'll be with you shortly."),
        "working_hours": _normalize_working_hours(config.get("working_hours")),
        "services": _normalize_services(config.get("services", [])),
        "modules": _normalize_modules(config.get("modules", {}), config.get("enabled_tools", [])),
        "faqs": _normalize_faqs(config.get("faqs", [])),
        "escalation_phone": config.get("escalation_phone"),
        "escalation_email": config.get("escalation_email"),
        "custom_instructions": config.get("custom_instructions"),
        "ignored_numbers": _normalize_string_list(config.get("ignored_numbers", [])),
        "limits": _normalize_limits(config.get("limits") or config.get("usage_limits") or {}),
        # Explicitly carry booking_settings and security through so they are
        # never silently dropped by model_validate when model_config extra="allow"
        # is present (model_dump strips fields not in model_fields for extra keys
        # depending on Pydantic version behaviour).
        "booking_settings": config.get("booking_settings"),
        "security": security,
    }
    # Preserve future extension keys while canonical keys above win.
    for key, value in config.items():
        if key not in normalized and key not in {"language", "enabled_tools", "usage_limits"}:
            normalized[key] = value
    return TenantConfig.model_validate(normalized).model_dump(exclude_none=True)
def _normalize_working_hours(value: Any) -> Dict[str, Dict[str, Any]]:
    source = value if isinstance(value, dict) else {}
    return {day: _normalize_day_schedule(source.get(day)) for day in DAYS}
def _normalize_day_schedule(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        is_open = bool(value.get("is_open", False))
        return {
            "is_open": is_open,
            "open_time": str(value.get("open_time") or "09:00"),
            "close_time": str(value.get("close_time") or "17:00"),
        }
    text = str(value or "closed").strip().lower()
    if not text or text == "closed":
        return {"is_open": False, "open_time": "09:00", "close_time": "17:00"}
    if "-" in text:
        open_time, close_time = [part.strip() for part in text.split("-", 1)]
        return {"is_open": True, "open_time": open_time or "09:00", "close_time": close_time or "17:00"}
    return {"is_open": True, "open_time": text, "close_time": "17:00"}
def _normalize_services(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    services: List[Dict[str, Any]] = []
    for service in value:
        if not isinstance(service, dict):
            continue
        name = str(service.get("name") or "Service").strip() or "Service"
        service_id = str(service.get("id") or _slugify(name))
        services.append({
            "id": service_id,
            "name": name,
            "duration_mins": _normalize_int(service.get("duration_mins") or service.get("duration_minutes"), 30),
            "price": _normalize_optional_float(service.get("price")),
            "required_fields": _normalize_string_list(service.get("required_fields") or service.get("fields") or []),
        })
    return services
def _normalize_modules(modules: Any, enabled_tools: Any) -> Dict[str, Dict[str, Any]]:
    source = modules if isinstance(modules, dict) else {}
    enabled_names = set(_normalize_string_list(enabled_tools))
    default_modules = {
        "booking": {"enabled": True, "fields": []},
        "order": {
            "enabled": False,
            "fields": [],
            "currency": "INR",
            "fulfillment_types": ["pickup", "delivery"],
            "payment_methods": ["cod", "upi_on_delivery", "pay_at_store"],
            "requires_confirmation": True,
            "categories": [],
        },
        "lead_capture": {"enabled": False, "fields": []},
        "payments": {"enabled": False, "fields": []},
        "reminders": {"enabled": False, "fields": []},
        "reviews": {"enabled": False, "fields": []},
        "support": {"enabled": False, "fields": []},
        "faq": {"enabled": True, "fields": []},
        "human_handoff": {"enabled": True, "fields": []},
    }
    merged = {**default_modules, **source}
    for module_name in enabled_names:
        merged.setdefault(module_name, {"enabled": True, "fields": []})
    result: Dict[str, Dict[str, Any]] = {}
    for module_name, module_config in merged.items():
        if not isinstance(module_config, dict):
            module_config = {"enabled": bool(module_config), "fields": []}
        enabled = module_name in enabled_names or bool(module_config.get("enabled", False))
        normalized_module = {
            "enabled": enabled,
            "fields": _normalize_module_fields(module_config.get("fields", [])),
        }
        for key, value in module_config.items():
            if key not in normalized_module and key != "fields":
                normalized_module[key] = value
        if module_name == "order":
            normalized_module["currency"] = str(normalized_module.get("currency") or "INR")
            fulfillment_source = (
                ["pickup", "delivery"]
                if "fulfillment_types" not in normalized_module or normalized_module.get("fulfillment_types") is None
                else normalized_module.get("fulfillment_types")
            )
            normalized_module["fulfillment_types"] = _normalize_string_list(
                fulfillment_source
            )
            normalized_module["payment_methods"] = _normalize_string_list(
                normalized_module.get("payment_methods") or ["cod", "upi_on_delivery", "pay_at_store"]
            )
            normalized_module["requires_confirmation"] = bool(normalized_module.get("requires_confirmation", True))
            normalized_module["categories"] = _normalize_order_categories(normalized_module.get("categories", []))
        result[module_name] = normalized_module
    return result
def _normalize_order_categories(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    categories: List[Dict[str, Any]] = []
    for category in value:
        if not isinstance(category, dict):
            continue
        name = str(category.get("name") or "Items").strip() or "Items"
        category_id = str(category.get("id") or _slugify(name))
        categories.append({
            "id": category_id,
            "name": name,
            "enabled": bool(category.get("enabled", True)),
            "items": _normalize_order_items(category.get("items", [])),
        })
    return categories
def _normalize_order_items(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "Item").strip() or "Item"
        item_id = str(item.get("id") or _slugify(name))
        items.append({
            "id": item_id,
            "name": name,
            "description": str(item.get("description") or ""),
            "price": _normalize_optional_float(item.get("price")) or 0,
            "sku": str(item.get("sku") or ""),
            "enabled": bool(item.get("enabled", True)),
            "preparation_minutes": int(_normalize_optional_float(item.get("preparation_minutes")) or 0),
            "variants": item.get("variants") if isinstance(item.get("variants"), list) else [],
            "addons": item.get("addons") if isinstance(item.get("addons"), list) else [],
        })
    return items
def _normalize_module_fields(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    fields: List[Dict[str, Any]] = []
    for field in value:
        if not isinstance(field, dict) or not field.get("key"):
            continue
        normalized = {
            "key": str(field["key"]),
            "label": field.get("label") or _labelize(str(field["key"])),
            "type": str(field.get("type") or "string"),
            "required": bool(field.get("required", True)),
        }
        if isinstance(field.get("options"), list):
            normalized["options"] = _normalize_string_list(field["options"])
        if field.get("options_from"):
            normalized["options_from"] = str(field["options_from"])
        if field.get("applies_when"):
            normalized["applies_when"] = _clean_text(str(field["applies_when"]), max_length=160)
        fields.append(normalized)
    return fields
def _normalize_faqs(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    faqs: List[Dict[str, Any]] = []
    for faq in value:
        if not isinstance(faq, dict) or not faq.get("question") or not faq.get("answer"):
            continue
        item = {"question": str(faq["question"]), "answer": str(faq["answer"])}
        if faq.get("id"):
            item["id"] = str(faq["id"])
        faqs.append(item)
    return faqs
def _normalize_limits(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}
def _clean_text(value: Any, max_length: int, default: str = "") -> str:
    text = str(value if value is not None else default)
    text = CONTROL_CHAR_PATTERN.sub("", text).strip()
    if len(text) > max_length:
        raise ValueError(f"Text is too long. Maximum length is {max_length} characters")
    return text
def _validate_tenant_prompt_text(value: Any, max_length: int) -> str:
    text = _clean_text(value, max_length=max_length)
    if not text:
        raise ValueError("Required text cannot be empty")
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern.search(text):
            raise ValueError(
                "This text looks like a prompt-injection or system-override instruction. "
                "Keep tenant AI content limited to business facts, tone, policies, and customer-facing answers."
            )
    return text
def _validate_optional_tenant_prompt_text(value: Any, max_length: int) -> Optional[str]:
    text = _clean_text(value, max_length=max_length)
    if not text:
        return None
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern.search(text):
            raise ValueError(
                "This text looks like a prompt-injection or system-override instruction. "
                "Keep tenant AI content limited to business facts, tone, policies, and customer-facing answers."
            )
    return text
def _validate_phone_like_text(value: Any, max_length: int) -> str:
    text = _clean_text(value, max_length=max_length)
    if not re.match(r"^\+?[0-9][0-9\s().-]{5,31}$", text):
        raise ValueError("Phone numbers must include digits and may only contain +, spaces, parentheses, dots, or hyphens")
    return text
def _normalize_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
def _normalize_optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
def _normalize_string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(",") if item.strip()]
    return []
def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "service"
def _labelize(value: str) -> str:
    return value.replace("_", " ").strip().title()
