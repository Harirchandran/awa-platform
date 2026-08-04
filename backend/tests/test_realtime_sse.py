import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4, UUID
import pytest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from backend.api.routes import public_chat
from backend.realtime.public_chat_events import (
    publish_public_chat_message_event,
    publish_public_chat_terminal_failure,
    build_public_chat_message_event,
    get_public_chat_channel,
)
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
class FailingRedis:
    async def publish(self, channel, message):
        raise RuntimeError("Redis connection failed")
    async def aclose(self):
        pass
class MockPubSub:
    def __init__(self):
        self.subscribed = None
    async def subscribe(self, channel):
        self.subscribed = channel
    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        await asyncio.sleep(0.01)
        return None
    async def unsubscribe(self, channel):
        pass
    async def close(self):
        pass
class MockRedis:
    def __init__(self):
        self._pubsub = MockPubSub()
        self.xread_calls = []
    def pubsub(self):
        return self._pubsub
    async def xread(self, streams, count=None, block=None):
        self.xread_calls.append(streams)
        await asyncio.sleep(0.01)
        return []
    async def publish(self, channel, message):
        pass
    async def aclose(self):
        pass
@pytest.fixture
def mock_redis_client(monkeypatch):
    client = MockRedis()
    monkeypatch.setattr(public_chat.redis, "from_url", lambda *args, **kwargs: client)
    from backend.realtime import public_chat_events
    monkeypatch.setattr(public_chat_events, "get_redis_client", lambda: client)
    return client
@pytest.mark.anyio
async def test_public_session_receives_sse_connected_event(mock_redis_client):
    session_id = uuid4()
    conversation_id = uuid4()
    token = "test_token_abcdefghijklmnopqrstuvwxyz"
    with patch("backend.api.routes.public_chat.db_client") as mock_db:
        mock_db.get_public_chat_session = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": TENANT_ID,
            "customer_id": uuid4(),
            "conversation_id": conversation_id,
            "tenant_status": "active",
        })
        # Mock Request object
        from fastapi import Request
        request = AsyncMock(spec=Request)
        request.is_disconnected = AsyncMock(return_value=False)
        # Call route handler directly
        from backend.api.routes.public_chat import public_chat_events
        response = await public_chat_events(session_id=session_id, token=token, request=request)
        assert isinstance(response, StreamingResponse)
        assert response.status_code == 200
        # Read the first yielded connected event from the generator
        generator = response.body_iterator
        first_chunk = await anext(generator)
        data = json.loads(first_chunk.replace("data: ", "").strip())
        assert data["event_type"] == "connected"
@pytest.mark.anyio
async def test_invalid_token_session_is_rejected(mock_redis_client):
    session_id = uuid4()
    token = "invalid_token_123456"
    with patch("backend.api.routes.public_chat.db_client") as mock_db:
        mock_db.get_public_chat_session = AsyncMock(return_value=None)
        from fastapi import Request
        request = AsyncMock(spec=Request)
        from backend.api.routes.public_chat import public_chat_events
        with pytest.raises(HTTPException) as exc:
            await public_chat_events(session_id=session_id, token=token, request=request)
        assert exc.value.status_code == 404
@pytest.mark.anyio
async def test_session_cannot_subscribe_to_another_sessions_conversation(mock_redis_client):
    session_id = uuid4()
    conversation_id = uuid4()
    token = "test_token_abcdef"
    with patch("backend.api.routes.public_chat.db_client") as mock_db:
        mock_db.get_public_chat_session = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": TENANT_ID,
            "customer_id": uuid4(),
            "conversation_id": conversation_id,
            "tenant_status": "active",
        })
        from fastapi import Request
        request = AsyncMock(spec=Request)
        request.is_disconnected = AsyncMock(return_value=False)
        from backend.api.routes.public_chat import public_chat_events
        response = await public_chat_events(session_id=session_id, token=token, request=request)
        # Iterate once to get connection event, and second to trigger xread
        generator = response.body_iterator
        await anext(generator)
        try:
            await asyncio.wait_for(anext(generator), timeout=0.05)
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass
        expected_channel = f"public_chat:conversation:{conversation_id}"
        # Assert that xread was called with verified conversation channel stream
        assert any(expected_channel in call_dict for call_dict in mock_redis_client.xread_calls)
def test_event_payload_contains_only_safe_fields():
    conversation_id = uuid4()
    message_id = uuid4()
    content = "Hello, world!"
    event = build_public_chat_message_event(
        conversation_id=conversation_id,
        message_id=message_id,
        content=content,
    )
    allowed_keys = {
        "event_type", "conversation_id", "message_id", "role", "content", "created_at",
        "cards", "raw_payload", "quick_replies", "quick_replies_state"
    }
    assert set(event.keys()) == allowed_keys
    assert event["event_type"] == "message"
    assert event["role"] == "assistant"
    assert event["content"] == content
    assert event["conversation_id"] == str(conversation_id)
    assert event["message_id"] == str(message_id)

def test_event_payload_includes_rich_media():
    conversation_id = uuid4()
    message_id = uuid4()
    raw_payload = {
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": "opt_1", "title": "Confirm"}}
            ]
        }
    }
    event = build_public_chat_message_event(
        conversation_id=conversation_id,
        message_id=message_id,
        content="Choose an option",
        raw_payload=raw_payload,
    )
    assert event["quick_replies"] == ["Confirm"]
    assert event["quick_replies_state"] == "active"
    assert event["raw_payload"]["action"]["buttons"][0]["reply"]["title"] == "Confirm"


def test_event_payload_promotes_typed_conversation_status():
    event = build_public_chat_message_event(
        conversation_id=uuid4(),
        message_id=uuid4(),
        content="A team member is joining.",
        raw_payload={"conversation_status": "escalated"},
    )

    assert event["conversation_status"] == "escalated"
    assert event["raw_payload"]["conversation_status"] == "escalated"
@pytest.mark.anyio
async def test_publishing_failure_does_not_break_message_save_path(monkeypatch):
    from backend.pipeline.worker import _dispatch_response
    from backend.schemas.canonical_message import CanonicalMessage
    from backend.realtime import public_chat_events
    monkeypatch.setattr(public_chat_events, "get_redis_client", lambda: FailingRedis())
    msg = CanonicalMessage.model_validate({
        "event_id": uuid4(),
        "source_type": "awa_chat",
        "channel_type": "awa_chat",
        "channel_account_id": uuid4(),
        "channel_session_id": "session-1",
        "tenant_id": TENANT_ID,
        "conversation_id": uuid4(),
        "conversation_sequence_number": 1,
        "customer_id": uuid4(),
        "config_version": 1,
        "worker_metadata": {"worker_version": "test"},
        "message": {
            "message_id": uuid4(),
            "provider_message_id": "awa:test:1",
            "direction": "inbound",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message_type": "text",
            "text": "hi",
        },
        "processing": {"first_received_at": datetime.now(timezone.utc).isoformat()},
        "trace": {"trace_id": uuid4()},
    })
    from unittest.mock import MagicMock
    context = MagicMock()
    context.session_data.state.action_ledger.last_tool_result = None
    context.session_data.state.action_ledger.pending_action = None
    context.incoming_message = {"text": "hi"}
    with patch("backend.pipeline.worker.db_client") as mock_db:
        mock_db.check_outbound_sent = AsyncMock(return_value=False)
        mock_db.save_message = AsyncMock(return_value={"created": True, "id": uuid4()})
        await _dispatch_response(msg, "Reply text", context)
        mock_db.save_message.assert_awaited_once()
@pytest.mark.anyio
async def test_redis_unavailable_does_not_break_message_persistence(monkeypatch):
    from backend.realtime import public_chat_events
    monkeypatch.setattr(public_chat_events, "get_redis_client", lambda: FailingRedis())
    success = await publish_public_chat_message_event(uuid4(), uuid4(), "hello")
    assert success is False
def test_SSE_contract():
    import inspect
    from backend.realtime.public_chat_events import publish_public_chat_terminal_failure
    source = inspect.getsource(publish_public_chat_terminal_failure)
    for contract in ('event["processing_status"] = "terminal_failed"', 'event["failure_code"] = "processing_retries_exhausted"', 'event["recoverable"] = False', 'event["source_event_id"] = source_event_id', 'event["quick_replies"] = []'):
        assert contract in source
    for sentinel in ("PRIVATE_CUSTOMER_MESSAGE", "person@example.com", "+15550001111", "OTP-123456", "RAW_PROVIDER_FAILURE"):
        assert sentinel not in source

@pytest.mark.anyio
async def test_SSE_best_effort(monkeypatch):
    from backend.realtime import public_chat_events
    class OrdinaryFailure:
        async def xadd(self, *args, **kwargs):
            raise RuntimeError("redis_down")
    monkeypatch.setattr(public_chat_events, "get_redis_client", lambda: OrdinaryFailure())
    result = await publish_public_chat_terminal_failure(
        conversation_id=uuid4(), message_id=uuid4(), content="terminal",
        created_at=datetime.now(timezone.utc), source_event_id=str(uuid4()),
    )
    assert result is False

    class Cancelled:
        async def xadd(self, *args, **kwargs):
            raise asyncio.CancelledError()
    monkeypatch.setattr(public_chat_events, "get_redis_client", lambda: Cancelled())
    with pytest.raises(asyncio.CancelledError):
        await publish_public_chat_terminal_failure(
            conversation_id=uuid4(), message_id=uuid4(), content="terminal",
            created_at=datetime.now(timezone.utc), source_event_id=str(uuid4()),
        )
