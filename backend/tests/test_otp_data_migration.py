import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.api.awa_chat_security import hash_public_chat_token
from backend.api.routes import public_chat
from backend.core.context import PendingAction, SessionState
from backend.db.client import db_client


pytestmark = pytest.mark.anyio


class AtomicOtpClaim:
    def __init__(self):
        self.values = {}
        self.ttls = {}
        self.claim_count = 0

    async def eval(self, script, key_count, *parts):
        challenge_key, claim_key = parts[:key_count]
        expected_code, claim_value, claim_ttl_ms = parts[key_count:]
        self.claim_count += 1
        challenge = self.values.get(challenge_key)
        if challenge is None or claim_key in self.values:
            return [0, 0]
        if challenge != expected_code:
            return [-1, 0]
        remaining_ms = self.ttls.get(challenge_key, 60_000)
        self.values[claim_key] = claim_value
        self.ttls[claim_key] = int(claim_ttl_ms)
        self.values.pop(challenge_key, None)
        self.ttls.pop(challenge_key, None)
        return [1, remaining_ms]


async def test_otp_verification_migrates_all_guest_data(monkeypatch):
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    guest_id = uuid4()
    verified_id = uuid4()
    other_customer_id = uuid4()
    session_id = uuid4()
    conversation_id = uuid4()
    link_id = uuid4()
    phone = "+919876543210"
    session_token = f"session-tok-{uuid4().hex}"

    async with db_client.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, plan_code, status) VALUES ($1, $2, $3, $4, $5)",
            tenant_id, "Test Merge Tenant", f"merge-{uuid4().hex[:8]}", "trial", "trial",
        )
        await conn.execute(
            "INSERT INTO tenant_configs (id, tenant_id, version, config_json, is_active) VALUES ($1, $2, $3, $4, $5)",
            uuid4(), tenant_id, 7, json.dumps({}), True,
        )
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, plan_code, status) VALUES ($1, $2, $3, $4, $5)",
            other_tenant_id, "Other Tenant", f"other-{uuid4().hex[:8]}", "trial", "trial",
        )
        await conn.execute(
            "INSERT INTO customers (id, tenant_id, phone_number, name, consent_status) VALUES ($1, $2, $3, $4, $5)",
            guest_id, tenant_id, f"guest-{guest_id}", "Guest Customer", "granted",
        )
        await conn.execute(
            "INSERT INTO customers (id, tenant_id, phone_number, name, consent_status) VALUES ($1, $2, $3, $4, $5)",
            verified_id, tenant_id, phone, "Verified Customer", "granted",
        )
        await conn.execute(
            "INSERT INTO customers (id, tenant_id, phone_number, name, consent_status) VALUES ($1, $2, $3, $4, $5)",
            other_customer_id, other_tenant_id, "+910000000000", "Other Customer", "granted",
        )
        await conn.execute(
            "INSERT INTO conversations (id, tenant_id, customer_id, channel_type, status) VALUES ($1, $2, $3, $4, $5)",
            conversation_id, tenant_id, guest_id, "awa_chat", "active",
        )
        await conn.execute(
            "INSERT INTO public_chat_links (id, tenant_id, slug, display_name, token_hash, metadata, status) VALUES ($1, $2, $3, $4, $5, '{}'::jsonb, 'active')",
            link_id, tenant_id, f"merge-link-{uuid4().hex[:8]}", "Merge Link", "link-token-hash",
        )
        await conn.execute(
            "INSERT INTO public_chat_sessions (id, tenant_id, link_id, customer_id, conversation_id, session_token_hash, visitor_name, status) VALUES ($1, $2, $3, $4, $5, $6, $7, 'active')",
            session_id, tenant_id, link_id, guest_id, conversation_id,
            hash_public_chat_token(session_token, "session"), "New Guest Name",
        )

        booking_id = uuid4()
        order_id = uuid4()
        lead_id = uuid4()
        payment_req_id = uuid4()
        review_req_id = uuid4()
        note_id = uuid4()
        tag_id = uuid4()
        other_booking_id = uuid4()
        await conn.execute(
            "INSERT INTO bookings (id, tenant_id, customer_id, service_name, booking_date, status) VALUES ($1, $2, $3, $4, CURRENT_DATE, 'confirmed')",
            booking_id, tenant_id, guest_id, "haircut",
        )
        await conn.execute(
            "INSERT INTO orders (id, tenant_id, customer_id, status, total_amount) VALUES ($1, $2, $3, $4, $5)",
            order_id, tenant_id, guest_id, "new", 100.0,
        )
        await conn.execute(
            "INSERT INTO leads (id, tenant_id, customer_id, source_channel, status) VALUES ($1, $2, $3, $4, $5)",
            lead_id, tenant_id, guest_id, "public_chat", "new",
        )
        await conn.execute(
            "INSERT INTO payment_requests (id, tenant_id, customer_id, amount, status) VALUES ($1, $2, $3, $4, $5)",
            payment_req_id, tenant_id, guest_id, 100.0, "draft",
        )
        await conn.execute(
            "INSERT INTO review_requests (id, tenant_id, customer_id, status) VALUES ($1, $2, $3, $4)",
            review_req_id, tenant_id, guest_id, "sent",
        )
        await conn.execute(
            "INSERT INTO customer_notes (id, tenant_id, customer_id, note) VALUES ($1, $2, $3, $4)",
            note_id, tenant_id, guest_id, "Test Note Text",
        )
        await conn.execute(
            "INSERT INTO customer_tags (id, tenant_id, name) VALUES ($1, $2, $3)",
            tag_id, tenant_id, "VIP",
        )
        await conn.execute(
            "INSERT INTO customer_tag_links (tenant_id, customer_id, tag_id) VALUES ($1, $2, $3)",
            tenant_id, guest_id, tag_id,
        )
        await conn.execute(
            "INSERT INTO bookings (id, tenant_id, customer_id, service_name, booking_date, status) VALUES ($1, $2, $3, $4, CURRENT_DATE, 'confirmed')",
            other_booking_id, other_tenant_id, other_customer_id, "untouched",
        )

    pending = PendingAction(
        tool_key="booking.execute",
        args={"service_name": "haircut"},
        requires_confirmation=True,
        requires_verified_customer=True,
        verification_action="booking_commitment",
    )
    state = SessionState()
    state.action_ledger.current_task = "booking"
    state.action_ledger.task_status = "awaiting_confirmation"
    state.action_ledger.pending_action = pending
    await db_client.create_conversation_session(
        session_id=uuid4(),
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        config_version=7,
        current_module="booking",
        current_intent="book",
        state_json={"config_version": 7, "state": state.model_dump(mode="json")},
    )
    conversation_session = await db_client.get_conversation_session(tenant_id, conversation_id)
    public_session = await db_client.get_public_chat_session(
        session_id, hash_public_chat_token(session_token, "session")
    )
    action_binding = public_chat._pending_action_binding(
        pending.model_dump(mode="json"),
        session=public_session,
        conversation_session=conversation_session,
    )
    assert action_binding

    atomic = AtomicOtpClaim()
    challenge_key = public_chat._otp_challenge_key(session_id, action_binding, phone)
    atomic.values[challenge_key] = "123456"
    atomic.ttls[challenge_key] = 60_000
    monkeypatch.setattr("backend.core.redis_client.redis_client", atomic)
    monkeypatch.setattr(public_chat, "_check_public_rate_limit", AsyncMock())
    monkeypatch.setattr(public_chat, "_redis_set", AsyncMock())
    monkeypatch.setattr(public_chat, "_redis_delete", AsyncMock())
    enqueue = AsyncMock(return_value="queued")
    monkeypatch.setattr(public_chat, "_enqueue_for_worker", enqueue)
    monkeypatch.setattr("backend.pipeline.session_manager.session_manager.invalidate_cache", AsyncMock())

    body = public_chat.PublicChatOtpVerifyRequest(
        phone=phone,
        code="123456",
        session_token=session_token,
        action="commitment",
    )
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    response = await public_chat.verify_public_chat_otp(session_id, body, request)
    assert response["data"]["verified"] is True

    with pytest.raises(HTTPException) as replay:
        await public_chat.verify_public_chat_otp(session_id, body, request)
    assert replay.value.status_code == 409
    assert atomic.claim_count == 2
    # OTP migration/identity reconciliation does not imply confirmation of the
    # protected action.
    enqueue.assert_not_awaited()

    async with db_client.acquire() as conn:
        for table, item_id in (
            ("bookings", booking_id),
            ("orders", order_id),
            ("leads", lead_id),
            ("payment_requests", payment_req_id),
            ("review_requests", review_req_id),
            ("customer_notes", note_id),
        ):
            assert await conn.fetchval(f"SELECT customer_id FROM {table} WHERE id = $1", item_id) == verified_id
        tags = await conn.fetch("SELECT tag_id FROM customer_tag_links WHERE customer_id = $1", verified_id)
        assert [row["tag_id"] for row in tags] == [tag_id]
        assert await conn.fetchval("SELECT id FROM customers WHERE id = $1", guest_id) is None
        assert await conn.fetchval("SELECT customer_id FROM bookings WHERE id = $1", other_booking_id) == other_customer_id
        assert await conn.fetchval("SELECT tenant_id FROM customers WHERE id = $1", other_customer_id) == other_tenant_id
