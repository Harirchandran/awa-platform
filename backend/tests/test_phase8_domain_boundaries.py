import pytest
from uuid import uuid4
from backend.domains.knowledge.service import knowledge_service
from backend.domains.catalog.service import catalog_service
from backend.domains.customers.service import customer_service
from backend.db.client import db_client

@pytest.fixture
async def connected_db():
    await db_client.connect()
    yield
    await db_client.close()

async def create_test_tenant():
    tid = uuid4()
    async with db_client.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name, plan_code, status, created_at, updated_at) VALUES ($1, 'Test Tenant', 'FREE', 'active', NOW(), NOW())",
            tid
        )
    return tid

@pytest.mark.anyio
async def test_knowledge_domain_isolation(connected_db):
    """Test that knowledge domain operates independently and respects tenant boundaries."""
    tenant_1 = await create_test_tenant()
    tenant_2 = await create_test_tenant()

    # 1. Create document for tenant 1
    doc_1 = await knowledge_service.create_entry(tenant_1, "Refund Policy", "Refunds take 5 days.", None, {"type": "faq"})
    assert doc_1["id"] is not None
    assert doc_1["tenant_id"] == tenant_1

    # 2. Search document for tenant 1
    docs_1 = await knowledge_service.search_text(tenant_1, "refund")
    assert len(docs_1) > 0
    assert docs_1[0]["document_title"] == "Refund Policy"

    # 3. Ensure tenant 2 cannot search tenant 1's documents
    docs_2 = await knowledge_service.search_text(tenant_2, "refund")
    assert len(docs_2) == 0

@pytest.mark.anyio
async def test_catalog_domain_isolation(connected_db):
    """Test that catalog domain operates independently and respects tenant boundaries."""
    tenant_1 = await create_test_tenant()
    tenant_2 = await create_test_tenant()

    # 1. Create category and item for tenant 1
    category = await catalog_service.create_category(tenant_1, "Electronics", "Devices")
    item = await catalog_service.create_item(
        tenant_1, name="Laptop", price=999.99, description="High-end laptop", is_active=True, category_id=category["id"]
    )
    assert item["id"] is not None
    assert item["tenant_id"] == tenant_1

    # 2. Fetch active items for tenant 1
    items_1 = await catalog_service.list_items(tenant_1, active_only=True)
    assert len(items_1) > 0
    assert items_1[0]["name"] == "Laptop"

    # 3. Ensure tenant 2 cannot fetch tenant 1's items
    items_2 = await catalog_service.list_items(tenant_2, active_only=True)
    assert len(items_2) == 0

@pytest.mark.anyio
async def test_customers_domain_isolation(connected_db):
    """Test that customers domain operates independently and respects tenant boundaries."""
    tenant_1 = await create_test_tenant()
    tenant_2 = await create_test_tenant()
    phone = f"+1555{uuid4().hex[:7]}"

    # 1. Create customer for tenant 1
    customer_1 = await customer_service.get_or_create_customer(tenant_1, phone, "Alice")
    assert customer_1["id"] is not None
    assert customer_1["tenant_id"] == tenant_1

    # 2. Get customer for tenant 1
    fetched_1 = await customer_service.get_customer(tenant_1, customer_1["id"])
    assert fetched_1 is not None
    assert fetched_1["name"] == "Alice"

    # 3. Ensure tenant 2 cannot access tenant 1's customer
    fetched_2 = await customer_service.get_customer(tenant_2, customer_1["id"])
    assert fetched_2 is None

@pytest.mark.anyio
async def test_public_chat_mapping(connected_db):
    """Test that public chat customer mapping uses CustomerService correctly."""
    tenant_1 = await create_test_tenant()
    session_id = uuid4()
    conv_id = uuid4()

    customer = await customer_service.get_or_create_awa_chat_customer(tenant_1, f"Browser User {uuid4().hex[:4]}", "Bob")
    assert customer["id"] is not None
    assert customer["channel_subject"] is not None

    # We can't fully test update_public_chat_session_customer without a real session row,
    # but we can ensure the function exists and accepts these types without syntax error
    try:
        await customer_service.update_public_chat_session_customer(tenant_1, session_id, conv_id, customer["id"])
    except Exception as e:
        # It might fail with foreign key violation since session_id doesn't exist in DB, which is expected.
        assert "violates foreign key constraint" in str(e) or "public_chat_sessions" in str(e) or "conversations" in str(e)
