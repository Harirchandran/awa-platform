from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID

from backend.db.client import db_client


SAFE_MEDIA_FIELDS = """
    id, tenant_id, entity_type, entity_id, served_url, thumbnail_url,
    original_filename_sanitized, mime_type, width, height, size_bytes,
    alt_text, caption, status, is_primary, created_at, updated_at
"""


class MediaRepository:
    def __init__(self, db=db_client):
        self.db = db

    async def catalog_item_exists(self, tenant_id: UUID, item_id: UUID) -> bool:
        async with self.db.acquire() as conn:
            return bool(await conn.fetchval(
                """
                SELECT TRUE
                FROM catalog_items
                WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                """,
                tenant_id,
                item_id,
            ))

    async def create_primary_asset(
        self,
        asset_id: UUID,
        tenant_id: UUID,
        entity_type: str,
        entity_id: UUID,
        storage_key: str,
        served_url: str,
        thumbnail_url: Optional[str],
        original_filename_sanitized: str,
        mime_type: str,
        width: int,
        height: int,
        size_bytes: int,
        alt_text: Optional[str],
        caption: Optional[str],
    ) -> Dict[str, Any]:
        async with self.db.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE media_assets
                    SET status = 'hidden', is_primary = FALSE, updated_at = NOW()
                    WHERE tenant_id = $1
                      AND entity_type = $2
                      AND entity_id = $3
                      AND status = 'active'
                      AND is_primary = TRUE
                    """,
                    tenant_id,
                    entity_type,
                    entity_id,
                )
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO media_assets (
                        id, tenant_id, entity_type, entity_id, storage_key, served_url,
                        thumbnail_url, original_filename_sanitized, mime_type,
                        width, height, size_bytes, alt_text, caption, status,
                        is_primary, created_at, updated_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6,
                        $7, $8, $9, $10, $11, $12, $13, $14,
                        'active', TRUE, NOW(), NOW()
                    )
                    RETURNING {SAFE_MEDIA_FIELDS}
                    """,
                    asset_id,
                    tenant_id,
                    entity_type,
                    entity_id,
                    storage_key,
                    served_url,
                    thumbnail_url,
                    original_filename_sanitized,
                    mime_type,
                    width,
                    height,
                    size_bytes,
                    alt_text,
                    caption,
                )
        return dict(row)

    async def get_asset(self, asset_id: UUID) -> Optional[Dict[str, Any]]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM media_assets
                WHERE id = $1 AND status = 'active'
                """,
                asset_id,
            )
            return dict(row) if row else None

    async def get_primary_asset(self, tenant_id: UUID, entity_type: str, entity_id: UUID) -> Optional[Dict[str, Any]]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {SAFE_MEDIA_FIELDS}
                FROM media_assets
                WHERE tenant_id = $1
                  AND entity_type = $2
                  AND entity_id = $3
                  AND status = 'active'
                  AND is_primary = TRUE
                ORDER BY created_at DESC
                LIMIT 1
                """,
                tenant_id,
                entity_type,
                entity_id,
            )
            return dict(row) if row else None

    async def list_primary_assets(
        self,
        tenant_id: UUID,
        entity_type: str,
        entity_ids: Iterable[UUID],
    ) -> Dict[str, Dict[str, Any]]:
        ids = [entity_id for entity_id in entity_ids if entity_id]
        if not ids:
            return {}
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {SAFE_MEDIA_FIELDS}
                FROM media_assets
                WHERE tenant_id = $1
                  AND entity_type = $2
                  AND entity_id = ANY($3::uuid[])
                  AND status = 'active'
                  AND is_primary = TRUE
                """,
                tenant_id,
                entity_type,
                ids,
            )
            return {str(row["entity_id"]): dict(row) for row in rows}

    async def mark_asset_deleted(self, tenant_id: UUID, entity_type: str, entity_id: UUID, asset_id: UUID) -> Optional[Dict[str, Any]]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE media_assets
                SET status = 'deleted', is_primary = FALSE, updated_at = NOW()
                WHERE tenant_id = $1
                  AND entity_type = $2
                  AND entity_id = $3
                  AND id = $4
                  AND status = 'active'
                RETURNING {SAFE_MEDIA_FIELDS}, storage_key
                """,
                tenant_id,
                entity_type,
                entity_id,
                asset_id,
            )
            return dict(row) if row else None


media_repository = MediaRepository()
