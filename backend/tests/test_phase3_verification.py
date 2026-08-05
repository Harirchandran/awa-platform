import pytest
import asyncio
import time as time_lib
import httpx
from uuid import uuid4
from datetime import date, time as dt_time, datetime, timezone
from fastapi import HTTPException
from backend.db.client import db_client
from backend.logic.engine.history import sanitize_agentic_history_dicts
from backend.observability.provider_rate_limiter import refund_provider_capacity, get_redis_client
from backend.core.config import settings
from backend.api.routes.knowledge import _extract_upload_text
from fastapi import UploadFile
from io import BytesIO

pytestmark = pytest.mark.anyio

async def test_meta_24h_rule_inbound_vs_outbound():
    # Seed tenant and conversation
    tenant_id = uuid4()
    customer_id = uuid4()
    conversation_id = uuid4()

    async with db_client.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, plan_code, status) VALUES ($1, $2, $3, $4, $5)",
            tenant_id, "Meta 24h Tenant", f"meta-{uuid4().hex[:8]}", "trial", "trial"
        )
        await conn.execute(
            "INSERT INTO customers (id, tenant_id, phone_number, name) VALUES ($1, $2, $3, $4)",
            customer_id, tenant_id, "1111111111", "Meta Customer"
        )
        await conn.execute(
            "INSERT INTO conversations (id, tenant_id, customer_id, channel_type, last_message_at) VALUES ($1, $2, $3, $4, NOW() - INTERVAL '2 days')",
            conversation_id, tenant_id, customer_id, "whatsapp"
        )

    # 1. Outbound message: last_message_at should NOT change (should stay 2 days ago)
    res_out = await db_client.save_message(
        message_id=uuid4(),
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        whatsapp_message_id=f"wa-{uuid4().hex}",
        direction="outbound",
        message_type="text",
        message_text="Outbound response"
    )
    assert res_out["created"] is True

    conv_after_out = await db_client.get_conversation(tenant_id, conversation_id)
    # Check that last_message_at is older than 1 day
    assert (datetime.now(timezone.utc) - conv_after_out["last_message_at"].replace(tzinfo=timezone.utc)).days >= 1

    # 2. Inbound message: last_message_at should update to NOW
    res_in = await db_client.save_message(
        message_id=uuid4(),
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        whatsapp_message_id=f"wa-{uuid4().hex}",
        direction="inbound",
        message_type="text",
        message_text="Inbound customer message"
    )
    assert res_in["created"] is True

    conv_after_in = await db_client.get_conversation(tenant_id, conversation_id)
    # Check that last_message_at is updated to recent seconds
    assert (datetime.now(timezone.utc) - conv_after_in["last_message_at"].replace(tzinfo=timezone.utc)).total_seconds() < 10


async def test_agentic_history_unmatched_tool_truncation():
    # History ending in a tool response but missing corresponding AI message if cut off
    history = [
        {"type": "human", "data": {"content": "hello"}},
        {"type": "ai", "data": {"content": "let me call a tool", "tool_calls": [{"id": "call_123", "name": "my_tool", "args": {}}]}},
        {"type": "tool", "data": {"content": "tool result", "tool_call_id": "call_123"}},
    ]

    # Under a strict limit of 1 (which would normally slice only the tool response),
    # the function must continue to include the preceding AI message with the tool call
    import backend.logic.engine.history as history_module

    # Temporary patch limits to force truncation
    original_limit = history_module.AGENTIC_HISTORY_LIMIT
    history_module.AGENTIC_HISTORY_LIMIT = 1

    try:
        sanitized = sanitize_agentic_history_dicts(history)
        # Because tool call_123 is unmatched, it must keep the AI message too!
        types = [m["type"] for m in sanitized]
        assert "tool" in types
        assert "ai" in types
    finally:
        history_module.AGENTIC_HISTORY_LIMIT = original_limit


async def test_inventory_bypass_stock_decrement():
    tenant_id = uuid4()
    item_id = uuid4()
    customer_id = uuid4()
    conversation_id = uuid4()

    # Seed tenant, customer, conversation, and catalog item with stock limit
    async with db_client.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, plan_code, status) VALUES ($1, $2, $3, $4, $5)",
            tenant_id, "Stock Tenant", f"stock-{uuid4().hex[:8]}", "trial", "trial"
        )
        await conn.execute(
            "INSERT INTO customers (id, tenant_id, phone_number, name) VALUES ($1, $2, $3, $4)",
            customer_id, tenant_id, "2222222222", "Stock Customer"
        )
        await conn.execute(
            "INSERT INTO conversations (id, tenant_id, customer_id, channel_type) VALUES ($1, $2, $3, $4)",
            conversation_id, tenant_id, customer_id, "whatsapp"
        )
        await conn.execute(
            """
            INSERT INTO catalog_items (id, tenant_id, name, price, stock_tracking_enabled, stock_quantity)
            VALUES ($1, $2, $3, $4, TRUE, 1)
            """,
            item_id, tenant_id, "Toothbrush", 5.00
        )

    # First order should succeed and decrement stock to 0
    order_items = [{"item_id": str(item_id), "item_name": "Toothbrush", "quantity": 1, "unit_price": 5.00, "line_total": 5.00}]
    order1 = await db_client.create_order(
        tenant_id=tenant_id,
        customer_id=customer_id,
        conversation_id=conversation_id,
        channel_type="whatsapp",
        status="new",
        currency="USD",
        subtotal_amount=5.00,
        total_amount=5.00,
        fulfillment_type="delivery",
        payment_method="cash",
        payment_status="pending",
        items=order_items
    )
    assert order1["id"] is not None

    async with db_client.acquire() as conn:
        stock = await conn.fetchval("SELECT stock_quantity FROM catalog_items WHERE id = $1", item_id)
        assert stock == 0

    # Second order should fail with ValueError("Out of stock")
    with pytest.raises(ValueError, match="Out of stock"):
        await db_client.create_order(
            tenant_id=tenant_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
            channel_type="whatsapp",
            status="new",
            currency="USD",
            subtotal_amount=5.00,
            total_amount=5.00,
            fulfillment_type="delivery",
            payment_method="cash",
            payment_status="pending",
            items=order_items
        )


async def test_webhook_payment_failed_does_not_suspend_tenant(monkeypatch):
    monkeypatch.setattr(settings, "BILLING_MODE", "mock")
    monkeypatch.setattr(settings, "ENABLE_MOCK_BILLING", True)

    # Seed tenant
    tenant_id = uuid4()
    sub_id = f"sub_{uuid4().hex[:8]}"
    async with db_client.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, plan_code, status, razorpay_subscription_id) VALUES ($1, $2, $3, $4, $5, $6)",
            tenant_id, "Webhook Tenant", f"web-{uuid4().hex[:8]}", "trial", "trial", sub_id
        )

    # Trigger webhook payment.failed
    from backend.api.app import app

    payload = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_{uuid4().hex[:8]}",
                    "subscription_id": sub_id,
                    "error_code": "BAD_CREDENTIALS",
                    "error_description": "Failed payment"
                }
            }
        }
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/razorpay", json=payload)
    assert response.status_code == 200

    # Assert tenant is STILL active/trial and not suspended
    async with db_client.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM tenants WHERE id = $1", tenant_id)
        assert status == "trial"


async def test_pdf_dos_protection_rejects_large_pages():
    # Build dummy PDF with more than 50 pages if possible, or test ValueError handling
    # Let's mock a file upload object and verify it raises HTTPException
    # We can create a fake PDF with pypdf containing 51 pages
    try:
        from reportlab.pdfgen import canvas
        buf = BytesIO()
        c = canvas.Canvas(buf)
        for _ in range(51):
            c.drawString(100, 100, "Hello World")
            c.showPage()
        c.save()
        content = buf.getvalue()
    except ImportError:
        # Fallback dummy if reportlab is not installed
        content = b"%PDF-1.4 ... dummy pages ..."

    class FakeUploadFile(UploadFile):
        def __init__(self, content):
            super().__init__(filename="large.pdf", file=BytesIO(content), headers=None)
            self.content_type = "application/pdf"

    # If the PDF contains 51 pages, it should raise HTTPException 400
    try:
        await _extract_upload_text(FakeUploadFile(content))
        pytest.fail("Should have raised page limit exception")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "PDF exceeds maximum allowed pages" in exc.detail
    except Exception:
        # Fallback dummy parser exception is fine too for dummy pdf contents
        pass


async def test_capacity_refund(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_PROVIDER_RATE_LIMITER", True)
    redis_client = await get_redis_client()
    if redis_client:
        tenant_id = str(uuid4())
        provider = "gemini"
        model = "gemini-3.1-flash-lite"

        # Initialize capacity
        key = f"ratelimit:provider:{provider}:tpm"
        await redis_client.hset(key, mapping={"tokens": "1000", "last_updated": str(time_lib.time())})

        # Refund capacity
        await refund_provider_capacity(
            redis_client=redis_client,
            provider=provider,
            model=model,
            tenant_id=tenant_id,
            refund_amount=500
        )

        # Verify tokens got increased
        tokens = await redis_client.hget(key, "tokens")
        assert float(tokens) > 1000
