from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from backend.api.routes import conversations as conversation_routes
pytestmark = pytest.mark.anyio

@pytest.fixture(autouse=True)
def allow_escalation_entitlements(monkeypatch):
    monkeypatch.setattr(conversation_routes, "require_any_module_operational_write", AsyncMock())
    monkeypatch.setattr(conversation_routes, "require_module_config_write", AsyncMock())

async def test_list_escalations_filters_to_escalated(monkeypatch):
    calls = {}
    async def fake_list_escalated_conversations(tenant_id, cursor=None, limit=30, search=None):
        calls.update({
            "tenant_id": tenant_id,
            "cursor": cursor,
            "limit": limit,
            "search": search,
        })
        return {"items": [{"id": str(uuid4()), "status": "escalated"}], "next_cursor": None}
    monkeypatch.setattr(conversation_routes, "support_service", SimpleNamespace(list_escalated_conversations=fake_list_escalated_conversations))
    tenant_id = uuid4()
    response = await conversation_routes.list_escalations(
        limit=15,
        search="Asha",
        user={"tenant_id": tenant_id, "user_id": uuid4()},
    )
    assert response["data"][0]["status"] == "escalated"
    assert calls == {
        "tenant_id": tenant_id,
        "cursor": None,
        "limit": 15,
        "search": "Asha",
    }
async def test_return_to_awa_clears_escalation_and_resumes_session(monkeypatch):
    tenant_id = uuid4()
    conversation_id = uuid4()
    updated_state = {}
    invalidated = {}
    async def fake_clear_conversation_escalation(tenant_id, conversation_id_arg):
        updated_state["clear"] = {"tenant_id": tenant_id, "conversation_id": conversation_id_arg, "status": "active", "resume_mode": "tenant"}
        return {"id": conversation_id, "status": "active", "resume_mode": "tenant"}
    async def fake_get_conversation_session(_tenant_id, _conversation_id):
        return {
            "config_version": 3,
            "state_json": {
                "config_version": 3,
                "state": {"workflow_state": "ESCALATED", "step_data": {"foo": "bar"}},
            },
            "current_module": "support",
            "current_intent": "support",
        }
    async def fake_update_conversation_session(**kwargs):
        updated_state["session"] = kwargs
        return True
    async def fake_invalidate_cache(_tenant_id, _conversation_id):
        invalidated["tenant_id"] = _tenant_id
        invalidated["conversation_id"] = _conversation_id
    monkeypatch.setattr(
        conversation_routes,
        "db_client",
        SimpleNamespace(
            get_conversation_session=fake_get_conversation_session,
            update_conversation_session=fake_update_conversation_session,
        ),
    )
    monkeypatch.setattr(conversation_routes, "support_service", SimpleNamespace(return_to_awa=fake_clear_conversation_escalation))
    monkeypatch.setattr(conversation_routes, "session_manager", SimpleNamespace(invalidate_cache=fake_invalidate_cache))
    response = await conversation_routes.return_escalation_to_awa(
        conversation_id,
        user={"tenant_id": tenant_id, "user_id": uuid4()},
    )
    assert response["data"]["status"] == "active"
    assert updated_state["clear"]["status"] == "active"
    assert updated_state["clear"]["resume_mode"] == "tenant"
    assert updated_state["session"]["state_json"]["state"]["workflow_state"] == "INTENT_IDENTIFIED"
    assert updated_state["session"]["state_json"]["state"]["step_data"] == {}
    assert updated_state["session"]["current_module"] is None
    assert invalidated == {"tenant_id": tenant_id, "conversation_id": conversation_id}
async def test_resolve_escalation_closes_and_completes_session(monkeypatch):
    tenant_id = uuid4()
    conversation_id = uuid4()
    updated_state = {}
    async def fake_clear_conversation_escalation(tenant_id, conversation_id_arg):
        updated_state["clear"] = {"tenant_id": tenant_id, "conversation_id": conversation_id_arg, "status": "closed", "resume_mode": "tenant"}
        return {"id": conversation_id, "status": "closed", "resume_mode": "tenant"}
    async def fake_get_conversation_session(_tenant_id, _conversation_id):
        return {"config_version": 1, "state_json": {"config_version": 1, "state": {"workflow_state": "ESCALATED"}}}
    async def fake_update_conversation_session(**kwargs):
        updated_state["session"] = kwargs
        return True
    async def fake_invalidate_cache(_tenant_id, _conversation_id):
        return None
    monkeypatch.setattr(
        conversation_routes,
        "db_client",
        SimpleNamespace(
            get_conversation_session=fake_get_conversation_session,
            update_conversation_session=fake_update_conversation_session,
        ),
    )
    monkeypatch.setattr(conversation_routes, "support_service", SimpleNamespace(resolve_conversation=fake_clear_conversation_escalation))
    monkeypatch.setattr(conversation_routes, "session_manager", SimpleNamespace(invalidate_cache=fake_invalidate_cache))
    response = await conversation_routes.resolve_escalation(
        conversation_id,
        user={"tenant_id": tenant_id, "user_id": uuid4()},
    )
    assert response["data"]["status"] == "closed"
    assert updated_state["clear"]["status"] == "closed"
    assert updated_state["session"]["state_json"]["state"]["workflow_state"] == "COMPLETED"
async def test_reply_to_escalation_sends_awa_chat_and_saves_knowledge(monkeypatch):
    tenant_id = uuid4()
    user_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    saved = {}
    async def fake_get_conversation(_tenant_id, _conversation_id):
        return {
            "id": conversation_id,
            "customer_id": customer_id,
            "status": "escalated",
            "channel_type": "awa_chat",
            "customer_name": "Asha",
            "customer_phone": "Browser chat",
            "customer_email": None,
            "escalation_reason": "What are your late hours?",
        }
    async def fake_get_public_chat_session_for_conversation(_tenant_id, _conversation_id):
        return {"id": uuid4(), "status": "active", "last_seen_at": datetime.now(timezone.utc)}
    async def fake_save_human_reply(**kwargs):
        saved["message"] = kwargs
        saved["status"] = (kwargs["tenant_id"], kwargs["provider_message_id"], kwargs["delivery_status"])
    async def fake_create_knowledge_base_entry(**kwargs):
        saved["knowledge"] = kwargs
        return {"id": uuid4()}
    monkeypatch.setattr(
        conversation_routes,
        "support_service",
        SimpleNamespace(
            get_conversation=fake_get_conversation,
            get_public_chat_session_for_conversation=fake_get_public_chat_session_for_conversation,
            save_human_reply=fake_save_human_reply,
            create_knowledge_base_entry=fake_create_knowledge_base_entry,
        ),
    )
    embed_mock = AsyncMock(return_value=[0.0] * 1536)
    monkeypatch.setattr(conversation_routes, "rag_service", SimpleNamespace(embed_query=embed_mock))
    response = await conversation_routes.reply_to_escalation(
        conversation_id,
        conversation_routes.EscalationReplyRequest(
            message_text="We are open until 8 PM on Fridays.",
            save_to_knowledge=True,
        ),
        user={"tenant_id": tenant_id, "user_id": user_id},
    )
    assert response["data"]["delivery_channel"] == "awa_chat"
    assert response["data"]["delivery_status"] == "sent"
    assert response["data"]["knowledge_saved"] is True
    assert saved["message"]["channel_type"] == "awa_chat"
    assert saved["message"]["message_text"] == "We are open until 8 PM on Fridays."
    assert saved["status"][2] == "sent"
    assert "What are your late hours?" in saved["knowledge"]["chunk_text"]
    embed_mock.assert_awaited_once()
    assert saved["knowledge"]["embedding"] == [0.0] * 1536
    assert saved["knowledge"]["metadata"]["source"] == "tenant_escalation_reply"
    assert saved["knowledge"]["metadata"]["conversation_id"] == str(conversation_id)
    assert saved["knowledge"]["metadata"]["customer_id"] == str(customer_id)
async def test_reply_to_escalation_uses_email_fallback_when_public_chat_closed(monkeypatch):
    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    saved = {}
    async def fake_get_conversation(_tenant_id, _conversation_id):
        return {
            "id": conversation_id,
            "customer_id": customer_id,
            "status": "escalated",
            "channel_type": "awa_chat",
            "customer_name": "Ravi",
            "customer_phone": "Browser chat",
            "customer_email": "ravi@example.com",
            "escalation_reason": "Need a callback",
        }
    async def fake_get_public_chat_session_for_conversation(_tenant_id, _conversation_id):
        return {"id": uuid4(), "status": "closed", "last_seen_at": datetime.now(timezone.utc)}
    async def fake_save_human_reply(**kwargs):
        saved["message"] = kwargs
        saved["status"] = (kwargs["tenant_id"], kwargs["provider_message_id"], kwargs["delivery_status"])
    monkeypatch.setattr(
        conversation_routes,
        "support_service",
        SimpleNamespace(
            get_conversation=fake_get_conversation,
            get_public_chat_session_for_conversation=fake_get_public_chat_session_for_conversation,
            save_human_reply=fake_save_human_reply,
        ),
    )
    response = await conversation_routes.reply_to_escalation(
        conversation_id,
        conversation_routes.EscalationReplyRequest(message_text="The team will call you today."),
        user={"tenant_id": tenant_id, "user_id": uuid4()},
    )
    assert response["data"]["delivery_channel"] == "email"
    assert response["data"]["delivery_status"] == "email_queued"
    assert response["data"]["knowledge_saved"] is False
    assert saved["message"]["channel_type"] == "email"
    assert saved["message"]["raw_payload"]["email_to"] == "ravi@example.com"
async def test_reply_to_escalation_marks_missing_contact_when_public_chat_closed(monkeypatch):
    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    saved = {}
    async def fake_get_conversation(_tenant_id, _conversation_id):
        return {
            "id": conversation_id,
            "customer_id": customer_id,
            "status": "escalated",
            "channel_type": "awa_chat",
            "customer_name": "Nina",
            "customer_phone": "Browser chat",
            "customer_email": None,
            "escalation_reason": "Need details later",
        }
    async def fake_get_public_chat_session_for_conversation(_tenant_id, _conversation_id):
        return {"id": uuid4(), "status": "closed", "last_seen_at": datetime.now(timezone.utc)}
    async def fake_save_human_reply(**kwargs):
        saved["message"] = kwargs
        saved["status"] = (kwargs["tenant_id"], kwargs["provider_message_id"], kwargs["delivery_status"])
    monkeypatch.setattr(
        conversation_routes,
        "support_service",
        SimpleNamespace(
            get_conversation=fake_get_conversation,
            get_public_chat_session_for_conversation=fake_get_public_chat_session_for_conversation,
            save_human_reply=fake_save_human_reply,
        ),
    )
    response = await conversation_routes.reply_to_escalation(
        conversation_id,
        conversation_routes.EscalationReplyRequest(message_text="Please reach out again when you are back."),
        user={"tenant_id": tenant_id, "user_id": uuid4()},
    )
    assert response["data"]["delivery_channel"] == "awa_chat"
    assert response["data"]["delivery_status"] == "pending_customer_contact"
    assert saved["message"]["channel_type"] == "awa_chat"
    assert saved["message"]["raw_payload"]["fallback"] == "awaiting_customer_contact"
    assert saved["status"][2] == "pending_customer_contact"
