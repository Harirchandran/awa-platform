import time
from typing import Dict, Optional
from uuid import UUID

from backend.domains.events.handlers import EVENT_HANDLERS, EventHandler
from backend.domains.events.models import EventHandlerResult
from backend.domains.events.service import EventService, event_service
from backend.ops.logger import logger


class EventDispatcher:
    def __init__(
        self,
        service: EventService = event_service,
        handlers: Optional[Dict[str, EventHandler]] = None,
    ):
        self.service = service
        self.handlers = handlers if handlers is not None else EVENT_HANDLERS

    async def dispatch_once(self, limit: int = 25) -> int:
        started = time.perf_counter()

        events = await self.service.claim_pending_events(limit=limit)
        ordinary_examined = len(events)
        remaining_capacity = max(0, limit - ordinary_examined)
        exhausted_events = []

        try:
            if remaining_capacity > 0:
                exhausted_events = await self.service.claim_exhausted_conversation_turn_events(
                    limit=remaining_capacity
                )
            for evt in exhausted_events:
                try:
                    event_id = evt.get("id")
                    tenant_id = evt.get("tenant_id")
                    conversation_id = evt.get("conversation_id")
                    message_id = evt.get("aggregate_id")
                    attempts = evt.get("attempts")
                    max_attempts = evt.get("max_attempts")
                    valid = (
                        evt.get("event_type") == "conversation.turn.received"
                        and evt.get("aggregate_type") == "message"
                        and all(isinstance(value, UUID) for value in (event_id, tenant_id, conversation_id, message_id))
                        and isinstance(attempts, int) and not isinstance(attempts, bool)
                        and isinstance(max_attempts, int) and not isinstance(max_attempts, bool)
                        and attempts >= max_attempts >= 1
                    )
                    if not valid:
                        if isinstance(event_id, UUID) and isinstance(tenant_id, UUID):
                            await self.service.defer_malformed_exhausted_event(
                                tenant_id, event_id, retry_delay_seconds=3600
                            )
                        logger.error("Malformed exhausted event authority", extra={"extra_data": {"reason_code": "malformed_exhausted_event_authority"}})
                        continue

                    retry_count = max(attempts, max_attempts)
                    from backend.db.client import db_client
                    outcome = await db_client.finalize_turn_terminal_failure(
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        inbound_message_id=message_id,
                        event_id=event_id,
                        retry_count=retry_count,
                    )

                    if outcome.outcome_code in ("terminal_created", "terminal_already_exists"):
                        try:
                            from backend.realtime.public_chat_events import publish_public_chat_terminal_failure
                            await publish_public_chat_terminal_failure(
                                conversation_id=conversation_id,
                                message_id=outcome.terminal_message_id,
                                content=outcome.terminal_message_text,
                                created_at=outcome.terminal_message_created_at,
                                source_event_id=str(event_id)
                            )
                        except Exception as e:
                            logger.error(f"SSE publication failed for exhausted event {event_id}", extra={"extra_data": {"error_type": type(e).__name__}})

                    await self.service.mark_processed(tenant_id, event_id)
                except Exception as e:
                    logger.error("Failed to process exhausted event", extra={"extra_data": {"error_type": type(e).__name__}})
                    continue
        except Exception as e:
            logger.error(f"Failed during exhausted event processing loop: {e}")

        counters = {"processed": 0, "skipped": 0, "failed": 0}
        processed = 0
        for event in events:
            result = await self.dispatch_event(event)
            if result.status in counters:
                counters[result.status] += 1
            processed += 1
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "event dispatcher batch summary",
            extra={
                "domain": "events",
                "action": "dispatch_batch",
                "events_claimed_total": len(events),
                "events_processed_total": counters["processed"],
                "events_skipped_total": counters["skipped"],
                "events_failed_total": counters["failed"],
                "event_dispatch_duration_ms": duration_ms,
                "outcome": "success",
            },
        )
        return ordinary_examined + len(exhausted_events)

    async def dispatch_event(self, event: dict) -> EventHandlerResult:
        tenant_id = event["tenant_id"]
        event_id = event["id"]
        event_type = event["event_type"]
        handler = self.handlers.get(event_type)
        started = time.perf_counter()
        safe_meta = {
            "domain": "events",
            "event_id": str(event_id),
            "event_type": event_type,
            "tenant_id": str(tenant_id),
            "source_domain": event.get("source_domain"),
            "aggregate_type": event.get("aggregate_type"),
            "aggregate_id": str(event.get("aggregate_id")) if event.get("aggregate_id") else None,
            "attempts": event.get("attempts"),
        }
        logger.debug("business event claimed", extra={**safe_meta, "action": "claim", "status": event.get("status")})
        try:
            if not handler:
                result = EventHandlerResult.skipped("no_handler_registered")
            else:
                result = await handler(event)
            if result.status == "processed":
                await self.service.mark_processed(tenant_id, event_id)
            elif result.status == "skipped":
                await self.service.mark_skipped(tenant_id, event_id, result.reason_code or "skipped")
            else:
                await self.service.mark_failed(tenant_id, event_id, f"Unsupported handler status: {result.status}")
                result = EventHandlerResult(status="failed", reason_code="unsupported_handler_status")
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.debug(
                "business event dispatched",
                extra={
                    **safe_meta,
                    "action": "dispatch",
                    "status": result.status,
                    "reason_code": result.reason_code,
                    "event_dispatch_duration_ms": duration_ms,
                },
            )
            return result
        except Exception as exc:
            failed = await self.service.mark_failed(tenant_id, event_id, "handler_exception")
            terminal = bool(failed and failed.get("status") == "failed")
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.error(
                "business event dispatch failed",
                exc_info=True,
                extra={
                    **safe_meta,
                    "action": "dispatch",
                    "status": "failed" if terminal else "pending",
                    "reason_code": "handler_exception",
                    "event_dispatch_duration_ms": duration_ms,
                    "retry_scheduled": not terminal,
                    "max_attempts_exhausted": terminal,
                },
            )
            return EventHandlerResult(status="failed", reason_code="handler_exception")


event_dispatcher = EventDispatcher()
