"""
Audit Log API Routes
GET /api/v1/audit-logs — list audit logs with cursor pagination
"""
from fastapi import APIRouter, Depends, Query
from typing import Optional
from backend.api.dependencies import get_current_user
from backend.api.pagination import decode_cursor
from backend.db.client import db_client
from backend.schemas.response import APIResponse
router = APIRouter(prefix="/api/v1", tags=["Audit Logs"])
@router.get("/audit-logs")
async def list_audit_logs(
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    limit: int = Query(50, ge=1, le=200),
    action_type: Optional[str] = Query(None, description="Filter by exact action string"),
    date_from: Optional[str] = Query(None, description="Filter from date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="Filter to date YYYY-MM-DD"),
    user: dict = Depends(get_current_user)
):
    """List audit logs for the current tenant with optional filters."""
    decoded = decode_cursor(cursor) if cursor else None
    result = await db_client.list_audit_logs(
        user["tenant_id"], cursor=decoded, limit=limit,
        action_type=action_type, date_from=date_from, date_to=date_to
    )
    return APIResponse.create_success(data=result["items"], meta={"next_cursor": result["next_cursor"]})