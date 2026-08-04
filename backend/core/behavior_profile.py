from __future__ import annotations

from typing import Any, Mapping

from pydantic import ValidationError

from backend.core.runtime_capabilities import RuntimeCapabilitySnapshot, normalize_runtime_module_key
from backend.schemas.behavior_profile import (
    BehaviorProfileDefinition,
    ChannelBehaviorOverride,
    EffectiveBehaviorProfile,
    PRESENTATION_FIELD_NAMES,
    SAFE_TENANT_EDITABLE_FIELDS,
    TenantBehaviorOverrides,
)


DEFAULT_JOURNEY_PRIORITY: tuple[str, ...] = (
    "booking",
    "order",
    "lead_capture",
    "support",
    "faq",
    "human_handoff",
)

LEGACY_TENANT_EDITABLE_FIELDS: tuple[str, ...] = tuple(
    field for field in (
        "journey_priority",
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
        "channel_overrides",
    )
    if field in SAFE_TENANT_EDITABLE_FIELDS
)


LEGACY_SAFE_DEFAULT_PROFILE = BehaviorProfileDefinition(
    profile_name="Legacy Safe Default",
    business_type_key=None,
    journey_priority=DEFAULT_JOURNEY_PRIORITY,
    greeting_mode="hybrid",
    clarification_style="one_question_at_a_time",
    tone="friendly",
    language_policy="follow_customer",
    price_visibility="show_when_available",
    service_detail_policy="brief",
    handoff_policy="when_stuck",
    fallback_policy="clarify_first",
    question_batching="single",
    menu_policy="show_available_workflows",
    tenant_editable_fields=LEGACY_TENANT_EDITABLE_FIELDS,
)


def resolve_effective_behavior_profile(
    *,
    tenant_config: Mapping[str, Any],
    preset: BehaviorProfileDefinition | Mapping[str, Any] | None,
    runtime_capability_snapshot: RuntimeCapabilitySnapshot,
    channel: str,
    conversation_state: Any = None,
) -> EffectiveBehaviorProfile:
    """Resolve a pure, presentation-only receptionist behavior profile.

    This function is intentionally not integrated into prompt/menu/NBA/public
    chat runtime paths in Phase 3D-5D-1. It reads no DB, Redis, env, provider,
    tool, or LLM state. RuntimeCapabilitySnapshot remains the module authority.
    """
    if not isinstance(tenant_config, Mapping):
        raise ValueError("tenant_config must be a mapping")

    base = LEGACY_SAFE_DEFAULT_PROFILE
    preset_profile = _coerce_preset(preset) if preset is not None else None
    if preset_profile is not None:
        base = _merge_profile(base, preset_profile)

    profile_values = _profile_values(base)
    overrides = _tenant_overrides(tenant_config)
    if overrides is not None:
        _apply_tenant_overrides(profile_values, overrides, base.tenant_editable_fields)

    _apply_channel_override(profile_values, channel)
    profile_values["journey_priority"] = _filter_available_journey(
        profile_values.get("journey_priority") or (),
        runtime_capability_snapshot,
    )
    profile_values["active_workflow_hint"] = _active_workflow_hint(
        conversation_state,
        runtime_capability_snapshot,
    )
    profile_values["safe_fallback_mode"] = "general_help"

    # Do not carry tenant custom_instructions, provider/payment/security fields,
    # feature flags, tenant limits, plan names, or execution permissions into
    # the effective profile. The schema rejects them if they are attempted.
    return EffectiveBehaviorProfile.model_validate(profile_values)


def _coerce_preset(value: BehaviorProfileDefinition | Mapping[str, Any]) -> BehaviorProfileDefinition:
    if isinstance(value, BehaviorProfileDefinition):
        return value
    if isinstance(value, Mapping):
        return BehaviorProfileDefinition.model_validate(dict(value))
    raise ValueError("preset must be a behavior profile mapping or model")


def _tenant_overrides(tenant_config: Mapping[str, Any]) -> TenantBehaviorOverrides | None:
    raw = tenant_config.get("receptionist_behavior")
    if raw in (None, ""):
        return None
    if isinstance(raw, TenantBehaviorOverrides):
        return raw
    if not isinstance(raw, Mapping):
        raise ValueError("tenant_config.receptionist_behavior must be a mapping")
    try:
        return TenantBehaviorOverrides.model_validate(dict(raw))
    except ValidationError:
        raise


def _merge_profile(
    default_profile: BehaviorProfileDefinition,
    preset_profile: BehaviorProfileDefinition,
) -> BehaviorProfileDefinition:
    values = default_profile.model_dump()
    values.update(preset_profile.model_dump(exclude_none=True))
    return BehaviorProfileDefinition.model_validate(values)


def _profile_values(profile: BehaviorProfileDefinition) -> dict[str, Any]:
    return profile.model_dump()


def _apply_tenant_overrides(
    profile_values: dict[str, Any],
    overrides: TenantBehaviorOverrides,
    editable_fields: tuple[str, ...],
) -> None:
    editable = set(editable_fields)
    for field_name, value in overrides.active_values().items():
        if field_name not in editable:
            continue
        if field_name == "channel_overrides":
            profile_values[field_name] = _merge_channel_overrides(
                profile_values.get("channel_overrides") or {},
                value or {},
            )
            continue
        profile_values[field_name] = value


def _merge_channel_overrides(
    existing: Mapping[str, Any],
    incoming: Mapping[str, Any],
) -> dict[str, ChannelBehaviorOverride]:
    merged: dict[str, ChannelBehaviorOverride] = {}
    for channel, override in existing.items():
        merged[str(channel)] = override if isinstance(override, ChannelBehaviorOverride) else ChannelBehaviorOverride.model_validate(override)
    for channel, override in incoming.items():
        current = merged.get(str(channel))
        current_values = current.active_values() if current else {}
        next_override = override if isinstance(override, ChannelBehaviorOverride) else ChannelBehaviorOverride.model_validate(override)
        current_values.update(next_override.active_values())
        merged[str(channel)] = ChannelBehaviorOverride.model_validate(current_values)
    return merged


def _apply_channel_override(profile_values: dict[str, Any], channel: str) -> None:
    channel_key = _normalize_channel(channel)
    channel_overrides = profile_values.get("channel_overrides") or {}
    override = channel_overrides.get(channel_key)
    if not override:
        return
    if not isinstance(override, ChannelBehaviorOverride):
        override = ChannelBehaviorOverride.model_validate(override)
    for field_name, value in override.active_values().items():
        if field_name in PRESENTATION_FIELD_NAMES:
            profile_values[field_name] = value


def _normalize_channel(channel: str) -> str:
    raw = str(channel or "").strip().lower()
    if raw in {"awa_chat", "public_chat", "website", "web"}:
        return "public_chat"
    if raw == "whatsapp":
        return "whatsapp"
    return raw


def _filter_available_journey(
    journey_priority: tuple[str, ...],
    snapshot: RuntimeCapabilitySnapshot,
) -> tuple[str, ...]:
    available = snapshot.runtime_available_modules
    filtered: list[str] = []
    seen: set[str] = set()
    for raw in journey_priority:
        module = normalize_runtime_module_key(raw)
        if module in available and module not in seen:
            filtered.append(module)
            seen.add(module)
    return tuple(filtered)


def _active_workflow_hint(
    conversation_state: Any,
    snapshot: RuntimeCapabilitySnapshot,
) -> str | None:
    if conversation_state is None:
        return None
    active = None
    ledger = getattr(conversation_state, "action_ledger", None)
    if ledger is not None:
        active = getattr(ledger, "current_task", None)
        pending = getattr(ledger, "pending_action", None)
        if pending is not None and getattr(pending, "tool_key", None):
            active = str(pending.tool_key).split(".", 1)[0]
    if not active:
        active = getattr(conversation_state, "current_module", None)
    module = normalize_runtime_module_key(str(active or ""))
    if module and snapshot.is_available(module):
        return module
    return None
