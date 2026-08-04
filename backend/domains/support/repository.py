from typing import Any, Dict, Optional
from uuid import UUID

from backend.db.client import db_client


class SupportRepository:
    def __init__(self, db=db_client):
        self.db = db

    async def list_escalated_conversations(
        self,
        tenant_id: UUID,
        cursor: Optional[str] = None,
        limit: int = 30,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.db.list_conversations(
            tenant_id,
            cursor=cursor,
            limit=limit,
            status="escalated",
            search=search,
        )

    async def get_conversation(self, tenant_id: UUID, conversation_id: UUID) -> Optional[Dict[str, Any]]:
        return await self.db.get_conversation(tenant_id, conversation_id)

    async def escalate_conversation(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        reason: Optional[str] = None,
        assigned_to: Optional[UUID] = None,
        resume_mode: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return await self.db.set_conversation_escalation(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            escalation_reason=reason,
            assigned_to=assigned_to,
            resume_mode=resume_mode,
        )

    async def return_to_awa(self, tenant_id: UUID, conversation_id: UUID) -> Optional[Dict[str, Any]]:
        return await self.db.clear_conversation_escalation(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            status="active",
            resume_mode="tenant",
        )

    async def resolve_conversation(self, tenant_id: UUID, conversation_id: UUID) -> Optional[Dict[str, Any]]:
        return await self.db.clear_conversation_escalation(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            status="closed",
            resume_mode="tenant",
        )

    async def update_conversation_status(self, tenant_id: UUID, conversation_id: UUID, status: str) -> bool:
        return await self.db.update_conversation_status(tenant_id, conversation_id, status)

    async def write_support_audit_log(
        self,
        tenant_id: UUID,
        actor_id: UUID,
        conversation_id: UUID,
        details: Dict[str, Any],
    ) -> None:
        await self.db.write_audit_log(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="support_ticket_created",
            resource_type="conversation",
            resource_id=conversation_id,
            details=details,
        )

    async def get_public_chat_session_for_conversation(self, tenant_id: UUID, conversation_id: UUID) -> Optional[Dict[str, Any]]:
        return await self.db.get_public_chat_session_for_conversation(tenant_id, conversation_id)

    async def save_human_reply(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        provider_message_id: str,
        channel_type: str,
        message_text: str,
        raw_payload: Dict[str, Any],
        delivery_status: str,
    ) -> None:
        await self.db.save_message(
            message_id=message_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            whatsapp_message_id=provider_message_id,
            provider_message_id=provider_message_id,
            channel_type=channel_type,
            direction="outbound",
            message_type="text",
            message_text=message_text,
            raw_payload=raw_payload,
        )
        await self.db.update_message_status(tenant_id, provider_message_id, delivery_status)

    async def get_whatsapp_account_for_dispatch(self, tenant_id: UUID, channel_account_id: UUID) -> Optional[Dict[str, Any]]:
        return await self.db.get_whatsapp_account_for_dispatch(tenant_id, channel_account_id)

    async def create_knowledge_base_entry(self, tenant_id: UUID, **kwargs) -> Optional[Dict[str, Any]]:
        return await self.db.create_knowledge_base_entry(tenant_id=tenant_id, **kwargs)


support_repository = SupportRepository()
