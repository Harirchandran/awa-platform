import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.api.routes.public_chat import _save_public_chat_greeting
from backend.core.context import (
    ConversationSession,
    ExecutionContext,
    SessionState,
    Tenant,
    TenantConfig,
    User,
)
from backend.logic.engine.field_extraction import _booking_extraction_payload
from backend.logic.engine.history import build_memory_context
from backend.pipeline.config_binding import ConfigBindingError, resolve_config_binding
from backend.pipeline.config_binding import state_payload_has_active_work


def test_persisted_idle_state_is_not_pinned_but_protected_action_is_pinned():
    assert state_payload_has_active_work({"config_version": 1, "state": {}}) is False
    assert state_payload_has_active_work({
        "config_version": 1,
        "state": {"action_ledger": {"pending_action": {"id": "pending"}}},
    }) is True
from backend.pipeline.session_manager import SessionLoadError, SessionManager


pytestmark = pytest.mark.customer_turn


class FakeRedis:
    def __init__(self, cached=None):
        self.cached = cached
        self.set_calls = []

    async def get(self, _key):
        return self.cached

    async def set(self, key, value, ex=None):
        self.cached = value
        self.set_calls.append((key, value, ex))

    async def delete(self, _key):
        self.cached = None


def _session(*, config_version=3, active=False, orientation=False):
    session = ConversationSession(
        id=uuid4(),
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        config_version=config_version,
        state=SessionState(orientation_config_pending=orientation),
    )
    if active:
        session.current_module = "booking"
        session.state.action_ledger.current_task = "booking"
        session.state.update_task_step_data("booking", {"service_name": "Cavity Filling"})
        session.state.set_prompt_focus(workflow="booking", requested_fields=["booking_date", "booking_time"])
    return session


def _context(session=None):
    session = session or _session()
    return ExecutionContext(
        tenant=Tenant(id=session.tenant_id, name="Clinic", plan_code="pro", status="active"),
        user=User(id=uuid4(), phone_number="unknown"),
        session_data=session,
        config=TenantConfig(tenant_id=session.tenant_id, version=session.config_version, config_data={}),
        incoming_message={"message_id": uuid4(), "text": "tomorrow", "conversation_sequence_number": 12},
    )


@pytest.mark.asyncio
async def test_orientation_is_marked_for_audit_but_not_semantic_authority(monkeypatch):
    save = AsyncMock(return_value={"created": True})
    monkeypatch.setattr("backend.api.routes.public_chat.db_client.save_message", save)
    await _save_public_chat_greeting(
        tenant_id=uuid4(), conversation_id=uuid4(), session_id=uuid4(),
        greeting_message="Welcome", suggestions=["Cavity Filling", "Opening hours", "Ask a question"],
    )
    payload = save.await_args.kwargs["raw_payload"]
    assert payload["source"] == "greeting"
    assert payload["semantic_role"] == "orientation"
    assert payload["include_in_semantic_history"] is False
    assert "Cavity Filling" in json.dumps(payload)


@pytest.mark.asyncio
async def test_history_excludes_new_and_historical_orientation_and_current_inbound(monkeypatch):
    context = _context()
    current_id = context.incoming_message["message_id"]
    messages = [
        {"id": uuid4(), "direction": "outbound", "message_text": "Welcome new", "raw_payload": {"semantic_role": "orientation"}},
        {"id": uuid4(), "direction": "outbound", "message_text": "Welcome old", "raw_payload": {"source": "greeting"}},
        {"id": uuid4(), "direction": "outbound", "message_text": "Which date?", "raw_payload": {}},
        {"id": current_id, "direction": "inbound", "message_text": "tomorrow", "raw_payload": {}},
        {"id": uuid4(), "direction": "inbound", "message_text": "Cavity Filling", "raw_payload": {}},
    ]
    monkeypatch.setattr(
        "backend.logic.engine.history.db_client.list_messages",
        AsyncMock(return_value={"items": list(reversed(messages))}),
    )
    memory = await build_memory_context(context)
    assert "Welcome new" not in memory and "Welcome old" not in memory
    assert "Assistant: Which date?" in memory
    assert memory.count("Customer: tomorrow") == 0
    assert memory.count("Customer: Cavity Filling") == 1


@pytest.mark.asyncio
async def test_cache_miss_recovers_complete_database_session_and_warms_cache():
    original = _session(active=True)
    state_json = {"config_version": original.config_version, "state": original.state.model_dump(mode="json")}
    db = SimpleNamespace(get_conversation_session=AsyncMock(return_value={
        "id": original.id, "tenant_id": original.tenant_id, "conversation_id": original.conversation_id,
        "current_module": original.current_module, "current_intent": None,
        "config_version": original.config_version,
        "state_json": state_json, "expires_at": None,
    }))
    manager = SessionManager("redis://unused")
    manager.redis = FakeRedis()
    manager.db = db
    recovered = await manager.get_or_create_session(original.tenant_id, original.conversation_id)
    assert recovered.config_version == 3
    assert recovered.state.get_task_step_data("booking")["service_name"] == "Cavity Filling"
    assert recovered.state.prompt_focus.requested_fields == ["booking_date", "booking_time"]
    assert manager.redis.set_calls


@pytest.mark.asyncio
async def test_database_failure_and_invalid_state_fail_closed_without_create():
    manager = SessionManager("redis://unused")
    manager.redis = FakeRedis()
    manager.db = SimpleNamespace(
        get_conversation_session=AsyncMock(side_effect=ConnectionError("database unavailable")),
        create_conversation_session=AsyncMock(),
    )
    with pytest.raises(SessionLoadError, match="session_load_failed"):
        await manager.get_or_create_session(uuid4(), uuid4())
    manager.db.create_conversation_session.assert_not_awaited()

    manager.db.get_conversation_session = AsyncMock(return_value={
        "id": uuid4(), "tenant_id": uuid4(), "conversation_id": uuid4(),
        "current_module": "booking", "current_intent": None,
        "state_json": "{not-json", "expires_at": None,
    })
    with pytest.raises(SessionLoadError, match="session_state_invalid"):
        await manager.get_or_create_session(uuid4(), uuid4())
    manager.db.create_conversation_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_genuinely_missing_session_is_created_only_after_definitive_none():
    manager = SessionManager("redis://unused")
    manager.redis = FakeRedis()
    create_mock = AsyncMock(return_value={})
    manager.db = SimpleNamespace(
        get_conversation_session=AsyncMock(return_value=None),
        get_active_config_version=AsyncMock(return_value=7),
        create_conversation_session=create_mock,
    )
    created = await manager.get_or_create_session(uuid4(), uuid4())
    assert created.config_version == 7
    create_mock.assert_awaited_once()


def test_config_binding_pins_active_and_orientation_but_refreshes_idle():
    active = _session(config_version=3, active=True)
    decision = resolve_config_binding(active, active_version=9)
    assert (decision.version, decision.reason_code) == (3, "config_pinned_active_workflow")

    orientation = _session(config_version=4, orientation=True)
    decision = resolve_config_binding(orientation, active_version=9)
    assert decision.version == 4 and decision.persist_required

    idle = _session(config_version=3)
    decision = resolve_config_binding(idle, active_version=9)
    assert (decision.version, decision.reason_code, decision.persist_required) == (9, "config_refreshed_idle_session", True)

    active.config_version = 0
    with pytest.raises(ConfigBindingError, match="pinned_config_unavailable"):
        resolve_config_binding(active, active_version=9)


@pytest.mark.asyncio
async def test_orientation_binding_is_committed_only_after_exact_config_load(monkeypatch):
    session = _session(config_version=4, orientation=True)
    manager = SessionManager("redis://unused")
    manager.redis = FakeRedis()
    manager.db = SimpleNamespace()
    async def fake_commit_session_change(session, candidate_state, candidate_module, candidate_intent, candidate_config_version):
        session.state = candidate_state
        session.current_module = candidate_module
        session.current_intent = candidate_intent
        session.config_version = candidate_config_version
    commit_mock = AsyncMock(side_effect=fake_commit_session_change)
    monkeypatch.setattr(manager, "_commit_session_change", commit_mock)

    version, reason = await manager.bind_config_for_turn(session, active_version=9)
    assert (version, reason) == (4, "config_pinned_orientation_first_turn")
    assert session.state.orientation_config_pending is True
    commit_mock.assert_not_awaited()

    await manager.commit_config_for_turn(session, version=version, reason_code=reason)
    assert session.state.orientation_config_pending is False
    commit_mock.assert_awaited_once()


def test_prompt_focus_is_bounded_pii_free_consumed_and_cleared_for_confirmation():
    session = _session(active=True)
    state = session.state
    state.set_prompt_focus(
        workflow="booking",
        requested_fields=["booking_date", "booking_time", "email@example.com", "+919999999999"],
        prompt_kind="missing_fields",
        turn_sequence=12,
    )
    dumped = state.prompt_focus.model_dump()
    assert dumped["requested_fields"] == ["booking_date", "booking_time"]
    assert "Which date" not in json.dumps(dumped)
    payload = _booking_extraction_payload(_context(session))
    assert payload["workflow_scope"]["prompt_focus"]["requested_fields"] == ["booking_date", "booking_time"]

    state.prepare_pending_action(tool_key="booking.execute", args={"service_name": "Cavity Filling"})
    assert state.prompt_focus is None
