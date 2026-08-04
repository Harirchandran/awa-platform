from typing import Any, Dict, Optional
from uuid import UUID

from backend.db.client import db_client


class BookingRepository:
    def __init__(self, db=db_client):
        self.db = db

    async def list_bookings(
        self,
        tenant_id: UUID,
        cursor: Optional[str] = None,
        limit: int = 20,
        status: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.db.list_bookings(
            tenant_id,
            cursor=cursor,
            limit=limit,
            status=status,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )

    async def get_booking(self, tenant_id: UUID, booking_id: UUID) -> Optional[Dict[str, Any]]:
        return await self.db.get_booking(tenant_id, booking_id)

    async def get_booking_by_idempotency_key(self, tenant_id: UUID, idempotency_key: str) -> Optional[Dict[str, Any]]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, customer_id, conversation_id, service_name,
                       booking_date, booking_time, status, created_at
                FROM bookings
                WHERE tenant_id = $1 AND idempotency_key = $2
                """,
                tenant_id,
                idempotency_key,
            )
            return dict(row) if row else None

    async def has_overlapping_booking(
        self,
        tenant_id: UUID,
        booking_date,
        booking_time,
        duration_mins: int,
        exclude_booking_id: Optional[UUID] = None,
        conn = None
    ) -> bool:
        if not booking_time:
            return False

        async def _run(c):
            query = """
                SELECT id FROM bookings
                WHERE tenant_id = $1
                  AND booking_date BETWEEN $2::date - 1 AND $2::date + 1
                  AND booking_time IS NOT NULL
                  AND status != 'cancelled'
                  AND (booking_date + booking_time) < (($2::date + $3::time) + $4 * INTERVAL '1 minute')
                  AND ($2::date + $3::time) < (booking_date + booking_time) + (COALESCE(booking_data->>'service_duration_mins', '30')::integer * INTERVAL '1 minute')
            """
            params = [tenant_id, booking_date, booking_time, duration_mins]
            if exclude_booking_id:
                query += " AND id != $5"
                params.append(exclude_booking_id)
            row = await c.fetchrow(query, *params)
            return row is not None

        if conn is not None:
            return await _run(conn)
        async with self.db.acquire() as connection:
            return await _run(connection)

    async def list_busy_intervals(self, tenant_id: UUID, booking_date) -> list[Dict[str, Any]]:
        """Return the minimal tenant-scoped occupancy facts needed for availability checks.

        The one-day look-behind/look-ahead mirrors ``has_overlapping_booking`` so
        unusually long appointments that cross midnight are not missed. Customer,
        conversation, and booking identifiers are deliberately not selected.
        """
        async with self.db.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT booking_date,
                       booking_time,
                       CASE
                           WHEN COALESCE(booking_data->>'service_duration_mins', '') ~ '^[0-9]{1,4}$'
                           THEN GREATEST(
                               5,
                               LEAST((booking_data->>'service_duration_mins')::integer, 1440)
                           )
                           ELSE 30
                       END AS duration_mins
                FROM bookings
                WHERE tenant_id = $1
                  AND booking_date BETWEEN $2::date - 1 AND $2::date + 1
                  AND booking_time IS NOT NULL
                  AND status != 'cancelled'
                ORDER BY booking_date, booking_time
                """,
                tenant_id,
                booking_date,
            )
        return [dict(row) for row in rows]

    async def create_booking(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        conversation_id: UUID,
        service_name: str,
        booking_date,
        booking_time=None,
        booking_data: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        existing = None
        if idempotency_key:
            existing = await self.get_booking_by_idempotency_key(tenant_id, idempotency_key)
            if existing:
                existing["_created"] = False
                return existing

        duration = 30
        if booking_data and "service_duration_mins" in booking_data:
            try:
                duration = int(booking_data["service_duration_mins"])
            except (ValueError, TypeError):
                pass

        import asyncpg
        if booking_time:
            async with self.db.acquire() as conn:
                async with conn.transaction():
                    # Strip time to lock the entire date for this tenant during the transaction
                    lock_key = f"booking:{tenant_id}:{booking_date}"
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1)::bigint)", lock_key)

                    if await self.has_overlapping_booking(tenant_id, booking_date, booking_time, duration, conn=conn):
                        raise ValueError("That appointment time is not available.")

                    try:
                        booking = await self.db.create_booking(
                            tenant_id=tenant_id,
                            customer_id=customer_id,
                            conversation_id=conversation_id,
                            service_name=service_name,
                            booking_date=booking_date,
                            booking_time=booking_time,
                            booking_data=booking_data,
                            idempotency_key=idempotency_key,
                            conn=conn,
                            **kwargs,
                        )
                        booking["_created"] = True
                        return booking
                    except asyncpg.UniqueViolationError as e:
                        raise ValueError("That appointment time is not available.") from e
        else:
            try:
                booking = await self.db.create_booking(
                    tenant_id=tenant_id,
                    customer_id=customer_id,
                    conversation_id=conversation_id,
                    service_name=service_name,
                    booking_date=booking_date,
                    booking_time=booking_time,
                    booking_data=booking_data,
                    idempotency_key=idempotency_key,
                    **kwargs,
                )
                booking["_created"] = True
                return booking
            except asyncpg.UniqueViolationError as e:
                raise ValueError("That appointment time is not available.") from e

    async def update_booking(
        self,
        tenant_id: UUID,
        booking_id: UUID,
        status: Optional[str] = None,
        booking_date: Optional[str] = None,
        booking_time: Optional[str] = None,
    ) -> bool:
        if booking_date or booking_time:
            existing = await self.get_booking(tenant_id, booking_id)
            if existing:
                new_date = booking_date or existing.get("booking_date")
                new_time = booking_time or existing.get("booking_time")

                duration = 30
                exist_data = existing.get("booking_data") or {}
                if "service_duration_mins" in exist_data:
                    try:
                        duration = int(exist_data["service_duration_mins"])
                    except (ValueError, TypeError):
                        pass

                if new_time:
                    async with self.db.acquire() as conn:
                        async with conn.transaction():
                            # Strip time to lock the entire date for this tenant during the transaction
                            lock_key = f"booking:{tenant_id}:{new_date}"
                            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1)::bigint)", lock_key)

                            if await self.has_overlapping_booking(tenant_id, new_date, new_time, duration, exclude_booking_id=booking_id, conn=conn):
                                raise ValueError("That appointment time is not available.")

                            return await self.db.update_booking(
                                tenant_id,
                                booking_id,
                                status=status,
                                booking_date=booking_date,
                                booking_time=booking_time,
                                conn=conn,
                            )

        return await self.db.update_booking(
            tenant_id,
            booking_id,
            status=status,
            booking_date=booking_date,
            booking_time=booking_time,
        )

    async def cancel_booking(self, tenant_id: UUID, booking_id: UUID) -> bool:
        return await self.update_booking(tenant_id, booking_id, status="cancelled")


booking_repository = BookingRepository()
