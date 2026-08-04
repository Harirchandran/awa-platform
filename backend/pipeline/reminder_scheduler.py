"""Reminder scheduler and dispatcher entrypoint."""
import asyncio
import signal
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4
import redis.asyncio as redis
from backend.api.whatsapp_client import whatsapp_client
from backend.core.config import settings
from backend.db.client import db_client
from backend.domains.events.service import event_service
from backend.domains.reminders.repository import ReminderRepository
from backend.domains.reminders.service import ReminderService
from backend.ops.logger import logger
from backend.schemas.canonical_message import ChannelType
from backend.pipeline.reliable_queue import WORKERS_PAUSED_KEY
BATCH_SIZE = 10
POLL_INTERVAL_SECONDS = 15
LOCK_TTL_SECONDS = 300
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 300
REMINDER_SCHEDULER_HEARTBEAT_KEY = "worker:reminder_scheduler:heartbeat"
REMINDER_SCHEDULER_HEARTBEAT_TTL_SECONDS = 60
_shutdown_event = asyncio.Event()
class ReminderDispatchError(RuntimeError):
    """Raised when a scheduled reminder cannot be delivered safely."""
def _reminder_service() -> ReminderService:
    return ReminderService(repository=ReminderRepository(db_client), events=event_service)
def _safe_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:1000] or exc.__class__.__name__
def _metadata(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("metadata")
    return value if isinstance(value, dict) else {}
def _coerce_uuid(value: Any) -> Optional[UUID]:
    if isinstance(value, UUID):
        return value
    if value:
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None
    return None
def _active_public_chat_session(session: Optional[dict[str, Any]]) -> bool:
    return bool(session and session.get("status") == "active")
async def dispatch_due_reminders_once(batch_size: int = BATCH_SIZE) -> int:
    """Claim and dispatch one batch of due scheduled messages."""
    due_messages = await _reminder_service().claim_due_scheduled_messages(
        limit=batch_size,
        lock_ttl_seconds=LOCK_TTL_SECONDS,
        max_attempts=MAX_ATTEMPTS,
    )
    for record in due_messages:
        await dispatch_scheduled_message(record)
        await asyncio.sleep(0.1)  # 100ms jitter prevents Meta rate limit spikes
    return len(due_messages)
async def dispatch_scheduled_message(record: dict[str, Any]) -> None:
    tenant_id: UUID = record["tenant_id"]
    message_id: UUID = record["id"]
    channel = str(record.get("channel") or "whatsapp")

    # ── Quiet Hours Gate ──
    rule_id = record.get("rule_id")
    if rule_id:
        async with db_client.acquire() as conn:
            rule = await conn.fetchrow(
                "SELECT quiet_hours_start, quiet_hours_end FROM reminder_rules WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL",
                tenant_id, rule_id
            )
        if rule and rule["quiet_hours_start"] is not None and rule["quiet_hours_end"] is not None:
            tenant_config_row = await db_client.get_tenant_config(tenant_id)
            config_json = tenant_config_row.get("config_json") if tenant_config_row else {}
            timezone_str = config_json.get("timezone", "UTC")

            from zoneinfo import ZoneInfo
            try:
                tenant_tz = ZoneInfo(timezone_str)
            except Exception:
                tenant_tz = ZoneInfo("UTC")

            now_tenant = datetime.now(tenant_tz)
            current_time = now_tenant.time()

            qh_start = rule["quiet_hours_start"]
            qh_end = rule["quiet_hours_end"]

            in_quiet_hours = False
            if qh_start <= qh_end:
                in_quiet_hours = qh_start <= current_time <= qh_end
            else: # overnight
                in_quiet_hours = current_time >= qh_start or current_time <= qh_end

            if in_quiet_hours:
                if qh_start > qh_end and current_time >= qh_start:
                    scheduled_tenant_dt = datetime.combine(now_tenant.date() + timedelta(days=1), qh_end) + timedelta(minutes=1)
                else:
                    scheduled_tenant_dt = datetime.combine(now_tenant.date(), qh_end) + timedelta(minutes=1)

                scheduled_tenant_dt = scheduled_tenant_dt.replace(tzinfo=tenant_tz)
                rescheduled_utc = scheduled_tenant_dt.astimezone(timezone.utc)

                logger.info(
                    "Quiet hours active for message=%s tenant=%s. Rescheduling to %s (tenant time) / %s (UTC)",
                    message_id,
                    tenant_id,
                    scheduled_tenant_dt.isoformat(),
                    rescheduled_utc.isoformat(),
                )

                async with db_client.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE scheduled_messages
                        SET scheduled_for = $3, locked_at = NULL, updated_at = NOW()
                        WHERE tenant_id = $1 AND id = $2
                        """,
                        tenant_id,
                        message_id,
                        rescheduled_utc,
                    )
                return

    if channel == "manual":
        await _reminder_service().record_scheduled_message_dispatch_result(
            tenant_id=tenant_id,
            message_id=message_id,
            status="skipped",
            event_type="manual_skipped",
            last_error="Manual reminders require staff action.",
            metadata={"channel": channel},
        )
        return
    try:
        if channel == ChannelType.WHATSAPP.value:
            provider_message_id = await _dispatch_whatsapp_reminder(record)
        elif channel == "email":
            provider_message_id = await _dispatch_email_stub(record)
        else:
            raise ReminderDispatchError(f"Unsupported reminder channel: {channel}")
        await _reminder_service().record_scheduled_message_dispatch_result(
            tenant_id=tenant_id,
            message_id=message_id,
            status="sent",
            event_type="dispatched",
            provider_message_id=provider_message_id,
            metadata={"channel": channel},
        )
    except Exception as exc:
        next_attempt = int(record.get("attempt_count") or 0) + 1
        retryable = channel == ChannelType.WHATSAPP.value
        retry_at = (
            datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAY_SECONDS)
            if retryable and next_attempt < MAX_ATTEMPTS
            else None
        )
        safe_error = _safe_error(exc)
        logger.warning(
            "Reminder dispatch failed message=%s tenant=%s channel=%s attempt=%s error=%s",
            message_id,
            tenant_id,
            channel,
            next_attempt,
            safe_error,
        )
        await _reminder_service().record_scheduled_message_dispatch_result(
            tenant_id=tenant_id,
            message_id=message_id,
            status="failed",
            event_type="dispatch_failed",
            last_error=safe_error,
            retry_at=retry_at,
            metadata={
                "channel": channel,
                "next_attempt": next_attempt,
                "will_retry": retry_at is not None,
            },
        )
async def _dispatch_email_stub(record: dict[str, Any]) -> str:
    """Temporary email dispatch stub.

    Logs the outbound message at INFO level and returns a synthetic
    provider_message_id so the scheduler marks the record as ``sent``.
    Replace this stub with a real AWS SES / SendGrid integration later.
    """
    target_address = str(record.get("customer_email") or "").strip()
    text = str(record.get("message_text") or "")
    logger.info(
        "[EMAIL DISPATCH STUB] Sending to %s: %s",
        target_address or "<no-address>",
        text,
    )
    return f"email_stub_{record['id']}"
async def _resolve_template_components(tenant_id: UUID, record: dict[str, Any]) -> list[dict[str, Any]]:
    # We will build parameters for the body component
    parameters = []

    # 1. Resolve Customer Name
    customer_name = "there" # fallback
    customer_id = record.get("customer_id")
    if customer_id:
        async with db_client.acquire() as conn:
            name = await conn.fetchval("SELECT name FROM customers WHERE tenant_id = $1 AND id = $2", tenant_id, customer_id)
            if name and name != "unknown":
                customer_name = name
    parameters.append({"type": "text", "text": customer_name})

    # Resolve trigger specific context details
    trigger_type = str(record.get("trigger_type") or "")
    booking_id = record.get("booking_id")
    payment_request_id = record.get("payment_request_id")

    if booking_id:
        async with db_client.acquire() as conn:
            booking = await conn.fetchrow(
                "SELECT service_name, booking_date, booking_time FROM bookings WHERE tenant_id = $1 AND id = $2",
                tenant_id, booking_id
            )
        if booking:
            svc_name = booking["service_name"]
            b_date = booking["booking_date"]
            b_time = booking["booking_time"]

            # Format date/time
            date_str = b_date.strftime("%B %d, %Y") if hasattr(b_date, "strftime") else str(b_date)
            time_str = b_time.strftime("%I:%M %p") if b_time and hasattr(b_time, "strftime") else str(b_time) if b_time else ""

            datetime_str = f"{date_str} at {time_str}" if time_str else date_str

            parameters.append({"type": "text", "text": svc_name})
            parameters.append({"type": "text", "text": datetime_str})

    elif payment_request_id:
        async with db_client.acquire() as conn:
            pay = await conn.fetchrow(
                "SELECT amount, reference, payment_url FROM payment_requests WHERE tenant_id = $1 AND id = $2",
                tenant_id, payment_request_id
            )
        if pay:
            amount = pay["amount"]
            ref = pay["reference"]
            url = pay["payment_url"] or ""

            parameters.append({"type": "text", "text": f"INR {amount}"})
            parameters.append({"type": "text", "text": ref})
            if url:
                parameters.append({"type": "text", "text": url})

    else:
        parameters.append({"type": "text", "text": trigger_type.replace("_", " ").title()})
        parameters.append({"type": "text", "text": datetime.now(timezone.utc).strftime("%B %d, %Y")})

    return [
        {
            "type": "body",
            "parameters": parameters
        }
    ]

async def _dispatch_whatsapp_reminder(record: dict[str, Any]) -> str:
    tenant_id: UUID = record["tenant_id"]
    conversation_id: Optional[UUID] = record.get("conversation_id")
    customer_phone = str(record.get("customer_phone") or "").strip()
    account_id = _coerce_uuid(
        record.get("conversation_channel_account_id") or _metadata(record).get("whatsapp_account_id")
    )
    if not conversation_id:
        raise ReminderDispatchError("WhatsApp reminder requires a conversation.")
    if not customer_phone:
        raise ReminderDispatchError("WhatsApp reminder requires a customer phone.")
    if not account_id:
        raise ReminderDispatchError("WhatsApp reminder requires a tenant WhatsApp account.")
    account = await db_client.get_whatsapp_account_for_dispatch(tenant_id, account_id)
    if not account:
        raise ReminderDispatchError("WhatsApp account is not available for dispatch.")
    # ------------------------------------------------------------------ #
    # Meta 24-hour rule: free-form text is only permitted inside the      #
    # active messaging window (last inbound message ≤ 24 h ago).          #
    # Outside it, a pre-approved template must be used.                   #
    # ------------------------------------------------------------------ #
    use_template = False
    conversation = await db_client.get_conversation(tenant_id, conversation_id)
    if conversation:
        last_msg_at = conversation.get("last_message_at")
        if last_msg_at is None:
            # No message timestamp — conservatively assume outside window.
            use_template = True
        else:
            if not isinstance(last_msg_at, datetime):
                try:
                    last_msg_at = datetime.fromisoformat(str(last_msg_at))
                except (TypeError, ValueError):
                    last_msg_at = None
            if last_msg_at is None:
                use_template = True
            else:
                # Ensure the timestamp is timezone-aware for comparison.
                if last_msg_at.tzinfo is None:
                    last_msg_at = last_msg_at.replace(tzinfo=timezone.utc)
                window_seconds = 24 * 3600
                age_seconds = (datetime.now(timezone.utc) - last_msg_at).total_seconds()
                use_template = age_seconds > window_seconds
    else:
        # Conversation not found — conservatively assume outside window.
        use_template = True
    if not use_template:
        # Inside 24-hour window: send free-form text as before.
        dispatch_result = await whatsapp_client.send_text_message(
            to_phone=customer_phone,
            message_text=str(record.get("message_text") or ""),
            phone_number_id=account.get("phone_number_id"),
            access_token=account.get("access_token"),
        )
    else:
        # Outside 24-hour window: must use a Meta-approved template.
        trigger_type = str(record.get("trigger_type") or "")
        tenant_config_row = await db_client.get_tenant_config(tenant_id)
        config_json: dict[str, Any] = {}
        if tenant_config_row and isinstance(tenant_config_row.get("config_json"), dict):
            config_json = tenant_config_row["config_json"]
        reminders_cfg = (config_json.get("modules") or {}).get("reminders") or {}
        template_name = (reminders_cfg.get("whatsapp_templates") or {}).get(trigger_type)
        if not template_name:
            logger.critical(
                "Meta 24h rule violation: no WhatsApp template configured for "
                "trigger_type=%s tenant=%s conversation=%s",
                trigger_type,
                tenant_id,
                conversation_id,
            )
            raise ReminderDispatchError(
                f"Meta 24h rule violation: No template configured for trigger type '{trigger_type}'. "
                "Configure reminders.whatsapp_templates in tenant settings."
            )
        logger.info(
            "Outside 24h window — sending WhatsApp template=%s to phone=%s "
            "for trigger_type=%s tenant=%s",
            template_name,
            customer_phone,
            trigger_type,
            tenant_id,
        )
        components = await _resolve_template_components(tenant_id, record)
        dispatch_result = await whatsapp_client.send_template_message(
            to_phone=customer_phone,
            template_name=template_name,
            phone_number_id=account.get("phone_number_id"),
            access_token=account.get("access_token"),
            components=components,
        )
    if not dispatch_result.get("success"):
        error = dispatch_result.get("error") or dispatch_result.get("error_code") or "provider send failed"
        raise ReminderDispatchError(f"WhatsApp dispatch failed: {error}")
    provider_message_id = dispatch_result.get("message_id")
    if not provider_message_id:
        raise ReminderDispatchError("WhatsApp dispatch succeeded without a provider message id.")
    saved = await db_client.save_message(
        message_id=uuid4(),
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        whatsapp_message_id=f"reminder_{record['id']}",
        provider_message_id=provider_message_id,
        channel_type=ChannelType.WHATSAPP.value,
        direction="outbound",
        message_type="text",
        message_text=str(record.get("message_text") or ""),
        raw_payload={
            "source": "reminder_scheduler",
            "scheduled_message_id": str(record["id"]),
            "provider_message_id": provider_message_id,
        },
    )
    if not saved.get("created") and saved.get("reason") != "duplicate":
        raise ReminderDispatchError(f"Failed to persist WhatsApp reminder: {saved.get('reason')}")
    return provider_message_id

async def scan_inactive_conversations() -> None:
    """Scan for inactive conversations and schedule reminder messages."""
    try:
        async with db_client.acquire() as conn:
            rules = await conn.fetch(
                """
                SELECT id, tenant_id, name, trigger_type, offset_minutes, channel, template_text
                FROM reminder_rules
                WHERE trigger_type = 'inactive_conversation'
                  AND is_active = TRUE
                  AND deleted_at IS NULL
                """
            )

            for rule in rules:
                rule_id = rule["id"]
                tenant_id = rule["tenant_id"]
                offset_minutes = rule["offset_minutes"]

                cutoff = datetime.now(timezone.utc) - timedelta(minutes=offset_minutes)

                inactive_convs = await conn.fetch(
                    """
                    SELECT c.id AS conversation_id, c.customer_id, c.last_message_at, c.tenant_id,
                           cu.name AS customer_name, cu.phone_number AS customer_phone
                    FROM conversations c
                    LEFT JOIN customers cu ON cu.id = c.customer_id AND cu.tenant_id = c.tenant_id
                    WHERE c.tenant_id = $1
                      AND c.status = 'active'
                      AND c.deleted_at IS NULL
                      AND c.last_message_at IS NOT NULL
                      AND c.last_message_at < $2
                    """,
                    tenant_id,
                    cutoff,
                )

                for conv in inactive_convs:
                    conv_id = conv["conversation_id"]
                    cust_id = conv["customer_id"]
                    last_msg_at = conv["last_message_at"]
                    cust_name = conv["customer_name"] or "there"
                    if cust_name == "unknown":
                        cust_name = "there"

                    template = rule["template_text"] or ""
                    message_text = template.replace("{{customer_name}}", cust_name)

                    scheduled_for = datetime.now(timezone.utc)

                    if last_msg_at.tzinfo is None:
                        last_msg_at = last_msg_at.replace(tzinfo=timezone.utc)
                    last_msg_iso = last_msg_at.isoformat()
                    idempotency_key = f"inactive_conv:{conv_id}:rule:{rule_id}:{last_msg_iso}"

                    try:
                        await _reminder_service().create_scheduled_message(
                            tenant_id=tenant_id,
                            scheduled_for=scheduled_for,
                            message_text=message_text,
                            trigger_type="inactive_conversation",
                            channel=rule["channel"],
                            rule_id=rule_id,
                            customer_id=cust_id,
                            conversation_id=conv_id,
                            metadata={
                                "source": "inactive_conversation_scanner",
                                "last_message_at": last_msg_iso,
                            },
                            idempotency_key=idempotency_key,
                        )
                    except Exception as e:
                        logger.error(
                            "Failed to schedule inactive conversation reminder conv=%s rule=%s error=%s",
                            conv_id, rule_id, e
                        )
    except Exception as exc:
        logger.error("Error in scan_inactive_conversations: %s", exc)

async def scheduler_loop() -> None:
    logger.info("Reminder scheduler started")
    heartbeat_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        while not _shutdown_event.is_set():
            try:
                try:
                    await heartbeat_client.set(
                        REMINDER_SCHEDULER_HEARTBEAT_KEY,
                        datetime.now(timezone.utc).isoformat(),
                        ex=REMINDER_SCHEDULER_HEARTBEAT_TTL_SECONDS,
                    )
                except Exception as heartbeat_exc:
                    logger.warning("Reminder scheduler heartbeat failed: %s", heartbeat_exc)

                # Pause-gate: skip processing while system:workers:paused is set.
                if await heartbeat_client.exists(WORKERS_PAUSED_KEY):
                    logger.debug("Reminder scheduler paused by operator signal")
                    await asyncio.sleep(5)
                    continue

                await scan_inactive_conversations()
                count = await dispatch_due_reminders_once()
                if count == 0:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Reminder scheduler loop error: %s", exc)
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
    finally:
        await heartbeat_client.aclose()
    logger.info("Reminder scheduler shutting down")
async def shutdown() -> None:
    logger.info("Reminder scheduler shutdown signal received")
    _shutdown_event.set()
if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(shutdown()))
        except NotImplementedError:
            pass
    try:
        loop.run_until_complete(scheduler_loop())
    except KeyboardInterrupt:
        loop.run_until_complete(shutdown())
    finally:
        with suppress(Exception):
            loop.run_until_complete(db_client.close())
        loop.close()
