import json
from typing import Optional
from uuid import UUID
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from backend.api.dependencies import get_current_user, require_tenant_admin
from backend.api.entitlements import require_module_operational_write
from backend.api.pagination import decode_cursor
from backend.domains.orders.service import order_service
from backend.schemas.response import APIResponse
router = APIRouter(prefix="/api/v1", tags=["Orders"])
ORDER_STATUSES = {"new", "accepted", "preparing", "ready", "completed", "cancelled"}
PAYMENT_STATUSES = {"not_collected", "pending", "paid", "failed", "refunded"}
PAYMENT_METHODS = {"cash", "card", "upi", "online", "razorpay", "unknown"}
class UpdateOrderRequest(BaseModel):
    status: Optional[str] = None
    payment_method: Optional[str] = Field(default=None, max_length=40)
    payment_status: Optional[str] = Field(default=None, max_length=40)
    delivery_address: Optional[str] = Field(default=None, max_length=500)
    internal_note: Optional[str] = Field(default=None, max_length=1000)
class OrderActionRequest(BaseModel):
    action: str
    internal_note: Optional[str] = Field(default=None, max_length=1000)
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

def _order_safe_record(record: dict) -> dict:
    data = dict(record)
    for key in ("customer_snapshot", "order_data"):
        if key in data:
            data[key] = _parse_json_value(data[key])

    # Also parse nested items and events if present
    if "items" in data and isinstance(data["items"], list):
        parsed_items = []
        for item in data["items"]:
            item_dict = dict(item)
            if "variant_data" in item_dict:
                item_dict["variant_data"] = _parse_json_value(item_dict["variant_data"])
            parsed_items.append(item_dict)
        data["items"] = parsed_items

    if "events" in data and isinstance(data["events"], list):
        parsed_events = []
        for event in data["events"]:
            event_dict = dict(event)
            if "event_data" in event_dict:
                event_dict["event_data"] = _parse_json_value(event_dict["event_data"])
            parsed_events.append(event_dict)
        data["events"] = parsed_events

    return data

def _order_safe_records(records: list[dict]) -> list[dict]:
    return [_order_safe_record(record) for record in records]
@router.get("/orders")
async def list_orders(
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    fulfillment_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    user: dict = Depends(get_current_user),
):
    if status and status not in ORDER_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid order status: {status}")
    decoded = decode_cursor(cursor) if cursor else None
    result = await order_service.list_orders(
        _tenant_id(user),
        cursor=decoded,
        limit=limit,
        status=status,
        fulfillment_type=fulfillment_type,
        search=search,
        date_from=date_from.isoformat() if date_from else None,
        date_to=date_to.isoformat() if date_to else None,
    )
    return APIResponse.create_success(data=_order_safe_records(result["items"]), meta={"next_cursor": result["next_cursor"]})
@router.get("/orders/{order_id}")
async def get_order(order_id: UUID, user: dict = Depends(get_current_user)):
    order = await order_service.get_order(_tenant_id(user), order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return APIResponse.create_success(data=_order_safe_record(order))
@router.patch("/orders/{order_id}")
async def update_order(order_id: UUID, req: UpdateOrderRequest, user: dict = Depends(require_tenant_admin)):
    await require_module_operational_write(_tenant_id(user), "order")
    if req.status and req.status not in ORDER_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid order status: {req.status}")
    if req.payment_status and req.payment_status not in PAYMENT_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid payment status")
    if req.payment_method and req.payment_method not in PAYMENT_METHODS:
        raise HTTPException(status_code=422, detail="Invalid payment method")
    success = await order_service.update_order(
        _tenant_id(user),
        order_id,
        status=req.status,
        payment_method=req.payment_method,
        payment_status=req.payment_status,
        delivery_address=req.delivery_address,
        internal_note=req.internal_note,
        actor_id=_user_id(user),
    )
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update order or no changes provided")
    return APIResponse.create_success(data={"updated": True, "order_id": str(order_id)})
@router.post("/orders/{order_id}/actions")
async def action_order(order_id: UUID, req: OrderActionRequest, user: dict = Depends(require_tenant_admin)):
    await require_module_operational_write(_tenant_id(user), "order")
    action_to_status = {
        "accept": "accepted",
        "accepted": "accepted",
        "preparing": "preparing",
        "mark_preparing": "preparing",
        "ready": "ready",
        "mark_ready": "ready",
        "complete": "completed",
        "completed": "completed",
        "cancel": "cancelled",
        "cancelled": "cancelled",
    }
    status = action_to_status.get(req.action.strip().lower())
    if not status:
        raise HTTPException(status_code=422, detail=f"Unsupported order action: {req.action}")
    success = await order_service.update_order(
        _tenant_id(user),
        order_id,
        status=status,
        internal_note=req.internal_note,
        actor_id=_user_id(user),
    )
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update order")
    return APIResponse.create_success(data={"updated": True, "order_id": str(order_id), "status": status})
