"""
SessionManager + Postgres integration tests.
"""
import sys
from pathlib import Path
from uuid import UUID, uuid4
import pytest
from dotenv import load_dotenv
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / "backend" / ".env.local")
from backend.core.context import SessionState
from backend.core.states import ConversationState
from backend.db.client import db_client
from backend.pipeline.session_manager import session_manager
pytestmark = pytest.mark.anyio
TEST_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
@pytest.fixture
async def connected_db():
    await db_client.connect()
    yield
    await db_client.close()
class FakeRedis:
    def __init__(self):
        self._store: dict[str, str | int] = {}
    async def get(self, key: str):
        value = self._store.get(key)
        if isinstance(value, int):
            return str(value)
        return value
    async def set(self, key: str, value: str, ex: int | None = None, px: int | None = None, nx: bool = False, **kwargs):
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True
    async def delete(self, key: str):
        self._store.pop(key, None)
    async def incr(self, key: str):
        next_value = int(self._store.get(key, 0)) + 1
        self._store[key] = next_value
        return next_value
@pytest.fixture(autouse=True)
def fake_session_cache(monkeypatch):
    monkeypatch.setattr(session_manager, "redis", FakeRedis())
@pytest.fixture
async def active_config_version(connected_db):
    version = await db_client.get_active_config_version(TEST_TENANT_ID)
    assert version is not None
    return version
@pytest.fixture
async def customer(connected_db):
    phone = f"+1555{uuid4().hex[:7]}"
    from backend.domains.customers.repository import customer_repository
    customer_record = await customer_repository.get_or_create_customer(
        tenant_id=TEST_TENANT_ID,
        phone_number=phone,
        name="Test Customer",
    )
    assert customer_record["tenant_id"] == TEST_TENANT_ID
    return customer_record
@pytest.fixture
async def conversation(customer):
    conversation_record = await db_client.get_or_create_conversation(
        tenant_id=TEST_TENANT_ID,
        customer_id=customer["id"],
    )
    assert conversation_record["tenant_id"] == TEST_TENANT_ID
    assert conversation_record["customer_id"] == customer["id"]
    return conversation_record
@pytest.fixture
async def session(conversation):
    await session_manager.invalidate_cache(TEST_TENANT_ID, conversation["id"])
    session_record = await session_manager.get_or_create_session(
        tenant_id=TEST_TENANT_ID,
        conversation_id=conversation["id"],
    )
    return session_record
async def test_database_connection(connected_db):
    async with db_client.acquire() as conn:
        result = await conn.fetchval("SELECT 1")
    assert result == 1
async def test_get_active_config_version(active_config_version):
    assert active_config_version > 0
async def test_get_tenant_config(active_config_version):
    config = await db_client.get_tenant_config(TEST_TENANT_ID)
    assert config is not None
    assert config["version"] == active_config_version
    assert config["is_active"] is True
    config_data = config["config_json"]
    if isinstance(config_data, str):
        import json
        config_data = json.loads(config_data)
    assert isinstance(config_data, dict)
    assert bool(config_data.get("business_name"))
async def test_create_customer(customer):
    from backend.domains.customers.repository import customer_repository
    customer_again = await customer_repository.get_or_create_customer(
        tenant_id=TEST_TENANT_ID,
        phone_number=customer["phone_number"],
    )
    assert customer_again["id"] == customer["id"]
async def test_create_conversation(conversation):
    assert conversation["status"] == "active"
async def test_session_manager_new_session(session, conversation, active_config_version):
    assert session.tenant_id == TEST_TENANT_ID
    assert session.conversation_id == conversation["id"]
    assert session.config_version == active_config_version
    assert session.state.workflow_state == ConversationState.NEW
async def test_session_manager_update_state(session):
    new_state = SessionState(
        workflow_state=ConversationState.INTENT_IDENTIFIED,
        current_workflow_id="booking",
        step_data={"service": "haircut"},
    )
    await session_manager.update_state(session, new_state)
    db_session = await db_client.get_conversation_session(
        session.tenant_id,
        session.conversation_id,
    )
    assert db_session is not None
    state_json = db_session["state_json"]
    if isinstance(state_json, str):
        import json
        state_json = json.loads(state_json)
    assert state_json["state"]["workflow_state"] == "INTENT_IDENTIFIED"
async def test_session_manager_cache_hit(session):
    updated_state = SessionState(
        workflow_state=ConversationState.INTENT_IDENTIFIED,
        current_workflow_id="booking",
        step_data={"service": "haircut"},
    )
    await session_manager.update_state(session, updated_state)
    cached_session = await session_manager.get_or_create_session(
        tenant_id=TEST_TENANT_ID,
        conversation_id=session.conversation_id,
    )
    assert cached_session.state.workflow_state == ConversationState.INTENT_IDENTIFIED
    assert cached_session.state.step_data.get("service") == "haircut"
async def test_session_manager_db_fallback(session):
    updated_state = SessionState(
        workflow_state=ConversationState.INTENT_IDENTIFIED,
        current_workflow_id="booking",
        step_data={"service": "haircut"},
    )
    await session_manager.update_state(session, updated_state)
    await session_manager.invalidate_cache(TEST_TENANT_ID, session.conversation_id)
    restored_session = await session_manager.get_or_create_session(
        tenant_id=TEST_TENANT_ID,
        conversation_id=session.conversation_id,
    )
    assert restored_session.state.workflow_state == ConversationState.INTENT_IDENTIFIED
    assert restored_session.state.step_data.get("service") == "haircut"
async def test_sequence_numbers(conversation):
    conversation_id = conversation["id"]
    seq1 = await session_manager.get_next_sequence_number(TEST_TENANT_ID, conversation_id)
    seq2 = await session_manager.get_next_sequence_number(TEST_TENANT_ID, conversation_id)
    seq3 = await session_manager.get_next_sequence_number(TEST_TENANT_ID, conversation_id)
    assert (seq1, seq2, seq3) == (1, 2, 3)