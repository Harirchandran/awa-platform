"""Reminder and follow-up scheduling API routes."""
import json
from typing import Optional
from uuid import UUID
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from backend.api.dependencies import get_current_user, require_tenant_admin
from backend.api.entitlements import require_module_config_write, require_module_operational_write
from backend.api.pagination import decode_cursor
from backend.db.client import db_client
from backend.domains.reminders.service import reminder_service
from backend.ops.logger import logger
from backend.schemas.reminder import (
    ReminderRuleCreateRequest,
    ReminderRuleUpdateRequest,
    ScheduledMessageActionRequest,
    ScheduledMessageCreateRequest,
)
from backend.schemas.response import APIResponse
router = APIRouter(prefix="/api/v1", tags=["Reminders"])
REMINDER_SCHEMA_UNAVAILABLE_DETAIL = (
    "Reminder storage is unavailable or out of date. "
    "Run the reminder schema migrations before using reminders."
)
REMINDER_STATUSES = {"scheduled", "due", "snoozed", "sent", "failed", "cancelled", "skipped"}
REMINDER_TRIGGERS = {
    "manual",
    "before_booking",
    "after_booking",
    "before_payment_expiry",
    "after_payment_failed",
    "after_order_completed",
    "inactive_conversation",
    "custom",
}
REMINDER_CHANNELS = {"whatsapp", "email", "manual"}
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
def _reminder_safe_record(record: dict) -> dict:
    data = dict(record)
    for key in ("metadata", "event_data"):
        if key in data:
            data[key] = _parse_json_value(data[key])
    return data
def _reminder_safe_records(records: list[dict]) -> list[dict]:
    return [_reminder_safe_record(record) for record in records]
async def _run_reminder_db(operation, *args, **kwargs):
    try:
        return await operation(*args, **kwargs)
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
        logger.error("Reminder schema is unavailable or out of date", exc_info=True)
        raise HTTPException(status_code=503, detail=REMINDER_SCHEMA_UNAVAILABLE_DETAIL) from exc
@router.get("/reminder-rules")
async def list_reminder_rules(
    active_only: bool = Query(False),
    user: dict = Depends(get_current_user),
):
    rules = await _run_reminder_db(db_client.list_reminder_rules, _tenant_id(user), active_only=active_only)
    return APIResponse.create_success(data=_reminder_safe_records(rules))
@router.post("/reminder-rules")
async def create_reminder_rule(
    req: ReminderRuleCreateRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_config_write(_tenant_id(user), "reminders")
    rule = await _run_reminder_db(
        db_client.create_reminder_rule,
        _tenant_id(user),
        name=req.name,
        trigger_type=req.trigger_type,
        offset_minutes=req.offset_minutes,
        channel=req.channel,
        template_text=req.template_text,
        is_active=req.is_active,
        quiet_hours_start=req.quiet_hours_start,
        quiet_hours_end=req.quiet_hours_end,
        metadata=req.metadata,
    )
    return APIResponse.create_success(data=_reminder_safe_record(rule))
@router.patch("/reminder-rules/{rule_id}")
async def update_reminder_rule(
    rule_id: UUID,
    req: ReminderRuleUpdateRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_config_write(_tenant_id(user), "reminders")
    rule = await _run_reminder_db(
        db_client.update_reminder_rule,
        _tenant_id(user),
        rule_id,
        req.model_dump(exclude_unset=True),
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Reminder rule not found or no changes provided")
    return APIResponse.create_success(data=_reminder_safe_record(rule))
@router.delete("/reminder-rules/{rule_id}")
async def delete_reminder_rule(rule_id: UUID, user: dict = Depends(require_tenant_admin)):
    await require_module_config_write(_tenant_id(user), "reminders")
    success = await _run_reminder_db(db_client.delete_reminder_rule, _tenant_id(user), rule_id)
    if not success:
        raise HTTPException(status_code=404, detail="Reminder rule not found")
    return APIResponse.create_success(data={"deleted": True, "rule_id": str(rule_id)})
@router.get("/scheduled-messages")
async def list_scheduled_messages(
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    trigger_type: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    customer_id: Optional[UUID] = Query(None),
    booking_id: Optional[UUID] = Query(None),
    order_id: Optional[UUID] = Query(None),
    payment_request_id: Optional[UUID] = Query(None),
    due_before: Optional[str] = Query(None),
    search: Optional[str] = Query(None, max_length=120),
    user: dict = Depends(get_current_user),
):
    if status and status not in REMINDER_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid scheduled message status")
    if trigger_type and trigger_type not in REMINDER_TRIGGERS:
        raise HTTPException(status_code=422, detail="Invalid reminder trigger")
    if channel and channel not in REMINDER_CHANNELS:
        raise HTTPException(status_code=422, detail="Invalid reminder channel")
    decoded = decode_cursor(cursor) if cursor else None
    result = await _run_reminder_db(
        reminder_service.list_scheduled_messages,
        _tenant_id(user),
        cursor=decoded,
        limit=limit,
        status=status,
        trigger_type=trigger_type,
        channel=channel,
        customer_id=customer_id,
        booking_id=booking_id,
        order_id=order_id,
        payment_request_id=payment_request_id,
        due_before=due_before,
        search=search,
    )
    return APIResponse.create_success(
        data=_reminder_safe_records(result["items"]),
        meta={"next_cursor": result["next_cursor"]},
    )
@router.get("/scheduled-messages/metrics")
async def get_scheduled_message_metrics(
    user: dict = Depends(get_current_user),
):
    tenant_id = _tenant_id(user)
    try:
        async with db_client.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT status, COUNT(*) as count
                FROM scheduled_messages
                WHERE tenant_id = $1
                GROUP BY status
                """,
                tenant_id,
            )
            due_row = await conn.fetchrow(
                """
                SELECT COUNT(*) as count
                FROM scheduled_messages
                WHERE tenant_id = $1
                  AND status IN ('scheduled', 'due', 'failed')
                  AND scheduled_for <= NOW()
                """,
                tenant_id,
            )
    except Exception as exc:
        logger.error("Failed to query scheduled message metrics", exc_info=True)
        raise HTTPException(status_code=503, detail=REMINDER_SCHEMA_UNAVAILABLE_DETAIL) from exc

    status_counts = {row["status"]: row["count"] for row in rows}

    scheduled_count = status_counts.get("scheduled", 0) + status_counts.get("snoozed", 0)
    failed_count = status_counts.get("failed", 0)
    sent_count = status_counts.get("sent", 0)
    skipped_count = status_counts.get("skipped", 0)
    due_count = due_row["count"] if due_row else 0

    data = {
        "scheduled": scheduled_count,
        "due": due_count,
        "failed": failed_count,
        "sent": sent_count,
        "skipped": skipped_count,
    }
    return APIResponse.create_success(data=data)
@router.post("/scheduled-messages")
async def create_scheduled_message(
    req: ScheduledMessageCreateRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_operational_write(_tenant_id(user), "reminders")
    try:
        message = await _run_reminder_db(
            reminder_service.create_scheduled_message,
            _tenant_id(user),
            rule_id=req.rule_id,
            customer_id=req.customer_id,
            conversation_id=req.conversation_id,
            booking_id=req.booking_id,
            order_id=req.order_id,
            payment_request_id=req.payment_request_id,
            review_request_id=req.review_request_id,
            trigger_type=req.trigger_type,
            channel=req.channel,
            message_text=req.message_text,
            scheduled_for=req.scheduled_for,
            idempotency_key=req.idempotency_key,
            metadata=req.metadata,
            created_by=_user_id(user),
        )
    except asyncpg.ForeignKeyViolationError as exc:
        raise HTTPException(status_code=400, detail="Invalid reference: the provided ID does not exist.") from exc
    if not message:
        raise HTTPException(status_code=404, detail="Target record, customer, or reminder rule was not found")
    return APIResponse.create_success(data=_reminder_safe_record(message))
@router.get("/scheduled-messages/{message_id}")
async def get_scheduled_message(message_id: UUID, user: dict = Depends(get_current_user)):
    message = await _run_reminder_db(reminder_service.get_scheduled_message, _tenant_id(user), message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Scheduled message not found")
    return APIResponse.create_success(data=_reminder_safe_record(message))
@router.post("/scheduled-messages/{message_id}/actions")
async def action_scheduled_message(
    message_id: UUID,
    req: ScheduledMessageActionRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_operational_write(_tenant_id(user), "reminders")
    message = await _run_reminder_db(
        reminder_service.action_scheduled_message,
        _tenant_id(user),
        message_id,
        action=req.action,
        actor_id=_user_id(user),
        scheduled_for=req.scheduled_for,
        snooze_minutes=req.snooze_minutes,
        note=req.note,
        last_error=req.last_error,
        metadata=req.metadata,
    )
    if not message:
        raise HTTPException(status_code=404, detail="Scheduled message not found or action not supported")
    await db_client.write_audit_log(
        tenant_id=_tenant_id(user),
        actor_id=_user_id(user),
        action=f"scheduled_message_{req.action}",
        resource_type="scheduled_message",
        resource_id=message_id,
        details={"status": message["status"], "note": req.note},
    )
    return APIResponse.create_success(data=_reminder_safe_record(message))
@router.get("/scheduled-messages/{message_id}/events")
async def list_scheduled_message_events(
    message_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    message = await _run_reminder_db(reminder_service.get_scheduled_message, _tenant_id(user), message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Scheduled message not found")
    events = await _run_reminder_db(reminder_service.list_scheduled_message_events, _tenant_id(user), message_id, limit=limit)
    return APIResponse.create_success(data=_reminder_safe_records(events))
