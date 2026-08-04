import pytest
import asyncio
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock
from backend.db.client import DatabaseClient, PublicChatTurnIntakeError

# Mocks for transaction failure & recovery tests
class FakeTransaction:
    def __init__(self, commit_callback=None, rollback_callback=None):
        self.commit_callback = commit_callback
        self.rollback_callback = rollback_callback
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            if self.rollback_callback:
                self.rollback_callback()
        else:
            if self.commit_callback:
                self.commit_callback()
        return False

class FakeConnection:
    def __init__(self, fetchrow_handler=None, execute_handler=None, fetchval_handler=None, commit_callback=None, rollback_callback=None):
        self.fetchrow_handler = fetchrow_handler or AsyncMock()
        self.execute_handler = execute_handler or AsyncMock()
        self.fetchval_handler = fetchval_handler or AsyncMock()
        self.commit_callback = commit_callback
        self.rollback_callback = rollback_callback

    async def fetchrow(self, query, *args):
        return await self.fetchrow_handler(query, *args)

    async def execute(self, query, *args):
        return await self.execute_handler(query, *args)

    async def fetchval(self, query, *args):
        return await self.fetchval_handler(query, *args)

    def transaction(self):
        return FakeTransaction(self.commit_callback, self.rollback_callback)

class FakeConnectionContext:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, exc_type, exc, tb):
        return False

@pytest.mark.anyio
async def test_intake_rollback_on_sequence_update_failure():
    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    channel_acc_id = uuid4()
    provider_msg_id = "test-provider-msg-id"
    trace_id = uuid4()

    rollback_called = False

    def on_rollback():
        nonlocal rollback_called
        rollback_called = True

    async def mock_fetchrow(query, *args):
        query_upper = query.upper()
        if "INSERT INTO MESSAGES" in query_upper:
            return {"id": uuid4(), "created_at": datetime.now(timezone.utc), "processing_status": "received"}
        elif "SELECT CONFIG_VERSION, SEQUENCE_NUMBER" in query_upper:
            # session exists
            return {"config_version": 5, "sequence_number": 10, "state_json": {"config_version": 5, "state": {}}}
        elif "SELECT VERSION FROM TENANT_CONFIGS" in query_upper:
            return {"version": 5}
        return None

    async def mock_execute(query, *args):
        query_upper = query.upper()
        if "UPDATE CONVERSATION_SESSIONS" in query_upper:
            # Sequence update fails!
            raise RuntimeError("Database connection lost during update")
        return "UPDATE 1"

    fake_conn = FakeConnection(
        fetchrow_handler=mock_fetchrow,
        execute_handler=mock_execute,
        rollback_callback=on_rollback
    )

    client = DatabaseClient(database_url="postgresql://mock")
    client.tenant_context = MagicMock(return_value=FakeConnectionContext(fake_conn))

    with pytest.raises(PublicChatTurnIntakeError) as exc_info:
        await client.create_or_get_public_chat_turn_intake(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            public_chat_session_id=session_id,
            channel_account_id=channel_acc_id,
            provider_message_id=provider_msg_id,
            message_text="Hello",
            message_raw_payload={},
            security_disposition="none",
            trace_id=trace_id,
            event_max_attempts=3
        )

    assert exc_info.value.reason_code == "intake_write_failed"
    assert rollback_called is True

@pytest.mark.anyio
async def test_intake_rollback_on_event_insert_failure():
    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    channel_acc_id = uuid4()
    provider_msg_id = "test-provider-msg-id"
    trace_id = uuid4()

    rollback_called = False

    def on_rollback():
        nonlocal rollback_called
        rollback_called = True

    async def mock_fetchrow(query, *args):
        query_upper = query.upper()
        if "INSERT INTO MESSAGES" in query_upper:
            return {"id": uuid4(), "created_at": datetime.now(timezone.utc), "processing_status": "received"}
        elif "SELECT CONFIG_VERSION, SEQUENCE_NUMBER" in query_upper:
            return {"config_version": 5, "sequence_number": 10, "state_json": {"config_version": 5, "state": {}}}
        elif "SELECT VERSION FROM TENANT_CONFIGS" in query_upper:
            return {"version": 5}
        return None

    async def mock_execute(query, *args):
        return "UPDATE 1"

    async def mock_fetchval(query, *args):
        query_upper = query.upper()
        if "INSERT INTO BUSINESS_EVENTS" in query_upper:
            # Event insert fails!
            raise RuntimeError("Unique key violation on event ID")
        return None

    fake_conn = FakeConnection(
        fetchrow_handler=mock_fetchrow,
        execute_handler=mock_execute,
        fetchval_handler=mock_fetchval,
        rollback_callback=on_rollback
    )

    client = DatabaseClient(database_url="postgresql://mock")
    client.tenant_context = MagicMock(return_value=FakeConnectionContext(fake_conn))

    with pytest.raises(PublicChatTurnIntakeError) as exc_info:
        await client.create_or_get_public_chat_turn_intake(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            public_chat_session_id=session_id,
            channel_account_id=channel_acc_id,
            provider_message_id=provider_msg_id,
            message_text="Hello",
            message_raw_payload={},
            security_disposition="none",
            trace_id=trace_id,
            event_max_attempts=3
        )

    assert exc_info.value.reason_code == "intake_write_failed"
    assert rollback_called is True

@pytest.mark.anyio
async def test_intake_cancelled_error_is_not_converted():
    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    channel_acc_id = uuid4()
    provider_msg_id = "test-provider-msg-id"
    trace_id = uuid4()

    async def mock_fetchrow(query, *args):
        # CancelledError is thrown!
        raise asyncio.CancelledError()

    fake_conn = FakeConnection(fetchrow_handler=mock_fetchrow)
    client = DatabaseClient(database_url="postgresql://mock")
    client.tenant_context = MagicMock(return_value=FakeConnectionContext(fake_conn))

    with pytest.raises(asyncio.CancelledError):
        await client.create_or_get_public_chat_turn_intake(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            public_chat_session_id=session_id,
            channel_account_id=channel_acc_id,
            provider_message_id=provider_msg_id,
            message_text="Hello",
            message_raw_payload={},
            security_disposition="none",
            trace_id=trace_id,
            event_max_attempts=3
        )

def test_polling_contract():
    from backend.api.routes.public_chat import _public_chat_message_record
    terminal = _public_chat_message_record({
        "id": uuid4(), "direction": "outbound", "message_text": "I'm sorry, I couldn't complete your request right now. Please try again.",
        "raw_payload": {"processing_status": "terminal_failed", "failure_code": "processing_retries_exhausted", "recoverable": False},
        "created_at": datetime.now(timezone.utc),
    }, None, session={})
    assert terminal["direction"] == "outbound"
    assert terminal["message_text"] == "I'm sorry, I couldn't complete your request right now. Please try again."
    assert terminal["raw_payload"]["processing_status"] == "terminal_failed"
    assert terminal["raw_payload"]["failure_code"] == "processing_retries_exhausted"
    assert terminal["raw_payload"]["recoverable"] is False
