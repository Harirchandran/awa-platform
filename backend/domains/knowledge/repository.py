import json
from typing import Any, Dict, List, Optional
from uuid import UUID

import asyncpg

from backend.db.client import db_client


class KnowledgeRepository:
    def __init__(self, db=db_client):
        self.db = db

    def _is_usable_embedding(self, embedding: Optional[List[float]]) -> bool:
        return bool(embedding and len(embedding) == 1536 and not all(v == 0.0 for v in embedding))

    async def create_entry(
        self,
        tenant_id: UUID,
        document_title: str,
        chunk_text: str,
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Insert a knowledge chunk safely."""
        from backend.core.config import settings
        metadata = dict(metadata or {})

        embedding_is_usable = self._is_usable_embedding(embedding)
        if embedding_is_usable:
            vector_str = "[" + ",".join(map(str, embedding)) + "]"
            metadata.setdefault("embedding_status", "ready")
            metadata.setdefault("embedding_model", settings.OPENAI_EMBEDDING_MODEL)
        else:
            vector_str = None
            metadata.setdefault("embedding_status", "lexical_only")

        async with self.db.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO tenant_knowledge_base
                        (tenant_id, document_title, chunk_text, embedding, metadata)
                    VALUES ($1, $2, $3, $4::vector, $5::jsonb)
                    RETURNING id, tenant_id, document_title, chunk_text, metadata, created_at
                    """,
                    tenant_id,
                    document_title,
                    chunk_text,
                    vector_str,
                    json.dumps(metadata),
                )
            except asyncpg.UndefinedColumnError:
                row = await conn.fetchrow(
                    """
                    INSERT INTO tenant_knowledge_base
                        (tenant_id, document_title, chunk_text, embedding)
                    VALUES ($1, $2, $3, $4::vector)
                    RETURNING id, tenant_id, document_title, chunk_text, created_at
                    """,
                    tenant_id,
                    document_title,
                    chunk_text,
                    vector_str,
                )
            return dict(row)

    async def list_entries(
        self,
        tenant_id: UUID,
        source: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List tenant knowledge chunks for review and management."""
        params: list[Any] = [tenant_id]
        filters = ["tenant_id = $1"]

        if source:
            params.append(source)
            filters.append(f"metadata->>'source' = ${len(params)}")

        if search:
            params.append(f"%{search}%")
            filters.append(f"(document_title ILIKE ${len(params)} OR chunk_text ILIKE ${len(params)})")

        params.append(limit)
        where_clause = " AND ".join(filters)

        async with self.db.acquire() as conn:
            try:
                rows = await conn.fetch(
                    f"""
                    SELECT id, tenant_id, document_title, chunk_text, metadata, created_at
                    FROM tenant_knowledge_base
                    WHERE {where_clause}
                    ORDER BY created_at DESC
                    LIMIT ${len(params)}
                    """,
                    *params,
                )
            except asyncpg.UndefinedColumnError:
                fallback_params: list[Any] = [tenant_id]
                fallback_filters = ["tenant_id = $1"]
                if search:
                    fallback_params.append(f"%{search}%")
                    fallback_filters.append(f"(document_title ILIKE ${len(fallback_params)} OR chunk_text ILIKE ${len(fallback_params)})")
                fallback_params.append(limit)
                fallback_where = " AND ".join(fallback_filters)
                rows = await conn.fetch(
                    f"""
                    SELECT id, tenant_id, document_title, chunk_text,
                           '{{}}'::jsonb AS metadata, created_at
                    FROM tenant_knowledge_base
                    WHERE {fallback_where}
                    ORDER BY created_at DESC
                    LIMIT ${len(fallback_params)}
                    """,
                    *fallback_params,
                )
            return [dict(row) for row in rows]

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
        """Update a tenant-owned knowledge chunk and preserve existing metadata."""
        from backend.core.config import settings
        metadata_patch = dict(metadata_patch or {})

        embedding_is_usable = self._is_usable_embedding(embedding)
        if embedding_is_usable:
            vector_str = "[" + ",".join(map(str, embedding)) + "]"
            metadata_patch.setdefault("embedding_status", "ready")
            metadata_patch.setdefault("embedding_model", settings.OPENAI_EMBEDDING_MODEL)
        else:
            vector_str = None
            if replace_embedding:
                metadata_patch.setdefault("embedding_status", "lexical_only")

        async with self.db.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """
                    UPDATE tenant_knowledge_base
                    SET document_title = COALESCE($3, document_title),
                        chunk_text = COALESCE($4, chunk_text),
                        embedding = CASE WHEN $7::boolean THEN $5::vector ELSE COALESCE($5::vector, embedding) END,
                        metadata = metadata || $6::jsonb
                    WHERE tenant_id = $1 AND id = $2
                    RETURNING id, tenant_id, document_title, chunk_text, metadata, created_at
                    """,
                    tenant_id,
                    entry_id,
                    document_title,
                    chunk_text,
                    vector_str,
                    json.dumps(metadata_patch),
                    replace_embedding,
                )
            except asyncpg.UndefinedColumnError:
                row = await conn.fetchrow(
                    """
                    UPDATE tenant_knowledge_base
                    SET document_title = COALESCE($3, document_title),
                        chunk_text = COALESCE($4, chunk_text),
                        embedding = CASE WHEN $6::boolean THEN $5::vector ELSE COALESCE($5::vector, embedding) END
                    WHERE tenant_id = $1 AND id = $2
                    RETURNING id, tenant_id, document_title, chunk_text,
                              '{}'::jsonb AS metadata, created_at
                    """,
                    tenant_id,
                    entry_id,
                    document_title,
                    chunk_text,
                    vector_str,
                    replace_embedding,
                )
            return dict(row) if row else None

    async def delete_entry(self, tenant_id: UUID, entry_id: UUID) -> bool:
        """Delete a tenant-owned knowledge chunk."""
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM tenant_knowledge_base
                WHERE tenant_id = $1 AND id = $2
                """,
                tenant_id,
                entry_id,
            )
            return "DELETE 1" in result

    async def delete_entries_by_source(self, tenant_id: UUID, source: str) -> int:
        """Delete all tenant-owned knowledge base chunks for a given source."""
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM tenant_knowledge_base
                WHERE tenant_id = $1 AND metadata->>'source' = $2
                """,
                tenant_id,
                source,
            )
            if result.startswith("DELETE "):
                try:
                    return int(result.split()[1])
                except (ValueError, IndexError):
                    pass
            return 0

    async def delete_entries_by_url(self, tenant_id: UUID, url: str) -> int:
        """Delete all tenant-owned knowledge base chunks for a given website URL."""
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM tenant_knowledge_base
                WHERE tenant_id = $1
                  AND metadata->>'source' = 'website'
                  AND metadata->>'url' = $2
                """,
                tenant_id,
                url,
            )
            if result.startswith("DELETE "):
                try:
                    return int(result.split()[1])
                except (ValueError, IndexError):
                    pass
            return 0

    async def summarize_sources(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        """Summarize knowledge sources."""
        async with self.db.acquire() as conn:
            try:
                rows = await conn.fetch(
                    """
                    SELECT COALESCE(metadata->>'source', 'manual') as source, COUNT(*) as chunk_count
                    FROM tenant_knowledge_base
                    WHERE tenant_id = $1
                    GROUP BY metadata->>'source'
                    """,
                    tenant_id,
                )
                return [dict(row) for row in rows]
            except asyncpg.UndefinedColumnError:
                return [{"source": "manual", "chunk_count": 0}]

    async def search_text(
        self, tenant_id: UUID, query: str, limit: int = 3
    ) -> List[Dict[str, Any]]:
        """Fallback lexical search for environments without real embeddings."""
        terms = [
            term.strip(".,!?;:'\"()[]{}").lower()
            for term in query.split()
            if len(term.strip(".,!?;:'\"()[]{}")) >= 4
        ]
        terms = list(dict.fromkeys(terms))[:8]
        if not terms:
            return []

        async with self.db.acquire() as conn:
            try:
                rows = await conn.fetch(
                    """
                    WITH q AS (
                        SELECT websearch_to_tsquery('simple', $2) AS query
                    )
                    SELECT id, chunk_text, document_title, metadata, created_at,
                           ts_rank(
                               to_tsvector('simple', COALESCE(document_title, '') || ' ' || COALESCE(chunk_text, '')),
                               q.query
                           ) AS lexical_rank
                    FROM tenant_knowledge_base, q
                    WHERE tenant_id = $1
                      AND COALESCE((metadata->>'enabled')::boolean, TRUE) = TRUE
                      AND (
                          to_tsvector('simple', COALESCE(document_title, '') || ' ' || COALESCE(chunk_text, '')) @@ q.query
                          OR EXISTS (
                              SELECT 1 FROM unnest($3::text[]) AS term
                              WHERE lower(chunk_text) LIKE '%' || term || '%'
                                 OR lower(document_title) LIKE '%' || term || '%'
                          )
                      )
                    ORDER BY lexical_rank DESC, created_at DESC
                    LIMIT $4
                    """,
                    tenant_id,
                    query,
                    terms,
                    limit,
                )
            except asyncpg.UndefinedColumnError:
                rows = await conn.fetch(
                    """
                    SELECT id, chunk_text, document_title, '{}'::jsonb AS metadata, created_at
                    FROM tenant_knowledge_base
                    WHERE tenant_id = $1
                      AND EXISTS (
                          SELECT 1 FROM unnest($2::text[]) AS term
                          WHERE lower(chunk_text) LIKE '%' || term || '%'
                             OR lower(document_title) LIKE '%' || term || '%'
                      )
                    ORDER BY created_at DESC
                    LIMIT $3
                    """,
                    tenant_id,
                    terms,
                    limit,
                )
            return [dict(row) for row in rows]


knowledge_repository = KnowledgeRepository()
