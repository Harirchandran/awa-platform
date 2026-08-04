from typing import Awaitable, Callable, Dict

from backend.domains.events.models import EventHandlerResult

EventHandler = Callable[[dict], Awaitable[EventHandlerResult]]


async def handle_lead_created(event: dict) -> EventHandlerResult:
    return EventHandlerResult.processed({"handler": "lead_created_noop"})


async def handle_review_negative(event: dict) -> EventHandlerResult:
    from backend.domains.support.service import support_service
    from backend.db.client import db_client
    import json
    from uuid import UUID

    tenant_id = event.get("tenant_id")
    if not tenant_id:
        return EventHandlerResult.skipped("missing_tenant_id")

    payload = event.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}

    rating = payload.get("rating")
    conversation_id = event.get("conversation_id")

    if not conversation_id:
        t_uuid = UUID(str(tenant_id)) if not isinstance(tenant_id, UUID) else tenant_id
        r_uuid = None
        review_request_id = payload.get("review_request_id")
        if review_request_id:
            try:
                r_uuid = UUID(str(review_request_id)) if not isinstance(review_request_id, UUID) else review_request_id
            except Exception:
                pass

        await db_client.write_audit_log(
            tenant_id=t_uuid,
            actor_id=None,
            action="orphan_negative_review_escalation_failed",
            resource_type="review_request",
            resource_id=r_uuid,
            details={
                "rating": rating,
                "error": "conversation_id is missing, cannot escalate conversation",
                "severity": "WARNING",
            }
        )
        return EventHandlerResult.processed({"note": "Orphan review logged to audit"})

    await support_service.escalate_conversation(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        reason=f"Negative review feedback (rating: {rating})",
        source="review_escalation",
    )
    return EventHandlerResult.processed({"escalated": True})


async def handle_booking_created(event: dict) -> EventHandlerResult:
    from backend.db.client import db_client
    from backend.domains.reminders.service import reminder_service
    from backend.domains.customers.service import customer_service
    import json
    from datetime import datetime, date, time, timezone, timedelta
    from uuid import UUID

    tenant_id = event.get("tenant_id")
    if not tenant_id:
        return EventHandlerResult.skipped("missing_tenant_id")

    payload = event.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}

    booking_id = payload.get("booking_id")
    if not booking_id:
        return EventHandlerResult.skipped("missing_booking_id")

    # Fetch active reminder rules for this tenant
    try:
        rules = await db_client.list_reminder_rules(tenant_id, active_only=True)
    except Exception as e:
        return EventHandlerResult.failed(f"failed_to_list_rules: {str(e)}")

    booking_rules = [r for r in rules if r["trigger_type"] in ("before_booking", "after_booking")]
    if not booking_rules:
        return EventHandlerResult.skipped("no_active_booking_rules")

    # Retrieve customer context
    customer_name = "Valued Customer"
    customer_id = event.get("customer_id")
    if customer_id:
        try:
            customer = await customer_service.get_customer(tenant_id, customer_id)
            if customer and customer.get("name"):
                customer_name = customer["name"]
        except Exception:
            pass

    # Extract date & time fields
    booking_date_str = payload.get("booking_date")
    booking_time_str = payload.get("booking_time") or "09:00:00"

    # Safely construct a timezone-aware booking datetime
    booking_dt = None
    try:
        if isinstance(booking_date_str, datetime):
            booking_dt = booking_date_str
        elif isinstance(booking_date_str, date):
            try:
                t = booking_time_str if isinstance(booking_time_str, time) else time.fromisoformat(str(booking_time_str))
            except Exception:
                t = time(9, 0)
            booking_dt = datetime.combine(booking_date_str, t)
        else:
            d_str = str(booking_date_str)
            t_str = str(booking_time_str)
            if "T" in d_str:
                d_str = d_str.split("T")[0]
            if "T" in t_str:
                t_str = t_str.split("T")[-1]
            if "+" in t_str:
                t_str = t_str.split("+")[0]
            if "-" in t_str and len(t_str.split("-")) > 1:
                t_str = t_str.split("-")[0]
            try:
                t_parsed = time.fromisoformat(t_str)
            except Exception:
                t_parsed = time(9, 0)
            booking_dt = datetime.combine(date.fromisoformat(d_str), t_parsed)
    except Exception:
        # Fallback if booking_date_str itself fails to parse: combine today with time(9, 0)
        try:
            if isinstance(booking_date_str, (date, datetime)):
                d_fallback = booking_date_str if isinstance(booking_date_str, date) else booking_date_str.date()
            else:
                d_fallback = date.fromisoformat(str(booking_date_str).split("T")[0])
        except Exception:
            d_fallback = date.today()
        booking_dt = datetime.combine(d_fallback, time(9, 0))

    if booking_dt.tzinfo is None:
        booking_dt = booking_dt.replace(tzinfo=timezone.utc)
    else:
        booking_dt = booking_dt.astimezone(timezone.utc)

    scheduled_count = 0
    exceptions = []
    for rule in booking_rules:
        offset = rule.get("offset_minutes") or 0
        scheduled_for = booking_dt + timedelta(minutes=offset)

        # Build rendering context
        context = {
            "customer_name": customer_name,
            "service_name": payload.get("service_name") or "service",
            "booking_date": str(booking_date_str),
            "booking_time": str(booking_time_str),
        }

        # Substitute template placeholders
        template = rule.get("template_text") or ""
        message_text = template
        for k, v in context.items():
            message_text = message_text.replace("{{" + k + "}}", str(v))

        try:
            booking_uuid = UUID(str(booking_id)) if not isinstance(booking_id, UUID) else booking_id
            customer_uuid = UUID(str(customer_id)) if customer_id and not isinstance(customer_id, UUID) else customer_id
            conv_uuid = UUID(str(event.get("conversation_id"))) if event.get("conversation_id") and not isinstance(event.get("conversation_id"), UUID) else event.get("conversation_id")

            await reminder_service.create_scheduled_message(
                tenant_id=tenant_id,
                scheduled_for=scheduled_for,
                message_text=message_text,
                trigger_type=rule["trigger_type"],
                channel=rule["channel"],
                rule_id=rule["id"],
                customer_id=customer_uuid,
                conversation_id=conv_uuid,
                booking_id=booking_uuid,
                metadata={
                    "source": "event_handler",
                    "event_id": str(event.get("id")),
                },
                idempotency_key=f"event:{event.get('id')}:rule:{rule['id']}",
            )
            scheduled_count += 1
        except Exception as e:
            logger.error(f"Failed to schedule booking reminder for rule {rule.get('id')}: {e}")
            exceptions.append(e)

    if exceptions:
        return EventHandlerResult.failed(f"Failed to schedule some booking reminders: {exceptions[0]}")
    return EventHandlerResult.processed({"scheduled_count": scheduled_count})


async def handle_order_created(event: dict) -> EventHandlerResult:
    from backend.db.client import db_client
    from backend.domains.reminders.service import reminder_service
    from backend.domains.customers.service import customer_service
    import json
    from datetime import datetime, timezone, timedelta
    from uuid import UUID

    tenant_id = event.get("tenant_id")
    if not tenant_id:
        return EventHandlerResult.skipped("missing_tenant_id")

    payload = event.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}

    order_id = payload.get("order_id")
    if not order_id:
        return EventHandlerResult.skipped("missing_order_id")

    # Fetch active reminder rules
    try:
        rules = await db_client.list_reminder_rules(tenant_id, active_only=True)
    except Exception as e:
        return EventHandlerResult.failed(f"failed_to_list_rules: {str(e)}")

    order_rules = [r for r in rules if r["trigger_type"] == "after_order_completed"]
    if not order_rules:
        return EventHandlerResult.skipped("no_active_order_rules")

    # Retrieve customer context
    customer_name = "Valued Customer"
    customer_id = event.get("customer_id")
    if customer_id:
        try:
            customer = await customer_service.get_customer(tenant_id, customer_id)
            if customer and customer.get("name"):
                customer_name = customer["name"]
        except Exception:
            pass

    ref_time = datetime.now(timezone.utc)
    scheduled_count = 0
    exceptions = []
    for rule in order_rules:
        offset = rule.get("offset_minutes") or 0
        scheduled_for = ref_time + timedelta(minutes=offset)

        # Build rendering context
        context = {
            "customer_name": customer_name,
            "order_id": str(order_id),
            "total_amount": str(payload.get("total_amount") or ""),
            "item_count": str(payload.get("item_count") or ""),
        }

        # Substitute template placeholders
        template = rule.get("template_text") or ""
        message_text = template
        for k, v in context.items():
            message_text = message_text.replace("{{" + k + "}}", str(v))

        try:
            order_uuid = UUID(str(order_id)) if not isinstance(order_id, UUID) else order_id
            customer_uuid = UUID(str(customer_id)) if customer_id and not isinstance(customer_id, UUID) else customer_id
            conv_uuid = UUID(str(event.get("conversation_id"))) if event.get("conversation_id") and not isinstance(event.get("conversation_id"), UUID) else event.get("conversation_id")

            await reminder_service.create_scheduled_message(
                tenant_id=tenant_id,
                scheduled_for=scheduled_for,
                message_text=message_text,
                trigger_type=rule["trigger_type"],
                channel=rule["channel"],
                rule_id=rule["id"],
                customer_id=customer_uuid,
                conversation_id=conv_uuid,
                order_id=order_uuid,
                metadata={
                    "source": "event_handler",
                    "event_id": str(event.get("id")),
                },
                idempotency_key=f"event:{event.get('id')}:rule:{rule['id']}",
            )
            scheduled_count += 1
        except Exception as e:
            logger.error(f"Failed to schedule order reminder for rule {rule.get('id')}: {e}")
            exceptions.append(e)

    if exceptions:
        return EventHandlerResult.failed(f"Failed to schedule some order reminders: {exceptions[0]}")
    return EventHandlerResult.processed({"scheduled_count": scheduled_count})


async def handle_handoff_created(event: dict) -> EventHandlerResult:
    return EventHandlerResult.processed({"handler": "handoff_created_noop"})


async def handle_payment_completed(event: dict) -> EventHandlerResult:
    from backend.db.client import db_client
    from backend.domains.reminders.service import reminder_service
    from backend.domains.customers.service import customer_service
    import json
    from datetime import datetime, timezone, timedelta
    from uuid import UUID

    tenant_id = event.get("tenant_id")
    if not tenant_id:
        return EventHandlerResult.skipped("missing_tenant_id")

    payload = event.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}

    payment_request_id = payload.get("payment_request_id")
    if not payment_request_id:
        return EventHandlerResult.skipped("missing_payment_request_id")

    # Fetch active reminder rules
    try:
        rules = await db_client.list_reminder_rules(tenant_id, active_only=True)
    except Exception as e:
        return EventHandlerResult.failed(f"failed_to_list_rules: {str(e)}")

    payment_rules = [r for r in rules if r["trigger_type"] == "after_payment_completed"]
    if not payment_rules:
        return EventHandlerResult.skipped("no_active_payment_rules")

    # Retrieve customer context
    customer_name = "Valued Customer"
    customer_id = event.get("customer_id")
    if customer_id:
        try:
            customer = await customer_service.get_customer(tenant_id, customer_id)
            if customer and customer.get("name"):
                customer_name = customer["name"]
        except Exception:
            pass

    # Retrieve amount from payment request
    total_amount = ""
    try:
        async with db_client.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT amount FROM payment_requests WHERE tenant_id = $1 AND id = $2",
                UUID(str(tenant_id)), UUID(str(payment_request_id))
            )
            if row and row["amount"] is not None:
                total_amount = str(row["amount"])
    except Exception:
        pass

    ref_time = datetime.now(timezone.utc)
    scheduled_count = 0
    exceptions = []
    for rule in payment_rules:
        offset = rule.get("offset_minutes") or 0
        scheduled_for = ref_time + timedelta(minutes=offset)

        # Build rendering context
        context = {
            "customer_name": customer_name,
            "total_amount": total_amount,
        }

        # Substitute template placeholders
        template = rule.get("template_text") or ""
        message_text = template
        for k, v in context.items():
            message_text = message_text.replace("{{" + k + "}}", str(v))

        try:
            payment_uuid = UUID(str(payment_request_id)) if not isinstance(payment_request_id, UUID) else payment_request_id
            customer_uuid = UUID(str(customer_id)) if customer_id and not isinstance(customer_id, UUID) else customer_id
            conv_uuid = UUID(str(event.get("conversation_id"))) if event.get("conversation_id") and not isinstance(event.get("conversation_id"), UUID) else event.get("conversation_id")

            booking_id = payload.get("booking_id")
            booking_uuid = UUID(str(booking_id)) if booking_id else None

            order_id = payload.get("order_id")
            order_uuid = UUID(str(order_id)) if order_id else None

            await reminder_service.create_scheduled_message(
                tenant_id=tenant_id,
                scheduled_for=scheduled_for,
                message_text=message_text,
                trigger_type=rule["trigger_type"],
                channel=rule["channel"],
                rule_id=rule["id"],
                customer_id=customer_uuid,
                conversation_id=conv_uuid,
                payment_request_id=payment_uuid,
                booking_id=booking_uuid,
                order_id=order_uuid,
                metadata={
                    "source": "event_handler",
                    "event_id": str(event.get("id")),
                },
                idempotency_key=f"event:{event.get('id')}:rule:{rule['id']}",
            )
            scheduled_count += 1
        except Exception as e:
            logger.error(f"Failed to schedule payment reminder for rule {rule.get('id')}: {e}")
            exceptions.append(e)

    if exceptions:
        return EventHandlerResult.failed(f"Failed to schedule some payment reminders: {exceptions[0]}")
    return EventHandlerResult.processed({"scheduled_count": scheduled_count})


async def handle_noop_processed(event: dict) -> EventHandlerResult:
    return EventHandlerResult.processed({"handler": "noop"})


async def handle_booking_completed(event: dict) -> EventHandlerResult:
    from datetime import datetime, timedelta, timezone
    from uuid import UUID
    from backend.domains.reviews.service import review_service

    tenant_id = event.get("tenant_id")
    booking_id = event.get("aggregate_id")
    if not tenant_id or not booking_id:
        return EventHandlerResult.skipped("missing_booking_completion_authority")
    settings = await review_service.get_review_settings(tenant_id)
    if settings.get("ask_after_booking_completed") is not True:
        return EventHandlerResult.processed({"review_scheduled": False})
    try:
        delay_minutes = max(0, int(settings.get("ask_delay_minutes") or 0))
    except (TypeError, ValueError):
        delay_minutes = 0
    completed_at = event.get("updated_at") or event.get("created_at") or datetime.now(timezone.utc)
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    available_at = completed_at + timedelta(minutes=delay_minutes)
    created = await review_service.create_review_request(
        tenant_id=tenant_id,
        status="draft",
        source="booking_completed_automation",
        customer_id=event.get("customer_id"),
        conversation_id=event.get("conversation_id"),
        booking_id=UUID(str(booking_id)),
        metadata={"source": "booking_completed_automation"},
        idempotency_key=f"booking-completed:{booking_id}",
        event_available_at=available_at,
        event_causation_id=str(event.get("id")),
    )
    if not created:
        return EventHandlerResult.failed("review_request_create_failed")
    return EventHandlerResult.processed({"review_scheduled": True, "delay_minutes": delay_minutes})


async def handle_review_requested(event: dict) -> EventHandlerResult:
    import asyncio
    from datetime import datetime, timezone
    from uuid import UUID, uuid5, NAMESPACE_URL

    from backend.db.client import db_client
    from backend.domains.reminders.service import reminder_service
    from backend.domains.reviews.service import review_service
    from backend.realtime.public_chat_events import publish_public_chat_message_event

    if event.get("event_type") != "review.requested" or event.get("aggregate_type") != "review_request":
        return EventHandlerResult.failed("invalid_review_event_authority")
    try:
        event_id = UUID(str(event.get("id")))
        tenant_id = UUID(str(event.get("tenant_id")))
        review_request_id = UUID(str(event.get("aggregate_id")))
    except (TypeError, ValueError, AttributeError):
        return EventHandlerResult.failed("invalid_review_event_identity")

    try:
        review = await review_service.get_review_request(tenant_id, review_request_id)
        if not review:
            return EventHandlerResult.failed("review_request_not_found")

        conversation_id = review.get("conversation_id")
        customer_id = review.get("customer_id")
        if not conversation_id or not customer_id:
            return EventHandlerResult.failed("review_delivery_authority_missing")
        try:
            conversation_id = UUID(str(conversation_id))
            customer_id = UUID(str(customer_id))
        except (TypeError, ValueError, AttributeError):
            return EventHandlerResult.failed("review_delivery_authority_invalid")

        conversation = await db_client.get_conversation(tenant_id, conversation_id)
        if not conversation or UUID(str(conversation.get("customer_id"))) != customer_id:
            return EventHandlerResult.failed("review_conversation_authority_mismatch")

        invitation = (
            "How was your experience? Reply with a rating from 1 to 5, "
            "and you can also share feedback."
        )
        channel = str(conversation.get("channel_type") or "").strip().lower()
        metadata = {
            "source": "automatic_review_request",
            "source_event_id": str(event_id),
            "review_request_id": str(review_request_id),
        }

        if channel in {"awa_chat", "public_chat", "browser_chat"}:
            outbound_identity = f"review_request_{event_id}"
            message_id = uuid5(NAMESPACE_URL, f"awa:{tenant_id}:{outbound_identity}")
            saved = await db_client.save_message(
                message_id=message_id,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                whatsapp_message_id=outbound_identity,
                provider_message_id=outbound_identity,
                channel_type="awa_chat",
                direction="outbound",
                message_type="text",
                message_text=invitation,
                raw_payload={**metadata, "delivery_channel": "awa_chat"},
            )
            created = bool(saved.get("created"))
            if not created and saved.get("reason") != "duplicate":
                return EventHandlerResult.failed("awa_chat_persistence_failed")
            if created:
                try:
                    await publish_public_chat_message_event(
                        conversation_id=conversation_id,
                        message_id=message_id,
                        content=invitation,
                        raw_payload={**metadata, "delivery_channel": "awa_chat"},
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            return EventHandlerResult.processed({
                "delivery_status": "committed",
                "delivery_channel": "awa_chat",
                "deduplicated": not created,
            })

        if channel == "whatsapp":
            idempotency_key = f"review-request:{event_id}:whatsapp"
            queued = await reminder_service.create_scheduled_message(
                tenant_id=tenant_id,
                scheduled_for=datetime.now(timezone.utc),
                message_text=invitation,
                trigger_type="review_requested",
                channel="whatsapp",
                customer_id=customer_id,
                conversation_id=conversation_id,
                booking_id=review.get("booking_id"),
                order_id=review.get("order_id"),
                review_request_id=review_request_id,
                metadata={**metadata, "delivery_channel": "whatsapp"},
                idempotency_key=idempotency_key,
            )
            if not queued:
                return EventHandlerResult.failed("whatsapp_queue_rejected")
            queued_metadata = queued.get("metadata") or {}
            return EventHandlerResult.processed({
                "delivery_status": "queued",
                "delivery_channel": "whatsapp",
                "deduplicated": str(queued_metadata.get("source_event_id") or "") == str(event_id)
                and queued_metadata.get("queue_reused") is True,
            })

        return EventHandlerResult.failed("unsupported_review_delivery_channel")
    except asyncio.CancelledError:
        raise
    except Exception:
        return EventHandlerResult.failed("review_delivery_failed")


async def handle_conversation_turn_received(event: dict) -> EventHandlerResult:
    from uuid import UUID
    import asyncio
    from backend.db.client import db_client, PublicChatTurnReplayError
    from backend.pipeline.public_chat_intake import build_canonical_public_chat_turn
    from backend.pipeline.public_chat_queue import enqueue_public_chat_turn

    tenant_id = event.get("tenant_id")
    event_id = event.get("id")
    if not tenant_id or not event_id:
        return EventHandlerResult.skipped("missing_tenant_id_or_event_id")

    try:
        t_uuid = UUID(str(tenant_id)) if not isinstance(tenant_id, UUID) else tenant_id
        e_uuid = UUID(str(event_id)) if not isinstance(event_id, UUID) else event_id
    except ValueError as exc:
        raise PublicChatTurnReplayError("event_invalid") from exc

    # 13.1 Validate and load from database
    record = await db_client.get_public_chat_turn_for_replay(
        tenant_id=t_uuid,
        event_id=e_uuid,
    )

    proc_status = record.intake.processing_status

    if proc_status in ("queued", "processing", "completed"):
        # 13.2 Already-published local state
        sync_ok = await db_client.mark_public_chat_turn_queued(
            tenant_id=record.tenant_id,
            conversation_id=record.conversation_id,
            message_id=record.intake.message_id,
            event_id=record.intake.event_id,
        )
        if not sync_ok:
            raise PublicChatTurnReplayError("queue_commit_invalid")
        return EventHandlerResult.processed({"queue_state": "already_published"})

    elif proc_status in ("received", "failed"):
        # 13.3 Replayable local state
        canonical = build_canonical_public_chat_turn(
            intake=record.intake,
            tenant_id=record.tenant_id,
            conversation_id=record.conversation_id,
            customer_id=record.customer_id,
            public_chat_session_id=record.public_chat_session_id,
            channel_account_id=record.channel_account_id,
            message_text=record.message_text,
            worker_version="1.0.0",
            max_retries=3,
        )

        await enqueue_public_chat_turn(canonical)

        sync_ok = await db_client.mark_public_chat_turn_queued(
            tenant_id=record.tenant_id,
            conversation_id=record.conversation_id,
            message_id=record.intake.message_id,
            event_id=record.intake.event_id,
        )
        if not sync_ok:
            raise PublicChatTurnReplayError("queue_commit_invalid")
        return EventHandlerResult.processed({"queue_state": "replayed"})

    else:
        # 13.4 Invalid processing state
        raise PublicChatTurnReplayError("message_invalid")


EVENT_HANDLERS: Dict[str, EventHandler] = {
    "lead.created": handle_lead_created,
    "booking.created": handle_booking_created,
    "booking.completed": handle_booking_completed,
    "order.created": handle_order_created,
    "handoff.created": handle_handoff_created,
    "payment.completed": handle_payment_completed,
    "review.negative": handle_review_negative,
    "payment.requested": handle_noop_processed,
    "reminder.scheduled": handle_noop_processed,
    "reminder.sent": handle_noop_processed,
    "review.requested": handle_review_requested,
    "review.submitted": handle_noop_processed,
    "conversation.turn.received": handle_conversation_turn_received,
}
