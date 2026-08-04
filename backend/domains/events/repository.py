import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

import asyncpg

from backend.db.client import db_client
from backend.domains.events.models import BusinessEventCreate


def _json_safe(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return json.loads(json.dumps(payload or {}, default=str))


class EventRepository:
    def __init__(self, db=db_client):
        self.db = db

    async def create_lead_created_event(
        self,
        event: BusinessEventCreate,
        *,
        conn: asyncpg.Connection,
    ) -> Optional[Dict[str, Any]]:
        if not (
            event.event_type == "lead.created"
            and event.aggregate_type == "lead"
            and event.aggregate_id is not None
        ):
            raise ValueError(
                "create_lead_created_event requires event_type='lead.created', "
                "aggregate_type='lead', and aggregate_id not None"
            )
        row = await conn.fetchrow(
            """
            INSERT INTO business_events (
                tenant_id, event_type, aggregate_type, aggregate_id, source_domain,
                correlation_id, causation_id, conversation_id, customer_id,
                payload, max_attempts, available_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, COALESCE($12::timestamptz, NOW()))
            ON CONFLICT (tenant_id, event_type, aggregate_id)
            WHERE event_type = 'lead.created'
              AND aggregate_id IS NOT NULL
            DO NOTHING
            RETURNING id, tenant_id, event_type, aggregate_type, aggregate_id,
                      source_domain, correlation_id, causation_id, conversation_id,
                      customer_id, payload, status, attempts, max_attempts,
                      available_at, processed_at, last_error, created_at, updated_at
            """,
            event.tenant_id,
            event.event_type,
            event.aggregate_type,
            event.aggregate_id,
            event.source_domain,
            event.correlation_id,
            event.causation_id,
            event.conversation_id,
            event.customer_id,
            json.dumps(_json_safe(event.payload)),
            event.max_attempts,
            event.available_at,
        )
        return dict(row) if row else None

    async def ensure_schema(self) -> None:
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS business_events (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    aggregate_type TEXT NOT NULL,
                    aggregate_id UUID,
                    source_domain TEXT NOT NULL,
                    correlation_id TEXT,
                    causation_id TEXT,
                    conversation_id UUID,
                    customer_id UUID,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'processed', 'failed', 'skipped')),
                    attempts INT NOT NULL DEFAULT 0,
                    max_attempts INT NOT NULL DEFAULT 3,
                    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    processed_at TIMESTAMPTZ,
                    last_error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    locked_at TIMESTAMPTZ
                )
                """
            )
            await conn.execute("ALTER TABLE business_events ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_business_events_tenant ON business_events (tenant_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_business_events_event_type ON business_events (event_type)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_business_events_status_available ON business_events (status, available_at)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_business_events_aggregate ON business_events (aggregate_type, aggregate_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_business_events_correlation ON business_events (correlation_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_business_events_created_at ON business_events (created_at)")
            await conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_business_events_conversation_turn_message
                ON business_events (tenant_id, event_type, aggregate_id)
                WHERE event_type = 'conversation.turn.received'
                  AND aggregate_id IS NOT NULL
                """
            )

    async def create_event(self, event: BusinessEventCreate) -> Dict[str, Any]:
        await self.ensure_schema()
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO business_events (
                    tenant_id, event_type, aggregate_type, aggregate_id, source_domain,
                    correlation_id, causation_id, conversation_id, customer_id,
                    payload, max_attempts, available_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, COALESCE($12::timestamptz, NOW()))
                RETURNING id, tenant_id, event_type, aggregate_type, aggregate_id,
                          source_domain, correlation_id, causation_id, conversation_id,
                          customer_id, payload, status, attempts, max_attempts,
                          available_at, processed_at, last_error, created_at, updated_at
                """,
                event.tenant_id,
                event.event_type,
                event.aggregate_type,
                event.aggregate_id,
                event.source_domain,
                event.correlation_id,
                event.causation_id,
                event.conversation_id,
                event.customer_id,
                json.dumps(_json_safe(event.payload)),
                event.max_attempts,
                event.available_at,
            )
            return dict(row)

    async def list_events(
        self,
        tenant_id: UUID,
        status: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        await self.ensure_schema()
        async with self.db.acquire() as conn:
            query = """
                SELECT id, tenant_id, event_type, aggregate_type, aggregate_id,
                       source_domain, correlation_id, causation_id, conversation_id,
                       customer_id, payload, status, attempts, max_attempts,
                       available_at, processed_at, last_error, created_at, updated_at
                FROM business_events
                WHERE tenant_id = $1
            """
            params: List[Any] = [tenant_id]
            idx = 2
            if status:
                query += f" AND status = ${idx}"
                params.append(status)
                idx += 1
            if event_type:
                query += f" AND event_type = ${idx}"
                params.append(event_type)
                idx += 1
            query += f" ORDER BY created_at DESC LIMIT ${idx}"
            params.append(limit)
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    async def event_health_summary(self) -> Dict[str, Any]:
        await self.ensure_schema()
        async with self.db.acquire() as conn:
            status_rows = await conn.fetch(
                """
                SELECT status, COUNT(*)::int AS count
                FROM business_events
                GROUP BY status
                """
            )
            type_rows = await conn.fetch(
                """
                SELECT event_type, COUNT(*)::int AS count
                FROM business_events
                GROUP BY event_type
                ORDER BY count DESC, event_type ASC
                """
            )
            source_rows = await conn.fetch(
                """
                SELECT source_domain, COUNT(*)::int AS count
                FROM business_events
                GROUP BY source_domain
                ORDER BY count DESC, source_domain ASC
                """
            )
            ages = await conn.fetchrow(
                """
                SELECT
                    EXTRACT(EPOCH FROM (NOW() - MIN(created_at) FILTER (WHERE status = 'pending')))::int AS oldest_pending_age_seconds,
                    EXTRACT(EPOCH FROM (NOW() - MIN(updated_at) FILTER (WHERE status = 'failed')))::int AS oldest_failed_age_seconds,
                    COUNT(*) FILTER (WHERE status = 'pending' AND attempts > 0)::int AS retrying_count,
                    COUNT(*) FILTER (WHERE status = 'failed' AND attempts < max_attempts)::int AS retryable_failed_count
                FROM business_events
                """
            )
        counts = {status: 0 for status in ("pending", "processing", "processed", "failed", "skipped")}
        counts.update({row["status"]: row["count"] for row in status_rows})
        return {
            "counts": counts,
            "retryable_failed_count": int((ages or {}).get("retryable_failed_count") or 0),
            "retrying_count": int((ages or {}).get("retrying_count") or 0),
            "oldest_pending_event_age_seconds": int((ages or {}).get("oldest_pending_age_seconds") or 0),
            "oldest_failed_event_age_seconds": int((ages or {}).get("oldest_failed_age_seconds") or 0),
            "events_by_type": {row["event_type"]: row["count"] for row in type_rows},
            "events_by_source_domain": {row["source_domain"]: row["count"] for row in source_rows},
        }

    async def list_failed_events(self, tenant_id: Optional[UUID] = None, limit: int = 50) -> List[Dict[str, Any]]:
        return await self._list_events_by_status("failed", tenant_id=tenant_id, limit=limit)

    async def list_skipped_events(self, tenant_id: Optional[UUID] = None, limit: int = 50) -> List[Dict[str, Any]]:
        return await self._list_events_by_status("skipped", tenant_id=tenant_id, limit=limit)

    async def get_event(self, event_id: UUID, tenant_id: Optional[UUID] = None) -> Optional[Dict[str, Any]]:
        await self.ensure_schema()
        async with self.db.acquire() as conn:
            if tenant_id:
                row = await conn.fetchrow(
                    """
                    SELECT id, tenant_id, event_type, aggregate_type, aggregate_id,
                           source_domain, correlation_id, causation_id, conversation_id,
                           customer_id, status, attempts, max_attempts,
                           available_at, processed_at, last_error, created_at, updated_at
                    FROM business_events
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id,
                    event_id,
                )
            else:
                row = await conn.fetchrow(
                    """
                    SELECT id, tenant_id, event_type, aggregate_type, aggregate_id,
                           source_domain, correlation_id, causation_id, conversation_id,
                           customer_id, status, attempts, max_attempts,
                           available_at, processed_at, last_error, created_at, updated_at
                    FROM business_events
                    WHERE id = $1
                    """,
                    event_id,
                )
            return dict(row) if row else None

    async def retry_failed_event(self, event_id: UUID, tenant_id: Optional[UUID] = None) -> Optional[Dict[str, Any]]:
        await self.ensure_schema()
        async with self.db.acquire() as conn:
            if tenant_id:
                row = await conn.fetchrow(
                    """
                    UPDATE business_events
                    SET status = 'pending',
                        available_at = NOW(),
                        last_error = NULL,
                        updated_at = NOW()
                    WHERE tenant_id = $1 AND id = $2 AND status = 'failed'
                    RETURNING id, tenant_id, event_type, aggregate_type, aggregate_id,
                              source_domain, correlation_id, causation_id, conversation_id,
                              customer_id, status, attempts, max_attempts,
                              available_at, processed_at, last_error, created_at, updated_at
                    """,
                    tenant_id,
                    event_id,
                )
            else:
                row = await conn.fetchrow(
                    """
                    UPDATE business_events
                    SET status = 'pending',
                        available_at = NOW(),
                        last_error = NULL,
                        updated_at = NOW()
                    WHERE id = $1 AND status = 'failed'
                    RETURNING id, tenant_id, event_type, aggregate_type, aggregate_id,
                              source_domain, correlation_id, causation_id, conversation_id,
                              customer_id, status, attempts, max_attempts,
                              available_at, processed_at, last_error, created_at, updated_at
                    """,
                    event_id,
                )
            return dict(row) if row else None

    async def _list_events_by_status(
        self,
        status: str,
        tenant_id: Optional[UUID] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        await self.ensure_schema()
        async with self.db.acquire() as conn:
            if tenant_id:
                rows = await conn.fetch(
                    """
                    SELECT id, tenant_id, event_type, aggregate_type, aggregate_id,
                           source_domain, correlation_id, causation_id, conversation_id,
                           customer_id, status, attempts, max_attempts,
                           available_at, processed_at, last_error, created_at, updated_at
                    FROM business_events
                    WHERE tenant_id = $1 AND status = $2
                    ORDER BY updated_at DESC
                    LIMIT $3
                    """,
                    tenant_id,
                    status,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, tenant_id, event_type, aggregate_type, aggregate_id,
                           source_domain, correlation_id, causation_id, conversation_id,
                           customer_id, status, attempts, max_attempts,
                           available_at, processed_at, last_error, created_at, updated_at
                    FROM business_events
                    WHERE status = $1
                    ORDER BY updated_at DESC
                    LIMIT $2
                    """,
                    status,
                    limit,
                )
            return [dict(row) for row in rows]

    async def claim_pending_events(self, limit: int = 25) -> List[Dict[str, Any]]:
        await self.ensure_schema()
        async with self.db.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT id
                    FROM business_events
                    WHERE (
                        status IN ('pending', 'failed')
                        OR (status = 'processing' AND locked_at < NOW() - INTERVAL '5 minutes')
                      )
                      AND available_at <= NOW()
                      AND attempts < max_attempts
                    ORDER BY available_at ASC, created_at ASC
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    limit,
                )
                if not rows:
                    return []
                ids = [row["id"] for row in rows]
                claimed = await conn.fetch(
                    """
                    UPDATE business_events
                    SET status = 'processing',
                        attempts = attempts + 1,
                        updated_at = NOW(),
                        locked_at = NOW(),
                        last_error = NULL
                    WHERE id = ANY($1::uuid[])
                    RETURNING id, tenant_id, event_type, aggregate_type, aggregate_id,
                              source_domain, correlation_id, causation_id, conversation_id,
                              customer_id, payload, status, attempts, max_attempts,
                              available_at, processed_at, last_error, created_at, updated_at, locked_at
                    """,
                    ids,
                )
                return [dict(row) for row in claimed]

    async def claim_exhausted_conversation_turn_events(
        self,
        limit: int = 25,
    ) -> list[dict]:
        await self.ensure_schema()
        async with self.db.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT id
                    FROM business_events
                    WHERE event_type = 'conversation.turn.received'
                      AND status = 'failed'
                      AND attempts >= max_attempts
                      AND processed_at IS NULL
                      AND available_at <= NOW()
                      AND (locked_at IS NULL OR locked_at < NOW() - INTERVAL '5 minutes')
                    ORDER BY available_at ASC, created_at ASC
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    limit,
                )
                if not rows:
                    return []
                ids = [row["id"] for row in rows]
                claimed = await conn.fetch(
                    """
                    UPDATE business_events
                    SET locked_at = NOW(),
                        updated_at = NOW()
                    WHERE id = ANY($1::uuid[])
                    RETURNING id, tenant_id, event_type, aggregate_type, aggregate_id,
                              conversation_id, customer_id, status, attempts, max_attempts,
                              payload, locked_at
                    """,
                    ids,
                )
                return [dict(row) for row in claimed]

    async def defer_malformed_exhausted_event(
        self,
        tenant_id: UUID,
        event_id: UUID,
        *,
        retry_delay_seconds: int = 3600,
    ) -> Optional[Dict[str, Any]]:
        """Release an exhausted malformed event with bounded, payload-free backoff."""
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds)
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE business_events
                SET status = 'failed',
                    available_at = $3,
                    locked_at = NULL,
                    last_error = 'malformed_exhausted_event_authority',
                    updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2
                  AND event_type = 'conversation.turn.received'
                  AND status = 'failed'
                  AND attempts >= max_attempts
                  AND processed_at IS NULL
                RETURNING id, tenant_id, status, attempts, max_attempts,
                          available_at, processed_at, last_error, locked_at
                """,
                tenant_id,
                event_id,
                retry_at,
            )
            return dict(row) if row else None

    async def mark_processed(self, tenant_id: UUID, event_id: UUID) -> Optional[Dict[str, Any]]:
        return await self._mark_terminal(tenant_id, event_id, "processed", None)

    async def mark_skipped(self, tenant_id: UUID, event_id: UUID, reason_code: str) -> Optional[Dict[str, Any]]:
        return await self._mark_terminal(tenant_id, event_id, "skipped", reason_code)

    async def mark_failed(
        self,
        tenant_id: UUID,
        event_id: UUID,
        error: str,
        retry_delay_seconds: int = 60,
    ) -> Optional[Dict[str, Any]]:
        await self.ensure_schema()
        safe_error = " ".join(str(error).split())[:1000]
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds)
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE business_events
                SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'pending' END,
                    available_at = CASE WHEN attempts >= max_attempts THEN available_at ELSE $3 END,
                    last_error = $4,
                    updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2 AND status = 'processing'
                RETURNING id, tenant_id, event_type, aggregate_type, aggregate_id,
                          source_domain, correlation_id, causation_id, conversation_id,
                          customer_id, payload, status, attempts, max_attempts,
                          available_at, processed_at, last_error, created_at, updated_at
                """,
                tenant_id,
                event_id,
                retry_at,
                safe_error,
            )
            return dict(row) if row else None

    async def _mark_terminal(
        self,
        tenant_id: UUID,
        event_id: UUID,
        status: str,
        reason_code: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        await self.ensure_schema()
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE business_events
                SET status = $3,
                    processed_at = NOW(),
                    last_error = $4,
                    updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2 AND status = 'processing'
                RETURNING id, tenant_id, event_type, aggregate_type, aggregate_id,
                          source_domain, correlation_id, causation_id, conversation_id,
                          customer_id, payload, status, attempts, max_attempts,
                          available_at, processed_at, last_error, created_at, updated_at
                """,
                tenant_id,
                event_id,
                status,
                reason_code,
            )
            return dict(row) if row else None


event_repository = EventRepository()
