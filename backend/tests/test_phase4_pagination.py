import pytest
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from backend.db.client import db_client, encode_cursor, decode_cursor
from backend.domains.orders.repository import order_repository

pytestmark = pytest.mark.anyio

def test_cursor_encoding_decoding():
    ts = datetime.now(timezone.utc)
    uid = uuid4()

    encoded = encode_cursor(ts, uid)
    assert isinstance(encoded, str)

    decoded_ts, decoded_uid = decode_cursor(encoded)
    # Compare with some tolerance on microseconds due to serialization precision if needed,
    # but isoformat is exact.
    assert decoded_ts.isoformat() == ts.isoformat()
    assert decoded_uid == uid

def test_cursor_fallback_for_legacy_format():
    ts = datetime.now(timezone.utc)
    ts_str = ts.isoformat()

    decoded_ts, decoded_uid = decode_cursor(ts_str)
    assert decoded_ts.isoformat() == ts.isoformat()
    assert decoded_uid is None

async def test_pagination_identical_timestamps():
    tenant_id = uuid4()
    customer_id = uuid4()
    conv_id = uuid4()

    # Seed tenant, customer, conversation in DB
    async with db_client.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, plan_code, status) VALUES ($1, $2, $3, $4, $5)",
            tenant_id, "Pagination Tenant", f"pag-{uuid4().hex[:8]}", "trial", "active"
        )
        await conn.execute(
            "INSERT INTO customers (id, tenant_id, phone_number, name) VALUES ($1, $2, $3, $4)",
            customer_id, tenant_id, "1234567890", "Test Customer"
        )
        await conn.execute(
            "INSERT INTO conversations (id, tenant_id, customer_id, channel_type) VALUES ($1, $2, $3, $4)",
            conv_id, tenant_id, customer_id, "whatsapp"
        )

    # Create three orders:
    # Order A: created at T0
    # Order B: created at T0 (identical timestamp to A, but different UUID)
    # Order C: created at T0 + 1 hour (newer)
    t0 = datetime(2026, 6, 29, 10, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=1)

    order_c = await order_repository.create_order(
        tenant_id=tenant_id,
        customer_id=customer_id,
        conversation_id=conv_id,
        channel_type="whatsapp",
        status="new",
        currency="INR",
        subtotal_amount=100.0,
        total_amount=100.0,
        fulfillment_type="delivery",
        payment_method="cod",
        payment_status="pending",
    )

    order_b = await order_repository.create_order(
        tenant_id=tenant_id,
        customer_id=customer_id,
        conversation_id=conv_id,
        channel_type="whatsapp",
        status="new",
        currency="INR",
        subtotal_amount=200.0,
        total_amount=200.0,
        fulfillment_type="delivery",
        payment_method="cod",
        payment_status="pending",
    )

    order_a = await order_repository.create_order(
        tenant_id=tenant_id,
        customer_id=customer_id,
        conversation_id=conv_id,
        channel_type="whatsapp",
        status="new",
        currency="INR",
        subtotal_amount=300.0,
        total_amount=300.0,
        fulfillment_type="delivery",
        payment_method="cod",
        payment_status="pending",
    )

    # Update created_at values to have exact duplicate timestamps
    async with db_client.acquire() as conn:
        await conn.execute("UPDATE orders SET created_at = $1 WHERE id = $2", t1, order_c["id"])
        await conn.execute("UPDATE orders SET created_at = $1 WHERE id = $2", t0, order_b["id"])
        await conn.execute("UPDATE orders SET created_at = $1 WHERE id = $2", t0, order_a["id"])

    # List orders with limit = 1. Should return Order C (newest).
    res1 = await order_repository.list_orders(tenant_id, limit=1)
    assert len(res1["items"]) == 1
    assert res1["items"][0]["id"] == order_c["id"]
    cursor1 = res1["next_cursor"]
    assert cursor1 is not None

    # List next page with limit = 1. Should return Order A or B (whichever is first by ID order DESC).
    # Since we sorted by created_at DESC, id DESC.
    expected_first_at_t0 = order_b if order_b["id"] > order_a["id"] else order_a
    expected_second_at_t0 = order_a if order_b["id"] > order_a["id"] else order_b

    res2 = await order_repository.list_orders(tenant_id, cursor=cursor1, limit=1)
    assert len(res2["items"]) == 1
    assert res2["items"][0]["id"] == expected_first_at_t0["id"]
    cursor2 = res2["next_cursor"]
    assert cursor2 is not None

    # List next page with limit = 1. Should return the second order at T0 (expected_second_at_t0).
    res3 = await order_repository.list_orders(tenant_id, cursor=cursor2, limit=1)
    assert len(res3["items"]) == 1
    assert res3["items"][0]["id"] == expected_second_at_t0["id"]
    cursor3 = res3["next_cursor"]
    assert cursor3 is None
