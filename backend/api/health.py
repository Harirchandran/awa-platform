from fastapi import APIRouter, Depends, HTTPException
from backend.core.config import settings
from backend.core.redis_client import get_redis_client
from backend.schemas.response import APIResponse
from backend.api.dependencies import require_super_admin
from backend.db.client import db_client
from backend.pipeline.burst_worker import (
    BURST_WORKER_HEARTBEAT_KEY,
    BURST_WORKER_HEARTBEAT_TTL_SECONDS,
)
from backend.pipeline.reminder_scheduler import (
    REMINDER_SCHEDULER_HEARTBEAT_KEY,
    REMINDER_SCHEDULER_HEARTBEAT_TTL_SECONDS,
)
from backend.pipeline.event_dispatcher import (
    EVENT_DISPATCHER_HEARTBEAT_KEY,
    EVENT_DISPATCHER_HEARTBEAT_TTL_SECONDS,
    EVENT_DISPATCHER_LAST_ERROR_KEY,
    EVENT_DISPATCHER_LAST_SUCCESS_KEY,
)
from backend.pipeline.reliable_queue import (
    DLQ_KEY,
    PROCESSING_KEY,
    QUEUE_KEY,
    WORKER_HEARTBEAT_KEY,
    WORKER_HEARTBEAT_TTL_SECONDS,
)
from backend.domains.events.service import event_service
from datetime import datetime, timezone
router = APIRouter()


def _event_visibility_record(event: dict) -> dict:
    return {
        "event_id": str(event.get("id")) if event.get("id") else None,
        "tenant_id": str(event.get("tenant_id")) if event.get("tenant_id") else None,
        "event_type": event.get("event_type"),
        "source_domain": event.get("source_domain"),
        "aggregate_type": event.get("aggregate_type"),
        "aggregate_id": str(event.get("aggregate_id")) if event.get("aggregate_id") else None,
        "status": event.get("status"),
        "attempts": event.get("attempts"),
        "max_attempts": event.get("max_attempts"),
        "reason_code": event.get("last_error"),
        "available_at": event.get("available_at").isoformat() if hasattr(event.get("available_at"), "isoformat") else event.get("available_at"),
        "updated_at": event.get("updated_at").isoformat() if hasattr(event.get("updated_at"), "isoformat") else event.get("updated_at"),
    }


def _coerce_heartbeat_status(last_seen_at: str | None, ttl_seconds: int) -> dict:
    if not last_seen_at:
        return {
            "status": "unhealthy",
            "last_seen_at": None,
            "lag_seconds": None,
            "ttl_seconds": ttl_seconds,
        }
    try:
        heartbeat_at = datetime.fromisoformat(last_seen_at)
        if heartbeat_at.tzinfo is None:
            heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
        lag_seconds = max(0, int((datetime.now(timezone.utc) - heartbeat_at).total_seconds()))
        return {
            "status": "healthy" if lag_seconds <= ttl_seconds else "unhealthy",
            "last_seen_at": last_seen_at,
            "lag_seconds": lag_seconds,
            "ttl_seconds": ttl_seconds,
        }
    except ValueError:
        return {
            "status": "unhealthy",
            "last_seen_at": last_seen_at,
            "lag_seconds": None,
            "ttl_seconds": ttl_seconds,
        }
def _queue_alert(queue_depth: int, processing_depth: int) -> str:
    total_depth = queue_depth + processing_depth
    if total_depth > 50:
        return "critical"
    if total_depth > 10:
        return "warning"
    return "ok"
def _dlq_alert(dlq_depth: int) -> str:
    if dlq_depth > 50:
        return "critical"
    if dlq_depth > 0:
        return "warning"
    return "ok"
def _configured(*values: str | None) -> bool:
    return all(bool(value and str(value).strip()) for value in values)
def _mode_status(mode: str, ready: bool, *, mock_safe_outside_production: bool = True) -> str:
    if ready:
        return "ready"
    if mode in {"mock", "test"} and settings.ENV != "production" and mock_safe_outside_production:
        return f"{mode}_mode"
    if mode == "disabled":
        return "disabled"
    return "blocked"
def _provider_health_snapshot() -> dict:
    whatsapp_mode = settings.effective_whatsapp_mode
    billing_mode = settings.effective_billing_mode
    support_mode = settings.SUPPORT_NOTIFICATION_MODE
    return {
        "whatsapp": {
            "mode": whatsapp_mode,
            "status": _mode_status(
                whatsapp_mode,
                whatsapp_mode == "live" and _configured(
                    settings.WHATSAPP_API_TOKEN,
                    settings.WHATSAPP_APP_SECRET,
                    settings.WHATSAPP_VERIFY_TOKEN,
                    settings.WHATSAPP_PHONE_NUMBER_ID,
                    settings.WHATSAPP_TOKEN_ENCRYPTION_KEY,
                ),
            ),
            "checks": {
                "api_token_configured": bool(settings.WHATSAPP_API_TOKEN),
                "app_secret_configured": bool(settings.WHATSAPP_APP_SECRET),
                "verify_token_configured": bool(settings.WHATSAPP_VERIFY_TOKEN),
                "phone_number_id_configured": bool(settings.WHATSAPP_PHONE_NUMBER_ID),
                "token_encryption_configured": bool(settings.WHATSAPP_TOKEN_ENCRYPTION_KEY),
            },
        },
        "razorpay": {
            "mode": billing_mode,
            "status": _mode_status(
                billing_mode,
                billing_mode == "live" and _configured(
                    settings.RAZORPAY_KEY_ID,
                    settings.RAZORPAY_KEY_SECRET,
                    settings.RAZORPAY_WEBHOOK_SECRET,
                ),
            ),
            "checks": {
                "key_id_configured": bool(settings.RAZORPAY_KEY_ID),
                "key_secret_configured": bool(settings.RAZORPAY_KEY_SECRET),
                "webhook_secret_configured": bool(settings.RAZORPAY_WEBHOOK_SECRET),
            },
        },
        "turnstile": {
            "mode": settings.PUBLIC_CHAT_CAPTCHA_MODE,
            "status": "ready" if settings.PUBLIC_CHAT_CAPTCHA_MODE == "turnstile" and bool(settings.TURNSTILE_SECRET_KEY) else _mode_status(settings.PUBLIC_CHAT_CAPTCHA_MODE, False),
            "checks": {"secret_configured": bool(settings.TURNSTILE_SECRET_KEY)},
        },
        "otp": {
            "mode": settings.CUSTOMER_OTP_MODE,
            "status": "ready" if settings.CUSTOMER_OTP_MODE == "live" and _configured(settings.CUSTOMER_OTP_WEBHOOK_URL, settings.CUSTOMER_OTP_WEBHOOK_SECRET) else _mode_status(settings.CUSTOMER_OTP_MODE, False),
            "checks": {
                "webhook_url_configured": bool(settings.CUSTOMER_OTP_WEBHOOK_URL),
                "webhook_secret_configured": bool(settings.CUSTOMER_OTP_WEBHOOK_SECRET),
                "max_attempts": settings.CUSTOMER_OTP_MAX_ATTEMPTS,
            },
        },
        "smtp_email": {
            "mode": "not_implemented",
            "status": "blocked",
            "checks": {
                "invite_email_provider_configured": False,
                "manual_invite_links_allowed": settings.ALLOW_MANUAL_INVITE_LINKS and settings.ENV != "production",
            },
        },
        "support_notifications": {
            "mode": support_mode,
            "status": "ready" if support_mode == "webhook" and bool(settings.SUPPORT_NOTIFICATION_WEBHOOK_URL) else _mode_status(support_mode, False),
            "checks": {
                "webhook_url_configured": bool(settings.SUPPORT_NOTIFICATION_WEBHOOK_URL),
            },
        },
        "llm": {
            "routing_provider": settings.ROUTING_LLM_PROVIDER,
            "agent_provider": settings.AGENT_LLM_PROVIDER,
            "status": "configured" if _configured(settings.GROQ_API_KEY or settings.OPENAI_API_KEY or settings.ANTHROPIC_API_KEY or settings.GEMINI_API_KEY) else "blocked",
        },
    }
async def _collect_health_snapshot(include_errors: bool = False) -> dict:
    redis_status = "healthy"
    db_status = "healthy"
    queue_depth = 0
    processing_depth = 0
    dlq_depth = 0
    active_worker_count = 0
    active_session_locks = 0
    workers = {
        "main": _coerce_heartbeat_status(None, WORKER_HEARTBEAT_TTL_SECONDS),
        "burst": _coerce_heartbeat_status(None, BURST_WORKER_HEARTBEAT_TTL_SECONDS),
        "reminder_scheduler": _coerce_heartbeat_status(None, REMINDER_SCHEDULER_HEARTBEAT_TTL_SECONDS),
    }
    client = get_redis_client(socket_timeout=2)
    try:
        await client.ping()
        queue_depth = await client.llen(QUEUE_KEY)
        processing_depth = await client.llen(PROCESSING_KEY)
        dlq_depth = await client.llen(DLQ_KEY)

        # Scan for active unique worker heartbeats
        worker_keys = []
        if hasattr(client, "scan"):
            try:
                cursor = 0
                for _ in range(5):  # Limit scan iterations
                    cursor, keys_found = await client.scan(cursor, match="worker:main:*:heartbeat", count=100)
                    worker_keys.extend(keys_found)
                    if cursor == 0:
                        break
            except Exception as scan_exc:
                logger.warning(f"Redis scan for worker heartbeats failed: {scan_exc}")

        scanned_heartbeats = []
        for key in worker_keys:
            val = await client.get(key)
            if val:
                status_info = _coerce_heartbeat_status(val, WORKER_HEARTBEAT_TTL_SECONDS)
                if status_info["status"] == "healthy":
                    active_worker_count += 1
                scanned_heartbeats.append(status_info)

        # Main worker heartbeat key compatibility
        old_val = await client.get(WORKER_HEARTBEAT_KEY)
        old_heartbeat_status = _coerce_heartbeat_status(old_val, WORKER_HEARTBEAT_TTL_SECONDS)

        # Fallback to 1 active worker if old key is healthy but scanned is 0
        if active_worker_count == 0 and old_heartbeat_status["status"] == "healthy":
            active_worker_count = 1

        # The main worker entry returned in workers dict:
        if scanned_heartbeats:
            # Return healthiest scanned worker info
            healthiest = max(
                scanned_heartbeats,
                key=lambda x: x.get("lag_seconds") or 999999 if x["status"] == "healthy" else -999999
            )
            main_worker_status = healthiest
        else:
            main_worker_status = old_heartbeat_status

        workers = {
            "main": main_worker_status,
            "burst": _coerce_heartbeat_status(
                await client.get(BURST_WORKER_HEARTBEAT_KEY),
                BURST_WORKER_HEARTBEAT_TTL_SECONDS,
            ),
            "reminder_scheduler": _coerce_heartbeat_status(
                await client.get(REMINDER_SCHEDULER_HEARTBEAT_KEY),
                REMINDER_SCHEDULER_HEARTBEAT_TTL_SECONDS,
            ),
        }

        # Bounded scan for session locks
        session_lock_keys = []
        if hasattr(client, "scan"):
            try:
                cursor = 0
                for _ in range(10):  # Bounded iterations
                    cursor, keys_found = await client.scan(cursor, match="lock:session:*", count=100)
                    session_lock_keys.extend(keys_found)
                    if cursor == 0:
                        break
                active_session_locks = len(session_lock_keys)
            except Exception as scan_exc:
                logger.warning(f"Redis scan for session locks failed: {scan_exc}")
                active_session_locks = 0

        # Read lock_wait_timeout_count from Redis
        lock_wait_timeout_count = 0
        try:
            lock_wait_timeout_val = await client.get("worker:metrics:lock_wait_timeout_count")
            if lock_wait_timeout_val:
                lock_wait_timeout_count = int(lock_wait_timeout_val)
        except Exception as metric_exc:
            logger.warning(f"Failed to read lock_wait_timeout_count: {metric_exc}")
    except Exception as e:
        redis_status = f"unhealthy: {e}" if include_errors else "unhealthy"
        lock_wait_timeout_count = 0
    try:
        async with db_client.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as e:
        db_status = f"unhealthy: {e}" if include_errors else "unhealthy"
    worker_status = "healthy" if all(worker["status"] == "healthy" for worker in workers.values()) else "unhealthy"
    status = "healthy"
    if redis_status != "healthy" or db_status != "healthy" or worker_status != "healthy":
        status = "degraded"
    if redis_status != "healthy" and db_status != "healthy":
        status = "unhealthy"
    main_worker = workers["main"]
    return {
        "status": status,
        "queue_depth": queue_depth,
        "processing_depth": processing_depth,
        "dlq_depth": dlq_depth,
        "queue_alert": _queue_alert(queue_depth, processing_depth),
        "dlq_alert": _dlq_alert(dlq_depth),
        "services": {
            "redis": redis_status,
            "database": db_status,
            "worker": worker_status,
            "api": "healthy",
        },
        "workers": workers,
        "worker_last_seen_at": main_worker["last_seen_at"],
        "worker_lag_seconds": main_worker["lag_seconds"],
        "version": "1.0.0",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "active_worker_count": active_worker_count,
        "active_session_locks": active_session_locks,
        "session_lock_enabled": settings.ENABLE_WORKER_SESSION_LOCKS,
        "lock_wait_timeout_count": lock_wait_timeout_count,
        "provider_rate_limiter_enabled": settings.ENABLE_PROVIDER_RATE_LIMITER,
        "rate_limiter_configured_rpm": settings.PROVIDER_GEMINI_RPM_LIMIT,
        "rate_limiter_safety_margin_percent": settings.PROVIDER_RATE_LIMIT_SAFETY_MARGIN_PERCENT,
        "rate_limiter_burst_cap_percent": settings.PROVIDER_RATE_LIMIT_BURST_CAP_PERCENT,
    }


async def _collect_event_ops_snapshot() -> dict:
    try:
        event_health = await event_service.event_health_summary()
        recent_failed = await event_service.list_failed_events(limit=10)
        recent_skipped = await event_service.list_skipped_events(limit=10)
    except Exception as exc:
        event_health = {
            "status": "unavailable",
            "reason": "event_store_unavailable",
            "counts": {"pending": 0, "processing": 0, "processed": 0, "failed": 0, "skipped": 0},
            "retryable_failed_count": 0,
            "retrying_count": 0,
            "oldest_pending_event_age_seconds": 0,
            "oldest_failed_event_age_seconds": 0,
            "events_by_type": {},
            "events_by_source_domain": {},
        }
        recent_failed = []
        recent_skipped = []
    client = get_redis_client(socket_timeout=2)
    try:
        dispatcher = _coerce_heartbeat_status(
            await client.get(EVENT_DISPATCHER_HEARTBEAT_KEY),
            EVENT_DISPATCHER_HEARTBEAT_TTL_SECONDS,
        )
        last_success_at = await client.get(EVENT_DISPATCHER_LAST_SUCCESS_KEY)
        last_error_at = await client.get(EVENT_DISPATCHER_LAST_ERROR_KEY)
    except Exception:
        dispatcher = _coerce_heartbeat_status(None, EVENT_DISPATCHER_HEARTBEAT_TTL_SECONDS)
        last_success_at = None
        last_error_at = "unavailable"
    return {
        **event_health,
        "dispatcher": {
            **dispatcher,
            "last_success_at": last_success_at,
            "last_error_at": last_error_at,
        },
        "recent_failed_events": [_event_visibility_record(event) for event in recent_failed],
        "recent_skipped_events": [_event_visibility_record(event) for event in recent_skipped],
        "payloads_included": False,
    }
@router.get("/health")
async def health_check():
    """
    Public platform health endpoint. In production this returns a redacted
    liveness summary; authenticated operators can use /health/ops for details.
    """
    snapshot = await _collect_health_snapshot(include_errors=settings.ENV != "production")
    if settings.ENV == "production":
        data = {
            "status": snapshot["status"],
            "queue_alert": snapshot["queue_alert"],
            "services": {
                "redis": "healthy" if snapshot["services"]["redis"] == "healthy" else "unhealthy",
                "database": "healthy" if snapshot["services"]["database"] == "healthy" else "unhealthy",
                "worker": snapshot["services"]["worker"],
                "api": "healthy",
            },
            "version": snapshot["version"],
            "checked_at": snapshot["checked_at"],
        }
        return APIResponse.create_success(data=data)
    return APIResponse.create_success(data=snapshot)
@router.get("/health/ops")
async def operational_health_check(admin: dict = Depends(require_super_admin)):
    """
    Super Admin endpoint for platform telemetry.
    Returns worker and queue signals without tenant, customer, payload, or secret data.
    """
    snapshot = await _collect_health_snapshot(include_errors=True)
    event_ops = await _collect_event_ops_snapshot()
    data = {
        **snapshot,
        "events": event_ops,
        "providers": _provider_health_snapshot(),
        "operator": {
            "user_id": str(admin.get("user_id")),
            "role": admin.get("role"),
        },
    }
    return APIResponse.create_success(data=data)


# ---------------------------------------------------------------------------
# Phase 3 – Infrastructure Command Endpoints
# ---------------------------------------------------------------------------

from pydantic import BaseModel
from backend.pipeline.reliable_queue import WORKERS_PAUSED_KEY
import json as _json


class WorkerSignalRequest(BaseModel):
    action: str  # "pause" | "resume"


class DLQRetryRequest(BaseModel):
    target: str = "all"  # "all" or a specific raw item (stringified JSON)


@router.get("/health/queues/dlq", response_model=APIResponse)
async def get_dlq_items(admin: dict = Depends(require_super_admin)):
    """
    Super Admin: Return the top 50 items currently in the Dead-Letter Queue.
    Each item is the raw JSON string that failed processing, enriched with its
    position index for display purposes.
    """
    redis_client = get_redis_client(socket_timeout=5)
    raw_items = await redis_client.lrange(DLQ_KEY, 0, 49)
    items = []
    for idx, raw in enumerate(raw_items):
        try:
            parsed = _json.loads(raw)
        except Exception:
            parsed = {"raw": raw}
        items.append({
            "index": idx,
            "raw": raw,
            "payload": parsed,
        })
    return APIResponse.create_success(data={
        "total": await redis_client.llen(DLQ_KEY),
        "items": items,
    })


@router.post("/health/queues/dlq/retry", response_model=APIResponse)
async def retry_dlq_items(req: DLQRetryRequest, admin: dict = Depends(require_super_admin)):
    """
    Super Admin: Move DLQ items back to the main processing queue.
    target="all" retries everything; otherwise treats target as the raw item string to retry.
    """
    redis_client = get_redis_client(socket_timeout=5)
    if req.target == "all":
        raw_items = await redis_client.lrange(DLQ_KEY, 0, -1)
        if not raw_items:
            return APIResponse.create_success(data={"retried": 0, "message": "DLQ is empty"})
        pipe = redis_client.pipeline()
        for raw in raw_items:
            pipe.lpush(QUEUE_KEY, raw)
            pipe.lrem(DLQ_KEY, 1, raw)
        await pipe.execute()
        retried = len(raw_items)
    else:
        removed = await redis_client.lrem(DLQ_KEY, 1, req.target)
        if removed:
            await redis_client.lpush(QUEUE_KEY, req.target)
        retried = removed

    await db_client.write_audit_log(
        tenant_id=None,
        actor_id=admin["user_id"],
        action="dlq_retry",
        resource_type="system",
        resource_id=None,
        details={"target": req.target, "retried_count": retried},
    )
    return APIResponse.create_success(data={"retried": retried})


@router.delete("/health/queues/dlq/purge", response_model=APIResponse)
async def purge_dlq(admin: dict = Depends(require_super_admin)):
    """
    Super Admin: Permanently delete all items in the Dead-Letter Queue.
    This is irreversible — failed messages will not be retried.
    """
    redis_client = get_redis_client(socket_timeout=5)
    count = await redis_client.llen(DLQ_KEY)
    await redis_client.delete(DLQ_KEY)
    await db_client.write_audit_log(
        tenant_id=None,
        actor_id=admin["user_id"],
        action="dlq_purge",
        resource_type="system",
        resource_id=None,
        details={"purged_count": count},
    )
    return APIResponse.create_success(data={"purged": count})


@router.post("/health/workers/signal", response_model=APIResponse)
async def signal_workers(req: WorkerSignalRequest, admin: dict = Depends(require_super_admin)):
    """
    Super Admin: Pause or resume all background workers.
    Sets/deletes the WORKERS_PAUSED_KEY in Redis. Workers check this key at
    the top of every poll iteration and skip processing while it is set.
    """
    if req.action not in ("pause", "resume"):
        raise HTTPException(status_code=400, detail="action must be 'pause' or 'resume'")

    redis_client = get_redis_client(socket_timeout=5)
    if req.action == "pause":
        await redis_client.set(WORKERS_PAUSED_KEY, datetime.now(timezone.utc).isoformat())
        paused = True
    else:
        await redis_client.delete(WORKERS_PAUSED_KEY)
        paused = False

    await db_client.write_audit_log(
        tenant_id=None,
        actor_id=admin["user_id"],
        action=f"workers_{req.action}d",
        resource_type="system",
        resource_id=None,
        details={"action": req.action, "paused": paused},
    )
    return APIResponse.create_success(data={"paused": paused, "action": req.action})
