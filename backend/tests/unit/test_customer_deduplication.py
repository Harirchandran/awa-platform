from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from backend.api.routes import public_chat
from backend.core.context import PendingAction, SessionState


TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Connection:
    def __init__(self):
        self.execute = AsyncMock()

    def transaction(self):
        return _Transaction()


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *args):
        return False


class _BoundDb:
    def __init__(self, conversation_session):
        self.connection = _Connection()
        self.get_conversation_session = AsyncMock(return_value=conversation_session)
        self.update_conversation_session = AsyncMock(return_value=True)
        self.get_next_sequence_number = AsyncMock(return_value=9)

    def acquire(self):
        return _Acquire(self.connection)


def _install_bound_otp(monkeypatch, *, visitor_name):
    session_id = uuid4()
    guest_customer_id = uuid4()
    conversation_id = uuid4()
    session = {
        "id": session_id,
        "link_id": uuid4(),
        "tenant_id": TENANT_ID,
        "customer_id": guest_customer_id,
        "conversation_id": conversation_id,
        "visitor_name": visitor_name,
    }
    pending = PendingAction(
        tool_key="booking.execute",
        args={"service_name": "Dental Cleaning"},
        requires_confirmation=True,
        requires_verified_customer=True,
        verification_action="booking_commitment",
    )
    state = SessionState()
    state.action_ledger.current_task = "booking"
    state.action_ledger.task_status = "awaiting_confirmation"
    state.action_ledger.pending_action = pending
    conversation_session = {
        "id": uuid4(),
        "tenant_id": TENANT_ID,
        "conversation_id": conversation_id,
        "config_version": 7,
        "current_module": "booking",
        "current_intent": "book",
        "state_json": {"config_version": 7, "state": state.model_dump(mode="json")},
    }
    binding = public_chat._pending_action_binding(
        pending.model_dump(mode="json"),
        session=session,
        conversation_session=conversation_session,
    )
    assert binding

    db = _BoundDb(conversation_session)
    claim = AsyncMock(side_effect=[("claimed", 60_000), ("consumed", 0)])
    enqueue = AsyncMock(return_value="queued")
    monkeypatch.setattr(public_chat, "db_client", db)
    monkeypatch.setattr(public_chat, "_check_public_rate_limit", AsyncMock())
    monkeypatch.setattr(public_chat, "_get_active_session", AsyncMock(return_value=session))
    monkeypatch.setattr(public_chat, "claim_otp_challenge", claim)
    monkeypatch.setattr(public_chat, "_redis_set", AsyncMock())
    monkeypatch.setattr(public_chat, "_redis_delete", AsyncMock())
    monkeypatch.setattr(public_chat, "_enqueue_for_worker", enqueue)
    monkeypatch.setattr("backend.pipeline.session_manager.session_manager.invalidate_cache", AsyncMock())
    body = public_chat.PublicChatOtpVerifyRequest(
        session_token="chat_test_token_abcdefghijklmnopqrstuvwxyz",
        phone="+1 555 0100",
        action="booking_commitment",
        code="123456",
    )
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    return session_id, guest_customer_id, session, body, request, binding, db, claim, enqueue


async def _verify_once_and_replay(session_id, body, request, claim, enqueue):
    response = await public_chat.verify_public_chat_otp(session_id, body, request)
    assert response["success"] is True
    assert response["data"]["verified"] is True
    with pytest.raises(HTTPException) as replay:
        await public_chat.verify_public_chat_otp(session_id, body, request)
    assert replay.value.status_code == 409
    assert claim.await_count == 2
    # Verification binds the customer identity but must not turn into an
    # implicit confirmation or enqueue execution.
    enqueue.assert_not_awaited()
    return response


@pytest.mark.anyio
async def test_verify_public_chat_otp_no_conflict(monkeypatch):
    session_id, guest_id, session, body, request, binding, db, claim, enqueue = _install_bound_otp(
        monkeypatch, visitor_name="New Guest"
    )
    with patch("backend.api.routes.public_chat.customer_service") as customer_service:
        customer_service.get_customer_by_phone = AsyncMock(return_value=None)
        customer_service.update_customer = AsyncMock(return_value=True)
        response = await _verify_once_and_replay(session_id, body, request, claim, enqueue)

    assert response["data"]["action"] == "booking_commitment"
    customer_service.get_customer_by_phone.assert_awaited_once_with(
        tenant_id=TENANT_ID, phone_number="+15550100"
    )
    customer_service.update_customer.assert_awaited_once_with(
        tenant_id=TENANT_ID,
        customer_id=guest_id,
        phone_number="+15550100",
        consent_status="granted",
    )
    assert session["customer_id"] == guest_id
    assert claim.await_args_list[0].kwargs["challenge_key"] == public_chat._otp_challenge_key(
        session_id, binding, "+15550100"
    )
    db.connection.execute.assert_not_awaited()


@pytest.mark.anyio
async def test_verify_public_chat_otp_same_customer(monkeypatch):
    session_id, guest_id, session, body, request, _, db, claim, enqueue = _install_bound_otp(
        monkeypatch, visitor_name="New Guest"
    )
    with patch("backend.api.routes.public_chat.customer_service") as customer_service:
        customer_service.get_customer_by_phone = AsyncMock(return_value={
            "id": guest_id,
            "tenant_id": TENANT_ID,
            "phone_number": "+15550100",
            "name": "New Guest",
        })
        customer_service.update_customer = AsyncMock(return_value=True)
        await _verify_once_and_replay(session_id, body, request, claim, enqueue)

    customer_service.update_customer.assert_awaited_once_with(
        tenant_id=TENANT_ID,
        customer_id=guest_id,
        phone_number="+15550100",
        consent_status="granted",
    )
    assert session["customer_id"] == guest_id
    db.connection.execute.assert_not_awaited()


@pytest.mark.anyio
async def test_verify_public_chat_otp_conflict_merge(monkeypatch):
    session_id, guest_id, session, body, request, _, db, claim, enqueue = _install_bound_otp(
        monkeypatch, visitor_name="Verified Name"
    )
    existing_customer_id = uuid4()
    with patch("backend.api.routes.public_chat.customer_service") as customer_service:
        customer_service.get_customer_by_phone = AsyncMock(return_value={
            "id": existing_customer_id,
            "tenant_id": TENANT_ID,
            "phone_number": "+15550100",
            "name": None,
        })
        customer_service.update_customer = AsyncMock(return_value=True)
        customer_service.update_public_chat_session_customer = AsyncMock()
        customer_service.delete_customer = AsyncMock(return_value=True)
        await _verify_once_and_replay(session_id, body, request, claim, enqueue)

    customer_service.update_customer.assert_awaited_once_with(
        tenant_id=TENANT_ID,
        customer_id=existing_customer_id,
        consent_status="granted",
        name="Verified Name",
    )
    customer_service.update_public_chat_session_customer.assert_awaited_once_with(
        tenant_id=TENANT_ID,
        session_id=session_id,
        conversation_id=session["conversation_id"],
        customer_id=existing_customer_id,
    )
    customer_service.delete_customer.assert_awaited_once_with(
        tenant_id=TENANT_ID, customer_id=guest_id
    )
    assert session["customer_id"] == existing_customer_id
    assert db.connection.execute.await_count == 8
    assert all(TENANT_ID in call.args for call in db.connection.execute.await_args_list)
