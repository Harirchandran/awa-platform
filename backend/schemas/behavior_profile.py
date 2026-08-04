from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.core.runtime_capabilities import KNOWN_RUNTIME_MODULES, normalize_runtime_module_key


GreetingMode = Literal["ask_need_first", "menu_first", "hybrid"]
ClarificationStyle = Literal["one_question_at_a_time", "compact", "detailed"]
Tone = Literal["neutral", "friendly", "formal", "concise", "premium"]
LanguagePolicy = Literal["tenant_default", "follow_customer", "local_language_first"]
PriceVisibility = Literal["show_when_available", "when_asked", "hide_unless_required"]
ServiceDetailPolicy = Literal["brief", "explain_when_asked", "explain_before_booking"]
HandoffPolicy = Literal["never_proactive", "when_stuck", "early_offer", "during_off_hours"]
FallbackPolicy = Literal["faq_first", "clarify_first", "handoff_if_available"]
QuestionBatching = Literal["single", "grouped"]
MenuPolicy = Literal["show_available_workflows", "hide_menu_unless_asked"]
ChannelKey = Literal["public_chat", "whatsapp"]
SafeFallbackMode = Literal["general_help"]
BusinessBehaviorPresetStatus = Literal["draft", "active", "inactive", "deprecated"]
BusinessBehaviorPresetCreateStatus = Literal["draft", "inactive", "deprecated"]
BusinessBehaviorPresetDeactivateStatus = Literal["inactive", "deprecated"]

PRESENTATION_FIELD_NAMES = frozenset(
    {
        "greeting_mode",
        "clarification_style",
        "tone",
        "language_policy",
        "price_visibility",
        "service_detail_policy",
        "handoff_policy",
        "fallback_policy",
        "question_batching",
        "menu_policy",
    }
)
SAFE_TENANT_EDITABLE_FIELDS = PRESENTATION_FIELD_NAMES | frozenset({"journey_priority", "channel_overrides"})

FORBIDDEN_FIELD_NAME_FRAGMENTS = (
    "prompt",
    "instruction",
    "provider",
    "payment",
    "paid",
    "success",
    "link",
    "otp",
    "captcha",
    "confirmation",
    "tool",
    "command",
    "internal",
    "uuid",
    "_id",
    "id_",
    "identifier",
    "plan",
    "feature_flag",
    "limit",
    "token",
    "secret",
)
_SAFE_TEXT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$")
_SAFE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class _StrictBehaviorModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _safe_profile_text(value: str, *, field_name: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned or not _SAFE_TEXT_PATTERN.match(cleaned):
        raise ValueError(f"{field_name} must be short label text, not instructions")
    lowered = cleaned.lower()
    if any(fragment in lowered for fragment in ("ignore previous", "system:", "developer:", "assistant:", "prompt", "instruction")):
        raise ValueError(f"{field_name} cannot contain prompt or instruction text")
    return cleaned


def _safe_key(value: str, *, field_name: str) -> str:
    cleaned = str(value or "").strip().lower()
    if not cleaned or not _SAFE_KEY_PATTERN.match(cleaned):
        raise ValueError(f"{field_name} must be a short slug key")
    return cleaned


def normalize_behavior_business_type_key(value: str) -> str:
    return _safe_key(value, field_name="business_type_key")


def normalize_journey_priority(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("journey_priority must be a list")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in value:
        module = normalize_runtime_module_key(str(raw or "").strip())
        if module not in KNOWN_RUNTIME_MODULES:
            raise ValueError(f"Unknown runtime module in journey_priority: {raw}")
        if module in seen:
            raise ValueError(f"Duplicate runtime module in journey_priority: {module}")
        seen.add(module)
        normalized.append(module)
    return tuple(normalized)


def validate_tenant_editable_fields(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("tenant_editable_fields must be a list")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in value:
        field_name = str(raw or "").strip()
        if field_name not in SAFE_TENANT_EDITABLE_FIELDS:
            raise ValueError(f"Unsafe tenant editable field: {field_name}")
        if field_name in seen:
            raise ValueError(f"Duplicate tenant editable field: {field_name}")
        seen.add(field_name)
        normalized.append(field_name)
    return tuple(normalized)


def assert_no_forbidden_keys(data: Any) -> None:
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        key_text = str(key).lower()
        if any(fragment in key_text for fragment in FORBIDDEN_FIELD_NAME_FRAGMENTS):
            raise ValueError(f"Unsafe behavior profile field is not allowed: {key}")
        assert_no_forbidden_keys(value)


class ChannelBehaviorOverride(_StrictBehaviorModel):
    greeting_mode: GreetingMode | None = None
    clarification_style: ClarificationStyle | None = None
    tone: Tone | None = None
    language_policy: LanguagePolicy | None = None
    price_visibility: PriceVisibility | None = None
    service_detail_policy: ServiceDetailPolicy | None = None
    handoff_policy: HandoffPolicy | None = None
    fallback_policy: FallbackPolicy | None = None
    question_batching: QuestionBatching | None = None
    menu_policy: MenuPolicy | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_unsafe_raw_keys(cls, data: Any) -> Any:
        assert_no_forbidden_keys(data)
        return data

    def active_values(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class BehaviorProfileDefinition(_StrictBehaviorModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    profile_name: str = "Legacy Safe Default"
    business_type_key: str | None = None
    journey_priority: tuple[str, ...] = Field(default_factory=tuple)
    greeting_mode: GreetingMode = "hybrid"
    clarification_style: ClarificationStyle = "one_question_at_a_time"
    tone: Tone = "friendly"
    language_policy: LanguagePolicy = "follow_customer"
    price_visibility: PriceVisibility = "show_when_available"
    service_detail_policy: ServiceDetailPolicy = "brief"
    handoff_policy: HandoffPolicy = "when_stuck"
    fallback_policy: FallbackPolicy = "clarify_first"
    question_batching: QuestionBatching = "single"
    menu_policy: MenuPolicy = "show_available_workflows"
    channel_overrides: dict[ChannelKey, ChannelBehaviorOverride] = Field(default_factory=dict)
    tenant_editable_fields: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="before")
    @classmethod
    def reject_unsafe_raw_keys(cls, data: Any) -> Any:
        assert_no_forbidden_keys(data)
        return data

    @field_validator("profile_name")
    @classmethod
    def validate_profile_name(cls, value: str) -> str:
        return _safe_profile_text(value, field_name="profile_name")

    @field_validator("business_type_key")
    @classmethod
    def validate_business_type_key(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        return _safe_key(value, field_name="business_type_key")

    @field_validator("journey_priority", mode="before")
    @classmethod
    def validate_journey_priority(cls, value: Any) -> tuple[str, ...]:
        return normalize_journey_priority(value)

    @field_validator("tenant_editable_fields", mode="before")
    @classmethod
    def validate_editable_fields(cls, value: Any) -> tuple[str, ...]:
        return validate_tenant_editable_fields(value)


class TenantBehaviorOverrides(_StrictBehaviorModel):
    journey_priority: tuple[str, ...] | None = None
    greeting_mode: GreetingMode | None = None
    clarification_style: ClarificationStyle | None = None
    tone: Tone | None = None
    language_policy: LanguagePolicy | None = None
    price_visibility: PriceVisibility | None = None
    service_detail_policy: ServiceDetailPolicy | None = None
    handoff_policy: HandoffPolicy | None = None
    fallback_policy: FallbackPolicy | None = None
    question_batching: QuestionBatching | None = None
    menu_policy: MenuPolicy | None = None
    channel_overrides: dict[ChannelKey, ChannelBehaviorOverride] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_unsafe_raw_keys(cls, data: Any) -> Any:
        assert_no_forbidden_keys(data)
        return data

    @field_validator("journey_priority", mode="before")
    @classmethod
    def validate_journey_priority(cls, value: Any) -> tuple[str, ...]:
        return normalize_journey_priority(value)

    def active_values(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class TenantReceptionistBehaviorOverrides(_StrictBehaviorModel):
    journey_priority: tuple[str, ...] | None = None
    greeting_mode: GreetingMode | None = None
    clarification_style: ClarificationStyle | None = None
    tone: Tone | None = None
    language_policy: LanguagePolicy | None = None
    price_visibility: PriceVisibility | None = None
    service_detail_policy: ServiceDetailPolicy | None = None
    handoff_policy: HandoffPolicy | None = None
    fallback_policy: FallbackPolicy | None = None
    question_batching: QuestionBatching | None = None
    menu_policy: MenuPolicy | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_unsafe_raw_keys(cls, data: Any) -> Any:
        assert_no_forbidden_keys(data)
        return data

    @field_validator("journey_priority", mode="before")
    @classmethod
    def validate_journey_priority(cls, value: Any) -> tuple[str, ...]:
        return normalize_journey_priority(value)

    def active_values(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class TenantReceptionistBehaviorPatchRequest(_StrictBehaviorModel):
    expected_config_version: int = Field(ge=1)
    overrides: TenantReceptionistBehaviorOverrides = Field(default_factory=TenantReceptionistBehaviorOverrides)
    channel_overrides: dict[ChannelKey, ChannelBehaviorOverride] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def reject_unsafe_raw_keys(cls, data: Any) -> Any:
        assert_no_forbidden_keys(data)
        return data


class TenantReceptionistBehaviorResetRequest(_StrictBehaviorModel):
    expected_config_version: int = Field(ge=1)

    @model_validator(mode="before")
    @classmethod
    def reject_unsafe_raw_keys(cls, data: Any) -> Any:
        assert_no_forbidden_keys(data)
        return data


class EffectiveBehaviorProfile(_StrictBehaviorModel):
    schema_version: int = 1
    profile_name: str
    business_type_key: str | None = None
    journey_priority: tuple[str, ...]
    greeting_mode: GreetingMode
    clarification_style: ClarificationStyle
    tone: Tone
    language_policy: LanguagePolicy
    price_visibility: PriceVisibility
    service_detail_policy: ServiceDetailPolicy
    handoff_policy: HandoffPolicy
    fallback_policy: FallbackPolicy
    question_batching: QuestionBatching
    menu_policy: MenuPolicy
    channel_overrides: dict[ChannelKey, ChannelBehaviorOverride] = Field(default_factory=dict)
    tenant_editable_fields: tuple[str, ...] = Field(default_factory=tuple)
    safe_fallback_mode: SafeFallbackMode = "general_help"
    active_workflow_hint: str | None = None


class BusinessBehaviorPresetValidateRequest(_StrictBehaviorModel):
    business_type_key: str
    profile: BehaviorProfileDefinition

    @field_validator("business_type_key")
    @classmethod
    def validate_business_type_key(cls, value: str) -> str:
        return _safe_key(value, field_name="business_type_key")

    @model_validator(mode="after")
    def validate_profile_business_type_key(self) -> "BusinessBehaviorPresetValidateRequest":
        if self.profile.business_type_key and self.profile.business_type_key != self.business_type_key:
            raise ValueError("profile.business_type_key must match business_type_key")
        return self

    def normalized_profile(self) -> BehaviorProfileDefinition:
        values = self.profile.model_dump(mode="json")
        values["business_type_key"] = self.business_type_key
        return BehaviorProfileDefinition.model_validate(values)


class BusinessBehaviorPresetCreateRequest(BusinessBehaviorPresetValidateRequest):
    version: int | None = Field(default=None, ge=1)
    status: BusinessBehaviorPresetCreateStatus = "draft"
    change_note: str | None = Field(default=None, max_length=500)


class BusinessBehaviorPresetActivateRequest(_StrictBehaviorModel):
    change_note: str | None = Field(default=None, max_length=500)


class BusinessBehaviorPresetDeactivateRequest(_StrictBehaviorModel):
    status: BusinessBehaviorPresetDeactivateStatus = "inactive"
    change_note: str | None = Field(default=None, max_length=500)


class BusinessBehaviorPresetVersionResponse(_StrictBehaviorModel):
    business_type_key: str
    version: int
    profile: BehaviorProfileDefinition
    tenant_editable_fields: tuple[str, ...] = Field(default_factory=tuple)
    status: BusinessBehaviorPresetStatus
    is_active: bool
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None = None
    deactivated_at: datetime | None = None
    change_note: str | None = None


class BusinessBehaviorPresetListResponse(_StrictBehaviorModel):
    presets: tuple[BusinessBehaviorPresetVersionResponse, ...]


class BusinessBehaviorPresetValidationResponse(_StrictBehaviorModel):
    valid: bool
    profile: BehaviorProfileDefinition
    tenant_editable_fields: tuple[str, ...]
