import pytest
from uuid import uuid4
from langchain_core.messages import AIMessage
import json

from backend.api.dependencies import get_current_user
from backend.api.app import app
from backend.db.client import db_client
from backend.pipeline.worker import process_message
from backend.core.states import ConversationState
from backend.domains.support.service import support_service
from backend.logic.engine.llm import DeterministicFakeToolCallingLLM

pytestmark = pytest.mark.anyio

async def test_human_handoff_flow(api_client, tenant_factory, mock_redis_globally, mock_llm_globally):
    # 1. Create tenant and authenticate
    tenant_info = await tenant_factory(name="Handoff Clinic", plan_code="enterprise")
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
        # Create public chat link
        slug = f"handoff-{uuid4().hex[:6]}"
        resp = await api_client.post(
            "/api/v1/awa-chat/links",
            json={"display_name": "Handoff Chat", "slug": slug}
        )
        assert resp.status_code == 200, resp.text
        created_slug = resp.json()["data"]["slug"]

        # Start customer session
        resp = await api_client.post(
            f"/api/v1/public-chat/links/{created_slug}/sessions",
            json={"visitor_name": "Hannah"}
        )
        assert resp.status_code == 200, resp.text
        session_data = resp.json()["data"]
        session_id = session_data["id"]
        session_token = session_data["session_token"]
        conversation_id = session_data["conversation_id"]

        # 2. Hannah requests a human agent
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={
                "session_token": session_token,
                "message_text": "I want to talk to a human agent, please."
            }
        )
        assert resp.status_code == 200, resp.text
        raw_msg = mock_redis_globally.data["message_queue"].pop()

        # Process the handoff request
        await process_message(raw_msg)

        # Verify state transitions to ESCALATED
        from backend.pipeline.session_manager import session_manager
        session = await session_manager.get_or_create_session(tenant_id, conversation_id)
        assert session.state.workflow_state == ConversationState.ESCALATED

        # Verify handoff escalation status persisted in conversation
        async with db_client.acquire() as conn:
            conv = await conn.fetchrow("SELECT * FROM conversations WHERE id = $1", conversation_id)
            assert conv["status"] == "escalated"
            assert "human" in conv["escalation_reason"].lower()

            # Record number of outbound messages in DB
            outbounds_initial = await conn.fetch(
                "SELECT * FROM messages WHERE tenant_id = $1 AND conversation_id = $2 AND direction = 'outbound'",
                tenant_id, conversation_id
            )
            count_initial = len(outbounds_initial)
            assert count_initial > 0

        # 3. Hannah sends a subsequent message while escalated
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={
                "session_token": session_token,
                "message_text": "Hello, is anyone there?"
            }
        )
        assert resp.status_code == 200, resp.text
        raw_msg2 = mock_redis_globally.data["message_queue"].pop()

        # Process the message
        await process_message(raw_msg2)

        # Verify that subsequent AI responses were suppressed
        async with db_client.acquire() as conn:
            outbounds_after = await conn.fetch(
                "SELECT * FROM messages WHERE tenant_id = $1 AND conversation_id = $2 AND direction = 'outbound'",
                tenant_id, conversation_id
            )
            assert len(outbounds_after) == count_initial  # Count must not have changed!

    finally:
        app.dependency_overrides.clear()


async def test_support_ticket_flow(api_client, tenant_factory, mock_redis_globally, mock_llm_globally, monkeypatch):
    # 1. Create tenant and authenticate
    tenant_info = await tenant_factory(name="Support Clinic", plan_code="enterprise")
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
        # Create public chat link
        slug = f"support-{uuid4().hex[:6]}"
        resp = await api_client.post(
            "/api/v1/awa-chat/links",
            json={"display_name": "Support Chat", "slug": slug}
        )
        assert resp.status_code == 200, resp.text
        created_slug = resp.json()["data"]["slug"]

        # Start customer session
        resp = await api_client.post(
            f"/api/v1/public-chat/links/{created_slug}/sessions",
            json={"visitor_name": "Steve"}
        )
        assert resp.status_code == 200, resp.text
        session_data = resp.json()["data"]
        session_id = session_data["id"]
        session_token = session_data["session_token"]
        conversation_id = session_data["conversation_id"]

        # 2. Monkeypatch DeterministicFakeToolCallingLLM to yield support_execute
        async def mock_support_ainvoke(self_instance, messages, *args, **kwargs):
            from langchain_core.messages import ToolMessage
            if messages and isinstance(messages[-1], ToolMessage):
                return AIMessage(content="I have filed that support ticket for you.")
            return AIMessage(content="", tool_calls=[{
                "name": "support_execute",
                "args": {
                    "issue_description": "Scheduler is showing wrong timezone for clinic bookings.",
                    "urgency": "high"
                },
                "id": "fake_call_support"
            }])
        monkeypatch.setattr(DeterministicFakeToolCallingLLM, "ainvoke", mock_support_ainvoke)

        # Steve sends support request
        resp = await api_client.post(
            f"/api/v1/public-chat/sessions/{session_id}/messages",
            json={
                "session_token": session_token,
                "message_text": "I want to file a support request about scheduling timezone."
            }
        )
        assert resp.status_code == 200, resp.text
        raw_msg = mock_redis_globally.data["message_queue"].pop()

        await process_message(raw_msg)

        # 3. Verify support ticket and handoff are persisted in DB
        async with db_client.acquire() as conn:
            conv = await conn.fetchrow("SELECT * FROM conversations WHERE id = $1", conversation_id)
            assert conv["status"] == "escalated"
            assert "wrong timezone" in conv["escalation_reason"]

            # Verify audit log was emitted
            audit = await conn.fetchrow(
                "SELECT * FROM audit_logs WHERE tenant_id = $1 AND action = 'support_ticket_created'",
                tenant_id
            )
            assert audit is not None
            details = json.loads(audit["details"]) if isinstance(audit["details"], str) else audit["details"]
            assert details["urgency"] == "high"

        # 4. Verify admin listing of escalations is scoped by tenant (tenant isolation)
        escalations = await support_service.list_escalated_conversations(tenant_id)
        assert len(escalations["items"]) == 1
        assert str(escalations["items"][0]["id"]) == str(conversation_id)

        # Verify another tenant sees nothing
        other_tenant = await tenant_factory(name="Other Clinic", plan_code="enterprise")
        other_escalations = await support_service.list_escalated_conversations(other_tenant["tenant_id"])
        assert len(other_escalations["items"]) == 0

    finally:
        app.dependency_overrides.clear()
