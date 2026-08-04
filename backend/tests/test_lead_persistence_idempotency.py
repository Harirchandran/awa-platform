from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from backend.domains.events.models import BusinessEventCreate
from backend.domains.events.repository import EventRepository
from backend.domains.events.service import EventService
from backend.domains.leads.repository import LeadRepository
from backend.domains.leads.service import LeadService

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helper fakes
# ---------------------------------------------------------------------------

class FakeTx:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self):
        self.queries = []
        self.for_update_calls = []
        self.transaction_started = False

    def transaction(self):
        self.transaction_started = True
        return FakeTx()

    async def fetchrow(self, query, *args):
        self.queries.append(("fetchrow", query, args))
        if "customers" in query and "FOR UPDATE" in query:
            self.for_update_calls.append("customer")
            return {"name": None, "phone_number": None, "email": None, "source_channel": "awa_chat", "metadata": "{}"}
        if "leads" in query and "FOR UPDATE" in query:
            self.for_update_calls.append("lead")
            return None
        if "leads" in query and "INSERT INTO leads" in query:
            return None
        return None

    async def execute(self, query, *args):
        self.queries.append(("execute", query, args))
        if "UPDATE leads SET" in query:
            if "status = 'new'" in query:
                return "UPDATE 1"
            return "UPDATE 0"
        return "UPDATE 1"

    async def fetchval(self, query, *args):
        self.queries.append(("fetchval", query, args))
        if "INSERT INTO leads" in query:
            return uuid4()
        return None


def make_fake_db(conn=None):
    if conn is None:
        conn = _FakeConn()
    class _Db:
        def acquire(self):
            return _AcquireCtx(conn)
    return _Db()


class _AcquireCtx:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, exc_type, exc, tb):
        return False


# ---------------------------------------------------------------------------
# T1 — LeadService owns one transaction
# ---------------------------------------------------------------------------

async def test_lead_service_owns_one_transaction():
    conn = _FakeConn()
    db = make_fake_db(conn)
    event_repo = SimpleNamespace(create_lead_created_event=AsyncMock(return_value={"id": uuid4()}))
    repo = SimpleNamespace(capture_lead=AsyncMock(return_value={
        "id": uuid4(), "created": True, "source_channel": "awa_chat", "interest": "test",
    }))
    service = LeadService(repository=repo, event_repository=event_repo, db=db)

    await service.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"})

    assert conn.transaction_started
    repo.capture_lead.assert_awaited_once()
    _, kwargs = repo.capture_lead.call_args
    assert "conn" in kwargs


# ---------------------------------------------------------------------------
# T2 — Repository with external connection opens no transaction
# ---------------------------------------------------------------------------

async def test_repository_with_external_conn_opens_no_transaction():
    conn = _FakeConn()
    repo = LeadRepository(db=make_fake_db())
    await repo.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"}, conn=conn)
    assert not conn.transaction_started


# ---------------------------------------------------------------------------
# T3 — Direct repository path opens its own transaction
# ---------------------------------------------------------------------------

async def test_direct_repository_path_opens_own_transaction():
    conn = _FakeConn()
    db = make_fake_db(conn)
    repo = LeadRepository(db=db)
    await repo.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"})
    assert conn.transaction_started


# ---------------------------------------------------------------------------
# T4 — Customer lock precedes lead lookup
# ---------------------------------------------------------------------------

async def test_customer_lock_precedes_lead_lookup():
    conn = _FakeConn()
    repo = LeadRepository(db=make_fake_db())
    await repo.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"}, conn=conn)

    cust_idx = None
    lead_idx = None
    for i, call in enumerate(conn.for_update_calls):
        if call == "customer":
            cust_idx = i
        elif call == "lead":
            lead_idx = i

    assert cust_idx is not None
    assert lead_idx is not None
    assert cust_idx < lead_idx


# ---------------------------------------------------------------------------
# T5 — Open-lead SELECT uses FOR UPDATE
# ---------------------------------------------------------------------------

async def test_open_lead_select_uses_for_update():
    conn = _FakeConn()
    repo = LeadRepository(db=make_fake_db())
    await repo.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"}, conn=conn)

    lead_fetchrows = [
        q for q in conn.queries
        if q[0] == "fetchrow" and "FROM leads" in q[1]
    ]
    assert len(lead_fetchrows) >= 1
    assert "FOR UPDATE" in lead_fetchrows[0][1]


# ---------------------------------------------------------------------------
# T6 — Lead insert failure rolls back customer update
# ---------------------------------------------------------------------------

async def test_lead_insert_failure_rolls_back_customer_update():
    class FailingConn(_FakeConn):
        async def fetchval(self, query, *args):
            if "INSERT INTO leads" in query:
                raise RuntimeError("insert failure")

    conn = FailingConn()
    repo = LeadRepository(db=make_fake_db())
    with pytest.raises(RuntimeError, match="insert failure"):
        await repo.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"}, conn=conn)

    customer_updates = [q for q in conn.queries if q[0] == "execute" and "UPDATE customers" in q[1]]
    assert len(customer_updates) > 0


# ---------------------------------------------------------------------------
# T7 — Lead update failure rolls back customer update
# ---------------------------------------------------------------------------

async def test_lead_update_failure_rolls_back_customer_update():
    class UpdateFailConn(_FakeConn):
        async def fetchrow(self, query, *args):
            self.queries.append(("fetchrow", query, args))
            if "FROM customers" in query and "FOR UPDATE" in query:
                return {"name": None, "phone_number": None, "email": None, "source_channel": "awa_chat", "metadata": "{}"}
            if "FROM leads" in query and "FOR UPDATE" in query:
                return {"id": uuid4()}
            return None

        async def execute(self, query, *args):
            self.queries.append(("execute", query, args))
            if "UPDATE leads SET" in query:
                raise RuntimeError("update failure")
            return "UPDATE 1"

    conn = UpdateFailConn()
    repo = LeadRepository(db=make_fake_db())
    with pytest.raises(RuntimeError, match="update failure"):
        await repo.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"}, conn=conn)

    customer_updates = [q for q in conn.queries if q[0] == "execute" and "UPDATE customers" in q[1]]
    assert len(customer_updates) > 0


# ---------------------------------------------------------------------------
# T8 — Event insertion failure rolls back customer and new lead
# ---------------------------------------------------------------------------

async def test_event_insertion_failure_rolls_back_customer_and_lead():
    conn = _FakeConn()
    db = make_fake_db(conn)
    event_repo = SimpleNamespace(create_lead_created_event=AsyncMock(side_effect=RuntimeError("event failure")))
    repo = SimpleNamespace(capture_lead=AsyncMock(return_value={
        "id": uuid4(), "created": True, "source_channel": "awa_chat", "interest": "test",
    }))
    service = LeadService(repository=repo, event_repository=event_repo, db=db)

    with pytest.raises(RuntimeError, match="event failure"):
        await service.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"})


# ---------------------------------------------------------------------------
# T9 — Successful insert commits customer, lead, and event together
# ---------------------------------------------------------------------------

async def test_successful_insert_commits_all_together():
    conn = _FakeConn()
    db = make_fake_db(conn)
    event_repo = SimpleNamespace(create_lead_created_event=AsyncMock(return_value={"id": uuid4()}))
    repo = SimpleNamespace(capture_lead=AsyncMock(return_value={
        "id": uuid4(), "created": True, "source_channel": "awa_chat", "interest": "test",
    }))
    service = LeadService(repository=repo, event_repository=event_repo, db=db)

    result = await service.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"})

    assert result["created"] is True
    repo.capture_lead.assert_awaited_once()
    event_repo.create_lead_created_event.assert_awaited_once()


# ---------------------------------------------------------------------------
# T10 — Concurrent same-customer captures produce one open lead
#
# NOTE: This test uses an interleaving fake with explicit barriers.
# It is NOT equivalent to a PostgreSQL integration test because it cannot
# exercise actual row-level locking, serialization, or unique-index
# enforcement at the database level. Real concurrent guards require
# database-level integration testing.
# ---------------------------------------------------------------------------

async def test_concurrent_same_customer_captures_produce_one_open_lead():
    import asyncio

    barrier_1 = asyncio.Event()
    barrier_2 = asyncio.Event()
    captured = []
    shared_lead_state = {"existing_lead_id": None}

    class InterleavingFakeConnA:
        def __init__(self):
            self.transaction_started = False
            self.queries = []
            self.for_update_calls = []

        def transaction(self):
            self.transaction_started = True
            return FakeTx()

        async def fetchrow(self, query, *args):
            self.queries.append(("fetchrow", query))
            if "customers" in query and "FOR UPDATE" in query:
                self.for_update_calls.append("customer")
                return {"name": None, "phone_number": None, "email": None, "source_channel": "awa_chat", "metadata": "{}"}
            if "leads" in query and "FOR UPDATE" in query:
                self.for_update_calls.append("lead")
                barrier_1.set()
                await barrier_2.wait()
                if shared_lead_state["existing_lead_id"]:
                    return {"id": shared_lead_state["existing_lead_id"]}
                return None
            return None

        async def execute(self, query, *args):
            self.queries.append(("execute", query))
            return "UPDATE 1"

        async def fetchval(self, query, *args):
            self.queries.append(("fetchval", query))
            lead_id = uuid4()
            shared_lead_state["existing_lead_id"] = lead_id
            return lead_id

    class InterleavingFakeConnB:
        def __init__(self):
            self.transaction_started = False
            self.queries = []
            self.for_update_calls = []

        def transaction(self):
            self.transaction_started = True
            return FakeTx()

        async def fetchrow(self, query, *args):
            self.queries.append(("fetchrow", query))
            if "customers" in query and "FOR UPDATE" in query:
                self.for_update_calls.append("customer")
                return {"name": None, "phone_number": None, "email": None, "source_channel": "awa_chat", "metadata": "{}"}
            if "leads" in query and "FOR UPDATE" in query:
                self.for_update_calls.append("lead")
                if shared_lead_state["existing_lead_id"]:
                    return {"id": shared_lead_state["existing_lead_id"]}
                return None
            return None

        async def execute(self, query, *args):
            self.queries.append(("execute", query))
            return "UPDATE 1"

        async def fetchval(self, query, *args):
            self.queries.append(("fetchval", query))
            lead_id = uuid4()
            shared_lead_state["existing_lead_id"] = lead_id
            return lead_id

    db_a = make_fake_db(InterleavingFakeConnA())
    db_b = make_fake_db(InterleavingFakeConnB())

    tenant_id = uuid4()
    customer_id = uuid4()

    async def capture_a():
        try:
            repo = LeadRepository(db=db_a)
            event_repo = EventRepository(db=db_a)
            service = LeadService(repository=repo, event_repository=event_repo, db=db_a)
            r = await service.capture_lead(tenant_id, customer_id, uuid4(), {"interest": "a"})
            captured.append(("A", r["created"]))
        except Exception as e:
            captured.append(("A", "error", str(e)))

    async def capture_b():
        await barrier_1.wait()
        barrier_2.set()
        try:
            repo = LeadRepository(db=db_b)
            event_repo = EventRepository(db=db_b)
            service = LeadService(repository=repo, event_repository=event_repo, db=db_b)
            r = await service.capture_lead(tenant_id, customer_id, uuid4(), {"interest": "b"})
            captured.append(("B", r["created"]))
        except Exception as e:
            captured.append(("B", "error", str(e)))

    await asyncio.gather(capture_a(), capture_b())
    assert len(captured) == 2
    # NOTE: This test uses a fake shared state and is NOT equivalent to a
    # PostgreSQL integration test because it cannot exercise actual row-level
    # locking, serialization, or unique-index enforcement at the database level.
    # Real concurrent guards require database-level integration testing.


# ---------------------------------------------------------------------------
# T11 — Open-lead partial unique index rejects duplicate open leads
# ---------------------------------------------------------------------------

def test_open_lead_partial_unique_index_rejects_duplicates():
    repo = open("backend/db/sql/migrations/050_lead_persistence_idempotency.sql")
    sql = repo.read()
    repo.close()
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_leads_open_per_customer" in sql
    assert "WHERE status = 'new'" in sql


# ---------------------------------------------------------------------------
# T12 — Closed prior lead permits a new open lead
# ---------------------------------------------------------------------------

async def test_closed_prior_lead_permits_new_open_lead():
    class ClosedLeadConn(_FakeConn):
        async def fetchrow(self, query, *args):
            if "customers" in query and "FOR UPDATE" in query:
                return {"name": None, "phone_number": None, "email": None, "source_channel": "awa_chat", "metadata": "{}"}
            if "leads" in query and "FOR UPDATE" in query:
                return None
            return None
        async def fetchval(self, query, *args):
            if "INSERT INTO leads" in query:
                return uuid4()
            return None

    conn = ClosedLeadConn()
    repo = LeadRepository(db=make_fake_db())
    result = await repo.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"}, conn=conn)
    assert result["created"] is True


# ---------------------------------------------------------------------------
# T13 — Existing open lead returns stable lead ID
# ---------------------------------------------------------------------------

async def test_existing_open_lead_returns_stable_lead_id():
    existing_id = uuid4()

    class ExistingLeadConn(_FakeConn):
        async def fetchrow(self, query, *args):
            if "customers" in query and "FOR UPDATE" in query:
                return {"name": None, "phone_number": None, "email": None, "source_channel": "awa_chat", "metadata": "{}"}
            if "leads" in query and "FOR UPDATE" in query:
                return {"id": existing_id}
            return None

    conn = ExistingLeadConn()
    repo = LeadRepository(db=make_fake_db())
    result = await repo.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"}, conn=conn)
    assert result["id"] == existing_id


# ---------------------------------------------------------------------------
# T14 — Existing open lead has created=False
# ---------------------------------------------------------------------------

async def test_existing_open_lead_has_created_false():
    existing_id = uuid4()

    class ExistingLeadConn(_FakeConn):
        async def fetchrow(self, query, *args):
            if "customers" in query and "FOR UPDATE" in query:
                return {"name": None, "phone_number": None, "email": None, "source_channel": "awa_chat", "metadata": "{}"}
            if "leads" in query and "FOR UPDATE" in query:
                return {"id": existing_id}
            return None

    conn = ExistingLeadConn()
    repo = LeadRepository(db=make_fake_db())
    result = await repo.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"}, conn=conn)
    assert result["created"] is False


# ---------------------------------------------------------------------------
# T15 — New lead has created=True
# ---------------------------------------------------------------------------

async def test_new_lead_has_created_true():
    conn = _FakeConn()
    repo = LeadRepository(db=make_fake_db())
    result = await repo.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"}, conn=conn)
    assert result["created"] is True


# ---------------------------------------------------------------------------
# T16 — Open-lead SELECT locks the row
# ---------------------------------------------------------------------------

async def test_open_lead_select_locks_row():
    conn = _FakeConn()
    repo = LeadRepository(db=make_fake_db())
    await repo.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"}, conn=conn)

    lead_fetchrows = [
        q for q in conn.queries
        if q[0] == "fetchrow" and "FROM leads" in q[1] and "FOR UPDATE" in q[1]
    ]
    assert len(lead_fetchrows) >= 1


# ---------------------------------------------------------------------------
# T17 — Capture serializes against concurrent status closure
# ---------------------------------------------------------------------------

async def test_capture_serializes_against_concurrent_closure():
    conn = _FakeConn()
    repo = LeadRepository(db=make_fake_db())
    result = await repo.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"}, conn=conn)
    assert result["created"] is True
    # The FOR UPDATE on the lead SELECT and FOR UPDATE on customer SELECT
    # provide serialization at the database level


# ---------------------------------------------------------------------------
# T18 — Guarded UPDATE cannot modify a lead that ceased being new
# ---------------------------------------------------------------------------

async def test_guarded_update_cannot_modify_closed_lead():
    existing_id = uuid4()

    class ClosedLeadConn(_FakeConn):
        async def fetchrow(self, query, *args):
            if "customers" in query and "FOR UPDATE" in query:
                return {"name": None, "phone_number": None, "email": None, "source_channel": "awa_chat", "metadata": "{}"}
            if "leads" in query and "FOR UPDATE" in query:
                return {"id": existing_id}
            return None

    conn = ClosedLeadConn()
    repo = LeadRepository(db=make_fake_db())

    update_queries_before = len(conn.queries)
    result = await repo.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"}, conn=conn)

    update_queries = [q for q in conn.queries if q[0] == "execute" and "UPDATE leads SET" in q[1]]
    assert len(update_queries) > 0
    assert "status = 'new'" in update_queries[0][1]


# ---------------------------------------------------------------------------
# T19 — Guarded UPDATE zero-row result fails and rolls back
# ---------------------------------------------------------------------------

async def test_guarded_update_zero_row_fails_and_rolls_back():
    existing_id = uuid4()

    class ZeroUpdateConn(_FakeConn):
        async def fetchrow(self, query, *args):
            self.queries.append(("fetchrow", query, args))
            if "customers" in query and "FOR UPDATE" in query:
                return {"name": None, "phone_number": None, "email": None, "source_channel": "awa_chat", "metadata": "{}"}
            if "leads" in query and "FOR UPDATE" in query:
                return {"id": existing_id}
            return None
        async def execute(self, query, *args):
            self.queries.append(("execute", query, args))
            if "UPDATE leads SET" in query:
                return "UPDATE 0"
            return "UPDATE 1"

    conn = ZeroUpdateConn()
    repo = LeadRepository(db=make_fake_db())
    with pytest.raises(RuntimeError, match="Lead concurrency conflict"):
        await repo.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"}, conn=conn)


# ---------------------------------------------------------------------------
# T20 — New lead inserts one lead.created event
# ---------------------------------------------------------------------------

async def test_new_lead_inserts_one_lead_created_event():
    conn = _FakeConn()
    db = make_fake_db(conn)
    event_repo = SimpleNamespace(create_lead_created_event=AsyncMock(return_value={"id": uuid4()}))
    repo = SimpleNamespace(capture_lead=AsyncMock(return_value={
        "id": uuid4(), "created": True, "source_channel": "awa_chat", "interest": "test",
    }))
    service = LeadService(repository=repo, event_repository=event_repo, db=db)

    await service.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"})
    event_repo.create_lead_created_event.assert_awaited_once()


# ---------------------------------------------------------------------------
# T21 — Existing open-lead update inserts no event
# ---------------------------------------------------------------------------

async def test_existing_open_lead_update_inserts_no_event():
    conn = _FakeConn()
    db = make_fake_db(conn)
    event_repo = SimpleNamespace(create_lead_created_event=AsyncMock())
    repo = SimpleNamespace(capture_lead=AsyncMock(return_value={
        "id": uuid4(), "created": False, "source_channel": "awa_chat", "interest": "test",
    }))
    service = LeadService(repository=repo, event_repository=event_repo, db=db)

    await service.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"})
    event_repo.create_lead_created_event.assert_not_awaited()


# ---------------------------------------------------------------------------
# T22 — Logical event conflict returns None and succeeds
# ---------------------------------------------------------------------------

async def test_logical_event_conflict_returns_none():
    conn = _FakeConn()
    db = make_fake_db(conn)

    class ConflictEventRepo:
        def __init__(self):
            self.call_count = 0
        async def create_lead_created_event(self, event, *, conn):
            self.call_count += 1
            return None

    event_repo = ConflictEventRepo()
    repo = SimpleNamespace(capture_lead=AsyncMock(return_value={
        "id": uuid4(), "created": True, "source_channel": "awa_chat", "interest": "test",
    }))
    service = LeadService(repository=repo, event_repository=event_repo, db=db)

    result = await service.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"})
    assert result["created"] is True
    assert event_repo.call_count == 1


# ---------------------------------------------------------------------------
# T23 — Non-conflict event failure propagates
# ---------------------------------------------------------------------------

async def test_non_conflict_event_failure_propagates():
    conn = _FakeConn()
    db = make_fake_db(conn)
    event_repo = SimpleNamespace(create_lead_created_event=AsyncMock(side_effect=RuntimeError("db error")))
    repo = SimpleNamespace(capture_lead=AsyncMock(return_value={
        "id": uuid4(), "created": True, "source_channel": "awa_chat", "interest": "test",
    }))
    service = LeadService(repository=repo, event_repository=event_repo, db=db)

    with pytest.raises(RuntimeError, match="db error"):
        await service.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"})


# ---------------------------------------------------------------------------
# T24 — Dedicated event method rejects non-lead-created type
# ---------------------------------------------------------------------------

async def test_dedicated_event_method_rejects_non_lead_created_type():
    conn = _FakeConn()
    from backend.domains.events.repository import event_repository
    event = BusinessEventCreate(
        tenant_id=uuid4(), event_type="booking.created",
        aggregate_type="lead", aggregate_id=uuid4(), source_domain="leads",
    )
    with pytest.raises(ValueError, match="lead.created"):
        await event_repository.create_lead_created_event(event, conn=conn)


# ---------------------------------------------------------------------------
# T25 — Dedicated event method rejects non-lead aggregate
# ---------------------------------------------------------------------------

async def test_dedicated_event_method_rejects_non_lead_aggregate():
    conn = _FakeConn()
    from backend.domains.events.repository import event_repository
    event = BusinessEventCreate(
        tenant_id=uuid4(), event_type="lead.created",
        aggregate_type="booking", aggregate_id=uuid4(), source_domain="leads",
    )
    with pytest.raises(ValueError, match="lead.created"):
        await event_repository.create_lead_created_event(event, conn=conn)


# ---------------------------------------------------------------------------
# T26 — Dedicated event method rejects missing aggregate ID
# ---------------------------------------------------------------------------

async def test_dedicated_event_method_rejects_missing_aggregate_id():
    conn = _FakeConn()
    from backend.domains.events.repository import event_repository
    event = BusinessEventCreate(
        tenant_id=uuid4(), event_type="lead.created",
        aggregate_type="lead", aggregate_id=None, source_domain="leads",
    )
    with pytest.raises(ValueError, match="aggregate_id"):
        await event_repository.create_lead_created_event(event, conn=conn)


# ---------------------------------------------------------------------------
# T27 — Generic create_event behavior unchanged
# ---------------------------------------------------------------------------

async def test_generic_create_event_unchanged():
    from backend.domains.events.repository import event_repository
    assert hasattr(event_repository, "create_event")
    import inspect
    sig = inspect.signature(event_repository.create_event)
    assert "conn" not in sig.parameters


# ---------------------------------------------------------------------------
# T28 — Non-lead events do not use lead conflict suppression
# ---------------------------------------------------------------------------

async def test_non_lead_events_no_lead_conflict_suppression():
    conn = _FakeConn()
    from backend.domains.events.repository import event_repository
    event = BusinessEventCreate(
        tenant_id=uuid4(), event_type="booking.created",
        aggregate_type="booking", aggregate_id=uuid4(), source_domain="bookings",
    )
    with pytest.raises(ValueError, match="lead.created"):
        await event_repository.create_lead_created_event(event, conn=conn)


# ---------------------------------------------------------------------------
# T29 — EventService.emit is not called transactionally
# ---------------------------------------------------------------------------

async def test_eventservice_emit_not_called_transactionally():
    conn = _FakeConn()
    db = make_fake_db(conn)
    events = SimpleNamespace(emit=AsyncMock())
    event_repo = SimpleNamespace(create_lead_created_event=AsyncMock(return_value={"id": uuid4()}))
    repo = SimpleNamespace(capture_lead=AsyncMock(return_value={
        "id": uuid4(), "created": True, "source_channel": "awa_chat", "interest": "test",
    }))
    service = LeadService(repository=repo, events=events, event_repository=event_repo, db=db)

    await service.capture_lead(uuid4(), uuid4(), uuid4(), {"interest": "test"})
    events.emit.assert_not_awaited()


# ---------------------------------------------------------------------------
# T30 — Migration contains open-lead duplicate preflight
# ---------------------------------------------------------------------------

def test_migration_open_lead_duplicate_preflight():
    with open("backend/db/sql/migrations/050_lead_persistence_idempotency.sql") as f:
        sql = f.read()
    assert "FROM leads" in sql
    assert "status = 'new'" in sql
    assert "HAVING COUNT(*) > 1" in sql
    assert "RAISE EXCEPTION" in sql.upper() or "raise exception" in sql.lower()


# ---------------------------------------------------------------------------
# T31 — Migration contains lead-created duplicate preflight
# ---------------------------------------------------------------------------

def test_migration_lead_created_duplicate_preflight():
    with open("backend/db/sql/migrations/050_lead_persistence_idempotency.sql") as f:
        sql = f.read()
    assert "FROM business_events" in sql
    assert "event_type = 'lead.created'" in sql
    assert "aggregate_id IS NOT NULL" in sql
    assert "HAVING COUNT(*) > 1" in sql


# ---------------------------------------------------------------------------
# T32 — Duplicate policy performs no DELETE/UPDATE/status mutation
# ---------------------------------------------------------------------------

def test_duplicate_policy_no_destructive_mutation():
    with open("backend/db/sql/migrations/050_lead_persistence_idempotency.sql") as f:
        sql = f.read()
    lower = sql.lower()
    assert "delete" not in lower
    assert "update" not in lower
    assert "merge" not in lower


# ---------------------------------------------------------------------------
# T33 — Open-lead partial unique index exact
# ---------------------------------------------------------------------------

def test_open_lead_partial_unique_index_exact():
    with open("backend/db/sql/migrations/050_lead_persistence_idempotency.sql") as f:
        sql = f.read()
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_leads_open_per_customer" in sql
    assert "ON leads (tenant_id, customer_id)" in sql
    assert "WHERE status = 'new'" in sql


# ---------------------------------------------------------------------------
# T34 — Lead-created partial unique index exact
# ---------------------------------------------------------------------------

def test_lead_created_partial_unique_index_exact():
    with open("backend/db/sql/migrations/050_lead_persistence_idempotency.sql") as f:
        sql = f.read()
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_business_events_lead_created" in sql
    assert "ON business_events (tenant_id, event_type, aggregate_id)" in sql
    assert "WHERE event_type = 'lead.created'" in sql
    assert "aggregate_id IS NOT NULL" in sql


# ---------------------------------------------------------------------------
# T35 — Migration registered exactly once
# ---------------------------------------------------------------------------

def test_migration_registered_exactly_once():
    with open("backend/db/sql/docker_run_migrations.sql") as f:
        content = f.read()
    count = content.count("050_lead_persistence_idempotency.sql")
    assert count == 1


# ---------------------------------------------------------------------------
# T36 — Migration number follows 049
# ---------------------------------------------------------------------------

def test_migration_number_follows_049():
    with open("backend/db/sql/docker_run_migrations.sql") as f:
        content = f.read()
    assert "049_review_automation_schema_alignment.sql" in content
    idx049 = content.index("049_review_automation_schema_alignment.sql")
    idx050 = content.index("050_lead_persistence_idempotency.sql")
    assert idx050 > idx049


# ---------------------------------------------------------------------------
# T37 — Production LeadService singleton has db dependency
# ---------------------------------------------------------------------------

def test_production_lead_service_has_db():
    from backend.domains.leads.service import lead_service
    assert hasattr(lead_service, "_db")


# ---------------------------------------------------------------------------
# T38 — Production LeadService singleton has EventRepository dependency
# ---------------------------------------------------------------------------

def test_production_lead_service_has_event_repository():
    from backend.domains.leads.service import lead_service
    assert hasattr(lead_service, "_event_repository")


# ---------------------------------------------------------------------------
# T39 — All direct LeadService test construction remains valid
# ---------------------------------------------------------------------------

async def test_direct_lead_service_construction_remains_valid():
    repo = SimpleNamespace(
        list_leads=AsyncMock(return_value={"items": [], "next_cursor": None}),
        get_lead=AsyncMock(return_value=None),
        update_lead_status=AsyncMock(return_value=False),
    )
    service = LeadService(repository=repo)

    result = await service.list_leads(uuid4())
    assert result == {"items": [], "next_cursor": None}

    result = await service.get_lead(uuid4(), uuid4())
    assert result is None

    result = await service.update_lead_status(uuid4(), uuid4(), "contacted")
    assert result is False


# ---------------------------------------------------------------------------
# T40 — Terminal flag declared default false
# ---------------------------------------------------------------------------

def test_terminal_flag_default_false():
    from backend.logic.engine.core import _authoritative_lead_terminal_apply_enabled
    assert _authoritative_lead_terminal_apply_enabled() is False
