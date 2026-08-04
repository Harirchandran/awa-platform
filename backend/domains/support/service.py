from typing import Any, Dict, Optional, Union
from uuid import UUID, uuid4

from backend.domains.events.models import BusinessEventCreate
from backend.domains.events.service import EventService, event_service
from backend.domains.support.repository import SupportRepository, support_repository
from backend.ops.logger import logger
from backend.support.notification_routing import record_support_notification_route


class SupportService:
    def __init__(self, repository: SupportRepository = support_repository, events: Optional[EventService] = None):
        self.repository = repository
        self.events = events

    def _log(self, action: str, tenant_id: UUID, outcome: str, record_id: Optional[UUID] = None, reason_code: Optional[str] = None) -> None:
        extra = {"domain": "support", "action": action, "tenant_id": str(tenant_id), "outcome": outcome}
        if record_id:
            extra["record_id"] = str(record_id)
        if reason_code:
            extra["reason_code"] = reason_code
        logger.debug("domain service call", extra=extra)

    async def list_escalated_conversations(self, tenant_id: UUID, **filters) -> Dict[str, Any]:
        result = await self.repository.list_escalated_conversations(tenant_id, **filters)
        self._log("list_escalated", tenant_id, "success")
        return result

    async def get_conversation(self, tenant_id: UUID, conversation_id: UUID) -> Optional[Dict[str, Any]]:
        conversation = await self.repository.get_conversation(tenant_id, conversation_id)
        self._log("get_conversation", tenant_id, "found" if conversation else "not_found", conversation_id)
        return conversation

    async def escalate_conversation(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        reason: Optional[str] = None,
        assigned_to: Optional[UUID] = None,
        resume_mode: Optional[str] = None,
        source: str = "system",
        source_channel: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        conversation = await self.repository.escalate_conversation(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            reason=reason,
            assigned_to=assigned_to,
            resume_mode=resume_mode,
        )
        if conversation and self.events:
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="handoff.created",
                aggregate_type="conversation",
                aggregate_id=conversation_id,
                source_domain="support",
                conversation_id=conversation_id,
                customer_id=conversation.get("customer_id"),
                payload={
                    "conversation_id": str(conversation_id),
                    "customer_id": str(conversation.get("customer_id")) if conversation.get("customer_id") else None,
                    "source_channel": source_channel,
                    "reason": reason,
                    "source": source,
                    "status": conversation.get("status"),
                },
                correlation_id=str(conversation_id),
            ))
        self._log("escalate", tenant_id, "success" if conversation else "not_found", conversation_id, source)
        return conversation

    async def create_handoff(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        customer_id: UUID,
        actor_id: UUID,
        reason: str,
        config: Dict[str, Any],
        source: str,
        source_channel: Optional[str] = None,
        urgency: str = "high",
        resume_mode: str = "customer",
    ) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        conversation = await self.escalate_conversation(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            reason=reason,
            resume_mode=resume_mode,
            source=source,
            source_channel=source_channel,
        )
        if not conversation:
            return None, None
        route = None
        try:
            route = await record_support_notification_route(
                db_client=self.repository.db,
                tenant_id=tenant_id,
                actor_id=actor_id,
                conversation_id=conversation_id,
                config=config,
                source=source,
                reason=reason,
                urgency=urgency,
            )
        except Exception as route_exc:
            logger.warning("Support notification routing audit failed: %s", route_exc)
        return conversation, route

    async def create_support_request(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        customer_id: UUID,
        actor_id: UUID,
        issue_description: str,
        urgency: str,
        customer_contact_present: bool,
        config: Dict[str, Any],
        source_channel: Optional[str] = None,
    ) -> bool:
        await self.repository.write_support_audit_log(
            tenant_id=tenant_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            details={
                "issue_length": len(str(issue_description or "")),
                "urgency": urgency,
                "customer_contact_present": customer_contact_present,
            },
        )
        conversation, _route = await self.create_handoff(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            actor_id=actor_id,
            reason=issue_description or "Support request",
            config=config,
            source="support_module",
            source_channel=source_channel,
            urgency=urgency,
            resume_mode="customer",
        )
        return bool(conversation)

    async def return_to_awa(self, tenant_id: UUID, conversation_id: UUID) -> Optional[Dict[str, Any]]:
        conversation = await self.repository.return_to_awa(tenant_id, conversation_id)
        self._log("return_to_awa", tenant_id, "success" if conversation else "not_found", conversation_id)
        return conversation

    async def resolve_conversation(self, tenant_id: UUID, conversation_id: UUID) -> Optional[Dict[str, Any]]:
        conversation = await self.repository.resolve_conversation(tenant_id, conversation_id)
        self._log("resolve", tenant_id, "success" if conversation else "not_found", conversation_id)
        return conversation

    async def update_conversation_status(self, tenant_id: UUID, conversation_id: UUID, status: str) -> bool:
        updated = await self.repository.update_conversation_status(tenant_id, conversation_id, status)
        self._log("update_status", tenant_id, "success" if updated else "not_found", conversation_id)
        return updated

    async def save_human_reply(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        message_text: str,
        channel_type: str,
        provider_message_id: str,
        raw_payload: Dict[str, Any],
        delivery_status: str,
        message_id: Optional[UUID] = None,
    ) -> Union[UUID, bool]:
        conversation = await self.repository.get_conversation(tenant_id, conversation_id)
        if not conversation:
            self._log("save_human_reply", tenant_id, "not_found", conversation_id)
            return False
        msg_id = message_id or uuid4()
        await self.repository.save_human_reply(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            message_id=msg_id,
            provider_message_id=provider_message_id,
            channel_type=channel_type,
            message_text=message_text,
            raw_payload=raw_payload,
            delivery_status=delivery_status,
        )
        self._log("save_human_reply", tenant_id, "success", conversation_id)
        return msg_id

    async def get_public_chat_session_for_conversation(self, tenant_id: UUID, conversation_id: UUID) -> Optional[Dict[str, Any]]:
        return await self.repository.get_public_chat_session_for_conversation(tenant_id, conversation_id)

    async def get_whatsapp_account_for_dispatch(self, tenant_id: UUID, channel_account_id: UUID) -> Optional[Dict[str, Any]]:
        return await self.repository.get_whatsapp_account_for_dispatch(tenant_id, channel_account_id)

    async def create_knowledge_base_entry(self, tenant_id: UUID, **kwargs) -> Optional[Dict[str, Any]]:
        return await self.repository.create_knowledge_base_entry(tenant_id, **kwargs)


support_service = SupportService(events=event_service)
