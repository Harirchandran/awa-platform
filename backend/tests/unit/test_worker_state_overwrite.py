import pytest
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
from backend.core.context import ConversationSession, SessionState
from backend.core.states import ConversationState
from backend.pipeline.session_manager import SessionManager

class FakeRedis:
    def __init__(self):
        self.data = {}
    async def get(self, key):
        return self.data.get(key)
    async def set(self, key, val, **kwargs):
        self.data[key] = val
        return True
    async def delete(self, key):
        self.data.pop(key, None)
        return True
    async def aclose(self):
        pass

@pytest.mark.anyio
async def test_session_manager_prevents_worker_overwrite_escalated_state(monkeypatch):
    tenant_id = uuid4()
    conversation_id = uuid4()
    session_id = uuid4()

    mock_db = AsyncMock()
    # Database currently has the session in escalated state
    mock_db.get_conversation_session = AsyncMock(return_value={
        "id": session_id,
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "state_json": {
            "config_version": 1,
            "state": {
                "workflow_state": "escalated",
                "last_updated_at": datetime.now(timezone.utc).isoformat(),
            }
        }
    })
    mock_db.update_conversation_session = AsyncMock(return_value=True)

    fake_redis = FakeRedis()
    monkeypatch.setattr("redis.asyncio.from_url", lambda *args, **kwargs: fake_redis)

    manager = SessionManager("redis://localhost")
    manager.db = mock_db

    session = ConversationSession(
        id=session_id,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        config_version=1,
        state=SessionState(workflow_state=ConversationState.ESCALATED)
    )

    # Worker tries to update state to COLLECTING_DATA
    new_state = SessionState(workflow_state=ConversationState.COLLECTING_DATA)
    from backend.pipeline.session_manager import SessionPersistenceError
    from backend.db.client import ConversationSessionCommitOutcome

    mock_db.commit_conversation_session_state = AsyncMock(return_value=ConversationSessionCommitOutcome(
        committed=False, reason_code="escalation_guard", committed_at=None
    ))

    with pytest.raises(SessionPersistenceError, match="escalation_guard"):
        await manager.update_state(session, new_state)

    # The session state should remain ESCALATED
    assert session.state.workflow_state == ConversationState.ESCALATED


@pytest.mark.anyio
async def test_create_conversation_session_uses_tenant_scoped_context():
    from backend.db.client import db_client
    tenant_id = uuid4()
    session_id = uuid4()
    conversation_id = uuid4()

    with patch.object(db_client, "tenant_context") as mock_tenant_context:
        mock_conn = AsyncMock()
        mock_tenant_context.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow = AsyncMock(return_value={
            "id": session_id,
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "current_module": None,
            "current_intent": None,
            "state_json": '{"foo": "bar"}',
            "updated_at": datetime.now(timezone.utc)
        })

        await db_client.create_conversation_session(
            session_id=session_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            state_json={"config_version": 1, "foo": "bar"},
            config_version=1,
        )

        mock_tenant_context.assert_called_once_with(tenant_id)


@pytest.mark.anyio
async def test_commit_conversation_session_state_success():
    from backend.db.client import db_client
    from unittest.mock import MagicMock
    tenant_id = uuid4()
    conversation_id = uuid4()

    with patch.object(db_client, "tenant_context") as mock_tenant_context:
        mock_conn = AsyncMock()
        mock_conn.transaction = MagicMock()
        mock_transaction = AsyncMock()
        mock_conn.transaction.return_value = mock_transaction
        mock_tenant_context.return_value.__aenter__.return_value = mock_conn

        mock_conn.fetchrow = AsyncMock(return_value={
            "tenant_id": tenant_id,
            "state_json": '{"state": {"workflow_state": "NEW", "last_updated_at": "2026-07-15T00:00:00+00:00"}}',
            "current_module": None,
            "current_intent": None
        })

        expected_ts = datetime.fromisoformat("2026-07-15T00:00:00+00:00")

        outcome = await db_client.commit_conversation_session_state(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            state_json={"config_version": 1, "state": {"workflow_state": "COLLECTING_DATA"}},
            config_version=1,
            current_module="booking",
            current_intent="book_appt",
            expected_last_updated_at=expected_ts,
            candidate_workflow_state="COLLECTING_DATA"
        )

        assert outcome.committed is True
        assert outcome.reason_code == "committed"
        assert outcome.committed_at is not None

        mock_conn.transaction.assert_called_once()
        select_query = mock_conn.fetchrow.call_args_list[0][0][0]
        assert "FOR UPDATE" in select_query

        update_query = mock_conn.execute.call_args_list[0][0][0]
        assert "UPDATE conversation_sessions" in update_query


@pytest.mark.anyio
async def test_commit_conversation_session_state_missing_session():
    from backend.db.client import db_client
    from unittest.mock import MagicMock
    tenant_id = uuid4()
    conversation_id = uuid4()

    with patch.object(db_client, "tenant_context") as mock_tenant_context:
        mock_conn = AsyncMock()
        mock_conn.transaction = MagicMock()
        mock_transaction = AsyncMock()
        mock_conn.transaction.return_value = mock_transaction
        mock_tenant_context.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow = AsyncMock(return_value=None)

        outcome = await db_client.commit_conversation_session_state(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            state_json={"config_version": 1},
            config_version=1,
            current_module=None,
            current_intent=None,
            expected_last_updated_at=None,
            candidate_workflow_state="NEW"
        )

        assert outcome.committed is False
        assert outcome.reason_code == "session_missing"


@pytest.mark.anyio
async def test_commit_conversation_session_state_tenant_mismatch():
    from backend.db.client import db_client
    from unittest.mock import MagicMock
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    conversation_id = uuid4()

    with patch.object(db_client, "tenant_context") as mock_tenant_context:
        mock_conn = AsyncMock()
        mock_conn.transaction = MagicMock()
        mock_transaction = AsyncMock()
        mock_conn.transaction.return_value = mock_transaction
        mock_tenant_context.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow = AsyncMock(return_value={
            "tenant_id": other_tenant_id,
            "state_json": '{}',
            "current_module": None,
            "current_intent": None
        })

        outcome = await db_client.commit_conversation_session_state(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            state_json={"config_version": 1},
            config_version=1,
            current_module=None,
            current_intent=None,
            expected_last_updated_at=None,
            candidate_workflow_state="NEW"
        )

        assert outcome.committed is False
        assert outcome.reason_code == "tenant_mismatch"


@pytest.mark.anyio
async def test_commit_conversation_session_state_optimistic_conflict():
    from backend.db.client import db_client
    from unittest.mock import MagicMock
    tenant_id = uuid4()
    conversation_id = uuid4()

    with patch.object(db_client, "tenant_context") as mock_tenant_context:
        mock_conn = AsyncMock()
        mock_conn.transaction = MagicMock()
        mock_transaction = AsyncMock()
        mock_conn.transaction.return_value = mock_transaction
        mock_tenant_context.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow = AsyncMock(return_value={
            "tenant_id": tenant_id,
            "state_json": '{"state": {"workflow_state": "NEW", "last_updated_at": "2026-07-15T12:00:00+00:00"}}',
            "current_module": None,
            "current_intent": None
        })

        expected_ts = datetime.fromisoformat("2026-07-15T10:00:00+00:00")

        outcome = await db_client.commit_conversation_session_state(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            state_json={"config_version": 1},
            config_version=1,
            current_module=None,
            current_intent=None,
            expected_last_updated_at=expected_ts,
            candidate_workflow_state="NEW"
        )

        assert outcome.committed is False
        assert outcome.reason_code == "optimistic_conflict"
        mock_conn.execute.assert_not_called()


@pytest.mark.anyio
async def test_commit_conversation_session_state_escalation_guard():
    from backend.db.client import db_client
    from unittest.mock import MagicMock
    tenant_id = uuid4()
    conversation_id = uuid4()

    with patch.object(db_client, "tenant_context") as mock_tenant_context:
        mock_conn = AsyncMock()
        mock_conn.transaction = MagicMock()
        mock_transaction = AsyncMock()
        mock_conn.transaction.return_value = mock_transaction
        mock_tenant_context.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow = AsyncMock(return_value={
            "tenant_id": tenant_id,
            "state_json": '{"state": {"workflow_state": "ESCALATED", "last_updated_at": "2026-07-15T00:00:00+00:00"}}',
            "current_module": None,
            "current_intent": None
        })

        outcome = await db_client.commit_conversation_session_state(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            state_json={"config_version": 1},
            config_version=1,
            current_module=None,
            current_intent=None,
            expected_last_updated_at=None,
            candidate_workflow_state="COLLECTING_DATA"
        )

        assert outcome.committed is False
        assert outcome.reason_code == "escalation_guard"
        mock_conn.execute.assert_not_called()


@pytest.mark.anyio
async def test_commit_conversation_session_state_transition_into_escalation():
    from backend.db.client import db_client
    from unittest.mock import MagicMock
    tenant_id = uuid4()
    conversation_id = uuid4()

    with patch.object(db_client, "tenant_context") as mock_tenant_context:
        mock_conn = AsyncMock()
        mock_conn.transaction = MagicMock()
        mock_transaction = AsyncMock()
        mock_conn.transaction.return_value = mock_transaction
        mock_tenant_context.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow = AsyncMock(return_value={
            "tenant_id": tenant_id,
            "state_json": '{"state": {"workflow_state": "NEW", "last_updated_at": "2026-07-15T00:00:00+00:00"}}',
            "current_module": None,
            "current_intent": None
        })

        outcome = await db_client.commit_conversation_session_state(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            state_json={"config_version": 1, "state": {"workflow_state": "ESCALATED"}},
            config_version=1,
            current_module=None,
            current_intent=None,
            expected_last_updated_at=None,
            candidate_workflow_state="ESCALATED",
            escalation_reason="Test escalation reason"
        )

        assert outcome.committed is True

        exec_calls = [args[0] for args, kwargs in mock_conn.execute.call_args_list]
        assert any("UPDATE conversation_sessions" in q for q in exec_calls)
        assert any("UPDATE conversations" in q for q in exec_calls)


@pytest.mark.anyio
async def test_commit_conversation_session_state_rollback_on_conversation_update_failure():
    from backend.db.client import db_client
    from unittest.mock import MagicMock
    tenant_id = uuid4()
    conversation_id = uuid4()

    with patch.object(db_client, "tenant_context") as mock_tenant_context:
        mock_conn = AsyncMock()
        mock_conn.transaction = MagicMock()
        mock_transaction = AsyncMock()
        mock_conn.transaction.return_value = mock_transaction
        mock_tenant_context.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow = AsyncMock(return_value={
            "tenant_id": tenant_id,
            "state_json": '{"state": {"workflow_state": "NEW", "last_updated_at": "2026-07-15T00:00:00+00:00"}}',
            "current_module": None,
            "current_intent": None
        })

        def mock_execute(query, *args):
            if "UPDATE conversations" in query:
                raise ValueError("Database crash during conversation status update")
            return "UPDATE 1"
        mock_conn.execute = AsyncMock(side_effect=mock_execute)

        with pytest.raises(ValueError, match="Database crash during conversation status update"):
            await db_client.commit_conversation_session_state(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                state_json={"config_version": 1, "state": {"workflow_state": "ESCALATED"}},
                config_version=1,
                current_module=None,
                current_intent=None,
                expected_last_updated_at=None,
                candidate_workflow_state="ESCALATED"
            )


@pytest.mark.anyio
async def test_commit_conversation_session_state_transition_out_of_escalation_rejected():
    from backend.db.client import db_client
    from unittest.mock import MagicMock
    tenant_id = uuid4()
    conversation_id = uuid4()

    with patch.object(db_client, "tenant_context") as mock_tenant_context:
        mock_conn = AsyncMock()
        mock_conn.transaction = MagicMock()
        mock_transaction = AsyncMock()
        mock_conn.transaction.return_value = mock_transaction
        mock_tenant_context.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow = AsyncMock(return_value={
            "tenant_id": tenant_id,
            "state_json": '{"state": {"workflow_state": "ESCALATED", "last_updated_at": "2026-07-15T00:00:00+00:00"}}',
            "current_module": None,
            "current_intent": None
        })

        outcome = await db_client.commit_conversation_session_state(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            state_json={"config_version": 1, "state": {"workflow_state": "NEW"}},
            config_version=1,
            current_module=None,
            current_intent=None,
            expected_last_updated_at=None,
            candidate_workflow_state="NEW"
        )

        assert outcome.committed is False
        assert outcome.reason_code == "escalation_guard"


@pytest.mark.anyio
async def test_update_state_does_not_mutate_session_before_db_commit():
    from backend.pipeline.session_manager import SessionManager
    from backend.db.client import ConversationSessionCommitOutcome

    mock_db = AsyncMock()
    captured_state = None

    session = ConversationSession(
        id=uuid4(),
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        config_version=1,
        state=SessionState(workflow_state=ConversationState.NEW)
    )

    async def mock_commit(*args, **kwargs):
        nonlocal captured_state
        captured_state = session.state.workflow_state
        return ConversationSessionCommitOutcome(committed=True, reason_code="committed", committed_at=datetime.now(timezone.utc))

    mock_db.commit_conversation_session_state = mock_commit

    manager = SessionManager("redis://unused")
    manager.db = mock_db
    manager._cache_session = AsyncMock()

    new_state = SessionState(workflow_state=ConversationState.COLLECTING_DATA)
    await manager.update_state(session, new_state)

    assert captured_state == ConversationState.NEW
    assert session.state.workflow_state == ConversationState.COLLECTING_DATA


@pytest.mark.anyio
async def test_update_state_success_installs_deep_copy_and_timestamp():
    from backend.pipeline.session_manager import SessionManager
    from backend.db.client import ConversationSessionCommitOutcome

    mock_db = AsyncMock()
    committed_time = datetime.now(timezone.utc)
    mock_db.commit_conversation_session_state = AsyncMock(return_value=ConversationSessionCommitOutcome(
        committed=True, reason_code="committed", committed_at=committed_time
    ))

    manager = SessionManager("redis://unused")
    manager.db = mock_db
    manager._cache_session = AsyncMock()

    session = ConversationSession(
        id=uuid4(),
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        config_version=1,
        state=SessionState(workflow_state=ConversationState.NEW)
    )

    new_state = SessionState(workflow_state=ConversationState.COLLECTING_DATA)
    await manager.update_state(session, new_state)

    assert session.state.workflow_state == ConversationState.COLLECTING_DATA
    assert session.state.last_updated_at == committed_time
    assert session.state is not new_state


@pytest.mark.anyio
async def test_update_state_rejected_outcome_raises_and_preserves():
    from backend.pipeline.session_manager import SessionManager, SessionPersistenceError
    from backend.db.client import ConversationSessionCommitOutcome

    mock_db = AsyncMock()
    mock_db.commit_conversation_session_state = AsyncMock(return_value=ConversationSessionCommitOutcome(
        committed=False, reason_code="optimistic_conflict", committed_at=None
    ))

    manager = SessionManager("redis://unused")
    manager.db = mock_db
    manager.invalidate_cache = AsyncMock()

    session = ConversationSession(
        id=uuid4(),
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        config_version=1,
        current_module="booking",
        current_intent="book_appt",
        state=SessionState(workflow_state=ConversationState.NEW)
    )

    new_state = SessionState(workflow_state=ConversationState.COLLECTING_DATA)

    with pytest.raises(SessionPersistenceError, match="optimistic_conflict") as exc_info:
        await manager.update_state(session, new_state)

    assert exc_info.value.reason_code == "optimistic_conflict"
    assert session.state.workflow_state == ConversationState.NEW
    assert session.current_module == "booking"
    assert session.current_intent == "book_appt"
    manager.invalidate_cache.assert_called_once_with(session.tenant_id, session.conversation_id)


@pytest.mark.anyio
async def test_update_state_db_exception_raises_session_write_failed():
    from backend.pipeline.session_manager import SessionManager, SessionPersistenceError

    mock_db = AsyncMock()
    mock_db.commit_conversation_session_state.side_effect = ConnectionError("Postgres offline")

    manager = SessionManager("redis://unused")
    manager.db = mock_db
    manager.invalidate_cache = AsyncMock()

    session = ConversationSession(
        id=uuid4(),
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        config_version=1,
        state=SessionState(workflow_state=ConversationState.NEW)
    )

    new_state = SessionState(workflow_state=ConversationState.COLLECTING_DATA)

    with pytest.raises(SessionPersistenceError, match="session_write_failed") as exc_info:
        await manager.update_state(session, new_state)

    assert exc_info.value.reason_code == "session_write_failed"
    assert session.state.workflow_state == ConversationState.NEW
    manager.invalidate_cache.assert_called_once()


@pytest.mark.anyio
async def test_update_state_cache_write_failure_does_not_raise():
    from backend.pipeline.session_manager import SessionManager
    from backend.db.client import ConversationSessionCommitOutcome

    mock_db = AsyncMock()
    committed_time = datetime.now(timezone.utc)
    mock_db.commit_conversation_session_state = AsyncMock(return_value=ConversationSessionCommitOutcome(
        committed=True, reason_code="committed", committed_at=committed_time
    ))

    manager = SessionManager("redis://unused")
    manager.db = mock_db
    manager._cache_session = AsyncMock(side_effect=ConnectionError("Redis offline"))
    manager.invalidate_cache = AsyncMock()

    session = ConversationSession(
        id=uuid4(),
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        config_version=1,
        state=SessionState(workflow_state=ConversationState.NEW)
    )

    new_state = SessionState(workflow_state=ConversationState.COLLECTING_DATA)
    await manager.update_state(session, new_state)

    assert session.state.workflow_state == ConversationState.COLLECTING_DATA
    manager.invalidate_cache.assert_called_once_with(session.tenant_id, session.conversation_id)


@pytest.mark.anyio
async def test_set_current_module_failed_commit_preserves_module():
    from backend.pipeline.session_manager import SessionManager, SessionPersistenceError
    from backend.db.client import ConversationSessionCommitOutcome

    mock_db = AsyncMock()
    mock_db.commit_conversation_session_state = AsyncMock(return_value=ConversationSessionCommitOutcome(
        committed=False, reason_code="session_missing", committed_at=None
    ))

    manager = SessionManager("redis://unused")
    manager.db = mock_db
    manager.invalidate_cache = AsyncMock()

    session = ConversationSession(
        id=uuid4(),
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        config_version=1,
        current_module="booking",
        state=SessionState(workflow_state=ConversationState.NEW)
    )

    with pytest.raises(SessionPersistenceError, match="session_missing"):
        await manager.set_current_module(session, "support")

    assert session.current_module == "booking"


@pytest.mark.anyio
async def test_set_current_intent_failed_commit_preserves_intent():
    from backend.pipeline.session_manager import SessionManager, SessionPersistenceError
    from backend.db.client import ConversationSessionCommitOutcome

    mock_db = AsyncMock()
    mock_db.commit_conversation_session_state = AsyncMock(return_value=ConversationSessionCommitOutcome(
        committed=False, reason_code="session_missing", committed_at=None
    ))

    manager = SessionManager("redis://unused")
    manager.db = mock_db
    manager.invalidate_cache = AsyncMock()

    session = ConversationSession(
        id=uuid4(),
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        config_version=1,
        current_intent="book_appt",
        state=SessionState(workflow_state=ConversationState.NEW)
    )

    with pytest.raises(SessionPersistenceError, match="session_missing"):
        await manager.set_current_intent(session, "cancel_appt")

    assert session.current_intent == "book_appt"


@pytest.mark.anyio
async def test_commit_config_for_turn_failed_commit_preserves_binding():
    from backend.pipeline.session_manager import SessionManager, SessionPersistenceError
    from backend.db.client import ConversationSessionCommitOutcome

    mock_db = AsyncMock()
    mock_db.commit_conversation_session_state = AsyncMock(return_value=ConversationSessionCommitOutcome(
        committed=False, reason_code="session_missing", committed_at=None
    ))

    manager = SessionManager("redis://unused")
    manager.db = mock_db
    manager.invalidate_cache = AsyncMock()

    session = ConversationSession(
        id=uuid4(),
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        config_version=3,
        state=SessionState(workflow_state=ConversationState.NEW)
    )

    with pytest.raises(SessionPersistenceError, match="session_missing"):
        await manager.commit_config_for_turn(session, version=5, reason_code="config_refreshed_idle_session")

    assert session.config_version == 3


@pytest.mark.anyio
async def test_timeout_reset_failed_persistence_does_not_install():
    from backend.pipeline.session_manager import SessionManager, SessionPersistenceError
    from backend.db.client import ConversationSessionCommitOutcome

    mock_db = AsyncMock()
    mock_db.get_conversation_session = AsyncMock(return_value={
        "id": uuid4(),
        "tenant_id": uuid4(),
        "conversation_id": uuid4(),
        "config_version": 1,
        "state_json": {
            "config_version": 1,
            "state": {
                "workflow_state": "COLLECTING_DATA",
                "last_updated_at": (datetime.now(timezone.utc) - timedelta(seconds=90000)).isoformat()
            }
        },
        "current_module": "booking",
        "current_intent": "book_appt"
    })

    mock_db.commit_conversation_session_state = AsyncMock(return_value=ConversationSessionCommitOutcome(
        committed=False, reason_code="optimistic_conflict", committed_at=None
    ))

    manager = SessionManager("redis://unused")
    manager.db = mock_db
    manager.redis = FakeRedis()

    with pytest.raises(SessionPersistenceError, match="optimistic_conflict"):
        await manager.get_or_create_session(uuid4(), uuid4())


@pytest.mark.anyio
async def test_cancelled_error_not_converted_into_session_persistence_error():
    from backend.pipeline.session_manager import SessionManager, SessionPersistenceError
    import asyncio

    mock_db = AsyncMock()
    mock_db.commit_conversation_session_state.side_effect = asyncio.CancelledError()

    manager = SessionManager("redis://unused")
    manager.db = mock_db
    manager.invalidate_cache = AsyncMock()

    session = ConversationSession(
        id=uuid4(),
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        config_version=1,
        state=SessionState(workflow_state=ConversationState.NEW)
    )

    new_state = SessionState(workflow_state=ConversationState.COLLECTING_DATA)

    with pytest.raises(asyncio.CancelledError):
        await manager.update_state(session, new_state)
