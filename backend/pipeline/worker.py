# Worker Entrypoint — Production Hardened
# Includes: idempotent processing, outbound dispatch, message persistence, session updates
import asyncio
import signal
import redis.asyncio as redis
from contextlib import suppress
from typing import Optional
from uuid import uuid4
from datetime import date, datetime, timezone
import time
from backend.core.config import settings
from backend.schemas.canonical_message import CanonicalMessage, ChannelType
from backend.ops.logger import logger, set_trace_context
from backend.ops.dlq import dlq_manager
from backend.core.states import ConversationState
from backend.core.config_loader import config_loader
from backend.pipeline.session_manager import session_manager, SessionPersistenceError
from backend.core.context import ExecutionContext, Tenant, User
from backend.api.whatsapp_client import whatsapp_client
from backend.db.client import db_client
from backend.cost.aggregator import cost_aggregator
from backend.billing.usage_guard import usage_guard
from backend.pipeline.interactive_parser import build_plain_text_fallback, parse_interactive_hints
from backend.pipeline.channel_output import normalize_channel_payload, redact_url_tokens
from backend.pipeline.message_router import message_router
from backend.pipeline.reliable_queue import (
    DLQ_KEY,
    QUEUE_KEY,
    WORKER_HEARTBEAT_KEY,
    WORKER_HEARTBEAT_TTL_SECONDS,
    WORKERS_PAUSED_KEY,
    ack_message,
    claim_message,
    enqueue_message,
    recover_stale_messages,
)
from backend.pipeline.session_lock import (
    SessionLock,
    acquire_session_lock,
    extend_session_lock,
    release_session_lock,
)
MAX_RETRIES = 3
BASE_RETRY_DELAY = 2  # seconds
RECOVERY_INTERVAL_SECONDS = 30
WORKER_HEARTBEAT_INTERVAL_SECONDS = 5


def _is_durable_public_chat_turn(
    msg: CanonicalMessage,
) -> bool:
    """Identify turns authorized by the durable public-chat intake contract."""
    try:
        return (
            msg.source_type.value == "awa_chat"
            and msg.channel_type.value == "awa_chat"
            and msg.whatsapp_account_id is None
            and bool(msg.message.message_id)
            and bool(msg.event_id)
            and bool(msg.conversation_id)
            and bool(msg.channel_account_id)
            and bool(msg.channel_session_id)
        )
    except (AttributeError, TypeError, ValueError):
        return False


class DispatchError(RuntimeError):
    """Raised when an outbound response cannot be safely delivered or recorded."""

async def renew_lock_loop(redis_client, lock: SessionLock, interval_ms: int, ttl_ms: int):
    """Periodically extend the session lock to prevent expiration during long tasks."""
    while lock.acquired and not _shutdown_event.is_set():
        await asyncio.sleep(interval_ms / 1000.0)
        if not lock.acquired:
            break
        # Extend the lock
        success = await extend_session_lock(redis_client, lock, ttl_ms)
        if not success:
            logger.error(f"Failed to renew session lock {lock.key}. Lost ownership.")
            lock.acquired = False
            break
# Shared Redis connection pool (created once, reused for all messages)
_redis_pool: redis.Redis | None = None
_shutdown_event = asyncio.Event()
async def get_redis() -> redis.Redis:
    """Get or create the shared Redis connection."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_pool


async def _recover_verified_continuation_failure(
    msg: CanonicalMessage,
    context: ExecutionContext,
    session,
    exc: Exception,
) -> bool:
    """Persist a bounded, recoverable pre-dispatch failure for public chat."""
    provider_message_id = str(msg.message.provider_message_id or "")
    is_verified_continuation = (
        msg.source_type.value == "internal_event"
        and provider_message_id.startswith("awa-internal:otp_verified_pending_action:")
    )
    pending = context.session_data.state.action_ledger.pending_action
    if not is_verified_continuation or pending is None:
        return False

    safe_message = (
        "I couldn't complete that request, and nothing was submitted. "
        "You can confirm again, change a detail, or cancel."
    )

    candidate_state = context.session_data.state.model_copy(deep=True)
    candidate_ledger = candidate_state.action_ledger
    candidate_pending = candidate_ledger.pending_action

    candidate_ledger.verification.otp_verified_actions = []
    candidate_ledger.verification.otp_verifications = []
    candidate_pending.action_nonce = uuid4().hex
    candidate_pending.idempotency_key = None
    candidate_state.record_safe_error(
        code="protected_action_processing_failed",
        safe_message=safe_message,
        channel_type=msg.channel_type.value,
    )
    candidate_ledger.task_status = "awaiting_confirmation"

    await session_manager.update_state(session, candidate_state)

    logger.warning(
        "Trusted continuation failed before dispatch and was made recoverable",
        extra={"extra_data": {"error_type": type(exc).__name__}},
    )
    await _dispatch_response(
        msg,
        safe_message,
        context,
        quick_replies=["Confirm", "Change", "Cancel"],
        processing_result={
            "processing_status": "failed",
            "recoverable": True,
            "failure_stage": "before_dispatch",
        },
    )
    return True
async def process_message(
    raw_msg: str,
    wall_claim_time_ms: Optional[float] = None,
    start_time_ms: Optional[float] = None
) -> bool:
    from backend.observability.latency import latency_metrics_var, now_ms, elapsed_ms, redact_metrics_payload

    metrics = {
        "queue_wait_ms": None,
        "worker_processing_ms": None,
        "provider_latency_ms": 0,
        "provider_llm_call_count": 0,
        "provider_input_estimated_tokens": 0,
        "provider_total_estimated_tokens": 0,
        "tool_execution_ms": 0,
        "db_latency_ms": 0,
        "response_publish_ms": None,
        "provider_throttle_wait_ms": 0,
        "worker_start_time_ms": start_time_ms,
        "session_lock_wait_ms": 0,
        "session_lock_held_ms": 0,
        "session_lock_acquired": 0,
        "session_lock_timeout_count": 0,
        "rate_limiter_configured_rpm": 0,
        "rate_limiter_effective_rpm": 0,
        "rate_limiter_safety_margin_percent": 0,
        "rate_limiter_burst_cap": 0,
        "provider_calls_allowed": 0,
        "provider_calls_denied": 0,
        "provider_wait_timeouts": 0,
        "provider_actual_429s": 0,
    }
    # Mandatory Correction 2: ContextVar Reset Safety
    token = latency_metrics_var.set(metrics)

    renewal_task = None
    lock = None
    lock_held_start_ms = None
    idempotency_key = None
    idempotency_claimed = False

    try:
        r = await get_redis()
        msg: CanonicalMessage | None = None
        try:
            # 1. Parse
            msg = CanonicalMessage.model_validate_json(raw_msg)

            # Compute queue_wait_ms
            if wall_claim_time_ms is not None:
                enqueued_time_ms = None
                if msg.trace and msg.trace.created_at:
                    enqueued_time_ms = msg.trace.created_at.timestamp() * 1000
                elif msg.processing and msg.processing.first_received_at:
                    enqueued_time_ms = msg.processing.first_received_at.timestamp() * 1000
                elif msg.message and msg.message.timestamp:
                    enqueued_time_ms = msg.message.timestamp.timestamp() * 1000

                if enqueued_time_ms is not None:
                    metrics["queue_wait_ms"] = max(0, int(wall_claim_time_ms - enqueued_time_ms))

            # 2. Context
            set_trace_context(str(msg.trace.trace_id), str(msg.tenant_id))
            logger.info(f"Worker received message: {msg.event_id}")

            # Acquire Session Lock
            if settings.ENABLE_WORKER_SESSION_LOCKS:
                lock = await acquire_session_lock(
                    redis_client=r,
                    tenant_id=msg.tenant_id,
                    conversation_id=msg.conversation_id,
                    ttl_ms=settings.WORKER_SESSION_LOCK_TTL_MS,
                    max_wait_ms=settings.WORKER_SESSION_LOCK_MAX_WAIT_MS
                )
                metrics["session_lock_wait_ms"] = lock.wait_ms
                if not lock.acquired:
                    metrics["session_lock_timeout_count"] = 1
                    raise RuntimeError("lock_wait_timeout")
                metrics["session_lock_acquired"] = 1
                lock_held_start_ms = now_ms()
                renewal_task = asyncio.create_task(
                    renew_lock_loop(
                        redis_client=r,
                        lock=lock,
                        interval_ms=settings.WORKER_SESSION_LOCK_RENEW_INTERVAL_MS,
                        ttl_ms=settings.WORKER_SESSION_LOCK_TTL_MS
                    )
                )

            # 3. Idempotency Check
            idempotency_key = f"processed:{msg.tenant_id}:{msg.event_id}"
            if await r.setnx(idempotency_key, "1"):
                idempotency_claimed = True
                await r.expire(idempotency_key, 86400)  # 24h TTL
                # 4. Context Hydration
                session = await session_manager.get_or_create_session(msg.tenant_id, msg.conversation_id)
                effective_config_version, config_reason = await session_manager.bind_config_for_turn(
                    session,
                    active_version=msg.config_version,
                )
                msg.config_version = effective_config_version
                try:
                    config = await config_loader.load_config(msg.tenant_id, effective_config_version)
                except Exception as exc:
                    from backend.pipeline.config_binding import ConfigBindingError
                    logger.error("Authoritative config could not be loaded", extra={"extra_data": {"reason_code": "pinned_config_unavailable", "binding_reason": config_reason, "error_type": type(exc).__name__}})
                    raise ConfigBindingError("pinned_config_unavailable") from exc
                await session_manager.commit_config_for_turn(
                    session,
                    version=effective_config_version,
                    reason_code=config_reason,
                )
                previous_state = session.state.workflow_state
                tenant_record = await db_client.get_tenant(msg.tenant_id)
                if not tenant_record:
                    raise ValueError(f"Tenant not found: {msg.tenant_id}")

                customer_name = "unknown"
                customer_phone = "unknown"
                try:
                    from backend.domains.customers.service import customer_service
                    customer_record = await customer_service.get_customer(msg.tenant_id, msg.customer_id)
                    if customer_record:
                        customer_name = customer_record.get("name") or "unknown"
                        customer_phone = customer_record.get("phone_number") or "unknown"
                except Exception as exc:
                    logger.error(f"Failed to fetch customer identity details from DB: {exc}")

                context = ExecutionContext(
                    tenant=Tenant(
                        id=tenant_record["id"],
                        name=tenant_record["name"],
                        plan_code=tenant_record["plan_code"],
                        status=tenant_record["status"],
                        ai_mode=tenant_record.get("ai_mode"),
                        trial_ends_at=tenant_record.get("trial_ends_at"),
                        current_period_end=tenant_record.get("current_period_end"),
                    ),
                    user=User(id=msg.customer_id, phone_number=customer_phone, name=customer_name),
                    session_data=session,
                    config=config,
                    incoming_message={
                        **msg.message.model_dump(),
                        "channel_type": msg.channel_type.value,
                        "source_type": msg.source_type.value,
                        "tenant_id": str(msg.tenant_id),
                        "conversation_id": str(msg.conversation_id),
                        "channel_account_id": str(msg.channel_account_id) if msg.channel_account_id else None,
                        "channel_session_id": msg.channel_session_id,
                        "whatsapp_account_id": str(msg.whatsapp_account_id) if msg.whatsapp_account_id else None,
                        "conversation_sequence_number": msg.conversation_sequence_number,
                    }
                )

                # Check ownership before DB save
                if settings.ENABLE_WORKER_SESSION_LOCKS and lock and not lock.acquired:
                    raise RuntimeError("lock_ownership_lost")

                try:
                    from backend.ai.security import is_prompt_security_request
                    db_text = msg.message.text
                    if is_prompt_security_request(db_text):
                        db_text = "[Content flagged for security]"
                    await db_client.save_message(
                        message_id=msg.message.message_id,
                        tenant_id=msg.tenant_id,
                        conversation_id=msg.conversation_id,
                        whatsapp_message_id=msg.message.provider_message_id,
                        provider_message_id=msg.message.provider_message_id,
                        channel_type=msg.channel_type.value,
                        direction="inbound",
                        message_type=msg.message.message_type.value,
                        message_text=db_text
                    )
                except Exception as e:
                    logger.warning(f"Failed to persist inbound message: {e}")

                # Check ownership before resolving AI mode
                if settings.ENABLE_WORKER_SESSION_LOCKS and lock and not lock.acquired:
                    raise RuntimeError("lock_ownership_lost")

                ai_mode = await usage_guard.resolve_ai_mode(
                    context,
                    tenant_record.get("ai_mode") or await db_client.get_tenant_ai_mode(msg.tenant_id),
                )
                context.tenant.ai_mode = ai_mode

                # Check ownership before executing agentic step
                if settings.ENABLE_WORKER_SESSION_LOCKS and lock and not lock.acquired:
                    raise RuntimeError("lock_ownership_lost")

                if _is_durable_public_chat_turn(msg):
                    res = await db_client.mark_public_chat_turn_processing(
                        tenant_id=msg.tenant_id,
                        conversation_id=msg.conversation_id,
                        message_id=msg.message.message_id,
                    )
                    if not res:
                        raise RuntimeError("worker_error")

                from backend.logic.engine.core import agentic_engine
                from backend.billing.usage_guard import UsageLimitExceeded
                logger.info(f"Executing Unified Agentic Workflow for Session {session.id} (Capability Profile: {ai_mode})")
                try:
                    result = await agentic_engine.execute_step(context)
                except UsageLimitExceeded as e:
                    logger.warning(f"Usage limit exceeded for tenant {context.tenant.id}: {e.reason}")
                    await _dispatch_response(msg, e.user_message, context)

                    if _is_durable_public_chat_turn(msg):
                        res = await db_client.mark_public_chat_turn_completed(
                            tenant_id=msg.tenant_id,
                            conversation_id=msg.conversation_id,
                            message_id=msg.message.message_id,
                        )
                        if not res:
                            raise RuntimeError("worker_error")

                    return True
                except Exception as exc:
                    if await _recover_verified_continuation_failure(msg, context, session, exc):
                        return True
                    raise
                logger.info(f"Workflow Execution Result: {result}")

                if result.next_state:
                    # Check ownership before mutating conversation state
                    if settings.ENABLE_WORKER_SESSION_LOCKS and lock and not lock.acquired:
                        raise RuntimeError("lock_ownership_lost")
                    candidate_state = context.session_data.state.model_copy(deep=True)
                    candidate_state.workflow_state = result.next_state
                    await session_manager.update_state(session, candidate_state)

                should_suppress_escalated_auto_reply = (
                    previous_state == ConversationState.ESCALATED
                    and context.session_data.state.workflow_state == ConversationState.ESCALATED
                )
                if should_suppress_escalated_auto_reply:
                    logger.info(
                        "Skipping automated response because conversation %s is escalated",
                        msg.conversation_id,
                    )
                elif result.requires_response and result.response_text:
                    # Check ownership before dispatching response
                    if settings.ENABLE_WORKER_SESSION_LOCKS and lock and not lock.acquired:
                        raise RuntimeError("lock_ownership_lost")
                    await _dispatch_response(msg, result.response_text, context, quick_replies=result.quick_replies)

                # Check ownership before recording usage
                if settings.ENABLE_WORKER_SESSION_LOCKS and lock and not lock.acquired:
                    raise RuntimeError("lock_ownership_lost")
                await _record_message_usage(msg, context)
                logger.info("Message processed successfully.")

                # Record processing time and log structured metrics
                if start_time_ms is not None:
                    metrics["worker_processing_ms"] = elapsed_ms(start_time_ms)
                logger.info(
                    "message_processed",
                    extra={
                        "event": "message_processed",
                        "trace_id": str(msg.trace.trace_id) if msg.trace else "unknown",
                        "tenant_id": str(msg.tenant_id),
                        "metrics": redact_metrics_payload(metrics),
                    }
                )

                if _is_durable_public_chat_turn(msg):
                    res = await db_client.mark_public_chat_turn_completed(
                        tenant_id=msg.tenant_id,
                        conversation_id=msg.conversation_id,
                        message_id=msg.message.message_id,
                    )
                    if not res:
                        raise RuntimeError("worker_error")

                return True
            else:
                logger.info("Duplicate message ignored.")
                return True
        except Exception as e:
            # Record processing time and log structured metrics even on failure
            if start_time_ms is not None:
                metrics["worker_processing_ms"] = elapsed_ms(start_time_ms)
            logger.info(
                "message_processed",
                extra={
                    "event": "message_processed",
                    "trace_id": str(msg.trace.trace_id) if msg and msg.trace else "unknown",
                    "tenant_id": str(msg.tenant_id) if msg else "unknown",
                    "metrics": redact_metrics_payload(metrics),
                    "error_type": type(e).__name__,
                }
            )
            logger.error("Worker processing failed", extra={"extra_data": {"error_type": type(e).__name__}})

            if msg and idempotency_claimed and idempotency_key:
                await r.delete(idempotency_key)
            try:
                retry_error: Exception | str = (
                    e if msg and _is_durable_public_chat_turn(msg) else str(e)
                )
                await handle_retry(raw_msg, msg, retry_error)
            except Exception:
                logger.exception("Failed to route failed message to retry/DLQ")
                raise
            return True
    finally:
        if renewal_task:
            renewal_task.cancel()
            with suppress(asyncio.CancelledError):
                await renewal_task
        if lock and lock.acquired:
            held_ms = elapsed_ms(lock_held_start_ms)
            metrics["session_lock_held_ms"] = held_ms
            await release_session_lock(r, lock)
        latency_metrics_var.reset(token)

async def _dispatch_response(
    msg: CanonicalMessage,
    response_text: str,
    context: ExecutionContext,
    quick_replies: list[str] | None = None,
    processing_result: dict | None = None,
):
    """
    Send or persist a response through the message's channel.
    Checks DB to ensure the same event_id doesn't trigger duplicate sends.
    """
    from backend.observability.latency import latency_metrics_var, now_ms, elapsed_ms, redact_metrics_payload
    pub_start = now_ms()
    try:
        # Idempotent check: was this response already sent?
        already_sent = await db_client.check_outbound_sent(msg.tenant_id, msg.event_id)
        if already_sent:
            logger.info(f"Outbound already sent for event {msg.event_id}, skipping duplicate")
            return

        provider_error = None
        if context.session_data and context.session_data.state and context.session_data.state.action_ledger and context.session_data.state.action_ledger.last_error:
            provider_error = getattr(context.session_data.state.action_ledger.last_error, "provider_error", None)

        from backend.pipeline.channel_output import is_internal_json_pattern
        is_leak = is_internal_json_pattern(response_text)

        normalized_payload = normalize_channel_payload(response_text, msg.channel_type.value, quick_replies=quick_replies)
        normalized_response = normalized_payload["text"]
        clean_text = None

        is_leak = is_leak or is_internal_json_pattern(normalized_response)
        if is_leak:
            normalized_response = "I couldn't complete that request right now. Please try again in a moment or ask to talk to a team member."

        redacted_info = {}
        if provider_error:
            redacted_info["provider_error"] = provider_error
        if is_leak:
            redacted_info["sanitization_redacted_reason"] = "internal_json_leakage_detected"
            redacted_info["original_leaked_response_len"] = len(str(response_text))

        cards = normalized_payload.get("cards") or []
        if not cards:
            cards = await _catalog_cards_for_context(context, normalized_response)
        if msg.channel_type == ChannelType.AWA_CHAT:
            interactive_data = None if is_leak else normalized_payload.get("interactive_data")
            raw_payload = dict(interactive_data or {})
            raw_payload["conversation_status"] = (
                "escalated"
                if context.session_data.state.workflow_state
                == ConversationState.ESCALATED
                else "active"
            )
            if cards:
                raw_payload["cards"] = cards
            if redacted_info:
                raw_payload.update(redacted_info)
            if processing_result:
                raw_payload.update({
                    "processing_status": str(processing_result.get("processing_status") or "failed")[:32],
                    "recoverable": bool(processing_result.get("recoverable")),
                    "failure_stage": str(processing_result.get("failure_stage") or "unknown")[:48],
                })
            elif str(msg.message.provider_message_id or "").startswith(
                "awa-internal:otp_verified_pending_action:"
            ):
                raw_payload.update({
                    "processing_status": "completed",
                    "recoverable": False,
                })

            # Inject frontend UI signals for confirmation and verification
            ledger = context.session_data.state.action_ledger
            required_next_step = None
            verification_action = None
            if ledger.last_tool_result and ledger.last_tool_result.data:
                required_next_step = ledger.last_tool_result.data.get("required_next_step")
                verification_action = ledger.last_tool_result.data.get("verification_action")

            if ledger.pending_action and not processing_result:
                channel_type = str(msg.channel_type.value or "").lower()
                if ledger.pending_action.requires_verified_customer and channel_type != "whatsapp":
                    required_next_step = "customer_verification"
                    verification_action = ledger.pending_action.verification_action or f"{ledger.pending_action.tool_key.split('.', 1)[0]}_commitment"
                elif ledger.pending_action.requires_confirmation and not required_next_step:
                    required_next_step = "confirm"

            if required_next_step:
                raw_payload["required_next_step"] = required_next_step
            if verification_action:
                raw_payload["verification_action"] = verification_action
            outbound_id = f"response_{msg.event_id}"

            pub_elapsed = elapsed_ms(pub_start)
            curr_metrics = latency_metrics_var.get() or {}
            curr_metrics["response_publish_ms"] = pub_elapsed
            worker_start = curr_metrics.get("worker_start_time_ms")
            if worker_start is not None:
                curr_metrics["worker_processing_ms"] = elapsed_ms(worker_start)
            if settings.ENABLE_WORKER_SESSION_LOCKS and curr_metrics.get("session_lock_acquired"):
                curr_metrics["session_lock_held_ms"] = max(0, (curr_metrics.get("worker_processing_ms") or 0) - (curr_metrics.get("session_lock_wait_ms") or 0))

            raw_payload["metrics"] = redact_metrics_payload(curr_metrics)

            saved = await db_client.save_message(
                message_id=uuid4(),
                tenant_id=msg.tenant_id,
                conversation_id=msg.conversation_id,
                whatsapp_message_id=outbound_id,
                provider_message_id=outbound_id,
                channel_type=ChannelType.AWA_CHAT.value,
                direction="outbound",
                message_type="interactive" if interactive_data else "text",
                message_text=clean_text or normalized_response,
                raw_payload=redact_url_tokens(raw_payload) if raw_payload else None,
            )
            if not saved.get("created") and saved.get("reason") != "duplicate":
                raise DispatchError(f"Failed to persist AWA Chat response: {saved.get('reason')}")
            logger.info(f"AWA Chat response persisted for polling: {outbound_id}")
            try:
                from backend.realtime.public_chat_events import publish_public_chat_message_event
                msg_id = saved.get("id")
                await publish_public_chat_message_event(
                    conversation_id=msg.conversation_id,
                    message_id=msg_id,
                    content=clean_text or normalized_response,
                    raw_payload=redact_url_tokens(raw_payload) if raw_payload else None,
                )
            except Exception as e:
                logger.warning(f"Failed to publish public chat realtime event: {e}")
            return
        # Get customer phone from context or DB
        customer_phone = context.user.phone_number
        if customer_phone == "unknown":
            logger.info(f"Looking up phone number for customer {msg.customer_id} in DB")
            # Run a direct raw SQL query since no helper exists
            async with db_client.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT phone_number FROM customers WHERE id = $1 AND tenant_id = $2",
                    msg.customer_id, msg.tenant_id
                )
                if row and row["phone_number"]:
                    customer_phone = row["phone_number"]
        if customer_phone == "unknown":
            raise DispatchError(f"Customer phone unknown for event {msg.event_id}")
        if not msg.whatsapp_account_id:
            raise DispatchError(f"WhatsApp account missing for event {msg.event_id}")
        account = await db_client.get_whatsapp_account_for_dispatch(msg.tenant_id, msg.whatsapp_account_id)
        account_phone_number_id = account.get("phone_number_id") if account else None
        account_access_token = account.get("access_token") if account else None
        # ---- PHASE 9d: DYNAMIC INTERACTIVE GENERATION ----
        interactive_data = None if is_leak else normalized_payload.get("interactive_data")
        if interactive_data:
            clean_text = normalized_response
        else:
            clean_text, interactive_data = parse_interactive_hints(normalized_response)
        plain_text_fallback = build_plain_text_fallback(clean_text or normalized_response, _interactive_options(interactive_data))
        image_card = cards[0] if len(cards) == 1 and cards[0].get("image_url") else None
        image_caption = _caption_for_card(image_card, clean_text or normalized_response) if image_card else ""
        # Send via WhatsApp with rate-limit evasion
        dispatch_result = {}
        for attempt in range(1, 4):
            if image_card:
                dispatch_result = await whatsapp_client.send_image_message(
                    to_phone=customer_phone,
                    image_url=image_card["image_url"],
                    caption=image_caption,
                    phone_number_id=account_phone_number_id,
                    access_token=account_access_token,
                )
                if not dispatch_result.get("success"):
                    logger.warning("WhatsApp image send failed with safe reason: %s", dispatch_result.get("error"))
                    image_card = None
                    clean_text = plain_text_fallback
            elif interactive_data:
                # Send as interactive buttons/list
                dispatch_result = await whatsapp_client.send_interactive_message(
                    to_phone=customer_phone,
                    interactive_data=interactive_data,
                    phone_number_id=account_phone_number_id,
                    access_token=account_access_token,
                )
            else:
                # Send as plain text
                dispatch_result = await whatsapp_client.send_text_message(
                    to_phone=customer_phone,
                    message_text=clean_text or normalized_response,
                    phone_number_id=account_phone_number_id,
                    access_token=account_access_token,
                )
            if dispatch_result.get("success"):
                break
            if dispatch_result.get("error_code") == 131048:
                delay = 2 ** attempt
                logger.warning(f"Meta Rate Limit 131048 hit! Exponential backoff for {delay} seconds (Attempt {attempt})...")
                await asyncio.sleep(delay)
            else:
                # If interactive send failed, retry as plain text
                if interactive_data:
                    logger.warning("Interactive message failed, falling back to plain text")
                    interactive_data = None
                    clean_text = plain_text_fallback
                else:
                    break
        if not dispatch_result.get("success"):
            raise DispatchError(f"WhatsApp dispatch failed: {dispatch_result.get('error') or dispatch_result}")
        outbound_wa_id = dispatch_result.get("message_id")
        if not outbound_wa_id:
            raise DispatchError("WhatsApp dispatch succeeded without a provider message_id")
        internal_outbound_id = f"response_{msg.event_id}"
        render_status = "image_sent" if image_card else "interactive_sent" if interactive_data else "text_fallback"

        wa_payload = {
            "provider_message_id": outbound_wa_id,
            "source_event_id": str(msg.event_id),
        }
        if interactive_data:
            wa_payload["interactive"] = interactive_data
        if cards:
            wa_payload["cards"] = cards
        if render_status:
            wa_payload["channel_render_status"] = render_status
        if redacted_info:
            wa_payload.update(redacted_info)

        pub_elapsed = elapsed_ms(pub_start)
        curr_metrics = latency_metrics_var.get() or {}
        curr_metrics["response_publish_ms"] = pub_elapsed
        worker_start = curr_metrics.get("worker_start_time_ms")
        if worker_start is not None:
            curr_metrics["worker_processing_ms"] = elapsed_ms(worker_start)
        if settings.ENABLE_WORKER_SESSION_LOCKS and curr_metrics.get("session_lock_acquired"):
            curr_metrics["session_lock_held_ms"] = max(0, (curr_metrics.get("worker_processing_ms") or 0) - (curr_metrics.get("session_lock_wait_ms") or 0))

        wa_payload["metrics"] = redact_metrics_payload(curr_metrics)

        saved = await db_client.save_message(
            message_id=uuid4(),
            tenant_id=msg.tenant_id,
            conversation_id=msg.conversation_id,
            whatsapp_message_id=internal_outbound_id,
            provider_message_id=outbound_wa_id,
            direction="outbound",
            message_type="image" if image_card else "interactive" if interactive_data else "text",
            message_text=clean_text or normalized_response,
            raw_payload=redact_url_tokens(wa_payload) if wa_payload else None,
        )
        if not saved.get("created") and saved.get("reason") != "duplicate":
            raise DispatchError(f"Failed to persist WhatsApp response: {saved.get('reason')}")
        logger.info(f"Response dispatched: {outbound_wa_id}")
    except Exception as e:
        logger.error(f"Response dispatch error: {e}")
        raise
async def _record_message_usage(msg: CanonicalMessage, context: ExecutionContext) -> None:
    llm_usage = context.aggregate_llm_usage()
    ai_tokens_used = (
        int(llm_usage["total_tokens"])
        if llm_usage.get("usage_available") and llm_usage.get("total_tokens") is not None
        else msg.cost_context.ai_tokens_used
    )
    ai_cost = (
        float(llm_usage["cost_usd"])
        if llm_usage.get("cost_available") and llm_usage.get("cost_usd") is not None
        else msg.cost_context.ai_cost
    )
    await usage_guard.record_message_processed(
        tenant_id=msg.tenant_id,
        ai_tokens_used=ai_tokens_used,
        ai_cost=ai_cost,
        compute_cost=msg.cost_context.compute_cost,
        llm_usage=llm_usage,
        conversation_id=msg.conversation_id,
        source_event_id=msg.event_id,
        tenant_timezone=context.config.config_data.get("timezone", "UTC"),
    )
def _interactive_options(interactive_data: dict | None) -> list[str]:
    if not isinstance(interactive_data, dict):
        return []
    action = interactive_data.get("action") if isinstance(interactive_data.get("action"), dict) else {}
    if interactive_data.get("type") == "button":
        return [
            str(button.get("reply", {}).get("title") or "").strip()
            for button in action.get("buttons") or []
            if isinstance(button, dict)
        ]
    if interactive_data.get("type") == "list":
        options: list[str] = []
        for section in action.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for row in section.get("rows") or []:
                if isinstance(row, dict):
                    options.append(str(row.get("title") or "").strip())
        return options
    return []

def _caption_for_card(card: dict | None, fallback_text: str) -> str:
    if not card:
        return fallback_text[:1024]
    parts = [
        str(card.get("title") or "").strip(),
        str(card.get("subtitle") or "").strip(),
        str(card.get("description") or "").strip(),
    ]
    action = "Reply to order, book, or ask for more details."
    text = "\n".join(part for part in parts if part)
    if text:
        text = f"{text}\n\n{action}"
    else:
        text = fallback_text or action
    return text[:1024]

async def _catalog_cards_for_context(context: ExecutionContext, response_text: str) -> list[dict]:
    incoming_text = str((context.incoming_message or {}).get("text") or "")
    haystack = f"{incoming_text}\n{response_text}".lower()
    if not haystack.strip():
        return []
    async with db_client.acquire() as conn:
        try:
            rows = await conn.fetch(
                """
                SELECT i.id, i.name, i.description, i.price, i.currency,
                       ma.served_url, ma.thumbnail_url, ma.alt_text
                FROM catalog_items i
                JOIN media_assets ma
                  ON ma.tenant_id = i.tenant_id
                 AND ma.entity_type = 'catalog_item'
                 AND ma.entity_id = i.id
                 AND ma.status = 'active'
                 AND ma.is_primary = TRUE
                WHERE i.tenant_id = $1
                  AND i.deleted_at IS NULL
                  AND i.is_active = TRUE
                  AND i.is_available = TRUE
                ORDER BY i.created_at DESC
                LIMIT 25
                """,
                context.tenant.id,
            )
        except Exception as exc:
            logger.debug("catalog card lookup skipped: %s", exc)
            return []
    cards: list[dict] = []
    for db_row in rows:
        row = dict(db_row)
        name = str(row.get("name") or "")
        if not name:
            continue
        normalized_name = name.lower()
        tokens = [token for token in normalized_name.replace("-", " ").replace("_", " ").split() if len(token) >= 3]
        if normalized_name not in haystack and not all(token in haystack for token in tokens[:3]):
            continue
        price = row.get("price")
        currency = row.get("currency") or "INR"
        subtitle = f"{currency} {price}" if price not in (None, "") else ""
        cards.append(
            {
                "type": "catalog_item",
                "id": str(row.get("id")),
                "title": name,
                "subtitle": subtitle,
                "description": str(row.get("description") or "")[:500],
                "image_url": row.get("served_url") or "",
                "thumbnail_url": row.get("thumbnail_url") or row.get("served_url") or "",
                "image_alt": row.get("alt_text") or name,
                "actions": ["order", "ask_more"],
            }
        )
        if len(cards) >= 3:
            break
    return cards
async def handle_retry(raw_msg: str, msg: CanonicalMessage | None, error: Exception | str):
    """Retry with exponential backoff, route to DLQ on max retries."""
    r = await get_redis()
    if msg is None:
        # Unparseable message — send directly to DLQ
        logger.error("Unparseable message sent to DLQ")
        await r.lpush(DLQ_KEY, raw_msg)
        return
    retry_count = msg.processing.retry_count + 1
    if retry_count > MAX_RETRIES:
        # Max retries exceeded — enrich and route to DLQ
        logger.warning(f"Max retries exceeded for {msg.event_id}, routing to DLQ")

        if _is_durable_public_chat_turn(msg):
            outcome = await db_client.finalize_turn_terminal_failure(
                tenant_id=msg.tenant_id,
                conversation_id=msg.conversation_id,
                inbound_message_id=msg.message.message_id,
                event_id=msg.event_id,
                retry_count=retry_count,
            )

            if outcome.outcome_code in ("terminal_created", "terminal_already_exists"):
                try:
                    from backend.realtime.public_chat_events import publish_public_chat_terminal_failure
                    await publish_public_chat_terminal_failure(
                        conversation_id=msg.conversation_id,
                        message_id=outcome.terminal_message_id,
                        content=outcome.terminal_message_text,
                        created_at=outcome.terminal_message_created_at,
                        source_event_id=str(msg.event_id)
                    )
                except Exception as e:
                    logger.error("SSE publication failed for terminalization", extra={"extra_data": {"event_id": str(msg.event_id), "conversation_id": str(msg.conversation_id), "error_type": type(e).__name__}})

            sanitized_dlq = {
                "event_id": str(msg.event_id),
                "tenant_id": str(msg.tenant_id),
                "conversation_id": str(msg.conversation_id),
                "message_id": str(msg.message.message_id),
                "source_type": "awa_chat",
                "channel_type": "awa_chat",
                "processing_status": "terminal_failed",
                "failure_code": "processing_retries_exhausted",
                "retry_count": retry_count,
                "error_type": type(error).__name__ if isinstance(error, Exception) else "worker_error"
            }
            import json
            await r.lpush(DLQ_KEY, json.dumps(sanitized_dlq))
            return

        enriched = dlq_manager.enrich_for_dlq(msg, reason=str(error), failure_code="MAX_RETRIES_EXCEEDED")
        await r.lpush(DLQ_KEY, enriched.model_dump_json())
        return
    # Exponential backoff
    delay = BASE_RETRY_DELAY ** retry_count
    logger.info(f"Retrying {msg.event_id} in {delay}s (attempt {retry_count}/{MAX_RETRIES})")
    msg.processing.retry_count = retry_count

    if _is_durable_public_chat_turn(msg):
        res = await db_client.mark_public_chat_turn_failed(
            tenant_id=msg.tenant_id,
            conversation_id=msg.conversation_id,
            message_id=msg.message.message_id,
            retry_count=retry_count
        )
        if not res:
            raise RuntimeError("worker_error")

    await asyncio.sleep(delay)
    await enqueue_message(r, msg.model_dump_json())

    if _is_durable_public_chat_turn(msg):
        res = await db_client.mark_public_chat_turn_retry_queued(
            tenant_id=msg.tenant_id,
            conversation_id=msg.conversation_id,
            message_id=msg.message.message_id,
            retry_count=retry_count
        )
        if not res:
            raise RuntimeError("worker_error")
async def worker_loop():
    logger.info("Worker started. Listening on Redis...")
    r = await get_redis()
    last_recovery_at = 0.0
    heartbeat_task = asyncio.create_task(_worker_heartbeat_loop(r))

    concurrency_mode = settings.WORKER_CONCURRENCY_MODE
    task_pool_size = max(1, settings.WORKER_TASK_POOL_SIZE)
    sem = asyncio.Semaphore(task_pool_size) if concurrency_mode == "task_pool" else None
    active_tasks = set()

    async def process_task(raw_msg: str, start_ms: float, claim_ms: float):
        try:
            processed = await process_message(
                raw_msg,
                wall_claim_time_ms=claim_ms,
                start_time_ms=start_ms
            )
            if processed:
                await ack_message(r, raw_msg)
        except Exception:
            logger.exception("Message processing task failed, left in processing queue for stale recovery")
        finally:
            if sem:
                sem.release()

    try:
        while not _shutdown_event.is_set():
            try:
                # Pause-gate: skip processing while system:workers:paused is set in Redis.
                # The heartbeat loop continues independently so health dashboards stay accurate.
                if await r.exists(WORKERS_PAUSED_KEY):
                    logger.debug("Main worker paused by operator signal — skipping poll iteration")
                    await asyncio.sleep(5)
                    continue

                now = time.time()
                if now - last_recovery_at >= RECOVERY_INTERVAL_SECONDS:
                    await recover_stale_messages(r)
                    last_recovery_at = now

                if sem:
                    await sem.acquire()

                sem_released = False
                try:
                    raw_msg = await claim_message(r, timeout=5)
                    if not raw_msg:
                        if sem:
                            sem.release()
                            sem_released = True
                        continue

                    from backend.observability.latency import now_ms
                    start_processing_time_ms = now_ms()
                    wall_claim_time_ms = time.time() * 1000.0

                    if sem:
                        task = asyncio.create_task(process_task(raw_msg, start_processing_time_ms, wall_claim_time_ms))
                        active_tasks.add(task)
                        task.add_done_callback(active_tasks.discard)
                    else:
                        try:
                            processed = await process_message(
                                raw_msg,
                                wall_claim_time_ms=wall_claim_time_ms,
                                start_time_ms=start_processing_time_ms
                            )
                        except Exception:
                            logger.exception("Message left in processing queue for stale recovery")
                            processed = False
                        if processed:
                            await ack_message(r, raw_msg)
                except Exception as e:
                    if sem and not sem_released:
                        sem.release()
                    raise e
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                await asyncio.sleep(1)
    finally:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
        if active_tasks:
            logger.info(f"Waiting for {len(active_tasks)} active tasks to finish...")
            await asyncio.gather(*active_tasks, return_exceptions=True)
    logger.info("Worker shutting down gracefully...")
WORKER_ID = str(uuid4())[:8]

async def _worker_heartbeat_loop(redis_client) -> None:
    while not _shutdown_event.is_set():
        try:
            await redis_client.set(
                WORKER_HEARTBEAT_KEY,
                datetime.now(timezone.utc).isoformat(),
                ex=WORKER_HEARTBEAT_TTL_SECONDS,
            )
            await redis_client.set(
                f"worker:main:{WORKER_ID}:heartbeat",
                datetime.now(timezone.utc).isoformat(),
                ex=WORKER_HEARTBEAT_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning(f"Worker heartbeat write failed: {e}")
        await asyncio.sleep(WORKER_HEARTBEAT_INTERVAL_SECONDS)
async def shutdown():
    """Graceful shutdown handler."""
    logger.info("Shutdown signal received")
    _shutdown_event.set()
    global _redis_pool
    if _redis_pool:
        await _redis_pool.close()
        _redis_pool = None
if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    # Register signal handlers for graceful shutdown
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(shutdown()))
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
    try:
        loop.run_until_complete(worker_loop())
    except KeyboardInterrupt:
        loop.run_until_complete(shutdown())
    finally:
        loop.close()
