from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio

import pytest

from backend.core.context import OtpVerificationEvidence
from backend.pipeline import worker
from backend.pipeline.session_manager import SessionPersistenceError
from backend.tests.unit.test_conversation_ux import make_context


pytestmark = pytest.mark.anyio


def _message(provider_message_id: str, *, source_type: str = "internal_event"):
    return SimpleNamespace(
        message=SimpleNamespace(provider_message_id=provider_message_id),
        source_type=SimpleNamespace(value=source_type),
        channel_type=SimpleNamespace(value="awa_chat"),
    )


async def test_pre_dispatch_continuation_failure_is_persisted_and_recoverable(monkeypatch):
    context = make_context(module="booking", step_data={"service_name": "Dental Cleaning"})
    state = context.session_data.state
    pending = state.prepare_pending_action(
        tool_key="booking.execute",
        args={"service_name": "Dental Cleaning"},
        summary="Booking request",
        requires_confirmation=True,
        requires_verified_customer=True,
        verification_action="booking_commitment",
        channel_type="awa_chat",
    )
    pending.idempotency_key = "old-idempotency"
    old_nonce = pending.action_nonce
    now = datetime.now(timezone.utc)
    state.action_ledger.verification.otp_verified_actions = ["booking_commitment"]
    state.action_ledger.verification.otp_verifications = [OtpVerificationEvidence(
        binding="a" * 64,
        action_nonce=old_nonce,
        verification_ref="b" * 32,
        tenant_id=str(context.tenant.id),
        conversation_id=str(context.session_data.conversation_id),
        channel_session_id="11111111-1111-1111-1111-111111111111",
        config_version=context.session_data.config_version,
        tool_key=pending.tool_key,
        args_hash=pending.args_hash,
        verified_at=now,
        expires_at=now + timedelta(minutes=5),
    )]
    async def mock_update(s, cand):
        s.state = cand
    update_state = AsyncMock(side_effect=mock_update)
    dispatch = AsyncMock()
    monkeypatch.setattr(worker.session_manager, "update_state", update_state)
    monkeypatch.setattr(worker, "_dispatch_response", dispatch)
    msg = _message("awa-internal:otp_verified_pending_action:" + "x" * 120)

    recovered = await worker._recover_verified_continuation_failure(
        msg,
        context,
        context.session_data,
        RuntimeError("provider detail must stay private"),
    )

    assert recovered is True
    # Resolve updated objects from context.session_data
    updated_state = context.session_data.state
    updated_pending = updated_state.action_ledger.pending_action

    assert updated_pending.action_nonce != old_nonce
    assert updated_pending.idempotency_key is None
    assert updated_state.action_ledger.task_status == "awaiting_confirmation"
    assert updated_state.action_ledger.last_error.code == "protected_action_processing_failed"
    assert "provider detail" not in updated_state.action_ledger.last_error.safe_message
    assert updated_state.action_ledger.verification.otp_verified_actions == []
    assert updated_state.action_ledger.verification.otp_verifications == []
    update_state.assert_awaited_once()
    assert dispatch.await_args.kwargs["processing_result"] == {
        "processing_status": "failed",
        "recoverable": True,
        "failure_stage": "before_dispatch",
    }
    assert dispatch.await_args.kwargs["quick_replies"] == ["Confirm", "Change", "Cancel"]


async def test_recovery_success_order(monkeypatch):
    """Proves the sequence: candidate recovery mutations prepared -> update_state completes -> dispatch begins."""
    context = make_context(module="booking", step_data={"service_name": "Dental Cleaning"})
    state = context.session_data.state
    pending = state.prepare_pending_action(
        tool_key="booking.execute",
        args={"service_name": "Dental Cleaning"},
        summary="Booking request",
        requires_confirmation=True,
        requires_verified_customer=True,
        verification_action="booking_commitment",
        channel_type="awa_chat",
    )
    pending.idempotency_key = "old-idempotency"
    old_nonce = pending.action_nonce
    state.action_ledger.verification.otp_verified_actions = ["booking_commitment"]
    state.action_ledger.verification.otp_verifications = [OtpVerificationEvidence(
        binding="a" * 64,
        action_nonce=old_nonce,
        verification_ref="b" * 32,
        tenant_id=str(context.tenant.id),
        conversation_id=str(context.session_data.conversation_id),
        channel_session_id="11111111-1111-1111-1111-111111111111",
        config_version=context.session_data.config_version,
        tool_key=pending.tool_key,
        args_hash=pending.args_hash,
        verified_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )]

    events = []

    # Mock SessionManager.update_state to verify mutations are prepared on candidate
    async def mock_update_state(session, candidate_state):
        events.append("update_state_called")
        # Assert mutations are applied on candidate_state before commit completes
        cand_ledger = candidate_state.action_ledger
        cand_pending = cand_ledger.pending_action
        assert cand_ledger.verification.otp_verified_actions == []
        assert cand_ledger.verification.otp_verifications == []
        assert cand_pending.action_nonce != old_nonce
        assert cand_pending.idempotency_key is None
        assert cand_ledger.task_status == "awaiting_confirmation"
        assert cand_ledger.last_error.code == "protected_action_processing_failed"

        # Simulating session manager success updates s.state to candidate_state
        session.state = candidate_state

    monkeypatch.setattr(worker.session_manager, "update_state", mock_update_state)

    async def mock_dispatch_response(*args, **kwargs):
        events.append("dispatch_called")

    monkeypatch.setattr(worker, "_dispatch_response", mock_dispatch_response)

    msg = _message("awa-internal:otp_verified_pending_action:" + "x" * 120)

    recovered = await worker._recover_verified_continuation_failure(
        msg,
        context,
        context.session_data,
        RuntimeError("provider detail must stay private"),
    )

    assert recovered is True
    assert events == ["update_state_called", "dispatch_called"]


async def test_recovery_commit_rejection(monkeypatch):
    """Configure update_state to raise SessionPersistenceError and assert original state is unchanged."""
    context = make_context(module="booking", step_data={"service_name": "Dental Cleaning"})
    state = context.session_data.state
    pending = state.prepare_pending_action(
        tool_key="booking.execute",
        args={"service_name": "Dental Cleaning"},
        summary="Booking request",
        requires_confirmation=True,
        requires_verified_customer=True,
        verification_action="booking_commitment",
        channel_type="awa_chat",
    )
    pending.idempotency_key = "old-idempotency"
    old_nonce = pending.action_nonce
    evidence = OtpVerificationEvidence(
        binding="a" * 64,
        action_nonce=old_nonce,
        verification_ref="b" * 32,
        tenant_id=str(context.tenant.id),
        conversation_id=str(context.session_data.conversation_id),
        channel_session_id="11111111-1111-1111-1111-111111111111",
        config_version=context.session_data.config_version,
        tool_key=pending.tool_key,
        args_hash=pending.args_hash,
        verified_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    state.action_ledger.verification.otp_verified_actions = ["booking_commitment"]
    state.action_ledger.verification.otp_verifications = [evidence]

    async def mock_update_state(session, candidate_state):
        raise SessionPersistenceError("session_write_failed")

    monkeypatch.setattr(worker.session_manager, "update_state", mock_update_state)

    dispatch = AsyncMock()
    monkeypatch.setattr(worker, "_dispatch_response", dispatch)

    msg = _message("awa-internal:otp_verified_pending_action:" + "x" * 120)

    with pytest.raises(SessionPersistenceError, match="session_write_failed"):
        await worker._recover_verified_continuation_failure(
            msg,
            context,
            context.session_data,
            RuntimeError("provider detail must stay private"),
        )

    dispatch.assert_not_awaited()

    # Original state must remain unchanged
    assert pending.action_nonce == old_nonce
    assert pending.idempotency_key == "old-idempotency"
    assert state.action_ledger.task_status == "awaiting_confirmation"
    assert state.action_ledger.last_error is None
    assert state.action_ledger.verification.otp_verified_actions == ["booking_commitment"]
    assert state.action_ledger.verification.otp_verifications == [evidence]


async def test_recovery_cancellation(monkeypatch):
    """Configure update_state to raise asyncio.CancelledError and assert dispatch not called, state unchanged."""
    context = make_context(module="booking", step_data={"service_name": "Dental Cleaning"})
    state = context.session_data.state
    pending = state.prepare_pending_action(
        tool_key="booking.execute",
        args={"service_name": "Dental Cleaning"},
        summary="Booking request",
        requires_confirmation=True,
        requires_verified_customer=True,
        verification_action="booking_commitment",
        channel_type="awa_chat",
    )
    pending.idempotency_key = "old-idempotency"
    old_nonce = pending.action_nonce
    evidence = OtpVerificationEvidence(
        binding="a" * 64,
        action_nonce=old_nonce,
        verification_ref="b" * 32,
        tenant_id=str(context.tenant.id),
        conversation_id=str(context.session_data.conversation_id),
        channel_session_id="11111111-1111-1111-1111-111111111111",
        config_version=context.session_data.config_version,
        tool_key=pending.tool_key,
        args_hash=pending.args_hash,
        verified_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    state.action_ledger.verification.otp_verified_actions = ["booking_commitment"]
    state.action_ledger.verification.otp_verifications = [evidence]

    async def mock_update_state(session, candidate_state):
        raise asyncio.CancelledError()

    monkeypatch.setattr(worker.session_manager, "update_state", mock_update_state)

    dispatch = AsyncMock()
    monkeypatch.setattr(worker, "_dispatch_response", dispatch)

    msg = _message("awa-internal:otp_verified_pending_action:" + "x" * 120)

    with pytest.raises(asyncio.CancelledError):
        await worker._recover_verified_continuation_failure(
            msg,
            context,
            context.session_data,
            RuntimeError("provider detail must stay private"),
        )

    dispatch.assert_not_awaited()

    # Original state must remain unchanged
    assert pending.action_nonce == old_nonce
    assert pending.idempotency_key == "old-idempotency"
    assert state.action_ledger.task_status == "awaiting_confirmation"
    assert state.action_ledger.last_error is None
    assert state.action_ledger.verification.otp_verified_actions == ["booking_commitment"]
    assert state.action_ledger.verification.otp_verifications == [evidence]


async def test_ordinary_worker_failure_is_not_reclassified_as_verified_continuation(monkeypatch):
    context = make_context(module="booking", step_data={"service_name": "Dental Cleaning"})
    context.session_data.state.prepare_pending_action(
        tool_key="booking.execute",
        args={"service_name": "Dental Cleaning"},
        requires_confirmation=True,
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(worker, "_dispatch_response", dispatch)

    recovered = await worker._recover_verified_continuation_failure(
        _message("ordinary-customer-message", source_type="awa_chat"),
        context,
        context.session_data,
        RuntimeError("ordinary failure"),
    )

    assert recovered is False
    dispatch.assert_not_awaited()
