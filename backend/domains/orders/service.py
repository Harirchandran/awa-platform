from typing import Any, Dict, Optional
from uuid import UUID

from backend.domains.events.models import BusinessEventCreate
from backend.domains.events.service import EventService, event_service
from backend.domains.orders.repository import OrderRepository, order_repository
from backend.ops.logger import logger


class OrderService:
    def __init__(self, repository: OrderRepository = order_repository, events: Optional[EventService] = None):
        self.repository = repository
        self.events = events

    def _log(self, action: str, tenant_id: UUID, outcome: str, record_id: Optional[UUID] = None, reason_code: Optional[str] = None) -> None:
        extra = {"domain": "orders", "action": action, "tenant_id": str(tenant_id), "outcome": outcome}
        if record_id:
            extra["record_id"] = str(record_id)
        if reason_code:
            extra["reason_code"] = reason_code
        logger.debug("domain service call", extra=extra)

    async def list_orders(
        self,
        tenant_id: UUID,
        cursor: Optional[str] = None,
        limit: int = 20,
        status: Optional[str] = None,
        fulfillment_type: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = await self.repository.list_orders(
            tenant_id,
            cursor=cursor,
            limit=limit,
            status=status,
            fulfillment_type=fulfillment_type,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        self._log("list", tenant_id, "success")
        return result

    async def get_order(self, tenant_id: UUID, order_id: UUID) -> Optional[Dict[str, Any]]:
        order = await self.repository.get_order(tenant_id, order_id)
        self._log("get", tenant_id, "found" if order else "not_found", order_id)
        return order

    async def create_order(
        self,
        tenant_id: UUID,
        customer_id: Optional[UUID],
        conversation_id: Optional[UUID],
        channel_type: str,
        status: str,
        currency: str,
        subtotal_amount: float,
        total_amount: float,
        fulfillment_type: str,
        payment_method: str,
        payment_status: str,
        customer_snapshot: Optional[Dict[str, Any]] = None,
        delivery_address: Optional[str] = None,
        order_data: Optional[Dict[str, Any]] = None,
        items: Optional[list[Dict[str, Any]]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        order = await self.repository.create_order(
            tenant_id=tenant_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
            channel_type=channel_type,
            status=status,
            currency=currency,
            subtotal_amount=subtotal_amount,
            total_amount=total_amount,
            fulfillment_type=fulfillment_type,
            payment_method=payment_method,
            payment_status=payment_status,
            customer_snapshot=customer_snapshot,
            delivery_address=delivery_address,
            order_data=order_data,
            items=items,
            idempotency_key=idempotency_key,
        )
        if self.events and order.get("_created", True):
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="order.created",
                aggregate_type="order",
                aggregate_id=order.get("id"),
                source_domain="orders",
                conversation_id=conversation_id,
                customer_id=customer_id,
                payload={
                    "order_id": str(order.get("id")),
                    "customer_id": str(customer_id) if customer_id else None,
                    "conversation_id": str(conversation_id) if conversation_id else None,
                    "source_channel": channel_type,
                    "item_count": len(items or []),
                    "total_amount": total_amount,
                    "status": order.get("status"),
                },
                correlation_id=str(conversation_id or order.get("id")),
            ))
        self._log("create", tenant_id, "created" if order.get("_created", True) else "idempotent_hit", order.get("id"))
        return order

    async def update_order(self, tenant_id: UUID, order_id: UUID, **updates) -> bool:
        updated = await self.repository.update_order(tenant_id, order_id, **updates)
        self._log("update", tenant_id, "success" if updated else "not_found", order_id)
        return updated

    async def action_order(self, tenant_id: UUID, order_id: UUID, action: str, actor_id: Optional[UUID] = None, internal_note: Optional[str] = None) -> bool:
        action_to_status = {
            "accept": "accepted",
            "accepted": "accepted",
            "preparing": "preparing",
            "mark_preparing": "preparing",
            "ready": "ready",
            "mark_ready": "ready",
            "complete": "completed",
            "completed": "completed",
            "cancel": "cancelled",
            "cancelled": "cancelled",
        }
        status = action_to_status.get(action.strip().lower())
        if not status:
            self._log("action", tenant_id, "rejected", order_id, "unsupported_action")
            return False
        return await self.update_order(
            tenant_id,
            order_id,
            status=status,
            internal_note=internal_note,
            actor_id=actor_id,
        )




order_service = OrderService(events=event_service)
