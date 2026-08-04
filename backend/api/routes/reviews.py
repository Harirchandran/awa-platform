"""Review collection API routes for private ratings and public review routing."""
import json
from typing import Optional
from uuid import UUID
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from backend.api.dependencies import get_current_user, require_tenant_admin
from backend.api.entitlements import require_module_config_write, require_module_operational_write
from backend.api.pagination import decode_cursor
from backend.db.client import db_client
from backend.domains.reviews.service import review_service
from backend.schemas.response import APIResponse
from backend.schemas.review import (
    ReviewActionRequest,
    ReviewRequestCreateRequest,
    ReviewRequestUpdateRequest,
    ReviewSettingsUpdateRequest,
)
router = APIRouter(prefix="/api/v1", tags=["Reviews"])
REVIEW_STATUSES = {
    "draft",
    "scheduled",
    "sent",
    "responded",
    "public_review_requested",
    "negative_feedback_escalated",
    "skipped",
    "expired",
}
REVIEW_SOURCES = {"manual", "booking", "order", "reminder", "automation"}
def _tenant_id(user: dict) -> UUID:
    return UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
def _user_id(user: dict) -> Optional[UUID]:
    value = user.get("user_id")
    return UUID(value) if isinstance(value, str) else value
def _parse_json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
def _review_safe_record(record: dict) -> dict:
    data = dict(record)
    for key in ("metadata", "event_data"):
        if key in data:
            data[key] = _parse_json_value(data[key])
    return data
def _review_safe_records(records: list[dict]) -> list[dict]:
    return [_review_safe_record(record) for record in records]
@router.get("/reviews/settings")
async def get_review_settings(user: dict = Depends(get_current_user)):
    settings = await review_service.get_review_settings(_tenant_id(user))
    return APIResponse.create_success(data=_review_safe_record(settings))
@router.put("/reviews/settings")
async def update_review_settings(
    req: ReviewSettingsUpdateRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_config_write(_tenant_id(user), "reviews")
    settings = await review_service.update_review_settings(
        _tenant_id(user),
        req.model_dump(exclude_unset=True),
    )
    await db_client.write_audit_log(
        tenant_id=_tenant_id(user),
        actor_id=_user_id(user),
        action="review_settings_updated",
        resource_type="review_settings",
        resource_id=settings["id"],
        details={"public_review_url_set": bool(settings.get("public_review_url"))},
    )
    return APIResponse.create_success(data=_review_safe_record(settings))
@router.get("/reviews/metrics")
async def get_review_metrics(user: dict = Depends(get_current_user)):
    metrics = await review_service.get_review_metrics(_tenant_id(user))
    return APIResponse.create_success(data=metrics)


@router.get("/reviews")
async def list_reviews(
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    rating: Optional[int] = Query(None, ge=1, le=5),
    customer_id: Optional[UUID] = Query(None),
    booking_id: Optional[UUID] = Query(None),
    order_id: Optional[UUID] = Query(None),
    search: Optional[str] = Query(None, max_length=120),
    user: dict = Depends(get_current_user),
):
    if status and status not in REVIEW_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid review status")
    if source and source not in REVIEW_SOURCES:
        raise HTTPException(status_code=422, detail="Invalid review source")
    decoded = decode_cursor(cursor) if cursor else None
    result = await review_service.list_review_requests(
        _tenant_id(user),
        cursor=decoded,
        limit=limit,
        status=status,
        source=source,
        rating=rating,
        customer_id=customer_id,
        booking_id=booking_id,
        order_id=order_id,
        search=search,
    )
    return APIResponse.create_success(
        data=_review_safe_records(result["items"]),
        meta={"next_cursor": result["next_cursor"]},
    )
@router.post("/reviews")
async def create_review(
    req: ReviewRequestCreateRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_operational_write(_tenant_id(user), "reviews")
    try:
        review = await review_service.create_review_request(
            _tenant_id(user),
            status=req.status,
            source=req.source,
            customer_id=req.customer_id,
            conversation_id=req.conversation_id,
            booking_id=req.booking_id,
            order_id=req.order_id,
            public_review_url=req.public_review_url,
            metadata=req.metadata,
            idempotency_key=req.idempotency_key,
            created_by=_user_id(user),
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="Duplicate review request") from exc
    if not review:
        raise HTTPException(status_code=404, detail="Linked customer, conversation, booking, or order was not found")
    await db_client.write_audit_log(
        tenant_id=_tenant_id(user),
        actor_id=_user_id(user),
        action="review_request_created",
        resource_type="review_request",
        resource_id=review["id"],
        details={"status": review["status"], "source": review["source"]},
    )
    return APIResponse.create_success(data=_review_safe_record(review))
@router.get("/reviews/{review_id}")
async def get_review(review_id: UUID, user: dict = Depends(get_current_user)):
    review = await review_service.get_review_request(_tenant_id(user), review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review request not found")
    return APIResponse.create_success(data=_review_safe_record(review))
@router.patch("/reviews/{review_id}")
async def update_review(
    review_id: UUID,
    req: ReviewRequestUpdateRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_operational_write(_tenant_id(user), "reviews")
    review = await review_service.update_review_request(
        _tenant_id(user),
        review_id,
        req.model_dump(exclude_unset=True),
    )
    if not review:
        raise HTTPException(status_code=404, detail="Review request not found or no editable changes provided")
    return APIResponse.create_success(data=_review_safe_record(review))
@router.post("/reviews/{review_id}/actions")
async def action_review(
    review_id: UUID,
    req: ReviewActionRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_operational_write(_tenant_id(user), "reviews")
    review = await review_service.action_review_request(
        _tenant_id(user),
        review_id,
        action=req.action,
        actor_id=_user_id(user),
        rating=req.rating,
        feedback_text=req.feedback_text,
        public_review_url=req.public_review_url,
        note=req.note,
        metadata=req.metadata,
    )
    if not review:
        raise HTTPException(status_code=404, detail="Review request not found or action not supported")
    await db_client.write_audit_log(
        tenant_id=_tenant_id(user),
        actor_id=_user_id(user),
        action=f"review_request_{req.action}",
        resource_type="review_request",
        resource_id=review_id,
        details={"status": review["status"], "rating": review.get("rating"), "note": req.note},
    )
    return APIResponse.create_success(data=_review_safe_record(review))
@router.get("/reviews/{review_id}/events")
async def list_review_events(
    review_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    review = await review_service.get_review_request(_tenant_id(user), review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review request not found")
    events = await review_service.list_review_events(_tenant_id(user), review_id, limit=limit)
    return APIResponse.create_success(data=_review_safe_records(events))


@router.delete("/reviews/{review_id}")
async def delete_review(
    review_id: UUID,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_operational_write(_tenant_id(user), "reviews")
    success = await review_service.delete_review_request(_tenant_id(user), review_id)
    if not success:
        raise HTTPException(status_code=404, detail="Review request not found")
    await db_client.write_audit_log(
        tenant_id=_tenant_id(user),
        actor_id=_user_id(user),
        action="review_request_deleted",
        resource_type="review_request",
        resource_id=review_id,
        details={},
    )
    return APIResponse.create_success(data={"success": True})
