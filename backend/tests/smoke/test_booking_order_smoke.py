import pytest
from uuid import uuid4
import json
from unittest.mock import AsyncMock

from backend.api.dependencies import get_current_user
from backend.api.app import app
from backend.db.client import db_client
from backend.pipeline.worker import process_message
from backend.core.states import ConversationState
from backend.logic.engine.pending_decision import PendingActionDecision
from backend.logic.engine.field_extraction import BookingFieldExtraction

pytestmark = pytest.mark.anyio

async def test_booking_creation_flow(api_client, tenant_factory, mock_redis_globally, mock_llm_globally, monkeypatch):
    async def fake_pending_decision(context):
        return PendingActionDecision(action="confirm", confidence=0.99)

    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", fake_pending_decision)
    monkeypatch.setattr(
        "backend.logic.engine.core.extract_booking_fields",
        AsyncMock(
            return_value=BookingFieldExtraction(
                workflow="booking",
                workflow_action="continue",
                confidence=0.99,
                field_updates={
                    "service_name": "dental cleaning",
                    "booking_date": "tomorrow",
                    "booking_time": "10 AM",
                    "customer_name": "Bob",
                },
                field_confidence={
                    "service_name": 0.99,
                    "booking_date": 0.99,
                    "booking_time": 0.99,
                    "customer_name": 0.99,
                },
                clarification_needed=False,
            )
        ),
    )

    # 1. Create a tenant under enterprise plan and authenticate
    tenant_info = await tenant_factory(name="Booking Clinic", plan_code="enterprise")
    tenant_id = tenant_info["tenant_id"]
    user_id = tenant_info["user_id"]

    async def mock_admin_user():
        return {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role": "admin"
        }
    app.dependency_overrides[get_current_user] = mock_admin_user

    try:
        slug = f"bookings-{uuid4().hex[:6]}"
        resp = await api_client.post(
            "/api/v1/awa-chat/links",
            json={"display_name": "Clinic Bookings", "slug": slug}
        )
        assert resp.status_code == 200, resp.text
        created_slug = resp.json()["data"]["slug"]

        # Start customer session
        visitor_name = "Bob"
        resp = await api_client.post(
            f"/api/v1/public-chat/links/{created_slug}/sessions",
            json={"visitor_name": visitor_name}
        )
        assert resp.status_code == 200, resp.text
        session_data = resp.json()["data"]
        session_id = session_data["id"]
        session_token = session_data["session_token"]
        conversation_id = session_data["conversation_id"]

        # 2. Bob requests a booking
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={
                "session_token": session_token,
                "message_text": "Book dental cleaning tomorrow at 10 AM. My name is Bob and my phone is +15552345678."
            }
        )
        assert resp.status_code == 200, resp.text
        raw_msg = mock_redis_globally.data["message_queue"].pop()

        await process_message(raw_msg)

        # Verify it transitions to awaiting_confirmation
        from backend.pipeline.session_manager import session_manager
        session = await session_manager.get_or_create_session(tenant_id, conversation_id)
        assert session.state.action_ledger.task_status == "awaiting_confirmation"
        assert session.state.action_ledger.pending_action is not None

        # 3. Simulate OTP challenge send
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/otp/send",
            json={
                "session_token": session_token,
                "phone": "+15552345678",
                "action": "booking_commitment"
            }
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["sent"] is True

        # 4. Simulate OTP challenge verify
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/otp/verify",
            json={
                "session_token": session_token,
                "phone": "+15552345678",
                "code": "123456",
                "action": "booking_commitment"
            }
        )
        assert resp.status_code == 200, resp.text

        # 5. OTP alone must leave the proposal pending; the customer must
        # explicitly confirm before a worker executes it.
        assert mock_redis_globally.data["message_queue"] == []
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={"session_token": session_token, "message_text": "Confirm"},
        )
        assert resp.status_code == 200, resp.text
        raw_msg = mock_redis_globally.data["message_queue"].pop()
        await process_message(raw_msg)

        # 6. Verify booking is created in database
        async with db_client.acquire() as conn:
            booking = await conn.fetchrow(
                "SELECT * FROM bookings WHERE tenant_id = $1 AND conversation_id = $2",
                tenant_id, conversation_id
            )
            assert booking is not None
            assert booking["service_name"] in ("dental cleaning", "cleaning")
            assert booking["status"] in ("confirmed", "pending")

        # 7. Verify tenant isolation: another tenant's admin cannot list this booking
        another_tenant_info = await tenant_factory(name="Other Booking Clinic", plan_code="enterprise")
        async def mock_other_admin():
            return {
                "user_id": another_tenant_info["user_id"],
                "tenant_id": another_tenant_info["tenant_id"],
                "role": "admin"
            }
        app.dependency_overrides[get_current_user] = mock_other_admin

        resp = await api_client.get("/api/v1/bookings")
        assert resp.status_code == 200
        bookings_list = resp.json()["data"]
        for b in bookings_list:
            assert b["id"] != str(booking["id"])

    finally:
        app.dependency_overrides.clear()


async def test_order_creation_flow(api_client, tenant_factory, mock_redis_globally, mock_llm_globally, monkeypatch):
    async def fake_pending_decision(context):
        return PendingActionDecision(action="confirm", confidence=0.99)

    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", fake_pending_decision)

    # 1. Create a tenant under enterprise plan and authenticate
    tenant_info = await tenant_factory(name="Order Clinic", plan_code="enterprise")
    tenant_id = tenant_info["tenant_id"]
    user_id = tenant_info["user_id"]

    async def mock_admin_user():
        return {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role": "admin"
        }
    app.dependency_overrides[get_current_user] = mock_admin_user

    try:
        slug = f"orders-{uuid4().hex[:6]}"
        resp = await api_client.post(
            "/api/v1/awa-chat/links",
            json={"display_name": "Clinic Orders", "slug": slug}
        )
        assert resp.status_code == 200, resp.text
        created_slug = resp.json()["data"]["slug"]

        # Start customer session
        visitor_name = "Bob"
        resp = await api_client.post(
            f"/api/v1/public-chat/links/{created_slug}/sessions",
            json={"visitor_name": visitor_name}
        )
        assert resp.status_code == 200, resp.text
        session_data = resp.json()["data"]
        session_id = session_data["id"]
        session_token = session_data["session_token"]
        conversation_id = session_data["conversation_id"]

        # 2. Bob requests toothpaste order
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={
                "session_token": session_token,
                "message_text": "I want to order 2 Toothpaste for pickup. My name is Bob and my phone is +15558765432."
            }
        )
        assert resp.status_code == 200, resp.text
        raw_msg = mock_redis_globally.data["message_queue"].pop()

        await process_message(raw_msg)

        # Verify it transitions to awaiting_confirmation
        from backend.pipeline.session_manager import session_manager
        session = await session_manager.get_or_create_session(tenant_id, conversation_id)
        assert session.state.action_ledger.task_status == "awaiting_confirmation"

        # 3. Simulate OTP challenge send
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/otp/send",
            json={
                "session_token": session_token,
                "phone": "+15558765432",
                "action": "order_commitment"
            }
        )
        assert resp.status_code == 200, resp.text

        # 4. Simulate OTP challenge verify
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/otp/verify",
            json={
                "session_token": session_token,
                "phone": "+15558765432",
                "code": "123456",
                "action": "order_commitment"
            }
        )
        assert resp.status_code == 200, resp.text

        # 5. OTP alone must leave the proposal pending; the customer must
        # explicitly confirm before a worker executes it.
        assert mock_redis_globally.data["message_queue"] == []
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={"session_token": session_token, "message_text": "Confirm"},
        )
        assert resp.status_code == 200, resp.text
        raw_msg = mock_redis_globally.data["message_queue"].pop()
        await process_message(raw_msg)

        # 6. Verify order was created in database
        async with db_client.acquire() as conn:
            order = await conn.fetchrow(
                "SELECT * FROM orders WHERE tenant_id = $1 AND conversation_id = $2",
                tenant_id, conversation_id
            )
            assert order is not None
            assert order["status"] in ("pending", "new")

    finally:
        app.dependency_overrides.clear()
