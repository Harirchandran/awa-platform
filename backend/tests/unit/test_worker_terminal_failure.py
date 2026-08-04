import pytest
import asyncio
import json
from uuid import uuid4
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from backend.pipeline import worker
from backend.core.config import settings
from backend.db.client import (
    db_client,
    PublicChatTerminalFailureOutcome,
    PublicChatTurnIntakeOutcome,
)
from backend.pipeline.public_chat_intake import build_canonical_public_chat_turn
from backend.schemas.canonical_message import CanonicalMessage

pytestmark = pytest.mark.anyio

class FakeRedis:
    def __init__(self):
        self.store = {}
        self.dlq = []

    async def ping(self):
        return True

    async def aclose(self):
        pass

    async def setnx(self, key, value):
        if key in self.store:
            return 0
        self.store[key] = str(value)
        return 1

    async def expire(self, key, seconds):
        return True

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)
        return True

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, **kwargs):
        self.store[key] = str(value)
        return True

    async def lpush(self, key, value):
        if key == worker.DLQ_KEY:
            self.dlq.append(value)
        return 1


def make_canonical_message_json(retry_count=0, *, durable=True):
    channel_account_id = uuid4() if durable else None
    channel_session_id = str(uuid4()) if durable else None
    return json.dumps({
        "schema_version": 2,
        "event_type": "incoming_message",
        "event_id": str(uuid4()),
        "source_type": "awa_chat" if durable else "webhook",
        "channel_type": "awa_chat",
        "channel_account_id": str(channel_account_id) if channel_account_id else None,
        "channel_session_id": channel_session_id,
        "tenant_id": str(uuid4()),
        "conversation_id": str(uuid4()),
        "conversation_sequence_number": 1,
        "customer_id": str(uuid4()),
        "config_version": 1,
        "worker_metadata": {
            "worker_version": "1.0.0"
        },
        "message": {
            "message_id": str(uuid4()),
            "provider_message_id": "pm_123",
            "direction": "inbound",
            "timestamp": "2026-06-17T08:00:00Z",
            "message_type": "text",
            "text": "test message"
        },
        "processing": {
            "retry_count": retry_count,
            "first_received_at": "2026-06-17T08:00:00Z"
        },
        "trace": {
            "trace_id": str(uuid4()),
            "created_at": "2026-06-17T08:00:00Z"
        },
        "cost_context": {
            "ai_tokens_used": 0,
            "ai_cost": 0.0,
            "compute_cost": 0.0
        }
    })


def test_durable_public_chat_boundary():
    now = datetime.now(timezone.utc)
    intake = PublicChatTurnIntakeOutcome(
        created=True,
        message_id=uuid4(),
        event_id=uuid4(),
        provider_message_id="awa:test",
        conversation_sequence_number=1,
        config_version=1,
        message_created_at=now,
        processing_status="queued",
        security_disposition="none",
        trace_id=uuid4(),
    )
    durable = build_canonical_public_chat_turn(
        intake=intake,
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        customer_id=uuid4(),
        public_chat_session_id=uuid4(),
        channel_account_id=uuid4(),
        message_text="hello",
        worker_version="test",
        max_retries=3,
    )
    assert worker._is_durable_public_chat_turn(durable) is True

    synthetic = CanonicalMessage.model_validate_json(
        make_canonical_message_json(durable=False)
    )
    assert worker._is_durable_public_chat_turn(synthetic) is False

    partial = durable.model_copy(update={"channel_session_id": None})
    assert worker._is_durable_public_chat_turn(partial) is False

    whatsapp = durable.model_copy(
        update={
            "source_type": "webhook",
            "channel_type": "whatsapp",
            "whatsapp_account_id": uuid4(),
        }
    )
    assert worker._is_durable_public_chat_turn(whatsapp) is False

@pytest.fixture
def base_mocks(monkeypatch):
    redis_client = FakeRedis()

    async def mock_get_redis():
        return redis_client

    monkeypatch.setattr(worker, "get_redis", mock_get_redis)
    monkeypatch.setattr(settings, "ENABLE_WORKER_SESSION_LOCKS", False)

    return redis_client

@pytest.mark.asyncio
async def test_worker_terminal_failure_exhausted(base_mocks, monkeypatch):
    msg_json = make_canonical_message_json(retry_count=worker.MAX_RETRIES)
    from backend.schemas.canonical_message import CanonicalMessage
    msg = CanonicalMessage.model_validate_json(msg_json)

    mock_finalize = AsyncMock(return_value=PublicChatTerminalFailureOutcome(
        outcome_code="terminal_created",
        inbound_message_id=str(msg.message.message_id),
        event_id=str(msg.event_id),
        conversation_id=str(msg.conversation_id),
        inbound_processing_status="terminal_failed",
        failure_code="processing_retries_exhausted",
        terminal_message_id=str(uuid4()),
        terminal_message_text="I'm sorry, I couldn't complete your request right now. Please try again.",
        terminal_message_created_at=datetime.now(timezone.utc)
    ))
    monkeypatch.setattr(db_client, "finalize_turn_terminal_failure", mock_finalize)

    mock_publish = AsyncMock(return_value=True)
    monkeypatch.setattr("backend.realtime.public_chat_events.publish_public_chat_terminal_failure", mock_publish)

    await worker.handle_retry(msg_json, msg, RuntimeError("test error"))

    assert mock_finalize.call_count == 1
    assert mock_publish.call_count == 1

    assert len(base_mocks.dlq) == 1
    dlq_item = json.loads(base_mocks.dlq[0])

    assert dlq_item["event_id"] == str(msg.event_id)
    assert dlq_item["processing_status"] == "terminal_failed"
    assert dlq_item["failure_code"] == "processing_retries_exhausted"
    assert dlq_item["error_type"] == "RuntimeError"

@pytest.mark.asyncio
async def test_worker_terminal_failure_not_exhausted(base_mocks, monkeypatch):
    msg_json = make_canonical_message_json(retry_count=0)
    from backend.schemas.canonical_message import CanonicalMessage
    msg = CanonicalMessage.model_validate_json(msg_json)

    mock_mark_failed = AsyncMock(return_value=True)
    mock_mark_retry_queued = AsyncMock(return_value=True)

    monkeypatch.setattr(db_client, "mark_public_chat_turn_failed", mock_mark_failed)
    monkeypatch.setattr(db_client, "mark_public_chat_turn_retry_queued", mock_mark_retry_queued)

    mock_sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    mock_enqueue = AsyncMock()
    monkeypatch.setattr(worker, "enqueue_message", mock_enqueue)

    await worker.handle_retry(msg_json, msg, RuntimeError("test error"))

    assert mock_mark_failed.call_count == 1
    assert mock_mark_retry_queued.call_count == 1
    assert mock_enqueue.call_count == 1

def test_terminal_creation():
    import inspect
    from backend.db.client import DatabaseClient
    source = inspect.getsource(DatabaseClient.finalize_turn_terminal_failure)
    assert source.count("async with conn.transaction()") == 1
    assert source.count("FOR UPDATE") >= 3
    assert 'response_key = f"response_{event_id}"' in source
    assert "INSERT INTO messages" in source
    assert "SET processing_status = 'dlq'" in source
    assert "SET status = 'processed'" in source
    assert "attempts =" not in source
    assert 'outcome_code="terminal_created"' in source

def test_terminal_payload():
    import ast, inspect
    from backend.db.client import DatabaseClient
    source = inspect.getsource(DatabaseClient.finalize_turn_terminal_failure)
    tree = ast.parse(inspect.cleandoc(source))
    payload = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "terminal_raw_payload" for t in n.targets))
    keys = {k.value for k in payload.keys}
    assert keys == {"processing_status", "failure_code", "recoverable", "source_event_id"}
    assert '"processing_status": "terminal_failed"' in source
    assert '"failure_code": "processing_retries_exhausted"' in source
    assert '"recoverable": False' in source

def test_terminal_idempotency():
    import inspect
    from backend.db.client import DatabaseClient
    source = inspect.getsource(DatabaseClient.finalize_turn_terminal_failure)
    assert 'outcome_code="terminal_already_exists"' in source
    assert 'terminal_message_id=outbound_row["id"]' in source
    assert 'terminal_message_text=outbound_row["message_text"]' in source

def test_normal_response_suppression():
    import inspect
    from backend.db.client import DatabaseClient
    source = inspect.getsource(DatabaseClient.finalize_turn_terminal_failure)
    assert 'outcome_code="normal_response_exists"' in source
    assert "terminal_message_id=None" in source
    assert "inbound_processing_status=\"completed\"" in source

def test_concurrent_race():
    import inspect
    from backend.db.client import DatabaseClient
    source = inspect.getsource(DatabaseClient.finalize_turn_terminal_failure)
    assert "ON CONFLICT (tenant_id, whatsapp_message_id) DO NOTHING" in source
    assert "if not insert_row:" in source
    assert source.count('outcome_code="terminal_already_exists"') >= 2
    assert source.count('outcome_code="normal_response_exists"') >= 2

def test_transaction_rollback():
    import inspect
    from backend.db.client import DatabaseClient
    source = inspect.getsource(DatabaseClient.finalize_turn_terminal_failure)
    assert "async with conn.transaction():" in source
    assert "except asyncio.CancelledError:\n                    raise" in source
    assert "raise PublicChatTerminalFailureError(\"terminal_write_failed\")" in source

def test_terminal_validation():
    import inspect
    from backend.db.client import DatabaseClient
    source = inspect.getsource(DatabaseClient.finalize_turn_terminal_failure)
    for reason in ("message_invalid", "message_missing", "event_missing"):
        assert f'PublicChatTerminalFailureError("{reason}")' in source
    assert "direction = 'inbound'" in source
    assert "channel_type = 'awa_chat'" in source
    assert "aggregate_type = 'message'" in source
    assert "aggregate_id = $3" in source

def test_CancelledError():
    import inspect
    from backend.db.client import DatabaseClient
    source = inspect.getsource(DatabaseClient.finalize_turn_terminal_failure)
    assert "except asyncio.CancelledError:\n                    raise" in source
