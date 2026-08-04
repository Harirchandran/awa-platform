from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from backend.domains.events.models import BusinessEventCreate
from backend.domains.events.service import EventService, event_service
from backend.domains.reminders.repository import ReminderRepository, reminder_repository
from backend.ops.logger import logger


class ReminderService:
    VALID_CHANNELS = {"whatsapp", "email", "manual"}

    def __init__(self, repository: ReminderRepository = reminder_repository, events: Optional[EventService] = None):
        self.repository = repository
        self.events = events

    def _log(self, action: str, tenant_id: Optional[UUID], outcome: str, record_id: Optional[UUID] = None, reason_code: Optional[str] = None) -> None:
        extra = {"domain": "reminders", "action": action, "outcome": outcome}
        if tenant_id:
            extra["tenant_id"] = str(tenant_id)
        if record_id:
            extra["record_id"] = str(record_id)
        if reason_code:
            extra["reason_code"] = reason_code
        logger.debug("domain service call", extra=extra)

    async def list_scheduled_messages(self, tenant_id: UUID, **filters) -> Dict[str, Any]:
        result = await self.repository.list_scheduled_messages(tenant_id, **filters)
        self._log("list", tenant_id, "success")
        return result

    async def get_scheduled_message(self, tenant_id: UUID, message_id: UUID) -> Optional[Dict[str, Any]]:
        message = await self.repository.get_scheduled_message(tenant_id, message_id)
        self._log("get", tenant_id, "found" if message else "not_found", message_id)
        return message

    async def create_scheduled_message(
        self,
        tenant_id: UUID,
        scheduled_for: datetime,
        message_text: str,
        trigger_type: str = "manual",
        channel: str = "whatsapp",
        rule_id: Optional[UUID] = None,
        customer_id: Optional[UUID] = None,
        conversation_id: Optional[UUID] = None,
        booking_id: Optional[UUID] = None,
        order_id: Optional[UUID] = None,
        payment_request_id: Optional[UUID] = None,
        review_request_id: Optional[UUID] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        created_by: Optional[UUID] = None,
    ) -> Optional[Dict[str, Any]]:
        if channel not in self.VALID_CHANNELS:
            self._log("create", tenant_id, "rejected", reason_code="invalid_channel")
            return None
        message = await self.repository.create_scheduled_message(
            tenant_id=tenant_id,
            scheduled_for=scheduled_for,
            message_text=message_text,
            trigger_type=trigger_type,
            channel=channel,
            rule_id=rule_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
            booking_id=booking_id,
            order_id=order_id,
            payment_request_id=payment_request_id,
            review_request_id=review_request_id,
            metadata=metadata,
            idempotency_key=idempotency_key,
            created_by=created_by,
        )
        if message and self.events:
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="reminder.scheduled",
                aggregate_type="scheduled_message",
                aggregate_id=message.get("id"),
                source_domain="reminders",
                conversation_id=message.get("conversation_id") or conversation_id,
                customer_id=message.get("customer_id") or customer_id,
                payload={
                    "scheduled_message_id": str(message.get("id")),
                    "trigger_type": trigger_type,
                    "channel": channel,
                    "scheduled_for": str(message.get("scheduled_for") or scheduled_for),
                    "idempotency_key": idempotency_key,
                },
                correlation_id=str((message.get("conversation_id") or conversation_id) or message.get("id")),
            ))
        self._log("create", tenant_id, "success" if message else "not_found", message.get("id") if message else None)
        return message

    async def action_scheduled_message(self, tenant_id: UUID, message_id: UUID, **kwargs) -> Optional[Dict[str, Any]]:
        message = await self.repository.action_scheduled_message(tenant_id, message_id, **kwargs)
        self._log("action", tenant_id, "success" if message else "not_found", message_id, kwargs.get("action"))
        return message

    async def list_scheduled_message_events(self, tenant_id: UUID, message_id: UUID, limit: int = 50) -> List[Dict[str, Any]]:
        events = await self.repository.list_scheduled_message_events(tenant_id, message_id, limit=limit)
        self._log("list_events", tenant_id, "success", message_id)
        return events

    async def claim_due_scheduled_messages(self, limit: int, lock_ttl_seconds: int, max_attempts: int) -> List[Dict[str, Any]]:
        messages = await self.repository.claim_due_scheduled_messages(limit, lock_ttl_seconds, max_attempts)
        logger.debug("domain service call", extra={"domain": "reminders", "action": "claim_due", "outcome": "success"})
        return messages

    async def record_scheduled_message_dispatch_result(self, tenant_id: UUID, message_id: UUID, **kwargs) -> Dict[str, Any]:
        result = await self.repository.record_scheduled_message_dispatch_result(tenant_id, message_id, **kwargs)
        if result and self.events and kwargs.get("status") == "sent":
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="reminder.sent",
                aggregate_type="scheduled_message",
                aggregate_id=message_id,
                source_domain="reminders",
                payload={
                    "scheduled_message_id": str(message_id),
                    "event_type": kwargs.get("event_type"),
                    "provider_message_id": kwargs.get("provider_message_id"),
                },
                correlation_id=str(message_id),
            ))
        self._log("record_dispatch", tenant_id, "success", message_id, kwargs.get("status"))
        return result


reminder_service = ReminderService(events=event_service)
