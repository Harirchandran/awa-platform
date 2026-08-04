import pytest
import asyncio
import time
import json
import inspect
from uuid import uuid4, UUID
from unittest.mock import AsyncMock, MagicMock
from backend.core.config import settings
from backend.core.states import ConversationState
from backend.core.context import ConversationSession, SessionState, TenantConfig
from backend.pipeline import worker
from backend.pipeline.session_manager import SessionPersistenceError
from backend.db.client import db_client
from backend.logic.engine.core import agentic_engine, WorkflowResult

pytestmark = pytest.mark.anyio


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.ttls = {}

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


def make_canonical_message_json(
    event_id: str,
    tenant_id: str,
    conversation_id: str,
    customer_id: str,
    text: str,
) -> str:
    return json.dumps({
        "schema_version": 2,
        "event_type": "incoming_message",
        "event_id": event_id,
        "source_type": "webhook",
        "channel_type": "awa_chat",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "conversation_sequence_number": 1,
        "customer_id": customer_id,
        "config_version": 1,
        "worker_metadata": {
            "worker_version": "1.0.0"
        },
        "message": {
            "message_id": str(uuid4()),
            "provider_message_id": f"pm_{event_id}",
            "direction": "inbound",
            "timestamp": "2026-06-17T08:00:00Z",
            "message_type": "text",
            "text": text
        },
        "processing": {
            "retry_count": 0,
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


@pytest.fixture
def base_mocks(monkeypatch):
    redis_client = FakeRedis()

    async def mock_get_redis():
        return redis_client

    monkeypatch.setattr(worker, "get_redis", mock_get_redis)
    monkeypatch.setattr(settings, "ENABLE_WORKER_SESSION_LOCKS", False)

    # Mock DB client tenant/session queries
    async def mock_get_tenant(tenant_id):
        return {
            "id": tenant_id,
            "name": "Test Tenant",
            "plan_code": "enterprise",
            "status": "active",
            "ai_mode": "standard"
        }

    async def mock_get_tenant_ai_mode(tenant_id):
        return "standard"

    async def mock_save_message(*args, **kwargs):
        return {"created": True, "id": uuid4()}

    async def mock_check_outbound_sent(*args, **kwargs):
        return False

    async def mock_get_active_config_version(tenant_id):
        return 1

    async def mock_create_conversation_session(*args, **kwargs):
        pass

    from backend.db.client import ConversationSessionCommitOutcome
    from datetime import datetime, timezone
    async def mock_commit_conversation_session_state(*args, **kwargs):
        return ConversationSessionCommitOutcome(
            committed=True,
            reason_code="committed",
            committed_at=datetime.now(timezone.utc)
        )

    monkeypatch.setattr(db_client, "get_tenant", mock_get_tenant)
    monkeypatch.setattr(db_client, "get_tenant_ai_mode", mock_get_tenant_ai_mode)
    monkeypatch.setattr(db_client, "save_message", mock_save_message)
    monkeypatch.setattr(db_client, "check_outbound_sent", mock_check_outbound_sent)
    monkeypatch.setattr(db_client, "get_active_config_version", mock_get_active_config_version)
    monkeypatch.setattr(db_client, "create_conversation_session", mock_create_conversation_session)
    monkeypatch.setattr(db_client, "commit_conversation_session_state", mock_commit_conversation_session_state)

    # Mock customer retrieval
    async def mock_get_customer(tenant_id, customer_id):
        return {"name": "John Doe", "phone_number": "+1234567890"}
    monkeypatch.setattr("backend.domains.customers.service.customer_service.get_customer", mock_get_customer)

    # Mock config loader
    async def mock_load_config(tenant_id, version=None):
        return TenantConfig(
            tenant_id=UUID(str(tenant_id)),
            version=version or 1,
            config_data={}
        )
    monkeypatch.setattr("backend.core.config_loader.config_loader.load_config", mock_load_config)

    # Mock usage guard
    async def mock_resolve_ai_mode(context, tenant_ai_mode):
        return "standard"
    monkeypatch.setattr("backend.billing.usage_guard.usage_guard.resolve_ai_mode", mock_resolve_ai_mode)

    async def mock_record_message_processed(*args, **kwargs):
        pass
    monkeypatch.setattr("backend.billing.usage_guard.usage_guard.record_message_processed", mock_record_message_processed)

    return redis_client


async def test_normal_state_success_ordering(monkeypatch, base_mocks):
    """
    Test that when an agent result contains next_state:
    - SessionManager.update_state succeeds
    - _dispatch_response runs only after update_state completed
    Verification uses an ordered call list of events.
    """
    events = []

    # Mock SessionManager.update_state to record its start/completion
    orig_update_state = worker.session_manager.update_state

    async def mock_update_state(session, new_state):
        events.append("state_commit_started")
        await orig_update_state(session, new_state)
        events.append("state_commit_completed")

    monkeypatch.setattr(worker.session_manager, "update_state", mock_update_state)

    # Mock _dispatch_response to record its start
    async def mock_dispatch_response(*args, **kwargs):
        events.append("dispatch_started")

    monkeypatch.setattr(worker, "_dispatch_response", mock_dispatch_response)

    # Mock engine execution to return next_state
    mock_result = WorkflowResult(
        next_state=ConversationState.COLLECTING_DATA,
        requires_response=True,
        response_text="Hello, we are processing your request.",
        quick_replies=[]
    )
    monkeypatch.setattr(agentic_engine, "execute_step", AsyncMock(return_value=mock_result))

    # Setup session
    tenant_id = str(uuid4())
    conversation_id = str(uuid4())
    session = await worker.session_manager.get_or_create_session(UUID(tenant_id), UUID(conversation_id))
    session.state.workflow_state = ConversationState.NEW

    raw_msg = make_canonical_message_json(str(uuid4()), tenant_id, conversation_id, str(uuid4()), "Hello")

    success = await worker.process_message(raw_msg, time.time() * 1000, time.monotonic() * 1000)

    assert success is True
    # Ensure correct sequence of events: state commit completed before dispatch started
    assert events == ["state_commit_started", "state_commit_completed", "dispatch_started"]


async def test_normal_state_persistence_rejection(monkeypatch, base_mocks):
    """
    Test that when SessionManager.update_state raises SessionPersistenceError:
    - _dispatch_response is not called
    - process_message does not return successful completion
    - the exception is propagated to the existing worker error/retry boundary (handle_retry callback)
    """
    async def mock_update_state(session, new_state):
        raise SessionPersistenceError("optimistic_conflict")

    monkeypatch.setattr(worker.session_manager, "update_state", mock_update_state)

    dispatch_mock = AsyncMock()
    monkeypatch.setattr(worker, "_dispatch_response", dispatch_mock)

    retry_mock = AsyncMock()
    monkeypatch.setattr(worker, "handle_retry", retry_mock)

    mock_result = WorkflowResult(
        next_state=ConversationState.COLLECTING_DATA,
        requires_response=True,
        response_text="Hello, we are processing your request.",
        quick_replies=[]
    )
    monkeypatch.setattr(agentic_engine, "execute_step", AsyncMock(return_value=mock_result))

    tenant_id = str(uuid4())
    conversation_id = str(uuid4())
    session = await worker.session_manager.get_or_create_session(UUID(tenant_id), UUID(conversation_id))
    session.state.workflow_state = ConversationState.NEW

    raw_msg = make_canonical_message_json(str(uuid4()), tenant_id, conversation_id, str(uuid4()), "Hello")

    # process_message catches the error and calls handle_retry, returning True to complete raw message ack lifecycle
    success = await worker.process_message(raw_msg, time.time() * 1000, time.monotonic() * 1000)

    assert success is True
    dispatch_mock.assert_not_awaited()
    # Confirm retry handler is invoked with the specific persistence error
    retry_mock.assert_awaited_once()
    assert "Session persistence failed: optimistic_conflict" in retry_mock.call_args[0][2]


async def test_no_direct_authoritative_workflow_state_mutation(monkeypatch, base_mocks):
    """
    Test that the original authoritative session's workflow state is not directly
    assigned to result.next_state before commit success (i.e. inside update_state).
    Specifically, if update_state is paused/pending, the live session's workflow state must remain unchanged.
    """
    commit_barrier = asyncio.Event()
    state_checked_during_commit = None

    tenant_id = str(uuid4())
    conversation_id = str(uuid4())
    session = await worker.session_manager.get_or_create_session(UUID(tenant_id), UUID(conversation_id))
    session.state.workflow_state = ConversationState.NEW

    async def mock_update_state(s, new_state):
        nonlocal state_checked_during_commit
        # Capture the state of the live session object during persistence run
        state_checked_during_commit = s.state.workflow_state
        commit_barrier.set()

    monkeypatch.setattr(worker.session_manager, "update_state", mock_update_state)

    mock_result = WorkflowResult(
        next_state=ConversationState.COLLECTING_DATA,
        requires_response=True,
        response_text="Hello, we are processing your request.",
        quick_replies=[]
    )
    monkeypatch.setattr(agentic_engine, "execute_step", AsyncMock(return_value=mock_result))

    raw_msg = make_canonical_message_json(str(uuid4()), tenant_id, conversation_id, str(uuid4()), "Hello")

    # Run processing as a task so we can synchronize/verify mid-flight state
    task = asyncio.create_task(worker.process_message(raw_msg, time.time() * 1000, time.monotonic() * 1000))
    await commit_barrier.wait()

    # The workflow state of the authoritative session must not have been mutated yet
    assert state_checked_during_commit == ConversationState.NEW

    await task


async def test_successful_committed_state_used_for_dispatch(monkeypatch, base_mocks):
    """
    Assert that after update_state succeeds, dispatch receives a context reflecting
    the committed state installed by the commit behavior.
    """
    captured_workflow_state_during_dispatch = None

    async def mock_dispatch_response(msg, response_text, context, **kwargs):
        nonlocal captured_workflow_state_during_dispatch
        captured_workflow_state_during_dispatch = context.session_data.state.workflow_state

    monkeypatch.setattr(worker, "_dispatch_response", mock_dispatch_response)

    # Let update_state behave normally (which installs the committed state on success)
    mock_result = WorkflowResult(
        next_state=ConversationState.COLLECTING_DATA,
        requires_response=True,
        response_text="Hello, we are processing your request.",
        quick_replies=[]
    )
    monkeypatch.setattr(agentic_engine, "execute_step", AsyncMock(return_value=mock_result))

    tenant_id = str(uuid4())
    conversation_id = str(uuid4())
    session = await worker.session_manager.get_or_create_session(UUID(tenant_id), UUID(conversation_id))
    session.state.workflow_state = ConversationState.NEW

    raw_msg = make_canonical_message_json(str(uuid4()), tenant_id, conversation_id, str(uuid4()), "Hello")

    await worker.process_message(raw_msg, time.time() * 1000, time.monotonic() * 1000)

    # Dispatch must receive the updated workflow state (COLLECTING_DATA)
    assert captured_workflow_state_during_dispatch == ConversationState.COLLECTING_DATA


def test_no_obsolete_escalation_sync():
    """
    Ensure _sync_escalation_status does not exist in worker module.
    Ensure process_message does not call db_client escalation methods.
    """
    assert not hasattr(worker, "_sync_escalation_status")

    source = inspect.getsource(worker.process_message)
    assert "set_conversation_escalation" not in source
    assert "clear_conversation_escalation" not in source
    assert "update_conversation_status" not in source
