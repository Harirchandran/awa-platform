"""Payment request API routes for tenant customer payments and deposits."""
import json
from decimal import Decimal
from typing import Optional
from uuid import UUID
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from backend.api.dependencies import get_current_user, require_tenant_admin
from backend.api.entitlements import require_module_operational_write
from backend.api.pagination import decode_cursor
from backend.db.client import db_client
from backend.domains.payments.service import payment_service
from backend.schemas.payment import (
    PaymentActionRequest,
    PaymentRequestCreateRequest,
    PaymentRequestUpdateRequest,
)
from backend.schemas.response import APIResponse
router = APIRouter(prefix="/api/v1", tags=["Payments"])
PAYMENT_STATUSES = {"draft", "sent", "viewed", "paid", "expired", "cancelled", "failed", "refunded"}
PAYMENT_TYPES = {"booking_deposit", "booking_full_payment", "order_payment", "custom_payment", "balance_due"}
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
def _number_value(value):
    if isinstance(value, Decimal):
        return float(value)
    return value
def _payment_safe_record(record: dict) -> dict:
    data = dict(record)
    for key in ("metadata", "event_data", "provider_payload"):
        if key in data:
            data[key] = _parse_json_value(data[key])
    for key in ("amount",):
        if key in data:
            data[key] = _number_value(data[key])
    return data
def _payment_safe_records(records: list[dict]) -> list[dict]:
    return [_payment_safe_record(record) for record in records]
@router.get("/payments")
async def list_payments(
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    customer_id: Optional[UUID] = Query(None),
    booking_id: Optional[UUID] = Query(None),
    order_id: Optional[UUID] = Query(None),
    search: Optional[str] = Query(None, max_length=120),
    user: dict = Depends(get_current_user),
):
    if status and status not in PAYMENT_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid payment status")
    if type and type not in PAYMENT_TYPES:
        raise HTTPException(status_code=422, detail="Invalid payment type")
    decoded = decode_cursor(cursor) if cursor else None
    result = await payment_service.list_payment_requests(
        _tenant_id(user),
        cursor=decoded,
        limit=limit,
        status=status,
        payment_type=type,
        customer_id=customer_id,
        booking_id=booking_id,
        order_id=order_id,
        search=search,
    )
    return APIResponse.create_success(
        data=_payment_safe_records(result["items"]),
        meta={"next_cursor": result["next_cursor"]},
    )
@router.get("/payments/metrics")
async def get_payment_metrics(
    user: dict = Depends(get_current_user),
):
    metrics = await payment_service.get_payment_metrics(_tenant_id(user))
    return APIResponse.create_success(data=metrics)
@router.post("/payments")
async def create_payment(
    req: PaymentRequestCreateRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_operational_write(_tenant_id(user), "payments")
    try:
        payment = await payment_service.create_payment_request(
            _tenant_id(user),
            amount=req.amount,
            currency=req.currency,
            payment_type=req.type,
            provider=req.provider,
            customer_id=req.customer_id,
            conversation_id=req.conversation_id,
            booking_id=req.booking_id,
            order_id=req.order_id,
            payment_url=req.payment_url,
            expires_at=req.expires_at,
            metadata=req.metadata,
            idempotency_key=req.idempotency_key,
            created_by=_user_id(user),
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="Duplicate payment request") from exc
    except asyncpg.ForeignKeyViolationError as exc:
        raise HTTPException(status_code=400, detail="Invalid reference: the provided customer, conversation, booking, or order ID does not exist.")
    if not payment:
        raise HTTPException(status_code=404, detail="Linked customer, booking, order, or amount was not valid")
    await db_client.write_audit_log(
        tenant_id=_tenant_id(user),
        actor_id=_user_id(user),
        action="payment_request_created",
        resource_type="payment_request",
        resource_id=payment["id"],
        details={"type": payment["type"], "amount": payment["amount"], "currency": payment["currency"]},
    )
    return APIResponse.create_success(data=_payment_safe_record(payment))
@router.get("/payments/{payment_id}")
async def get_payment(payment_id: UUID, user: dict = Depends(get_current_user)):
    payment = await payment_service.get_payment_request(_tenant_id(user), payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment request not found")
    return APIResponse.create_success(data=_payment_safe_record(payment))
@router.patch("/payments/{payment_id}")
async def update_payment(
    payment_id: UUID,
    req: PaymentRequestUpdateRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_operational_write(_tenant_id(user), "payments")
    payment = await payment_service.update_payment_request(
        _tenant_id(user),
        payment_id,
        req.model_dump(exclude_unset=True),
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Payment request not found or no editable changes provided")
    return APIResponse.create_success(data=_payment_safe_record(payment))
@router.post("/payments/{payment_id}/actions")
async def action_payment(
    payment_id: UUID,
    req: PaymentActionRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_operational_write(_tenant_id(user), "payments")
    payment = await payment_service.action_payment_request(
        _tenant_id(user),
        payment_id,
        action=req.action,
        actor_id=_user_id(user),
        note=req.note,
        provider_payment_id=req.provider_payment_id,
        payment_url=req.payment_url,
        metadata=req.metadata,
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Payment request not found or action not supported")
    await db_client.write_audit_log(
        tenant_id=_tenant_id(user),
        actor_id=_user_id(user),
        action=f"payment_request_{req.action}",
        resource_type="payment_request",
        resource_id=payment_id,
        details={"status": payment["status"], "note": req.note},
    )
    return APIResponse.create_success(data=_payment_safe_record(payment))
@router.get("/payments/{payment_id}/events")
async def list_payment_events(
    payment_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    payment = await payment_service.get_payment_request(_tenant_id(user), payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment request not found")
    events = await payment_service.list_payment_events(_tenant_id(user), payment_id, limit=limit)
    return APIResponse.create_success(data=_payment_safe_records(events))
