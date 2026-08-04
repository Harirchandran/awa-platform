"""
Leads API Routes
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field
from backend.api.dependencies import get_current_user, require_tenant_operator
from backend.api.entitlements import require_module_operational_write
from backend.api.pagination import decode_cursor
from backend.domains.leads.service import lead_service
from backend.schemas.response import APIResponse

router = APIRouter(prefix="/api/v1", tags=["Leads"])

LEAD_STATUSES = {"new", "contacted", "converted", "lost"}

class UpdateLeadRequest(BaseModel):
    status: str = Field(..., description="Lead status: new, contacted, converted, or lost")

@router.get("/leads")
async def list_leads(
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="Filter by status"),
    search: Optional[str] = Query(None, description="Search term"),
    user: dict = Depends(get_current_user)
):
    """List leads for the current tenant with cursor pagination and optional status filter."""
    if status and status.lower() not in LEAD_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid lead status")

    decoded = decode_cursor(cursor) if cursor else None
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")

    result = await lead_service.list_leads(
        tenant_id=tenant_id,
        cursor=decoded,
        limit=limit,
        status=status.lower() if status else None,
        search=search
    )
    return APIResponse.create_success(data=result["items"], meta={"next_cursor": result["next_cursor"]})

@router.get("/leads/{lead_id}")
async def get_lead(
    lead_id: UUID,
    user: dict = Depends(get_current_user)
):
    """Get details of a specific lead by ID."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    lead = await lead_service.get_lead(tenant_id, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return APIResponse.create_success(data=lead)

@router.patch("/leads/{lead_id}")
async def update_lead_status(
    lead_id: UUID,
    req: UpdateLeadRequest,
    user: dict = Depends(require_tenant_operator)
):
    """Update status of a specific lead."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    await require_module_operational_write(tenant_id, "lead_capture")
    status = req.status.strip().lower()
    if status not in LEAD_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid lead status")

    success = await lead_service.update_lead_status(tenant_id, lead_id, status)
    if not success:
        raise HTTPException(status_code=404, detail="Lead not found or failed to update")

    return APIResponse.create_success(data={"updated": True, "lead_id": str(lead_id), "status": status})
