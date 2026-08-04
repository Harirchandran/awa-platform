"""
Booking API Routes
GET /api/v1/bookings — list bookings with cursor pagination
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional
from uuid import UUID
from datetime import date, time
from pydantic import BaseModel
from backend.api.dependencies import get_current_user, require_tenant_admin
from backend.api.entitlements import require_module_operational_write
from backend.api.pagination import decode_cursor
from backend.domains.bookings.service import booking_service
from backend.schemas.response import APIResponse
router = APIRouter(prefix="/api/v1", tags=["Bookings"])
BOOKING_STATUSES = {"confirmed", "cancelled"}
class UpdateBookingRequest(BaseModel):
    status: Optional[str] = None
    booking_date: Optional[date] = None
    booking_time: Optional[time] = None
@router.get("/bookings")
async def list_bookings(
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="Filter by booking status"),
    search: Optional[str] = Query(None, description="Search term"),
    date_from: Optional[date] = Query(None, description="Filter by date from (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="Filter by date to (YYYY-MM-DD)"),
    user: dict = Depends(get_current_user)
):
    """List bookings for the current tenant with cursor pagination and optional filters."""
    if status and status not in BOOKING_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid booking status")
    decoded = decode_cursor(cursor) if cursor else None
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    result = await booking_service.list_bookings(
        tenant_id, cursor=decoded, limit=limit,
        status=status, search=search,
        date_from=date_from.isoformat() if date_from else None,
        date_to=date_to.isoformat() if date_to else None
    )
    return APIResponse.create_success(data=result["items"], meta={"next_cursor": result["next_cursor"]})
@router.get("/bookings/{booking_id}")
async def get_booking(
    booking_id: UUID,
    user: dict = Depends(get_current_user)
):
    """Get a specific booking by ID."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    booking = await booking_service.get_booking(tenant_id, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    return APIResponse.create_success(data=booking)
@router.patch("/bookings/{booking_id}")
async def update_booking(
    booking_id: UUID,
    req: UpdateBookingRequest,
    user: dict = Depends(require_tenant_admin)
):
    """Update a booking (e.g. Cancel or Reschedule)."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    await require_module_operational_write(tenant_id, "booking")
    if req.status and req.status not in BOOKING_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid booking status")
    try:
        success = await booking_service.update_booking(
            tenant_id, booking_id,
            status=req.status,
            booking_date=req.booking_date.isoformat() if req.booking_date else None,
            booking_time=req.booking_time.isoformat() if req.booking_time else None
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update booking or no changes provided")
    return APIResponse.create_success(data={"updated": True, "booking_id": str(booking_id)})
