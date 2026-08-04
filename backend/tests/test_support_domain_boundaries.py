from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.domains.support.repository import SupportRepository
from backend.domains.support.service import SupportService
from backend.modules.human_handoff_module import human_handoff_module
from backend.modules.support_module import support_module


pytestmark = pytest.mark.anyio


class FakeEvents:
    def __init__(self):
        self.emitted = []

    async def emit(self, event):
        self.emitted.append(event)
        return {"id": uuid4(), "event_type": event.event_type}


class TenantScopedSupportDb:
    def __init__(self, tenant_id):
        self.tenant_id = tenant_id
        self.conversation_id = uuid4()
        self.customer_id = uuid4()
        self.list_conversations = AsyncMock(side_effect=self._list_conversations)
        self.get_conversation = AsyncMock(side_effect=self._get_conversation)
        self.set_conversation_escalation = AsyncMock(side_effect=self._escalate)
        self.clear_conversation_escalation = AsyncMock(side_effect=self._clear)
        self.update_conversation_status = AsyncMock(side_effect=self._update_status)
        self.save_message = AsyncMock()
        self.update_message_status = AsyncMock()

    async def _list_conversations(self, tenant_id, **kwargs):
        items = [{"id": self.conversation_id, "tenant_id": self.tenant_id, "status": "escalated"}] if tenant_id == self.tenant_id else []
        return {"items": items, "next_cursor": None}

    async def _get_conversation(self, tenant_id, conversation_id):
        if tenant_id == self.tenant_id and conversation_id == self.conversation_id:
            return {"id": conversation_id, "tenant_id": tenant_id, "customer_id": self.customer_id, "status": "escalated"}
        return None

    async def _escalate(self, tenant_id, conversation_id, escalation_reason=None, assigned_to=None, resume_mode=None):
        if tenant_id == self.tenant_id and conversation_id == self.conversation_id:
            return {"id": conversation_id, "tenant_id": tenant_id, "customer_id": self.customer_id, "status": "escalated", "escalation_reason": escalation_reason}
        return None

    async def _clear(self, tenant_id, conversation_id, status="active", resume_mode=None):
        if tenant_id == self.tenant_id and conversation_id == self.conversation_id:
            return {"id": conversation_id, "tenant_id": tenant_id, "customer_id": self.customer_id, "status": status}
        return None

    async def _update_status(self, tenant_id, conversation_id, status):
        return tenant_id == self.tenant_id and conversation_id == self.conversation_id


def _context(module_name: str, step_data: dict):
    tenant_id = uuid4()
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Clinic", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+919999999999", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module=module_name,
            state=SessionState(workflow_state=ConversationState.EXECUTING, step_data=step_data),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={"modules": {module_name: {"enabled": True}}}),
        incoming_message={"text": "help", "channel_type": "awa_chat"},
    )


async def test_support_repository_methods_are_tenant_scoped():
    tenant_a = uuid4()
    tenant_b = uuid4()
    db = TenantScopedSupportDb(tenant_a)
    repo = SupportRepository(db)

    assert (await repo.list_escalated_conversations(tenant_a))["items"]
    assert (await repo.list_escalated_conversations(tenant_b))["items"] == []
    assert await repo.get_conversation(tenant_a, db.conversation_id)
    assert await repo.get_conversation(tenant_b, db.conversation_id) is None
    assert await repo.escalate_conversation(tenant_a, db.conversation_id, reason="help")
    assert await repo.escalate_conversation(tenant_b, db.conversation_id, reason="help") is None
    assert await repo.update_conversation_status(tenant_a, db.conversation_id, "closed") is True
    assert await repo.update_conversation_status(tenant_b, db.conversation_id, "closed") is False


async def test_tenant_a_cannot_send_human_reply_into_tenant_b_conversation():
    tenant_a = uuid4()
    tenant_b = uuid4()
    db = TenantScopedSupportDb(tenant_a)
    service = SupportService(repository=SupportRepository(db))

    saved = await service.save_human_reply(
        tenant_id=tenant_b,
        conversation_id=db.conversation_id,
        provider_message_id="tenant_reply_test",
        channel_type="awa_chat",
        message_text="Hello",
        raw_payload={"source": "test"},
        delivery_status="sent",
    )

    assert saved is False
    db.save_message.assert_not_awaited()


async def test_support_service_emits_handoff_created_only_after_successful_escalation():
    tenant_id = uuid4()
    conversation_id = uuid4()
    customer_id = uuid4()
    events = FakeEvents()
    repo = SimpleNamespace(
        escalate_conversation=AsyncMock(return_value={"id": conversation_id, "customer_id": customer_id, "status": "escalated"})
    )
    service = SupportService(repository=repo, events=events)

    result = await service.escalate_conversation(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        reason="human requested",
        source="customer",
        source_channel="awa_chat",
    )

    assert result["status"] == "escalated"
    assert len(events.emitted) == 1
    event = events.emitted[0]
    assert event.event_type == "handoff.created"
    assert event.tenant_id == tenant_id
    assert event.payload["conversation_id"] == str(conversation_id)
    assert event.payload["source"] == "customer"

    repo.escalate_conversation.return_value = None
    await service.escalate_conversation(
        tenant_id=tenant_id,
        conversation_id=uuid4(),
        reason="missing",
        source="operator",
    )
    assert len(events.emitted) == 1


async def test_human_handoff_module_uses_support_service(monkeypatch):
    context = _context("human_handoff", {"reason": "Need a person"})
    service = SimpleNamespace(create_handoff=AsyncMock(return_value=({"status": "escalated"}, {"routing": "in_app"})))
    monkeypatch.setattr("backend.modules.human_handoff_module.support_service", service)

    result = await human_handoff_module.execute(context)

    assert result.success is True
    assert result.created_resource_type == "conversation_escalation"
    service.create_handoff.assert_awaited_once()
    assert service.create_handoff.await_args.kwargs["tenant_id"] == context.tenant.id


async def test_support_module_uses_support_service(monkeypatch):
    context = _context("support", {"issue_description": "Need refund urgently", "urgency": "high"})
    service = SimpleNamespace(create_support_request=AsyncMock(return_value=True))
    monkeypatch.setattr("backend.modules.support_module.support_service", service)

    result = await support_module.execute(context)

    assert result is True
    service.create_support_request.assert_awaited_once()
    assert service.create_support_request.await_args.kwargs["tenant_id"] == context.tenant.id
    assert service.create_support_request.await_args.kwargs["issue_description"] == "Need refund urgently"
