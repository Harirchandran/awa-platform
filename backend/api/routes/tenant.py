"""
Tenant & User API Routes
GET /api/v1/tenant — current tenant info
GET /api/v1/me — current user info
"""
from fastapi import APIRouter, Depends, HTTPException
from uuid import UUID
from backend.api.dependencies import get_current_user
from backend.core.ai_modes import normalize_ai_mode
from backend.db.client import db_client
from backend.schemas.response import APIResponse
router = APIRouter(prefix="/api/v1", tags=["Tenant"])
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
class TenantResponse(BaseModel):
    id: str
    name: str
    slug: Optional[str]
    plan_code: str
    status: str
    created_at: str
class UserResponse(BaseModel):
    id: str
    phone_number: str
    email: Optional[str]
    name: Optional[str]
    created_at: str
    role: str
@router.get("/tenant")
async def get_tenant(user: dict = Depends(get_current_user)):
    """Get current tenant information."""
    tenant = await db_client.get_tenant(user["tenant_id"])
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    # Serialize UUIDs
    tenant = {k: str(v) if isinstance(v, UUID) else v for k, v in tenant.items()}
    tenant["ai_mode"] = normalize_ai_mode(tenant.get("ai_mode"))
    return APIResponse.create_success(data=tenant)
@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Get current user information with role."""
    user_info = await db_client.get_user(user["user_id"])
    if not user_info:
        raise HTTPException(status_code=404, detail="User not found")
    user_info = {k: str(v) if isinstance(v, UUID) else v for k, v in user_info.items()}
    user_info["tenant_id"] = str(user["tenant_id"])
    user_info["role"] = await db_client.get_user_tenant_role(user["user_id"], user["tenant_id"]) or "member"
    return APIResponse.create_success(data=user_info)
