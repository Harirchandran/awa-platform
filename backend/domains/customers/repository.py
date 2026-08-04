import json
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from backend.db.client import db_client


class CustomerRepository:
    def __init__(self, db=db_client):
        self.db = db

    async def get_or_create_awa_chat_customer(
        self, tenant_id: UUID, channel_subject: str, name: Optional[str] = None, phone_number: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a browser-chat customer without inventing a WhatsApp phone number."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, phone_number, name, consent_status, channel_subject
                FROM customers
                WHERE tenant_id = $1 AND channel_subject = $2 AND deleted_at IS NULL
                """,
                tenant_id,
                channel_subject,
            )
            if row:
                return dict(row)
            row = await conn.fetchrow(
                """
                INSERT INTO customers
                    (id, tenant_id, phone_number, name, consent_status, consent_at,
                     consent_version, consent_method, channel_subject, source_channel, created_at)
                VALUES ($1, $2, $3, $4, 'pending', NULL, 'v1.0',
                        'awa_chat_public_link', $5, 'awa_chat', NOW())
                ON CONFLICT (tenant_id, channel_subject)
                WHERE channel_subject IS NOT NULL
                DO UPDATE SET
                    name = COALESCE(customers.name, EXCLUDED.name),
                    updated_at = NOW()
                RETURNING id, tenant_id, phone_number, name, consent_status, channel_subject
                """,
                uuid4(),
                tenant_id,
                phone_number,
                name,
                channel_subject,
            )
            return dict(row)

    async def update_public_chat_session_customer(
        self, tenant_id: UUID, session_id: UUID, conversation_id: UUID, customer_id: UUID
    ) -> None:
        """Point a public chat session and its conversation at the canonical customer."""
        async with self.db.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE public_chat_sessions
                    SET customer_id = $4,
                        updated_at = NOW()
                    WHERE tenant_id = $1 AND id = $2 AND conversation_id = $3
                    """,
                    tenant_id,
                    session_id,
                    conversation_id,
                    customer_id,
                )
                await conn.execute(
                    """
                    UPDATE conversations
                    SET customer_id = $3
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id,
                    conversation_id,
                    customer_id,
                )

    async def get_or_create_customer(
        self, tenant_id: UUID, phone_number: str, name: Optional[str] = None
    ) -> Dict[str, Any]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, phone_number, name, consent_status
                FROM customers
                WHERE tenant_id = $1 AND phone_number = $2
                """,
                tenant_id,
                phone_number,
            )
            if row:
                return dict(row)
            row = await conn.fetchrow(
                """
                INSERT INTO customers (id, tenant_id, phone_number, name, consent_status, created_at)
                VALUES ($1, $2, $3, $4, 'pending', NOW())
                ON CONFLICT (tenant_id, phone_number) DO UPDATE SET
                    name = COALESCE(customers.name, EXCLUDED.name),
                    updated_at = NOW()
                RETURNING id, tenant_id, phone_number, name, consent_status
                """,
                uuid4(),
                tenant_id,
                phone_number,
                name,
            )
            return dict(row)

    async def update_consent(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        consent_status: str,
        consent_version: str = "v1.0",
        consent_method: str = "whatsapp_opt_in",
    ) -> bool:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE customers
                SET consent_status = $1, consent_at = NOW(),
                    consent_version = $2, consent_method = $3
                WHERE tenant_id = $4 AND id = $5
                """,
                consent_status,
                consent_version,
                consent_method,
                tenant_id,
                customer_id,
            )
            return "UPDATE 1" in result

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
        async with self.db.acquire() as conn:
            updates = []
            values = [tenant_id, customer_id]
            idx = 3
            if name is not None:
                updates.append(f"name = ${idx}")
                values.append(name)
                idx += 1
            if email is not None:
                updates.append(f"email = ${idx}")
                values.append(email)
                idx += 1
            if phone_number is not None:
                updates.append(f"phone_number = ${idx}")
                values.append(phone_number)
                idx += 1
            if metadata is not None:
                updates.append(f"metadata = ${idx}::jsonb")
                values.append(json.dumps(metadata))
                idx += 1
            if consent_status is not None:
                updates.append(f"consent_status = ${idx}")
                values.append(consent_status)
                idx += 1
            if not updates:
                return False
            updates.append("updated_at = NOW()")
            set_clause = ", ".join(updates)
            result = await conn.execute(
                f"UPDATE customers SET {set_clause} WHERE tenant_id = $1 AND id = $2", *values
            )
            return "UPDATE 1" in result

    async def get_customer_by_phone(self, tenant_id: UUID, phone_number: str) -> Optional[Dict[str, Any]]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, phone_number, name, email, consent_status, metadata, channel_subject
                FROM customers
                WHERE tenant_id = $1 AND phone_number = $2 AND deleted_at IS NULL
                """,
                tenant_id,
                phone_number,
            )
            return dict(row) if row else None

    async def delete_customer(self, tenant_id: UUID, customer_id: UUID) -> bool:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM customers WHERE tenant_id = $1 AND id = $2",
                tenant_id,
                customer_id,
            )
            return "DELETE 1" in result

    async def get_customer(self, tenant_id: UUID, customer_id: UUID) -> Optional[Dict[str, Any]]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    cu.id,
                    cu.phone_number,
                    cu.name,
                    cu.email,
                    cu.metadata,
                    cu.consent_status,
                    cu.created_at,
                    cu.created_at AS first_seen,
                    COALESCE((
                        SELECT MAX(COALESCE(c.last_message_at, c.created_at))
                        FROM conversations c
                        WHERE c.tenant_id = cu.tenant_id
                          AND c.customer_id = cu.id
                          AND c.deleted_at IS NULL
                    ), cu.created_at) AS last_interaction,
                    (
                        SELECT COUNT(*)::int
                        FROM conversations c
                        WHERE c.tenant_id = cu.tenant_id
                          AND c.customer_id = cu.id
                          AND c.deleted_at IS NULL
                    ) AS conversation_count,
                    (
                        SELECT COUNT(*)::int
                        FROM bookings b
                        WHERE b.tenant_id = cu.tenant_id
                          AND b.customer_id = cu.id
                    ) AS booking_count,
                    (
                        SELECT COUNT(*)::int
                        FROM orders o
                        WHERE o.tenant_id = cu.tenant_id
                          AND o.customer_id = cu.id
                    ) AS order_count,
                    COALESCE((
                        SELECT SUM(o.total_amount)
                        FROM orders o
                        WHERE o.tenant_id = cu.tenant_id
                          AND o.customer_id = cu.id
                          AND o.status != 'cancelled'
                    ), 0)::float AS total_order_value,
                    COALESCE((
                        SELECT jsonb_agg(
                            jsonb_build_object(
                                'id', ct.id,
                                'name', ct.name,
                                'color', ct.color,
                                'description', ct.description
                            )
                            ORDER BY ct.name
                        )
                        FROM customer_tag_links ctl
                        JOIN customer_tags ct
                          ON ct.id = ctl.tag_id
                         AND ct.tenant_id = ctl.tenant_id
                         AND ct.deleted_at IS NULL
                        WHERE ctl.tenant_id = cu.tenant_id
                          AND ctl.customer_id = cu.id
                    ), '[]'::jsonb) AS tags
                FROM customers cu
                WHERE cu.tenant_id = $1
                  AND cu.id = $2
                  AND cu.deleted_at IS NULL
                """,
                tenant_id,
                customer_id,
            )
            return dict(row) if row else None

    async def list_customers(
        self, tenant_id: UUID, cursor: Optional[str] = None, limit: int = 20, search: Optional[str] = None
    ) -> Dict[str, Any]:
        async with self.db.acquire() as conn:
            query = """
                SELECT
                    cu.id,
                    cu.phone_number,
                    cu.name,
                    cu.email,
                    cu.metadata,
                    cu.consent_status,
                    cu.created_at,
                    cu.created_at AS first_seen,
                    COALESCE((
                        SELECT MAX(COALESCE(c.last_message_at, c.created_at))
                        FROM conversations c
                        WHERE c.tenant_id = cu.tenant_id
                          AND c.customer_id = cu.id
                          AND c.deleted_at IS NULL
                    ), cu.created_at) AS last_interaction,
                    (
                        SELECT COUNT(*)::int
                        FROM conversations c
                        WHERE c.tenant_id = cu.tenant_id
                          AND c.customer_id = cu.id
                          AND c.deleted_at IS NULL
                    ) AS conversation_count,
                    (
                        SELECT COUNT(*)::int
                        FROM bookings b
                        WHERE b.tenant_id = cu.tenant_id
                          AND b.customer_id = cu.id
                    ) AS booking_count,
                    (
                        SELECT COUNT(*)::int
                        FROM orders o
                        WHERE o.tenant_id = cu.tenant_id
                          AND o.customer_id = cu.id
                    ) AS order_count,
                    COALESCE((
                        SELECT SUM(o.total_amount)
                        FROM orders o
                        WHERE o.tenant_id = cu.tenant_id
                          AND o.customer_id = cu.id
                          AND o.status != 'cancelled'
                    ), 0)::float AS total_order_value,
                    COALESCE((
                        SELECT jsonb_agg(
                            jsonb_build_object(
                                'id', ct.id,
                                'name', ct.name,
                                'color', ct.color,
                                'description', ct.description
                            )
                            ORDER BY ct.name
                        )
                        FROM customer_tag_links ctl
                        JOIN customer_tags ct
                          ON ct.id = ctl.tag_id
                         AND ct.tenant_id = ctl.tenant_id
                         AND ct.deleted_at IS NULL
                        WHERE ctl.tenant_id = cu.tenant_id
                          AND ctl.customer_id = cu.id
                    ), '[]'::jsonb) AS tags
                FROM customers cu
                WHERE cu.tenant_id = $1
                  AND cu.deleted_at IS NULL
            """
            params: List[Any] = [tenant_id]
            idx = 2
            if search:
                query += f" AND (cu.name ILIKE '%' || ${idx} || '%' OR cu.phone_number ILIKE '%' || ${idx} || '%' OR cu.email ILIKE '%' || ${idx} || '%')"
                params.append(search)
                idx += 1
            if cursor:
                query += f" AND cu.created_at < ${idx}::timestamptz"
                params.append(cursor)
                idx += 1
            query += f" ORDER BY cu.created_at DESC LIMIT ${idx}"
            params.append(limit + 1)
            rows = await conn.fetch(query, *params)
            items = [dict(r) for r in rows[:limit]]
            next_cursor = str(items[-1]["created_at"].isoformat()) if len(rows) > limit else None
            return {"items": items, "next_cursor": next_cursor}

    # Tags
    async def list_tags(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, color, description, created_at, updated_at
                FROM customer_tags
                WHERE tenant_id = $1 AND deleted_at IS NULL
                ORDER BY lower(name)
                """,
                tenant_id,
            )
            return [dict(row) for row in rows]

    async def create_tag(
        self, tenant_id: UUID, name: str, color: str = "#64748b", description: Optional[str] = None
    ) -> Dict[str, Any]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO customer_tags (tenant_id, name, color, description)
                VALUES ($1, $2, $3, $4)
                RETURNING id, name, color, description, created_at, updated_at
                """,
                tenant_id,
                name,
                color,
                description,
            )
            return dict(row)

    async def update_tag(
        self,
        tenant_id: UUID,
        tag_id: UUID,
        name: Optional[str] = None,
        color: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        async with self.db.acquire() as conn:
            updates = []
            values = [tenant_id, tag_id]
            idx = 3
            if name is not None:
                updates.append(f"name = ${idx}")
                values.append(name)
                idx += 1
            if color is not None:
                updates.append(f"color = ${idx}")
                values.append(color)
                idx += 1
            if description is not None:
                updates.append(f"description = ${idx}")
                values.append(description)
                idx += 1
            if not updates:
                return None
            updates.append("updated_at = NOW()")
            row = await conn.fetchrow(
                f"""
                UPDATE customer_tags
                SET {', '.join(updates)}
                WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                RETURNING id, name, color, description, created_at, updated_at
                """,
                *values,
            )
            return dict(row) if row else None

    async def delete_tag(self, tenant_id: UUID, tag_id: UUID) -> bool:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE customer_tags
                SET deleted_at = NOW(), updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                """,
                tenant_id,
                tag_id,
            )
            return "UPDATE 1" in result

    async def add_tag(
        self, tenant_id: UUID, customer_id: UUID, tag_id: UUID, created_by: Optional[UUID] = None
    ) -> bool:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO customer_tag_links (tenant_id, customer_id, tag_id, created_by)
                SELECT $1, cu.id, ct.id, $4
                FROM customers cu
                JOIN customer_tags ct ON ct.tenant_id = cu.tenant_id
                WHERE cu.tenant_id = $1
                  AND cu.id = $2
                  AND cu.deleted_at IS NULL
                  AND ct.id = $3
                  AND ct.deleted_at IS NULL
                ON CONFLICT (tenant_id, customer_id, tag_id) DO NOTHING
                RETURNING customer_id
                """,
                tenant_id,
                customer_id,
                tag_id,
                created_by,
            )
            if row:
                return True
            exists = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM customer_tag_links
                    WHERE tenant_id = $1 AND customer_id = $2 AND tag_id = $3
                )
                """,
                tenant_id,
                customer_id,
                tag_id,
            )
            return bool(exists)

    async def remove_tag(self, tenant_id: UUID, customer_id: UUID, tag_id: UUID) -> bool:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM customer_tag_links
                WHERE tenant_id = $1 AND customer_id = $2 AND tag_id = $3
                """,
                tenant_id,
                customer_id,
                tag_id,
            )
            return "DELETE 1" in result

    # Notes
    async def list_notes(self, tenant_id: UUID, customer_id: UUID) -> List[Dict[str, Any]]:
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT cn.id, cn.customer_id, cn.author_user_id, u.email AS author_email,
                       cn.note, cn.visibility, cn.metadata, cn.created_at, cn.updated_at
                FROM customer_notes cn
                LEFT JOIN users u ON u.id = cn.author_user_id
                WHERE cn.tenant_id = $1
                  AND cn.customer_id = $2
                  AND cn.deleted_at IS NULL
                ORDER BY cn.created_at DESC
                """,
                tenant_id,
                customer_id,
            )
            return [dict(row) for row in rows]

    async def create_note(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        note: str,
        author_user_id: Optional[UUID],
        visibility: str = "internal",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO customer_notes (tenant_id, customer_id, author_user_id, note, visibility, metadata)
                SELECT $1, cu.id, $3, $4, $5, $6::jsonb
                FROM customers cu
                WHERE cu.tenant_id = $1
                  AND cu.id = $2
                  AND cu.deleted_at IS NULL
                RETURNING id, customer_id, author_user_id, note, visibility, metadata, created_at, updated_at
                """,
                tenant_id,
                customer_id,
                author_user_id,
                note,
                visibility,
                json.dumps(metadata or {}),
            )
            return dict(row) if row else None

    async def update_note(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        note_id: UUID,
        note: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        async with self.db.acquire() as conn:
            updates = []
            values = [tenant_id, customer_id, note_id]
            idx = 4
            if note is not None:
                updates.append(f"note = ${idx}")
                values.append(note)
                idx += 1
            if metadata is not None:
                updates.append(f"metadata = ${idx}::jsonb")
                values.append(json.dumps(metadata))
                idx += 1
            if not updates:
                return None
            updates.append("updated_at = NOW()")
            row = await conn.fetchrow(
                f"""
                UPDATE customer_notes
                SET {', '.join(updates)}
                WHERE tenant_id = $1
                  AND customer_id = $2
                  AND id = $3
                  AND deleted_at IS NULL
                RETURNING id, customer_id, author_user_id, note, visibility, metadata, created_at, updated_at
                """,
                *values,
            )
            return dict(row) if row else None

    async def delete_note(self, tenant_id: UUID, customer_id: UUID, note_id: UUID) -> bool:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE customer_notes
                SET deleted_at = NOW(), updated_at = NOW()
                WHERE tenant_id = $1
                  AND customer_id = $2
                  AND id = $3
                  AND deleted_at IS NULL
                """,
                tenant_id,
                customer_id,
                note_id,
            )
            return "UPDATE 1" in result

    # Timeline
    async def get_timeline(self, tenant_id: UUID, customer_id: UUID, limit: int = 30) -> List[Dict[str, Any]]:
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT occurred_at, event_type, ref_id, title, subtitle, data
                FROM (
                    SELECT cn.created_at AS occurred_at,
                           'note'::text AS event_type,
                           cn.id AS ref_id,
                           'Internal note'::text AS title,
                           left(cn.note, 160) AS subtitle,
                           jsonb_build_object('note', cn.note, 'author_user_id', cn.author_user_id) AS data
                    FROM customer_notes cn
                    WHERE cn.tenant_id = $1
                      AND cn.customer_id = $2
                      AND cn.deleted_at IS NULL
                    UNION ALL
                    SELECT c.created_at AS occurred_at,
                           'conversation'::text AS event_type,
                           c.id AS ref_id,
                           'Conversation started'::text AS title,
                           COALESCE(c.status::text, 'active') AS subtitle,
                           jsonb_build_object('status', c.status, 'channel_type', COALESCE(c.channel_type, 'whatsapp')) AS data
                    FROM conversations c
                    WHERE c.tenant_id = $1
                      AND c.customer_id = $2
                      AND c.deleted_at IS NULL
                    UNION ALL
                    SELECT b.created_at AS occurred_at,
                           'booking'::text AS event_type,
                           b.id AS ref_id,
                           'Booking: ' || b.service_name AS title,
                           b.status::text AS subtitle,
                           jsonb_build_object('status', b.status, 'booking_date', b.booking_date, 'booking_time', b.booking_time) AS data
                    FROM bookings b
                    WHERE b.tenant_id = $1
                      AND b.customer_id = $2
                    UNION ALL
                    SELECT o.created_at AS occurred_at,
                           'order'::text AS event_type,
                           o.id AS ref_id,
                           'Order ' || upper(left(o.status, 1)) || substring(o.status from 2) AS title,
                           o.currency || ' ' || o.total_amount::text AS subtitle,
                           jsonb_build_object('status', o.status, 'total_amount', o.total_amount, 'currency', o.currency, 'payment_status', o.payment_status) AS data
                    FROM orders o
                    WHERE o.tenant_id = $1
                      AND o.customer_id = $2
                ) timeline
                ORDER BY occurred_at DESC
                LIMIT $3
                """,
                tenant_id,
                customer_id,
                limit,
            )
            return [dict(row) for row in rows]

    async def merge_customers(self, tenant_id: UUID, primary_id: UUID, secondary_id: UUID) -> bool:
        """Merge a duplicate customer (secondary) into a primary customer profile."""
        async with self.db.tenant_context(tenant_id) as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE conversations
                    SET status = 'closed',
                        updated_at = NOW(),
                        metadata = COALESCE(metadata, '{}'::jsonb)
                            || jsonb_build_object('closed_reason', 'customer_merged')
                    WHERE tenant_id = $1
                      AND customer_id = $2
                      AND status = 'active'
                      AND channel_type = 'whatsapp'
                      AND channel_session_id IS NULL
                    """,
                    tenant_id,
                    secondary_id,
                )

                # Update references across tables
                tables = ["conversations", "bookings", "orders", "payment_requests", "review_requests", "leads", "customer_notes", "scheduled_messages", "public_chat_sessions"]
                for t in tables:
                    await conn.execute(
                        f"UPDATE {t} SET customer_id = $1 WHERE tenant_id = $2 AND customer_id = $3",
                        primary_id, tenant_id, secondary_id
                    )

                # Handle customer_tag_links carefully
                await conn.execute(
                    """
                    INSERT INTO customer_tag_links (tenant_id, customer_id, tag_id)
                    SELECT tenant_id, $1, tag_id FROM customer_tag_links
                    WHERE tenant_id = $2 AND customer_id = $3
                    ON CONFLICT DO NOTHING
                    """,
                    primary_id, tenant_id, secondary_id
                )
                await conn.execute(
                    "DELETE FROM customer_tag_links WHERE tenant_id = $1 AND customer_id = $2",
                    tenant_id, secondary_id
                )

                # Finally, delete secondary customer
                res = await conn.execute(
                    "DELETE FROM customers WHERE tenant_id = $1 AND id = $2",
                    tenant_id, secondary_id
                )
                return "DELETE 1" in res


customer_repository = CustomerRepository()
