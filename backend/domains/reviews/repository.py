from typing import Any, Dict, List, Optional
from uuid import UUID

from backend.db.client import db_client


class ReviewRepository:
    def __init__(self, db=db_client):
        self.db = db

    async def get_review_settings(self, tenant_id: UUID) -> Dict[str, Any]:
        return await self.db.get_review_settings(tenant_id)

    async def update_review_settings(self, tenant_id: UUID, updates: Dict[str, Any]) -> Dict[str, Any]:
        return await self.db.update_review_settings(tenant_id, updates)

    async def list_review_requests(self, tenant_id: UUID, **filters) -> Dict[str, Any]:
        return await self.db.list_review_requests(tenant_id, **filters)

    async def get_review_request(self, tenant_id: UUID, review_id: UUID) -> Optional[Dict[str, Any]]:
        return await self.db.get_review_request(tenant_id, review_id)

    async def create_review_request(
        self,
        tenant_id: UUID,
        status: str = "draft",
        source: str = "manual",
        customer_id: Optional[UUID] = None,
        conversation_id: Optional[UUID] = None,
        booking_id: Optional[UUID] = None,
        order_id: Optional[UUID] = None,
        public_review_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        created_by: Optional[UUID] = None,
    ) -> Optional[Dict[str, Any]]:
        return await self.db.create_review_request(
            tenant_id=tenant_id,
            status=status,
            source=source,
            customer_id=customer_id,
            conversation_id=conversation_id,
            booking_id=booking_id,
            order_id=order_id,
            public_review_url=public_review_url,
            metadata=metadata,
            idempotency_key=idempotency_key,
            created_by=created_by,
        )

    async def update_review_request(self, tenant_id: UUID, review_id: UUID, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return await self.db.update_review_request(tenant_id, review_id, updates)

    async def delete_review_request(self, tenant_id: UUID, review_id: UUID) -> bool:
        return await self.db.delete_review_request(tenant_id, review_id)

    async def get_review_metrics(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        return await self.db.get_review_metrics(tenant_id)

    async def action_review_request(self, tenant_id: UUID, review_id: UUID, **kwargs) -> Optional[Dict[str, Any]]:
        return await self.db.action_review_request(tenant_id, review_id, **kwargs)

    async def list_review_events(self, tenant_id: UUID, review_id: UUID, limit: int = 50) -> List[Dict[str, Any]]:
        return await self.db.list_review_events(tenant_id, review_id, limit=limit)


review_repository = ReviewRepository()
