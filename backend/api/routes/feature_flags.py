"""
Feature Flags API Routes
GET /api/v1/feature-flags — list flags for tenant
PUT /api/v1/feature-flags/{key} — toggle a feature flag
"""
from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel
from typing import Annotated, Optional, Dict, Any
from backend.api.dependencies import get_current_user, require_tenant_admin
from backend.db.client import db_client
from backend.schemas.response import APIResponse
router = APIRouter(prefix="/api/v1", tags=["Feature Flags"])
class ToggleFlagRequest(BaseModel):
    enabled: bool
    metadata: Optional[Dict[str, Any]] = None
@router.get("/feature-flags")
async def list_flags(user: dict = Depends(get_current_user)):
    """List all feature flags for the current tenant."""
    flags = await db_client.list_feature_flags(user["tenant_id"])
    return APIResponse.create_success(data=flags)
@router.put("/feature-flags/{feature_key}")
async def toggle_flag(
    feature_key: Annotated[str, Path(min_length=1, max_length=80, pattern=r"^[a-z0-9_.-]+$")],
    body: ToggleFlagRequest,
    user: dict = Depends(require_tenant_admin)
):
    """Toggle a feature flag (upsert)."""
    result = await db_client.upsert_feature_flag(
        user["tenant_id"], feature_key, body.enabled, body.metadata
    )
    # Audit log — feature flag changes are sensitive
    await db_client.write_audit_log(
        tenant_id=user["tenant_id"],
        actor_id=user["user_id"],
        action="feature_flag_toggled",
        resource_type="feature_flag",
        details={"key": feature_key, "enabled": body.enabled}
    )
    return APIResponse.create_success(data=result)