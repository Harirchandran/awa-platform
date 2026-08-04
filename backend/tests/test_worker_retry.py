import asyncio
import inspect
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.db.client import PublicChatTerminalFailureOutcome, db_client
from backend.pipeline import worker
from backend.schemas.canonical_message import CanonicalMessage


def _durable_message():
    from backend.tests.unit.test_worker_terminal_failure import make_canonical_message_json
    return CanonicalMessage.model_validate_json(make_canonical_message_json(retry_count=worker.MAX_RETRIES))


class _LedgerRedis:
    def __init__(self, ledger):
        self.ledger = ledger
    async def lpush(self, key, value):
        self.ledger.append("redis_dlq")
        json.loads(value)
        return 1


def test_processing_lifecycle():
    source = inspect.getsource(worker.process_message)
    assert source.index("mark_public_chat_turn_processing") < source.index("agentic_engine.execute_step")
    assert "if not res:\n                        raise RuntimeError(\"worker_error\")" in source

def test_completion_lifecycle():
    source = inspect.getsource(worker.process_message)
    assert source.index("await _dispatch_response") < source.rindex("mark_public_chat_turn_completed")
    assert source.rindex("mark_public_chat_turn_completed") < source.rindex("return True")

def test_retry_lifecycle():
    source = inspect.getsource(worker.handle_retry)
    failed = source.index("mark_public_chat_turn_failed")
    delayed = source.index("await asyncio.sleep(delay)")
    enqueue = source.index("await enqueue_message")
    queued = source.index("mark_public_chat_turn_retry_queued")
    assert failed < delayed < enqueue < queued
    assert source.count("if not res:") == 2

@pytest.mark.anyio
async def test_worker_terminal_ordering(monkeypatch):
    ledger = []
    msg = _durable_message()
    gate = asyncio.Event()
    async def terminalizer(**kwargs):
        ledger.append("terminalizer_start")
        await asyncio.sleep(0)
        assert not gate.is_set()
        ledger.append("terminalizer_complete")
        gate.set()
        return PublicChatTerminalFailureOutcome("terminal_created", uuid4(), "terminal", datetime.now(timezone.utc), msg.message.message_id, msg.event_id, msg.conversation_id, "dlq", "processing_retries_exhausted")
    async def publish(**kwargs):
        assert gate.is_set()
        ledger.append("terminal_sse")
        return True
    monkeypatch.setattr(worker, "get_redis", AsyncMock(return_value=_LedgerRedis(ledger)))
    monkeypatch.setattr(db_client, "finalize_turn_terminal_failure", terminalizer)
    monkeypatch.setattr("backend.realtime.public_chat_events.publish_public_chat_terminal_failure", publish)
    await worker.handle_retry(msg.model_dump_json(), msg, RuntimeError("bounded"))
    assert ledger == ["terminalizer_start", "terminalizer_complete", "terminal_sse", "redis_dlq"]

@pytest.mark.anyio
async def test_terminalizer_failure(monkeypatch):
    ledger = []
    msg = _durable_message()
    publish = AsyncMock()
    monkeypatch.setattr(worker, "get_redis", AsyncMock(return_value=_LedgerRedis(ledger)))
    monkeypatch.setattr(db_client, "finalize_turn_terminal_failure", AsyncMock(side_effect=RuntimeError("terminalization_failed")))
    monkeypatch.setattr("backend.realtime.public_chat_events.publish_public_chat_terminal_failure", publish)
    with pytest.raises(RuntimeError, match="terminalization_failed"):
        await worker.handle_retry(msg.model_dump_json(), msg, RuntimeError("bounded"))
    publish.assert_not_awaited()
    assert ledger == []

def test_sanitized_DLQ():
    source = inspect.getsource(worker.handle_retry)
    expected = {"event_id", "tenant_id", "conversation_id", "message_id", "source_type", "channel_type", "processing_status", "failure_code", "retry_count", "error_type"}
    block = source[source.index("sanitized_dlq = {"):source.index("import json")]
    actual = {line.split('"')[1] for line in block.splitlines() if line.strip().startswith('"')}
    assert actual == expected
    for sentinel in ("PRIVATE_CUSTOMER_MESSAGE", "person@example.com", "+15550001111", "OTP-123456", "RAW_PROVIDER_FAILURE"):
        assert sentinel not in block

def test_non_AWA_preservation():
    source = inspect.getsource(worker.handle_retry)
    assert "if _is_durable_public_chat_turn(msg):" in source
    assert "dlq_manager.enrich_for_dlq" in source
    assert 'failure_code="MAX_RETRIES_EXCEEDED"' in source
