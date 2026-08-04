from typing import Any, Dict, Optional
from uuid import UUID

from backend.db.client import DatabaseClient, db_client
from backend.domains.events.models import BusinessEventCreate
from backend.domains.events.repository import EventRepository, event_repository
from backend.domains.events.service import EventService, event_service
from backend.domains.leads.repository import LeadRepository, lead_repository
from backend.ops.logger import logger


class LeadService:
    def __init__(
        self,
        repository: LeadRepository = lead_repository,
        events: Optional[EventService] = None,
        *,
        event_repository: EventRepository = event_repository,
        db: DatabaseClient = db_client,
    ) -> None:
        self.repository = repository
        self.events = events
        self._event_repository = event_repository
        self._db = db

    async def list_leads(
        self,
        tenant_id: UUID,
        cursor: Optional[str] = None,
        limit: int = 20,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = await self.repository.list_leads(tenant_id, cursor=cursor, limit=limit, status=status, search=search)
        logger.debug(
            "domain service call",
            extra={"domain": "leads", "action": "list", "tenant_id": str(tenant_id), "outcome": "success"},
        )
        return result

    async def get_lead(self, tenant_id: UUID, lead_id: UUID) -> Optional[Dict[str, Any]]:
        lead = await self.repository.get_lead(tenant_id, lead_id)
        logger.debug(
            "domain service call",
            extra={
                "domain": "leads",
                "action": "get",
                "tenant_id": str(tenant_id),
                "record_id": str(lead_id),
                "outcome": "found" if lead else "not_found",
            },
        )
        return lead

    async def update_lead_status(self, tenant_id: UUID, lead_id: UUID, status: str) -> bool:
        updated = await self.repository.update_lead_status(tenant_id, lead_id, status)
        logger.debug(
            "domain service call",
            extra={
                "domain": "leads",
                "action": "update_status",
                "tenant_id": str(tenant_id),
                "record_id": str(lead_id),
                "outcome": "success" if updated else "not_found",
            },
        )
        return updated

    async def capture_lead(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        conversation_id: Optional[UUID],
        lead_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        async with self._db.acquire() as conn:
            async with conn.transaction():
                lead = await self.repository.capture_lead(
                    tenant_id, customer_id, conversation_id, lead_data, conn=conn,
                )
                if lead.get("created"):
                    await self._event_repository.create_lead_created_event(
                        BusinessEventCreate(
                            tenant_id=tenant_id,
                            event_type="lead.created",
                            aggregate_type="lead",
                            aggregate_id=lead.get("id"),
                            source_domain="leads",
                            conversation_id=conversation_id,
                            customer_id=customer_id,
                            payload={
                                "lead_id": str(lead.get("id")),
                                "source_channel": lead.get("source_channel"),
                                "interest": lead.get("interest"),
                            },
                            correlation_id=str(conversation_id) if conversation_id else None,
                        ),
                        conn=conn,
                    )
        logger.debug(
            "domain service call",
            extra={
                "domain": "leads",
                "action": "capture",
                "tenant_id": str(tenant_id),
                "record_id": str(lead.get("id")),
                "outcome": "created" if lead.get("created") else "updated",
            },
        )
        return lead


lead_service = LeadService(
    repository=lead_repository,
    events=event_service,
    event_repository=event_repository,
    db=db_client,
)
