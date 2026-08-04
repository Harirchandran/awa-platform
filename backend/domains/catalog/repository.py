import json
import asyncpg
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from backend.db.client import db_client


class CatalogRepository:
    def __init__(self, db=db_client):
        self.db = db

    async def list_categories(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        """List active catalog categories with item counts."""
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.tenant_id, c.name, c.description, c.sort_order,
                       c.is_active, c.metadata, c.created_at, c.updated_at,
                       COALESCE(COUNT(i.id), 0)::int AS item_count
                FROM catalog_categories c
                LEFT JOIN catalog_items i
                  ON i.tenant_id = c.tenant_id
                 AND i.category_id = c.id
                 AND i.deleted_at IS NULL
                WHERE c.tenant_id = $1 AND c.deleted_at IS NULL
                GROUP BY c.id
                ORDER BY c.sort_order ASC, c.created_at DESC
                """,
                tenant_id,
            )
            return [dict(row) for row in rows]

    async def create_category(
        self,
        tenant_id: UUID,
        name: str,
        description: Optional[str] = None,
        sort_order: int = 0,
        is_active: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a tenant catalog category."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO catalog_categories (
                    id, tenant_id, name, description, sort_order, is_active,
                    metadata, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, NOW(), NOW())
                RETURNING id, tenant_id, name, description, sort_order, is_active,
                          metadata, created_at, updated_at, 0::int AS item_count
                """,
                uuid4(),
                tenant_id,
                name,
                description,
                sort_order,
                is_active,
                json.dumps(metadata or {}, default=str),
            )
            return dict(row)

    async def update_category(
        self, tenant_id: UUID, category_id: UUID, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update mutable catalog category fields."""
        allowed = {"name", "description", "sort_order", "is_active", "metadata"}
        assignments = []
        values: List[Any] = [tenant_id, category_id]
        idx = 3
        for field, value in updates.items():
            if field not in allowed:
                continue
            if field == "metadata":
                assignments.append(f"{field} = ${idx}::jsonb")
                values.append(json.dumps(value or {}, default=str))
            else:
                assignments.append(f"{field} = ${idx}")
                values.append(value)
            idx += 1
        if not assignments:
            return None
        assignments.append("updated_at = NOW()")
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE catalog_categories
                SET {', '.join(assignments)}
                WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                RETURNING id, tenant_id, name, description, sort_order, is_active,
                          metadata, created_at, updated_at,
                          (
                              SELECT COALESCE(COUNT(i.id), 0)::int
                              FROM catalog_items i
                              WHERE i.tenant_id = catalog_categories.tenant_id
                                AND i.category_id = catalog_categories.id
                                AND i.deleted_at IS NULL
                          ) AS item_count
                """,
                *values,
            )
            return dict(row) if row else None

    async def delete_category(self, tenant_id: UUID, category_id: UUID) -> bool:
        """Soft-delete a category and detach its items."""
        async with self.db.acquire() as conn:
            async with conn.transaction():
                result = await conn.execute(
                    """
                    UPDATE catalog_categories
                    SET deleted_at = NOW(), is_active = FALSE, updated_at = NOW()
                    WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                    """,
                    tenant_id,
                    category_id,
                )
                if "UPDATE 1" not in result:
                    return False
                await conn.execute(
                    """
                    UPDATE catalog_items
                    SET category_id = NULL, updated_at = NOW()
                    WHERE tenant_id = $1 AND category_id = $2 AND deleted_at IS NULL
                    """,
                    tenant_id,
                    category_id,
                )
                return True

    async def list_items(
        self,
        tenant_id: UUID,
        category_id: Optional[UUID] = None,
        search: Optional[str] = None,
        active_only: bool = False,
        available_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """List catalog items for management and order lookup surfaces."""
        async with self.db.acquire() as conn:
            query = """
                SELECT i.id, i.tenant_id, i.category_id, c.name AS category_name,
                       i.name, i.description, i.price, i.currency, i.sku, i.image_url,
                       i.is_active, i.is_available, i.stock_tracking_enabled,
                       i.stock_quantity, i.low_stock_threshold, i.metadata,
                       i.created_at, i.updated_at,
                       CASE
                           WHEN i.stock_tracking_enabled
                            AND i.low_stock_threshold IS NOT NULL
                            AND i.stock_quantity <= i.low_stock_threshold
                           THEN TRUE ELSE FALSE
                       END AS low_stock
                FROM catalog_items i
                LEFT JOIN catalog_categories c
                  ON c.tenant_id = i.tenant_id
                 AND c.id = i.category_id
                 AND c.deleted_at IS NULL
                WHERE i.tenant_id = $1 AND i.deleted_at IS NULL
            """
            params: List[Any] = [tenant_id]
            idx = 2
            if category_id:
                query += f" AND i.category_id = ${idx}"
                params.append(category_id)
                idx += 1
            if search:
                query += f" AND (i.name ILIKE ${idx} OR i.sku ILIKE ${idx})"
                params.append(f"%{search}%")
                idx += 1
            if active_only:
                query += " AND i.is_active = TRUE"
            if available_only:
                query += " AND i.is_available = TRUE"
            query += """
                ORDER BY COALESCE(c.sort_order, 9999) ASC,
                         c.name ASC NULLS LAST,
                         i.created_at DESC
                LIMIT 500
            """
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    async def get_item(self, tenant_id: UUID, item_id: UUID) -> Optional[Dict[str, Any]]:
        """Fetch one catalog item with category metadata."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT i.id, i.tenant_id, i.category_id, c.name AS category_name,
                       i.name, i.description, i.price, i.currency, i.sku, i.image_url,
                       i.is_active, i.is_available, i.stock_tracking_enabled,
                       i.stock_quantity, i.low_stock_threshold, i.metadata,
                       i.created_at, i.updated_at,
                       CASE
                           WHEN i.stock_tracking_enabled
                            AND i.low_stock_threshold IS NOT NULL
                            AND i.stock_quantity <= i.low_stock_threshold
                           THEN TRUE ELSE FALSE
                       END AS low_stock
                FROM catalog_items i
                LEFT JOIN catalog_categories c
                  ON c.tenant_id = i.tenant_id
                 AND c.id = i.category_id
                 AND c.deleted_at IS NULL
                WHERE i.tenant_id = $1 AND i.id = $2 AND i.deleted_at IS NULL
                """,
                tenant_id,
                item_id,
            )
            return dict(row) if row else None

    async def create_item(
        self,
        tenant_id: UUID,
        name: str,
        category_id: Optional[UUID] = None,
        description: Optional[str] = None,
        price: float = 0,
        currency: str = "INR",
        sku: Optional[str] = None,
        image_url: Optional[str] = None,
        is_active: bool = True,
        is_available: bool = True,
        stock_tracking_enabled: bool = False,
        stock_quantity: float = 0,
        low_stock_threshold: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a tenant catalog item."""
        async with self.db.acquire() as conn:
            if category_id:
                category_exists = await conn.fetchval(
                    """
                    SELECT TRUE
                    FROM catalog_categories
                    WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                    """,
                    tenant_id,
                    category_id,
                )
                if not category_exists:
                    return None
            item_id = uuid4()
            await conn.execute(
                """
                INSERT INTO catalog_items (
                    id, tenant_id, category_id, name, description, price, currency,
                    sku, image_url, is_active, is_available, stock_tracking_enabled,
                    stock_quantity, low_stock_threshold, metadata, created_at, updated_at
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7,
                    $8, $9, $10, $11, $12,
                    $13, $14, $15::jsonb, NOW(), NOW()
                )
                """,
                item_id,
                tenant_id,
                category_id,
                name,
                description,
                price,
                currency,
                sku,
                image_url,
                is_active,
                is_available,
                stock_tracking_enabled,
                stock_quantity,
                low_stock_threshold,
                json.dumps(metadata or {}, default=str),
            )
            return await self.get_item(tenant_id, item_id)

    async def update_item(
        self, tenant_id: UUID, item_id: UUID, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update mutable catalog item fields."""
        allowed = {
            "category_id",
            "name",
            "description",
            "price",
            "currency",
            "sku",
            "image_url",
            "is_active",
            "is_available",
            "stock_tracking_enabled",
            "stock_quantity",
            "low_stock_threshold",
            "metadata",
        }
        async with self.db.acquire() as conn:
            if "category_id" in updates and updates["category_id"] is not None:
                category_exists = await conn.fetchval(
                    """
                    SELECT TRUE
                    FROM catalog_categories
                    WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                    """,
                    tenant_id,
                    updates["category_id"],
                )
                if not category_exists:
                    return None
            assignments = []
            values: List[Any] = [tenant_id, item_id]
            idx = 3
            for field, value in updates.items():
                if field not in allowed:
                    continue
                if field == "metadata":
                    assignments.append(f"{field} = ${idx}::jsonb")
                    values.append(json.dumps(value or {}, default=str))
                else:
                    assignments.append(f"{field} = ${idx}")
                    values.append(value)
                idx += 1
            if not assignments:
                return None
            assignments.append("updated_at = NOW()")
            row = await conn.fetchrow(
                f"""
                UPDATE catalog_items
                SET {', '.join(assignments)}
                WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                RETURNING id
                """,
                *values,
            )
            if not row:
                return None
            return await self.get_item(tenant_id, item_id)

    async def delete_item(self, tenant_id: UUID, item_id: UUID) -> bool:
        """Soft-delete a catalog item."""
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE catalog_items
                SET deleted_at = NOW(), is_active = FALSE, is_available = FALSE, updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                """,
                tenant_id,
                item_id,
            )
            return "UPDATE 1" in result

    async def adjust_stock(
        self,
        tenant_id: UUID,
        item_id: UUID,
        quantity_delta: float,
        reason: Optional[str] = None,
        event_type: str = "manual_adjustment",
        metadata: Optional[Dict[str, Any]] = None,
        created_by: Optional[UUID] = None,
    ) -> Optional[Dict[str, Any]]:
        """Adjust item stock and append an inventory event."""
        async with self.db.acquire() as conn:
            async with conn.transaction():
                updated = await conn.fetchrow(
                    """
                    UPDATE catalog_items
                    SET stock_tracking_enabled = TRUE,
                        stock_quantity = stock_quantity + $3,
                        updated_at = NOW()
                    WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                    RETURNING id
                    """,
                    tenant_id,
                    item_id,
                    quantity_delta,
                )
                if not updated:
                    return None
                await conn.execute(
                    """
                    INSERT INTO inventory_events (
                        id, tenant_id, item_id, event_type, quantity_delta,
                        reason, created_by, metadata, created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, NOW())
                    """,
                    uuid4(),
                    tenant_id,
                    item_id,
                    event_type,
                    quantity_delta,
                    reason,
                    created_by,
                    json.dumps(metadata or {}, default=str),
                )
            return await self.get_item(tenant_id, item_id)

    async def list_inventory_events(
        self, tenant_id: UUID, item_id: UUID, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """List inventory events for one item."""
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, tenant_id, item_id, variant_id, event_type, quantity_delta,
                       reason, order_id, created_by, metadata, created_at
                FROM inventory_events
                WHERE tenant_id = $1 AND item_id = $2
                ORDER BY created_at DESC
                LIMIT $3
                """,
                tenant_id,
                item_id,
                limit,
            )
            return [dict(row) for row in rows]

    async def resolve_catalog_item(self, tenant_id: UUID, value: Any) -> Optional[Dict[str, Any]]:
        """Resolve a customer-provided item value against the DB-backed catalog."""
        text = str(value or "").strip()
        if not text:
            return None
        try:
            async with self.db.acquire() as conn:
                row = await conn.fetchrow(
                """
                SELECT i.id, i.name, i.description, i.price, i.currency,
                       i.category_id, c.name AS category_name, TRUE AS enabled,
                       TRUE AS source_catalog_db,
                       (ma.id IS NOT NULL) AS has_image,
                       ma.alt_text AS image_alt
                FROM catalog_items i
                LEFT JOIN catalog_categories c
                  ON c.tenant_id = i.tenant_id
                 AND c.id = i.category_id
                 AND c.deleted_at IS NULL
                LEFT JOIN media_assets ma
                  ON ma.tenant_id = i.tenant_id
                 AND ma.entity_type = 'catalog_item'
                 AND ma.entity_id = i.id
                 AND ma.status = 'active'
                 AND ma.is_primary = TRUE
                WHERE i.tenant_id = $1
                  AND i.deleted_at IS NULL
                  AND i.is_active = TRUE
                  AND i.is_available = TRUE
                  AND (
                      i.id::text = $2
                      OR lower(i.name) = lower($2)
                      OR lower(COALESCE(i.sku, '')) = lower($2)
                  )
                ORDER BY i.created_at DESC
                LIMIT 1
                """,
                    tenant_id,
                    text,
                )
                if not row:
                    row = await conn.fetchrow(
                    """
                    SELECT i.id, i.name, i.description, i.price, i.currency,
                           i.category_id, c.name AS category_name, TRUE AS enabled,
                           TRUE AS source_catalog_db,
                           (ma.id IS NOT NULL) AS has_image,
                           ma.alt_text AS image_alt
                    FROM catalog_items i
                    LEFT JOIN catalog_categories c
                      ON c.tenant_id = i.tenant_id
                     AND c.id = i.category_id
                     AND c.deleted_at IS NULL
                    LEFT JOIN media_assets ma
                      ON ma.tenant_id = i.tenant_id
                     AND ma.entity_type = 'catalog_item'
                     AND ma.entity_id = i.id
                     AND ma.status = 'active'
                     AND ma.is_primary = TRUE
                    WHERE i.tenant_id = $1
                      AND i.deleted_at IS NULL
                      AND i.is_active = TRUE
                      AND i.is_available = TRUE
                      AND (i.name ILIKE $2 OR $3 ILIKE '%' || i.name || '%')
                    ORDER BY length(i.name) ASC, i.created_at DESC
                    LIMIT 1
                    """,
                        tenant_id,
                        f"%{text}%",
                        text,
                    )
                return dict(row) if row else None
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
            async with self.db.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT i.id, i.name, i.description, i.price, i.currency,
                           i.category_id, c.name AS category_name, TRUE AS enabled,
                           TRUE AS source_catalog_db, FALSE AS has_image, NULL::text AS image_alt
                    FROM catalog_items i
                    LEFT JOIN catalog_categories c
                      ON c.tenant_id = i.tenant_id
                     AND c.id = i.category_id
                     AND c.deleted_at IS NULL
                    WHERE i.tenant_id = $1
                      AND i.deleted_at IS NULL
                      AND i.is_active = TRUE
                      AND i.is_available = TRUE
                      AND (
                          i.id::text = $2
                          OR lower(i.name) = lower($2)
                          OR lower(COALESCE(i.sku, '')) = lower($2)
                      )
                    ORDER BY i.created_at DESC
                    LIMIT 1
                    """,
                    tenant_id,
                    text,
                )
                if not row:
                    row = await conn.fetchrow(
                        """
                        SELECT i.id, i.name, i.description, i.price, i.currency,
                               i.category_id, c.name AS category_name, TRUE AS enabled,
                               TRUE AS source_catalog_db, FALSE AS has_image, NULL::text AS image_alt
                        FROM catalog_items i
                        LEFT JOIN catalog_categories c
                          ON c.tenant_id = i.tenant_id
                         AND c.id = i.category_id
                         AND c.deleted_at IS NULL
                        WHERE i.tenant_id = $1
                          AND i.deleted_at IS NULL
                          AND i.is_active = TRUE
                          AND i.is_available = TRUE
                          AND (i.name ILIKE $2 OR $3 ILIKE '%' || i.name || '%')
                        ORDER BY length(i.name) ASC, i.created_at DESC
                        LIMIT 1
                        """,
                        tenant_id,
                        f"%{text}%",
                        text,
                    )
                return dict(row) if row else None


catalog_repository = CatalogRepository()
