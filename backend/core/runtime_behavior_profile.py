from __future__ import annotations

from typing import Any, Literal, Mapping
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from backend.core.behavior_profile import (
    LEGACY_SAFE_DEFAULT_PROFILE,
    resolve_effective_behavior_profile,
)
from backend.core.runtime_capabilities import RuntimeCapabilitySnapshot
from backend.db.client import db_client
from backend.schemas.behavior_profile import (
    BehaviorProfileDefinition,
    BusinessBehaviorPresetStatus,
    EffectiveBehaviorProfile,
)


PINNED_PRESET_VALID_STATUSES = frozenset({"active", "inactive"})
RuntimeBehaviorProfileSource = Literal["pinned", "active", "legacy_fallback"]


class RuntimeBehaviorProfileResolution(BaseModel):
    model_config = ConfigDict(frozen=True)

    effective_profile: EffectiveBehaviorProfile
    source: RuntimeBehaviorProfileSource
    warning_code: str | None = None
    preset_key: str | None = None
    preset_version: int | None = None
    preset_status: BusinessBehaviorPresetStatus | None = None


class _PresetSelection(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    preset: Mapping[str, Any] | None = None
    source: RuntimeBehaviorProfileSource
    warning_code: str | None = None


def behavior_storage_from_config(config_data: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = config_data.get("receptionist_behavior") if isinstance(config_data, Mapping) else None
    return dict(raw) if isinstance(raw, Mapping) else {}


def runtime_override_payload(storage: Mapping[str, Any]) -> dict[str, Any]:
    overrides = storage.get("overrides") if isinstance(storage.get("overrides"), Mapping) else {}
    channel_overrides = storage.get("channel_overrides") if isinstance(storage.get("channel_overrides"), Mapping) else {}
    return {**dict(overrides), "channel_overrides": dict(channel_overrides)}


async def select_behavior_preset_for_config(
    *,
    config_data: Mapping[str, Any],
    db: Any = db_client,
) -> _PresetSelection:
    storage = behavior_storage_from_config(config_data)
    pinned_key = storage.get("preset_key")
    pinned_version = storage.get("preset_version")
    has_pin = pinned_key not in (None, "") or pinned_version not in (None, "")

    if has_pin:
        try:
            version = int(pinned_version)
        except (TypeError, ValueError):
            return _PresetSelection(source="legacy_fallback", warning_code="invalid_pinned_behavior_preset")
        if not str(pinned_key or "").strip() or version < 1:
            return _PresetSelection(source="legacy_fallback", warning_code="invalid_pinned_behavior_preset")
        preset = await db.get_business_behavior_preset(str(pinned_key), version)
        if preset and str(preset.get("status") or "") in PINNED_PRESET_VALID_STATUSES:
            return _PresetSelection(preset=preset, source="pinned")
        return _PresetSelection(
            preset=preset if isinstance(preset, Mapping) else None,
            source="legacy_fallback",
            warning_code="invalid_pinned_behavior_preset",
        )

    business_type_key = str(config_data.get("business_type") or "other")
    preset = await db.get_active_business_behavior_preset(business_type_key)
    if not preset:
        return _PresetSelection(source="legacy_fallback", warning_code="no_active_behavior_preset")
    return _PresetSelection(preset=preset, source="active")


async def resolve_runtime_behavior_profile(
    *,
    tenant_id: UUID,
    config_data: Mapping[str, Any],
    runtime_capability_snapshot: RuntimeCapabilitySnapshot,
    channel: str,
    conversation_state: Any = None,
    db: Any = db_client,
) -> RuntimeBehaviorProfileResolution:
    del tenant_id  # Tenant identity is carried by the supplied capability snapshot.
    try:
        selection = await select_behavior_preset_for_config(config_data=config_data, db=db)
        if selection.source == "legacy_fallback" or selection.preset is None:
            return _fallback_resolution(
                runtime_capability_snapshot=runtime_capability_snapshot,
                channel=channel,
                conversation_state=conversation_state,
                warning_code=selection.warning_code,
                preset=selection.preset,
            )

        preset_profile = BehaviorProfileDefinition.model_validate(selection.preset.get("profile") or {})
        effective = resolve_effective_behavior_profile(
            tenant_config={"receptionist_behavior": runtime_override_payload(behavior_storage_from_config(config_data))},
            preset=preset_profile,
            runtime_capability_snapshot=runtime_capability_snapshot,
            channel=channel,
            conversation_state=conversation_state,
        )
        return RuntimeBehaviorProfileResolution(
            effective_profile=effective,
            source=selection.source,
            preset_key=_safe_preset_key(selection.preset),
            preset_version=_safe_preset_version(selection.preset),
            preset_status=_safe_preset_status(selection.preset),
        )
    except Exception:
        return _fallback_resolution(
            runtime_capability_snapshot=runtime_capability_snapshot,
            channel=channel,
            conversation_state=conversation_state,
            warning_code="behavior_profile_resolution_failed",
        )


def _fallback_resolution(
    *,
    runtime_capability_snapshot: RuntimeCapabilitySnapshot,
    channel: str,
    conversation_state: Any,
    warning_code: str | None,
    preset: Mapping[str, Any] | None = None,
) -> RuntimeBehaviorProfileResolution:
    effective = resolve_effective_behavior_profile(
        tenant_config={},
        preset=LEGACY_SAFE_DEFAULT_PROFILE,
        runtime_capability_snapshot=runtime_capability_snapshot,
        channel=channel,
        conversation_state=conversation_state,
    )
    return RuntimeBehaviorProfileResolution(
        effective_profile=effective,
        source="legacy_fallback",
        warning_code=warning_code or "behavior_profile_fallback",
        preset_key=_safe_preset_key(preset),
        preset_version=_safe_preset_version(preset),
        preset_status=_safe_preset_status(preset),
    )


def _safe_preset_key(preset: Mapping[str, Any] | None) -> str | None:
    if not isinstance(preset, Mapping):
        return None
    value = preset.get("business_type_key")
    return str(value) if value not in (None, "") else None


def _safe_preset_version(preset: Mapping[str, Any] | None) -> int | None:
    if not isinstance(preset, Mapping):
        return None
    try:
        version = int(preset.get("version"))
    except (TypeError, ValueError):
        return None
    return version if version >= 1 else None


def _safe_preset_status(preset: Mapping[str, Any] | None) -> BusinessBehaviorPresetStatus | None:
    if not isinstance(preset, Mapping):
        return None
    status = str(preset.get("status") or "")
    return status if status in {"draft", "active", "inactive", "deprecated"} else None
