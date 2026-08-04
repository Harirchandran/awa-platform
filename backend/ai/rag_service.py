"""
RAG Service (Phase 11)
Handles fetching context from the pgvector database for the Agentic Engine.
"""
import math
import json
import re
from typing import List, Optional
from uuid import UUID
from backend.core.config import settings
from backend.db.client import db_client
from backend.core.context import ExecutionContext
from backend.domains.knowledge.service import knowledge_service
from backend.ops.logger import logger
from backend.ai.security import sanitize_untrusted_text
class RAGService:
    NO_CONTEXT_MESSAGE = "No helpful context found."
    UNAVAILABLE_MESSAGE = "Knowledge base search temporarily unavailable."
    EMBEDDING_DIMENSIONS = 1536
    UUID_PATTERN = re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    )
    def __init__(self):
        self._openai_client = None
    @staticmethod
    def _is_placeholder_api_key(api_key: str) -> bool:
        normalized = (api_key or "").strip().lower()
        if not normalized:
            return True
        placeholder_markers = (
            "your_openai",
            "your-openai",
            "your_",
            "your-",
            "placeholder",
            "change_me",
            "changeme",
            "replace_me",
            "replace-me",
            "example",
        )
        return any(marker in normalized for marker in placeholder_markers)
    async def embed_query(self, text: str) -> List[float]:
        """
        Vectorise text when a real embedding provider is configured.
        In local/mock mode this returns an empty list so callers store/search
        lexically instead of writing unusable all-zero vectors.
        """
        clean_text = (text or "").strip()
        if not clean_text:
            return []
        if (settings.AGENT_LLM_PROVIDER or "").strip().lower() == "ollama" or (settings.ROUTING_LLM_PROVIDER or "").strip().lower() == "ollama":
            return []
        api_key = (settings.OPENAI_API_KEY or "").strip()
        if self._is_placeholder_api_key(api_key):
            return []
        try:
            if self._openai_client is None:
                from openai import AsyncOpenAI
                self._openai_client = AsyncOpenAI(api_key=api_key)
            response = await self._openai_client.embeddings.create(
                model=getattr(settings, "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
                input=clean_text[:8000],
            )
            vector = list(response.data[0].embedding)
        except Exception as exc:
            logger.warning(f"Embedding provider unavailable, using lexical RAG fallback: {exc}")
            return []
        if not self.is_usable_embedding(vector):
            logger.warning("Embedding provider returned an invalid vector; using lexical RAG fallback")
            return []
        return vector
    @classmethod
    def is_usable_embedding(cls, embedding: Optional[List[float]]) -> bool:
        if not embedding or len(embedding) != cls.EMBEDDING_DIMENSIONS:
            return False
        try:
            norm = math.sqrt(sum(float(value) * float(value) for value in embedding))
        except (TypeError, ValueError):
            return False
        return math.isfinite(norm) and norm > 0.0
    async def search_knowledge_base(self, tenant_id: UUID, query: str, limit: int = 3) -> str:
        """
        Embeds the query and performs a cosine-similarity search against
        the pgvector 'tenant_knowledge_base' table.
        """
        try:
            query_embedding = await self.embed_query(query)
            rows = []
            if self.is_usable_embedding(query_embedding):
                vector_str = "[" + ",".join(map(str, query_embedding)) + "]"
                sql = """
                SELECT id, document_title, chunk_text, metadata, created_at,
                       1 - (embedding <=> $1::vector) AS similarity
                FROM tenant_knowledge_base
                WHERE tenant_id = $2
                  AND embedding IS NOT NULL
                  AND vector_norm(embedding) > 0
                  AND COALESCE((metadata->>'enabled')::boolean, TRUE) = TRUE
                  AND COALESCE(metadata->>'status', 'ready') IN ('ready', 'approved', 'active', 'published')
                  AND COALESCE(metadata->>'approval_status', 'approved') IN ('approved', 'ready', 'active')
                  AND 1 - (embedding <=> $1::vector) > 0.75
                ORDER BY embedding <=> $1::vector
                LIMIT $3
                """
                async with db_client.acquire() as conn:
                    rows = await conn.fetch(sql, vector_str, tenant_id, limit)
            if not rows:
                rows = await knowledge_service.search_text(tenant_id, query, limit)
                if not rows:
                    return self.NO_CONTEXT_MESSAGE
            # Compile the top chunks into a provenance-labelled reference block for the LLM.
            context = self.format_rows_with_provenance(rows)
            logger.info(f"RAG triggered for tenant {tenant_id}: Found {len(rows)} matching documents.")
            return context
        except Exception as e:
            logger.error(f"RAG Service error: {e}")
            try:
                lexical_rows = await knowledge_service.search_text(tenant_id, query, limit)
                if lexical_rows:
                    return self.format_rows_with_provenance(lexical_rows)
            except Exception as fallback_exc:
                logger.error(f"RAG lexical fallback error: {fallback_exc}")
            return self.UNAVAILABLE_MESSAGE
    @classmethod
    def has_helpful_context(cls, context: str) -> bool:
        normalized = (context or "").strip()
        return bool(
            normalized
            and normalized not in {cls.NO_CONTEXT_MESSAGE, cls.UNAVAILABLE_MESSAGE}
        )
    @classmethod
    def _redact_internal_identifiers(cls, value: str) -> str:
        return cls.UUID_PATTERN.sub("[redacted]", value or "")
    @classmethod
    def format_rows_with_provenance(cls, rows: List[dict]) -> str:
        blocks: List[str] = []
        for index, row in enumerate(rows or [], start=1):
            row_dict = dict(row)
            metadata = row_dict.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            chunk = cls._redact_internal_identifiers(
                sanitize_untrusted_text(row_dict.get("chunk_text") or "", max_chars=1800)
            )
            if not chunk:
                continue
            title = cls._redact_internal_identifiers(
                sanitize_untrusted_text(row_dict.get("document_title") or metadata.get("title") or "Untitled knowledge", max_chars=160)
            )
            source = cls._redact_internal_identifiers(
                sanitize_untrusted_text(metadata.get("source") or "unknown", max_chars=80)
            )
            url = cls._redact_internal_identifiers(
                sanitize_untrusted_text(metadata.get("url") or metadata.get("file_name") or "", max_chars=300)
            )
            chunk_index = metadata.get("chunk_index")
            chunk_count = metadata.get("chunk_count")
            chunk_label = f"{chunk_index}/{chunk_count}" if chunk_index and chunk_count else str(index)
            provenance = [
                f"title={title or 'Untitled knowledge'}",
                f"source={source or 'unknown'}",
                f"chunk={chunk_label}",
            ]
            if url:
                provenance.append(f"url_or_file={url}")
            blocks.append(
                "Knowledge reference "
                + " | ".join(provenance)
                + "\n"
                + chunk
            )
        return "\n\n".join(blocks) if blocks else cls.NO_CONTEXT_MESSAGE
# Singleton instance
rag_service = RAGService()
