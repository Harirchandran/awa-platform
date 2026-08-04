from uuid import uuid4

import pytest

from backend.domains.customers.repository import CustomerRepository

pytestmark = pytest.mark.anyio


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self):
        self.calls = []

    def transaction(self):
        return FakeTransaction()

    async def execute(self, query, *args):
        self.calls.append((query, args))
        if "DELETE FROM customers" in query:
            return "DELETE 1"
        return "UPDATE 1"


class FakeTenantContext:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDB:
    def __init__(self):
        self.conn = FakeConnection()

    def tenant_context(self, tenant_id):
        return FakeTenantContext(self.conn)


async def test_merge_customers_closes_secondary_active_whatsapp_before_reassigning():
    db = FakeDB()
    repository = CustomerRepository(db=db)
    tenant_id = uuid4()
    primary_id = uuid4()
    secondary_id = uuid4()

    merged = await repository.merge_customers(tenant_id, primary_id, secondary_id)

    assert merged is True
    first_query, first_args = db.conn.calls[0]
    second_query, _ = db.conn.calls[1]
    assert "UPDATE conversations" in first_query
    assert "closed_reason" in first_query
    assert "customer_merged" in first_query
    assert "channel_type = 'whatsapp'" in first_query
    assert first_args == (tenant_id, secondary_id)
    assert "UPDATE conversations SET customer_id" in second_query
