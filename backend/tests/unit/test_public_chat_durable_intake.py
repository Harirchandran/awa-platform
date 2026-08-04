import os
import re
import json
import pytest
import asyncio
from uuid import UUID, uuid4
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from backend.domains.events.models import BUSINESS_EVENT_TYPES
from backend.schemas.canonical_message import SecurityDisposition, MessagePayload, MessageDirection, MessageType
from backend.db.client import (
    db_client, PublicChatTurnIntakeOutcome, PublicChatTurnIntakeError, DatabaseClient
)
from backend.pipeline.public_chat_intake import build_canonical_public_chat_turn

# 13.1 Migration and Schema Tests
def test_migration_046_exists_and_validates():
    migration_path = "backend/db/sql/migrations/046_conversation_turn_intake.sql"
    assert os.path.exists(migration_path), "Migration 046 must exist"

    with open(migration_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Check transaction boundaries
    assert "BEGIN;" in content, "Migration must start with BEGIN;"
    assert "COMMIT;" in content, "Migration must end with COMMIT;"

    # Prove migration creates no table, adds no column, alters no enum
    assert "CREATE TABLE" not in content.upper(), "Migration must not create a table"
    assert "ALTER TABLE" not in content.upper(), "Migration must not alter a table"
    assert "ADD COLUMN" not in content.upper(), "Migration must not add a column"
    assert "ALTER TYPE" not in content.upper(), "Migration must not alter an enum"

    # Prove migration contains the exact unique intake index
    intake_index_pattern = r"CREATE UNIQUE INDEX IF NOT EXISTS\s+uq_business_events_conversation_turn_message\s+ON business_events\s*\(\s*tenant_id\s*,\s*event_type\s*,\s*aggregate_id\s*\)\s*WHERE event_type = 'conversation.turn.received'\s+AND aggregate_id IS NOT NULL"
    assert re.search(intake_index_pattern, content, re.IGNORECASE) is not None, "Missing exact uq_business_events_conversation_turn_message index"

    # Prove migration contains the exact browser processing recovery index
    recovery_index_pattern = r"CREATE INDEX IF NOT EXISTS\s+idx_messages_awa_processing_recovery\s+ON messages\s*\(\s*tenant_id\s*,\s*processing_status\s*,\s*created_at\s*\)\s*WHERE channel_type = 'awa_chat'\s+AND direction = 'inbound'"
    assert re.search(recovery_index_pattern, content, re.IGNORECASE) is not None, "Missing exact idx_messages_awa_processing_recovery index"

def test_complete_schema_contains_indexes():
    schema_path = "backend/db/sql/supabase_complete.sql"
    assert os.path.exists(schema_path), "Complete schema file must exist"

    with open(schema_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "uq_business_events_conversation_turn_message" in content, "uq_business_events_conversation_turn_message index missing from complete schema"
    assert "idx_messages_awa_processing_recovery" in content, "idx_messages_awa_processing_recovery index missing from complete schema"

def test_event_repository_ensure_schema_contains_index():
    repo_path = "backend/domains/events/repository.py"
    with open(repo_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "uq_business_events_conversation_turn_message" in content, "uq_business_events_conversation_turn_message index missing from EventRepository"
    assert "idx_messages_awa_processing_recovery" not in content, "idx_messages_awa_processing_recovery index must NOT be in EventRepository"

# 13.2 Event-Type Tests
def test_conversation_turn_received_in_business_event_types():
    assert "conversation.turn.received" in BUSINESS_EVENT_TYPES

# 13.3 Security-Disposition Tests
def test_security_disposition_validation():
    # Omitted field defaults to none
    payload_omitted = MessagePayload(
        message_id=uuid4(),
        provider_message_id="test-prov-id",
        direction=MessageDirection.INBOUND,
        timestamp=datetime.now(timezone.utc),
        message_type=MessageType.TEXT,
        text="Hello"
    )
    assert payload_omitted.security_disposition == SecurityDisposition.NONE

    # none validates
    payload_none = MessagePayload(
        message_id=uuid4(),
        provider_message_id="test-prov-id",
        direction=MessageDirection.INBOUND,
        timestamp=datetime.now(timezone.utc),
        message_type=MessageType.TEXT,
        text="Hello",
        security_disposition=SecurityDisposition.NONE
    )
    assert payload_none.security_disposition == SecurityDisposition.NONE

    # prompt_security_flagged validates
    payload_flagged = MessagePayload(
        message_id=uuid4(),
        provider_message_id="test-prov-id",
        direction=MessageDirection.INBOUND,
        timestamp=datetime.now(timezone.utc),
        message_type=MessageType.TEXT,
        text="Hello",
        security_disposition=SecurityDisposition.PROMPT_SECURITY_FLAGGED
    )
    assert payload_flagged.security_disposition == SecurityDisposition.PROMPT_SECURITY_FLAGGED

    # Invalid string fails validation
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        MessagePayload(
            message_id=uuid4(),
            provider_message_id="test-prov-id",
            direction=MessageDirection.INBOUND,
            timestamp=datetime.now(timezone.utc),
            message_type=MessageType.TEXT,
            text="Hello",
            security_disposition="invalid_disposition"
        )

# Mocks for database tests
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
async def test_new_intake_transaction_success():
    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    channel_acc_id = uuid4()
    provider_msg_id = "test-provider-msg-id"
    trace_id = uuid4()

    # Track order of operations
    operations = []

    async def mock_fetchrow(query, *args):
        query_upper = query.upper()
        if "INSERT INTO MESSAGES" in query_upper:
            operations.append("messages_insert")
            return {"id": uuid4(), "created_at": datetime.now(timezone.utc), "processing_status": "received"}
        elif "SELECT CONFIG_VERSION, SEQUENCE_NUMBER" in query_upper:
            operations.append("session_lock")
            # session exists
            return {"config_version": 5, "sequence_number": 10, "state_json": {"config_version": 5, "state": {}}}
        elif "SELECT VERSION FROM TENANT_CONFIGS" in query_upper:
            return {"version": 5}
        return None

    async def mock_execute(query, *args):
        query_upper = query.upper()
        if "UPDATE CONVERSATION_SESSIONS" in query_upper:
            operations.append("sequence_increment")
        return "UPDATE 1"

    async def mock_fetchval(query, *args):
        query_upper = query.upper()
        if "INSERT INTO BUSINESS_EVENTS" in query_upper:
            operations.append("event_insert")
            # Verify payload contains only approved safe keys
            payload_str = args[6]
            payload = json.loads(payload_str)
            assert set(payload.keys()) == {
                "public_chat_session_id",
                "channel_account_id",
                "conversation_sequence_number",
                "config_version",
                "security_disposition",
                "trace_id"
            }
            # Verify no customer PII / sentinel values in payload or SQL args
            for val in args:
                val_str = str(val)
                assert "PRIVATE_MESSAGE_SENTINEL" not in val_str
                assert "person@example.com" not in val_str
                assert "+15550001111" not in val_str
                assert "OTP-123456" not in val_str
            return uuid4()
        return None

    fake_conn = FakeConnection(
        fetchrow_handler=mock_fetchrow,
        execute_handler=mock_execute,
        fetchval_handler=mock_fetchval
    )

    client = DatabaseClient(database_url="postgresql://mock")
    client.tenant_context = MagicMock(return_value=FakeConnectionContext(fake_conn))

    outcome = await client.create_or_get_public_chat_turn_intake(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        customer_id=customer_id,
        public_chat_session_id=session_id,
        channel_account_id=channel_acc_id,
        provider_message_id=provider_msg_id,
        message_text="PRIVATE_MESSAGE_SENTINEL with person@example.com +15550001111 OTP-123456",
        message_raw_payload={"pii": "OTP-123456"},
        security_disposition="none",
        trace_id=trace_id,
        event_max_attempts=3
    )

    assert outcome.created is True
    assert outcome.conversation_sequence_number == 11
    assert outcome.config_version == 5
    assert outcome.trace_id == trace_id
    assert outcome.security_disposition == "none"

    # Assert correct order of operations: message insert, session lock, sequence increment, event insert
    assert operations == ["messages_insert", "session_lock", "sequence_increment", "event_insert"]

@pytest.mark.anyio
async def test_new_intake_missing_session_creates_session():
    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    channel_acc_id = uuid4()
    provider_msg_id = "test-provider-msg-id"
    trace_id = uuid4()

    operations = []

    async def mock_fetchrow(query, *args):
        query_upper = query.upper()
        if "INSERT INTO MESSAGES" in query_upper:
            operations.append("messages_insert")
            return {"id": uuid4(), "created_at": datetime.now(timezone.utc), "processing_status": "received"}
        elif "SELECT CONFIG_VERSION, SEQUENCE_NUMBER" in query_upper:
            operations.append("session_lock")
            # Session missing
            return None
        elif "SELECT VERSION FROM TENANT_CONFIGS" in query_upper:
            operations.append("config_lookup")
            return {"version": 42}
        return None

    async def mock_execute(query, *args):
        query_upper = query.upper()
        if "INSERT INTO CONVERSATION_SESSIONS" in query_upper:
            operations.append("session_create")
        return "INSERT 1"

    async def mock_fetchval(query, *args):
        query_upper = query.upper()
        if "INSERT INTO BUSINESS_EVENTS" in query_upper:
            operations.append("event_insert")
            return uuid4()
        return None

    fake_conn = FakeConnection(
        fetchrow_handler=mock_fetchrow,
        execute_handler=mock_execute,
        fetchval_handler=mock_fetchval
    )

    client = DatabaseClient(database_url="postgresql://mock")
    client.tenant_context = MagicMock(return_value=FakeConnectionContext(fake_conn))

    outcome = await client.create_or_get_public_chat_turn_intake(
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

    assert outcome.created is True
    assert outcome.conversation_sequence_number == 1
    assert outcome.config_version == 42
    assert operations == ["messages_insert", "session_lock", "config_lookup", "session_create", "event_insert"]

@pytest.mark.anyio
async def test_new_intake_missing_config_raises_error():
    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    channel_acc_id = uuid4()
    provider_msg_id = "test-provider-msg-id"
    trace_id = uuid4()

    async def mock_fetchrow(query, *args):
        query_upper = query.upper()
        if "INSERT INTO MESSAGES" in query_upper:
            return {"id": uuid4(), "created_at": datetime.now(timezone.utc), "processing_status": "received"}
        elif "SELECT CONFIG_VERSION, SEQUENCE_NUMBER" in query_upper:
            return None # Missing session
        elif "SELECT VERSION FROM TENANT_CONFIGS" in query_upper:
            return None # Missing active config!
        return None

    fake_conn = FakeConnection(fetchrow_handler=mock_fetchrow)
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
    assert exc_info.value.reason_code == "active_config_missing"

# 13.5 Duplicate Tests
@pytest.mark.anyio
async def test_duplicate_intake_returns_original_identities():
    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    channel_acc_id = uuid4()
    provider_msg_id = "duplicate-msg-id"
    trace_id = uuid4()

    original_msg_id = uuid4()
    original_event_id = uuid4()
    original_created_at = datetime.now(timezone.utc)

    async def mock_fetchrow(query, *args):
        query_upper = query.upper()
        if "INSERT INTO MESSAGES" in query_upper:
            # Duplicate conflict returns None
            return None
        elif "SELECT ID, CREATED_AT, PROCESSING_STATUS FROM MESSAGES" in query_upper:
            # Select existing message
            return {"id": original_msg_id, "created_at": original_created_at, "processing_status": "received"}
        elif "SELECT ID, CONVERSATION_ID, CUSTOMER_ID, PAYLOAD FROM BUSINESS_EVENTS" in query_upper:
            # Select existing event
            payload = {
                "public_chat_session_id": str(session_id),
                "channel_account_id": str(channel_acc_id),
                "conversation_sequence_number": 7,
                "config_version": 2,
                "security_disposition": "prompt_security_flagged",
                "trace_id": str(trace_id)
            }
            return {
                "id": original_event_id,
                "conversation_id": conversation_id,
                "customer_id": customer_id,
                "payload": json.dumps(payload)
            }
        return None

    fake_conn = FakeConnection(fetchrow_handler=mock_fetchrow)
    client = DatabaseClient(database_url="postgresql://mock")
    client.tenant_context = MagicMock(return_value=FakeConnectionContext(fake_conn))

    outcome = await client.create_or_get_public_chat_turn_intake(
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

    assert outcome.created is False
    assert outcome.message_id == original_msg_id
    assert outcome.event_id == original_event_id
    assert outcome.conversation_sequence_number == 7
    assert outcome.config_version == 2
    assert outcome.security_disposition == "prompt_security_flagged"
    assert outcome.trace_id == trace_id
    assert outcome.message_created_at == original_created_at

@pytest.mark.anyio
async def test_duplicate_missing_event_raises_error():
    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    channel_acc_id = uuid4()
    provider_msg_id = "duplicate-msg-id"
    trace_id = uuid4()

    async def mock_fetchrow(query, *args):
        query_upper = query.upper()
        if "INSERT INTO MESSAGES" in query_upper:
            return None
        elif "SELECT ID, CREATED_AT, PROCESSING_STATUS FROM MESSAGES" in query_upper:
            return {"id": uuid4(), "created_at": datetime.now(timezone.utc), "processing_status": "received"}
        elif "SELECT ID, CONVERSATION_ID, CUSTOMER_ID, PAYLOAD FROM BUSINESS_EVENTS" in query_upper:
            # Event is missing!
            return None
        return None

    fake_conn = FakeConnection(fetchrow_handler=mock_fetchrow)
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
    assert exc_info.value.reason_code == "existing_intake_event_missing"

@pytest.mark.anyio
async def test_duplicate_corrupt_payload_raises_error():
    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    channel_acc_id = uuid4()
    provider_msg_id = "duplicate-msg-id"
    trace_id = uuid4()

    async def mock_fetchrow(query, *args):
        query_upper = query.upper()
        if "INSERT INTO MESSAGES" in query_upper:
            return None
        elif "SELECT ID, CREATED_AT, PROCESSING_STATUS FROM MESSAGES" in query_upper:
            return {"id": uuid4(), "created_at": datetime.now(timezone.utc), "processing_status": "received"}
        elif "SELECT ID, CONVERSATION_ID, CUSTOMER_ID, PAYLOAD FROM BUSINESS_EVENTS" in query_upper:
            # Corrupt payload JSON
            return {
                "id": uuid4(),
                "conversation_id": conversation_id,
                "customer_id": customer_id,
                "payload": "{invalid-json}"
            }
        return None

    fake_conn = FakeConnection(fetchrow_handler=mock_fetchrow)
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
    assert exc_info.value.reason_code == "existing_intake_event_invalid"

@pytest.mark.anyio
async def test_duplicate_ownership_mismatch_raises_error():
    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    channel_acc_id = uuid4()
    provider_msg_id = "duplicate-msg-id"
    trace_id = uuid4()

    async def mock_fetchrow(query, *args):
        query_upper = query.upper()
        if "INSERT INTO MESSAGES" in query_upper:
            return None
        elif "SELECT ID, CREATED_AT, PROCESSING_STATUS FROM MESSAGES" in query_upper:
            return {"id": uuid4(), "created_at": datetime.now(timezone.utc), "processing_status": "received"}
        elif "SELECT ID, CONVERSATION_ID, CUSTOMER_ID, PAYLOAD FROM BUSINESS_EVENTS" in query_upper:
            # Event belongs to another conversation/customer
            payload = {
                "public_chat_session_id": str(session_id),
                "channel_account_id": str(channel_acc_id),
                "conversation_sequence_number": 7,
                "config_version": 2,
                "security_disposition": "none",
                "trace_id": str(trace_id)
            }
            return {
                "id": uuid4(),
                "conversation_id": uuid4(),  # Different!
                "customer_id": customer_id,
                "payload": json.dumps(payload)
            }
        return None

    fake_conn = FakeConnection(fetchrow_handler=mock_fetchrow)
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
    assert exc_info.value.reason_code == "existing_intake_event_invalid"

# 13.6 Canonical Builder Tests
def test_canonical_builder_preserves_identities():
    intake = PublicChatTurnIntakeOutcome(
        created=True,
        message_id=uuid4(),
        event_id=uuid4(),
        provider_message_id="test-prov-123",
        conversation_sequence_number=17,
        config_version=3,
        message_created_at=datetime.now(timezone.utc),
        processing_status="received",
        security_disposition="prompt_security_flagged",
        trace_id=uuid4()
    )

    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    channel_acc_id = uuid4()

    canonical = build_canonical_public_chat_turn(
        intake=intake,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        customer_id=customer_id,
        public_chat_session_id=session_id,
        channel_account_id=channel_acc_id,
        message_text="redacted prompt security text",
        worker_version="worker-v2",
        max_retries=10
    )

    assert canonical.event_id == intake.event_id
    assert canonical.tenant_id == tenant_id
    assert canonical.conversation_id == conversation_id
    assert canonical.customer_id == customer_id
    assert canonical.channel_session_id == str(session_id)
    assert canonical.channel_account_id == channel_acc_id
    assert canonical.conversation_sequence_number == intake.conversation_sequence_number
    assert canonical.config_version == intake.config_version
    assert canonical.message.message_id == intake.message_id
    assert canonical.message.provider_message_id == intake.provider_message_id
    assert canonical.message.text == "redacted prompt security text"
    assert canonical.message.security_disposition == SecurityDisposition.PROMPT_SECURITY_FLAGGED
    assert canonical.processing.max_retries == 10
    assert canonical.trace.trace_id == intake.trace_id
    assert canonical.whatsapp_account_id is None

    # Preserves on repeat calls
    canonical2 = build_canonical_public_chat_turn(
        intake=intake,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        customer_id=customer_id,
        public_chat_session_id=session_id,
        channel_account_id=channel_acc_id,
        message_text="redacted prompt security text",
        worker_version="worker-v2",
        max_retries=10
    )

    assert canonical2.event_id == canonical.event_id
    assert canonical2.message.message_id == canonical.message.message_id
    assert canonical2.conversation_sequence_number == canonical.conversation_sequence_number
    assert canonical2.config_version == canonical.config_version
    assert canonical2.trace.trace_id == canonical.trace.trace_id
    assert canonical2.message.timestamp == canonical.message.timestamp


# ==================== B2 QUEUE HELPER TESTS ====================
@pytest.mark.anyio
async def test_queue_helper_accepts_only_awa_chat(monkeypatch):
    from backend.pipeline.public_chat_queue import enqueue_public_chat_turn
    from backend.schemas.canonical_message import CanonicalMessage, SourceType, ChannelType, WorkerMetadata, MessagePayload, MessageDirection, MessageType, ProcessingMetadata, TraceMetadata

    # Valid canonical turn
    valid_canonical = CanonicalMessage(
        event_id=uuid4(),
        source_type=SourceType.AWA_CHAT,
        channel_type=ChannelType.AWA_CHAT,
        channel_account_id=uuid4(),
        channel_session_id="session-id",
        tenant_id=uuid4(),
        whatsapp_account_id=None,
        conversation_id=uuid4(),
        conversation_sequence_number=1,
        customer_id=uuid4(),
        config_version=1,
        worker_metadata=WorkerMetadata(worker_version="1.0.0"),
        message=MessagePayload(
            message_id=uuid4(),
            provider_message_id="prov-id",
            direction=MessageDirection.INBOUND,
            timestamp=datetime.now(timezone.utc),
            message_type=MessageType.TEXT,
            text="hello"
        ),
        processing=ProcessingMetadata(first_received_at=datetime.now(timezone.utc)),
        trace=TraceMetadata(trace_id=uuid4())
    )

    mock_redis = AsyncMock()
    await enqueue_public_chat_turn(valid_canonical, redis_client=mock_redis)
    assert mock_redis.lpush.call_count == 1

    # Invalid source_type
    invalid_source = valid_canonical.model_copy(update={"source_type": SourceType.WEBHOOK})
    with pytest.raises(ValueError, match="source_type"):
        await enqueue_public_chat_turn(invalid_source, redis_client=mock_redis)

    # Invalid channel_type
    invalid_channel = valid_canonical.model_copy(update={"channel_type": ChannelType.WHATSAPP})
    with pytest.raises(ValueError, match="channel_type"):
        await enqueue_public_chat_turn(invalid_channel, redis_client=mock_redis)

    # whatsapp_account_id is not None
    invalid_wa = valid_canonical.model_copy(update={"whatsapp_account_id": uuid4()})
    with pytest.raises(ValueError, match="whatsapp_account_id"):
        await enqueue_public_chat_turn(invalid_wa, redis_client=mock_redis)


@pytest.mark.anyio
async def test_queue_helper_redis_close_behavior():
    from backend.pipeline.public_chat_queue import enqueue_public_chat_turn
    from backend.schemas.canonical_message import CanonicalMessage, SourceType, ChannelType, WorkerMetadata, MessagePayload, MessageDirection, MessageType, ProcessingMetadata, TraceMetadata

    valid_canonical = CanonicalMessage(
        event_id=uuid4(),
        source_type=SourceType.AWA_CHAT,
        channel_type=ChannelType.AWA_CHAT,
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        conversation_sequence_number=1,
        customer_id=uuid4(),
        config_version=1,
        worker_metadata=WorkerMetadata(worker_version="1.0.0"),
        message=MessagePayload(
            message_id=uuid4(),
            provider_message_id="prov-id",
            direction=MessageDirection.INBOUND,
            timestamp=datetime.now(timezone.utc),
            message_type=MessageType.TEXT,
            text="hello"
        ),
        processing=ProcessingMetadata(first_received_at=datetime.now(timezone.utc)),
        trace=TraceMetadata(trace_id=uuid4())
    )

    # Supplied Redis client is not closed
    supplied_redis = AsyncMock()
    await enqueue_public_chat_turn(valid_canonical, redis_client=supplied_redis)
    assert supplied_redis.close.call_count == 0
    assert supplied_redis.aclose.call_count == 0

    # Helper-owned Redis client is closed
    import redis.asyncio as redis
    mock_client = AsyncMock()
    mock_from_url = MagicMock(return_value=mock_client)
    redis.from_url = mock_from_url

    await enqueue_public_chat_turn(valid_canonical, redis_client=None)
    assert mock_client.aclose.call_count == 1 or mock_client.close.call_count == 1


@pytest.mark.anyio
async def test_queue_helper_redis_errors_propagate():
    from backend.pipeline.public_chat_queue import enqueue_public_chat_turn
    from backend.schemas.canonical_message import CanonicalMessage, SourceType, ChannelType, WorkerMetadata, MessagePayload, MessageDirection, MessageType, ProcessingMetadata, TraceMetadata

    valid_canonical = CanonicalMessage(
        event_id=uuid4(),
        source_type=SourceType.AWA_CHAT,
        channel_type=ChannelType.AWA_CHAT,
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        conversation_sequence_number=1,
        customer_id=uuid4(),
        config_version=1,
        worker_metadata=WorkerMetadata(worker_version="1.0.0"),
        message=MessagePayload(
            message_id=uuid4(),
            provider_message_id="prov-id",
            direction=MessageDirection.INBOUND,
            timestamp=datetime.now(timezone.utc),
            message_type=MessageType.TEXT,
            text="hello"
        ),
        processing=ProcessingMetadata(first_received_at=datetime.now(timezone.utc)),
        trace=TraceMetadata(trace_id=uuid4())
    )

    mock_redis = AsyncMock()
    mock_redis.lpush.side_effect = RuntimeError("Redis disconnected")
    with pytest.raises(RuntimeError, match="Redis disconnected"):
        await enqueue_public_chat_turn(valid_canonical, redis_client=mock_redis)

    # CancelledError propagates
    mock_redis.lpush.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await enqueue_public_chat_turn(valid_canonical, redis_client=mock_redis)


# ==================== B2 QUEUE SYNCHRONIZATION DATABASE TESTS ====================
@pytest.mark.anyio
async def test_mark_public_chat_turn_queued_transitions():
    tenant_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    event_id = uuid4()

    # Track locked rows
    locked_rows = []

    async def mock_fetchrow(query, *args):
        query_upper = query.upper()
        if "FROM MESSAGES" in query_upper:
            assert "FOR UPDATE" in query_upper
            locked_rows.append("message")
            return {"processing_status": "received"}
        elif "FROM BUSINESS_EVENTS" in query_upper:
            assert "FOR UPDATE" in query_upper
            locked_rows.append("event")
            return {"status": "pending", "processed_at": None}
        return None

    execute_count = 0
    async def mock_execute(query, *args):
        nonlocal execute_count
        execute_count += 1
        return "UPDATE 1"

    fake_conn = FakeConnection(fetchrow_handler=mock_fetchrow, execute_handler=mock_execute)
    client = DatabaseClient(database_url="postgresql://mock")
    client.tenant_context = MagicMock(return_value=FakeConnectionContext(fake_conn))

    res = await client.mark_public_chat_turn_queued(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        message_id=message_id,
        event_id=event_id
    )

    assert res is True
    assert locked_rows == ["message", "event"]
    assert execute_count == 2  # message status -> queued, event status -> processed


@pytest.mark.anyio
async def test_mark_public_chat_turn_queued_preserved_and_skipped():
    tenant_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    event_id = uuid4()

    # Status: queued remains queued, pending event -> processed
    async def mock_fetchrow(query, *args):
        if "FROM MESSAGES" in query.upper():
            return {"processing_status": "queued"}
        elif "FROM BUSINESS_EVENTS" in query.upper():
            return {"status": "processed", "processed_at": datetime.now(timezone.utc)}
        return None

    execute_count = 0
    async def mock_execute(query, *args):
        nonlocal execute_count
        execute_count += 1
        return "UPDATE 1"

    fake_conn = FakeConnection(fetchrow_handler=mock_fetchrow, execute_handler=mock_execute)
    client = DatabaseClient(database_url="postgresql://mock")
    client.tenant_context = MagicMock(return_value=FakeConnectionContext(fake_conn))

    res = await client.mark_public_chat_turn_queued(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        message_id=message_id,
        event_id=event_id
    )

    assert res is True
    assert execute_count == 1  # Only updates event to processed (idempotently)


@pytest.mark.anyio
async def test_mark_public_chat_turn_queued_validation_failures():
    tenant_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    event_id = uuid4()

    client = DatabaseClient(database_url="postgresql://mock")

    # Missing message
    fake_conn = FakeConnection(fetchrow_handler=AsyncMock(side_effect=[None, None]))
    client.tenant_context = MagicMock(return_value=FakeConnectionContext(fake_conn))
    assert await client.mark_public_chat_turn_queued(
        tenant_id=tenant_id, conversation_id=conversation_id, message_id=message_id, event_id=event_id
    ) is False

    # Missing event
    fake_conn = FakeConnection(fetchrow_handler=AsyncMock(side_effect=[{"processing_status": "received"}, None]))
    client.tenant_context = MagicMock(return_value=FakeConnectionContext(fake_conn))
    assert await client.mark_public_chat_turn_queued(
        tenant_id=tenant_id, conversation_id=conversation_id, message_id=message_id, event_id=event_id
    ) is False

    # Invalid message status (e.g. invalid_status)
    fake_conn = FakeConnection(fetchrow_handler=AsyncMock(side_effect=[
        {"processing_status": "invalid_status"},
        {"status": "pending", "processed_at": None}
    ]))
    client.tenant_context = MagicMock(return_value=FakeConnectionContext(fake_conn))
    assert await client.mark_public_chat_turn_queued(
        tenant_id=tenant_id, conversation_id=conversation_id, message_id=message_id, event_id=event_id
    ) is False


# ==================== B2 REPLAY LOADING TESTS ====================
@pytest.mark.anyio
async def test_get_public_chat_turn_for_replay_success():
    from backend.db.client import PublicChatTurnReplayRecord
    tenant_id = uuid4()
    event_id = uuid4()
    msg_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    link_id = uuid4()
    trace_id = uuid4()

    event_payload = {
        "public_chat_session_id": str(session_id),
        "channel_account_id": str(link_id),
        "conversation_sequence_number": 4,
        "config_version": 2,
        "security_disposition": "none",
        "trace_id": str(trace_id)
    }

    async def mock_fetchrow(query, *args):
        query_upper = query.upper()
        if "FROM BUSINESS_EVENTS" in query_upper:
            return {
                "id": event_id,
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "customer_id": customer_id,
                "aggregate_id": msg_id,
                "aggregate_type": "message",
                "event_type": "conversation.turn.received",
                "payload": json.dumps(event_payload),
                "status": "pending",
                "attempts": 0,
                "max_attempts": 3
            }
        elif "FROM MESSAGES" in query_upper:
            return {
                "id": msg_id,
                "conversation_id": conversation_id,
                "whatsapp_message_id": "awa:msg",
                "provider_message_id": "awa:msg",
                "message_text": "Hello world",
                "created_at": datetime.now(timezone.utc),
                "processing_status": "received",
                "direction": "inbound",
                "channel_type": "awa_chat"
            }
        elif "FROM PUBLIC_CHAT_SESSIONS" in query_upper:
            return {
                "id": session_id,
                "tenant_id": tenant_id,
                "link_id": link_id,
                "customer_id": customer_id,
                "conversation_id": conversation_id
            }
        return None

    fake_conn = FakeConnection(fetchrow_handler=mock_fetchrow)
    client = DatabaseClient(database_url="postgresql://mock")
    client.tenant_context = MagicMock(return_value=FakeConnectionContext(fake_conn))

    record = await client.get_public_chat_turn_for_replay(tenant_id=tenant_id, event_id=event_id)
    assert isinstance(record, PublicChatTurnReplayRecord)
    assert record.tenant_id == tenant_id
    assert record.conversation_id == conversation_id
    assert record.customer_id == customer_id
    assert record.message_text == "Hello world"
    assert record.intake.created is False
    assert record.intake.message_id == msg_id
    assert record.intake.event_id == event_id
    assert record.intake.conversation_sequence_number == 4
    assert record.intake.config_version == 2


# ==================== B2 PUBLIC-CHAT API TESTS ====================
@pytest.mark.anyio
async def test_api_send_message_new_queued(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.routes.public_chat import router
    from backend.db.client import PublicChatTurnIntakeOutcome

    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    link_id = uuid4()

    monkeypatch.setattr("backend.api.routes.public_chat._get_active_session", AsyncMock(return_value={
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "customer_id": customer_id,
        "link_id": link_id,
        "visitor_name": "Visitor"
    }))
    monkeypatch.setattr("backend.api.routes.public_chat._require_otp_before_commitment", AsyncMock())
    monkeypatch.setattr("backend.api.routes.public_chat._check_public_rate_limit", AsyncMock())

    intake_outcome = PublicChatTurnIntakeOutcome(
        created=True,
        message_id=uuid4(),
        event_id=uuid4(),
        provider_message_id="prov-id",
        conversation_sequence_number=1,
        config_version=1,
        message_created_at=datetime.now(timezone.utc),
        processing_status="received",
        security_disposition="none",
        trace_id=uuid4()
    )

    mock_intake = AsyncMock(return_value=intake_outcome)
    monkeypatch.setattr(db_client, "create_or_get_public_chat_turn_intake", mock_intake)

    mock_enqueue = AsyncMock()
    monkeypatch.setattr("backend.pipeline.public_chat_queue.enqueue_public_chat_turn", mock_enqueue)

    mock_sync = AsyncMock(return_value=True)
    monkeypatch.setattr(db_client, "mark_public_chat_turn_queued", mock_sync)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.post(
        f"/api/v1/public-chat/sessions/{session_id}/messages",
        json={"session_token": "a" * 24, "message_text": "Hello engine"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["status"] == "queued"
    assert data["data"]["processing_status"] == "queued"
    assert data["data"]["message_id"] == str(intake_outcome.message_id)
    assert data["data"]["event_id"] == str(intake_outcome.event_id)
    assert data["data"]["duplicate"] is False

    mock_intake.assert_called_once()
    mock_enqueue.assert_called_once()
    mock_sync.assert_called_once()


@pytest.mark.anyio
async def test_api_send_message_redis_failure(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.routes.public_chat import router
    from backend.db.client import PublicChatTurnIntakeOutcome

    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    link_id = uuid4()

    monkeypatch.setattr("backend.api.routes.public_chat._get_active_session", AsyncMock(return_value={
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "customer_id": customer_id,
        "link_id": link_id,
        "visitor_name": "Visitor"
    }))
    monkeypatch.setattr("backend.api.routes.public_chat._require_otp_before_commitment", AsyncMock())
    monkeypatch.setattr("backend.api.routes.public_chat._check_public_rate_limit", AsyncMock())

    intake_outcome = PublicChatTurnIntakeOutcome(
        created=True,
        message_id=uuid4(),
        event_id=uuid4(),
        provider_message_id="prov-id",
        conversation_sequence_number=1,
        config_version=1,
        message_created_at=datetime.now(timezone.utc),
        processing_status="received",
        security_disposition="none",
        trace_id=uuid4()
    )

    monkeypatch.setattr(db_client, "create_or_get_public_chat_turn_intake", AsyncMock(return_value=intake_outcome))
    monkeypatch.setattr("backend.pipeline.public_chat_queue.enqueue_public_chat_turn", AsyncMock(side_effect=RuntimeError("Redis down")))

    mock_sync = AsyncMock()
    monkeypatch.setattr(db_client, "mark_public_chat_turn_queued", mock_sync)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.post(
        f"/api/v1/public-chat/sessions/{session_id}/messages",
        json={"session_token": "a" * 24, "message_text": "Hello engine"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["status"] == "received"
    assert data["data"]["processing_status"] == "delayed"
    assert data["data"]["processing_code"] == "worker_degraded"
    assert mock_sync.call_count == 0


@pytest.mark.anyio
async def test_api_send_message_sync_failure(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.routes.public_chat import router
    from backend.db.client import PublicChatTurnIntakeOutcome

    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    link_id = uuid4()

    monkeypatch.setattr("backend.api.routes.public_chat._get_active_session", AsyncMock(return_value={
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "customer_id": customer_id,
        "link_id": link_id,
        "visitor_name": "Visitor"
    }))
    monkeypatch.setattr("backend.api.routes.public_chat._require_otp_before_commitment", AsyncMock())
    monkeypatch.setattr("backend.api.routes.public_chat._check_public_rate_limit", AsyncMock())

    intake_outcome = PublicChatTurnIntakeOutcome(
        created=True,
        message_id=uuid4(),
        event_id=uuid4(),
        provider_message_id="prov-id",
        conversation_sequence_number=1,
        config_version=1,
        message_created_at=datetime.now(timezone.utc),
        processing_status="received",
        security_disposition="none",
        trace_id=uuid4()
    )

    monkeypatch.setattr(db_client, "create_or_get_public_chat_turn_intake", AsyncMock(return_value=intake_outcome))
    monkeypatch.setattr("backend.pipeline.public_chat_queue.enqueue_public_chat_turn", AsyncMock())
    monkeypatch.setattr(db_client, "mark_public_chat_turn_queued", AsyncMock(return_value=False))

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.post(
        f"/api/v1/public-chat/sessions/{session_id}/messages",
        json={"session_token": "a" * 24, "message_text": "Hello engine"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["status"] == "received"
    assert data["data"]["processing_status"] == "delayed"


@pytest.mark.anyio
async def test_api_send_message_duplicate(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.routes.public_chat import router
    from backend.db.client import PublicChatTurnIntakeOutcome

    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    link_id = uuid4()

    monkeypatch.setattr("backend.api.routes.public_chat._get_active_session", AsyncMock(return_value={
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "customer_id": customer_id,
        "link_id": link_id,
        "visitor_name": "Visitor"
    }))
    monkeypatch.setattr("backend.api.routes.public_chat._require_otp_before_commitment", AsyncMock())
    monkeypatch.setattr("backend.api.routes.public_chat._check_public_rate_limit", AsyncMock())

    intake_outcome = PublicChatTurnIntakeOutcome(
        created=False,
        message_id=uuid4(),
        event_id=uuid4(),
        provider_message_id="prov-id",
        conversation_sequence_number=1,
        config_version=1,
        message_created_at=datetime.now(timezone.utc),
        processing_status="queued",
        security_disposition="none",
        trace_id=uuid4()
    )

    monkeypatch.setattr(db_client, "create_or_get_public_chat_turn_intake", AsyncMock(return_value=intake_outcome))

    mock_enqueue = AsyncMock()
    monkeypatch.setattr("backend.pipeline.public_chat_queue.enqueue_public_chat_turn", mock_enqueue)
    mock_sync = AsyncMock()
    monkeypatch.setattr(db_client, "mark_public_chat_turn_queued", mock_sync)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.post(
        f"/api/v1/public-chat/sessions/{session_id}/messages",
        json={"session_token": "a" * 24, "message_text": "Hello engine"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["status"] == "duplicate"
    assert data["data"]["duplicate"] is True
    assert data["data"]["processing_status"] == "queued"

    assert mock_enqueue.call_count == 0
    assert mock_sync.call_count == 0


@pytest.mark.anyio
async def test_api_send_message_intake_failure(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.routes.public_chat import router
    from backend.db.client import PublicChatTurnIntakeError

    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    link_id = uuid4()

    monkeypatch.setattr("backend.api.routes.public_chat._get_active_session", AsyncMock(return_value={
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "customer_id": customer_id,
        "link_id": link_id,
        "visitor_name": "Visitor"
    }))
    monkeypatch.setattr("backend.api.routes.public_chat._require_otp_before_commitment", AsyncMock())
    monkeypatch.setattr("backend.api.routes.public_chat._check_public_rate_limit", AsyncMock())

    monkeypatch.setattr(db_client, "create_or_get_public_chat_turn_intake", AsyncMock(side_effect=PublicChatTurnIntakeError("active_config_missing")))

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.post(
        f"/api/v1/public-chat/sessions/{session_id}/messages",
        json={"session_token": "a" * 24, "message_text": "Hello engine"}
    )
    assert response.status_code == 503
    data = response.json()
    assert "active_config_missing" not in str(data)  # No internal details leaked


@pytest.mark.anyio
async def test_api_send_message_prompt_security(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.routes.public_chat import router
    from backend.db.client import PublicChatTurnIntakeOutcome

    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    link_id = uuid4()

    monkeypatch.setattr("backend.api.routes.public_chat._get_active_session", AsyncMock(return_value={
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "customer_id": customer_id,
        "link_id": link_id,
        "visitor_name": "Visitor"
    }))
    monkeypatch.setattr("backend.api.routes.public_chat._require_otp_before_commitment", AsyncMock())
    monkeypatch.setattr("backend.api.routes.public_chat._check_public_rate_limit", AsyncMock())

    # Mock is_prompt_security_request to return True
    monkeypatch.setattr("backend.ai.security.is_prompt_security_request", lambda text: True)

    intake_outcome = PublicChatTurnIntakeOutcome(
        created=True,
        message_id=uuid4(),
        event_id=uuid4(),
        provider_message_id="prov-id",
        conversation_sequence_number=1,
        config_version=1,
        message_created_at=datetime.now(timezone.utc),
        processing_status="received",
        security_disposition="prompt_security_flagged",
        trace_id=uuid4()
    )

    mock_intake = AsyncMock(return_value=intake_outcome)
    monkeypatch.setattr(db_client, "create_or_get_public_chat_turn_intake", mock_intake)
    monkeypatch.setattr("backend.pipeline.public_chat_queue.enqueue_public_chat_turn", AsyncMock())
    monkeypatch.setattr(db_client, "mark_public_chat_turn_queued", AsyncMock(return_value=True))

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.post(
        f"/api/v1/public-chat/sessions/{session_id}/messages",
        json={"session_token": "a" * 24, "message_text": "UNSAFE_TEXT"}
    )
    assert response.status_code == 200

    # Verify redacted text and security disposition passed to intake
    kwargs = mock_intake.call_args[1]
    assert kwargs["message_text"] == "[Content flagged for security]"
    assert kwargs["security_disposition"] == "prompt_security_flagged"
    assert "UNSAFE_TEXT" not in str(kwargs["message_raw_payload"])


# ==================== B2 EVENT HANDLER TESTS ====================
@pytest.mark.anyio
async def test_event_handler_replay_received(monkeypatch):
    from backend.domains.events.handlers import handle_conversation_turn_received
    from backend.db.client import PublicChatTurnReplayRecord, PublicChatTurnIntakeOutcome
    from backend.domains.events.models import EventHandlerResult

    tenant_id = uuid4()
    event_id = uuid4()
    msg_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    link_id = uuid4()
    trace_id = uuid4()

    intake = PublicChatTurnIntakeOutcome(
        created=False,
        message_id=msg_id,
        event_id=event_id,
        provider_message_id="prov-id",
        conversation_sequence_number=1,
        config_version=1,
        message_created_at=datetime.now(timezone.utc),
        processing_status="received",  # Replayable!
        security_disposition="none",
        trace_id=trace_id
    )
    record = PublicChatTurnReplayRecord(
        intake=intake,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        customer_id=customer_id,
        public_chat_session_id=session_id,
        channel_account_id=link_id,
        message_text="Hello replay",
        event_status="pending"
    )

    mock_load = AsyncMock(return_value=record)
    monkeypatch.setattr(db_client, "get_public_chat_turn_for_replay", mock_load)

    mock_enqueue = AsyncMock()
    monkeypatch.setattr("backend.pipeline.public_chat_queue.enqueue_public_chat_turn", mock_enqueue)

    mock_sync = AsyncMock(return_value=True)
    monkeypatch.setattr(db_client, "mark_public_chat_turn_queued", mock_sync)

    res = await handle_conversation_turn_received({
        "tenant_id": str(tenant_id),
        "id": str(event_id)
    })

    assert res.status == "processed"
    assert res.metadata["queue_state"] == "replayed"
    mock_load.assert_called_once_with(tenant_id=tenant_id, event_id=event_id)
    mock_enqueue.assert_called_once()
    mock_sync.assert_called_once()


@pytest.mark.anyio
async def test_event_handler_already_queued(monkeypatch):
    from backend.domains.events.handlers import handle_conversation_turn_received
    from backend.db.client import PublicChatTurnReplayRecord, PublicChatTurnIntakeOutcome
    from backend.domains.events.models import EventHandlerResult

    tenant_id = uuid4()
    event_id = uuid4()
    msg_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    link_id = uuid4()
    trace_id = uuid4()

    intake = PublicChatTurnIntakeOutcome(
        created=False,
        message_id=msg_id,
        event_id=event_id,
        provider_message_id="prov-id",
        conversation_sequence_number=1,
        config_version=1,
        message_created_at=datetime.now(timezone.utc),
        processing_status="queued",  # Already queued!
        security_disposition="none",
        trace_id=trace_id
    )
    record = PublicChatTurnReplayRecord(
        intake=intake,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        customer_id=customer_id,
        public_chat_session_id=session_id,
        channel_account_id=link_id,
        message_text="Hello replay",
        event_status="processed"
    )

    monkeypatch.setattr(db_client, "get_public_chat_turn_for_replay", AsyncMock(return_value=record))

    mock_enqueue = AsyncMock()
    monkeypatch.setattr("backend.pipeline.public_chat_queue.enqueue_public_chat_turn", mock_enqueue)

    mock_sync = AsyncMock(return_value=True)
    monkeypatch.setattr(db_client, "mark_public_chat_turn_queued", mock_sync)

    res = await handle_conversation_turn_received({
        "tenant_id": str(tenant_id),
        "id": str(event_id)
    })

    assert res.status == "processed"
    assert res.metadata["queue_state"] == "already_published"
    assert mock_enqueue.call_count == 0
    mock_sync.assert_called_once()


@pytest.mark.anyio
async def test_event_handler_sync_error(monkeypatch):
    from backend.domains.events.handlers import handle_conversation_turn_received
    from backend.db.client import PublicChatTurnReplayRecord, PublicChatTurnIntakeOutcome, PublicChatTurnReplayError

    tenant_id = uuid4()
    event_id = uuid4()
    msg_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    session_id = uuid4()
    link_id = uuid4()
    trace_id = uuid4()

    intake = PublicChatTurnIntakeOutcome(
        created=False,
        message_id=msg_id,
        event_id=event_id,
        provider_message_id="prov-id",
        conversation_sequence_number=1,
        config_version=1,
        message_created_at=datetime.now(timezone.utc),
        processing_status="received",
        security_disposition="none",
        trace_id=trace_id
    )
    record = PublicChatTurnReplayRecord(
        intake=intake,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        customer_id=customer_id,
        public_chat_session_id=session_id,
        channel_account_id=link_id,
        message_text="Hello replay",
        event_status="pending"
    )

    monkeypatch.setattr(db_client, "get_public_chat_turn_for_replay", AsyncMock(return_value=record))
    monkeypatch.setattr("backend.pipeline.public_chat_queue.enqueue_public_chat_turn", AsyncMock())
    monkeypatch.setattr(db_client, "mark_public_chat_turn_queued", AsyncMock(return_value=False))

    with pytest.raises(PublicChatTurnReplayError) as exc_info:
        await handle_conversation_turn_received({
            "tenant_id": str(tenant_id),
            "id": str(event_id)
        })
    assert exc_info.value.reason_code == "queue_commit_invalid"


# ==================== B2 SECURITY ENGINE TESTS ====================
@pytest.mark.anyio
async def test_engine_consumes_security_disposition_flagged(monkeypatch):
    from backend.logic.engine.core import AgenticEngine, TurnInterpretationMode
    from backend.core.context import ExecutionContext
    from backend.logic.engine.core import ConversationState, SECURITY_REFUSAL_MESSAGE
    from backend.pipeline.message_router import RoutingDecision
    from backend.core.runtime_capabilities import RuntimeCapabilitySnapshot

    context = MagicMock(spec=ExecutionContext)
    context.incoming_message = {
        "text": "regular text",
        "security_disposition": "prompt_security_flagged"
    }
    context.tenant = MagicMock()
    context.tenant.id = uuid4()
    context.tenant.name = "Test Tenant"
    context.config = MagicMock()
    context.config.config_data = {}
    context.session_data = MagicMock()
    context.session_data.state.workflow_state = ConversationState.NEW
    context.session_data.current_module = ""
    context.session_data.state.action_ledger.current_task = ""
    context.session_data.state.action_ledger.pending_action = None

    engine = AgenticEngine()
    engine.tool_registry = MagicMock()

    # Stub direct response check and semantic prefill
    monkeypatch.setattr("backend.logic.engine.core._direct_response_for_route", lambda *a: None)
    monkeypatch.setattr("backend.logic.engine.core._has_pending_order_disambiguation", lambda *a: False)

    monkeypatch.setattr("backend.billing.usage_guard.usage_guard.check_message_allowed", AsyncMock())

    mock_caps = MagicMock(spec=RuntimeCapabilitySnapshot)
    mock_caps.runtime_available_modules = set()
    mock_caps.is_available.return_value = False
    build_caps = AsyncMock(return_value=mock_caps)
    monkeypatch.setattr("backend.logic.engine.core.build_runtime_capability_snapshot", build_caps)
    monkeypatch.setattr("backend.logic.engine.core.resolve_runtime_behavior_profile", AsyncMock(return_value=None))

    routing_decision = RoutingDecision("chat", 1.0, "workflow", False, False, "test_evidence")
    route = MagicMock(return_value=routing_decision)
    monkeypatch.setattr("backend.pipeline.message_router.message_router.route_message", route)

    res = await engine._execute_step_inner(context=context)

    assert res.response_text == SECURITY_REFUSAL_MESSAGE
    assert res.next_state == ConversationState.NEW
    build_caps.assert_not_awaited()
    route.assert_not_called()
    engine.tool_registry.execute_tool.assert_not_called()
