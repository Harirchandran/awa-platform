from types import MethodType
from uuid import UUID, uuid4
import pytest
from fastapi.testclient import TestClient
from backend.api.app import app
from backend.api.dependencies import get_current_user
from backend.db.client import DatabaseClient
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000099")
async def override_member_user():
    return {"user_id": USER_ID, "tenant_id": TENANT_ID, "role": "member"}
class _FakeTransaction:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        return False
class _FakeConnection:
    def __init__(self, target_exists: bool):
        self.target_exists = target_exists
        self.executed: list[tuple[str, tuple]] = []
    def transaction(self):
        return _FakeTransaction()
    async def fetchval(self, query, *args):
        return 1 if self.target_exists else None
    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "UPDATE 1"
class _FakeAcquire:
    def __init__(self, conn: _FakeConnection):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, exc_type, exc, tb):
        return False
@pytest.mark.anyio
async def test_activate_config_missing_target_does_not_deactivate_existing_configs():
    client = DatabaseClient("postgresql://unused")
    conn = _FakeConnection(target_exists=False)
    def fake_acquire(self):
        return _FakeAcquire(conn)
    client.acquire = MethodType(fake_acquire, client)
    activated = await client.activate_config_version(TENANT_ID, uuid4())
    assert activated is False
    assert conn.executed == []
class _FakeSequenceConnection:
    def __init__(self, sequence_number: int):
        self.sequence_number = sequence_number
        self.fetchrow_calls: list[tuple[str, tuple]] = []
    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        return {"sequence_number": self.sequence_number}
class _FakeSequenceAcquire:
    def __init__(self, conn: _FakeSequenceConnection):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, exc_type, exc, tb):
        return False
@pytest.mark.anyio
async def test_sequence_number_uses_atomic_upsert_for_missing_session():
    client = DatabaseClient("postgresql://unused")
    conn = _FakeSequenceConnection(sequence_number=1)
    conversation_id = uuid4()
    def fake_acquire(self):
        return _FakeSequenceAcquire(conn)
    client.acquire = MethodType(fake_acquire, client)
    sequence = await client.get_next_sequence_number(TENANT_ID, conversation_id)
    assert sequence == 1
    assert len(conn.fetchrow_calls) == 1
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO conversation_sessions" in query
    assert "ON CONFLICT (conversation_id) DO UPDATE" in query
    assert "sequence_number = conversation_sessions.sequence_number + 1" in query
    assert args == (TENANT_ID, conversation_id)
def test_member_cannot_mutate_escalations():
    app.dependency_overrides[get_current_user] = override_member_user
    client = TestClient(app)
    conversation_id = uuid4()
    try:
        response = client.post(
            f"/api/v1/conversations/{conversation_id}/escalation",
            json={"reason": "needs a human"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 403
    assert "operator" in response.json()["error"]["message"].lower()
def test_member_cannot_edit_knowledge_entries():
    app.dependency_overrides[get_current_user] = override_member_user
    client = TestClient(app)
    entry_id = uuid4()
    try:
        response = client.patch(
            f"/api/v1/knowledge-base/{entry_id}",
            json={"enabled": False},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 403
    assert "tenant admin" in response.json()["error"]["message"].lower()