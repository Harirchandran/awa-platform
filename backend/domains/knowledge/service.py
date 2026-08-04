from typing import Any, Dict, List, Optional
from uuid import UUID

from backend.domains.events.models import BusinessEventCreate
from backend.domains.events.service import EventService, event_service
from backend.domains.knowledge.repository import KnowledgeRepository, knowledge_repository
from backend.ops.logger import logger


class KnowledgeService:
    def __init__(self, repository: KnowledgeRepository = knowledge_repository, events: Optional[EventService] = None):
        self.repository = repository
        self.events = events

    def _log(self, action: str, tenant_id: UUID, outcome: str, record_id: Optional[UUID] = None) -> None:
        extra = {"domain": "knowledge", "action": action, "tenant_id": str(tenant_id), "outcome": outcome}
        if record_id:
            extra["record_id"] = str(record_id)
        logger.debug("domain service call", extra=extra)

    async def list_entries(
        self, tenant_id: UUID, source: Optional[str] = None, search: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        result = await self.repository.list_entries(tenant_id, source=source, search=search, limit=limit)
        self._log("list", tenant_id, "success")
        return result

    async def summarize_sources(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        result = await self.repository.summarize_sources(tenant_id)
        self._log("summarize", tenant_id, "success")
        return result

    async def search_text(
        self, tenant_id: UUID, query: str, limit: int = 3
    ) -> List[Dict[str, Any]]:
        result = await self.repository.search_text(tenant_id, query, limit=limit)
        self._log("search", tenant_id, "success")
        return result

    async def create_entry(
        self,
        tenant_id: UUID,
        document_title: str,
        chunk_text: str,
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        entry = await self.repository.create_entry(
            tenant_id=tenant_id,
            document_title=document_title,
            chunk_text=chunk_text,
            embedding=embedding,
            metadata=metadata,
        )
        if self.events and entry:
            import json
            meta = entry.get("metadata")
            if isinstance(meta, str):
                meta = json.loads(meta)
            elif meta is None:
                meta = {}
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="knowledge.created",
                aggregate_type="knowledge",
                aggregate_id=entry.get("id"),
                source_domain="knowledge",
                payload={
                    "knowledge_id": str(entry.get("id")),
                    "document_title": entry.get("document_title"),
                    "source": meta.get("source", "manual")
                },
            ))
        self._log("create", tenant_id, "success", entry.get("id"))
        return entry

    async def update_entry(
        self,
        tenant_id: UUID,
        entry_id: UUID,
        document_title: Optional[str] = None,
        chunk_text: Optional[str] = None,
        embedding: Optional[List[float]] = None,
        metadata_patch: Optional[Dict[str, Any]] = None,
        replace_embedding: bool = False,
    ) -> Optional[Dict[str, Any]]:
        entry = await self.repository.update_entry(
            tenant_id=tenant_id,
            entry_id=entry_id,
            document_title=document_title,
            chunk_text=chunk_text,
            embedding=embedding,
            metadata_patch=metadata_patch,
            replace_embedding=replace_embedding,
        )
        if entry:
            if self.events:
                await self.events.emit(BusinessEventCreate(
                    tenant_id=tenant_id,
                    event_type="knowledge.updated",
                    aggregate_type="knowledge",
                    aggregate_id=entry.get("id"),
                    source_domain="knowledge",
                    payload={
                        "knowledge_id": str(entry.get("id")),
                        "document_title": entry.get("document_title"),
                    },
                ))
            self._log("update", tenant_id, "success", entry_id)
        else:
            self._log("update", tenant_id, "not_found", entry_id)
        return entry

    async def delete_entry(self, tenant_id: UUID, entry_id: UUID) -> bool:
        result = await self.repository.delete_entry(tenant_id, entry_id)
        self._log("delete", tenant_id, "success" if result else "not_found", entry_id)
        return result

    async def delete_entries_by_source(self, tenant_id: UUID, source: str) -> int:
        count = await self.repository.delete_entries_by_source(tenant_id, source)
        self._log("delete_by_source", tenant_id, "success")
        return count

    async def delete_entries_by_url(self, tenant_id: UUID, url: str) -> int:
        count = await self.repository.delete_entries_by_url(tenant_id, url)
        self._log("delete_by_url", tenant_id, "success")
        return count


knowledge_service = KnowledgeService(events=event_service)
