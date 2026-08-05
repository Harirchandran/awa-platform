import pytest
from uuid import uuid4
import json
from datetime import date, datetime, timedelta

from backend.api.dependencies import get_current_user
from backend.api.app import app
from backend.db.client import db_client
from backend.pipeline.worker import process_message
from backend.pipeline.session_manager import session_manager
from backend.core.states import ConversationState
from backend.modules.booking_module import booking_module

pytestmark = pytest.mark.anyio

async def test_public_chat_basic_message_flow(api_client, tenant_factory, mock_redis_globally, mock_llm_globally):
    # 1. Create a tenant under enterprise plan and authenticate
    tenant_info = await tenant_factory(name="Public Chat Clinic", plan_code="enterprise")
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
        # 2. Create public chat link
        slug = f"chat-link-{uuid4().hex[:6]}"
        resp = await api_client.post(
            "/api/v1/awa-chat/links",
            json={"display_name": "Chat Link", "slug": slug}
        )
        assert resp.status_code == 200, resp.text
        link_data = resp.json()["data"]
        created_slug = link_data["slug"]
        assert created_slug.startswith(slug)

        # 3. Start public chat session as a customer
        visitor_name = "Jane Doe"
        resp = await api_client.post(
            f"/api/v1/public-chat/links/{created_slug}/sessions",
            json={"visitor_name": visitor_name}
        )
        assert resp.status_code == 200, resp.text
        session_data = resp.json()["data"]
        session_id = session_data["id"]
        session_token = session_data["session_token"]
        conversation_id = session_data["conversation_id"]

        # 4. Send basic message
        message_text = "Hello clinic!"
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={
                "session_token": session_token,
                "message_text": message_text
            }
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "queued"

        # 5. Extract message from Redis queue and process it via the worker
        queue_key = "message_queue"
        assert queue_key in mock_redis_globally.data
        assert len(mock_redis_globally.data[queue_key]) == 1
        raw_msg = mock_redis_globally.data[queue_key].pop()

        # Run process_message
        success = await process_message(raw_msg)
        assert success is True

        # 6. Verify message was persisted in the DB
        async with db_client.acquire() as conn:
            # Check inbound message
            inbound = await conn.fetchrow(
                "SELECT * FROM messages WHERE tenant_id = $1 AND conversation_id = $2 AND direction = 'inbound'",
                tenant_id, conversation_id
            )
            assert inbound is not None
            assert inbound["message_text"] == message_text

            # Check outbound response was persisted
            outbounds = await conn.fetch(
                "SELECT * FROM messages WHERE tenant_id = $1 AND conversation_id = $2 AND direction = 'outbound' ORDER BY created_at DESC",
                tenant_id, conversation_id
            )
            assert len(outbounds) > 0
            outbound_texts = [o["message_text"].lower() for o in outbounds]
            assert any(
                "help with that" in txt
                or "mock response" in txt
                or "welcome" in txt
                or "what would you like to do" in txt
                or "i can help with" in txt
                for txt in outbound_texts
            )

        # 7. Verify tenant scope and security: another tenant cannot read these messages
        another_tenant_info = await tenant_factory(name="Other Clinic", plan_code="enterprise")
        async def mock_other_admin():
            return {
                "user_id": another_tenant_info["user_id"],
                "tenant_id": another_tenant_info["tenant_id"],
                "role": "admin"
            }
        app.dependency_overrides[get_current_user] = mock_other_admin

        # Attempt to list messages from the first session (should be rejected/empty or fail session check)
        # Using the visitor session token checks
        # Let's verify listing messages with mismatched session token fails
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages/list",
            json={"session_token": "mismatched-or-invalid-token"}
        )
        assert resp.status_code == 404

    finally:
        app.dependency_overrides.clear()


async def test_lead_capture_flow(api_client, tenant_factory, mock_redis_globally, mock_llm_globally):
    # 1. Create a tenant under enterprise plan and authenticate
    tenant_info = await tenant_factory(name="Lead Capture Clinic", plan_code="enterprise")
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
        slug = f"leads-{uuid4().hex[:6]}"
        resp = await api_client.post(
            "/api/v1/awa-chat/links",
            json={"display_name": "Clinic Leads", "slug": slug}
        )
        assert resp.status_code == 200, resp.text
        created_slug = resp.json()["data"]["slug"]

        # Start customer session
        resp = await api_client.post(
            f"/api/v1/public-chat/links/{created_slug}/sessions",
            json={"visitor_name": "Alice"}
        )
        assert resp.status_code == 200, resp.text
        session_data = resp.json()["data"]
        session_id = session_data["id"]
        session_token = session_data["session_token"]
        conversation_id = session_data["conversation_id"]

        # Alice expresses interest with all details
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={
                "session_token": session_token,
                "message_text": "I am interested in Dental cleanups. My name is Alice Lead and my phone is +15551112222."
            }
        )
        assert resp.status_code == 200
        raw_msg = mock_redis_globally.data["message_queue"].pop()

        await process_message(raw_msg)

        # Verify lead has been captured in the database
        async with db_client.acquire() as conn:
            lead = await conn.fetchrow(
                "SELECT * FROM leads WHERE tenant_id = $1 AND name = $2",
                tenant_id, "Alice Lead"
            )
            assert lead is not None
            assert lead["interest"] == "Dental cleanups"
            assert lead["status"] == "new"

        # Verify cross-tenant isolation: listing leads from another tenant returns empty
        another_tenant_info = await tenant_factory(name="Other Leads Clinic", plan_code="enterprise")
        async def mock_other_admin():
            return {
                "user_id": another_tenant_info["user_id"],
                "tenant_id": another_tenant_info["tenant_id"],
                "role": "admin"
            }
        app.dependency_overrides[get_current_user] = mock_other_admin

        # Listing leads should not show first tenant's lead
        resp = await api_client.get("/api/v1/leads")
        assert resp.status_code == 200
        leads_list = resp.json()["data"]
        for l in leads_list:
            assert l["name"] != "Alice Lead"

    finally:
        app.dependency_overrides.clear()


async def test_configured_booking_fields_persist_through_public_chat_worker(
    api_client, tenant_factory, mock_redis_globally, mock_llm_globally, monkeypatch,
):
    """Real API/intake/worker/session path for one-field configured booking collection."""
    controlled_today = date(2026, 7, 14)
    visit_date_value = (controlled_today + timedelta(days=6)).isoformat()

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls.combine(controlled_today, datetime.min.time()).replace(hour=9)
            return value.replace(tzinfo=tz) if tz is not None else value

    monkeypatch.setattr("backend.modules.booking_module.datetime", FrozenDateTime)
    monkeypatch.setattr(booking_module, "_tenant_today", lambda _: controlled_today)
    tenant_info = await tenant_factory(name="Configured Booking Clinic", plan_code="enterprise")
    tenant_id, user_id = tenant_info["tenant_id"], tenant_info["user_id"]
    config = tenant_info["config_data"]
    config["services"] = [{"id": "retest", "name": "Retest Consultation", "duration_mins": 30, "price": 0, "enabled": True}]
    config["modules"]["booking"]["fields"] = [
        {"key": "guest_display_name", "label": "Guest", "type": "string", "required": True},
        {"key": "visit_date", "label": "Visit date", "type": "date", "required": True},
        {"key": "visit_time", "label": "Visit time", "type": "time", "required": True},
    ]
    async with db_client.acquire() as conn:
        await conn.execute(
            "UPDATE tenant_configs SET config_json = $2 WHERE tenant_id = $1 AND is_active = true",
            tenant_id, json.dumps(config),
        )

    async def mock_admin_user():
        return {"user_id": user_id, "tenant_id": tenant_id, "role": "admin"}
    app.dependency_overrides[get_current_user] = mock_admin_user

    try:
        slug = f"configured-booking-{uuid4().hex[:6]}"
        link = await api_client.post("/api/v1/awa-chat/links", json={"display_name": "Configured Booking", "slug": slug})
        assert link.status_code == 200, link.text
        slug = link.json()["data"]["slug"]
        session_start = await api_client.post(
            f"/api/v1/public-chat/links/{slug}/sessions", json={"visitor_name": "Alex"},
        )
        assert session_start.status_code == 200, session_start.text
        session_data = session_start.json()["data"]
        session_id, session_token, conversation_id = session_data["id"], session_data["session_token"], session_data["conversation_id"]

        async def customer_turn(text):
            response = await api_client.post(
                f"/api/v1/public-chat/sessions/{session_id}/messages",
                json={"session_token": session_token, "message_text": text},
            )
            assert response.status_code == 200, response.text
            raw = mock_redis_globally.data["message_queue"].pop()
            assert await process_message(raw) is True
            return await session_manager.get_or_create_session(tenant_id, conversation_id)

        initial = await customer_turn("Book appointment")
        assert initial.state.prompt_focus.requested_fields == ["service_name"]

        service = await customer_turn("Retest Consultation")
        assert service.state.get_task_step_data("booking") == {
            "service_id": "retest", "service_name": "Retest Consultation",
        }
        assert service.state.prompt_focus.requested_fields == ["guest_display_name"]

        name = await customer_turn("Alex")
        assert name.state.get_task_step_data("booking")["guest_display_name"] == "Alex"
        assert "visit_date" not in name.state.get_task_step_data("booking")
        assert name.state.prompt_focus.requested_fields == ["visit_date"]

        visit_date = await customer_turn(visit_date_value)
        assert visit_date.state.get_task_step_data("booking")["service_name"] == "Retest Consultation"
        assert visit_date.state.get_task_step_data("booking")["service_id"] == "retest"
        assert visit_date.state.get_task_step_data("booking")["guest_display_name"] == "Alex"
        assert visit_date.state.get_task_step_data("booking")["visit_date"] == visit_date_value
        assert "visit_time" not in visit_date.state.get_task_step_data("booking")
        assert visit_date.state.prompt_focus.requested_fields == ["visit_time"]

        # Require a database rehydrate rather than a cached envelope before the
        # temporal final field is accepted.
        await session_manager.invalidate_cache(tenant_id, conversation_id)
        rehydrated = await session_manager.get_or_create_session(tenant_id, conversation_id)
        assert rehydrated.state.get_task_step_data("booking")["visit_date"] == visit_date_value

        visit_time = await customer_turn("10:00")
        assert visit_time.state.get_task_step_data("booking")["visit_time"] == "10:00"
        assert visit_time.state.action_ledger.pending_action is not None
        assert visit_time.state.action_ledger.task_status == "awaiting_confirmation"
        assert visit_time.state.action_ledger.pending_action.args == {
            "service_id": "retest",
            "service_name": "Retest Consultation",
            "guest_display_name": "Alex",
            "visit_date": visit_date_value,
            "visit_time": "10:00",
        }
        assert "Retest Consultation" in visit_time.state.action_ledger.pending_action.summary
        assert "guest_display_name: Alex" in visit_time.state.action_ledger.pending_action.summary
        assert "service_id" not in visit_time.state.action_ledger.pending_action.summary
        async with db_client.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM bookings WHERE tenant_id = $1", tenant_id) == 0

        confirm = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={"session_token": session_token, "message_text": "Confirm"},
        )
        assert confirm.status_code == 403  # verified-customer gate owns commitment
        confirmation = await session_manager.get_or_create_session(tenant_id, conversation_id)
        assert confirmation.state.action_ledger.pending_action is not None
        async with db_client.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM bookings WHERE tenant_id = $1", tenant_id) == 0
    finally:
        app.dependency_overrides.clear()
