import pytest
from uuid import uuid4
from datetime import datetime, timezone
from backend.db.client import db_client
from backend.domains.events.repository import EventRepository
from backend.domains.events.models import BusinessEventCreate

pytestmark = pytest.mark.anyio

async def test_event_recovery_claims_orphans():
    repo = EventRepository()
    await repo.ensure_schema()

    tenant_id = uuid4()
    # Seed tenant in DB
    async with db_client.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, plan_code, status) VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING",
            tenant_id, "Event Recovery Tenant", f"event-{uuid4().hex[:8]}", "trial", "trial"
        )

    # Create 3 events:
    # 1. Standard pending event
    # 2. Processing event locked 6 minutes ago (orphan)
    # 3. Processing event locked 1 minute ago (active)

    # 1. Standard pending event
    event1 = await repo.create_event(BusinessEventCreate(
        tenant_id=tenant_id,
        event_type="test.pending",
        aggregate_type="test",
        source_domain="test_domain",
        payload={"val": 1}
    ))

    # 2. Processing event locked 6 minutes ago
    event2 = await repo.create_event(BusinessEventCreate(
        tenant_id=tenant_id,
        event_type="test.orphan",
        aggregate_type="test",
        source_domain="test_domain",
        payload={"val": 2}
    ))

    # 3. Processing event locked 1 minute ago
    event3 = await repo.create_event(BusinessEventCreate(
        tenant_id=tenant_id,
        event_type="test.active",
        aggregate_type="test",
        source_domain="test_domain",
        payload={"val": 3}
    ))

    # Manually update events 2 and 3 in the DB to represent locked processing states
    async with db_client.acquire() as conn:
        # Event 2: processing, locked 6 minutes ago
        await conn.execute(
            """
            UPDATE business_events
            SET status = 'processing', locked_at = NOW() - INTERVAL '6 minutes', updated_at = NOW() - INTERVAL '6 minutes'
            WHERE id = $1
            """,
            event2["id"]
        )
        # Event 3: processing, locked 1 minute ago
        await conn.execute(
            """
            UPDATE business_events
            SET status = 'processing', locked_at = NOW() - INTERVAL '1 minute', updated_at = NOW() - INTERVAL '1 minute'
            WHERE id = $1
            """,
            event3["id"]
        )

    # Claim pending events
    claimed = await repo.claim_pending_events(limit=10)
    claimed_ids = [c["id"] for c in claimed]

    # Assert event1 (pending) and event2 (orphan) are claimed, but event3 (active) is NOT claimed!
    assert event1["id"] in claimed_ids
    assert event2["id"] in claimed_ids
    assert event3["id"] not in claimed_ids

    # Assert locked_at of claimed events is now close to current time
    for c in claimed:
        assert c["locked_at"] is not None
        # Should be within last 10 seconds
        time_diff = datetime.now(timezone.utc) - c["locked_at"].replace(tzinfo=timezone.utc)
        assert time_diff.total_seconds() < 10

def test_exhausted_event_claim():
    import inspect
    from backend.domains.events.repository import EventRepository
    source = inspect.getsource(EventRepository.claim_exhausted_conversation_turn_events)
    for predicate in ("event_type = 'conversation.turn.received'", "status = 'failed'", "attempts >= max_attempts", "processed_at IS NULL", "available_at <= NOW()", "locked_at IS NULL", "FOR UPDATE SKIP LOCKED"):
        assert predicate in source
    update = source[source.index("UPDATE business_events"):]
    assert "SET locked_at = NOW()" in update and "updated_at = NOW()" in update
    for forbidden in ("SET status", "attempts =", "max_attempts =", "payload =", "available_at ="):
        assert forbidden not in update

def test_concurrent_event_claim():
    import inspect
    from backend.domains.events.repository import EventRepository
    source = inspect.getsource(EventRepository.claim_exhausted_conversation_turn_events)
    assert "FOR UPDATE SKIP LOCKED" in source
    assert "WHERE id = ANY($1::uuid[])" in source


def test_malformed_exhausted_event_backoff_excludes_immediate_reclaim():
    import inspect
    source = inspect.getsource(EventRepository.defer_malformed_exhausted_event)
    assert "available_at = $3" in source
    assert "locked_at = NULL" in source
    assert "malformed_exhausted_event_authority" in source
    sql = source[source.index("UPDATE business_events"):]
    assert "payload" not in sql
