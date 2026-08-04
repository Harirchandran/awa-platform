from typing import Any, Dict, List, Optional
from uuid import UUID

from backend.domains.customers.repository import CustomerRepository, customer_repository
from backend.domains.events.models import BusinessEventCreate
from backend.domains.events.service import EventService, event_service
from backend.ops.logger import logger


class CustomerService:
    def __init__(self, repository: CustomerRepository = customer_repository, events: Optional[EventService] = None):
        self.repository = repository
        self.events = events

    def _log(self, action: str, tenant_id: UUID, outcome: str, record_id: Optional[UUID] = None) -> None:
        extra = {"domain": "customers", "action": action, "tenant_id": str(tenant_id), "outcome": outcome}
        if record_id:
            extra["record_id"] = str(record_id)
        logger.debug("domain service call", extra=extra)

    # Core Customers
    async def get_or_create_awa_chat_customer(
        self, tenant_id: UUID, channel_subject: str, name: Optional[str] = None, phone_number: Optional[str] = None
    ) -> Dict[str, Any]:
        customer = await self.repository.get_or_create_awa_chat_customer(tenant_id, channel_subject, name, phone_number)
        self._log("get_or_create_awa_chat", tenant_id, "success", customer.get("id"))
        return customer

    async def update_public_chat_session_customer(
        self, tenant_id: UUID, session_id: UUID, conversation_id: UUID, customer_id: UUID
    ) -> None:
        await self.repository.update_public_chat_session_customer(tenant_id, session_id, conversation_id, customer_id)
        self._log("update_public_chat_session_customer", tenant_id, "success", customer_id)

    async def get_or_create_customer(
        self, tenant_id: UUID, phone_number: str, name: Optional[str] = None
    ) -> Dict[str, Any]:
        customer = await self.repository.get_or_create_customer(tenant_id, phone_number, name)
        self._log("get_or_create", tenant_id, "success", customer.get("id"))
        return customer

    async def update_consent(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        consent_status: str,
        consent_version: str = "v1.0",
        consent_method: str = "whatsapp_opt_in",
    ) -> bool:
        result = await self.repository.update_consent(
            tenant_id, customer_id, consent_status, consent_version, consent_method
        )
        self._log("update_consent", tenant_id, "success" if result else "failure", customer_id)
        return result

    async def update_customer(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        name: Optional[str] = None,
        email: Optional[str] = None,
        phone_number: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        consent_status: Optional[str] = None,
    ) -> bool:
        result = await self.repository.update_customer(
            tenant_id, customer_id, name, email, phone_number, metadata, consent_status
        )
        if result and self.events:
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="customer.updated",
                aggregate_type="customer",
                aggregate_id=customer_id,
                source_domain="customers",
                payload={"customer_id": str(customer_id)},
            ))
        self._log("update_customer", tenant_id, "success" if result else "not_found", customer_id)
        return result

    async def get_customer_by_phone(self, tenant_id: UUID, phone_number: str) -> Optional[Dict[str, Any]]:
        result = await self.repository.get_customer_by_phone(tenant_id, phone_number)
        self._log("get_customer_by_phone", tenant_id, "success" if result else "not_found")
        return result

    async def delete_customer(self, tenant_id: UUID, customer_id: UUID) -> bool:
        result = await self.repository.delete_customer(tenant_id, customer_id)
        if result and self.events:
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="customer.deleted",
                aggregate_type="customer",
                aggregate_id=customer_id,
                source_domain="customers",
                payload={"customer_id": str(customer_id)},
            ))
        self._log("delete_customer", tenant_id, "success" if result else "not_found", customer_id)
        return result

    async def merge_customers(self, tenant_id: UUID, primary_id: UUID, secondary_id: UUID) -> bool:
        """Merge a secondary customer into primary, emit event, and log."""
        result = await self.repository.merge_customers(tenant_id, primary_id, secondary_id)
        if result and self.events:
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="customer.merged",
                aggregate_type="customer",
                aggregate_id=primary_id,
                source_domain="customers",
                payload={"primary_id": str(primary_id), "secondary_id": str(secondary_id)},
            ))
        self._log("merge_customers", tenant_id, "success" if result else "failure", primary_id)
        return result

    async def get_customer(self, tenant_id: UUID, customer_id: UUID) -> Optional[Dict[str, Any]]:
        result = await self.repository.get_customer(tenant_id, customer_id)
        self._log("get_customer", tenant_id, "success" if result else "not_found", customer_id)
        return result

    async def list_customers(
        self, tenant_id: UUID, cursor: Optional[str] = None, limit: int = 20, search: Optional[str] = None
    ) -> Dict[str, Any]:
        result = await self.repository.list_customers(tenant_id, cursor=cursor, limit=limit, search=search)
        self._log("list_customers", tenant_id, "success")
        return result

    # Tags
    async def list_tags(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        result = await self.repository.list_tags(tenant_id)
        self._log("list_tags", tenant_id, "success")
        return result

    async def create_tag(
        self, tenant_id: UUID, name: str, color: str = "#64748b", description: Optional[str] = None
    ) -> Dict[str, Any]:
        tag = await self.repository.create_tag(tenant_id, name, color, description)
        self._log("create_tag", tenant_id, "success", tag.get("id"))
        return tag

    async def update_tag(
        self,
        tenant_id: UUID,
        tag_id: UUID,
        name: Optional[str] = None,
        color: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        tag = await self.repository.update_tag(tenant_id, tag_id, name, color, description)
        self._log("update_tag", tenant_id, "success" if tag else "not_found", tag_id)
        return tag

    async def delete_tag(self, tenant_id: UUID, tag_id: UUID) -> bool:
        result = await self.repository.delete_tag(tenant_id, tag_id)
        self._log("delete_tag", tenant_id, "success" if result else "not_found", tag_id)
        return result

    async def add_tag(
        self, tenant_id: UUID, customer_id: UUID, tag_id: UUID, created_by: Optional[UUID] = None
    ) -> bool:
        result = await self.repository.add_tag(tenant_id, customer_id, tag_id, created_by)
        self._log("add_tag", tenant_id, "success" if result else "failure", customer_id)
        return result

    async def remove_tag(self, tenant_id: UUID, customer_id: UUID, tag_id: UUID) -> bool:
        result = await self.repository.remove_tag(tenant_id, customer_id, tag_id)
        self._log("remove_tag", tenant_id, "success" if result else "not_found", customer_id)
        return result

    # Notes
    async def list_notes(self, tenant_id: UUID, customer_id: UUID) -> List[Dict[str, Any]]:
        result = await self.repository.list_notes(tenant_id, customer_id)
        self._log("list_notes", tenant_id, "success", customer_id)
        return result

    async def create_note(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        note: str,
        author_user_id: Optional[UUID],
        visibility: str = "internal",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        result = await self.repository.create_note(
            tenant_id, customer_id, note, author_user_id, visibility, metadata
        )
        self._log("create_note", tenant_id, "success" if result else "not_found", customer_id)
        return result

    async def update_note(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        note_id: UUID,
        note: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        result = await self.repository.update_note(tenant_id, customer_id, note_id, note, metadata)
        self._log("update_note", tenant_id, "success" if result else "not_found", note_id)
        return result

    async def delete_note(self, tenant_id: UUID, customer_id: UUID, note_id: UUID) -> bool:
        result = await self.repository.delete_note(tenant_id, customer_id, note_id)
        self._log("delete_note", tenant_id, "success" if result else "not_found", note_id)
        return result

    # Timeline
    async def get_timeline(self, tenant_id: UUID, customer_id: UUID, limit: int = 30) -> List[Dict[str, Any]]:
        result = await self.repository.get_timeline(tenant_id, customer_id, limit)
        self._log("get_timeline", tenant_id, "success", customer_id)
        return result


customer_service = CustomerService(events=event_service)
