"""
Config API Routes
GET /api/v1/config — get active config
POST /api/v1/config — create new config version
POST /api/v1/config/{id}/activate — activate a config version
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from typing import Dict, Any, Mapping, Optional
from uuid import UUID
import json
from backend.api.dependencies import get_current_user, require_tenant_admin
from backend.api.entitlements import assert_config_update_allowed, build_tenant_admin_entitlement_summary
from backend.billing.usage_guard import build_ai_capability_profiles, get_ai_capability_policy, usage_guard
from backend.core.ai_modes import normalize_ai_mode
from backend.core.context import Tenant
from backend.core.behavior_profile import LEGACY_SAFE_DEFAULT_PROFILE, resolve_effective_behavior_profile
from backend.core.runtime_behavior_profile import (
    behavior_storage_from_config,
    runtime_override_payload,
    select_behavior_preset_for_config,
)
from backend.core.runtime_capabilities import KNOWN_RUNTIME_MODULES, build_runtime_capability_snapshot
from backend.db.client import db_client
from backend.knowledge.ingestion import knowledge_ingestion_service
from backend.schemas.response import APIResponse
from backend.schemas.config import normalize_tenant_config
from backend.schemas.behavior_profile import (
    BehaviorProfileDefinition,
    ChannelBehaviorOverride,
    PRESENTATION_FIELD_NAMES,
    TenantReceptionistBehaviorPatchRequest,
    TenantReceptionistBehaviorResetRequest,
)
from backend.services.tool_controls import build_tool_control_state
from backend.ops.logger import logger
router = APIRouter(prefix="/api/v1", tags=["Config"])
class CreateConfigRequest(BaseModel):
    config_json: Dict[str, Any]
    expected_version: Optional[int] = None
def _normalize_or_422(config_json: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return normalize_tenant_config(config_json)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
def _parse_config_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}
async def _assert_current_version_or_409(tenant_id: UUID, expected_version: Optional[int]) -> None:
    if expected_version is None:
        return
    active_version = await db_client.get_active_config_version(tenant_id)
    if active_version != expected_version:
        raise HTTPException(
            status_code=409,
            detail=(
                "Configuration changed in another tab or session. "
                "Refresh before saving so you do not overwrite newer changes."
            ),
        )
async def _reject_receptionist_behavior_bypass(
    tenant_id: UUID,
    proposed_config: Mapping[str, Any],
) -> None:
    if "receptionist_behavior" not in proposed_config:
        return
    current = await db_client.get_tenant_config(tenant_id)
    current_config = _parse_config_json(current.get("config_json") if current else {})
    if proposed_config.get("receptionist_behavior") == current_config.get("receptionist_behavior"):
        return
    raise HTTPException(
        status_code=422,
        detail={
            "code": "receptionist_behavior_dedicated_api_required",
            "message": "Use /api/v1/config/receptionist-behavior to update receptionist behavior.",
        },
    )
async def _get_tool_catalog_or_none() -> Optional[list[dict[str, Any]]]:
    try:
        tools = await db_client.get_tool_catalog()
    except (AttributeError, TypeError):
        return None
    return tools if isinstance(tools, list) and tools else None
async def _get_tenant_tool_policy(tenant_record: dict) -> tuple[dict, list[str]]:
    tenant = Tenant(
        id=tenant_record["id"],
        name=tenant_record["name"],
        plan_code=tenant_record.get("plan_code") or "trial",
        status=tenant_record.get("status") or "trial",
        ai_mode=tenant_record.get("ai_mode"),
        trial_ends_at=tenant_record.get("trial_ends_at"),
        current_period_end=tenant_record.get("current_period_end"),
    )
    policy = await usage_guard.get_policy_async(tenant)
    return {
        "plan_code": policy.plan_code,
        "allowed_ai_modes": list(policy.allowed_ai_modes),
        "allowed_modules": list(policy.allowed_modules),
        "ai_capability_profiles": build_ai_capability_profiles(policy),
        "ai_capability_policy": get_ai_capability_policy(
            tenant_record.get("ai_mode") or "standard",
            policy,
        ),
    }, list(policy.allowed_modules)
async def _assert_ai_mode_allowed(tenant_id: UUID, config_dict: Dict[str, Any]) -> None:
    tenant = await db_client.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    policy, _allowed_modules = await _get_tenant_tool_policy(tenant)
    requested_mode = normalize_ai_mode(config_dict.get("ai_mode"))
    allowed_modes = set(policy["allowed_ai_modes"])
    if requested_mode not in allowed_modes:
        raise HTTPException(
            status_code=403,
            detail=(
                f"AI mode '{requested_mode}' is not available on the current "
                f"{policy['plan_code']} plan. Allowed modes: {', '.join(policy['allowed_ai_modes'])}."
            ),
        )
async def _sync_configured_faq_knowledge(tenant_id: UUID, config_dict: Dict[str, Any]) -> None:
    try:
        await knowledge_ingestion_service.sync_faqs(tenant_id, config_dict.get("faqs", []))
    except Exception as exc:
        logger.warning(f"FAQ knowledge sync failed for tenant {tenant_id}: {exc}")

def _labelize_key(value: str) -> str:
    return str(value or "").replace("_", " ").replace("-", " ").strip().title()

def _behavior_storage_from_config(config_json: Mapping[str, Any]) -> dict[str, Any]:
    return behavior_storage_from_config(config_json)

def _runtime_override_payload(storage: Mapping[str, Any]) -> dict[str, Any]:
    return runtime_override_payload(storage)

async def _build_runtime_snapshot(tenant: dict[str, Any], config_json: Mapping[str, Any]):
    return await build_runtime_capability_snapshot(
        tenant_id=tenant["id"],
        config_data=config_json,
        plan_code=tenant.get("plan_code") or "trial",
        tenant_status=tenant.get("status") or "trial",
        tenant_name=tenant.get("name") or "",
    )

async def _load_behavior_preset(
    *,
    business_type_key: str,
    storage: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    selection = await select_behavior_preset_for_config(
        config_data={"business_type": business_type_key, "receptionist_behavior": dict(storage)},
        db=db_client,
    )
    warnings: list[str] = []
    if selection.warning_code == "invalid_pinned_behavior_preset":
        warnings.append("pinned_behavior_preset_unavailable")
    elif selection.warning_code:
        warnings.append(selection.warning_code)
    if selection.source == "legacy_fallback":
        return None, warnings
    return dict(selection.preset) if selection.preset else None, warnings

def _preset_profile(preset: dict[str, Any] | None) -> BehaviorProfileDefinition:
    if not preset:
        return LEGACY_SAFE_DEFAULT_PROFILE
    return BehaviorProfileDefinition.model_validate(preset.get("profile") or {})

def _safe_overrides(storage: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    overrides = storage.get("overrides") if isinstance(storage.get("overrides"), Mapping) else {}
    channel_overrides = storage.get("channel_overrides") if isinstance(storage.get("channel_overrides"), Mapping) else {}
    return dict(overrides), dict(channel_overrides)

def _workflow_label(module: str) -> str:
    labels = {
        "faq": "FAQ",
        "human_handoff": "Human Handoff",
        "lead_capture": "Lead Capture",
    }
    return labels.get(module, _labelize_key(module))

async def _build_receptionist_behavior_response(
    *,
    tenant: dict[str, Any],
    config_record: dict[str, Any],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    config_json = _normalize_or_422(_parse_config_json(config_record.get("config_json", {})))
    storage = _behavior_storage_from_config(config_json)
    business_type_key = str(config_json.get("business_type") or "other")
    preset, preset_warnings = await _load_behavior_preset(
        business_type_key=business_type_key,
        storage=storage,
    )
    warning_values = [*(warnings or []), *preset_warnings]
    snapshot = await _build_runtime_snapshot(tenant, config_json)
    inherited = _preset_profile(preset)
    overrides, channel_overrides = _safe_overrides(storage)
    effective = resolve_effective_behavior_profile(
        tenant_config={"receptionist_behavior": _runtime_override_payload(storage)},
        preset=inherited,
        runtime_capability_snapshot=snapshot,
        channel="public_chat",
    )
    editable_fields = tuple(preset.get("tenant_editable_fields") or inherited.tenant_editable_fields) if preset else ()
    locked_fields = {
        field: "not_editable_by_preset"
        for field in sorted({"journey_priority", *PRESENTATION_FIELD_NAMES, "channel_overrides"} - set(editable_fields))
    }
    workflows = [
        {
            "key": module,
            "label": _workflow_label(module),
            "allowed_by_plan": snapshot.capability_for(module).allowed_by_plan,
            "enabled_by_tenant": snapshot.capability_for(module).enabled_by_tenant,
            "runtime_available": snapshot.capability_for(module).runtime_available,
            "selectable_in_journey_priority": snapshot.capability_for(module).runtime_available,
            "reason": snapshot.capability_for(module).reason,
        }
        for module in sorted(KNOWN_RUNTIME_MODULES)
    ]
    preset_data = None
    if preset:
        preset_data = {
            "key": preset["business_type_key"],
            "label": _labelize_key(preset["business_type_key"]),
            "version": preset["version"],
            "status": preset["status"],
        }
    return {
        "config_version": config_record.get("version"),
        "preset": preset_data,
        "preset_available": bool(preset),
        "inherited_profile": inherited.model_dump(mode="json"),
        "overrides": overrides,
        "channel_overrides": channel_overrides,
        "effective_profile": effective.model_dump(mode="json"),
        "editable_fields": list(editable_fields),
        "locked_fields": locked_fields,
        "workflows": workflows,
        "warnings": warning_values,
    }

def _http_validation_error(code: str, message: str, status_code: int = 422) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})

def _validate_editable_behavior_payload(
    *,
    req: TenantReceptionistBehaviorPatchRequest,
    preset: dict[str, Any],
    snapshot,
) -> tuple[dict[str, Any], dict[str, Any]]:
    editable = set(preset.get("tenant_editable_fields") or [])
    overrides = req.overrides.active_values()
    channel_overrides = {
        channel: override.active_values()
        for channel, override in req.channel_overrides.items()
        if override.active_values()
    }
    for field in overrides:
        if field not in editable:
            raise _http_validation_error("behavior_field_not_editable", f"{field} is not editable for this preset.")
    if channel_overrides and "channel_overrides" not in editable:
        raise _http_validation_error("behavior_field_not_editable", "channel_overrides is not editable for this preset.")
    for module in overrides.get("journey_priority") or ():
        if not snapshot.is_available(module):
            capability = snapshot.capability_for(module)
            code = "module_plan_disallowed" if not capability.allowed_by_plan else "module_tenant_disabled"
            raise _http_validation_error(code, f"{module} is not available for receptionist workflow priority.")
    return overrides, channel_overrides

async def _create_and_activate_behavior_config(
    *,
    tenant_id: UUID,
    config_json: dict[str, Any],
) -> dict[str, Any]:
    result = await db_client.create_config_version(tenant_id, config_json)
    new_config_id = result.get("id")
    success = await db_client.activate_config_version(tenant_id, new_config_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to activate new config")
    return result

@router.get("/config/receptionist-behavior")
async def get_receptionist_behavior(user: dict = Depends(require_tenant_admin)):
    tenant = await db_client.get_tenant(user["tenant_id"])
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    config = await db_client.get_tenant_config(user["tenant_id"])
    if not config:
        raise HTTPException(status_code=404, detail="No active config found")
    return APIResponse.create_success(data=await _build_receptionist_behavior_response(tenant=tenant, config_record=config))

@router.patch("/config/receptionist-behavior")
async def patch_receptionist_behavior(
    req: TenantReceptionistBehaviorPatchRequest,
    user: dict = Depends(require_tenant_admin),
):
    await _assert_current_version_or_409(user["tenant_id"], req.expected_config_version)
    tenant = await db_client.get_tenant(user["tenant_id"])
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    config = await db_client.get_tenant_config(user["tenant_id"])
    if not config:
        raise HTTPException(status_code=404, detail="No active config found")
    config_json = _normalize_or_422(_parse_config_json(config.get("config_json", {})))
    storage = _behavior_storage_from_config(config_json)
    preset, warnings = await _load_behavior_preset(business_type_key=str(config_json.get("business_type") or "other"), storage=storage)
    if not preset:
        code = "invalid_pinned_behavior_preset" if "pinned_behavior_preset_unavailable" in warnings else "no_active_behavior_preset"
        message = (
            "The pinned behavior preset is no longer available for editing."
            if code == "invalid_pinned_behavior_preset"
            else "No active behavior preset is available for this business."
        )
        raise _http_validation_error(code, message, status_code=409)
    snapshot = await _build_runtime_snapshot(tenant, config_json)
    overrides, channel_overrides = _validate_editable_behavior_payload(req=req, preset=preset, snapshot=snapshot)
    previous_version = config.get("version")
    next_config = dict(config_json)
    next_config["receptionist_behavior"] = {
        "preset_key": preset["business_type_key"],
        "preset_version": preset["version"],
        "overrides": overrides,
        "channel_overrides": channel_overrides,
    }
    result = await _create_and_activate_behavior_config(tenant_id=user["tenant_id"], config_json=next_config)
    await db_client.write_audit_log(
        tenant_id=user["tenant_id"],
        actor_id=user["user_id"],
        action="tenant_receptionist_behavior_updated",
        resource_type="tenant_config",
        resource_id=result.get("id"),
        details={
            "action": "tenant_receptionist_behavior_updated",
            "preset_key": preset["business_type_key"],
            "preset_version": preset["version"],
            "changed_behavior_fields": sorted(overrides.keys()),
            "changed_channel_fields": sorted({f"{channel}.{field}" for channel, values in channel_overrides.items() for field in values}),
            "previous_config_version": previous_version,
            "new_config_version": result.get("version"),
        },
    )
    new_config = await db_client.get_tenant_config(user["tenant_id"], version=result.get("version"))
    return APIResponse.create_success(data=await _build_receptionist_behavior_response(tenant=tenant, config_record=new_config or {**config, "version": result.get("version"), "config_json": next_config}))

@router.post("/config/receptionist-behavior/reset")
async def reset_receptionist_behavior(
    req: TenantReceptionistBehaviorResetRequest,
    user: dict = Depends(require_tenant_admin),
):
    await _assert_current_version_or_409(user["tenant_id"], req.expected_config_version)
    tenant = await db_client.get_tenant(user["tenant_id"])
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    config = await db_client.get_tenant_config(user["tenant_id"])
    if not config:
        raise HTTPException(status_code=404, detail="No active config found")
    config_json = _normalize_or_422(_parse_config_json(config.get("config_json", {})))
    active_preset = await db_client.get_active_business_behavior_preset(str(config_json.get("business_type") or "other"))
    if not active_preset:
        raise _http_validation_error(
            "no_active_behavior_preset",
            "No active behavior preset is available for this business.",
            status_code=409,
        )
    next_config = dict(config_json)
    next_config["receptionist_behavior"] = {
        "preset_key": active_preset["business_type_key"],
        "preset_version": active_preset["version"],
    }
    result = await _create_and_activate_behavior_config(tenant_id=user["tenant_id"], config_json=next_config)
    await db_client.write_audit_log(
        tenant_id=user["tenant_id"],
        actor_id=user["user_id"],
        action="tenant_receptionist_behavior_reset",
        resource_type="tenant_config",
        resource_id=result.get("id"),
        details={
            "action": "tenant_receptionist_behavior_reset",
            "preset_key": active_preset.get("business_type_key") if active_preset else None,
            "preset_version": active_preset.get("version") if active_preset else None,
            "changed_behavior_fields": [],
            "changed_channel_fields": [],
            "previous_config_version": config.get("version"),
            "new_config_version": result.get("version"),
        },
    )
    new_config = await db_client.get_tenant_config(user["tenant_id"], version=result.get("version"))
    return APIResponse.create_success(data=await _build_receptionist_behavior_response(tenant=tenant, config_record=new_config or {**config, "version": result.get("version"), "config_json": next_config}))
@router.get("/config")
async def get_active_config(user: dict = Depends(get_current_user)):
    """Get the currently active tenant config."""
    tenant = await db_client.get_tenant(user["tenant_id"])
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    config = await db_client.get_tenant_config(user["tenant_id"])
    if not config:
        raise HTTPException(status_code=404, detail="No active config found")
    if isinstance(config.get("config_json"), str):
        config["config_json"] = json.loads(config["config_json"])
    config["config_json"] = _normalize_or_422(config.get("config_json", {}))
    policy, _allowed_modules = await _get_tenant_tool_policy(tenant)
    config["plan_policy"] = policy
    entitlements = await build_tenant_admin_entitlement_summary(user["tenant_id"])
    config["entitlements"] = entitlements.to_public_dict()
    return APIResponse.create_success(data=config)
@router.get("/tools")
async def get_tenant_tools(user: dict = Depends(get_current_user)):
    """Get the tenant-visible workflow tool catalog and enabled state."""
    tenant = await db_client.get_tenant(user["tenant_id"])
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    config = await db_client.get_tenant_config(user["tenant_id"])
    if not config:
        raise HTTPException(status_code=404, detail="No active config found")
    config_json = config.get("config_json", {})
    if isinstance(config_json, str):
        config_json = json.loads(config_json)
    policy, allowed_modules = await _get_tenant_tool_policy(tenant)
    tool_catalog = await _get_tool_catalog_or_none()
    state = build_tool_control_state(
        config_json,
        allowed_modules,
        tool_definitions=tool_catalog,
    )
    entitlements = await build_tenant_admin_entitlement_summary(user["tenant_id"])
    module_entitlements = entitlements.to_public_dict()["modules"]
    for tool in state["tools"]:
        access = module_entitlements.get(tool["key"])
        if access:
            tool.update(access)
    return APIResponse.create_success(data={
        "tenant": tenant,
        "config_version": config.get("version"),
        "plan_policy": policy,
        "entitlements": entitlements.to_public_dict(),
        **state,
    })
@router.post("/config")
async def create_config(
    body: CreateConfigRequest,
    user: dict = Depends(require_tenant_admin)
):
    """Create a new config version (inactive by default)."""
    config_dict = _normalize_or_422(body.config_json)
    await _reject_receptionist_behavior_bypass(user["tenant_id"], config_dict)
    await _assert_ai_mode_allowed(user["tenant_id"], config_dict)
    await assert_config_update_allowed(user["tenant_id"], config_dict)
    result = await db_client.create_config_version(user["tenant_id"], config_dict)
    # Audit log — config creation is a sensitive operation
    await db_client.write_audit_log(
        tenant_id=user["tenant_id"],
        actor_id=user["user_id"],
        action="config_created",
        resource_type="tenant_config",
        resource_id=result.get("id"),
        details={"version": result.get("version")}
    )
    return APIResponse.create_success(data=result)
@router.post("/config/{config_id}/activate")
async def activate_config(
    config_id: UUID,
    user: dict = Depends(require_tenant_admin)
):
    """Activate a specific config version, deactivating all others."""
    success = await db_client.activate_config_version(user["tenant_id"], config_id)
    if not success:
        raise HTTPException(status_code=404, detail="Config version not found")
    activated_config = await db_client.get_config_version(user["tenant_id"], config_id)
    if activated_config:
        config_json = activated_config.get("config_json", {})
        if isinstance(config_json, str):
            config_json = json.loads(config_json)
        await _sync_configured_faq_knowledge(user["tenant_id"], config_json)
    # Audit log — config activation is a sensitive operation
    await db_client.write_audit_log(
        tenant_id=user["tenant_id"],
        actor_id=user["user_id"],
        action="config_activated",
        resource_type="tenant_config",
        resource_id=config_id,
        details={}
    )
    return APIResponse.create_success(data={"activated": str(config_id)})
@router.put("/config")
async def update_and_activate_config(
    body: CreateConfigRequest,
    user: dict = Depends(require_tenant_admin)
):
    """
    Convenience endpoint for admin UI: Creates a new config version and
    activates it immediately in one transaction.
    """
    await _assert_current_version_or_409(user["tenant_id"], body.expected_version)
    # 1. Create new version
    config_dict = _normalize_or_422(body.config_json)
    await _reject_receptionist_behavior_bypass(user["tenant_id"], config_dict)
    await _assert_ai_mode_allowed(user["tenant_id"], config_dict)
    await assert_config_update_allowed(user["tenant_id"], config_dict)
    result = await db_client.create_config_version(user["tenant_id"], config_dict)
    new_config_id = result.get("id")
    # 2. Activate it
    success = await db_client.activate_config_version(user["tenant_id"], new_config_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to activate new config")
    await _sync_configured_faq_knowledge(user["tenant_id"], config_dict)
    # Audit log
    await db_client.write_audit_log(
        tenant_id=user["tenant_id"],
        actor_id=user["user_id"],
        action="config_updated_and_activated",
        resource_type="tenant_config",
        resource_id=new_config_id,
        details={"version": result.get("version")}
    )
    return APIResponse.create_success(data={"activated": str(new_config_id), "version": result.get("version")})
