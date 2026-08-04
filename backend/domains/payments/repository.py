from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from backend.db.client import db_client


class PaymentRepository:
    def __init__(self, db=db_client):
        self.db = db

    async def list_payment_requests(self, tenant_id: UUID, **filters) -> Dict[str, Any]:
        return await self.db.list_payment_requests(tenant_id, **filters)

    async def get_payment_request(self, tenant_id: UUID, payment_id: UUID) -> Optional[Dict[str, Any]]:
        return await self.db.get_payment_request(tenant_id, payment_id)

    async def get_payment_request_by_provider_link_id(self, provider_link_id: str) -> Optional[Dict[str, Any]]:
        return await self.db.get_payment_request_by_provider_link_id(provider_link_id)

    async def create_payment_request(
        self,
        tenant_id: UUID,
        amount: Optional[float] = None,
        currency: str = "INR",
        payment_type: str = "custom_payment",
        provider: str = "manual",
        status: str = "draft",
        customer_id: Optional[UUID] = None,
        conversation_id: Optional[UUID] = None,
        booking_id: Optional[UUID] = None,
        order_id: Optional[UUID] = None,
        payment_url: Optional[str] = None,
        provider_payment_link_id: Optional[str] = None,
        provider_order_id: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        created_by: Optional[UUID] = None,
    ) -> Optional[Dict[str, Any]]:
        return await self.db.create_payment_request(
            tenant_id=tenant_id,
            amount=amount,
            currency=currency,
            payment_type=payment_type,
            provider=provider,
            status=status,
            customer_id=customer_id,
            conversation_id=conversation_id,
            booking_id=booking_id,
            order_id=order_id,
            payment_url=payment_url,
            provider_payment_link_id=provider_payment_link_id,
            provider_order_id=provider_order_id,
            expires_at=expires_at,
            metadata=metadata,
            idempotency_key=idempotency_key,
            created_by=created_by,
        )

    async def update_payment_request(
        self,
        tenant_id: UUID,
        payment_id: UUID,
        updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        return await self.db.update_payment_request(tenant_id, payment_id, updates)

    async def update_payment_request_provider_details(
        self,
        tenant_id: UUID,
        payment_id: UUID,
        status: str,
        payment_url: Optional[str] = None,
        provider_payment_link_id: Optional[str] = None,
        provider_order_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        return await self.db.update_payment_request_provider_details(
            tenant_id=tenant_id,
            payment_id=payment_id,
            status=status,
            payment_url=payment_url,
            provider_payment_link_id=provider_payment_link_id,
            provider_order_id=provider_order_id,
            metadata=metadata,
        )

    async def action_payment_request(self, tenant_id: UUID, payment_id: UUID, **kwargs) -> Optional[Dict[str, Any]]:
        return await self.db.action_payment_request(tenant_id, payment_id, **kwargs)

    async def list_payment_events(self, tenant_id: UUID, payment_id: UUID, limit: int = 50) -> List[Dict[str, Any]]:
        return await self.db.list_payment_events(tenant_id, payment_id, limit=limit)

    async def get_payment_metrics(self, tenant_id: UUID) -> Dict[str, Any]:
        return await self.db.get_payment_metrics(tenant_id)


payment_repository = PaymentRepository()
