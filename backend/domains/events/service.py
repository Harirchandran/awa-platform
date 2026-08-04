from typing import Any, Dict, List, Optional
from uuid import UUID

from backend.domains.events.models import BUSINESS_EVENT_TYPES, BusinessEventCreate
from backend.domains.events.repository import EventRepository, event_repository
from backend.ops.logger import logger


class EventService:
    def __init__(self, repository: EventRepository = event_repository):
        self.repository = repository

    async def emit(self, event: BusinessEventCreate) -> Optional[Dict[str, Any]]:
        if event.event_type not in BUSINESS_EVENT_TYPES:
            logger.warning(
                "Unsupported business event type",
                extra={
                    "domain": "events",
                    "action": "emit",
                    "tenant_id": str(event.tenant_id),
                    "event_type": event.event_type,
                    "outcome": "rejected",
                    "reason_code": "unsupported_event_type",
                },
            )
            return None
        try:
            created = await self.repository.create_event(event)
        except Exception:
            logger.error(
                "Business event emit failed",
                extra={
                    "domain": "events",
                    "action": "emit",
                    "tenant_id": str(event.tenant_id),
                    "event_type": event.event_type,
                    "outcome": "failed",
                    "reason_code": "event_emit_exception",
                },
            )
            return None
        logger.debug(
            "business event emitted",
            extra={
                "domain": "events",
                "action": "emit",
                "tenant_id": str(created["tenant_id"]),
                "event_id": str(created["id"]),
                "event_type": created["event_type"],
                "source_domain": created["source_domain"],
                "aggregate_type": created["aggregate_type"],
                "aggregate_id": str(created["aggregate_id"]) if created.get("aggregate_id") else None,
                "status": created["status"],
                "attempts": created["attempts"],
                "outcome": "success",
            },
        )
        return created

    async def list_events(
        self,
        tenant_id: UUID,
        status: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return await self.repository.list_events(tenant_id, status=status, event_type=event_type, limit=limit)

    async def claim_pending_events(self, limit: int = 25) -> List[Dict[str, Any]]:
        return await self.repository.claim_pending_events(limit=limit)

    async def claim_exhausted_conversation_turn_events(
        self,
        limit: int = 25,
    ) -> list[dict]:
        return await self.repository.claim_exhausted_conversation_turn_events(
            limit=limit
        )

    async def defer_malformed_exhausted_event(
        self,
        tenant_id: UUID,
        event_id: UUID,
        *,
        retry_delay_seconds: int = 3600,
    ) -> Optional[Dict[str, Any]]:
        return await self.repository.defer_malformed_exhausted_event(
            tenant_id,
            event_id,
            retry_delay_seconds=retry_delay_seconds,
        )

    async def event_health_summary(self) -> Dict[str, Any]:
        return await self.repository.event_health_summary()

    async def list_failed_events(self, tenant_id: Optional[UUID] = None, limit: int = 50) -> List[Dict[str, Any]]:
        return await self.repository.list_failed_events(tenant_id=tenant_id, limit=limit)

    async def list_skipped_events(self, tenant_id: Optional[UUID] = None, limit: int = 50) -> List[Dict[str, Any]]:
        return await self.repository.list_skipped_events(tenant_id=tenant_id, limit=limit)

    async def get_event(self, event_id: UUID, tenant_id: Optional[UUID] = None) -> Optional[Dict[str, Any]]:
        return await self.repository.get_event(event_id, tenant_id=tenant_id)

    async def retry_failed_event(self, event_id: UUID, tenant_id: Optional[UUID] = None) -> Optional[Dict[str, Any]]:
        return await self.repository.retry_failed_event(event_id, tenant_id=tenant_id)

    async def mark_processed(self, tenant_id: UUID, event_id: UUID) -> Optional[Dict[str, Any]]:
        return await self.repository.mark_processed(tenant_id, event_id)

    async def mark_skipped(self, tenant_id: UUID, event_id: UUID, reason_code: str) -> Optional[Dict[str, Any]]:
        return await self.repository.mark_skipped(tenant_id, event_id, reason_code)

    async def mark_failed(self, tenant_id: UUID, event_id: UUID, error: str, retry_delay_seconds: int = 60) -> Optional[Dict[str, Any]]:
        return await self.repository.mark_failed(tenant_id, event_id, error, retry_delay_seconds=retry_delay_seconds)


event_service = EventService()
