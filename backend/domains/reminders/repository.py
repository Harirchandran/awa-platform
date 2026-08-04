from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from backend.db.client import db_client


class ReminderRepository:
    def __init__(self, db=db_client):
        self.db = db

    async def list_scheduled_messages(self, tenant_id: UUID, **filters) -> Dict[str, Any]:
        return await self.db.list_scheduled_messages(tenant_id, **filters)

    async def get_scheduled_message(self, tenant_id: UUID, message_id: UUID) -> Optional[Dict[str, Any]]:
        return await self.db.get_scheduled_message(tenant_id, message_id)

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
        return await self.db.create_scheduled_message(
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

    async def action_scheduled_message(self, tenant_id: UUID, message_id: UUID, **kwargs) -> Optional[Dict[str, Any]]:
        return await self.db.action_scheduled_message(tenant_id, message_id, **kwargs)

    async def list_scheduled_message_events(self, tenant_id: UUID, message_id: UUID, limit: int = 50) -> List[Dict[str, Any]]:
        return await self.db.list_scheduled_message_events(tenant_id, message_id, limit=limit)

    async def claim_due_scheduled_messages(self, limit: int, lock_ttl_seconds: int, max_attempts: int) -> List[Dict[str, Any]]:
        return await self.db.claim_due_scheduled_messages(
            limit=limit,
            lock_ttl_seconds=lock_ttl_seconds,
            max_attempts=max_attempts,
        )

    async def record_scheduled_message_dispatch_result(self, tenant_id: UUID, message_id: UUID, **kwargs) -> Dict[str, Any]:
        return await self.db.record_scheduled_message_dispatch_result(
            tenant_id=tenant_id,
            message_id=message_id,
            **kwargs,
        )


reminder_repository = ReminderRepository()
