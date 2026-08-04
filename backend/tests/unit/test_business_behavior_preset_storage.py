from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from backend.db.client import DatabaseClient


def _profile(business_type_key: str = "test_service_business") -> dict:
    return {
        "profile_name": "Test Service",
        "business_type_key": business_type_key,
        "journey_priority": ["booking", "faq"],
        "tone": "friendly",
        "tenant_editable_fields": ["tone", "journey_priority"],
    }


def _record(
    *,
    business_type_key: str = "test_service_business",
    version: int = 1,
    status: str = "draft",
    is_active: bool = False,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "business_type_key": business_type_key,
        "version": version,
        "profile_json": _profile(business_type_key),
        "tenant_editable_fields": ["tone", "journey_priority"],
        "status": status,
        "is_active": is_active,
        "created_at": now,
        "updated_at": now,
        "activated_at": now if is_active else None,
        "deactivated_at": None,
        "change_note": "test change",
    }


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeConn:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.fetchvals: list[object] = []
        self.fetchrows: list[object] = []
        self.fetch_results: list[list[object]] = []

    def transaction(self):
        return FakeTransaction()

    async def execute(self, query: str, *args):
        self.executed.append((query, args))
        return "UPDATE 1"

    async def fetchval(self, query: str, *args):
        self.executed.append((query, args))
        return self.fetchvals.pop(0)

    async def fetchrow(self, query: str, *args):
        self.executed.append((query, args))
        return self.fetchrows.pop(0)

    async def fetch(self, query: str, *args):
        self.executed.append((query, args))
        return self.fetch_results.pop(0)


def _client_with(conn: FakeConn) -> DatabaseClient:
    client = DatabaseClient("postgresql://unit-test")
    client.acquire = lambda: FakeAcquire(conn)
    return client


def test_business_behavior_preset_migration_declares_required_table_constraints_and_indexes():
    migration = Path("backend/db/sql/migrations/045_business_behavior_presets.sql").read_text(encoding="utf-8")
    runner = Path("backend/db/sql/docker_run_migrations.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS business_behavior_presets" in migration
    assert "business_type_key TEXT NOT NULL REFERENCES business_types(code)" in migration
    assert "CONSTRAINT business_behavior_presets_unique_version UNIQUE (business_type_key, version)" in migration
    assert "uniq_business_behavior_presets_active" in migration
    assert "WHERE is_active = TRUE" in migration
    assert "jsonb_typeof(profile_json) = 'object'" in migration
    assert "jsonb_typeof(tenant_editable_fields) = 'array'" in migration
    assert "status IN ('draft', 'active', 'inactive', 'deprecated')" in migration
    assert "version > 0" in migration
    assert "045_business_behavior_presets.sql" in runner


@pytest.mark.asyncio
async def test_create_business_behavior_preset_version_assigns_next_version_and_stays_inactive():
    conn = FakeConn()
    conn.fetchvals = [1, 1]
    conn.fetchrows = [_record(version=2, status="draft", is_active=False)]
    client = _client_with(conn)

    result = await client.create_business_behavior_preset_version(
        business_type_key="test_service_business",
        profile_json=_profile(),
        actor=uuid4(),
        change_note="initial draft",
    )

    assert result["version"] == 2
    assert result["is_active"] is False
    assert result["status"] == "draft"
    assert result["tenant_editable_fields"] == ["tone", "journey_priority"]
    assert any("pg_advisory_xact_lock" in query for query, _ in conn.executed)
    assert any("COALESCE(MAX(version), 0)" in query for query, _ in conn.executed)
    assert any("INSERT INTO business_behavior_presets" in query for query, _ in conn.executed)


@pytest.mark.asyncio
async def test_create_business_behavior_preset_version_requires_existing_business_type():
    conn = FakeConn()
    conn.fetchvals = [None]
    client = _client_with(conn)

    with pytest.raises(ValueError, match="Unknown business_type_key"):
        await client.create_business_behavior_preset_version(
            business_type_key="missing_type",
            profile_json=_profile("missing_type"),
        )


@pytest.mark.asyncio
async def test_activate_business_behavior_preset_version_deactivates_previous_active_version():
    conn = FakeConn()
    conn.fetchrows = [{"id": uuid4(), "status": "draft"}, _record(version=2, status="active", is_active=True)]
    conn.fetchvals = [1]
    client = _client_with(conn)

    result = await client.activate_business_behavior_preset_version("test_service_business", 2, actor=uuid4())

    assert result["is_active"] is True
    assert result["status"] == "active"
    assert result["previous_active_version"] == 1
    assert result["new_active_version"] == 2
    assert any("SET is_active = FALSE" in query for query, _ in conn.executed)
    assert any("SET is_active = TRUE" in query for query, _ in conn.executed)


@pytest.mark.asyncio
async def test_activate_business_behavior_preset_rejects_deprecated_version():
    conn = FakeConn()
    conn.fetchrows = [{"id": uuid4(), "status": "deprecated"}]
    client = _client_with(conn)

    with pytest.raises(ValueError, match="Deprecated"):
        await client.activate_business_behavior_preset_version("test_service_business", 1)


@pytest.mark.asyncio
async def test_deactivate_business_behavior_preset_version_returns_readable_inactive_version():
    conn = FakeConn()
    conn.fetchrows = [_record(version=1, status="inactive", is_active=False)]
    client = _client_with(conn)

    result = await client.deactivate_business_behavior_preset_version(
        "test_service_business",
        1,
        status="inactive",
        change_note="pause preset",
    )

    assert result["is_active"] is False
    assert result["status"] == "inactive"
    assert any("deactivated_at = NOW()" in query for query, _ in conn.executed)


@pytest.mark.asyncio
async def test_list_and_active_lookup_return_safe_dtos_without_internal_db_id():
    conn = FakeConn()
    conn.fetch_results = [[_record(version=1), _record(version=2, status="active", is_active=True)]]
    conn.fetchrows = [_record(version=2, status="active", is_active=True)]
    client = _client_with(conn)

    listed = await client.list_business_behavior_presets_for_type("test_service_business")
    active = await client.get_active_business_behavior_preset("test_service_business")

    assert [record["version"] for record in listed] == [1, 2]
    assert active["version"] == 2
    assert "id" not in active
    assert "created_by" not in active
    assert active["profile"]["business_type_key"] == "test_service_business"
