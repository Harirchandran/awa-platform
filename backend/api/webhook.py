from fastapi import APIRouter, Request, HTTPException, Depends
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import re
import time
import hashlib
import hmac
import json
from uuid import uuid4, UUID
from fastapi.responses import JSONResponse, PlainTextResponse
from backend.core.config import settings
from backend.core.redis_client import redis_client
from backend.schemas.canonical_message import CanonicalMessage, SourceType, MessageDirection, MessageType, ChannelType
from backend.db.client import db_client
from backend.domains.customers.service import customer_service
from backend.domains.payments.service import payment_service
from backend.schemas.response import APIResponse
from backend.schemas.config import normalize_tenant_config
from backend.ops.logger import logger
from backend.pipeline.burst_buffer import burst_buffer
from backend.pipeline.reliable_queue import PROCESSING_KEY, QUEUE_KEY
webhook_router = APIRouter()
# Replay protection window (seconds)
REPLAY_WINDOW_SECONDS = 300  # ±5 minutes
RAZORPAY_WEBHOOK_IDEMPOTENCY_TTL_SECONDS = 7 * 24 * 60 * 60
def _first_dict(value: Any) -> dict:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}
def _whatsapp_webhook_value(payload: dict) -> dict:
    entry = _first_dict(payload.get("entry"))
    change = _first_dict(entry.get("changes"))
    value = change.get("value") if isinstance(change, dict) else {}
    return value if isinstance(value, dict) else {}
def _normalize_phone_digits(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))
def _razorpay_event_id(payload: dict, header_event_id: Optional[str] = None) -> str:
    event_id = header_event_id or payload.get("id") or payload.get("event_id")
    if event_id:
        return str(event_id)
    event_name = str(payload.get("event") or "unknown")
    payload_data = payload.get("payload") or {}
    subscription = (payload_data.get("subscription") or {}).get("entity") or {}
    payment = (payload_data.get("payment") or {}).get("entity") or {}
    base = {
        "event": event_name,
        "subscription_id": subscription.get("id") or payment.get("subscription_id"),
        "payment_id": payment.get("id"),
        "created_at": payload.get("created_at") or subscription.get("created_at") or payment.get("created_at"),
    }
    digest = hashlib.sha256(json.dumps(base, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:32]
    return f"derived:{digest}"
async def _claim_razorpay_event(payload: dict, header_event_id: Optional[str] = None) -> tuple[bool, str]:
    event_id = _razorpay_event_id(payload, header_event_id)
    key = f"razorpay:webhook:{event_id}"
    try:
        claimed = await redis_client.setnx(key, "1")
        if claimed:
            await redis_client.expire(key, RAZORPAY_WEBHOOK_IDEMPOTENCY_TTL_SECONDS)
        return bool(claimed), event_id
    except Exception as exc:
        if settings.ENV == "production":
            logger.error("Razorpay webhook idempotency unavailable: %s", exc)
            raise HTTPException(status_code=503, detail="Webhook idempotency unavailable") from exc
        logger.warning("Razorpay webhook idempotency unavailable in non-production: %s", exc)
        return True, event_id
async def _claim_whatsapp_event(wa_message_id: str) -> bool:
    key = f"whatsapp:webhook:{wa_message_id}"
    try:
        claimed = await redis_client.setnx(key, "1")
        if claimed:
            await redis_client.expire(key, 7 * 24 * 60 * 60)
        return bool(claimed)
    except Exception as exc:
        if settings.ENV == "production":
            logger.error("WhatsApp webhook idempotency unavailable: %s", exc)
            raise HTTPException(status_code=503, detail="Webhook protection is temporarily unavailable.")
        logger.warning("WhatsApp webhook idempotency unavailable in non-production: %s", exc)
        return True
def _extract_webhook_timestamp(payload: dict) -> Optional[int]:
    """Return the best available webhook timestamp for replay protection."""
    entry = _first_dict(payload.get("entry"))
    entry_time = entry.get("time")
    if entry_time:
        return int(entry_time)
    value = _whatsapp_webhook_value(payload)
    for item in value.get("messages") or []:
        if item.get("timestamp"):
            return int(item["timestamp"])
    for item in value.get("statuses") or []:
        if item.get("timestamp"):
            return int(item["timestamp"])
    return None
# ==================== RAZORPAY WEBHOOK ====================
@webhook_router.post("/razorpay")
async def razorpay_webhook(request: Request):
    """
    Handles asynchronous payment events from Razorpay.
    """
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    if settings.allow_mock_billing and (not secret or secret == "dummy_razorpay_webhook_secret"):
        logger.warning(f"RAZORPAY WEBHOOK: Dev bypass triggered for {signature[:5]}")
        pass # Allow bypass in local dev
    elif not secret:
        raise HTTPException(status_code=500, detail="Razorpay webhook secret is not configured")
    else:
        # Verify Razorpay signature
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=400, detail="Invalid signature")
    try:
        payload = json.loads(raw_body)
        event_name = payload.get("event")
        payload_data = payload.get("payload", {})
        header_event_id = request.headers.get("x-razorpay-event-id")
        claimed, event_id = await _claim_razorpay_event(payload, header_event_id)
        if not claimed:
            logger.info("RAZORPAY WEBHOOK: duplicate event ignored: %s", event_id)
            return JSONResponse(status_code=200, content={"status": "ok", "duplicate": True})
        logger.info(f"RAZORPAY WEBHOOK: Received {event_name}")
        if event_name and event_name.startswith("payment_link."):
            link_entity = payload_data.get("payment_link", {}).get("entity", {})
            link_id = link_entity.get("id")
            if not link_id:
                logger.warning("RAZORPAY WEBHOOK: payment_link event without link ID")
                return JSONResponse(status_code=200, content={"status": "ignored", "reason": "missing_link_id"})
            payment_req = await payment_service.get_payment_request_by_provider_link_id(link_id)
            if not payment_req:
                logger.warning(f"RAZORPAY WEBHOOK: No payment request found for link ID {link_id}")
                return JSONResponse(status_code=200, content={"status": "ignored", "reason": "unknown_payment_link_id"})
            action = None
            if event_name == "payment_link.paid":
                action = "mark_paid"
            elif event_name == "payment_link.partially_paid":
                action = "partial_payment"
            elif event_name == "payment_link.cancelled":
                action = "cancel"
            elif event_name == "payment_link.expired":
                action = "expire"
            if not action:
                logger.info(f"RAZORPAY WEBHOOK: Unhandled payment link event {event_name}")
                await db_client.write_audit_log(
                    tenant_id=payment_req["tenant_id"],
                    actor_id=None,
                    action="unhandled_payment_link_webhook",
                    resource_type="payment",
                    resource_id=payment_req["id"],
                    details={"event": event_name, "event_id": event_id},
                    ip_address="razorpay",
                )
                return JSONResponse(status_code=200, content={"status": "ignored", "reason": "unhandled_event_type"})
            payment_entity = payload_data.get("payment", {}).get("entity", {}) or {}
            provider_payment_id = payment_entity.get("id")
            # Sanitize payload to avoid storing sensitive customer/payment data
            sanitized_payload = {
                "event_id": event_id,
                "event_type": event_name,
                "payment_link_id": link_id,
                "status": link_entity.get("status"),
                "amount": link_entity.get("amount"),
                "currency": link_entity.get("currency"),
            }
            if provider_payment_id:
                sanitized_payload["payment_id"] = provider_payment_id
            tenant_id = payment_req["tenant_id"]
            await payment_service.action_payment_request(
                tenant_id=tenant_id,
                payment_id=payment_req["id"],
                action=action,
                provider_payment_id=provider_payment_id,
                provider_event_id=event_id,
                provider_payload=sanitized_payload
            )
            return JSONResponse(status_code=200, content={"status": "ok"})
        elif event_name in ["subscription.charged", "subscription.authenticated"]:
            sub_entity = payload_data.get("subscription", {}).get("entity", {})
            sub_id = sub_entity.get("id")
            if sub_id:
                async with db_client.acquire() as conn:
                    tenant_row = await conn.fetchrow("""
                        UPDATE tenants
                        SET status = 'active',
                            razorpay_customer_id = COALESCE($2, razorpay_customer_id),
                            current_period_end = CASE
                                WHEN $3::bigint IS NOT NULL THEN to_timestamp($3::bigint)
                                ELSE NOW() + INTERVAL '1 month'
                            END
                        WHERE razorpay_subscription_id = $1
                        RETURNING id, plan_code, status, razorpay_customer_id, current_period_end
                    """, sub_id, sub_entity.get("customer_id"), sub_entity.get("current_end"))
                if tenant_row:
                    await db_client.write_audit_log(
                        tenant_id=tenant_row["id"],
                        actor_id=None,
                        action="razorpay_subscription_activated",
                        resource_type="billing",
                        resource_id=tenant_row["id"],
                        details={"event": event_name, "event_id": event_id, "subscription_id": sub_id, "tenant": dict(tenant_row)},
                        ip_address="razorpay",
                    )
                logger.info(f"RAZORPAY: Activated tenant for sub: {sub_id}")
        elif event_name == "subscription.pending":
            sub_entity = payload_data.get("subscription", {}).get("entity", {})
            sub_id = sub_entity.get("id")
            if sub_id:
                async with db_client.acquire() as conn:
                    tenant_row = await conn.fetchrow("""
                        UPDATE tenants
                        SET razorpay_customer_id = COALESCE($2, razorpay_customer_id)
                        WHERE razorpay_subscription_id = $1
                        RETURNING id, plan_code, status, razorpay_customer_id
                    """, sub_id, sub_entity.get("customer_id"))
                if tenant_row:
                    await db_client.write_audit_log(
                        tenant_id=tenant_row["id"],
                        actor_id=None,
                        action="razorpay_subscription_pending",
                        resource_type="billing",
                        resource_id=tenant_row["id"],
                        details={"event": event_name, "event_id": event_id, "subscription_id": sub_id, "tenant": dict(tenant_row)},
                        ip_address="razorpay",
                    )
        elif event_name in ["subscription.cancelled", "subscription.halted"]:
            sub_entity = payload_data.get("subscription", {}).get("entity", {})
            sub_id = sub_entity.get("id")
            if sub_id:
                async with db_client.acquire() as conn:
                    tenant_row = await conn.fetchrow("""
                        UPDATE tenants
                        SET status = 'suspended'
                        WHERE razorpay_subscription_id = $1
                        RETURNING id, plan_code, status
                    """, sub_id)
                if tenant_row:
                    await db_client.write_audit_log(
                        tenant_id=tenant_row["id"],
                        actor_id=None,
                        action="razorpay_subscription_suspended",
                        resource_type="billing",
                        resource_id=tenant_row["id"],
                        details={"event": event_name, "event_id": event_id, "subscription_id": sub_id, "tenant": dict(tenant_row)},
                        ip_address="razorpay",
                    )
                logger.warning(f"RAZORPAY: Suspended tenant for sub: {sub_id}")
        elif event_name == "subscription.completed":
            sub_entity = payload_data.get("subscription", {}).get("entity", {})
            sub_id = sub_entity.get("id")
            if sub_id:
                async with db_client.acquire() as conn:
                    tenant_row = await conn.fetchrow("""
                        UPDATE tenants
                        SET status = 'expired',
                            razorpay_customer_id = COALESCE($2, razorpay_customer_id),
                            current_period_end = CASE
                                WHEN $3::bigint IS NOT NULL THEN to_timestamp($3::bigint)
                                ELSE current_period_end
                            END
                        WHERE razorpay_subscription_id = $1
                        RETURNING id, plan_code, status, razorpay_customer_id, current_period_end
                    """, sub_id, sub_entity.get("customer_id"), sub_entity.get("current_end") or sub_entity.get("ended_at"))
                if tenant_row:
                    await db_client.write_audit_log(
                        tenant_id=tenant_row["id"],
                        actor_id=None,
                        action="razorpay_subscription_completed",
                        resource_type="billing",
                        resource_id=tenant_row["id"],
                        details={"event": event_name, "event_id": event_id, "subscription_id": sub_id, "tenant": dict(tenant_row)},
                        ip_address="razorpay",
                    )
        elif event_name == "payment.failed":
            payment_entity = payload_data.get("payment", {}).get("entity", {})
            sub_id = payment_entity.get("subscription_id")
            if sub_id:
                async with db_client.acquire() as conn:
                    tenant_row = await conn.fetchrow("""
                        SELECT id, plan_code, status, razorpay_customer_id
                        FROM tenants
                        WHERE razorpay_subscription_id = $1
                    """, sub_id)
                if tenant_row:
                    await db_client.write_audit_log(
                        tenant_id=tenant_row["id"],
                        actor_id=None,
                        action="billing_subscription_payment_failed",
                        resource_type="billing",
                        resource_id=tenant_row["id"],
                        details={
                            "event": event_name,
                            "event_id": event_id,
                            "subscription_id": sub_id,
                            "payment_id": payment_entity.get("id"),
                            "error_code": payment_entity.get("error_code"),
                            "error_description": payment_entity.get("error_description"),
                            "tenant": dict(tenant_row),
                            "dunning_ready": True,
                            "notification_sent": False,
                            "notification_gap": "No billing dunning notification channel is configured.",
                        },
                        ip_address="razorpay",
                    )
                    await db_client.write_audit_log(
                        tenant_id=tenant_row["id"],
                        actor_id=None,
                        action="razorpay_payment_failed",
                        resource_type="payment",
                        resource_id=tenant_row["id"],
                        details={
                            "event": event_name,
                            "event_id": event_id,
                            "subscription_id": sub_id,
                            "payment_id": payment_entity.get("id"),
                            "error_code": payment_entity.get("error_code"),
                            "error_description": payment_entity.get("error_description"),
                            "tenant": dict(tenant_row),
                        },
                        ip_address="razorpay",
                    )
        elif event_name == "subscription.paused":
            sub_entity = payload_data.get("subscription", {}).get("entity", {})
            sub_id = sub_entity.get("id")
            if sub_id:
                async with db_client.acquire() as conn:
                    tenant_row = await conn.fetchrow("""
                        UPDATE tenants
                        SET status = 'suspended',
                            razorpay_customer_id = COALESCE($2, razorpay_customer_id)
                        WHERE razorpay_subscription_id = $1
                        RETURNING id, plan_code, status, razorpay_customer_id
                    """, sub_id, sub_entity.get("customer_id"))
                if tenant_row:
                    await db_client.write_audit_log(
                        tenant_id=tenant_row["id"],
                        actor_id=None,
                        action="razorpay_subscription_paused",
                        resource_type="billing",
                        resource_id=tenant_row["id"],
                        details={"event": event_name, "event_id": event_id, "subscription_id": sub_id, "tenant": dict(tenant_row)},
                        ip_address="razorpay",
                    )
        return JSONResponse(status_code=200, content={"status": "ok"})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"RAZORPAY WEBHOOK FAILED: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
# ==================== HEALTH CHECK ====================
@webhook_router.get("/webhook/health")
async def health_check():
    """Health check endpoint with real service checks + queue depth monitoring (Phase 9h)"""
    redis_status = "healthy"
    db_status = "healthy"
    queue_depth = 0
    processing_depth = 0
    queue_alert = "ok"
    try:
        await redis_client.ping()
        queue_depth = await redis_client.llen(QUEUE_KEY)
        processing_depth = await redis_client.llen(PROCESSING_KEY)
        total_depth = queue_depth + processing_depth
        if total_depth > 50:
            queue_alert = "critical"
        elif total_depth > 10:
            queue_alert = "warning"
    except Exception as e:
        redis_status = f"unhealthy: {e}"
    try:
        async with db_client.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as e:
        db_status = f"unhealthy: {e}"
    overall = "healthy" if redis_status == "healthy" and db_status == "healthy" else "degraded"
    # H-6: Limit health endpoint detail in production to prevent information leakage
    if settings.ENV == "production":
        return APIResponse.create_success(data={"status": overall})
    return APIResponse.create_success(data={
        "status": overall,
        "queue_depth": queue_depth,
        "processing_depth": processing_depth,
        "queue_alert": queue_alert,
        "services": {
            "redis": redis_status,
            "database": db_status,
            "api": "healthy"
        },
        "version": "1.0.0"
    })
# ==================== HMAC VALIDATION ====================
async def validate_signature(request: Request):
    """Validate WhatsApp webhook signature using App Secret (Part V-A §12)"""
    if settings.allow_webhook_signature_bypass:
        return True
    signature = request.headers.get("X-Hub-Signature-256")
    if not signature:
        raise HTTPException(status_code=403, detail="Missing signature")
    app_secret = settings.WHATSAPP_APP_SECRET
    if not app_secret:
        raise HTTPException(status_code=500, detail="WhatsApp app secret is not configured")
    # Enforce maximum body size of 2MB to prevent OOM DoS
    MAX_SIZE = 2 * 1024 * 1024
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_SIZE:
                raise HTTPException(status_code=413, detail="Payload too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")

    if hasattr(request, "stream"):
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_SIZE:
                raise HTTPException(status_code=413, detail="Payload too large")
            chunks.append(chunk)
        body = b"".join(chunks)
        request._body = body
    else:
        body = await request.body()
        if len(body) > MAX_SIZE:
            raise HTTPException(status_code=413, detail="Payload too large")
    # Replay protection: check timestamp freshness (±5 min window)
    try:
        payload = json.loads(body)
        webhook_time = _extract_webhook_timestamp(payload)
        if webhook_time is None:
            raise HTTPException(status_code=403, detail="Missing webhook timestamp")
        now = int(time.time())
        if abs(now - webhook_time) > REPLAY_WINDOW_SECONDS:
            raise HTTPException(status_code=403, detail="Stale webhook payload")
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Invalid webhook timestamp") from exc
    except (json.JSONDecodeError, TypeError):
        pass  # Let the main handler deal with parse errors
    expected_signature = hmac.new(
        app_secret.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(f"sha256={expected_signature}", signature):
        raise HTTPException(status_code=403, detail="Invalid signature")
    return True
# ==================== WEBHOOK RECEIVER ====================
@webhook_router.post("/webhook")
async def receive_webhook(request: Request, valid: bool = Depends(validate_signature)):
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    value = _whatsapp_webhook_value(payload)
    messages = value.get("messages") or []
    statuses = value.get("statuses") or []
    metadata = value.get("metadata") or {}
    if not messages and not statuses:
        return APIResponse.create_success(data={"status": "ignored_no_messages_or_statuses"})
    # ---- TENANT ROUTING (Part XIV §61) ----
    # Resolve phone_number_id from WhatsApp webhook metadata to tenant
    phone_number_id = metadata.get("phone_number_id", "")
    display_phone = metadata.get("display_phone_number", "")
    tenant_info = None
    if phone_number_id:
        tenant_info = await db_client.get_tenant_by_wa_phone_number_id(phone_number_id)
    if not tenant_info and display_phone:
        tenant_info = await db_client.get_tenant_by_wa_phone(display_phone)
    if not tenant_info:
        logger.warning(
            "No tenant found for WhatsApp webhook "
            f"phone_number_id_present={bool(phone_number_id)} display_phone_present={bool(display_phone)}"
        )
        raise HTTPException(status_code=404, detail="Unknown WhatsApp account -- no tenant mapping")
    tenant_id = tenant_info["tenant_id"]
    tenant_slug = tenant_info.get("tenant_slug")
    whatsapp_account_id = tenant_info.get("whatsapp_account_id")
    # Check tenant status
    if tenant_info.get("tenant_status") not in {"active", "trial"}:
        logger.warning(f"Tenant {tenant_id} is {tenant_info.get('tenant_status')}")
        raise HTTPException(status_code=403, detail="Tenant is suspended or inactive")
    if messages:
        if not whatsapp_account_id and phone_number_id:
            whatsapp_account_id = await db_client.get_whatsapp_account_id(phone_number_id)
        if not whatsapp_account_id:
            logger.warning(
                "No WhatsApp account mapping for webhook message "
                f"tenant={tenant_id} phone_number_id_present={bool(phone_number_id)} "
                f"display_phone_present={bool(display_phone)}"
            )
            raise HTTPException(status_code=404, detail="Unknown WhatsApp account -- no account mapping")
    # ---- STATUS WEBHOOK HANDLING (Meta API v23) ----
    if statuses:
        status_count = 0
        logger.info(f"Received {len(statuses)} status updates via webhook")
        for st in statuses:
            try:
                wa_message_id = st.get("id")
                status = st.get("status")  # sent, delivered, read, failed
                timestamp = st.get("timestamp")
                if wa_message_id and status and timestamp:
                    dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
                    await db_client.update_message_status(tenant_id, wa_message_id, status)
                    status_count += 1
                    # ---- PHASE 9i: FAILED MESSAGE ALERT ----
                    if status == "failed":
                        errors = st.get("errors", [])
                        error_detail = errors[0] if errors else {"code": "unknown", "title": "Unknown failure"}
                        logger.error(f"Message delivery FAILED: {wa_message_id} — {error_detail}")
                        try:
                            # Resolve tenant from the recipient_id in status
                            recipient_id = st.get("recipient_id", "unknown")
                            await db_client.write_audit_log(
                                tenant_id=tenant_id,
                                actor_id=None,
                                action="message_delivery_failed",
                                resource_type="message",
                                resource_id=None,
                                details={
                                    "provider_message_id": wa_message_id,
                                    "error": error_detail,
                                    "recipient": recipient_id,
                                },
                                ip_address="system"
                            )
                        except Exception as audit_err:
                            logger.warning(f"Could not write failure audit log: {audit_err}")
            except Exception as e:
                logger.error(f"Failed to process status webhook: {e}")
        # If there are NO messages, we can return early as we processed the statuses
        if not messages:
            return APIResponse.create_success(data={"status": "processed_statuses", "count": status_count})
    # ---- GET ACTIVE CONFIG VERSION ----
    config_version = await db_client.get_active_config_version(tenant_id)
    if config_version is None:
        config_version = 1  # Fallback
    # ---- LOAD TENANT CONFIG for blocklist check (Phase 9b) ----
    ignored_numbers = []
    try:
        tenant_config = await db_client.get_tenant_config(tenant_id, config_version)
        if tenant_config:
            cjson = tenant_config.get("config_json", {})
            if isinstance(cjson, str):
                cjson = json.loads(cjson)
            cjson = normalize_tenant_config(cjson)
            ignored_numbers = cjson.get("ignored_numbers", [])
    except Exception as e:
        logger.warning(f"Could not load config for blocklist check: {e}")
    processed_count = 0
    for msg in messages:
        wa_message_id = msg.get("id")
        claimed = False
        if wa_message_id:
            if not await _claim_whatsapp_event(wa_message_id):
                logger.info(f"WhatsApp duplicate message ignored: {wa_message_id}")
                continue
            claimed = True
        try:
            # ---- CUSTOMER ROUTING (Part IV §22) ----
            sender_phone = msg.get("from", "")
            sender_name = None
            contacts = value.get("contacts", [])
            if contacts:
                profile = contacts[0].get("profile", {})
                sender_name = profile.get("name")
            # ---- PHASE 9b: IGNORED NUMBERS BLOCKLIST ----
            normalized_sender_phone = _normalize_phone_digits(sender_phone)
            normalized_ignored_numbers = {
                normalized
                for normalized in (_normalize_phone_digits(number) for number in ignored_numbers)
                if normalized
            }
            if normalized_sender_phone and normalized_sender_phone in normalized_ignored_numbers:
                logger.info(f"Message from {sender_phone} ignored (blocklist)")
                continue
            customer = await customer_service.get_or_create_customer(
                tenant_id=tenant_id,
                phone_number=sender_phone,
                name=sender_name
            )
            customer_id = customer["id"]
            # ---- CONSENT CHECK (Phase 9a: implied consent — tenant guarantees privacy) ----
            consent_status = customer.get("consent_status", "granted")
            if consent_status == "withdrawn":
                # Customer explicitly withdrew consent — reject processing
                logger.info(f"Customer {customer_id} — consent withdrawn, rejecting message")
                continue
            # ---- CONVERSATION ROUTING (Part IV §22) ----
            conversation = await db_client.get_or_create_conversation(
                tenant_id=tenant_id,
                customer_id=customer_id,
                channel_type=ChannelType.WHATSAPP.value,
                channel_account_id=whatsapp_account_id,
            )
            conversation_id = conversation["id"]
            # ---- BUILD CANONICAL MESSAGE ----
            wa_message_id = msg.get("id", str(uuid4()))
            msg_type = msg.get("type", "text")
            # ---- DATA MINIMIZATION (DPDP §6 / GDPR Art. 5(1)(c)) ----
            # Strip raw_payload to essential fields only — do NOT store full webhook body
            stripped_payload = {
                "message_id": wa_message_id,
                "type": msg_type,
                "timestamp": msg.get("timestamp"),
            }
            seq_number = await db_client.get_next_sequence_number(tenant_id, conversation_id)
            canonical = CanonicalMessage(
                event_id=uuid4(),
                source_type=SourceType.WEBHOOK,
                channel_type=ChannelType.WHATSAPP,
                channel_account_id=whatsapp_account_id,
                tenant_id=tenant_id,
                whatsapp_account_id=whatsapp_account_id,
                conversation_id=conversation_id,
                conversation_sequence_number=seq_number,
                customer_id=customer_id,
                config_version=config_version,
                worker_metadata={"worker_version": "1.0.0", "tenant_slug": tenant_slug},
                message={
                    "message_id": uuid4(),
                    "provider_message_id": wa_message_id,
                    "direction": MessageDirection.INBOUND,
                    "timestamp": datetime.now(timezone.utc),
                    "message_type": _resolve_message_type(msg_type),
                    "text": _extract_message_text(msg, msg_type)
                },
                processing={
                    "first_received_at": datetime.now(timezone.utc),
                    "retry_count": 0,
                    "max_retries": 3
                },
                trace={
                    "trace_id": uuid4(),
                    "created_at": datetime.now(timezone.utc)
                }
            )
            # ---- ENQUEUE TO BURST BUFFER (Phase 9c: sliding window) ----
            canonical_json = canonical.model_dump_json()
            await burst_buffer.add_message(tenant_id, sender_phone, canonical_json)
            processed_count += 1
            logger.info(f"Buffered message {canonical.event_id} for tenant {tenant_id}")
        except Exception as e:
            if claimed and wa_message_id:
                try:
                    await redis_client.delete(f"whatsapp:webhook:{wa_message_id}")
                except Exception as del_err:
                    logger.warning(f"Failed to clear webhook claim on error: {del_err}")
            raise e
    return APIResponse.create_success(data={
        "status": "queued", "count": processed_count
    })
# ==================== NON-TEXT MESSAGE HELPERS (Phase 9e) ====================
def _resolve_message_type(msg_type: str) -> MessageType:
    """Map WhatsApp message type string to our MessageType enum."""
    type_map = {
        "text": MessageType.TEXT,
        "image": MessageType.IMAGE,
        "video": MessageType.VIDEO,
        "audio": MessageType.AUDIO,
        "document": MessageType.DOCUMENT,
        "sticker": MessageType.STICKER,
        "location": MessageType.LOCATION,
        "interactive": MessageType.INTERACTIVE,
        "contacts": MessageType.CONTACTS,
    }
    return type_map.get(msg_type, MessageType.UNKNOWN)
def _extract_message_text(msg: dict, msg_type: str) -> str:
    """Extract meaningful text from any WhatsApp message type."""
    if msg_type == "text":
        return msg.get("text", {}).get("body", "")
    if msg_type == "interactive":
        # Button reply or list reply
        interactive = msg.get("interactive", {})
        ir_type = interactive.get("type", "")
        if ir_type == "button_reply":
            return interactive.get("button_reply", {}).get("title", "")
        elif ir_type == "list_reply":
            return interactive.get("list_reply", {}).get("title", "")
        return "[Interactive response]"
    if msg_type == "location":
        loc = msg.get("location", {})
        lat = loc.get("latitude", "")
        lon = loc.get("longitude", "")
        name = loc.get("name", "")
        addr = loc.get("address", "")
        return f"[Location: {name or addr or f'{lat},{lon}'}]"
    if msg_type in ("image", "video", "audio", "document"):
        # Extract caption if present, otherwise describe the media type
        media_obj = msg.get(msg_type, {})
        caption = media_obj.get("caption", "")
        if caption:
            return caption
        return f"[{msg_type.capitalize()} received]"
    if msg_type == "sticker":
        return "[Sticker received]"
    if msg_type == "contacts":
        return "[Contact card received]"
    return "[Unsupported message type]"
# ==================== WEBHOOK VERIFICATION ====================
@webhook_router.get("/webhook")
async def verify_webhook(request: Request):
    """WhatsApp webhook verification challenge handler"""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    # C-1: Use constant-time comparison to prevent timing side-channel attacks
    if (
        mode == "subscribe"
        and token is not None
        and settings.WHATSAPP_VERIFY_TOKEN
        and hmac.compare_digest(token, settings.WHATSAPP_VERIFY_TOKEN)
    ):
        # C-2: Validate challenge and return as plain text (Meta expects raw value)
        if not challenge or not re.match(r"^[a-zA-Z0-9_.-]{1,128}$", challenge):
            raise HTTPException(status_code=400, detail="Invalid challenge parameter")
        return PlainTextResponse(content=challenge)
    raise HTTPException(status_code=403, detail="Invalid verify token")
