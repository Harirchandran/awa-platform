import pytest
from uuid import uuid4
import json
from datetime import datetime, timezone, timedelta

from backend.api.dependencies import get_current_user, require_tenant_admin
from backend.api.app import app
from backend.db.client import db_client
from backend.pipeline.worker import process_message
from backend.core.states import ConversationState
from backend.logic.engine.pending_decision import PendingActionDecision

pytestmark = pytest.mark.anyio

async def test_payment_and_webhook_flow(api_client, tenant_factory, mock_redis_globally, mock_llm_globally, monkeypatch):
    async def fake_pending_decision(context):
        return PendingActionDecision(action="confirm", confidence=0.99)

    monkeypatch.setattr("backend.logic.engine.core.decide_pending_action", fake_pending_decision)

    # Mock Razorpay keys
    monkeypatch.setattr("backend.modules.payments_module.settings.RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr("backend.modules.payments_module.settings.RAZORPAY_KEY_SECRET", "rzp_test_secret")

    # Mock the actual _create_razorpay_payment_link method on payments_module singleton
    from backend.modules.payments_module import payments_module
    async def fake_create_link(*args, **kwargs):
        return {
            "blocked": False,
            "payment_url": "https://rzp.io/i/mock_pay_123",
            "provider_payment_link_id": "plink_mock_123",
            "provider_order_id": "order_mock_123",
        }
    monkeypatch.setattr(payments_module, "_create_razorpay_payment_link", fake_create_link)

    # 1. Create a tenant under enterprise plan and authenticate
    modules_config = {
        "payments": {
            "enabled": True,
            "provider": "razorpay",
            "use_platform_razorpay": True
        },
        "booking": {"enabled": True},
        "order": {"enabled": True},
        "lead_capture": {"enabled": True},
        "human_handoff": {"enabled": True},
        "support": {"enabled": True},
    }
    tenant_info = await tenant_factory(
        name="Payment Clinic",
        plan_code="enterprise",
        modules_config=modules_config,
    )
    tenant_id = tenant_info["tenant_id"]
    user_id = tenant_info["user_id"]

    async def mock_admin_user():
        return {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role": "admin"
        }
    app.dependency_overrides[get_current_user] = mock_admin_user
    app.dependency_overrides[require_tenant_admin] = mock_admin_user

    try:
        slug = f"payments-{uuid4().hex[:6]}"
        resp = await api_client.post(
            "/api/v1/awa-chat/links",
            json={"display_name": "Clinic Payments", "slug": slug}
        )
        assert resp.status_code == 200, resp.text
        created_slug = resp.json()["data"]["slug"]

        # Start customer session
        resp = await api_client.post(
            f"/api/v1/public-chat/links/{created_slug}/sessions",
            json={"visitor_name": "Bob"}
        )
        assert resp.status_code == 200, resp.text
        session_data = resp.json()["data"]
        session_id = session_data["id"]
        session_token = session_data["session_token"]
        conversation_id = session_data["conversation_id"]

        # 2. Bob requests a payment link with name and phone number
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={
                "session_token": session_token,
                "message_text": "I want to pay 500. My name is Bob and my phone is +15552345678."
            }
        )
        assert resp.status_code == 200
        raw_msg = mock_redis_globally.data["message_queue"].pop()

        await process_message(raw_msg)

        # Verify it transitions to awaiting_confirmation
        from backend.pipeline.session_manager import session_manager
        session = await session_manager.get_or_create_session(tenant_id, conversation_id)
        assert session.state.action_ledger.task_status == "awaiting_confirmation"

        # Simulate OTP challenge send
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/otp/send",
            json={
                "session_token": session_token,
                "phone": "+15552345678",
                "action": "payments_commitment"
            }
        )
        assert resp.status_code == 200, resp.text

        # Simulate OTP challenge verify
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/otp/verify",
            json={
                "session_token": session_token,
                "phone": "+15552345678",
                "code": "123456",
                "action": "payments_commitment"
            }
        )
        assert resp.status_code == 200, resp.text

        # OTP establishes phone possession only; execute only after an
        # explicit customer confirmation.
        assert mock_redis_globally.data["message_queue"] == []
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={"session_token": session_token, "message_text": "Confirm"},
        )
        assert resp.status_code == 200, resp.text
        raw_msg = mock_redis_globally.data["message_queue"].pop()
        await process_message(raw_msg)

        # 3. Verify payment request created in DB
        async with db_client.acquire() as conn:
            pay_req = await conn.fetchrow(
                "SELECT * FROM payment_requests WHERE tenant_id = $1 AND amount = 500.00",
                tenant_id
            )
            assert pay_req is not None
            assert pay_req["status"] == "sent"

            # Verify payment link ID was set (mock generated link)
            link_id = pay_req["provider_payment_link_id"]
            assert link_id is not None

        # 4. Trigger Webhook Reconciliation (Valid signature / bypass)
        # Using dummy/empty secret ensures signature bypass is active
        monkeypatch.setattr("backend.api.webhook.settings.RAZORPAY_WEBHOOK_SECRET", "dummy_razorpay_webhook_secret")

        webhook_payload = {
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {
                    "entity": {
                        "id": link_id,
                        "status": "paid",
                        "amount": 50000,
                        "currency": "INR"
                    }
                },
                "payment": {
                    "entity": {
                        "id": "pay_mock_123"
                    }
                }
            }
        }

        headers = {
            "x-razorpay-signature": "dummy-sig",
            "x-razorpay-event-id": f"evt-{uuid4()}"
        }
        resp = await api_client.post(
            "/api/v1/razorpay",
            json=webhook_payload,
            headers=headers
        )
        assert resp.status_code == 200

        # Verify payment status became paid/completed
        async with db_client.acquire() as conn:
            pay_req_updated = await conn.fetchrow(
                "SELECT * FROM payment_requests WHERE id = $1",
                pay_req["id"]
            )
            assert pay_req_updated["status"] == "paid"

        # 5. Verify Webhook Signature Check rejection (Bypass inactive)
        monkeypatch.setattr("backend.api.webhook.settings.RAZORPAY_WEBHOOK_SECRET", "real_secret_key")
        # Ensure allow_mock_billing returns False or settings.BILLING_MODE is live/etc
        monkeypatch.setattr("backend.api.webhook.settings.BILLING_MODE", "live")

        resp = await api_client.post(
            "/api/v1/razorpay",
            json=webhook_payload,
            headers=headers
        )
        # Should be forbidden/bad signature
        assert resp.status_code == 400

    finally:
        app.dependency_overrides.clear()


async def test_reminder_scheduling_and_dispatch(api_client, tenant_factory, mock_redis_globally, mock_llm_globally):
    # 1. Create tenant
    tenant_info = await tenant_factory(name="Reminder Clinic", plan_code="enterprise")
    tenant_id = tenant_info["tenant_id"]
    user_id = tenant_info["user_id"]

    async def mock_admin_user():
        return {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role": "admin"
        }
    app.dependency_overrides[get_current_user] = mock_admin_user
    app.dependency_overrides[require_tenant_admin] = mock_admin_user

    try:
        slug = f"reminders-{uuid4().hex[:6]}"
        resp = await api_client.post(
            "/api/v1/awa-chat/links",
            json={"display_name": "Clinic Reminders", "slug": slug}
        )
        assert resp.status_code == 200, resp.text
        created_slug = resp.json()["data"]["slug"]

        # Start customer session
        resp = await api_client.post(
            f"/api/v1/public-chat/links/{created_slug}/sessions",
            json={"visitor_name": "Charlie"}
        )
        assert resp.status_code == 200, resp.text
        session_data = resp.json()["data"]
        session_id = session_data["id"]
        conversation_id = session_data["conversation_id"]

        # Query the DB to find customer_id and insert scheduled message
        async with db_client.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT customer_id FROM public_chat_sessions WHERE id = $1",
                session_id
            )
            customer_id = row["customer_id"]

            msg_id = uuid4()
            # Insert scheduled message with scheduled_for in the past and channel='awa_chat'
            await conn.execute(
                """
                INSERT INTO scheduled_messages (id, tenant_id, customer_id, conversation_id, channel, message_text, scheduled_for, status, attempt_count)
                VALUES ($1, $2, $3, $4, 'email', 'This is a scheduled reminder.', NOW() - INTERVAL '1 hour', 'scheduled', 0)
                """,
                msg_id, tenant_id, customer_id, conversation_id
            )

        # 2. Run the scheduler dispatch loop once
        from backend.pipeline.reminder_scheduler import dispatch_due_reminders_once
        count = await dispatch_due_reminders_once()
        assert count > 0

        # 3. Verify status changed to sent
        async with db_client.acquire() as conn:
            reminder = await conn.fetchrow(
                "SELECT * FROM scheduled_messages WHERE id = $1",
                msg_id
            )
            assert reminder["status"] == "sent"

    finally:
        app.dependency_overrides.clear()


async def test_review_collection_and_escalation(api_client, tenant_factory, mock_redis_globally, mock_llm_globally):
    # 1. Create tenant
    tenant_info = await tenant_factory(
        name="Review Clinic",
        plan_code="enterprise",
        modules_config={
            "reviews": {"enabled": True},
            "support": {"enabled": True},
            "human_handoff": {"enabled": True},
        },
    )
    tenant_id = tenant_info["tenant_id"]
    user_id = tenant_info["user_id"]

    async def mock_admin_user():
        return {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role": "admin"
        }
    app.dependency_overrides[get_current_user] = mock_admin_user
    app.dependency_overrides[require_tenant_admin] = mock_admin_user

    try:
        customer_id = uuid4()
        review_req_id = uuid4()
        conversation_id = uuid4()

        # Create customer, conversation, and review request
        async with db_client.acquire() as conn:
            await conn.execute(
                "INSERT INTO customers (id, tenant_id, phone_number, name) VALUES ($1, $2, $3, $4)",
                customer_id, tenant_id, "+15558887777", "Diana"
            )
            await conn.execute(
                "INSERT INTO conversations (id, tenant_id, customer_id, status) VALUES ($1, $2, $3, 'active')",
                conversation_id, tenant_id, customer_id
            )
            await conn.execute(
                """
                INSERT INTO review_requests (id, tenant_id, customer_id, conversation_id, status, source)
                VALUES ($1, $2, $3, $4, 'sent', 'manual')
                """,
                review_req_id, tenant_id, customer_id, conversation_id
            )

        # 2. Dianna submits a low rating (e.g. 2 stars)
        resp = await api_client.post(
            f"/api/v1/reviews/{review_req_id}/actions",
            json={
                "action": "submit_rating",
                "rating": 2,
                "feedback_text": "Very slow service."
            }
        )
        assert resp.status_code == 200, resp.text

        # Dispatch the business events (like review.negative)
        from backend.pipeline.event_dispatcher import dispatch_events_once
        dispatched_count = await dispatch_events_once()
        assert dispatched_count > 0

        # 3. Verify status changed to negative_feedback_escalated
        async with db_client.acquire() as conn:
            review = await conn.fetchrow(
                "SELECT * FROM review_requests WHERE id = $1",
                review_req_id
            )
            assert review["status"] == "negative_feedback_escalated"
            assert review["rating"] == 2

            # Check that the conversation itself has been escalated in the database!
            conv = await conn.fetchrow(
                "SELECT * FROM conversations WHERE id = $1",
                conversation_id
            )
            assert conv["status"] == "escalated"

    finally:
        app.dependency_overrides.clear()
