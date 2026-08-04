from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from backend.domains.events.models import BusinessEventCreate
from backend.domains.events.service import EventService, event_service
from backend.domains.payments.repository import PaymentRepository, payment_repository
from backend.ops.logger import logger


class PaymentService:
    def __init__(self, repository: PaymentRepository = payment_repository, events: Optional[EventService] = None):
        self.repository = repository
        self.events = events

    def _log(self, action: str, tenant_id: Optional[UUID], outcome: str, record_id: Optional[UUID] = None, reason_code: Optional[str] = None) -> None:
        extra = {"domain": "payments", "action": action, "outcome": outcome}
        if tenant_id:
            extra["tenant_id"] = str(tenant_id)
        if record_id:
            extra["record_id"] = str(record_id)
        if reason_code:
            extra["reason_code"] = reason_code
        logger.debug("domain service call", extra=extra)

    async def list_payment_requests(self, tenant_id: UUID, **filters) -> Dict[str, Any]:
        result = await self.repository.list_payment_requests(tenant_id, **filters)
        self._log("list", tenant_id, "success")
        return result

    async def get_payment_request(self, tenant_id: UUID, payment_id: UUID) -> Optional[Dict[str, Any]]:
        payment = await self.repository.get_payment_request(tenant_id, payment_id)
        self._log("get", tenant_id, "found" if payment else "not_found", payment_id)
        return payment

    async def get_payment_request_by_provider_link_id(self, provider_link_id: str) -> Optional[Dict[str, Any]]:
        payment = await self.repository.get_payment_request_by_provider_link_id(provider_link_id)
        self._log("get_by_provider_link", payment.get("tenant_id") if payment else None, "found" if payment else "not_found")
        return payment

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
        payment = await self.repository.create_payment_request(
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
        if payment and self.events:
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="payment.requested",
                aggregate_type="payment_request",
                aggregate_id=payment.get("id"),
                source_domain="payments",
                conversation_id=conversation_id,
                customer_id=customer_id,
                payload={
                    "payment_request_id": str(payment.get("id")),
                    "amount": amount,
                    "currency": currency,
                    "type": payment_type,
                    "provider": provider,
                    "status": payment.get("status"),
                    "idempotency_key": idempotency_key,
                },
                correlation_id=str(conversation_id or payment.get("id")),
            ))
        self._log("create", tenant_id, "success" if payment else "not_found", payment.get("id") if payment else None)
        return payment

    async def update_payment_request(self, tenant_id: UUID, payment_id: UUID, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        payment = await self.repository.update_payment_request(tenant_id, payment_id, updates)
        self._log("update", tenant_id, "success" if payment else "not_found", payment_id)
        return payment

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
        payment = await self.repository.update_payment_request_provider_details(
            tenant_id=tenant_id,
            payment_id=payment_id,
            status=status,
            payment_url=payment_url,
            provider_payment_link_id=provider_payment_link_id,
            provider_order_id=provider_order_id,
            metadata=metadata,
        )
        self._log("update_provider_details", tenant_id, "success" if payment else "not_found", payment_id)
        return payment

    async def action_payment_request(self, tenant_id: UUID, payment_id: UUID, **kwargs) -> Optional[Dict[str, Any]]:
        payment = await self.repository.action_payment_request(tenant_id, payment_id, **kwargs)
        action = kwargs.get("action")
        if payment and self.events and (action in {"mark_paid", "partial_payment"} or payment.get("status") == "paid"):
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="payment.completed",
                aggregate_type="payment_request",
                aggregate_id=payment_id,
                source_domain="payments",
                conversation_id=payment.get("conversation_id"),
                customer_id=payment.get("customer_id"),
                payload={
                    "payment_request_id": str(payment_id),
                    "status": payment.get("status"),
                    "action": action,
                    "order_id": str(payment.get("order_id")) if payment.get("order_id") else None,
                    "booking_id": str(payment.get("booking_id")) if payment.get("booking_id") else None,
                },
                correlation_id=str(payment.get("conversation_id") or payment_id),
            ))
        self._log("action", tenant_id, "success" if payment else "not_found", payment_id, kwargs.get("action"))
        return payment

    async def get_payment_metrics(self, tenant_id: UUID) -> Dict[str, Any]:
        result = await self.repository.get_payment_metrics(tenant_id)
        self._log("metrics", tenant_id, "success")
        return result

    async def list_payment_events(self, tenant_id: UUID, payment_id: UUID, limit: int = 50) -> List[Dict[str, Any]]:
        events = await self.repository.list_payment_events(tenant_id, payment_id, limit=limit)
        self._log("list_events", tenant_id, "success", payment_id)
        return events


payment_service = PaymentService(events=event_service)
