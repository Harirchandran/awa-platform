from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from backend.api.dependencies import require_super_admin
from backend.db.client import db_client
from backend.schemas.behavior_profile import (
    BusinessBehaviorPresetActivateRequest,
    BusinessBehaviorPresetCreateRequest,
    BusinessBehaviorPresetDeactivateRequest,
    BusinessBehaviorPresetListResponse,
    BusinessBehaviorPresetValidateRequest,
    BusinessBehaviorPresetValidationResponse,
    BusinessBehaviorPresetVersionResponse,
    normalize_behavior_business_type_key,
)
from backend.schemas.response import APIResponse


router = APIRouter(prefix="/admin/business-behavior-presets", tags=["Super Admin"])


def _safe_record(record: dict[str, Any]) -> dict[str, Any]:
    allowed = BusinessBehaviorPresetVersionResponse.model_fields.keys()
    public_record = {key: value for key, value in record.items() if key in allowed}
    return BusinessBehaviorPresetVersionResponse.model_validate(public_record).model_dump(mode="json")


def _safe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    response = BusinessBehaviorPresetListResponse(
        presets=tuple(BusinessBehaviorPresetVersionResponse.model_validate(_safe_record(record)) for record in records)
    )
    return response.model_dump(mode="json")["presets"]


def _normalize_key_or_400(business_type_key: str) -> str:
    try:
        return normalize_behavior_business_type_key(business_type_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _require_business_type_exists(business_type_key: str) -> None:
    if not await db_client.get_business_type_by_code(business_type_key):
        raise HTTPException(status_code=404, detail=f"Business type '{business_type_key}' not found")


async def _audit_behavior_preset(
    *,
    admin: dict,
    action: str,
    business_type_key: str,
    version: int,
    details: dict[str, Any],
) -> None:
    safe_details = {
        "business_type_key": business_type_key,
        "version": version,
        "action": action,
        **details,
    }
    await db_client.write_audit_log(
        tenant_id=admin["tenant_id"],
        actor_id=admin["user_id"],
        action=action,
        resource_type="business_behavior_preset",
        resource_id=None,
        details=safe_details,
    )


@router.get("", response_model=APIResponse)
async def list_business_behavior_presets(
    business_type_key: str | None = Query(default=None),
    admin: dict = Depends(require_super_admin),
):
    normalized_key = _normalize_key_or_400(business_type_key) if business_type_key else None
    records = await db_client.list_business_behavior_presets(normalized_key)
    return APIResponse.create_success(data=_safe_records(records))


@router.get("/{business_type_key}", response_model=APIResponse)
async def list_business_behavior_presets_for_type(
    business_type_key: str,
    admin: dict = Depends(require_super_admin),
):
    normalized_key = _normalize_key_or_400(business_type_key)
    records = await db_client.list_business_behavior_presets_for_type(normalized_key)
    return APIResponse.create_success(data=_safe_records(records))


@router.get("/{business_type_key}/active", response_model=APIResponse)
async def get_active_business_behavior_preset(
    business_type_key: str,
    admin: dict = Depends(require_super_admin),
):
    normalized_key = _normalize_key_or_400(business_type_key)
    record = await db_client.get_active_business_behavior_preset(normalized_key)
    if not record:
        raise HTTPException(status_code=404, detail="No active behavior preset found")
    return APIResponse.create_success(data=_safe_record(record))


@router.post("/validate", response_model=APIResponse)
async def validate_business_behavior_preset(
    req: BusinessBehaviorPresetValidateRequest,
    admin: dict = Depends(require_super_admin),
):
    await _require_business_type_exists(req.business_type_key)
    profile = req.normalized_profile()
    response = BusinessBehaviorPresetValidationResponse(
        valid=True,
        profile=profile,
        tenant_editable_fields=profile.tenant_editable_fields,
    )
    return APIResponse.create_success(data=response.model_dump(mode="json"))


@router.post("", response_model=APIResponse)
async def create_business_behavior_preset(
    req: BusinessBehaviorPresetCreateRequest,
    admin: dict = Depends(require_super_admin),
):
    await _require_business_type_exists(req.business_type_key)
    profile = req.normalized_profile()
    try:
        record = await db_client.create_business_behavior_preset_version(
            business_type_key=req.business_type_key,
            profile_json=profile.model_dump(mode="json"),
            version=req.version,
            status=req.status,
            actor=admin["user_id"],
            change_note=req.change_note,
        )
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _audit_behavior_preset(
        admin=admin,
        action="business_behavior_preset_created",
        business_type_key=record["business_type_key"],
        version=record["version"],
        details={
            "status": record["status"],
            "is_active": record["is_active"],
            "tenant_editable_fields": record["tenant_editable_fields"],
            "change_note": req.change_note,
        },
    )
    return APIResponse.create_success(data=_safe_record(record))


@router.post("/{business_type_key}/versions/{version}/activate", response_model=APIResponse)
async def activate_business_behavior_preset(
    business_type_key: str,
    version: int,
    req: BusinessBehaviorPresetActivateRequest | None = None,
    admin: dict = Depends(require_super_admin),
):
    normalized_key = _normalize_key_or_400(business_type_key)
    body = req or BusinessBehaviorPresetActivateRequest()
    try:
        record = await db_client.activate_business_behavior_preset_version(
            normalized_key,
            version,
            actor=admin["user_id"],
            change_note=body.change_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not record:
        raise HTTPException(status_code=404, detail="Behavior preset version not found")
    await _audit_behavior_preset(
        admin=admin,
        action="business_behavior_preset_activated",
        business_type_key=normalized_key,
        version=version,
        details={
            "previous_active_version": record.get("previous_active_version"),
            "new_active_version": record.get("new_active_version"),
            "change_note": body.change_note,
        },
    )
    return APIResponse.create_success(data=_safe_record(record))


@router.post("/{business_type_key}/versions/{version}/deactivate", response_model=APIResponse)
async def deactivate_business_behavior_preset(
    business_type_key: str,
    version: int,
    req: BusinessBehaviorPresetDeactivateRequest | None = None,
    admin: dict = Depends(require_super_admin),
):
    normalized_key = _normalize_key_or_400(business_type_key)
    body = req or BusinessBehaviorPresetDeactivateRequest()
    record = await db_client.deactivate_business_behavior_preset_version(
        normalized_key,
        version,
        actor=admin["user_id"],
        status=body.status,
        change_note=body.change_note,
    )
    if not record:
        raise HTTPException(status_code=404, detail="Behavior preset version not found")
    await _audit_behavior_preset(
        admin=admin,
        action="business_behavior_preset_deactivated",
        business_type_key=normalized_key,
        version=version,
        details={
            "status": record["status"],
            "is_active": record["is_active"],
            "change_note": body.change_note,
        },
    )
    return APIResponse.create_success(data=_safe_record(record))
