from typing import Any, Dict, Optional
from uuid import UUID

from backend.db.client import db_client


class OrderRepository:
    def __init__(self, db=db_client):
        self.db = db

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
        return await self.db.list_orders(
            tenant_id,
            cursor=cursor,
            limit=limit,
            status=status,
            fulfillment_type=fulfillment_type,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )

    async def get_order(self, tenant_id: UUID, order_id: UUID) -> Optional[Dict[str, Any]]:
        return await self.db.get_order(tenant_id, order_id)

    async def get_order_by_idempotency_key(self, tenant_id: UUID, idempotency_key: str) -> Optional[Dict[str, Any]]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, customer_id, conversation_id, channel_type,
                       status, currency, subtotal_amount, total_amount,
                       fulfillment_type, payment_method, payment_status,
                       customer_snapshot, delivery_address, order_data,
                       created_at, updated_at
                FROM orders
                WHERE tenant_id = $1 AND idempotency_key = $2
                """,
                tenant_id,
                idempotency_key,
            )
            return dict(row) if row else None

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
        if idempotency_key:
            existing = await self.get_order_by_idempotency_key(tenant_id, idempotency_key)
            if existing:
                existing["_created"] = False
                return existing
        order = await self.db.create_order(
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
        order["_created"] = True
        return order

    async def update_order(self, tenant_id: UUID, order_id: UUID, **updates) -> bool:
        status = updates.get("status")
        if status == "cancelled" and hasattr(self.db, "acquire"):
            async with self.db.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE payment_requests SET status = 'cancelled', updated_at = NOW() WHERE order_id = $2 AND status IN ('draft', 'sent', 'viewed')",
                        tenant_id,
                        order_id
                    )
        return await self.db.update_order(tenant_id, order_id, **updates)




order_repository = OrderRepository()
