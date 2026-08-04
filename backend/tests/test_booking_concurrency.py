import pytest
import asyncio
from uuid import uuid4
from datetime import date, time
from backend.db.client import db_client
from backend.domains.bookings.repository import BookingRepository

pytestmark = pytest.mark.anyio

async def test_booking_concurrency_locks_overlapping_slots():
    tenant_id = uuid4()
    customer1_id = uuid4()
    customer2_id = uuid4()
    conv1_id = uuid4()
    conv2_id = uuid4()

    # Seed tenant, customers, and conversations in DB
    async with db_client.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, plan_code, status) VALUES ($1, $2, $3, $4, $5)",
            tenant_id, "Booking Concurrency Tenant", f"book-{uuid4().hex[:8]}", "trial", "trial"
        )
        await conn.execute(
            "INSERT INTO customers (id, tenant_id, phone_number, name) VALUES ($1, $2, $3, $4)",
            customer1_id, tenant_id, "1234567890", "Cust 1"
        )
        await conn.execute(
            "INSERT INTO customers (id, tenant_id, phone_number, name) VALUES ($1, $2, $3, $4)",
            customer2_id, tenant_id, "0987654321", "Cust 2"
        )
        await conn.execute(
            "INSERT INTO conversations (id, tenant_id, customer_id, channel_type) VALUES ($1, $2, $3, $4)",
            conv1_id, tenant_id, customer1_id, "whatsapp"
        )
        await conn.execute(
            "INSERT INTO conversations (id, tenant_id, customer_id, channel_type) VALUES ($1, $2, $3, $4)",
            conv2_id, tenant_id, customer2_id, "whatsapp"
        )

    repo = BookingRepository()

    # Run two create_booking calls for the exact same slot concurrently
    results = await asyncio.gather(
        repo.create_booking(
            tenant_id=tenant_id,
            customer_id=customer1_id,
            conversation_id=conv1_id,
            service_name="Haircut",
            booking_date=date(2026, 7, 1),
            booking_time=time(10, 0),
            idempotency_key=str(uuid4())
        ),
        repo.create_booking(
            tenant_id=tenant_id,
            customer_id=customer2_id,
            conversation_id=conv2_id,
            service_name="Massage",
            booking_date=date(2026, 7, 1),
            booking_time=time(10, 0),
            idempotency_key=str(uuid4())
        ),
        return_exceptions=True
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert "not available" in str(failures[0])
