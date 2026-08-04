"""
Database Client Module
Provides async Postgres connection using asyncpg for direct SQL queries.
"""
import asyncpg
import math
import contextvars
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from uuid import UUID
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
from backend.core.config import settings
from backend.core.ai_modes import is_known_ai_mode_input, normalize_ai_mode
from backend.ops.logger import logger
import base64

current_tenant_id: contextvars.ContextVar[Optional[UUID]] = contextvars.ContextVar("current_tenant_id", default=None)

def encode_cursor(timestamp: datetime, resource_id: UUID) -> str:
    s = f"{timestamp.isoformat()}|{resource_id}"
    return base64.b64encode(s.encode("utf-8")).decode("utf-8")

def decode_cursor(cursor_str: str) -> tuple[Optional[datetime], Optional[UUID]]:
    try:
        decoded = base64.b64decode(cursor_str.encode("utf-8")).decode("utf-8")
        if "|" in decoded:
            ts_str, id_str = decoded.split("|", 1)
            return datetime.fromisoformat(ts_str), UUID(id_str)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(cursor_str), None
    except Exception:
        return None, None

@dataclass(frozen=True)
class ConversationSessionCommitOutcome:
    committed: bool
    reason_code: str
    committed_at: Optional[datetime]

@dataclass(frozen=True)
class PublicChatTurnIntakeOutcome:
    created: bool
    message_id: UUID
    event_id: UUID
    provider_message_id: str
    conversation_sequence_number: int
    config_version: int
    message_created_at: datetime
    processing_status: str
    security_disposition: str
    trace_id: UUID

class PublicChatTurnIntakeError(RuntimeError):
    def __init__(self, reason_code: str, message: str = "Intake operation failed"):
        super().__init__(message)
        self.reason_code = reason_code

PUBLIC_CHAT_TERMINAL_FAILURE_TEXT = "I'm sorry, I couldn't complete your request right now. Please try again."

@dataclass(frozen=True)
class PublicChatTerminalFailureOutcome:
    outcome_code: str
    terminal_message_id: Optional[UUID]
    terminal_message_text: Optional[str]
    terminal_message_created_at: Optional[datetime]
    inbound_message_id: UUID
    event_id: UUID
    conversation_id: UUID
    inbound_processing_status: str
    failure_code: Optional[str]

class PublicChatTerminalFailureError(RuntimeError):
    def __init__(self, reason_code: str, message: str = "Terminal failure operation failed"):
        super().__init__(message)
        self.reason_code = reason_code

@dataclass(frozen=True)
class PublicChatTurnReplayRecord:
    intake: PublicChatTurnIntakeOutcome
    tenant_id: UUID
    conversation_id: UUID
    customer_id: UUID
    public_chat_session_id: UUID
    channel_account_id: UUID
    message_text: Optional[str]
    event_status: str

class PublicChatTurnReplayError(RuntimeError):
    def __init__(self, reason_code: str, message: str = "Replay operation failed"):
        super().__init__(message)
        self.reason_code = reason_code

class DatabaseClient:
    """
    Async PostgreSQL client using asyncpg for high-performance queries.
    """
    def __init__(self, database_url: str):
        self.database_url = database_url
        self._pool: Optional[asyncpg.Pool] = None
    async def connect(self):
        """Initialize connection pool"""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=10,
                command_timeout=30
            )
        return self._pool
    async def close(self):
        """Close connection pool"""
        if self._pool:
            await self._pool.close()
            self._pool = None
    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool"""
        if self._pool is None:
            await self.connect()
        async with self._pool.acquire() as conn:
            tenant_id = current_tenant_id.get()
            if tenant_id is not None:
                try:
                    await conn.execute("SELECT set_config('app.current_tenant', $1, false)", str(tenant_id))
                    yield conn
                finally:
                    await conn.execute("SELECT set_config('app.current_tenant', '', false)")
            else:
                yield conn
    @asynccontextmanager
    async def tenant_context(self, tenant_id: UUID):
        """Set the current tenant context for any database connections acquired in this scope."""
        token = current_tenant_id.set(tenant_id)
        try:
            async with self.acquire() as conn:
                yield conn
        finally:
            current_tenant_id.reset(token)

    async def mark_public_chat_turn_processing(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
    ) -> bool:
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                UPDATE messages
                SET processing_status = 'processing'
                WHERE tenant_id = $1
                  AND id = $2
                  AND conversation_id = $3
                  AND direction = 'inbound'
                  AND channel_type = 'awa_chat'
                  AND processing_status IN ('received', 'queued', 'failed', 'processing')
                RETURNING id
                """,
                tenant_id, message_id, conversation_id
            )
            return bool(row)

    async def mark_public_chat_turn_failed(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        retry_count: int,
    ) -> bool:
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                UPDATE messages
                SET processing_status = 'failed',
                    retry_count = GREATEST(retry_count, $4)
                WHERE tenant_id = $1
                  AND id = $2
                  AND conversation_id = $3
                  AND direction = 'inbound'
                  AND channel_type = 'awa_chat'
                  AND processing_status IN ('processing', 'failed', 'queued')
                RETURNING id
                """,
                tenant_id, message_id, conversation_id, retry_count
            )
            return bool(row)

    async def mark_public_chat_turn_retry_queued(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        retry_count: int,
    ) -> bool:
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                UPDATE messages
                SET processing_status = 'queued',
                    retry_count = GREATEST(retry_count, $4)
                WHERE tenant_id = $1
                  AND id = $2
                  AND conversation_id = $3
                  AND direction = 'inbound'
                  AND channel_type = 'awa_chat'
                  AND processing_status IN ('failed', 'queued')
                RETURNING id
                """,
                tenant_id, message_id, conversation_id, retry_count
            )
            return bool(row)

    async def mark_public_chat_turn_completed(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
    ) -> bool:
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                UPDATE messages
                SET processing_status = 'completed'
                WHERE tenant_id = $1
                  AND id = $2
                  AND conversation_id = $3
                  AND direction = 'inbound'
                  AND channel_type = 'awa_chat'
                  AND processing_status IN ('processing', 'queued', 'completed')
                RETURNING id
                """,
                tenant_id, message_id, conversation_id
            )
            return bool(row)

    async def finalize_turn_terminal_failure(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        inbound_message_id: UUID,
        event_id: UUID,
        retry_count: int,
    ) -> PublicChatTerminalFailureOutcome:
        import asyncio
        if not isinstance(retry_count, int) or retry_count < 1:
            raise PublicChatTerminalFailureError("message_invalid")

        async with self.tenant_context(tenant_id) as conn:
            async with conn.transaction():
                try:
                    msg_row = await conn.fetchrow(
                        """
                        SELECT processing_status FROM messages
                        WHERE tenant_id = $1
                          AND id = $2
                          AND conversation_id = $3
                          AND direction = 'inbound'
                          AND channel_type = 'awa_chat'
                        FOR UPDATE
                        """,
                        tenant_id, inbound_message_id, conversation_id
                    )
                    if not msg_row:
                        raise PublicChatTerminalFailureError("message_missing")

                    if msg_row["processing_status"] not in ("received", "queued", "processing", "failed", "dlq", "completed"):
                        raise PublicChatTerminalFailureError("message_invalid")

                    evt_row = await conn.fetchrow(
                        """
                        SELECT status, processed_at FROM business_events
                        WHERE tenant_id = $1
                          AND id = $2
                          AND event_type = 'conversation.turn.received'
                          AND aggregate_type = 'message'
                          AND aggregate_id = $3
                          AND conversation_id = $4
                        FOR UPDATE
                        """,
                        tenant_id, event_id, inbound_message_id, conversation_id
                    )
                    if not evt_row:
                        raise PublicChatTerminalFailureError("event_missing")

                    response_key = f"response_{event_id}"

                    outbound_row = await conn.fetchrow(
                        """
                        SELECT id, raw_payload, message_text, created_at FROM messages
                        WHERE tenant_id = $1
                          AND conversation_id = $2
                          AND direction = 'outbound'
                          AND channel_type = 'awa_chat'
                          AND whatsapp_message_id = $3
                        FOR UPDATE
                        """,
                        tenant_id, conversation_id, response_key
                    )

                    terminal_raw_payload = {
                        "processing_status": "terminal_failed",
                        "failure_code": "processing_retries_exhausted",
                        "recoverable": False,
                        "source_event_id": str(event_id)
                    }

                    if outbound_row:
                        raw = outbound_row["raw_payload"]
                        try:
                            payload_data = json.loads(raw) if isinstance(raw, str) else raw
                        except Exception:
                            payload_data = None

                        is_terminal = False
                        if isinstance(payload_data, dict):
                            is_terminal = (
                                payload_data.get("processing_status") == "terminal_failed" and
                                payload_data.get("failure_code") == "processing_retries_exhausted" and
                                payload_data.get("recoverable") is False and
                                payload_data.get("source_event_id") == str(event_id)
                            )

                        if is_terminal:
                            await conn.execute(
                                """
                                UPDATE messages
                                SET processing_status = 'dlq',
                                    retry_count = GREATEST(retry_count, $3)
                                WHERE tenant_id = $1 AND id = $2
                                """,
                                tenant_id, inbound_message_id, retry_count
                            )
                            new_processed_at = evt_row["processed_at"] or datetime.now(timezone.utc)
                            await conn.execute(
                                """
                                UPDATE business_events
                                SET status = 'processed',
                                    processed_at = $3,
                                    locked_at = NULL,
                                    last_error = 'processing_retries_exhausted',
                                    updated_at = NOW()
                                WHERE tenant_id = $1 AND id = $2
                                """,
                                tenant_id, event_id, new_processed_at
                            )
                            return PublicChatTerminalFailureOutcome(
                                outcome_code="terminal_already_exists",
                                terminal_message_id=outbound_row["id"],
                                terminal_message_text=outbound_row["message_text"],
                                terminal_message_created_at=outbound_row["created_at"],
                                inbound_message_id=inbound_message_id,
                                event_id=event_id,
                                conversation_id=conversation_id,
                                inbound_processing_status="dlq",
                                failure_code="processing_retries_exhausted",
                            )
                        else:
                            await conn.execute(
                                """
                                UPDATE messages
                                SET processing_status = 'completed',
                                    retry_count = GREATEST(retry_count, $3)
                                WHERE tenant_id = $1 AND id = $2
                                """,
                                tenant_id, inbound_message_id, retry_count
                            )
                            new_processed_at = evt_row["processed_at"] or datetime.now(timezone.utc)
                            await conn.execute(
                                """
                                UPDATE business_events
                                SET status = 'processed',
                                    processed_at = $3,
                                    locked_at = NULL,
                                    last_error = NULL,
                                    updated_at = NOW()
                                WHERE tenant_id = $1 AND id = $2
                                """,
                                tenant_id, event_id, new_processed_at
                            )
                            return PublicChatTerminalFailureOutcome(
                                outcome_code="normal_response_exists",
                                terminal_message_id=None,
                                terminal_message_text=None,
                                terminal_message_created_at=None,
                                inbound_message_id=inbound_message_id,
                                event_id=event_id,
                                conversation_id=conversation_id,
                                inbound_processing_status="completed",
                                failure_code=None,
                            )

                    insert_row = await conn.fetchrow(
                        """
                        INSERT INTO messages (
                            id, tenant_id, conversation_id, whatsapp_message_id,
                            provider_message_id, direction, message_type, message_text,
                            channel_type, processing_status, raw_payload, created_at
                        ) VALUES (
                            gen_random_uuid(), $1, $2, $3, $3, 'outbound', 'text', $4,
                            'awa_chat', 'completed', $5::jsonb, NOW()
                        )
                        ON CONFLICT (tenant_id, whatsapp_message_id) DO NOTHING
                        RETURNING id, message_text, created_at
                        """,
                        tenant_id, conversation_id, response_key, PUBLIC_CHAT_TERMINAL_FAILURE_TEXT,
                        json.dumps(terminal_raw_payload)
                    )

                    if not insert_row:
                        outbound_row = await conn.fetchrow(
                            """
                            SELECT id, raw_payload, message_text, created_at FROM messages
                            WHERE tenant_id = $1
                              AND conversation_id = $2
                              AND direction = 'outbound'
                              AND channel_type = 'awa_chat'
                              AND whatsapp_message_id = $3
                            """,
                            tenant_id, conversation_id, response_key
                        )
                        if not outbound_row:
                            raise PublicChatTerminalFailureError("terminal_write_failed")

                        raw = outbound_row["raw_payload"]
                        try:
                            payload_data = json.loads(raw) if isinstance(raw, str) else raw
                        except Exception:
                            payload_data = None

                        is_terminal = False
                        if isinstance(payload_data, dict):
                            is_terminal = (
                                payload_data.get("processing_status") == "terminal_failed" and
                                payload_data.get("failure_code") == "processing_retries_exhausted" and
                                payload_data.get("recoverable") is False and
                                payload_data.get("source_event_id") == str(event_id)
                            )

                        if is_terminal:
                            await conn.execute(
                                """
                                UPDATE messages
                                SET processing_status = 'dlq',
                                    retry_count = GREATEST(retry_count, $3)
                                WHERE tenant_id = $1 AND id = $2
                                """,
                                tenant_id, inbound_message_id, retry_count
                            )
                            new_processed_at = evt_row["processed_at"] or datetime.now(timezone.utc)
                            await conn.execute(
                                """
                                UPDATE business_events
                                SET status = 'processed',
                                    processed_at = $3,
                                    locked_at = NULL,
                                    last_error = 'processing_retries_exhausted',
                                    updated_at = NOW()
                                WHERE tenant_id = $1 AND id = $2
                                """,
                                tenant_id, event_id, new_processed_at
                            )
                            return PublicChatTerminalFailureOutcome(
                                outcome_code="terminal_already_exists",
                                terminal_message_id=outbound_row["id"],
                                terminal_message_text=outbound_row["message_text"],
                                terminal_message_created_at=outbound_row["created_at"],
                                inbound_message_id=inbound_message_id,
                                event_id=event_id,
                                conversation_id=conversation_id,
                                inbound_processing_status="dlq",
                                failure_code="processing_retries_exhausted",
                            )
                        else:
                            await conn.execute(
                                """
                                UPDATE messages
                                SET processing_status = 'completed',
                                    retry_count = GREATEST(retry_count, $3)
                                WHERE tenant_id = $1 AND id = $2
                                """,
                                tenant_id, inbound_message_id, retry_count
                            )
                            new_processed_at = evt_row["processed_at"] or datetime.now(timezone.utc)
                            await conn.execute(
                                """
                                UPDATE business_events
                                SET status = 'processed',
                                    processed_at = $3,
                                    locked_at = NULL,
                                    last_error = NULL,
                                    updated_at = NOW()
                                WHERE tenant_id = $1 AND id = $2
                                """,
                                tenant_id, event_id, new_processed_at
                            )
                            return PublicChatTerminalFailureOutcome(
                                outcome_code="normal_response_exists",
                                terminal_message_id=None,
                                terminal_message_text=None,
                                terminal_message_created_at=None,
                                inbound_message_id=inbound_message_id,
                                event_id=event_id,
                                conversation_id=conversation_id,
                                inbound_processing_status="completed",
                                failure_code=None,
                            )

                    await conn.execute(
                        """
                        UPDATE messages
                        SET processing_status = 'dlq',
                            retry_count = GREATEST(retry_count, $3)
                        WHERE tenant_id = $1 AND id = $2
                        """,
                        tenant_id, inbound_message_id, retry_count
                    )
                    new_processed_at = evt_row["processed_at"] or datetime.now(timezone.utc)
                    await conn.execute(
                        """
                        UPDATE business_events
                        SET status = 'processed',
                            processed_at = $3,
                            locked_at = NULL,
                            last_error = 'processing_retries_exhausted',
                            updated_at = NOW()
                        WHERE tenant_id = $1 AND id = $2
                        """,
                        tenant_id, event_id, new_processed_at
                    )
                    return PublicChatTerminalFailureOutcome(
                        outcome_code="terminal_created",
                        terminal_message_id=insert_row["id"],
                        terminal_message_text=insert_row["message_text"],
                        terminal_message_created_at=insert_row["created_at"],
                        inbound_message_id=inbound_message_id,
                        event_id=event_id,
                        conversation_id=conversation_id,
                        inbound_processing_status="dlq",
                        failure_code="processing_retries_exhausted",
                    )
                except PublicChatTerminalFailureError:
                    raise
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    raise PublicChatTerminalFailureError("terminal_write_failed") from e

    async def _tables_exist(self, conn: asyncpg.Connection, table_names: List[str]) -> Dict[str, bool]:
        """Return table availability for optional feature joins."""
        rows = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = ANY($1::text[])
            """,
            table_names,
        )
        found = {row["table_name"] for row in rows}
        return {name: name in found for name in table_names}
    # ==================== CONVERSATION SESSIONS ====================
    async def get_conversation_session(self, tenant_id: UUID, conversation_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Fetch conversation session from DB.
        Returns None if not found.
        """
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, conversation_id, current_module, current_intent,
                       config_version, state_json, expires_at, updated_at
                FROM conversation_sessions
                WHERE tenant_id = $1 AND conversation_id = $2
                """,
                tenant_id, conversation_id
            )
            if row:
                return dict(row)
            return None
    async def create_conversation_session(
        self,
        session_id: UUID,
        tenant_id: UUID,
        conversation_id: UUID,
        state_json: Dict[str, Any],
        config_version: int,
        current_module: Optional[str] = None,
        current_intent: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Insert new conversation session.
        """
        import json
        if not isinstance(config_version, int) or isinstance(config_version, bool) or config_version < 1:
            raise ValueError("Invalid session config version")
        if state_json.get("config_version") != config_version:
            raise ValueError("Session config version mismatch")
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO conversation_sessions
                    (id, tenant_id, conversation_id, current_module, current_intent, config_version, state_json, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                ON CONFLICT (conversation_id) DO NOTHING
                RETURNING id, tenant_id, conversation_id, current_module, current_intent, config_version, state_json, updated_at
                """,
                session_id, tenant_id, conversation_id, current_module, current_intent, config_version, json.dumps(state_json)
            )
            if not row:
                row = await conn.fetchrow(
                    """
                    SELECT id, tenant_id, conversation_id, current_module, current_intent, config_version, state_json, updated_at
                    FROM conversation_sessions
                    WHERE tenant_id = $1 AND conversation_id = $2
                    """,
                    tenant_id, conversation_id,
                )
                if not row:
                    raise ValueError("Conversation session tenant mismatch")
            return dict(row)
    async def update_conversation_session(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        state_json: Dict[str, Any],
        config_version: int,
        current_module: Optional[str] = None,
        current_intent: Optional[str] = None
    ) -> bool:
        """
        Update existing conversation session state.
        """
        import json
        if not isinstance(config_version, int) or isinstance(config_version, bool) or config_version < 1:
            raise ValueError("Invalid session config version")
        if state_json.get("config_version") != config_version:
            raise ValueError("Session config version mismatch")
        async with self.tenant_context(tenant_id) as conn:
            result = await conn.execute(
                """
                UPDATE conversation_sessions
                SET state_json = $1, config_version = $2, current_module = $3, current_intent = $4, updated_at = NOW()
                WHERE tenant_id = $5 AND conversation_id = $6
                """,
                json.dumps(state_json), config_version, current_module, current_intent, tenant_id, conversation_id
            )
            return result == "UPDATE 1"

    async def commit_conversation_session_state(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        state_json: Dict[str, Any],
        config_version: int,
        current_module: Optional[str],
        current_intent: Optional[str],
        expected_last_updated_at: Optional[datetime],
        candidate_workflow_state: str,
        escalation_reason: Optional[str] = None,
    ) -> ConversationSessionCommitOutcome:
        """
        Durable transactional session state update with pessimistic locking,
        optimistic concurrency, and escalation status safety.
        """
        if not isinstance(config_version, int) or isinstance(config_version, bool) or config_version < 1:
            raise ValueError("Invalid session config version")
        if state_json.get("config_version") != config_version:
            raise ValueError("Session config version mismatch")

        def parse_db_datetime(dt_val: Any) -> Optional[datetime]:
            if not dt_val:
                return None
            if isinstance(dt_val, datetime):
                return dt_val.astimezone(timezone.utc) if dt_val.tzinfo else dt_val.replace(tzinfo=timezone.utc)
            if isinstance(dt_val, str):
                normalized = dt_val.replace("Z", "+00:00")
                try:
                    dt = datetime.fromisoformat(normalized)
                    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                except Exception:
                    return None
            return None

        async with self.tenant_context(tenant_id) as conn:
            async with conn.transaction():
                # Step 1 — Lock the session row
                row = await conn.fetchrow(
                    """
                    SELECT tenant_id, state_json, current_module, current_intent
                    FROM conversation_sessions
                    WHERE tenant_id = $1 AND conversation_id = $2
                    FOR UPDATE
                    """,
                    tenant_id, conversation_id
                )

                # Step 2 — Missing row
                if not row:
                    return ConversationSessionCommitOutcome(
                        committed=False,
                        reason_code="session_missing",
                        committed_at=None
                    )

                # Step 3 — Tenant protection
                if row["tenant_id"] != tenant_id:
                    return ConversationSessionCommitOutcome(
                        committed=False,
                        reason_code="tenant_mismatch",
                        committed_at=None
                    )

                # Step 4 — Deserialize persisted state
                db_state_json = row["state_json"]
                if isinstance(db_state_json, str):
                    try:
                        db_state_json = json.loads(db_state_json)
                    except Exception:
                        db_state_json = {}

                # Step 5 — Escalation guard
                persisted_workflow_state = (db_state_json or {}).get("state", {}).get("workflow_state")
                if persisted_workflow_state and str(persisted_workflow_state).upper() == "ESCALATED":
                    if str(candidate_workflow_state).upper() != "ESCALATED":
                        return ConversationSessionCommitOutcome(
                            committed=False,
                            reason_code="escalation_guard",
                            committed_at=None
                        )

                # Step 6 — Optimistic concurrency
                persisted_last_updated_at_str = (db_state_json or {}).get("state", {}).get("last_updated_at")
                persisted_dt = parse_db_datetime(persisted_last_updated_at_str)

                if expected_last_updated_at is not None:
                    expected_dt = expected_last_updated_at.astimezone(timezone.utc) if expected_last_updated_at.tzinfo else expected_last_updated_at.replace(tzinfo=timezone.utc)
                    if persisted_dt != expected_dt:
                        return ConversationSessionCommitOutcome(
                            committed=False,
                            reason_code="optimistic_conflict",
                            committed_at=None
                        )

                # Use one committed timestamp consistently
                candidate_ts_str = state_json.get("state", {}).get("last_updated_at")
                if candidate_ts_str:
                    committed_at = parse_db_datetime(candidate_ts_str) or datetime.now(timezone.utc)
                else:
                    committed_at = datetime.now(timezone.utc)

                # Sync timestamp to state_json
                if "state" in state_json:
                    state_json["state"]["last_updated_at"] = committed_at.isoformat()

                # Step 7 — Commit session state
                await conn.execute(
                    """
                    UPDATE conversation_sessions
                    SET state_json = $1,
                        config_version = $2,
                        current_module = $3,
                        current_intent = $4,
                        updated_at = $5
                    WHERE tenant_id = $6 AND conversation_id = $7
                    """,
                    json.dumps(state_json),
                    config_version,
                    current_module,
                    current_intent,
                    committed_at,
                    tenant_id,
                    conversation_id
                )

                # Step 8 — Synchronize conversation status in the same transaction
                prev_state_str = str(persisted_workflow_state).upper() if persisted_workflow_state else ""
                cand_state_str = str(candidate_workflow_state).upper()

                if cand_state_str == "ESCALATED" and prev_state_str != "ESCALATED":
                    reason = (escalation_reason or "Customer requested human handoff")[:500]
                    await conn.execute(
                        """
                        UPDATE conversations
                        SET status = 'escalated',
                            escalation_reason = COALESCE($3, escalation_reason),
                            escalated_at = COALESCE(escalated_at, NOW()),
                            resolved_at = NULL,
                            resume_mode = 'customer',
                            updated_at = NOW()
                        WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                        """,
                        tenant_id, conversation_id, reason
                    )
                elif prev_state_str == "ESCALATED" and cand_state_str != "ESCALATED":
                    await conn.execute(
                        """
                        UPDATE conversations
                        SET status = 'active',
                            resolved_at = NOW(),
                            assigned_to = NULL,
                            escalated_at = NULL,
                            escalation_reason = NULL,
                            resume_mode = 'customer',
                            updated_at = NOW()
                        WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                        """,
                        tenant_id, conversation_id
                    )

                # Step 9 — Return committed outcome
                return ConversationSessionCommitOutcome(
                    committed=True,
                    reason_code="committed",
                    committed_at=committed_at
                )

    async def create_or_get_public_chat_turn_intake(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        customer_id: UUID,
        public_chat_session_id: UUID,
        channel_account_id: UUID,
        provider_message_id: str,
        message_text: Optional[str],
        message_raw_payload: Dict[str, Any],
        security_disposition: str,
        trace_id: UUID,
        event_max_attempts: int,
    ) -> PublicChatTurnIntakeOutcome:
        import uuid
        import json
        import asyncio
        from uuid import UUID

        # 11.1 Input validation
        if security_disposition not in ("none", "prompt_security_flagged"):
            raise PublicChatTurnIntakeError(
                reason_code="intake_write_failed",
                message="Invalid security disposition"
            )
        if not isinstance(event_max_attempts, int) or event_max_attempts < 1 or event_max_attempts > 20:
            raise PublicChatTurnIntakeError(
                reason_code="intake_write_failed",
                message="Invalid event_max_attempts"
            )
        if not provider_message_id or not isinstance(provider_message_id, str) or len(provider_message_id) > 1000:
            raise PublicChatTurnIntakeError(
                reason_code="intake_write_failed",
                message="Invalid provider_message_id"
            )

        # Prepare safe payload to store
        payload_to_store = dict(message_raw_payload) if message_raw_payload else {}
        payload_to_store["security_disposition"] = security_disposition

        # Generate a candidate message ID
        message_id_candidate = uuid.uuid4()

        try:
            async with self.tenant_context(tenant_id) as conn:
                async with conn.transaction():
                    # 11.3 Insert the inbound message first
                    row = await conn.fetchrow(
                        """
                        INSERT INTO messages (
                            id, tenant_id, conversation_id, whatsapp_message_id, provider_message_id,
                            direction, message_type, processing_status, message_text, raw_payload, channel_type, created_at
                        )
                        VALUES ($1, $2, $3, $4, $5, 'inbound', 'text', 'received', $6, $7::jsonb, 'awa_chat', NOW())
                        ON CONFLICT (tenant_id, whatsapp_message_id)
                        DO NOTHING
                        RETURNING id, created_at, processing_status
                        """,
                        message_id_candidate,
                        tenant_id,
                        conversation_id,
                        provider_message_id,
                        provider_message_id,
                        message_text,
                        json.dumps(payload_to_store)
                    )

                    # 11.4 Duplicate-message branch
                    if row is None:
                        # Select the existing message
                        existing_msg = await conn.fetchrow(
                            """
                            SELECT id, created_at, processing_status FROM messages
                            WHERE tenant_id = $1
                              AND whatsapp_message_id = $2
                              AND conversation_id = $3
                              AND direction = 'inbound'
                              AND channel_type = 'awa_chat'
                            """,
                            tenant_id,
                            provider_message_id,
                            conversation_id
                        )
                        if not existing_msg:
                            raise PublicChatTurnIntakeError("intake_write_failed", "Duplicate found on conflict but could not select row")

                        original_msg_id = existing_msg["id"]
                        original_msg_created_at = existing_msg["created_at"]
                        original_msg_processing_status = existing_msg["processing_status"]

                        # Select its existing intake event
                        existing_event = await conn.fetchrow(
                            """
                            SELECT id, conversation_id, customer_id, payload FROM business_events
                            WHERE tenant_id = $1
                              AND event_type = 'conversation.turn.received'
                              AND aggregate_type = 'message'
                              AND aggregate_id = $2
                            """,
                            tenant_id,
                            original_msg_id
                        )
                        if not existing_event:
                            raise PublicChatTurnIntakeError("existing_intake_event_missing", "Missing duplicate event")

                        event_payload = existing_event["payload"]
                        if isinstance(event_payload, str):
                            try:
                                event_payload = json.loads(event_payload)
                            except Exception:
                                raise PublicChatTurnIntakeError("existing_intake_event_invalid", "Malformed duplicate event payload")

                        try:
                            seq = event_payload["conversation_sequence_number"]
                            cfg_ver = event_payload["config_version"]
                            sec_disp = event_payload["security_disposition"]
                            trace_id_str = event_payload["trace_id"]
                            event_trace_id = UUID(trace_id_str)
                        except Exception:
                            raise PublicChatTurnIntakeError("existing_intake_event_invalid", "Invalid duplicate event payload fields")

                        # Validate
                        if not isinstance(seq, int) or seq <= 0:
                            raise PublicChatTurnIntakeError("existing_intake_event_invalid", "Invalid sequence in duplicate event")
                        if not isinstance(cfg_ver, int) or cfg_ver <= 0:
                            raise PublicChatTurnIntakeError("existing_intake_event_invalid", "Invalid config version in duplicate event")
                        if sec_disp not in ("none", "prompt_security_flagged"):
                            raise PublicChatTurnIntakeError("existing_intake_event_invalid", "Invalid security disposition in duplicate event")

                        if existing_event["conversation_id"] != conversation_id:
                            raise PublicChatTurnIntakeError("existing_intake_event_invalid", "Duplicate event conversation_id mismatch")
                        if existing_event["customer_id"] is not None and existing_event["customer_id"] != customer_id:
                            raise PublicChatTurnIntakeError("existing_intake_event_invalid", "Duplicate event customer_id mismatch")

                        return PublicChatTurnIntakeOutcome(
                            created=False,
                            message_id=original_msg_id,
                            event_id=existing_event["id"],
                            provider_message_id=provider_message_id,
                            conversation_sequence_number=seq,
                            config_version=cfg_ver,
                            message_created_at=original_msg_created_at,
                            processing_status=original_msg_processing_status,
                            security_disposition=sec_disp,
                            trace_id=event_trace_id
                        )

                    # 11.5 New-message sequence and config binding
                    session_row = await conn.fetchrow(
                        """
                        SELECT config_version, sequence_number, state_json, current_module, current_intent
                        FROM conversation_sessions
                        WHERE tenant_id = $1 AND conversation_id = $2
                        FOR UPDATE
                        """,
                        tenant_id,
                        conversation_id
                    )

                    if session_row:
                        pinned_config_version = session_row["config_version"]
                        session_state = session_row["state_json"]
                        if isinstance(session_state, str):
                            try:
                                session_state = json.loads(session_state)
                            except Exception:
                                session_state = None
                        mirrored_config_version = session_state.get("config_version") if isinstance(session_state, dict) else None
                        if (
                            not isinstance(pinned_config_version, int)
                            or isinstance(pinned_config_version, bool)
                            or pinned_config_version < 1
                            or not isinstance(mirrored_config_version, int)
                            or isinstance(mirrored_config_version, bool)
                            or mirrored_config_version < 1
                            or mirrored_config_version != pinned_config_version
                        ):
                            raise PublicChatTurnIntakeError("session_config_mismatch", "Session config binding is inconsistent")
                        new_sequence = session_row["sequence_number"] + 1
                        from backend.pipeline.config_binding import state_payload_has_active_work
                        if not state_payload_has_active_work(
                            session_state,
                            current_module=session_row.get("current_module"),
                            current_intent=session_row.get("current_intent"),
                        ):
                            active_cfg_row = await conn.fetchrow(
                                """
                                SELECT version FROM tenant_configs
                                WHERE tenant_id = $1 AND is_active = true
                                ORDER BY version DESC
                                LIMIT 1
                                """,
                                tenant_id,
                            )
                            if not active_cfg_row:
                                raise PublicChatTurnIntakeError("active_config_missing", "No active tenant configuration found")
                            active_version = active_cfg_row["version"]
                            if active_version != pinned_config_version:
                                session_state["config_version"] = active_version
                                pinned_config_version = active_version
                        await conn.execute(
                            """
                            UPDATE conversation_sessions
                            SET sequence_number = $1,
                                config_version = $2,
                                state_json = $3::jsonb,
                                updated_at = NOW()
                            WHERE tenant_id = $4 AND conversation_id = $5
                            """,
                            new_sequence,
                            pinned_config_version,
                            json.dumps(session_state),
                            tenant_id,
                            conversation_id,
                        )
                    else:
                        active_cfg_row = await conn.fetchrow(
                            """
                            SELECT version FROM tenant_configs
                            WHERE tenant_id = $1 AND is_active = true
                            ORDER BY version DESC
                            LIMIT 1
                            """,
                            tenant_id
                        )
                        if not active_cfg_row:
                            raise PublicChatTurnIntakeError("active_config_missing", "No active tenant configuration found")
                        pinned_config_version = active_cfg_row["version"]
                        new_sequence = 1
                        await conn.execute(
                            """
                            INSERT INTO conversation_sessions (
                                id, tenant_id, conversation_id, config_version, sequence_number, state_json, updated_at
                            )
                            VALUES (gen_random_uuid(), $1, $2, $3, $4, jsonb_build_object('config_version', $3, 'state', '{}'::jsonb), NOW())
                            """,
                            tenant_id,
                            conversation_id,
                            pinned_config_version,
                            new_sequence
                        )

                    # 11.6 Insert the durable intake event
                    event_payload = {
                        "public_chat_session_id": str(public_chat_session_id),
                        "channel_account_id": str(channel_account_id),
                        "conversation_sequence_number": new_sequence,
                        "config_version": pinned_config_version,
                        "security_disposition": security_disposition,
                        "trace_id": str(trace_id)
                    }

                    event_id = await conn.fetchval(
                        """
                        INSERT INTO business_events (
                            tenant_id, event_type, aggregate_type, aggregate_id, source_domain,
                            correlation_id, causation_id, conversation_id, customer_id,
                            payload, status, attempts, max_attempts, available_at, updated_at
                        )
                        VALUES ($1, 'conversation.turn.received', 'message', $2, 'conversation',
                                $3, $4, $5, $6, $7::jsonb, 'pending', 0, $8, NOW(), NOW())
                        RETURNING id
                        """,
                        tenant_id,
                        message_id_candidate,
                        str(trace_id),
                        provider_message_id,
                        conversation_id,
                        customer_id,
                        json.dumps(event_payload),
                        event_max_attempts
                    )

                    return PublicChatTurnIntakeOutcome(
                        created=True,
                        message_id=message_id_candidate,
                        event_id=event_id,
                        provider_message_id=provider_message_id,
                        conversation_sequence_number=new_sequence,
                        config_version=pinned_config_version,
                        message_created_at=row["created_at"],
                        processing_status=row["processing_status"],
                        security_disposition=security_disposition,
                        trace_id=trace_id
                    )
        except asyncio.CancelledError:
            raise
        except PublicChatTurnIntakeError:
            raise
        except Exception as exc:
            raise PublicChatTurnIntakeError("intake_write_failed") from exc

    async def get_public_chat_turn_for_replay(
        self,
        *,
        tenant_id: UUID,
        event_id: UUID,
    ) -> PublicChatTurnReplayRecord:
        import asyncio
        import json
        try:
            async with self.tenant_context(tenant_id) as conn:
                event_row = await conn.fetchrow(
                    """
                    SELECT id, tenant_id, conversation_id, customer_id, aggregate_id, aggregate_type, event_type, payload, status, attempts, max_attempts
                    FROM business_events
                    WHERE id = $1 AND tenant_id = $2 AND event_type = 'conversation.turn.received' AND aggregate_type = 'message'
                    """,
                    event_id,
                    tenant_id,
                )
                if not event_row:
                    raise PublicChatTurnReplayError("event_missing")

                if event_row["tenant_id"] != tenant_id:
                    raise PublicChatTurnReplayError("event_invalid")
                if not event_row["conversation_id"] or not isinstance(event_row["conversation_id"], UUID):
                    raise PublicChatTurnReplayError("event_invalid")
                if not event_row["customer_id"] or not isinstance(event_row["customer_id"], UUID):
                    raise PublicChatTurnReplayError("event_invalid")
                if not event_row["aggregate_id"] or not isinstance(event_row["aggregate_id"], UUID):
                    raise PublicChatTurnReplayError("event_invalid")

                payload = event_row["payload"]
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception as exc:
                        raise PublicChatTurnReplayError("event_invalid") from exc
                if not isinstance(payload, dict):
                    raise PublicChatTurnReplayError("event_invalid")

                try:
                    public_chat_session_id = UUID(payload.get("public_chat_session_id"))
                    channel_account_id = UUID(payload.get("channel_account_id"))
                    seq = payload.get("conversation_sequence_number")
                    if not isinstance(seq, int) or seq <= 0:
                        raise ValueError()
                    cfg_ver = payload.get("config_version")
                    if not isinstance(cfg_ver, int) or cfg_ver <= 0:
                        raise ValueError()
                    sec_disp = payload.get("security_disposition")
                    if sec_disp not in ("none", "prompt_security_flagged"):
                        raise ValueError()
                    trace_id = UUID(payload.get("trace_id"))
                except Exception as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    raise PublicChatTurnReplayError("event_invalid") from exc

                msg_row = await conn.fetchrow(
                    """
                    SELECT id, conversation_id, whatsapp_message_id, provider_message_id, message_text, created_at, processing_status, direction, channel_type
                    FROM messages
                    WHERE tenant_id = $1 AND id = $2 AND conversation_id = $3 AND direction = 'inbound' AND channel_type = 'awa_chat'
                    """,
                    tenant_id,
                    event_row["aggregate_id"],
                    event_row["conversation_id"],
                )
                if not msg_row:
                    raise PublicChatTurnReplayError("message_missing")

                if not msg_row["provider_message_id"]:
                    raise PublicChatTurnReplayError("message_invalid")
                if not isinstance(msg_row["created_at"], datetime):
                    raise PublicChatTurnReplayError("message_invalid")
                if msg_row["processing_status"] not in ("received", "failed", "queued", "processing", "completed"):
                    raise PublicChatTurnReplayError("message_invalid")

                session_row = await conn.fetchrow(
                    """
                    SELECT id, tenant_id, link_id, customer_id, conversation_id
                    FROM public_chat_sessions
                    WHERE id = $1 AND tenant_id = $2
                    """,
                    public_chat_session_id,
                    tenant_id,
                )
                if not session_row:
                    raise PublicChatTurnReplayError("session_invalid")

                if (
                    session_row["conversation_id"] != event_row["conversation_id"]
                    or session_row["customer_id"] != event_row["customer_id"]
                    or session_row["link_id"] != channel_account_id
                ):
                    raise PublicChatTurnReplayError("session_invalid")

                intake = PublicChatTurnIntakeOutcome(
                    created=False,
                    message_id=msg_row["id"],
                    event_id=event_row["id"],
                    provider_message_id=msg_row["provider_message_id"],
                    conversation_sequence_number=seq,
                    config_version=cfg_ver,
                    message_created_at=msg_row["created_at"],
                    processing_status=msg_row["processing_status"],
                    security_disposition=sec_disp,
                    trace_id=trace_id,
                )
                return PublicChatTurnReplayRecord(
                    intake=intake,
                    tenant_id=tenant_id,
                    conversation_id=event_row["conversation_id"],
                    customer_id=event_row["customer_id"],
                    public_chat_session_id=public_chat_session_id,
                    channel_account_id=channel_account_id,
                    message_text=msg_row["message_text"],
                    event_status=event_row["status"],
                )
        except asyncio.CancelledError:
            raise
        except PublicChatTurnReplayError:
            raise
        except Exception as exc:
            raise PublicChatTurnReplayError("replay_read_failed") from exc

    async def mark_public_chat_turn_queued(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        event_id: UUID,
    ) -> bool:
        import asyncio
        try:
            async with self.tenant_context(tenant_id) as conn:
                async with conn.transaction():
                    msg_row = await conn.fetchrow(
                        """
                        SELECT processing_status FROM messages
                        WHERE tenant_id = $1
                          AND id = $2
                          AND conversation_id = $3
                          AND direction = 'inbound'
                          AND channel_type = 'awa_chat'
                        FOR UPDATE
                        """,
                        tenant_id,
                        message_id,
                        conversation_id,
                    )
                    if not msg_row:
                        return False

                    evt_row = await conn.fetchrow(
                        """
                        SELECT status, processed_at FROM business_events
                        WHERE tenant_id = $1
                          AND id = $2
                          AND event_type = 'conversation.turn.received'
                          AND aggregate_type = 'message'
                          AND aggregate_id = $3
                          AND conversation_id = $4
                        FOR UPDATE
                        """,
                        tenant_id,
                        event_id,
                        message_id,
                        conversation_id,
                    )
                    if not evt_row:
                        return False

                    curr_msg_status = msg_row["processing_status"]
                    if curr_msg_status not in ("received", "failed", "queued", "processing", "completed"):
                        return False

                    curr_evt_status = evt_row["status"]
                    if curr_evt_status not in ("pending", "processing", "processed"):
                        return False

                    if curr_msg_status in ("received", "failed"):
                        await conn.execute(
                            """
                            UPDATE messages
                            SET processing_status = 'queued'
                            WHERE tenant_id = $1 AND id = $2
                            """,
                            tenant_id,
                            message_id,
                        )

                    new_processed_at = evt_row["processed_at"] or datetime.now(timezone.utc)
                    await conn.execute(
                        """
                        UPDATE business_events
                        SET status = 'processed',
                            processed_at = $3,
                            locked_at = NULL,
                            last_error = NULL,
                            updated_at = NOW()
                        WHERE tenant_id = $1 AND id = $2
                        """,
                        tenant_id,
                        event_id,
                        new_processed_at,
                    )
                    return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise PublicChatTurnReplayError("queue_commit_failed") from exc

    # ==================== TENANT ROUTING ====================
    async def get_tenant_by_wa_phone_number_id(self, phone_number_id: str) -> Optional[Dict[str, Any]]:
        """
        Resolve WhatsApp phone_number_id to tenant.
        This is the primary tenant routing mechanism for incoming messages.
        """
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT wa.tenant_id, wa.phone_number, t.name AS tenant_name,
                       t.status AS tenant_status, t.slug AS tenant_slug, t.ai_mode
                FROM whatsapp_accounts wa
                JOIN tenants t ON t.id = wa.tenant_id
                WHERE wa.phone_number_id = $1 AND t.deleted_at IS NULL
                """,
                phone_number_id
            )
            if row:
                return dict(row)
            return None
    async def get_tenant_by_wa_phone(self, phone_number: str) -> Optional[Dict[str, Any]]:
        """
        Fallback: Resolve WhatsApp phone number to tenant.
        """
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT wa.tenant_id, wa.phone_number, t.name AS tenant_name,
                       t.status AS tenant_status, t.slug AS tenant_slug, t.ai_mode
                FROM whatsapp_accounts wa
                JOIN tenants t ON t.id = wa.tenant_id
                WHERE wa.phone_number = $1 AND t.deleted_at IS NULL
                """,
                phone_number
            )
            if row:
                return dict(row)
            return None
    async def get_tenant_ai_mode(self, tenant_id: UUID) -> str:
        """
        Fetch the tenant ai_mode. Legacy 'concierge' rows normalize at use boundaries.
        """
        async with self.tenant_context(tenant_id) as conn:
            mode = await conn.fetchval(
                "SELECT ai_mode FROM tenants WHERE id = $1 AND deleted_at IS NULL",
                tenant_id
            )
            return normalize_ai_mode(mode)
    async def get_subscription_plan_by_code(self, plan_code: str) -> Optional[Dict[str, Any]]:
        """Fetch an active subscription plan by internal plan_code."""
        normalized = (plan_code or "trial").lower()
        async with self.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """
                    SELECT plan_code, name, monthly_price_cents, currency, razorpay_plan_id,
                           features_json, limits_json, is_active
                    FROM subscription_plans
                    WHERE lower(plan_code) = $1 AND is_active = true
                    LIMIT 1
                    """,
                    normalized,
                )
            except asyncpg.UndefinedColumnError:
                row = None
            return dict(row) if row else None
    # ==================== TENANT CONFIGS ====================
    async def get_active_config_version(self, tenant_id: UUID) -> Optional[int]:
        """
        Get the active config version for a tenant.
        """
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT version FROM tenant_configs
                WHERE tenant_id = $1 AND is_active = true
                ORDER BY version DESC
                LIMIT 1
                """,
                tenant_id
            )
            if row:
                return row['version']
            return None
    async def get_tenant_config(self, tenant_id: UUID, version: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Get tenant config. If version not specified, gets active config.
        """
        import json
        async with self.tenant_context(tenant_id) as conn:
            if version:
                row = await conn.fetchrow(
                    """
                    SELECT id, tenant_id, version, config_json, is_active
                    FROM tenant_configs
                    WHERE tenant_id = $1 AND version = $2
                    """,
                    tenant_id, version
                )
            else:
                row = await conn.fetchrow(
                    """
                    SELECT id, tenant_id, version, config_json, is_active
                    FROM tenant_configs
                    WHERE tenant_id = $1 AND is_active = true
                    """,
                    tenant_id
                )
            if row:
                res = dict(row)
                cjson = res.get("config_json")
                if isinstance(cjson, str):
                    try:
                        cjson = json.loads(cjson)
                    except Exception:
                        cjson = {}
                elif not isinstance(cjson, dict):
                    cjson = {}
                res["config_json"] = cjson
                return res
            return None
    # ==================== AWA CHAT LINKS & SESSIONS ====================
    async def list_public_chat_links(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        """List first-party AWA Chat links for a tenant."""
        async with self.tenant_context(tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT id, tenant_id, slug, display_name, status, scan_count,
                       token_hash, metadata, created_at, updated_at, disabled_at, last_opened_at
                FROM public_chat_links
                WHERE tenant_id = $1
                ORDER BY created_at DESC
                """,
                tenant_id,
            )
            return [dict(r) for r in rows]
    async def create_public_chat_link(
        self,
        tenant_id: UUID,
        slug: str,
        display_name: str,
        token_hash: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a tenant-scoped public AWA Chat link."""
        import json
        from uuid import uuid4
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO public_chat_links
                    (id, tenant_id, slug, display_name, token_hash, metadata, status, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'active', NOW(), NOW())
                RETURNING id, tenant_id, slug, display_name, status, scan_count,
                          token_hash, metadata, created_at, updated_at, disabled_at, last_opened_at
                """,
                uuid4(), tenant_id, slug, display_name, token_hash,
                json.dumps(metadata or {}),
            )
            return dict(row)
    async def update_public_chat_link(
        self,
        tenant_id: UUID,
        link_id: UUID,
        display_name: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update mutable public chat link fields."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                UPDATE public_chat_links
                SET display_name = COALESCE($3, display_name),
                    status = COALESCE($4, status),
                    disabled_at = CASE
                        WHEN $4 IN ('paused', 'disabled') THEN NOW()
                        WHEN $4 = 'active' THEN NULL
                        ELSE disabled_at
                    END,
                    updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2
                RETURNING id, tenant_id, slug, display_name, status, scan_count,
                          token_hash, metadata, created_at, updated_at, disabled_at, last_opened_at
                """,
                tenant_id, link_id, display_name, status,
            )
            return dict(row) if row else None
    async def rotate_public_chat_link(
        self,
        tenant_id: UUID,
        link_id: UUID,
        new_slug: str,
        new_token_hash: str,
    ) -> Optional[Dict[str, Any]]:
        """Invalidate the old public link and generate/save a new one."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                UPDATE public_chat_links
                SET slug = $3,
                    token_hash = $4,
                    updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2
                RETURNING id, tenant_id, slug, display_name, status, scan_count,
                          token_hash, metadata, created_at, updated_at, disabled_at, last_opened_at
                """,
                tenant_id, link_id, new_slug, new_token_hash,
            )
            return dict(row) if row else None
    async def get_public_chat_link_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Resolve a public chat slug to tenant/link metadata."""
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM get_public_chat_link_by_slug_v1($1)",
                slug,
            )
            return dict(row) if row else None
    async def record_public_chat_link_open(self, link_id: UUID) -> None:
        """Increment link scans/opens without exposing tenant internals."""
        async with self.acquire() as conn:
            await conn.execute(
                """
                UPDATE public_chat_links
                SET scan_count = scan_count + 1,
                    last_opened_at = NOW(),
                    updated_at = NOW()
                WHERE id = $1
                """,
                link_id,
            )
    async def create_public_chat_session(
        self,
        session_id: UUID,
        tenant_id: UUID,
        link_id: UUID,
        customer_id: UUID,
        conversation_id: UUID,
        session_token_hash: str,
        visitor_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a public browser chat session."""
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO public_chat_sessions
                    (id, tenant_id, link_id, customer_id, conversation_id, session_token_hash,
                     visitor_name, status, created_at, updated_at, last_seen_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, 'active', NOW(), NOW(), NOW())
                RETURNING id, tenant_id, link_id, customer_id, conversation_id,
                          visitor_name, status, created_at, updated_at, last_seen_at
                """,
                session_id, tenant_id, link_id, customer_id, conversation_id,
                session_token_hash, visitor_name,
            )
            return dict(row)
    async def get_public_chat_session(
        self,
        session_id: UUID,
        session_token_hash: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch an active public chat session by opaque token hash."""
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT pcs.id, pcs.tenant_id, pcs.link_id, pcs.customer_id,
                       pcs.conversation_id, pcs.visitor_name, pcs.status,
                       pcl.slug, pcl.display_name, pcl.status AS link_status,
                       t.name AS tenant_name, t.status AS tenant_status
                FROM public_chat_sessions pcs
                JOIN public_chat_links pcl ON pcl.id = pcs.link_id
                JOIN tenants t ON t.id = pcs.tenant_id
                WHERE pcs.id = $1
                  AND pcs.session_token_hash = $2
                  AND pcs.status = 'active'
                  AND pcl.status = 'active'
                  AND t.deleted_at IS NULL
                """,
                session_id, session_token_hash,
            )
            if not row:
                return None
            await conn.execute(
                "UPDATE public_chat_sessions SET last_seen_at = NOW(), updated_at = NOW() WHERE id = $1",
                session_id,
            )
            return dict(row)
    async def get_public_chat_session_for_conversation(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
    ) -> Optional[Dict[str, Any]]:
        """Fetch the public chat session attached to a conversation for tenant-side delivery."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, link_id, customer_id, conversation_id,
                       visitor_name, status, last_seen_at, created_at, updated_at
                FROM public_chat_sessions
                WHERE tenant_id = $1 AND conversation_id = $2
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                tenant_id, conversation_id,
            )
            return dict(row) if row else None
    async def update_public_chat_session_status(
        self,
        tenant_id: UUID,
        session_id: UUID,
        status: str,
    ) -> Optional[Dict[str, Any]]:
        """Update a public browser chat session status."""
        async with self.tenant_context(tenant_id) as conn:
            try:
                row = await conn.fetchrow(
                    """
                    UPDATE public_chat_sessions
                    SET status = $3,
                        ended_at = CASE WHEN $3 <> 'active' THEN NOW() ELSE ended_at END,
                        updated_at = NOW()
                    WHERE tenant_id = $1 AND id = $2
                    RETURNING id, tenant_id, link_id, customer_id, conversation_id,
                              visitor_name, status, last_seen_at, created_at, updated_at, ended_at
                    """,
                    tenant_id, session_id, status,
                )
            except asyncpg.UndefinedColumnError:
                row = await conn.fetchrow(
                    """
                    UPDATE public_chat_sessions
                    SET status = $3, updated_at = NOW()
                    WHERE tenant_id = $1 AND id = $2
                    RETURNING id, tenant_id, link_id, customer_id, conversation_id,
                              visitor_name, status, last_seen_at, created_at, updated_at
                    """,
                    tenant_id, session_id, status,
                )
            return dict(row) if row else None
    # ==================== CONVERSATIONS ====================
    async def get_or_create_conversation(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        conversation_id: Optional[UUID] = None,
        channel_type: str = "whatsapp",
        channel_account_id: Optional[UUID] = None,
        channel_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get existing conversation or create new one.
        """
        from uuid import uuid4
        async with self.tenant_context(tenant_id) as conn:
            async with conn.transaction():
                lock_key = f"conversation:{tenant_id}:{customer_id}:{channel_type}:{channel_session_id or 'default'}"
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1)::bigint)", lock_key)
                if (
                    channel_type == "whatsapp"
                    and channel_account_id is None
                    and channel_session_id is None
                ):
                    row = await conn.fetchrow(
                        """
                        SELECT id, tenant_id, customer_id, status, last_message_at
                        FROM conversations
                        WHERE tenant_id = $1 AND customer_id = $2 AND status = 'active'
                          AND COALESCE(channel_type, 'whatsapp') = 'whatsapp'
                          AND channel_session_id IS NULL
                        ORDER BY last_message_at DESC
                        LIMIT 1
                        """,
                        tenant_id, customer_id
                    )
                    if row:
                        return dict(row)
                    new_id = conversation_id or uuid4()
                    row = await conn.fetchrow(
                        """
                        INSERT INTO conversations (id, tenant_id, customer_id, status, last_message_at, created_at)
                        VALUES ($1, $2, $3, 'active', NOW(), NOW())
                        RETURNING id, tenant_id, customer_id, status, last_message_at
                        """,
                        new_id, tenant_id, customer_id
                    )
                    return dict(row)
                # Check if active conversation exists
                row = await conn.fetchrow(
                    """
                    SELECT id, tenant_id, customer_id, status, last_message_at
                    FROM conversations
                    WHERE tenant_id = $1 AND customer_id = $2 AND status = 'active'
                      AND COALESCE(channel_type, 'whatsapp') = $3
                      AND ($4::text IS NULL OR channel_session_id = $4)
                    ORDER BY last_message_at DESC
                    LIMIT 1
                    """,
                    tenant_id, customer_id, channel_type, channel_session_id
                )
                if row:
                    return dict(row)
                # Create new conversation
                new_id = conversation_id or uuid4()
                row = await conn.fetchrow(
                    """
                    INSERT INTO conversations
                        (id, tenant_id, customer_id, status, last_message_at, created_at,
                         channel_type, channel_account_id, channel_session_id)
                    VALUES ($1, $2, $3, 'active', NOW(), NOW(), $4, $5, $6)
                    RETURNING id, tenant_id, customer_id, status, last_message_at
                    """,
                    new_id, tenant_id, customer_id, channel_type, channel_account_id,
                    channel_session_id
                )
                return dict(row)
    async def update_conversation_status(
        self, tenant_id: UUID, conversation_id: UUID, status: str
    ) -> bool:
        """Update the status of a conversation (e.g. active, closed, escalated)."""
        async with self.tenant_context(tenant_id) as conn:
            try:
                result = await conn.execute(
                    """
                    UPDATE conversations
                    SET status = $1,
                        escalated_at = CASE
                            WHEN $1 = 'escalated' THEN COALESCE(escalated_at, NOW())
                            ELSE NULL
                        END,
                        escalation_reason = CASE
                            WHEN $1 = 'escalated' THEN escalation_reason
                            ELSE NULL
                        END,
                        resolved_at = CASE
                            WHEN $1 <> 'escalated' AND status = 'escalated' THEN NOW()
                            ELSE resolved_at
                        END,
                        assigned_to = CASE
                            WHEN $1 = 'escalated' THEN assigned_to
                            ELSE NULL
                        END,
                        updated_at = NOW()
                    WHERE tenant_id = $2 AND id = $3
                    """,
                    status, tenant_id, conversation_id
                )
            except asyncpg.UndefinedColumnError:
                result = await conn.execute(
                    "UPDATE conversations SET status = $1, updated_at = NOW() WHERE tenant_id = $2 AND id = $3",
                    status, tenant_id, conversation_id
                )
            return "UPDATE 1" in result
    async def set_conversation_escalation(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        escalation_reason: Optional[str] = None,
        assigned_to: Optional[UUID] = None,
        resume_mode: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Mark a conversation escalated and persist escalation metadata."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                UPDATE conversations
                SET status = 'escalated',
                    escalation_reason = COALESCE($3, escalation_reason),
                    escalated_at = COALESCE(escalated_at, NOW()),
                    resolved_at = NULL,
                    assigned_to = COALESCE($4, assigned_to),
                    resume_mode = COALESCE($5, resume_mode),
                    updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                RETURNING id, tenant_id, customer_id, status, last_message_at,
                          escalation_reason, escalated_at, resolved_at,
                          assigned_to, resume_mode, created_at, updated_at
                """,
                tenant_id, conversation_id, escalation_reason, assigned_to, resume_mode
            )
            return dict(row) if row else None
    async def clear_conversation_escalation(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        status: str = "active",
        resume_mode: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Clear active escalation assignment and mark when the escalation was resolved."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                UPDATE conversations
                SET status = $3,
                    resolved_at = NOW(),
                    assigned_to = NULL,
                    escalated_at = NULL,
                    escalation_reason = NULL,
                    resume_mode = COALESCE($4, resume_mode),
                    updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                RETURNING id, tenant_id, customer_id, status, last_message_at,
                          escalation_reason, escalated_at, resolved_at,
                          assigned_to, resume_mode, created_at, updated_at
                """,
                tenant_id, conversation_id, status, resume_mode
            )
            return dict(row) if row else None
    async def get_conversation(self, tenant_id: UUID, conversation_id: UUID) -> Optional[Dict[str, Any]]:
        """Fetch one conversation with customer and tenant display fields."""
        async with self.tenant_context(tenant_id) as conn:
            try:
                row = await conn.fetchrow(
                    """
                    SELECT c.id, c.customer_id, c.status, c.last_message_at, c.created_at,
                           c.escalation_reason, c.escalated_at, c.resolved_at,
                           c.assigned_to, c.resume_mode, c.channel_account_id,
                           COALESCE(cu.phone_number, 'Browser chat') AS customer_phone,
                           cu.name AS customer_name, cu.email AS customer_email,
                           COALESCE(c.channel_type, cu.source_channel, 'whatsapp') AS channel_type,
                           c.channel_session_id,
                           COALESCE(cs.current_module, 'idle') AS workflow_state,
                           t.ai_mode
                    FROM conversations c
                    JOIN customers cu ON cu.id = c.customer_id AND cu.tenant_id = c.tenant_id
                    JOIN tenants t ON t.id = c.tenant_id
                    LEFT JOIN LATERAL (
                        SELECT current_module
                        FROM conversation_sessions
                        WHERE tenant_id = c.tenant_id AND conversation_id = c.id
                        ORDER BY updated_at DESC
                        LIMIT 1
                    ) cs ON TRUE
                    WHERE c.tenant_id = $1 AND c.id = $2 AND c.deleted_at IS NULL
                    """,
                    tenant_id, conversation_id
                )
            except asyncpg.UndefinedColumnError:
                row = await conn.fetchrow(
                    """
                    SELECT c.id, c.customer_id, c.status, c.last_message_at, c.created_at,
                           NULL::text AS escalation_reason,
                           NULL::timestamptz AS escalated_at,
                           NULL::timestamptz AS resolved_at,
                           NULL::uuid AS assigned_to,
                            NULL::text AS resume_mode,
                            NULL::uuid AS channel_account_id,
                            cu.phone_number AS customer_phone, cu.name AS customer_name,
                            cu.email AS customer_email,
                           'whatsapp' AS channel_type,
                           NULL AS channel_session_id,
                           COALESCE(cs.current_module, 'idle') AS workflow_state,
                           t.ai_mode
                    FROM conversations c
                    JOIN customers cu ON cu.id = c.customer_id AND cu.tenant_id = c.tenant_id
                    JOIN tenants t ON t.id = c.tenant_id
                    LEFT JOIN LATERAL (
                        SELECT current_module
                        FROM conversation_sessions
                        WHERE tenant_id = c.tenant_id AND conversation_id = c.id
                        ORDER BY updated_at DESC
                        LIMIT 1
                    ) cs ON TRUE
                    WHERE c.tenant_id = $1 AND c.id = $2 AND c.deleted_at IS NULL
                    """,
                    tenant_id, conversation_id
                )
            return dict(row) if row else None
    # ==================== CUSTOMERS ====================
    # ==================== CONSENT (DPDP §6 / GDPR Art. 7) ====================
    # ==================== PRIVACY / ERASURE (DPDP §12 / GDPR Art. 17) ====================
    async def erase_customer_data(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        conn = None
    ) -> Dict[str, int]:
        """
        Hard-delete all customer data across all tables.
        CASCADE handles messages, sessions, conversations.
        Returns count of deleted records per table.
        """
        counts = {}
        if conn is not None:
            return await self._erase_customer_data_impl(conn, tenant_id, customer_id, counts)
        async with self.tenant_context(tenant_id) as conn:
            async with conn.transaction():
                return await self._erase_customer_data_impl(conn, tenant_id, customer_id, counts)

    async def _erase_customer_data_impl(self, conn, tenant_id: UUID, customer_id: UUID, counts: dict) -> Dict[str, int]:
        order_rows = await conn.fetch(
            """
            SELECT DISTINCT o.id
            FROM orders o
            LEFT JOIN conversations c
              ON c.tenant_id = o.tenant_id AND c.id = o.conversation_id
            WHERE o.tenant_id = $1
              AND (o.customer_id = $2 OR c.customer_id = $2)
            """,
            tenant_id, customer_id
        )
        order_ids = [row["id"] for row in order_rows]
        counts["orders_sanitized"] = len(order_ids)
        if order_ids:
            result = await conn.execute(
                """
                UPDATE order_events
                SET event_data = jsonb_build_object(
                        'privacy_erased', true,
                        'erased_at', NOW()::text
                    )
                WHERE tenant_id = $1 AND order_id = ANY($2::uuid[])
                """,
                tenant_id, order_ids
            )
            counts["order_events_sanitized"] = int(result.split()[-1]) if result else 0
            await conn.execute(
                """
                UPDATE orders
                SET customer_id = NULL,
                    conversation_id = NULL,
                    customer_snapshot = '{}'::jsonb,
                    delivery_address = NULL,
                    order_data = jsonb_build_object(
                        'privacy_erased', true,
                        'erased_at', NOW()::text
                    ),
                    updated_at = NOW()
                WHERE tenant_id = $1 AND id = ANY($2::uuid[])
                """,
                tenant_id, order_ids
            )
        # Delete bookings (no CASCADE from customer)
        result = await conn.execute(
            "DELETE FROM bookings WHERE tenant_id = $1 AND customer_id = $2",
            tenant_id, customer_id
        )
        counts["bookings"] = int(result.split()[-1]) if result else 0
        # Delete scheduled messages
        result = await conn.execute(
            "DELETE FROM scheduled_messages WHERE tenant_id = $1 AND customer_id = $2",
            tenant_id, customer_id
        )
        counts["scheduled_messages"] = int(result.split()[-1]) if result else 0
        # Delete review requests
        result = await conn.execute(
            "DELETE FROM review_requests WHERE tenant_id = $1 AND customer_id = $2",
            tenant_id, customer_id
        )
        counts["review_requests"] = int(result.split()[-1]) if result else 0
        # Delete payment requests
        result = await conn.execute(
            "DELETE FROM payment_requests WHERE tenant_id = $1 AND customer_id = $2",
            tenant_id, customer_id
        )
        counts["payment_requests"] = int(result.split()[-1]) if result else 0
        # Delete leads
        result = await conn.execute(
            "DELETE FROM leads WHERE tenant_id = $1 AND customer_id = $2",
            tenant_id, customer_id
        )
        counts["leads"] = int(result.split()[-1]) if result else 0
        # Count conversations before deleting (messages + sessions CASCADE)
        conv_count = await conn.fetchval(
            "SELECT COUNT(*) FROM conversations WHERE tenant_id = $1 AND customer_id = $2",
            tenant_id, customer_id
        )
        counts["conversations"] = conv_count or 0
        msg_count = await conn.fetchval(
            """SELECT COUNT(*) FROM messages m
               JOIN conversations c ON m.conversation_id = c.id
               WHERE c.tenant_id = $1 AND c.customer_id = $2""",
            tenant_id, customer_id
        )
        counts["messages"] = msg_count or 0
        note_count = await conn.fetchval(
            "SELECT COUNT(*) FROM customer_notes WHERE tenant_id = $1 AND customer_id = $2",
            tenant_id, customer_id
        )
        counts["customer_notes"] = note_count or 0
        tag_link_count = await conn.fetchval(
            "SELECT COUNT(*) FROM customer_tag_links WHERE tenant_id = $1 AND customer_id = $2",
            tenant_id, customer_id
        )
        counts["customer_tag_links"] = tag_link_count or 0
        # Delete customer — cascades to conversations → messages, sessions
        result = await conn.execute(
            "DELETE FROM customers WHERE tenant_id = $1 AND id = $2",
            tenant_id, customer_id
        )
        counts["customers"] = int(result.split()[-1]) if result else 0
        return counts
    async def export_customer_data(
        self,
        tenant_id: UUID,
        customer_id: UUID
    ) -> Dict[str, Any]:
        """
        Export all customer data in structured format (DPDP §11 / GDPR Art. 20).
        """
        async with self.tenant_context(tenant_id) as conn:
            # Customer profile
            customer = await conn.fetchrow(
                "SELECT id, phone_number, name, email, consent_status, consent_at, created_at FROM customers WHERE tenant_id = $1 AND id = $2",
                tenant_id, customer_id
            )
            if not customer:
                return None
            # Conversations
            conversations = await conn.fetch(
                "SELECT id, status, created_at FROM conversations WHERE tenant_id = $1 AND customer_id = $2",
                tenant_id, customer_id
            )
            # Messages
            messages = await conn.fetch(
                """SELECT m.id, m.direction, m.message_type, m.message_text, m.created_at
                   FROM messages m JOIN conversations c ON m.conversation_id = c.id
                   WHERE c.tenant_id = $1 AND c.customer_id = $2
                   ORDER BY m.created_at""",
                tenant_id, customer_id
            )
            # Bookings
            bookings = await conn.fetch(
                "SELECT id, service_name, booking_date, booking_time, status, created_at FROM bookings WHERE tenant_id = $1 AND customer_id = $2",
                tenant_id, customer_id
            )
            customer_tags = await conn.fetch(
                """
                SELECT ct.id, ct.name, ct.color, ct.description, ctl.created_at AS tagged_at
                FROM customer_tag_links ctl
                JOIN customer_tags ct
                  ON ct.tenant_id = ctl.tenant_id
                 AND ct.id = ctl.tag_id
                WHERE ctl.tenant_id = $1
                  AND ctl.customer_id = $2
                  AND ct.deleted_at IS NULL
                ORDER BY ct.name
                """,
                tenant_id, customer_id
            )
            customer_notes = await conn.fetch(
                """
                SELECT id, author_user_id, note, visibility, metadata, created_at, updated_at
                FROM customer_notes
                WHERE tenant_id = $1
                  AND customer_id = $2
                  AND deleted_at IS NULL
                ORDER BY created_at
                """,
                tenant_id, customer_id
            )
            orders = await conn.fetch(
                """
                SELECT o.id, o.customer_id, o.conversation_id, o.channel_type,
                       o.status, o.currency, o.subtotal_amount, o.total_amount,
                       o.fulfillment_type, o.payment_method, o.payment_status,
                       o.customer_snapshot, o.delivery_address, o.order_data,
                       o.created_at, o.updated_at
                FROM orders o
                LEFT JOIN conversations c
                  ON c.tenant_id = o.tenant_id AND c.id = o.conversation_id
                WHERE o.tenant_id = $1
                  AND (o.customer_id = $2 OR c.customer_id = $2)
                ORDER BY o.created_at
                """,
                tenant_id, customer_id
            )
            order_ids = [row["id"] for row in orders]
            if order_ids:
                order_items = await conn.fetch(
                    """
                    SELECT id, order_id, item_id, item_name, quantity, unit_price,
                           line_total, variant_data, notes, created_at
                    FROM order_items
                    WHERE tenant_id = $1 AND order_id = ANY($2::uuid[])
                    ORDER BY created_at
                    """,
                    tenant_id, order_ids
                )
                order_events = await conn.fetch(
                    """
                    SELECT id, order_id, event_type, actor_type, event_data, created_at
                    FROM order_events
                    WHERE tenant_id = $1 AND order_id = ANY($2::uuid[])
                    ORDER BY created_at
                    """,
                    tenant_id, order_ids
                )
            else:
                order_items = []
                order_events = []
            return {
                "customer": dict(customer),
                "conversations": [dict(c) for c in conversations],
                "messages": [dict(m) for m in messages],
                "bookings": [dict(b) for b in bookings],
                "customer_tags": [dict(t) for t in customer_tags],
                "customer_notes": [dict(n) for n in customer_notes],
                "orders": [dict(o) for o in orders],
                "order_items": [dict(item) for item in order_items],
                "order_events": [dict(event) for event in order_events],
                "exported_at": str(datetime.now(timezone.utc))
            }
    # ==================== MESSAGES ====================
    async def save_message(
        self,
        message_id: UUID,
        tenant_id: UUID,
        conversation_id: UUID,
        whatsapp_message_id: str,
        direction: str,
        message_type: str,
        message_text: Optional[str] = None,
        raw_payload: Optional[Dict] = None,
        channel_type: str = "whatsapp",
        provider_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Save message to DB with idempotency check.
        """
        import json
        async with self.tenant_context(tenant_id) as conn:
            try:
                if channel_type == "whatsapp":
                    row = await conn.fetchrow(
                        """
                        INSERT INTO messages
                            (id, tenant_id, conversation_id, whatsapp_message_id, direction,
                             message_type, message_text, raw_payload, processing_status, created_at,
                             channel_type, provider_message_id)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'received', NOW(), $9, $10)
                        ON CONFLICT (tenant_id, whatsapp_message_id) DO NOTHING
                        RETURNING id, tenant_id, conversation_id, whatsapp_message_id,
                                  provider_message_id, channel_type, processing_status
                        """,
                        message_id, tenant_id, conversation_id, whatsapp_message_id,
                        direction, message_type, message_text,
                        json.dumps(raw_payload) if raw_payload else None,
                        channel_type, provider_message_id or whatsapp_message_id,
                    )
                    if row:
                        if direction == "inbound":
                            await conn.execute(
                                "UPDATE conversations SET last_message_at = NOW() WHERE tenant_id = $1 AND id = $2",
                                tenant_id, conversation_id,
                            )
                        return {"created": True, **dict(row)}
                    return {"created": False, "reason": "duplicate"}
                provider_id = provider_message_id or whatsapp_message_id
                row = await conn.fetchrow(
                    """
                    INSERT INTO messages
                        (id, tenant_id, conversation_id, whatsapp_message_id, direction,
                         message_type, message_text, raw_payload, processing_status, created_at,
                         channel_type, provider_message_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'received', NOW(), $9, $10)
                    ON CONFLICT (tenant_id, whatsapp_message_id) DO NOTHING
                    RETURNING id, tenant_id, conversation_id, whatsapp_message_id,
                              provider_message_id, channel_type, processing_status
                    """,
                    message_id, tenant_id, conversation_id, provider_id,
                    direction, message_type, message_text,
                    json.dumps(raw_payload) if raw_payload else None,
                    channel_type, provider_id
                )
                if row:
                    if direction == "inbound":
                        await conn.execute(
                            """
                            UPDATE conversations
                            SET last_message_at = NOW()
                            WHERE tenant_id = $1 AND id = $2
                            """,
                            tenant_id, conversation_id,
                        )
                    return {"created": True, **dict(row)}
                return {"created": False, "reason": "duplicate"}
            except Exception:
                logger.exception("Failed to save message")
                raise
    async def check_outbound_sent(self, tenant_id: UUID, event_id: UUID) -> bool:
        """Check if an outbound message for this event was already sent (idempotent dispatch)."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchval(
                """
                SELECT 1 FROM messages
                WHERE tenant_id = $1 AND whatsapp_message_id = $2 AND direction = 'outbound'
                LIMIT 1
                """,
                tenant_id, f"response_{event_id}"
            )
            return row is not None
    async def update_message_status(self, tenant_id: UUID, whatsapp_message_id: str, status: str) -> bool:
        """Update provider delivery status (e.g. sent, delivered, read, failed)."""
        async with self.tenant_context(tenant_id) as conn:
            result = await conn.execute(
                """
                UPDATE messages
                SET delivery_status = $1
                WHERE tenant_id = $2
                  AND (whatsapp_message_id = $3 OR provider_message_id = $3)
                """,
                status, tenant_id, whatsapp_message_id
            )
            return "UPDATE 1" in result
    # ==================== SEQUENCE NUMBERS (ATOMIC UPSERT) ====================
    async def get_next_sequence_number(self, tenant_id: UUID, conversation_id: UUID) -> int:
        """
        Atomically increment the sequence number, creating the session row if needed.
        Prevents first-message races from producing duplicate sequence numbers.
        """
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO conversation_sessions
                    (id, tenant_id, conversation_id, config_version, state_json, sequence_number, updated_at)
                SELECT gen_random_uuid(), $1, $2, active.version,
                       jsonb_build_object('config_version', active.version, 'state', '{}'::jsonb), 1, NOW()
                FROM (
                    SELECT version FROM tenant_configs
                    WHERE tenant_id = $1 AND is_active = TRUE
                    ORDER BY version DESC LIMIT 1
                ) AS active
                ON CONFLICT (conversation_id) DO UPDATE
                SET sequence_number = conversation_sessions.sequence_number + 1,
                    updated_at = NOW()
                WHERE conversation_sessions.tenant_id = EXCLUDED.tenant_id
                RETURNING sequence_number
                """,
                tenant_id, conversation_id
            )
            if not row:
                raise ValueError("Conversation session tenant mismatch")
            return row["sequence_number"]
    # ==================== BOOKINGS ====================
    async def create_booking(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        conversation_id: UUID,
        service_name: str,
        booking_date: str,
        booking_time: Optional[str] = None,
        booking_data: Optional[Dict] = None,
        idempotency_key: Optional[str] = None,
        conn=None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Insert a new booking row."""
        import json
        from uuid import uuid4

        async def _run(c):
            row = await c.fetchrow(
                """
                INSERT INTO bookings
                    (id, tenant_id, customer_id, conversation_id, service_name,
                     booking_date, booking_time, status, booking_data, idempotency_key,
                     created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6::date, $7::time, 'confirmed',
                        $8::jsonb, $9, NOW(), NOW())
                ON CONFLICT (tenant_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL
                DO NOTHING
                RETURNING id, tenant_id, customer_id, service_name, booking_date,
                          booking_time, status, created_at
                """,
                uuid4(), tenant_id, customer_id, conversation_id, service_name,
                booking_date, booking_time,
                json.dumps(booking_data) if booking_data else '{}',
                idempotency_key,
            )
            if not row and idempotency_key:
                row = await c.fetchrow(
                    """
                    SELECT id, tenant_id, customer_id, service_name, booking_date,
                           booking_time, status, created_at
                    FROM bookings
                    WHERE tenant_id = $1 AND idempotency_key = $2
                    """,
                    tenant_id, idempotency_key,
                )
            return dict(row)

        if conn is not None:
            return await _run(conn)
        async with self.acquire() as connection:
            return await _run(connection)
    async def get_booking(self, tenant_id: UUID, booking_id: UUID) -> Optional[Dict[str, Any]]:
        """Fetch a specific booking by ID."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT b.id, b.customer_id, b.conversation_id,
                       cu.name AS customer_name, cu.phone_number AS customer_phone,
                       COALESCE(ci.name, b.service_name) AS service_name,
                       COALESCE(ci.name, b.service_name) AS service_type,
                       b.booking_date, b.booking_time,
                       (b.booking_date::text || 'T' || COALESCE(b.booking_time::text, '00:00:00')) AS booking_datetime,
                       b.status, b.booking_data AS collected_fields, b.booking_data,
                       b.created_at, b.updated_at
                FROM bookings b
                LEFT JOIN customers cu ON cu.id = b.customer_id AND cu.tenant_id = b.tenant_id
                LEFT JOIN catalog_items ci ON ci.tenant_id = b.tenant_id AND (
                    ci.id = CASE
                        WHEN b.booking_data->>'service_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                        THEN (b.booking_data->>'service_id')::uuid
                        ELSE NULL
                    END OR
                    ci.id = CASE
                        WHEN b.service_name ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                        THEN b.service_name::uuid
                        ELSE NULL
                    END
                )
                WHERE b.tenant_id = $1 AND b.id = $2
                """,
                tenant_id, booking_id
            )
            return dict(row) if row else None
    async def update_booking(
        self, tenant_id: UUID, booking_id: UUID,
        status: Optional[str] = None,
        booking_date: Optional[str] = None,
        booking_time: Optional[str] = None,
        conn=None
    ) -> bool:
        """Update booking status or reschedule details."""
        async def _run(c):
            updates = []
            values = [tenant_id, booking_id]
            idx = 3
            if status:
                updates.append(f"status = ${idx}")
                values.append(status)
                idx += 1
            if booking_date:
                updates.append(f"booking_date = ${idx}::date")
                values.append(booking_date)
                idx += 1
            if booking_time:
                updates.append(f"booking_time = ${idx}::time")
                values.append(booking_time)
                idx += 1
            if not updates:
                return False
            updates.append("updated_at = NOW()")
            set_clause = ", ".join(updates)
            result = await c.execute(
                f"UPDATE bookings SET {set_clause} WHERE tenant_id = $1 AND id = $2",
                *values
            )
            if "UPDATE 1" in result and status == "cancelled":
                await c.execute(
                    "UPDATE payment_requests SET status = 'cancelled', updated_at = NOW() WHERE booking_id = $2 AND status IN ('draft', 'sent', 'viewed')",
                    tenant_id,
                    booking_id
                )
            return "UPDATE 1" in result

        if conn is not None:
            return await _run(conn)
        async with self.acquire() as connection:
            return await _run(connection)
    async def list_bookings(
        self, tenant_id: UUID, cursor: Optional[str] = None, limit: int = 20,
        status: Optional[str] = None, search: Optional[str] = None,
        date_from: Optional[str] = None, date_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """Paginated bookings for a tenant with optional filtering."""
        async with self.tenant_context(tenant_id) as conn:
            query = """
                SELECT b.id, b.customer_id, b.conversation_id,
                       cu.name AS customer_name, cu.phone_number AS customer_phone,
                       COALESCE(ci.name, b.service_name) AS service_name,
                       COALESCE(ci.name, b.service_name) AS service_type,
                       b.booking_date, b.booking_time,
                       (b.booking_date::text || 'T' || COALESCE(b.booking_time::text, '00:00:00')) AS booking_datetime,
                       b.status, b.booking_data AS collected_fields, b.booking_data,
                       b.created_at, b.updated_at
                FROM bookings b
                LEFT JOIN customers cu ON cu.id = b.customer_id AND cu.tenant_id = b.tenant_id
                LEFT JOIN catalog_items ci ON ci.tenant_id = b.tenant_id AND (
                    ci.id = CASE
                        WHEN b.booking_data->>'service_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                        THEN (b.booking_data->>'service_id')::uuid
                        ELSE NULL
                    END OR
                    ci.id = CASE
                        WHEN b.service_name ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                        THEN b.service_name::uuid
                        ELSE NULL
                    END
                )
                WHERE b.tenant_id = $1
            """
            params = [tenant_id]
            idx = 2
            if status:
                query += f" AND b.status = ${idx}"
                params.append(status)
                idx += 1
            if search:
                query += f" AND (cu.name ILIKE ${idx} OR cu.phone_number ILIKE ${idx} OR b.id::text ILIKE ${idx})"
                params.append(f"%{search}%")
                idx += 1
            if date_from:
                query += f" AND b.booking_date >= ${idx}::date"
                params.append(date_from)
                idx += 1
            if date_to:
                query += f" AND b.booking_date <= ${idx}::date"
                params.append(date_to)
                idx += 1
            if cursor:
                ts, r_id = decode_cursor(cursor)
                if ts:
                    if r_id:
                        query += f" AND (b.created_at, b.id) < (${idx}::timestamptz, ${idx+1}::uuid)"
                        params.extend([ts, r_id])
                        idx += 2
                    else:
                        query += f" AND b.created_at < ${idx}::timestamptz"
                        params.append(ts)
                        idx += 1
            query += f" ORDER BY b.created_at DESC, b.id DESC LIMIT ${idx}"
            params.append(limit + 1)
            rows = await conn.fetch(query, *params)
            items = [dict(r) for r in rows[:limit]]
            next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["id"]) if len(rows) > limit else None
            return {"items": items, "next_cursor": next_cursor}
    # ==================== ORDERS ====================
    async def create_order(
        self,
        tenant_id: UUID,
        customer_id: Optional[UUID],
        conversation_id: Optional[UUID],
        channel_type: str,
        status: str,
        currency: str,
        subtotal_amount: float,
        total_amount: float,
        fulfillment_type: str,
        payment_method: str,
        payment_status: str,
        customer_snapshot: Optional[Dict[str, Any]] = None,
        delivery_address: Optional[str] = None,
        order_data: Optional[Dict[str, Any]] = None,
        items: Optional[List[Dict[str, Any]]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create an order and item snapshots idempotently."""
        import json
        from uuid import uuid4
        order_items = items or []
        async with self.tenant_context(tenant_id) as conn:
            async with conn.transaction():
                order_id = uuid4()
                row = await conn.fetchrow(
                    """
                    INSERT INTO orders (
                        id, tenant_id, customer_id, conversation_id, channel_type,
                        status, currency, subtotal_amount, total_amount,
                        fulfillment_type, payment_method, payment_status,
                        customer_snapshot, delivery_address, order_data, idempotency_key,
                        created_at, updated_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8, $9,
                        $10, $11, $12,
                        $13::jsonb, $14, $15::jsonb, $16,
                        NOW(), NOW()
                    )
                    ON CONFLICT (tenant_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                    DO NOTHING
                    RETURNING id, tenant_id, customer_id, conversation_id, channel_type,
                              status, currency, subtotal_amount, total_amount,
                              fulfillment_type, payment_method, payment_status,
                              customer_snapshot, delivery_address, order_data,
                              created_at, updated_at
                    """,
                    order_id, tenant_id, customer_id, conversation_id, channel_type,
                    status, currency, subtotal_amount, total_amount,
                    fulfillment_type, payment_method, payment_status,
                    json.dumps(customer_snapshot or {}, default=str),
                    delivery_address,
                    json.dumps(order_data or {}, default=str),
                    idempotency_key,
                )
                if not row and idempotency_key:
                    row = await conn.fetchrow(
                        """
                        SELECT id, tenant_id, customer_id, conversation_id, channel_type,
                               status, currency, subtotal_amount, total_amount,
                               fulfillment_type, payment_method, payment_status,
                               customer_snapshot, delivery_address, order_data,
                               created_at, updated_at
                        FROM orders
                        WHERE tenant_id = $1 AND idempotency_key = $2
                        """,
                        tenant_id, idempotency_key,
                    )
                    if row:
                        return dict(row)
                if not row:
                    raise ValueError("Order insert failed")
                for item in order_items:
                    item_id = item.get("item_id")
                    if item_id:
                        from uuid import UUID
                        try:
                            item_uuid = UUID(str(item_id))
                        except ValueError:
                            item_uuid = None
                        if item_uuid:
                            catalog_item = await conn.fetchrow(
                                "SELECT stock_tracking_enabled FROM catalog_items WHERE id = $1 AND tenant_id = $2",
                                item_uuid, tenant_id
                            )
                            if catalog_item and catalog_item["stock_tracking_enabled"]:
                                quantity = int(item.get("quantity") or 1)
                                result = await conn.execute(
                                    """
                                    UPDATE catalog_items
                                    SET stock_quantity = stock_quantity - $1
                                    WHERE id = $2 AND tenant_id = $3 AND stock_quantity >= $1
                                    """,
                                    quantity, item_uuid, tenant_id
                                )
                                if result == "UPDATE 0":
                                    raise ValueError("Out of stock")

                    await conn.execute(
                        """
                        INSERT INTO order_items (
                            id, tenant_id, order_id, item_id, item_name,
                            quantity, unit_price, line_total, variant_data, notes,
                            created_at
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, NOW())
                        """,
                        uuid4(), tenant_id, order_id,
                        str(item.get("item_id") or ""),
                        str(item.get("item_name") or "Item"),
                        int(item.get("quantity") or 1),
                        float(item.get("unit_price") or 0),
                        float(item.get("line_total") or 0),
                        json.dumps(item.get("variant_data") or {}, default=str),
                        item.get("notes"),
                    )
                await conn.execute(
                    """
                    INSERT INTO order_events (id, tenant_id, order_id, event_type, actor_type, event_data, created_at)
                    VALUES ($1, $2, $3, 'created', 'system', $4::jsonb, NOW())
                    """,
                    uuid4(), tenant_id, order_id,
                    json.dumps({"source": channel_type, "item_count": len(order_items)}, default=str),
                )
                return dict(row)
    async def list_orders(
        self,
        tenant_id: UUID,
        cursor: Optional[str] = None,
        limit: int = 20,
        status: Optional[str] = None,
        fulfillment_type: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Paginated orders for a tenant with optional filtering."""
        async with self.tenant_context(tenant_id) as conn:
            query = """
                SELECT o.id, o.customer_id, o.conversation_id, o.channel_type,
                       COALESCE(NULLIF(o.customer_snapshot->>'name', ''), NULLIF(cu.name, '')) AS customer_name,
                       COALESCE(NULLIF(o.customer_snapshot->>'phone', ''), NULLIF(cu.phone_number, '')) AS customer_phone,
                       o.status, o.currency, o.subtotal_amount, o.total_amount,
                       o.fulfillment_type, o.payment_method, o.payment_status,
                       o.customer_snapshot, o.delivery_address, o.order_data,
                       o.created_at, o.updated_at,
                       COALESCE(COUNT(oi.id), 0)::int AS item_count
                FROM orders o
                LEFT JOIN customers cu ON cu.id = o.customer_id AND cu.tenant_id = o.tenant_id
                LEFT JOIN order_items oi ON oi.order_id = o.id AND oi.tenant_id = o.tenant_id
                WHERE o.tenant_id = $1
            """
            params: List[Any] = [tenant_id]
            idx = 2
            if status:
                query += f" AND o.status = ${idx}"
                params.append(status)
                idx += 1
            if fulfillment_type:
                query += f" AND o.fulfillment_type = ${idx}"
                params.append(fulfillment_type)
                idx += 1
            if search:
                query += f" AND (cu.name ILIKE ${idx} OR cu.phone_number ILIKE ${idx} OR o.id::text ILIKE ${idx})"
                params.append(f"%{search}%")
                idx += 1
            if date_from:
                query += f" AND o.created_at >= ${idx}::timestamptz"
                params.append(date_from)
                idx += 1
            if date_to:
                query += f" AND o.created_at < (${idx}::date + interval '1 day')"
                params.append(date_to)
                idx += 1
            if cursor:
                ts, r_id = decode_cursor(cursor)
                if ts:
                    if r_id:
                        query += f" AND (o.created_at, o.id) < (${idx}::timestamptz, ${idx+1}::uuid)"
                        params.extend([ts, r_id])
                        idx += 2
                    else:
                        query += f" AND o.created_at < ${idx}::timestamptz"
                        params.append(ts)
                        idx += 1
            query += f"""
                GROUP BY o.id, cu.name, cu.phone_number
                ORDER BY o.created_at DESC, o.id DESC
                LIMIT ${idx}
            """
            params.append(limit + 1)
            rows = await conn.fetch(query, *params)
            items = [dict(r) for r in rows[:limit]]
            next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["id"]) if len(rows) > limit else None
            return {"items": items, "next_cursor": next_cursor}
    async def get_order(self, tenant_id: UUID, order_id: UUID) -> Optional[Dict[str, Any]]:
        """Fetch an order with items and timeline."""
        async with self.tenant_context(tenant_id) as conn:
            order = await conn.fetchrow(
                """
                SELECT o.id, o.customer_id, o.conversation_id, o.channel_type,
                       COALESCE(NULLIF(o.customer_snapshot->>'name', ''), NULLIF(cu.name, '')) AS customer_name,
                       COALESCE(NULLIF(o.customer_snapshot->>'phone', ''), NULLIF(cu.phone_number, '')) AS customer_phone,
                       o.status, o.currency, o.subtotal_amount, o.total_amount,
                       o.fulfillment_type, o.payment_method, o.payment_status,
                       o.customer_snapshot, o.delivery_address, o.order_data,
                       o.created_at, o.updated_at
                FROM orders o
                LEFT JOIN customers cu ON cu.id = o.customer_id AND cu.tenant_id = o.tenant_id
                WHERE o.tenant_id = $1 AND o.id = $2
                """,
                tenant_id, order_id,
            )
            if not order:
                return None
            items = await conn.fetch(
                """
                SELECT id, item_id, item_name, quantity, unit_price, line_total,
                       variant_data, notes, created_at
                FROM order_items
                WHERE tenant_id = $1 AND order_id = $2
                ORDER BY created_at ASC
                """,
                tenant_id, order_id,
            )
            events = await conn.fetch(
                """
                SELECT id, event_type, actor_type, event_data, created_at
                FROM order_events
                WHERE tenant_id = $1 AND order_id = $2
                ORDER BY created_at ASC
                """,
                tenant_id, order_id,
            )
            data = dict(order)
            data["items"] = [dict(item) for item in items]
            data["events"] = [dict(event) for event in events]
            return data
    async def update_order(
        self,
        tenant_id: UUID,
        order_id: UUID,
        status: Optional[str] = None,
        payment_method: Optional[str] = None,
        payment_status: Optional[str] = None,
        delivery_address: Optional[str] = None,
        internal_note: Optional[str] = None,
        actor_id: Optional[UUID] = None,
    ) -> bool:
        """Update mutable order fields and write an order event."""
        import json
        from uuid import uuid4
        async with self.tenant_context(tenant_id) as conn:
            async with conn.transaction():
                updates = []
                values: List[Any] = [tenant_id, order_id]
                idx = 3
                if status:
                    updates.append(f"status = ${idx}")
                    values.append(status)
                    idx += 1
                if payment_method:
                    updates.append(f"payment_method = ${idx}")
                    values.append(payment_method)
                    idx += 1
                if payment_status:
                    updates.append(f"payment_status = ${idx}")
                    values.append(payment_status)
                    idx += 1
                if delivery_address is not None:
                    updates.append(f"delivery_address = ${idx}")
                    values.append(delivery_address)
                    idx += 1
                if internal_note:
                    updates.append(f"order_data = jsonb_set(COALESCE(order_data, '{{}}'::jsonb), '{{internal_note}}', ${idx}::jsonb, true)")
                    values.append(json.dumps(internal_note))
                    idx += 1
                if not updates:
                    return False
                updates.append("updated_at = NOW()")
                result = await conn.execute(
                    f"UPDATE orders SET {', '.join(updates)} WHERE tenant_id = $1 AND id = $2",
                    *values,
                )
                if "UPDATE 1" not in result:
                    return False
                if status == "cancelled":
                    await conn.execute(
                        "UPDATE payment_requests SET status = 'cancelled', updated_at = NOW() WHERE order_id = $2 AND status IN ('draft', 'sent', 'viewed')",
                        tenant_id,
                        order_id
                    )
                    # Restock inventory
                    await conn.execute(
                        """
                        UPDATE catalog_items ci
                        SET stock_quantity = ci.stock_quantity + oi.quantity
                        FROM order_items oi
                        WHERE oi.order_id = $2
                          AND ci.id = oi.item_id
                          AND ci.tenant_id = $1
                          AND ci.stock_tracking_enabled = true
                        """,
                        tenant_id,
                        order_id
                    )
                event_payload = {
                    "status": status,
                    "payment_method": payment_method,
                    "payment_status": payment_status,
                    "delivery_address_changed": delivery_address is not None,
                    "internal_note": internal_note,
                }
                await conn.execute(
                    """
                    INSERT INTO order_events (id, tenant_id, order_id, event_type, actor_type, event_data, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, NOW())
                    """,
                    uuid4(), tenant_id, order_id,
                    "status_changed" if status else "edited",
                    "tenant_user" if actor_id else "system",
                    json.dumps(event_payload, default=str),
                )
                return True
    # ==================== PAYMENTS / DEPOSITS ====================
    async def list_payment_requests(
        self,
        tenant_id: UUID,
        cursor: Optional[str] = None,
        limit: int = 20,
        status: Optional[str] = None,
        payment_type: Optional[str] = None,
        customer_id: Optional[UUID] = None,
        booking_id: Optional[UUID] = None,
        order_id: Optional[UUID] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Paginated payment requests for a tenant."""
        async with self.tenant_context(tenant_id) as conn:
            query = """
                SELECT p.id, p.tenant_id, p.customer_id, p.conversation_id,
                       p.booking_id, p.order_id, p.amount, p.currency, p.type,
                       p.status, p.provider, p.provider_payment_link_id,
                       p.provider_order_id, p.provider_payment_id, p.payment_url,
                       p.expires_at, p.sent_at, p.paid_at, p.cancelled_at,
                       p.created_by, p.metadata, p.created_at, p.updated_at,
                       cu.name AS customer_name, cu.phone_number AS customer_phone,
                       b.service_name AS booking_service_name,
                       b.booking_date, b.booking_time,
                       o.status AS order_status,
                       o.payment_status AS order_payment_status
                FROM payment_requests p
                LEFT JOIN customers cu ON cu.id = p.customer_id AND cu.tenant_id = p.tenant_id
                LEFT JOIN bookings b ON b.id = p.booking_id AND b.tenant_id = p.tenant_id
                LEFT JOIN orders o ON o.id = p.order_id AND o.tenant_id = p.tenant_id
                WHERE p.tenant_id = $1 AND p.deleted_at IS NULL
            """
            params: List[Any] = [tenant_id]
            idx = 2
            if status:
                query += f" AND p.status = ${idx}"
                params.append(status)
                idx += 1
            if payment_type:
                query += f" AND p.type = ${idx}"
                params.append(payment_type)
                idx += 1
            if customer_id:
                query += f" AND p.customer_id = ${idx}"
                params.append(customer_id)
                idx += 1
            if booking_id:
                query += f" AND p.booking_id = ${idx}"
                params.append(booking_id)
                idx += 1
            if order_id:
                query += f" AND p.order_id = ${idx}"
                params.append(order_id)
                idx += 1
            if search:
                query += f"""
                    AND (
                        cu.name ILIKE ${idx}
                        OR cu.phone_number ILIKE ${idx}
                        OR p.id::text ILIKE ${idx}
                        OR COALESCE(p.provider_payment_id, '') ILIKE ${idx}
                    )
                """
                params.append(f"%{search}%")
                idx += 1
            if cursor:
                ts, r_id = decode_cursor(cursor)
                if ts:
                    if r_id:
                        query += f" AND (p.created_at, p.id) < (${idx}::timestamptz, ${idx+1}::uuid)"
                        params.extend([ts, r_id])
                        idx += 2
                    else:
                        query += f" AND p.created_at < ${idx}::timestamptz"
                        params.append(ts)
                        idx += 1
            query += f" ORDER BY p.created_at DESC, p.id DESC LIMIT ${idx}"
            params.append(limit + 1)
            rows = await conn.fetch(query, *params)
            items = [dict(row) for row in rows[:limit]]
            next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["id"]) if len(rows) > limit else None
            return {"items": items, "next_cursor": next_cursor}
    async def get_payment_metrics(self, tenant_id: UUID) -> Dict[str, Any]:
        """Get aggregate metrics for payment requests of a tenant."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status IN ('draft', 'sent', 'viewed')) as pending,
                    COUNT(*) FILTER (WHERE status IN ('failed', 'expired')) as needs_attention,
                    COALESCE(SUM(amount) FILTER (WHERE status = 'paid'), 0) as paid_amount
                FROM payment_requests
                WHERE tenant_id = $1 AND deleted_at IS NULL
                """,
                tenant_id,
            )
            return {
                "pending": row["pending"] if row else 0,
                "needs_attention": row["needs_attention"] if row else 0,
                "paid_amount": float(row["paid_amount"]) if row and row["paid_amount"] is not None else 0.0,
            }
    async def get_payment_request(self, tenant_id: UUID, payment_id: UUID) -> Optional[Dict[str, Any]]:
        """Fetch a payment request with linked customer/order/booking context."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT p.id, p.tenant_id, p.customer_id, p.conversation_id,
                       p.booking_id, p.order_id, p.amount, p.currency, p.type,
                       p.status, p.provider, p.provider_payment_link_id,
                       p.provider_order_id, p.provider_payment_id, p.payment_url,
                       p.expires_at, p.sent_at, p.paid_at, p.cancelled_at,
                       p.created_by, p.metadata, p.created_at, p.updated_at,
                       cu.name AS customer_name, cu.phone_number AS customer_phone,
                       b.service_name AS booking_service_name,
                       b.booking_date, b.booking_time,
                       o.status AS order_status,
                       o.payment_status AS order_payment_status
                FROM payment_requests p
                LEFT JOIN customers cu ON cu.id = p.customer_id AND cu.tenant_id = p.tenant_id
                LEFT JOIN bookings b ON b.id = p.booking_id AND b.tenant_id = p.tenant_id
                LEFT JOIN orders o ON o.id = p.order_id AND o.tenant_id = p.tenant_id
                WHERE p.tenant_id = $1 AND p.id = $2 AND p.deleted_at IS NULL
                """,
                tenant_id,
                payment_id,
            )
            return dict(row) if row else None
    async def create_payment_request(
        self,
        tenant_id: UUID,
        amount: Optional[float] = None,
        currency: str = "INR",
        payment_type: str = "custom_payment",
        provider: str = "manual",
        status: str = "draft",
        customer_id: Optional[UUID] = None,
        conversation_id: Optional[UUID] = None,
        booking_id: Optional[UUID] = None,
        order_id: Optional[UUID] = None,
        payment_url: Optional[str] = None,
        provider_payment_link_id: Optional[str] = None,
        provider_order_id: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        created_by: Optional[UUID] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a tenant payment request and infer linked context where possible."""
        import json
        from uuid import uuid4
        async with self.tenant_context(tenant_id) as conn:
            resolved_customer_id = customer_id
            resolved_conversation_id = conversation_id
            resolved_amount = amount
            resolved_currency = currency or "INR"
            if order_id:
                order = await conn.fetchrow(
                    """
                    SELECT id, customer_id, conversation_id, total_amount, currency
                    FROM orders
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id,
                    order_id,
                )
                if not order:
                    return None
                resolved_customer_id = resolved_customer_id or order["customer_id"]
                resolved_conversation_id = resolved_conversation_id or order["conversation_id"]
                resolved_amount = resolved_amount if resolved_amount is not None else float(order["total_amount"] or 0)
                resolved_currency = resolved_currency or order["currency"] or "INR"
            if booking_id:
                booking = await conn.fetchrow(
                    """
                    SELECT id, customer_id, conversation_id
                    FROM bookings
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id,
                    booking_id,
                )
                if not booking:
                    return None
                resolved_customer_id = resolved_customer_id or booking["customer_id"]
                resolved_conversation_id = resolved_conversation_id or booking["conversation_id"]
            if resolved_customer_id:
                customer_exists = await conn.fetchval(
                    """
                    SELECT TRUE
                    FROM customers
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id,
                    resolved_customer_id,
                )
                if not customer_exists:
                    return None
            if not resolved_amount or resolved_amount <= 0:
                return None
            async with conn.transaction():
                payment_id = uuid4()
                row = await conn.fetchrow(
                    """
                    INSERT INTO payment_requests (
                        id, tenant_id, customer_id, conversation_id, booking_id, order_id,
                        amount, currency, type, status, provider, payment_url,
                        provider_payment_link_id, provider_order_id, expires_at,
                        created_by, idempotency_key, metadata, created_at, updated_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6,
                        $7, $8, $9, $10, $11, $12,
                        $13, $14, $15,
                        $16, $17, $18::jsonb, NOW(), NOW()
                    )
                    ON CONFLICT (tenant_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                    DO NOTHING
                    RETURNING id
                    """,
                    payment_id,
                    tenant_id,
                    resolved_customer_id,
                    resolved_conversation_id,
                    booking_id,
                    order_id,
                    resolved_amount,
                    resolved_currency,
                    payment_type,
                    status,
                    provider,
                    payment_url,
                    provider_payment_link_id,
                    provider_order_id,
                    expires_at,
                    created_by,
                    idempotency_key,
                    json.dumps(metadata or {}, default=str),
                )
                if not row and idempotency_key:
                    existing_id = await conn.fetchval(
                        """
                        SELECT id
                        FROM payment_requests
                        WHERE tenant_id = $1 AND idempotency_key = $2 AND deleted_at IS NULL
                        """,
                        tenant_id,
                        idempotency_key,
                    )
                    if existing_id:
                        return await self.get_payment_request(tenant_id, existing_id)
                if not row:
                    return None
                await conn.execute(
                    """
                    INSERT INTO payment_events (
                        id, tenant_id, payment_request_id, event_type, actor_type,
                        actor_user_id, event_data, created_at
                    )
                    VALUES ($1, $2, $3, 'created', $4, $5, $6::jsonb, NOW())
                    """,
                    uuid4(),
                    tenant_id,
                    row["id"],
                    "tenant_user" if created_by else "system",
                    created_by,
                    json.dumps({"source": "dashboard"}, default=str),
                )
            return await self.get_payment_request(tenant_id, row["id"])
    async def update_payment_request(
        self,
        tenant_id: UUID,
        payment_id: UUID,
        updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Update editable payment request fields before final settlement."""
        import json
        allowed = {"amount", "currency", "type", "provider", "payment_url", "expires_at", "metadata"}
        assignments = []
        values: List[Any] = [tenant_id, payment_id]
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
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE payment_requests
                SET {', '.join(assignments)}
                WHERE tenant_id = $1
                  AND id = $2
                  AND deleted_at IS NULL
                  AND status NOT IN ('paid', 'refunded', 'cancelled')
                RETURNING id
                """,
                *values,
            )
            if not row:
                return None
            return await self.get_payment_request(tenant_id, payment_id)
    async def update_payment_request_provider_details(
        self,
        tenant_id: UUID,
        payment_id: UUID,
        status: str,
        payment_url: Optional[str] = None,
        provider_payment_link_id: Optional[str] = None,
        provider_order_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update provider linkage details and status after link creation."""
        import json
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                UPDATE payment_requests
                SET status = $3,
                    payment_url = COALESCE($4, payment_url),
                    provider_payment_link_id = COALESCE($5, provider_payment_link_id),
                    provider_order_id = COALESCE($6, provider_order_id),
                    metadata = COALESCE($7::jsonb, metadata),
                    updated_at = NOW()
                WHERE tenant_id = $1
                  AND id = $2
                  AND deleted_at IS NULL
                RETURNING id
                """,
                tenant_id,
                payment_id,
                status,
                payment_url,
                provider_payment_link_id,
                provider_order_id,
                json.dumps(metadata) if metadata is not None else None,
            )
            if not row:
                return None
            return await self.get_payment_request(tenant_id, payment_id)
    async def action_payment_request(
        self,
        tenant_id: UUID,
        payment_id: UUID,
        action: str,
        actor_id: Optional[UUID] = None,
        note: Optional[str] = None,
        provider_payment_id: Optional[str] = None,
        payment_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        provider_event_id: Optional[str] = None,
        provider_payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Apply a payment lifecycle action and sync linked order payment status."""
        import json
        from uuid import uuid4
        action_to_status = {
            "send": "sent",
            "cancel": "cancelled",
            "mark_paid": "paid",
            "mark_failed": "failed",
            "mark_refunded": "refunded",
            "expire": "expired",
            "partial_payment": None,
        }
        if action == "partial_payment":
            next_status = None
        else:
            next_status = action_to_status.get(action)
            if not next_status:
                return None
        timestamp_field = {
            "send": "sent_at",
            "cancel": "cancelled_at",
            "mark_paid": "paid_at",
        }.get(action)
        async with self.tenant_context(tenant_id) as conn:
            async with conn.transaction():
                if provider_event_id:
                    exists = await conn.fetchval(
                        "SELECT id FROM payment_events WHERE tenant_id = $1 AND provider_event_id = $2",
                        tenant_id,
                        provider_event_id,
                    )
                    if exists:
                        return await self.get_payment_request(tenant_id, payment_id)
                current = await conn.fetchrow(
                    """
                    SELECT id, order_id, status
                    FROM payment_requests
                    WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                    FOR UPDATE
                    """,
                    tenant_id,
                    payment_id,
                )
                if not current:
                    return None
                if next_status is None:
                    next_status = current["status"]
                updates = ["status = $3", "updated_at = NOW()"]
                values: List[Any] = [tenant_id, payment_id, next_status]
                idx = 4
                if timestamp_field:
                    updates.append(f"{timestamp_field} = COALESCE({timestamp_field}, NOW())")
                if provider_payment_id is not None:
                    updates.append(f"provider_payment_id = ${idx}")
                    values.append(provider_payment_id)
                    idx += 1
                if payment_url is not None:
                    updates.append(f"payment_url = ${idx}")
                    values.append(payment_url)
                    idx += 1
                await conn.execute(
                    f"""
                    UPDATE payment_requests
                    SET {', '.join(updates)}
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    *values,
                )
                event_payload = {
                    "action": action,
                    "previous_status": current["status"],
                    "status": next_status,
                    "note": note,
                    "provider_payment_id": provider_payment_id,
                    "metadata": metadata or {},
                }
                await conn.execute(
                    """
                    INSERT INTO payment_events (
                        id, tenant_id, payment_request_id, event_type, actor_type,
                        actor_user_id, provider_event_id, provider_payload, event_data, created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, NOW())
                    """,
                    uuid4(),
                    tenant_id,
                    payment_id,
                    action,
                    "provider" if provider_event_id else ("tenant_user" if actor_id else "system"),
                    actor_id,
                    provider_event_id,
                    json.dumps(provider_payload or {}, default=str),
                    json.dumps(event_payload, default=str),
                )
                order_status = {
                    "send": "pending",
                    "mark_paid": "paid",
                    "mark_failed": "failed",
                    "mark_refunded": "refunded",
                    "expire": "failed",
                    "partial_payment": "pending",
                }.get(action)
                if action == "cancel" and current["status"] != "paid":
                    # Check if there are other paid requests for this order before reverting status
                    has_paid = await conn.fetchval(
                        """
                        SELECT TRUE FROM payment_requests
                        WHERE tenant_id = $1 AND order_id = $2 AND status = 'paid' AND id != $3
                        """,
                        tenant_id,
                        current["order_id"],
                        payment_id,
                    )
                    if not has_paid:
                        order_status = "not_collected"
                    else:
                        order_status = None
                if current["order_id"] and order_status:
                    await conn.execute(
                        """
                        UPDATE orders
                        SET payment_status = $3, updated_at = NOW()
                        WHERE tenant_id = $1 AND id = $2
                        """,
                        tenant_id,
                        current["order_id"],
                        order_status,
                    )
                    await conn.execute(
                        """
                        INSERT INTO order_events (
                            id, tenant_id, order_id, event_type, actor_type,
                            event_data, created_at
                        )
                        VALUES ($1, $2, $3, 'payment_updated', $4, $5::jsonb, NOW())
                        """,
                        uuid4(),
                        tenant_id,
                        current["order_id"],
                        "tenant_user" if actor_id else "system",
                        json.dumps({
                            "payment_request_id": str(payment_id),
                            "payment_status": order_status,
                            "action": action,
                        }, default=str),
                    )
            return await self.get_payment_request(tenant_id, payment_id)
    async def get_payment_request_by_provider_link_id(
        self,
        provider_link_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch a payment request by its provider_payment_link_id."""
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, customer_id, conversation_id, booking_id, order_id,
                       amount, currency, type, status, provider, provider_payment_link_id,
                       provider_order_id, provider_payment_id, payment_url, metadata
                FROM payment_requests
                WHERE provider_payment_link_id = $1 AND deleted_at IS NULL
                """,
                provider_link_id,
            )
            return dict(row) if row else None
    async def list_payment_events(
        self,
        tenant_id: UUID,
        payment_id: UUID,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List payment lifecycle events."""
        async with self.tenant_context(tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT id, tenant_id, payment_request_id, event_type, actor_type,
                       actor_user_id, provider_event_id, provider_payload,
                       event_data, created_at
                FROM payment_events
                WHERE tenant_id = $1 AND payment_request_id = $2
                ORDER BY created_at DESC
                LIMIT $3
                """,
                tenant_id,
                payment_id,
                limit,
            )
            return [dict(row) for row in rows]
    # ==================== REMINDERS / FOLLOW-UPS ====================
    async def list_reminder_rules(
        self,
        tenant_id: UUID,
        active_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """List tenant reminder rules."""
        async with self.tenant_context(tenant_id) as conn:
            query = """
                SELECT id, tenant_id, name, trigger_type, offset_minutes, channel,
                       template_text, is_active, quiet_hours_start, quiet_hours_end,
                       metadata, created_at, updated_at
                FROM reminder_rules
                WHERE tenant_id = $1 AND deleted_at IS NULL
            """
            params: List[Any] = [tenant_id]
            if active_only:
                query += " AND is_active = TRUE"
            query += " ORDER BY is_active DESC, trigger_type ASC, created_at DESC"
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]
    async def create_reminder_rule(
        self,
        tenant_id: UUID,
        name: str,
        trigger_type: str = "manual",
        offset_minutes: int = 0,
        channel: str = "whatsapp",
        template_text: str = "",
        is_active: bool = True,
        quiet_hours_start: Optional[Any] = None,
        quiet_hours_end: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a reminder rule."""
        import json
        from uuid import uuid4
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO reminder_rules (
                    id, tenant_id, name, trigger_type, offset_minutes, channel,
                    template_text, is_active, quiet_hours_start, quiet_hours_end,
                    metadata, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, NOW(), NOW())
                RETURNING id, tenant_id, name, trigger_type, offset_minutes, channel,
                          template_text, is_active, quiet_hours_start, quiet_hours_end,
                          metadata, created_at, updated_at
                """,
                uuid4(),
                tenant_id,
                name,
                trigger_type,
                offset_minutes,
                channel,
                template_text,
                is_active,
                quiet_hours_start,
                quiet_hours_end,
                json.dumps(metadata or {}, default=str),
            )
            return dict(row)
    async def update_reminder_rule(
        self,
        tenant_id: UUID,
        rule_id: UUID,
        updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Update mutable reminder rule fields."""
        import json
        allowed = {
            "name", "trigger_type", "offset_minutes", "channel", "template_text",
            "is_active", "quiet_hours_start", "quiet_hours_end", "metadata",
        }
        assignments = []
        values: List[Any] = [tenant_id, rule_id]
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
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE reminder_rules
                SET {', '.join(assignments)}
                WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                RETURNING id, tenant_id, name, trigger_type, offset_minutes, channel,
                          template_text, is_active, quiet_hours_start, quiet_hours_end,
                          metadata, created_at, updated_at
                """,
                *values,
            )
            return dict(row) if row else None
    async def delete_reminder_rule(self, tenant_id: UUID, rule_id: UUID) -> bool:
        """Soft-delete a reminder rule."""
        async with self.tenant_context(tenant_id) as conn:
            result = await conn.execute(
                """
                UPDATE reminder_rules
                SET deleted_at = NOW(), is_active = FALSE, updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                """,
                tenant_id,
                rule_id,
            )
            return "UPDATE 1" in result
    async def list_scheduled_messages(
        self,
        tenant_id: UUID,
        cursor: Optional[str] = None,
        limit: int = 20,
        status: Optional[str] = None,
        trigger_type: Optional[str] = None,
        channel: Optional[str] = None,
        customer_id: Optional[UUID] = None,
        booking_id: Optional[UUID] = None,
        order_id: Optional[UUID] = None,
        payment_request_id: Optional[UUID] = None,
        due_before: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Paginated scheduled reminder/follow-up messages."""
        async with self.tenant_context(tenant_id) as conn:
            optional_tables = await self._tables_exist(conn, ["payment_requests", "review_requests"])
            if payment_request_id and not optional_tables["payment_requests"]:
                return {"items": [], "next_cursor": None}
            payment_select = "p.status AS payment_status" if optional_tables["payment_requests"] else "NULL::text AS payment_status"
            payment_join = (
                "LEFT JOIN payment_requests p ON p.id = sm.payment_request_id AND p.tenant_id = sm.tenant_id"
                if optional_tables["payment_requests"]
                else ""
            )
            review_select = (
                "rv.status AS review_status, rv.rating AS review_rating"
                if optional_tables["review_requests"]
                else "NULL::text AS review_status, NULL::int AS review_rating"
            )
            review_join = (
                "LEFT JOIN review_requests rv ON rv.id = sm.review_request_id AND rv.tenant_id = sm.tenant_id"
                if optional_tables["review_requests"]
                else ""
            )
            query = f"""
                SELECT sm.id, sm.tenant_id, sm.rule_id, rr.name AS rule_name,
                       sm.customer_id, sm.conversation_id, sm.booking_id,
                       sm.order_id, sm.payment_request_id, sm.review_request_id,
                       sm.trigger_type, sm.channel, sm.message_text, sm.status,
                       sm.scheduled_for, sm.locked_at, sm.sent_at, sm.cancelled_at,
                       sm.attempt_count, sm.last_error, sm.created_by, sm.metadata,
                       sm.created_at, sm.updated_at,
                       cu.name AS customer_name, cu.phone_number AS customer_phone,
                       b.service_name AS booking_service_name,
                       b.booking_date, b.booking_time,
                       o.status AS order_status,
                       {payment_select},
                       {review_select}
                FROM scheduled_messages sm
                LEFT JOIN reminder_rules rr ON rr.id = sm.rule_id AND rr.tenant_id = sm.tenant_id
                LEFT JOIN customers cu ON cu.id = sm.customer_id AND cu.tenant_id = sm.tenant_id
                LEFT JOIN bookings b ON b.id = sm.booking_id AND b.tenant_id = sm.tenant_id
                LEFT JOIN orders o ON o.id = sm.order_id AND o.tenant_id = sm.tenant_id
                {payment_join}
                {review_join}
                WHERE sm.tenant_id = $1 AND sm.deleted_at IS NULL
            """
            params: List[Any] = [tenant_id]
            idx = 2
            if status:
                query += f" AND sm.status = ${idx}"
                params.append(status)
                idx += 1
            if trigger_type:
                query += f" AND sm.trigger_type = ${idx}"
                params.append(trigger_type)
                idx += 1
            if channel:
                query += f" AND sm.channel = ${idx}"
                params.append(channel)
                idx += 1
            if customer_id:
                query += f" AND sm.customer_id = ${idx}"
                params.append(customer_id)
                idx += 1
            if booking_id:
                query += f" AND sm.booking_id = ${idx}"
                params.append(booking_id)
                idx += 1
            if order_id:
                query += f" AND sm.order_id = ${idx}"
                params.append(order_id)
                idx += 1
            if payment_request_id:
                query += f" AND sm.payment_request_id = ${idx}"
                params.append(payment_request_id)
                idx += 1
            if due_before:
                query += f" AND sm.scheduled_for <= ${idx}::timestamptz"
                params.append(due_before)
                idx += 1
            if search:
                query += f"""
                    AND (
                        sm.message_text ILIKE ${idx}
                        OR cu.name ILIKE ${idx}
                        OR cu.phone_number ILIKE ${idx}
                        OR sm.id::text ILIKE ${idx}
                    )
                """
                params.append(f"%{search}%")
                idx += 1
            if cursor:
                ts, r_id = decode_cursor(cursor)
                if ts:
                    if r_id:
                        query += f" AND (sm.created_at, sm.id) < (${idx}::timestamptz, ${idx+1}::uuid)"
                        params.extend([ts, r_id])
                        idx += 2
                    else:
                        query += f" AND sm.created_at < ${idx}::timestamptz"
                        params.append(ts)
                        idx += 1
            query += f" ORDER BY sm.created_at DESC, sm.id DESC LIMIT ${idx}"
            params.append(limit + 1)
            rows = await conn.fetch(query, *params)
            items = [dict(row) for row in rows[:limit]]
            next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["id"]) if len(rows) > limit else None
            return {"items": items, "next_cursor": next_cursor}
    async def get_scheduled_message(self, tenant_id: UUID, message_id: UUID) -> Optional[Dict[str, Any]]:
        """Fetch one scheduled reminder/follow-up message."""
        async with self.tenant_context(tenant_id) as conn:
            optional_tables = await self._tables_exist(conn, ["payment_requests", "review_requests"])
            payment_select = "p.status AS payment_status" if optional_tables["payment_requests"] else "NULL::text AS payment_status"
            payment_join = (
                "LEFT JOIN payment_requests p ON p.id = sm.payment_request_id AND p.tenant_id = sm.tenant_id"
                if optional_tables["payment_requests"]
                else ""
            )
            review_select = (
                "rv.status AS review_status, rv.rating AS review_rating"
                if optional_tables["review_requests"]
                else "NULL::text AS review_status, NULL::int AS review_rating"
            )
            review_join = (
                "LEFT JOIN review_requests rv ON rv.id = sm.review_request_id AND rv.tenant_id = sm.tenant_id"
                if optional_tables["review_requests"]
                else ""
            )
            row = await conn.fetchrow(
                f"""
                SELECT sm.id, sm.tenant_id, sm.rule_id, rr.name AS rule_name,
                       sm.customer_id, sm.conversation_id, sm.booking_id,
                       sm.order_id, sm.payment_request_id, sm.review_request_id,
                       sm.trigger_type, sm.channel, sm.message_text, sm.status,
                       sm.scheduled_for, sm.locked_at, sm.sent_at, sm.cancelled_at,
                       sm.attempt_count, sm.last_error, sm.created_by, sm.metadata,
                       sm.created_at, sm.updated_at,
                       cu.name AS customer_name, cu.phone_number AS customer_phone,
                       b.service_name AS booking_service_name,
                       b.booking_date, b.booking_time,
                       o.status AS order_status,
                       {payment_select},
                       {review_select}
                FROM scheduled_messages sm
                LEFT JOIN reminder_rules rr ON rr.id = sm.rule_id AND rr.tenant_id = sm.tenant_id
                LEFT JOIN customers cu ON cu.id = sm.customer_id AND cu.tenant_id = sm.tenant_id
                LEFT JOIN bookings b ON b.id = sm.booking_id AND b.tenant_id = sm.tenant_id
                LEFT JOIN orders o ON o.id = sm.order_id AND o.tenant_id = sm.tenant_id
                {payment_join}
                {review_join}
                WHERE sm.tenant_id = $1 AND sm.id = $2 AND sm.deleted_at IS NULL
                """,
                tenant_id,
                message_id,
            )
            return dict(row) if row else None
    async def create_scheduled_message(
        self,
        tenant_id: UUID,
        scheduled_for: datetime,
        message_text: str,
        trigger_type: str = "manual",
        channel: str = "whatsapp",
        rule_id: Optional[UUID] = None,
        customer_id: Optional[UUID] = None,
        conversation_id: Optional[UUID] = None,
        booking_id: Optional[UUID] = None,
        order_id: Optional[UUID] = None,
        payment_request_id: Optional[UUID] = None,
        review_request_id: Optional[UUID] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        created_by: Optional[UUID] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a scheduled reminder/follow-up message."""
        import json
        from uuid import uuid4
        async with self.tenant_context(tenant_id) as conn:
            resolved_customer_id = customer_id
            resolved_conversation_id = conversation_id
            if rule_id:
                rule_exists = await conn.fetchval(
                    """
                    SELECT TRUE
                    FROM reminder_rules
                    WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                    """,
                    tenant_id,
                    rule_id,
                )
                if not rule_exists:
                    return None
            if booking_id:
                booking = await conn.fetchrow(
                    """
                    SELECT customer_id, conversation_id
                    FROM bookings
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id,
                    booking_id,
                )
                if not booking:
                    return None
                resolved_customer_id = resolved_customer_id or booking["customer_id"]
                resolved_conversation_id = resolved_conversation_id or booking["conversation_id"]
            if order_id:
                order = await conn.fetchrow(
                    """
                    SELECT customer_id, conversation_id
                    FROM orders
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id,
                    order_id,
                )
                if not order:
                    return None
                resolved_customer_id = resolved_customer_id or order["customer_id"]
                resolved_conversation_id = resolved_conversation_id or order["conversation_id"]
            if payment_request_id:
                optional_tables = await self._tables_exist(conn, ["payment_requests", "review_requests"])
                if not optional_tables["payment_requests"]:
                    return None
                payment = await conn.fetchrow(
                    """
                    SELECT customer_id, conversation_id, booking_id, order_id
                    FROM payment_requests
                    WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                    """,
                    tenant_id,
                    payment_request_id,
                )
                if not payment:
                    return None
                resolved_customer_id = resolved_customer_id or payment["customer_id"]
                resolved_conversation_id = resolved_conversation_id or payment["conversation_id"]
                booking_id = booking_id or payment["booking_id"]
                order_id = order_id or payment["order_id"]
            if review_request_id:
                optional_tables = await self._tables_exist(conn, ["payment_requests", "review_requests"])
                if not optional_tables["review_requests"]:
                    return None
                review = await conn.fetchrow(
                    """
                    SELECT customer_id, conversation_id, booking_id, order_id
                    FROM review_requests
                    WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                    """,
                    tenant_id,
                    review_request_id,
                )
                if not review:
                    return None
                resolved_customer_id = resolved_customer_id or review["customer_id"]
                resolved_conversation_id = resolved_conversation_id or review["conversation_id"]
                booking_id = booking_id or review["booking_id"]
                order_id = order_id or review["order_id"]
            if resolved_customer_id:
                customer_exists = await conn.fetchval(
                    """
                    SELECT TRUE
                    FROM customers
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id,
                    resolved_customer_id,
                )
                if not customer_exists:
                    return None
            async with conn.transaction():
                message_id = uuid4()
                row = await conn.fetchrow(
                    """
                    INSERT INTO scheduled_messages (
                        id, tenant_id, rule_id, customer_id, conversation_id,
                        booking_id, order_id, payment_request_id, review_request_id,
                        trigger_type, channel, message_text, status, scheduled_for,
                        created_by, idempotency_key, metadata, created_at, updated_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8, $9,
                        $10, $11, $12, 'scheduled', $13,
                        $14, $15, $16::jsonb, NOW(), NOW()
                    )
                    ON CONFLICT (tenant_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                    DO NOTHING
                    RETURNING id
                    """,
                    message_id,
                    tenant_id,
                    rule_id,
                    resolved_customer_id,
                    resolved_conversation_id,
                    booking_id,
                    order_id,
                    payment_request_id,
                    review_request_id,
                    trigger_type,
                    channel,
                    message_text,
                    scheduled_for,
                    created_by,
                    idempotency_key,
                    json.dumps(metadata or {}, default=str),
                )
                if not row and idempotency_key:
                    existing_id = await conn.fetchval(
                        """
                        SELECT id
                        FROM scheduled_messages
                        WHERE tenant_id = $1 AND idempotency_key = $2 AND deleted_at IS NULL
                        """,
                        tenant_id,
                        idempotency_key,
                    )
                    if existing_id:
                        existing = await self.get_scheduled_message(tenant_id, existing_id)
                        if existing:
                            existing = dict(existing)
                            existing_metadata = existing.get("metadata") or {}
                            if isinstance(existing_metadata, dict):
                                existing["metadata"] = {**existing_metadata, "queue_reused": True}
                            return existing
                if not row:
                    return None
                await conn.execute(
                    """
                    INSERT INTO scheduled_message_events (
                        id, tenant_id, scheduled_message_id, event_type, actor_type,
                        actor_user_id, event_data, created_at
                    )
                    VALUES ($1, $2, $3, 'created', $4, $5, $6::jsonb, NOW())
                    """,
                    uuid4(),
                    tenant_id,
                    row["id"],
                    "tenant_user" if created_by else "system",
                    created_by,
                    json.dumps({"scheduled_for": scheduled_for.isoformat(), "channel": channel}, default=str),
                )
            return await self.get_scheduled_message(tenant_id, row["id"])
    async def action_scheduled_message(
        self,
        tenant_id: UUID,
        message_id: UUID,
        action: str,
        actor_id: Optional[UUID] = None,
        scheduled_for: Optional[datetime] = None,
        snooze_minutes: Optional[int] = None,
        note: Optional[str] = None,
        last_error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Apply a scheduled message lifecycle action."""
        import json
        from uuid import uuid4
        now = datetime.now(timezone.utc)
        next_status_by_action = {
            "cancel": "cancelled",
            "snooze": "snoozed",
            "mark_sent": "sent",
            "mark_failed": "failed",
            "skip": "skipped",
            "mark_due": "due",
            "retry": "scheduled",
        }
        next_status = next_status_by_action.get(action)
        if not next_status:
            return None
        if action == "snooze" and scheduled_for is None:
            scheduled_for = now + timedelta(minutes=snooze_minutes or 60)
        if action == "retry" and scheduled_for is None:
            scheduled_for = now
        async with self.tenant_context(tenant_id) as conn:
            async with conn.transaction():
                current = await conn.fetchrow(
                    """
                    SELECT id, status, scheduled_for
                    FROM scheduled_messages
                    WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                    FOR UPDATE
                    """,
                    tenant_id,
                    message_id,
                )
                if not current:
                    return None
                updates = ["status = $3", "updated_at = NOW()"]
                values: List[Any] = [tenant_id, message_id, next_status]
                idx = 4
                if action in {"snooze", "retry"} and scheduled_for is not None:
                    updates.append(f"scheduled_for = ${idx}")
                    values.append(scheduled_for)
                    idx += 1
                if action == "mark_sent":
                    updates.append("sent_at = COALESCE(sent_at, NOW())")
                    updates.append("last_error = NULL")
                if action == "cancel":
                    updates.append("cancelled_at = COALESCE(cancelled_at, NOW())")
                if action == "mark_failed":
                    updates.append("attempt_count = attempt_count + 1")
                    updates.append(f"last_error = ${idx}")
                    values.append(last_error or note or "Marked failed")
                    idx += 1
                if action == "retry":
                    updates.append("last_error = NULL")
                await conn.execute(
                    f"""
                    UPDATE scheduled_messages
                    SET {', '.join(updates)}
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    *values,
                )
                event_payload = {
                    "action": action,
                    "previous_status": current["status"],
                    "status": next_status,
                    "note": note,
                    "scheduled_for": scheduled_for.isoformat() if scheduled_for else None,
                    "metadata": metadata or {},
                }
                await conn.execute(
                    """
                    INSERT INTO scheduled_message_events (
                        id, tenant_id, scheduled_message_id, event_type, actor_type,
                        actor_user_id, event_data, created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, NOW())
                    """,
                    uuid4(),
                    tenant_id,
                    message_id,
                    action,
                    "tenant_user" if actor_id else "system",
                    actor_id,
                    json.dumps(event_payload, default=str),
                )
            return await self.get_scheduled_message(tenant_id, message_id)
    async def claim_due_scheduled_messages(
        self,
        limit: int,
        lock_ttl_seconds: int,
        max_attempts: int,
    ) -> List[Dict[str, Any]]:
        import datetime
        interval = datetime.timedelta(seconds=lock_ttl_seconds)
        async with self.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT sm.id, sm.tenant_id, sm.rule_id, sm.conversation_id, sm.channel, sm.message_text,
                           sm.trigger_type, sm.attempt_count, sm.customer_id, sm.metadata,
                           cu.phone_number AS customer_phone
                    FROM scheduled_messages sm
                    LEFT JOIN customers cu ON cu.id = sm.customer_id AND cu.tenant_id = sm.tenant_id
                    WHERE sm.status = 'scheduled'
                      AND sm.scheduled_for <= NOW()
                      AND sm.deleted_at IS NULL
                      AND sm.attempt_count < $1
                      AND (sm.locked_at IS NULL OR sm.locked_at < NOW() - $2::interval)
                    ORDER BY sm.scheduled_for ASC
                    LIMIT $3
                    FOR UPDATE OF sm SKIP LOCKED
                    """,
                    max_attempts,
                    interval,
                    limit,
                )
                if rows:
                    ids = [row["id"] for row in rows]
                    await conn.execute(
                        """
                        UPDATE scheduled_messages
                        SET locked_at = NOW()
                        WHERE id = ANY($1::uuid[])
                        """,
                        ids,
                    )
                return [dict(row) for row in rows]
    async def record_scheduled_message_dispatch_result(
        self,
        tenant_id: UUID,
        message_id: UUID,
        status: str,
        event_type: str,
        provider_message_id: Optional[str] = None,
        last_error: Optional[str] = None,
        retry_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        import json
        from uuid import uuid4
        async with self.tenant_context(tenant_id) as conn:
            async with conn.transaction():
                # Get current message
                current = await conn.fetchrow(
                    "SELECT status, attempt_count FROM scheduled_messages WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL FOR UPDATE",
                    tenant_id,
                    message_id,
                )
                if not current:
                    raise ValueError("Scheduled message not found")
                final_status = status
                updates = ["locked_at = NULL", "updated_at = NOW()"]
                values = [tenant_id, message_id]
                idx = 3
                if status == "sent":
                    updates.append("sent_at = NOW()")
                    updates.append("last_error = NULL")
                elif status == "failed":
                    updates.append("attempt_count = attempt_count + 1")
                    if last_error:
                        updates.append(f"last_error = ${idx}")
                        values.append(last_error)
                        idx += 1
                    if retry_at:
                        final_status = "scheduled"
                        updates.append(f"scheduled_for = ${idx}")
                        values.append(retry_at)
                        idx += 1
                updates.append(f"status = ${idx}")
                values.append(final_status)
                idx += 1
                await conn.execute(
                    f"UPDATE scheduled_messages SET {', '.join(updates)} WHERE tenant_id = $1 AND id = $2",
                    *values,
                )
                # Insert event
                event_payload = {
                    "previous_status": current["status"],
                    "status": final_status,
                    "provider_message_id": provider_message_id,
                    "last_error": last_error,
                    "retry_at": retry_at.isoformat() if retry_at else None,
                    "metadata": metadata or {},
                }
                await conn.execute(
                    """
                    INSERT INTO scheduled_message_events (
                        id, tenant_id, scheduled_message_id, event_type, actor_type, event_data, created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, NOW())
                    """,
                    uuid4(),
                    tenant_id,
                    message_id,
                    event_type,
                    "system",
                    json.dumps(event_payload, default=str),
                )
                return {"id": message_id, "status": final_status}
    async def list_scheduled_message_events(
        self,
        tenant_id: UUID,
        message_id: UUID,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List scheduled message lifecycle events."""
        async with self.tenant_context(tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT id, tenant_id, scheduled_message_id, event_type,
                       actor_type, actor_user_id, event_data, created_at
                FROM scheduled_message_events
                WHERE tenant_id = $1 AND scheduled_message_id = $2
                ORDER BY created_at DESC
                LIMIT $3
                """,
                tenant_id,
                message_id,
                limit,
            )
            return [dict(row) for row in rows]
    # ==================== REVIEW COLLECTION ====================
    async def get_review_settings(self, tenant_id: UUID) -> Dict[str, Any]:
        """Fetch or create tenant review collection settings."""
        from uuid import uuid4
        async with self.tenant_context(tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO review_settings (id, tenant_id, created_at, updated_at)
                VALUES ($1, $2, NOW(), NOW())
                ON CONFLICT (tenant_id) DO NOTHING
                """,
                uuid4(),
                tenant_id,
            )
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, is_active, public_review_url,
                       ask_after_booking_completed, ask_after_order_completed,
                       ask_delay_minutes, positive_rating_threshold,
                       negative_feedback_escalation_enabled,
                       request_template, positive_template, negative_template,
                       metadata, created_at, updated_at
                FROM review_settings
                WHERE tenant_id = $1
                """,
                tenant_id,
            )
            return dict(row)
    async def update_review_settings(
        self,
        tenant_id: UUID,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update tenant review settings."""
        import json
        await self.get_review_settings(tenant_id)
        allowed = {
            "is_active",
            "public_review_url",
            "ask_after_booking_completed",
            "ask_after_order_completed",
            "ask_delay_minutes",
            "positive_rating_threshold",
            "negative_feedback_escalation_enabled",
            "request_template",
            "positive_template",
            "negative_template",
            "metadata",
        }
        assignments = []
        values: List[Any] = [tenant_id]
        idx = 2
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
            return await self.get_review_settings(tenant_id)
        assignments.append("updated_at = NOW()")
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE review_settings
                SET {', '.join(assignments)}
                WHERE tenant_id = $1
                RETURNING id, tenant_id, is_active, public_review_url,
                          ask_after_booking_completed, ask_after_order_completed,
                          ask_delay_minutes, positive_rating_threshold,
                          negative_feedback_escalation_enabled,
                          request_template, positive_template, negative_template,
                          metadata, created_at, updated_at
                """,
                *values,
            )
            return dict(row)
    async def list_review_requests(
        self,
        tenant_id: UUID,
        cursor: Optional[str] = None,
        limit: int = 20,
        status: Optional[str] = None,
        source: Optional[str] = None,
        rating: Optional[int] = None,
        customer_id: Optional[UUID] = None,
        booking_id: Optional[UUID] = None,
        order_id: Optional[UUID] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Paginated review request list."""
        async with self.tenant_context(tenant_id) as conn:
            query = """
                SELECT rr.id, rr.tenant_id, rr.customer_id, rr.conversation_id,
                       rr.booking_id, rr.order_id, rr.status, rr.source,
                       rr.rating, rr.feedback_text, rr.public_review_url,
                       rr.sent_at, rr.responded_at, rr.public_requested_at,
                       rr.escalated_at, rr.skipped_at, rr.created_by,
                       rr.idempotency_key, rr.metadata, rr.created_at, rr.updated_at,
                       cu.name AS customer_name, cu.phone_number AS customer_phone,
                       b.service_name AS booking_service_name,
                       b.booking_date, b.booking_time,
                       o.status AS order_status, o.total_amount AS order_total_amount
                FROM review_requests rr
                LEFT JOIN customers cu ON cu.id = rr.customer_id AND cu.tenant_id = rr.tenant_id
                LEFT JOIN bookings b ON b.id = rr.booking_id AND b.tenant_id = rr.tenant_id
                LEFT JOIN orders o ON o.id = rr.order_id AND o.tenant_id = rr.tenant_id
                WHERE rr.tenant_id = $1 AND rr.deleted_at IS NULL
            """
            params: List[Any] = [tenant_id]
            idx = 2
            if status:
                query += f" AND rr.status = ${idx}"
                params.append(status)
                idx += 1
            if source:
                query += f" AND rr.source = ${idx}"
                params.append(source)
                idx += 1
            if rating:
                query += f" AND rr.rating = ${idx}"
                params.append(rating)
                idx += 1
            if customer_id:
                query += f" AND rr.customer_id = ${idx}"
                params.append(customer_id)
                idx += 1
            if booking_id:
                query += f" AND rr.booking_id = ${idx}"
                params.append(booking_id)
                idx += 1
            if order_id:
                query += f" AND rr.order_id = ${idx}"
                params.append(order_id)
                idx += 1
            if search:
                query += f"""
                    AND (
                        rr.feedback_text ILIKE ${idx}
                        OR rr.id::text ILIKE ${idx}
                        OR cu.name ILIKE ${idx}
                        OR cu.phone_number ILIKE ${idx}
                    )
                """
                params.append(f"%{search}%")
                idx += 1
            if cursor:
                ts, r_id = decode_cursor(cursor)
                if ts:
                    if r_id:
                        query += f" AND (rr.created_at, rr.id) < (${idx}::timestamptz, ${idx+1}::uuid)"
                        params.extend([ts, r_id])
                        idx += 2
                    else:
                        query += f" AND rr.created_at < ${idx}::timestamptz"
                        params.append(ts)
                        idx += 1
            query += f" ORDER BY rr.created_at DESC, rr.id DESC LIMIT ${idx}"
            params.append(limit + 1)
            rows = await conn.fetch(query, *params)
            items = [dict(row) for row in rows[:limit]]
            next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["id"]) if len(rows) > limit else None
            return {"items": items, "next_cursor": next_cursor}
    async def get_review_request(self, tenant_id: UUID, review_id: UUID) -> Optional[Dict[str, Any]]:
        """Fetch one review request."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT rr.id, rr.tenant_id, rr.customer_id, rr.conversation_id,
                       rr.booking_id, rr.order_id, rr.status, rr.source,
                       rr.rating, rr.feedback_text, rr.public_review_url,
                       rr.sent_at, rr.responded_at, rr.public_requested_at,
                       rr.escalated_at, rr.skipped_at, rr.created_by,
                       rr.idempotency_key, rr.metadata, rr.created_at, rr.updated_at,
                       cu.name AS customer_name, cu.phone_number AS customer_phone,
                       b.service_name AS booking_service_name,
                       b.booking_date, b.booking_time,
                       o.status AS order_status, o.total_amount AS order_total_amount
                FROM review_requests rr
                LEFT JOIN customers cu ON cu.id = rr.customer_id AND cu.tenant_id = rr.tenant_id
                LEFT JOIN bookings b ON b.id = rr.booking_id AND b.tenant_id = rr.tenant_id
                LEFT JOIN orders o ON o.id = rr.order_id AND o.tenant_id = rr.tenant_id
                WHERE rr.tenant_id = $1 AND rr.id = $2 AND rr.deleted_at IS NULL
                """,
                tenant_id,
                review_id,
            )
            return dict(row) if row else None
    async def create_review_request(
        self,
        tenant_id: UUID,
        status: str = "draft",
        source: str = "manual",
        customer_id: Optional[UUID] = None,
        conversation_id: Optional[UUID] = None,
        booking_id: Optional[UUID] = None,
        order_id: Optional[UUID] = None,
        public_review_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        created_by: Optional[UUID] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a review request, inferring customer/conversation from linked records."""
        import json
        from uuid import uuid4
        settings = await self.get_review_settings(tenant_id)
        resolved_customer_id = customer_id
        resolved_conversation_id = conversation_id
        resolved_public_url = public_review_url or settings.get("public_review_url")
        async with self.tenant_context(tenant_id) as conn:
            if booking_id:
                booking = await conn.fetchrow(
                    """
                    SELECT customer_id, conversation_id
                    FROM bookings
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id,
                    booking_id,
                )
                if not booking:
                    return None
                resolved_customer_id = resolved_customer_id or booking["customer_id"]
                resolved_conversation_id = resolved_conversation_id or booking["conversation_id"]
            if order_id:
                order = await conn.fetchrow(
                    """
                    SELECT customer_id, conversation_id
                    FROM orders
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id,
                    order_id,
                )
                if not order:
                    return None
                resolved_customer_id = resolved_customer_id or order["customer_id"]
                resolved_conversation_id = resolved_conversation_id or order["conversation_id"]
            if resolved_customer_id:
                customer_exists = await conn.fetchval(
                    """
                    SELECT TRUE
                    FROM customers
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id,
                    resolved_customer_id,
                )
                if not customer_exists:
                    return None
            if resolved_conversation_id:
                conversation_exists = await conn.fetchval(
                    """
                    SELECT TRUE
                    FROM conversations
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id,
                    resolved_conversation_id,
                )
                if not conversation_exists:
                    return None
            async with conn.transaction():
                review_id = uuid4()
                row = await conn.fetchrow(
                    """
                    INSERT INTO review_requests (
                        id, tenant_id, customer_id, conversation_id,
                        booking_id, order_id, status, source, public_review_url,
                        created_by, idempotency_key, metadata, created_at, updated_at
                    )
                    VALUES (
                        $1, $2, $3, $4,
                        $5, $6, $7, $8, $9,
                        $10, $11, $12::jsonb, NOW(), NOW()
                    )
                    ON CONFLICT (tenant_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                    DO NOTHING
                    RETURNING id
                    """,
                    review_id,
                    tenant_id,
                    resolved_customer_id,
                    resolved_conversation_id,
                    booking_id,
                    order_id,
                    status,
                    source,
                    resolved_public_url,
                    created_by,
                    idempotency_key,
                    json.dumps(metadata or {}, default=str),
                )
                if not row and idempotency_key:
                    existing_id = await conn.fetchval(
                        """
                        SELECT id
                        FROM review_requests
                        WHERE tenant_id = $1 AND idempotency_key = $2 AND deleted_at IS NULL
                        """,
                        tenant_id,
                        idempotency_key,
                    )
                    if existing_id:
                        return await self.get_review_request(tenant_id, existing_id)
                if not row:
                    return None
                await conn.execute(
                    """
                    INSERT INTO review_events (
                        id, tenant_id, review_request_id, event_type, actor_type,
                        actor_user_id, event_data, created_at
                    )
                    VALUES ($1, $2, $3, 'created', $4, $5, $6::jsonb, NOW())
                    """,
                    uuid4(),
                    tenant_id,
                    row["id"],
                    "tenant_user" if created_by else "system",
                    created_by,
                    json.dumps({"status": status, "source": source}, default=str),
                )
            return await self.get_review_request(tenant_id, row["id"])
    async def update_review_request(
        self,
        tenant_id: UUID,
        review_id: UUID,
        updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Update editable review request fields."""
        import json
        allowed = {"status", "public_review_url", "rating", "feedback_text", "metadata"}
        assignments = []
        values: List[Any] = [tenant_id, review_id]
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
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE review_requests
                SET {', '.join(assignments)}
                WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                RETURNING id
                """,
                *values,
            )
            return await self.get_review_request(tenant_id, row["id"]) if row else None

    async def delete_review_request(self, tenant_id: UUID, review_id: UUID) -> bool:
        """Soft-delete a review request."""
        async with self.tenant_context(tenant_id) as conn:
            result = await conn.execute(
                """
                UPDATE review_requests
                SET deleted_at = NOW(), updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                """,
                tenant_id,
                review_id,
            )
            return "UPDATE 1" in result

    async def get_review_metrics(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        """Get summary metrics for review requests."""
        async with self.tenant_context(tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT
                    status,
                    COUNT(*)::int AS count,
                    COUNT(rating)::int AS rated_count,
                    AVG(rating)::float AS average_rating,
                    COUNT(CASE WHEN rating <= 2 THEN 1 END)::int AS low_ratings_count
                FROM review_requests
                WHERE tenant_id = $1 AND deleted_at IS NULL
                GROUP BY status
                """,
                tenant_id,
            )
            return [dict(row) for row in rows]

    async def action_review_request(
        self,
        tenant_id: UUID,
        review_id: UUID,
        action: str,
        actor_id: Optional[UUID] = None,
        rating: Optional[int] = None,
        feedback_text: Optional[str] = None,
        public_review_url: Optional[str] = None,
        note: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Apply a review request lifecycle action."""
        import json
        from uuid import uuid4
        settings = await self.get_review_settings(tenant_id)
        async with self.tenant_context(tenant_id) as conn:
            async with conn.transaction():
                current = await conn.fetchrow(
                    """
                    SELECT id, status, rating, public_review_url
                    FROM review_requests
                    WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL
                    FOR UPDATE
                    """,
                    tenant_id,
                    review_id,
                )
                if not current:
                    return None
                resolved_public_url = public_review_url or current["public_review_url"] or settings.get("public_review_url")
                next_status_by_action = {
                    "send": "sent",
                    "request_public_review": "public_review_requested",
                    "escalate_negative": "negative_feedback_escalated",
                    "skip": "skipped",
                    "expire": "expired",
                    "reopen": "sent",
                }
                next_status = next_status_by_action.get(action)
                if action == "submit_rating":
                    if rating is None:
                        return None
                    threshold = settings.get("positive_rating_threshold") or 4
                    negative_enabled = bool(settings.get("negative_feedback_escalation_enabled", True))
                    if rating >= threshold and resolved_public_url:
                        next_status = "public_review_requested"
                    elif rating >= threshold:
                        next_status = "responded"
                    elif negative_enabled:
                        next_status = "negative_feedback_escalated"
                    else:
                        next_status = "responded"
                if not next_status:
                    return None
                updates = ["status = $3", "updated_at = NOW()"]
                values: List[Any] = [tenant_id, review_id, next_status]
                idx = 4
                if action in {"send", "reopen"}:
                    updates.append("sent_at = COALESCE(sent_at, NOW())")
                if action == "submit_rating":
                    updates.append(f"rating = ${idx}")
                    values.append(rating)
                    idx += 1
                    updates.append(f"feedback_text = ${idx}")
                    values.append(feedback_text)
                    idx += 1
                    updates.append("responded_at = COALESCE(responded_at, NOW())")
                if action in {"submit_rating", "request_public_review"} and resolved_public_url:
                    updates.append(f"public_review_url = ${idx}")
                    values.append(resolved_public_url)
                    idx += 1
                if next_status == "public_review_requested":
                    updates.append("public_requested_at = COALESCE(public_requested_at, NOW())")
                if next_status == "negative_feedback_escalated":
                    updates.append("escalated_at = COALESCE(escalated_at, NOW())")
                if action == "skip":
                    updates.append("skipped_at = COALESCE(skipped_at, NOW())")
                await conn.execute(
                    f"""
                    UPDATE review_requests
                    SET {', '.join(updates)}
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    *values,
                )
                event_payload = {
                    "action": action,
                    "previous_status": current["status"],
                    "status": next_status,
                    "rating": rating,
                    "feedback_text": feedback_text,
                    "public_review_url": resolved_public_url,
                    "note": note,
                    "metadata": metadata or {},
                }
                await conn.execute(
                    """
                    INSERT INTO review_events (
                        id, tenant_id, review_request_id, event_type, actor_type,
                        actor_user_id, event_data, created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, NOW())
                    """,
                    uuid4(),
                    tenant_id,
                    review_id,
                    action,
                    "tenant_user" if actor_id else "system",
                    actor_id,
                    json.dumps(event_payload, default=str),
                )
            return await self.get_review_request(tenant_id, review_id)
    async def list_review_events(
        self,
        tenant_id: UUID,
        review_id: UUID,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List review request events."""
        async with self.tenant_context(tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT id, tenant_id, review_request_id, event_type,
                       actor_type, actor_user_id, event_data, created_at
                FROM review_events
                WHERE tenant_id = $1 AND review_request_id = $2
                ORDER BY created_at DESC
                LIMIT $3
                """,
                tenant_id,
                review_id,
                limit,
            )
            return [dict(row) for row in rows]
    # ==================== CATALOG / INVENTORY ====================
    async def list_catalog_categories(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        """List active catalog categories with item counts."""
        async with self.tenant_context(tenant_id) as conn:
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
    async def create_catalog_category(
        self,
        tenant_id: UUID,
        name: str,
        description: Optional[str] = None,
        sort_order: int = 0,
        is_active: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a tenant catalog category."""
        import json
        from uuid import uuid4
        async with self.tenant_context(tenant_id) as conn:
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
    async def update_catalog_category(
        self,
        tenant_id: UUID,
        category_id: UUID,
        updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Update mutable catalog category fields."""
        import json
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
        async with self.tenant_context(tenant_id) as conn:
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
    async def delete_catalog_category(self, tenant_id: UUID, category_id: UUID) -> bool:
        """Soft-delete a category and detach its items."""
        async with self.tenant_context(tenant_id) as conn:
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
    async def list_catalog_items(
        self,
        tenant_id: UUID,
        category_id: Optional[UUID] = None,
        search: Optional[str] = None,
        active_only: bool = False,
        available_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """List catalog items for management and order lookup surfaces."""
        async with self.tenant_context(tenant_id) as conn:
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
    async def get_catalog_item(self, tenant_id: UUID, item_id: UUID) -> Optional[Dict[str, Any]]:
        """Fetch one catalog item with category metadata."""
        async with self.tenant_context(tenant_id) as conn:
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
    async def create_catalog_item(
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
        import json
        from uuid import uuid4
        async with self.tenant_context(tenant_id) as conn:
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
            return await self.get_catalog_item(tenant_id, item_id)
    async def update_catalog_item(
        self,
        tenant_id: UUID,
        item_id: UUID,
        updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Update mutable catalog item fields."""
        import json
        allowed = {
            "category_id", "name", "description", "price", "currency", "sku",
            "image_url", "is_active", "is_available", "stock_tracking_enabled",
            "stock_quantity", "low_stock_threshold", "metadata",
        }
        async with self.tenant_context(tenant_id) as conn:
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
            return await self.get_catalog_item(tenant_id, item_id)
    async def delete_catalog_item(self, tenant_id: UUID, item_id: UUID) -> bool:
        """Soft-delete a catalog item."""
        async with self.tenant_context(tenant_id) as conn:
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
    async def adjust_catalog_stock(
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
        import json
        from uuid import uuid4
        async with self.tenant_context(tenant_id) as conn:
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
            return await self.get_catalog_item(tenant_id, item_id)
    async def list_inventory_events(
        self,
        tenant_id: UUID,
        item_id: UUID,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List inventory events for one item."""
        async with self.tenant_context(tenant_id) as conn:
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
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT i.id, i.name, i.description, i.price, i.currency,
                       i.category_id, c.name AS category_name, TRUE AS enabled
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
                           i.category_id, c.name AS category_name, TRUE AS enabled
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
    # ==================== TENANT LOOKUP ====================
    async def get_tenant(self, tenant_id: UUID) -> Optional[Dict[str, Any]]:
        """Get tenant by ID."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT id, name, slug, plan_code, status, ai_mode, trial_ends_at,
                       current_period_end, created_at
                FROM tenants
                WHERE id = $1 AND deleted_at IS NULL
                """,
                tenant_id
            )
            return dict(row) if row else None
    async def get_user(self, user_id: UUID) -> Optional[Dict[str, Any]]:
        """Get user by ID."""
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, phone_number, email, name, created_at FROM users WHERE id = $1 AND deleted_at IS NULL",
                user_id
            )
            return dict(row) if row else None
    async def get_user_tenant_role(self, user_id: UUID, tenant_id: UUID) -> Optional[str]:
        """Get user's role within a tenant."""
        async with self.acquire() as conn:
            return await conn.fetchval(
                "SELECT role FROM user_tenants WHERE user_id = $1 AND tenant_id = $2",
                user_id, tenant_id
            )
    # ==================== TENANT INVITATIONS ====================
    async def create_tenant_invitation(
        self,
        tenant_id: UUID,
        email: str,
        role: str,
        token_hash: str,
        invited_by_user_id: UUID,
        expires_at: datetime
    ) -> Dict[str, Any]:
        """Create a new tenant invitation."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tenant_invitations
                    (tenant_id, email, role, token_hash, invited_by_user_id, expires_at, status)
                VALUES ($1, $2, $3, $4, $5, $6, 'pending')
                RETURNING id, tenant_id, email, role, status, expires_at, created_at
                """,
                tenant_id, email.strip().lower(), role, token_hash, invited_by_user_id, expires_at
            )
            return dict(row)
    async def get_tenant_invitations(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        """List invites for a tenant, omitting the raw token_hash for security."""
        async with self.tenant_context(tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT id, tenant_id, email, role, status, invited_by_user_id,
                       accepted_by_user_id, expires_at, accepted_at, revoked_at, created_at, updated_at
                FROM tenant_invitations
                WHERE tenant_id = $1
                ORDER BY created_at DESC
                """,
                tenant_id
            )
            return [dict(row) for row in rows]
    async def get_tenant_invitation_by_hash(self, token_hash: str) -> Optional[Dict[str, Any]]:
        """Fetch invite by hashed token."""
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, email, role, token_hash, status, expires_at,
                       invited_by_user_id, accepted_by_user_id, accepted_at, revoked_at
                FROM tenant_invitations
                WHERE token_hash = $1
                """,
                token_hash
            )
            return dict(row) if row else None
    async def revoke_tenant_invitation(self, tenant_id: UUID, invite_id: UUID) -> Optional[Dict[str, Any]]:
        """Revoke a pending invitation. Idempotent if already non-pending."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT id, tenant_id, email, role, status, revoked_at FROM tenant_invitations WHERE id = $1 AND tenant_id = $2",
                invite_id, tenant_id
            )
            if not row:
                return None
            if row["status"] != "pending":
                return dict(row)
            updated = await conn.fetchrow(
                """
                UPDATE tenant_invitations
                SET status = 'revoked', revoked_at = NOW(), updated_at = NOW()
                WHERE id = $1 AND tenant_id = $2 AND status = 'pending'
                RETURNING id, tenant_id, email, role, status, revoked_at
                """,
                invite_id, tenant_id
            )
            return dict(updated) if updated else dict(row)
    async def accept_tenant_invitation(
        self,
        tenant_id: UUID,
        invite_id: UUID,
        user_id: UUID,
        role: str,
        email: str
    ) -> Dict[str, Any]:
        """
        Atomically accept invitation. Locks the invitation row, checks status, expiry,
        and updates user_tenants membership (handling existing role).
        """
        async with self.tenant_context(tenant_id) as conn:
            async with conn.transaction():
                # Lock the invitation row
                invite = await conn.fetchrow(
                    """
                    SELECT id, tenant_id, email, role, status, expires_at
                    FROM tenant_invitations
                    WHERE id = $1 AND tenant_id = $2
                    FOR UPDATE
                    """,
                    invite_id, tenant_id
                )
                if not invite:
                    raise ValueError("Invitation not found")
                if invite["status"] != "pending":
                    raise ValueError(f"Invitation is already {invite['status']}")
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                if invite["expires_at"] < now:
                    await conn.execute(
                        "UPDATE tenant_invitations SET status = 'expired', updated_at = NOW() WHERE id = $1",
                        invite_id
                    )
                    raise ValueError("Invitation has expired")
                if invite["email"].strip().lower() != email.strip().lower():
                    raise ValueError("Invitation email mismatch")
                # Check existing tenant membership
                existing_role = await conn.fetchval(
                    "SELECT role FROM user_tenants WHERE user_id = $1 AND tenant_id = $2",
                    user_id, tenant_id
                )
                ROLE_HIERARCHY = {
                    "member": 1,
                    "operator": 2,
                    "tenant_admin": 3,
                    "admin": 4,
                    "owner": 5,
                    "super_admin": 6,
                }
                final_role = role
                if existing_role:
                    existing_role_lower = existing_role.strip().lower()
                    if ROLE_HIERARCHY.get(existing_role_lower, 0) >= ROLE_HIERARCHY.get(role.strip().lower(), 0):
                        final_role = existing_role_lower
                    await conn.execute(
                        "UPDATE user_tenants SET role = $3 WHERE user_id = $1 AND tenant_id = $2",
                        user_id, tenant_id, final_role
                    )
                else:
                    from uuid import uuid4
                    await conn.execute(
                        "INSERT INTO user_tenants (id, user_id, tenant_id, role) VALUES ($1, $2, $3, $4)",
                        uuid4(), user_id, tenant_id, final_role
                    )
                updated_invite = await conn.fetchrow(
                    """
                    UPDATE tenant_invitations
                    SET status = 'accepted',
                        accepted_by_user_id = $2,
                        accepted_at = NOW(),
                        updated_at = NOW()
                    WHERE id = $1
                    RETURNING id, tenant_id, email, role, status, accepted_at
                    """,
                    invite_id, user_id
                )
                return dict(updated_invite)
    # ==================== WHATSAPP ACCOUNTS ====================
    async def list_whatsapp_accounts(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        """List WhatsApp accounts for a tenant."""
        async with self.tenant_context(tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT id, tenant_id, phone_number, phone_number_id, provider_account_id,
                       waba_id, business_id, display_name, verification_status,
                       onboarding_mode, connected_at, created_at,
                       CASE
                           WHEN verification_status IN ('connected', 'verified') THEN 'connected'
                           ELSE 'disconnected'
                       END AS status,
                       (
                           SELECT MAX(created_at)
                           FROM messages
                           WHERE messages.tenant_id = whatsapp_accounts.tenant_id
                             AND messages.direction = 'outbound'
                       ) AS last_message_at
                FROM whatsapp_accounts WHERE tenant_id = $1
                ORDER BY created_at
                """,
                tenant_id
            )
            return [dict(r) for r in rows]
    async def create_whatsapp_account(
        self,
        tenant_id: UUID,
        phone_number: str,
        phone_number_id: Optional[str] = None,
        provider_account_id: Optional[str] = None,
        display_name: Optional[str] = None,
        verification_status: str = "pending",
        access_token: Optional[str] = None,
        waba_id: Optional[str] = None,
        business_id: Optional[str] = None,
        onboarding_mode: str = "manual",
        app_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add or update a WhatsApp account for a tenant."""
        from uuid import uuid4
        from backend.security.secret_box import encrypt_optional_secret
        stored_access_token = encrypt_optional_secret(access_token)
        async with self.tenant_context(tenant_id) as conn:
            if phone_number_id:
                existing_phone_id = await conn.fetchrow(
                    """
                    SELECT tenant_id, phone_number
                    FROM whatsapp_accounts
                    WHERE phone_number_id = $1
                    """,
                    phone_number_id,
                )
                if existing_phone_id and (
                    existing_phone_id["tenant_id"] != tenant_id
                    or existing_phone_id["phone_number"] != phone_number
                ):
                    raise ValueError("WhatsApp phone number ID is already connected to another account")
            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO whatsapp_accounts (
                        id, tenant_id, phone_number, phone_number_id, provider_account_id,
                        display_name, verification_status, access_token, waba_id, business_id,
                        onboarding_mode, app_id, connected_at, created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                            CASE WHEN $7 IN ('connected', 'verified') THEN NOW() ELSE NULL END, NOW())
                    ON CONFLICT (phone_number) DO UPDATE SET
                        phone_number_id = COALESCE(EXCLUDED.phone_number_id, whatsapp_accounts.phone_number_id),
                        provider_account_id = COALESCE(EXCLUDED.provider_account_id, whatsapp_accounts.provider_account_id),
                        display_name = COALESCE(EXCLUDED.display_name, whatsapp_accounts.display_name),
                        verification_status = EXCLUDED.verification_status,
                        access_token = COALESCE(EXCLUDED.access_token, whatsapp_accounts.access_token),
                        waba_id = COALESCE(EXCLUDED.waba_id, whatsapp_accounts.waba_id),
                        business_id = COALESCE(EXCLUDED.business_id, whatsapp_accounts.business_id),
                        onboarding_mode = EXCLUDED.onboarding_mode,
                        app_id = COALESCE(EXCLUDED.app_id, whatsapp_accounts.app_id),
                        connected_at = CASE
                            WHEN EXCLUDED.verification_status IN ('connected', 'verified') THEN NOW()
                            ELSE whatsapp_accounts.connected_at
                        END,
                        last_error = NULL
                    WHERE whatsapp_accounts.tenant_id = EXCLUDED.tenant_id
                    RETURNING id, tenant_id, phone_number, phone_number_id, provider_account_id,
                              display_name, verification_status, access_token IS NOT NULL AS has_access_token,
                              waba_id, business_id, onboarding_mode, connected_at, created_at,
                              CASE
                                  WHEN verification_status IN ('connected', 'verified') THEN 'connected'
                                  ELSE 'disconnected'
                              END AS status
                    """,
                    uuid4(), tenant_id, phone_number, phone_number_id, provider_account_id,
                    display_name, verification_status, stored_access_token, waba_id, business_id,
                    onboarding_mode, app_id
                )
            except asyncpg.UniqueViolationError as exc:
                raise ValueError("WhatsApp account is already connected") from exc
            if row is None:
                raise ValueError("WhatsApp phone number is already connected to another tenant")
            return dict(row)
    async def get_whatsapp_account_id(self, phone_number_id: str) -> Optional[UUID]:
        """Get the whatsapp_accounts.id for a phone_number_id."""
        async with self.acquire() as conn:
            return await conn.fetchval(
                "SELECT id FROM whatsapp_accounts WHERE phone_number_id = $1",
                phone_number_id
            )
    async def get_whatsapp_account_for_dispatch(
        self, tenant_id: UUID, whatsapp_account_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """Fetch tenant WhatsApp credentials for outbound dispatch."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, phone_number, phone_number_id, access_token, verification_status
                FROM whatsapp_accounts
                WHERE tenant_id = $1 AND id = $2
                """,
                tenant_id, whatsapp_account_id
            )
            if not row:
                return None
            data = dict(row)
            from backend.security.secret_box import decrypt_optional_secret
            data["access_token"] = decrypt_optional_secret(data.get("access_token"))
            return data
    # ==================== CUSTOMER LISTING ====================
    # ==================== CONVERSATION LISTING ====================
    async def list_conversations(
        self, tenant_id: UUID, cursor: Optional[str] = None, limit: int = 20,
        status: Optional[str] = None, search: Optional[str] = None
    ) -> Dict[str, Any]:
        """Paginated conversation list for a tenant with optional filters."""
        async with self.tenant_context(tenant_id) as conn:
            channel_select = """
                CASE
                    WHEN COALESCE(c.channel_type, cu.source_channel) IN ('awa_chat', 'public_chat', 'browser_chat') THEN 'awa_chat'
                    WHEN COALESCE(c.channel_type, cu.source_channel) IS NULL THEN 'whatsapp'
                    ELSE COALESCE(c.channel_type, cu.source_channel)
                END AS channel_type
            """
            customer_phone_select = "COALESCE(cu.phone_number, 'Browser chat') AS customer_phone"
            query = """
                SELECT c.id, c.customer_id, c.status, c.last_message_at, c.created_at,
                       c.escalation_reason, c.escalated_at, c.resolved_at,
                       c.assigned_to, c.resume_mode, c.channel_account_id, c.channel_session_id,
                       {customer_phone_select}, cu.name AS customer_name, cu.email AS customer_email,
                       {channel_select}, t.ai_mode
                FROM conversations c
                JOIN customers cu ON cu.id = c.customer_id
                JOIN tenants t ON t.id = c.tenant_id
                WHERE c.tenant_id = $1 AND c.deleted_at IS NULL
            """.format(customer_phone_select=customer_phone_select, channel_select=channel_select)
            params = [tenant_id]
            idx = 2
            if status:
                query += f" AND c.status = ${idx}"
                params.append(status)
                idx += 1
            if search:
                query += f" AND (cu.name ILIKE '%' || ${idx} || '%' OR cu.phone_number ILIKE '%' || ${idx} || '%')"
                params.append(search)
                idx += 1
            if cursor:
                ts, r_id = decode_cursor(cursor)
                if ts:
                    if r_id:
                        query += f" AND (COALESCE(c.last_message_at, c.created_at), c.id) < (${idx}::timestamptz, ${idx+1}::uuid)"
                        params.extend([ts, r_id])
                        idx += 2
                    else:
                        query += f" AND COALESCE(c.last_message_at, c.created_at) < ${idx}::timestamptz"
                        params.append(ts)
                        idx += 1
            query += f" ORDER BY COALESCE(c.last_message_at, c.created_at) DESC, c.id DESC LIMIT ${idx}"
            params.append(limit + 1)
            try:
                rows = await conn.fetch(query, *params)
            except asyncpg.UndefinedColumnError:
                query = query.replace(
                    """c.escalation_reason, c.escalated_at, c.resolved_at,
                       c.assigned_to, c.resume_mode, c.channel_account_id,""",
                    """NULL::text AS escalation_reason,
                       NULL::timestamptz AS escalated_at,
                       NULL::timestamptz AS resolved_at,
                       NULL::uuid AS assigned_to,
                       NULL::text AS resume_mode,
                       NULL::uuid AS channel_account_id,""",
                ).replace(
                    "COALESCE(cu.phone_number, 'Browser chat') AS customer_phone",
                    "cu.phone_number AS customer_phone",
                ).replace(
                    channel_select,
                    "'whatsapp' AS channel_type",
                )
                rows = await conn.fetch(query, *params)
            items = [dict(r) for r in rows[:limit]]
            next_cursor = encode_cursor(items[-1]["last_message_at"] or items[-1]["created_at"], items[-1]["id"]) if len(rows) > limit else None
            return {"items": items, "next_cursor": next_cursor}
    async def list_messages(
        self, tenant_id: UUID, conversation_id: UUID, cursor: Optional[str] = None, limit: int = 50
    ) -> Dict[str, Any]:
        """Paginated messages for a conversation."""
        async with self.tenant_context(tenant_id) as conn:
            channel_field = "COALESCE(channel_type, 'whatsapp') AS channel_type"
            query = """
                SELECT id, direction, message_type, message_text, raw_payload,
                       processing_status, delivery_status, created_at,
                       {channel_field}
                FROM messages
                WHERE tenant_id = $1 AND conversation_id = $2
            """
            params = [tenant_id, conversation_id]
            idx = 3
            if cursor:
                ts, r_id = decode_cursor(cursor)
                if ts:
                    if r_id:
                        query += f" AND (created_at, id) < (${idx}::timestamptz, ${idx+1}::uuid)"
                        params.extend([ts, r_id])
                        idx += 2
                    else:
                        query += f" AND created_at < ${idx}::timestamptz"
                        params.append(ts)
                        idx += 1
            query += f" ORDER BY created_at DESC, id DESC LIMIT ${idx}"
            params.append(limit + 1)
            try:
                rows = await conn.fetch(query.format(channel_field=channel_field), *params)
            except asyncpg.UndefinedColumnError:
                rows = await conn.fetch(query.format(channel_field="'whatsapp' AS channel_type"), *params)
            items = [dict(r) for r in rows[:limit]]
            next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["id"]) if len(rows) > limit else None
            return {"items": items, "next_cursor": next_cursor}
    async def create_knowledge_base_entry(
        self,
        tenant_id: UUID,
        document_title: str,
        chunk_text: str,
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Insert an approved tenant knowledge-base chunk for future RAG responses."""
        import json
        metadata = dict(metadata or {})
        embedding_is_usable = self._is_usable_embedding(embedding)
        if embedding_is_usable:
            vector_str = "[" + ",".join(map(str, embedding)) + "]"
            metadata.setdefault("embedding_status", "ready")
            metadata.setdefault("embedding_model", settings.OPENAI_EMBEDDING_MODEL)
        else:
            vector_str = None
            metadata.setdefault("embedding_status", "lexical_only")
        async with self.tenant_context(tenant_id) as conn:
            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO tenant_knowledge_base
                        (tenant_id, document_title, chunk_text, embedding, metadata)
                    VALUES ($1, $2, $3, $4::vector, $5::jsonb)
                    RETURNING id, tenant_id, document_title, chunk_text, created_at
                    """,
                    tenant_id, document_title, chunk_text, vector_str, json.dumps(metadata),
                )
            except asyncpg.UndefinedColumnError:
                row = await conn.fetchrow(
                    """
                    INSERT INTO tenant_knowledge_base
                        (tenant_id, document_title, chunk_text, embedding)
                    VALUES ($1, $2, $3, $4::vector)
                    RETURNING id, tenant_id, document_title, chunk_text, created_at
                    """,
                    tenant_id, document_title, chunk_text, vector_str,
                )
            return dict(row)
    async def list_knowledge_base_entries(
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
        async with self.tenant_context(tenant_id) as conn:
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
    async def update_knowledge_base_entry(
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
        import json
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
        async with self.tenant_context(tenant_id) as conn:
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
                    tenant_id, entry_id, document_title, chunk_text, vector_str, json.dumps(metadata_patch), replace_embedding,
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
                    tenant_id, entry_id, document_title, chunk_text, vector_str, replace_embedding,
                )
            return dict(row) if row else None
    @staticmethod
    def _is_usable_embedding(embedding: Optional[List[float]]) -> bool:
        if not embedding or len(embedding) != 1536:
            return False
        try:
            norm = math.sqrt(sum(float(value) * float(value) for value in embedding))
        except (TypeError, ValueError):
            return False
        return math.isfinite(norm) and norm > 0.0
    async def delete_knowledge_base_entry(self, tenant_id: UUID, entry_id: UUID) -> bool:
        """Delete a tenant-owned knowledge chunk."""
        async with self.tenant_context(tenant_id) as conn:
            result = await conn.execute(
                """
                DELETE FROM tenant_knowledge_base
                WHERE tenant_id = $1 AND id = $2
                """,
                tenant_id, entry_id,
            )
            return "DELETE 1" in result
    async def delete_knowledge_base_entries_by_source(self, tenant_id: UUID, source: str) -> int:
        """Delete all tenant-owned knowledge base chunks for a given source."""
        async with self.tenant_context(tenant_id) as conn:
            result = await conn.execute(
                """
                DELETE FROM tenant_knowledge_base
                WHERE tenant_id = $1 AND metadata->>'source' = $2
                """,
                tenant_id, source,
            )
            if result.startswith("DELETE "):
                try:
                    return int(result.split()[1])
                except (ValueError, IndexError):
                    pass
            return 0
    async def search_knowledge_base_text(
        self,
        tenant_id: UUID,
        query: str,
        limit: int = 3,
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
        async with self.tenant_context(tenant_id) as conn:
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
                    tenant_id, query, terms, limit,
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
                    tenant_id, terms, limit,
                )
            return [dict(row) for row in rows]
    # ==================== CONFIG VERSIONING ====================
    async def create_config_version(
        self, tenant_id: UUID, config_json: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a new config version (inactive by default)."""
        import json
        from uuid import uuid4
        from backend.schemas.config import normalize_tenant_config
        config_json = normalize_tenant_config(config_json)
        async with self.tenant_context(tenant_id) as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1)::bigint)",
                    f"tenant_config:{tenant_id}",
                )
                max_version = await conn.fetchval(
                    "SELECT COALESCE(MAX(version), 0) FROM tenant_configs WHERE tenant_id = $1",
                    tenant_id
                )
                new_version = max_version + 1
                row = await conn.fetchrow(
                    """
                    INSERT INTO tenant_configs (id, tenant_id, version, config_json, is_active, created_at)
                    VALUES ($1, $2, $3, $4::jsonb, FALSE, NOW())
                    RETURNING id, tenant_id, version, is_active, created_at
                    """,
                    uuid4(), tenant_id, new_version, json.dumps(config_json)
                )
                return dict(row)
    async def activate_config_version(self, tenant_id: UUID, config_id: UUID) -> bool:
        """Deactivate all configs for tenant, activate the specified one."""
        async with self.tenant_context(tenant_id) as conn:
            async with conn.transaction():
                target_exists = await conn.fetchval(
                    """
                    SELECT 1
                    FROM tenant_configs
                    WHERE tenant_id = $1 AND id = $2
                    FOR UPDATE
                    """,
                    tenant_id, config_id,
                )
                if not target_exists:
                    return False
                await conn.execute(
                    """
                    UPDATE tenant_configs
                    SET is_active = FALSE
                    WHERE tenant_id = $1 AND id <> $2 AND is_active = TRUE
                    """,
                    tenant_id, config_id,
                )
                result = await conn.execute(
                    "UPDATE tenant_configs SET is_active = TRUE WHERE tenant_id = $1 AND id = $2",
                    tenant_id, config_id
                )
                config_ai_mode = await conn.fetchval(
                    """
                    SELECT config_json->>'ai_mode'
                    FROM tenant_configs
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id, config_id,
                )
                if is_known_ai_mode_input(config_ai_mode):
                    await conn.execute(
                        "UPDATE tenants SET ai_mode = $1, updated_at = NOW() WHERE id = $2",
                        normalize_ai_mode(config_ai_mode), tenant_id,
                    )
                return "UPDATE 1" in result
    async def get_config_version(self, tenant_id: UUID, config_id: UUID) -> Optional[Dict[str, Any]]:
        """Fetch a specific tenant config version by ID."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, version, config_json, is_active, created_at
                FROM tenant_configs
                WHERE tenant_id = $1 AND id = $2
                """,
                tenant_id,
                config_id,
            )
            return dict(row) if row else None
    # ==================== FEATURE FLAGS ====================
    async def list_feature_flags(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        """List all feature flags for a tenant."""
        async with self.tenant_context(tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT id, feature_key, is_enabled, metadata, created_at, updated_at
                FROM tenant_feature_flags WHERE tenant_id = $1
                ORDER BY feature_key
                """,
                tenant_id
            )
            return [dict(r) for r in rows]
    async def upsert_feature_flag(
        self, tenant_id: UUID, feature_key: str, is_enabled: bool,
        metadata: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Toggle a feature flag (upsert)."""
        import json
        from uuid import uuid4
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tenant_feature_flags (id, tenant_id, feature_key, is_enabled, metadata, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5::jsonb, NOW(), NOW())
                ON CONFLICT (tenant_id, feature_key)
                DO UPDATE SET is_enabled = EXCLUDED.is_enabled, metadata = COALESCE(EXCLUDED.metadata, tenant_feature_flags.metadata), updated_at = NOW()
                RETURNING id, feature_key, is_enabled, metadata, updated_at
                """,
                uuid4(), tenant_id, feature_key, is_enabled,
                json.dumps(metadata) if metadata is not None else None
            )
            return dict(row)
    # ==================== COST DAILY ====================
    async def upsert_cost_daily(
        self, tenant_id: UUID, cost_date: date | str,
        ai_token_cost: float = 0.0, compute_cost: float = 0.0, message_count: int = 0,
        ai_tokens_used: int = 0, tool_action_count: int = 0, total_credits_used: float = 0.0
    ) -> bool:
        """Upsert daily cost record. Adds to existing values."""
        async with self.tenant_context(tenant_id) as conn:
            try:
                result = await conn.execute(
                    """
                    INSERT INTO tenant_cost_daily (
                        tenant_id, cost_date, ai_token_cost, compute_cost, message_count,
                        ai_tokens_used, tool_action_count, total_credits_used
                    )
                    VALUES ($1, $2::date, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (tenant_id, cost_date)
                    DO UPDATE SET
                        ai_token_cost = tenant_cost_daily.ai_token_cost + EXCLUDED.ai_token_cost,
                        compute_cost = tenant_cost_daily.compute_cost + EXCLUDED.compute_cost,
                        message_count = tenant_cost_daily.message_count + EXCLUDED.message_count,
                        ai_tokens_used = tenant_cost_daily.ai_tokens_used + EXCLUDED.ai_tokens_used,
                        tool_action_count = tenant_cost_daily.tool_action_count + EXCLUDED.tool_action_count,
                        total_credits_used = tenant_cost_daily.total_credits_used + EXCLUDED.total_credits_used
                    """,
                    tenant_id, cost_date, ai_token_cost, compute_cost, message_count,
                    ai_tokens_used, tool_action_count, total_credits_used
                )
            except asyncpg.UndefinedColumnError:
                result = await conn.execute(
                    """
                    INSERT INTO tenant_cost_daily (tenant_id, cost_date, ai_token_cost, compute_cost, message_count)
                    VALUES ($1, $2::date, $3, $4, $5)
                    ON CONFLICT (tenant_id, cost_date)
                    DO UPDATE SET
                        ai_token_cost = tenant_cost_daily.ai_token_cost + EXCLUDED.ai_token_cost,
                        compute_cost = tenant_cost_daily.compute_cost + EXCLUDED.compute_cost,
                        message_count = tenant_cost_daily.message_count + EXCLUDED.message_count
                    """,
                    tenant_id, cost_date, ai_token_cost, compute_cost, message_count
                )
            return "INSERT" in result or "UPDATE" in result
    def _coerce_date(self, value: date | datetime | str) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            return date.fromisoformat(value)
        raise TypeError("date value must be a date, datetime, or ISO date string")
    async def get_cost_daily(
        self, tenant_id: UUID, date_from: date | datetime | str, date_to: date | datetime | str
    ) -> List[Dict[str, Any]]:
        """Get daily cost records for a date range."""
        start_date = self._coerce_date(date_from)
        end_date = self._coerce_date(date_to)
        async with self.tenant_context(tenant_id) as conn:
            try:
                rows = await conn.fetch(
                    """
                    SELECT tenant_id, cost_date, ai_token_cost, compute_cost, message_count,
                           ai_tokens_used, tool_action_count, total_credits_used
                    FROM tenant_cost_daily
                    WHERE tenant_id = $1 AND cost_date >= $2::date AND cost_date <= $3::date
                    ORDER BY cost_date
                    """,
                    tenant_id, start_date, end_date
                )
            except asyncpg.UndefinedColumnError:
                rows = await conn.fetch(
                    """
                    SELECT tenant_id, cost_date, ai_token_cost, compute_cost, message_count
                    FROM tenant_cost_daily
                    WHERE tenant_id = $1 AND cost_date >= $2::date AND cost_date <= $3::date
                    ORDER BY cost_date
                    """,
                    tenant_id, start_date, end_date
                )
            return [dict(r) for r in rows]
    async def get_tenant_usage_summary(self, tenant_id: UUID) -> Dict[str, Any]:
        """Aggregate tenant usage for today and the current calendar month."""
        async with self.tenant_context(tenant_id) as conn:
            try:
                row = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(SUM(message_count) FILTER (WHERE cost_date = CURRENT_DATE), 0) AS today_message_count,
                        COALESCE(SUM(message_count), 0) AS month_message_count,
                        COALESCE(SUM(ai_tokens_used), 0) AS month_ai_tokens_used,
                        COALESCE(SUM(total_credits_used), 0) AS month_credits_used,
                        COALESCE(SUM(tool_action_count), 0) AS month_tool_action_count,
                        COALESCE(SUM(ai_token_cost + compute_cost), 0) AS month_cost
                    FROM tenant_cost_daily
                    WHERE tenant_id = $1
                      AND cost_date >= date_trunc('month', CURRENT_DATE)::date
                    """,
                    tenant_id
                )
            except asyncpg.UndefinedColumnError:
                row = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(SUM(message_count) FILTER (WHERE cost_date = CURRENT_DATE), 0) AS today_message_count,
                        COALESCE(SUM(message_count), 0) AS month_message_count,
                        0 AS month_ai_tokens_used,
                        COALESCE(SUM(ai_token_cost + compute_cost), 0) AS month_credits_used,
                        0 AS month_tool_action_count,
                        COALESCE(SUM(ai_token_cost + compute_cost), 0) AS month_cost
                    FROM tenant_cost_daily
                    WHERE tenant_id = $1
                      AND cost_date >= date_trunc('month', CURRENT_DATE)::date
                    """,
                    tenant_id
                )
            return dict(row) if row else {
                "today_message_count": 0,
                "month_message_count": 0,
                "month_ai_tokens_used": 0,
                "month_credits_used": 0,
                "month_tool_action_count": 0,
                "month_cost": 0,
            }
    async def get_tenant_daily_usage(self, tenant_id: UUID) -> float:
        """Get today's total AI cost for a tenant (for quota checking)."""
        from datetime import date
        async with self.tenant_context(tenant_id) as conn:
            cost = await conn.fetchval(
                """
                SELECT COALESCE(ai_token_cost, 0) FROM tenant_cost_daily
                WHERE tenant_id = $1 AND cost_date = $2
                """,
                tenant_id, date.today()
            )
            return float(cost) if cost else 0.0
    # ==================== AUDIT LOGS ====================
    async def write_audit_log(
        self, tenant_id: UUID, actor_id: Optional[UUID],
        action: str, resource_type: str, resource_id: Optional[UUID] = None,
        details: Optional[Dict] = None, ip_address: Optional[str] = None
    ) -> Dict[str, Any]:
        """Write an audit log entry. Only for sensitive operations."""
        import json
        from uuid import uuid4
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO audit_logs (id, tenant_id, actor_id, action, resource_type,
                                        resource_id, details, ip_address, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, NOW())
                RETURNING id, action, resource_type, created_at
                """,
                uuid4(), tenant_id, actor_id, action, resource_type,
                resource_id, json.dumps(details, default=str) if details else '{}', ip_address
            )
            return dict(row)
    async def list_audit_logs(
        self, tenant_id: UUID, cursor: Optional[str] = None, limit: int = 50,
        action_type: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """Paginated audit logs for a tenant with optional filtering. Joins users and tenants for clear UI."""
        async with self.tenant_context(tenant_id) as conn:
            query = """
                SELECT a.id, a.actor_id, a.action, a.resource_type, a.resource_id, a.details, a.ip_address, a.created_at,
                       t.name AS tenant_name, u.email AS actor_email
                FROM audit_logs a
                LEFT JOIN tenants t ON t.id = a.tenant_id
                LEFT JOIN users u ON u.id = a.actor_id
                WHERE a.tenant_id = $1
            """
            params = [tenant_id]
            idx = 2
            if action_type:
                query += f" AND a.action = ${idx}"
                params.append(action_type)
                idx += 1
            if date_from:
                query += f" AND a.created_at >= ${idx}::timestamptz"
                params.append(date_from)
                idx += 1
            if date_to:
                # Add 1 day to make date_to inclusive if just given YYYY-MM-DD
                query += f" AND a.created_at < (${idx}::date + interval '1 day')"
                params.append(date_to)
                idx += 1
            if cursor:
                query += f" AND a.created_at < ${idx}::timestamptz"
                params.append(cursor)
                idx += 1
            query += f" ORDER BY a.created_at DESC LIMIT ${idx}"
            params.append(limit + 1)
            rows = await conn.fetch(query, *params)
            items = [dict(r) for r in rows[:limit]]
            next_cursor = str(items[-1]["created_at"].isoformat()) if len(rows) > limit else None
            return {"items": items, "next_cursor": next_cursor}

    async def list_platform_audit_logs(
        self, cursor: Optional[str] = None, limit: int = 50,
        action_type: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
        tenant_id_filter: Optional[UUID] = None
    ) -> Dict[str, Any]:
        """Paginated global audit logs for superadmin with optional filtering by tenant, action, and dates."""
        async with self.acquire() as conn:
            query = """
                SELECT a.id, a.tenant_id, a.actor_id, a.action, a.resource_type, a.resource_id, a.details, a.ip_address, a.created_at,
                       t.name AS tenant_name, u.email AS actor_email
                FROM audit_logs a
                LEFT JOIN tenants t ON t.id = a.tenant_id
                LEFT JOIN users u ON u.id = a.actor_id
                WHERE 1 = 1
            """
            params = []
            idx = 1
            if tenant_id_filter:
                query += f" AND a.tenant_id = ${idx}"
                params.append(tenant_id_filter)
                idx += 1
            if action_type:
                query += f" AND a.action = ${idx}"
                params.append(action_type)
                idx += 1
            if date_from:
                query += f" AND a.created_at >= ${idx}::timestamptz"
                params.append(date_from)
                idx += 1
            if date_to:
                query += f" AND a.created_at < (${idx}::date + interval '1 day')"
                params.append(date_to)
                idx += 1
            if cursor:
                query += f" AND a.created_at < ${idx}::timestamptz"
                params.append(cursor)
                idx += 1
            query += f" ORDER BY a.created_at DESC LIMIT ${idx}"
            params.append(limit + 1)
            rows = await conn.fetch(query, *params)
            items = [dict(r) for r in rows[:limit]]
            next_cursor = str(items[-1]["created_at"].isoformat()) if len(rows) > limit else None
            return {"items": items, "next_cursor": next_cursor}

    # ==================== PROMPT REGISTRY ====================
    async def get_prompt(self, tenant_id: UUID, prompt_name: str) -> Optional[str]:
        """
        Fetch active prompt for tenant. Falls back to platform default (tenant_id=NULL).
        """
        query = """
        SELECT prompt_text FROM prompt_registry
        WHERE (tenant_id = $1 OR tenant_id IS NULL)
          AND prompt_name = $2
          AND is_active = true
        ORDER BY tenant_id NULLS LAST, version DESC
        LIMIT 1;
        """
        async with self.tenant_context(tenant_id) as conn:
            try:
                row = await conn.fetchrow(query, tenant_id, prompt_name)
                return row["prompt_text"] if row else None
            except Exception as e:
                logger.error(f"Failed to fetch prompt {prompt_name}: {e}")
                return None
    async def set_prompt(self, tenant_id: Optional[UUID], prompt_name: str, prompt_text: str) -> dict:
        """
        Create a new version of a prompt and activate it.
        """
        query_version = """
        SELECT COALESCE(MAX(version), 0) + 1 FROM prompt_registry
        WHERE tenant_id IS NOT DISTINCT FROM $1 AND prompt_name = $2;
        """
        query_lock = "SELECT pg_advisory_xact_lock(hashtext($1)::bigint)"
        query_deactivate = """
        UPDATE prompt_registry
        SET is_active = false, updated_at = NOW()
        WHERE tenant_id IS NOT DISTINCT FROM $1 AND prompt_name = $2 AND is_active = true;
        """
        query_insert = """
        INSERT INTO prompt_registry (tenant_id, prompt_name, prompt_text, version, is_active)
        VALUES ($1, $2, $3, $4, true)
        RETURNING id, version;
        """
        async with self.tenant_context(tenant_id) as conn:
            try:
                async with conn.transaction():
                    await conn.execute(query_lock, f"prompt:{tenant_id or 'global'}:{prompt_name}")
                    version = await conn.fetchval(query_version, tenant_id, prompt_name)
                    await conn.execute(query_deactivate, tenant_id, prompt_name)
                    row = await conn.fetchrow(query_insert, tenant_id, prompt_name, prompt_text, version)
                    return dict(row)
            except Exception as e:
                logger.error(f"Failed to set prompt {prompt_name}: {e}")
                raise
    async def get_all_prompts(self) -> List[Dict[str, Any]]:
        """Fetch all active global system prompts for the Super Admin Monaco Editor."""
        query = """
        SELECT DISTINCT ON (prompt_name)
               prompt_name as key, prompt_text as body, version,
               updated_at, 'system' as updated_by, 'Global instruction' as description
        FROM prompt_registry
        WHERE is_active = true AND tenant_id IS NULL
        ORDER BY prompt_name, version DESC, updated_at DESC, created_at DESC, id DESC;
        """
        async with self.acquire() as conn:
            try:
                rows = await conn.fetch(query)
                prompts: Dict[str, Dict[str, Any]] = {}
                for row in rows:
                    prompt = dict(row)
                    current = prompts.get(prompt["key"])
                    if not current:
                        prompts[prompt["key"]] = prompt
                        continue
                    prompt_version = int(prompt.get("version") or 0)
                    current_version = int(current.get("version") or 0)
                    prompt_updated_at = prompt.get("updated_at") or datetime.min.replace(tzinfo=timezone.utc)
                    current_updated_at = current.get("updated_at") or datetime.min.replace(tzinfo=timezone.utc)
                    if (prompt_version, prompt_updated_at) > (current_version, current_updated_at):
                        prompts[prompt["key"]] = prompt
                return list(prompts.values())
            except Exception as e:
                logger.error(f"Failed to fetch prompt registry: {e}")
                return []
    # ==================== SUPER ADMIN ====================
    async def get_all_tenants(self) -> List[Dict[str, Any]]:
        """Fetch all tenants for Super Admin Dashboard."""
        async with self.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name, slug, plan_code, status, created_at FROM tenants WHERE deleted_at IS NULL ORDER BY created_at DESC"
            )
            return [dict(r) for r in rows]
    async def get_tool_catalog(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        """Fetch workflow tool catalog rows from the database."""
        import json
        async with self.acquire() as conn:
            try:
                rows = await conn.fetch(
                    """
                    SELECT key, label, category, description, icon_key, route_path,
                           actions, sort_order, is_active, created_at, updated_at
                    FROM tool_catalog
                    WHERE ($1::boolean = TRUE OR is_active = TRUE)
                    ORDER BY sort_order ASC, label ASC
                    """,
                    include_inactive,
                )
            except asyncpg.UndefinedTableError:
                logger.warning("tool_catalog table is unavailable; using built-in tool definitions")
                return []
        results = []
        for row in rows:
            item = dict(row)
            actions = item.get("actions")
            if isinstance(actions, str):
                try:
                    item["actions"] = json.loads(actions)
                except json.JSONDecodeError:
                    item["actions"] = []
            elif actions is None:
                item["actions"] = []
            results.append(item)
        return results
    async def get_all_business_types(self) -> List[Dict[str, Any]]:
        """Fetch all business types."""
        async with self.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, code, label, enabled_modules, note, created_at, updated_at FROM business_types ORDER BY label ASC"
            )
            results = []
            for r in rows:
                d = dict(r)
                if isinstance(d["enabled_modules"], str):
                    import json
                    d["enabled_modules"] = json.loads(d["enabled_modules"])
                results.append(d)
            return results
    async def get_business_type_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        """Fetch a single business type by code."""
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, code, label, enabled_modules, note, created_at, updated_at FROM business_types WHERE code = $1",
                code
            )
            if row:
                d = dict(row)
                if isinstance(d["enabled_modules"], str):
                    import json
                    d["enabled_modules"] = json.loads(d["enabled_modules"])
                return d
            return None
    async def create_business_type(self, code: str, label: str, enabled_modules: List[str], note: Optional[str] = None) -> Dict[str, Any]:
        """Create a new business type."""
        import json
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO business_types (code, label, enabled_modules, note, created_at, updated_at)
                VALUES ($1, $2, $3::jsonb, $4, NOW(), NOW())
                RETURNING id, code, label, enabled_modules, note, created_at, updated_at
                """,
                code, label, json.dumps(enabled_modules), note
            )
            d = dict(row)
            if isinstance(d["enabled_modules"], str):
                d["enabled_modules"] = json.loads(d["enabled_modules"])
            return d
    async def update_business_type(self, code: str, label: Optional[str] = None, enabled_modules: Optional[List[str]] = None, note: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Update a business type by code."""
        import json
        async with self.acquire() as conn:
            fields = []
            params = []
            idx = 1
            params.append(code)
            if label is not None:
                idx += 1
                fields.append(f"label = ${idx}")
                params.append(label)
            if enabled_modules is not None:
                idx += 1
                fields.append(f"enabled_modules = ${idx}::jsonb")
                params.append(json.dumps(enabled_modules))
            if note is not None:
                idx += 1
                fields.append(f"note = ${idx}")
                params.append(note)
            if not fields:
                return await self.get_business_type_by_code(code)
            query = f"""
                UPDATE business_types
                SET {", ".join(fields)}, updated_at = NOW()
                WHERE code = $1
                RETURNING id, code, label, enabled_modules, note, created_at, updated_at
            """
            row = await conn.fetchrow(query, *params)
            if row:
                d = dict(row)
                if isinstance(d["enabled_modules"], str):
                    d["enabled_modules"] = json.loads(d["enabled_modules"])
                return d
            return None
    async def delete_business_type(self, code: str) -> bool:
        """Delete a business type by code."""
        async with self.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM business_types WHERE code = $1",
                code
            )
            return result != "DELETE 0"
    # ==================== BUSINESS BEHAVIOR PRESETS ====================
    def _normalize_business_behavior_preset_record(self, row: Any) -> Optional[Dict[str, Any]]:
        """Return a safe behavior preset DTO without internal DB-only fields."""
        if not row:
            return None
        import json
        from backend.schemas.behavior_profile import BehaviorProfileDefinition
        data = dict(row)
        profile_json = data.get("profile_json") or {}
        if isinstance(profile_json, str):
            profile_json = json.loads(profile_json)
        profile = BehaviorProfileDefinition.model_validate(profile_json)
        tenant_editable_fields = data.get("tenant_editable_fields") or []
        if isinstance(tenant_editable_fields, str):
            tenant_editable_fields = json.loads(tenant_editable_fields)
        return {
            "business_type_key": data["business_type_key"],
            "version": data["version"],
            "profile": profile.model_dump(mode="json"),
            "tenant_editable_fields": list(profile.tenant_editable_fields),
            "status": data["status"],
            "is_active": data["is_active"],
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
            "activated_at": data.get("activated_at"),
            "deactivated_at": data.get("deactivated_at"),
            "change_note": data.get("change_note"),
        }
    async def list_business_behavior_presets(self, business_type_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """List versioned business behavior presets, optionally filtered by business type."""
        async with self.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT business_type_key, version, profile_json, tenant_editable_fields,
                       status, is_active, created_at, updated_at, activated_at,
                       deactivated_at, change_note
                FROM business_behavior_presets
                WHERE ($1::text IS NULL OR business_type_key = $1)
                ORDER BY business_type_key ASC, version DESC
                """,
                business_type_key,
            )
            return [self._normalize_business_behavior_preset_record(row) for row in rows]
    async def list_business_behavior_presets_for_type(self, business_type_key: str) -> List[Dict[str, Any]]:
        """List all behavior preset versions for one business type."""
        return await self.list_business_behavior_presets(business_type_key=business_type_key)
    async def get_business_behavior_preset(
        self,
        business_type_key: str,
        version: Optional[int] = None,
        active_only: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Fetch one behavior preset by type/version or active version."""
        if active_only:
            version = None
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT business_type_key, version, profile_json, tenant_editable_fields,
                       status, is_active, created_at, updated_at, activated_at,
                       deactivated_at, change_note
                FROM business_behavior_presets
                WHERE business_type_key = $1
                  AND ($2::int IS NULL OR version = $2)
                  AND ($3::boolean = FALSE OR is_active = TRUE)
                ORDER BY version DESC
                LIMIT 1
                """,
                business_type_key,
                version,
                active_only,
            )
            return self._normalize_business_behavior_preset_record(row)
    async def get_active_business_behavior_preset(self, business_type_key: str) -> Optional[Dict[str, Any]]:
        """Fetch the active behavior preset for a business type."""
        return await self.get_business_behavior_preset(business_type_key, active_only=True)
    async def create_business_behavior_preset_version(
        self,
        *,
        business_type_key: str,
        profile_json: Dict[str, Any],
        version: Optional[int] = None,
        status: str = "draft",
        actor: Optional[UUID] = None,
        change_note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create an inactive behavior preset version without mutating old versions."""
        import json
        from backend.schemas.behavior_profile import BehaviorProfileDefinition
        profile = BehaviorProfileDefinition.model_validate(profile_json)
        tenant_editable_fields = list(profile.tenant_editable_fields)
        async with self.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1)::bigint)",
                    f"business_behavior_preset:{business_type_key}",
                )
                business_type_exists = await conn.fetchval(
                    "SELECT 1 FROM business_types WHERE code = $1",
                    business_type_key,
                )
                if not business_type_exists:
                    raise ValueError(f"Unknown business_type_key: {business_type_key}")
                next_version = version
                if next_version is None:
                    current_max = await conn.fetchval(
                        "SELECT COALESCE(MAX(version), 0) FROM business_behavior_presets WHERE business_type_key = $1",
                        business_type_key,
                    )
                    next_version = int(current_max or 0) + 1
                row = await conn.fetchrow(
                    """
                    INSERT INTO business_behavior_presets (
                        business_type_key, version, profile_json, tenant_editable_fields,
                        status, is_active, created_by, change_note
                    )
                    VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, FALSE, $6, $7)
                    RETURNING business_type_key, version, profile_json, tenant_editable_fields,
                              status, is_active, created_at, updated_at, activated_at,
                              deactivated_at, change_note
                    """,
                    business_type_key,
                    next_version,
                    json.dumps(profile.model_dump(mode="json")),
                    json.dumps(tenant_editable_fields),
                    status,
                    actor,
                    change_note,
                )
                return self._normalize_business_behavior_preset_record(row) or {}
    async def activate_business_behavior_preset_version(
        self,
        business_type_key: str,
        version: int,
        actor: Optional[UUID] = None,
        change_note: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Transactionally activate one preset version and deactivate the previous active version."""
        async with self.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1)::bigint)",
                    f"business_behavior_preset:{business_type_key}",
                )
                target = await conn.fetchrow(
                    """
                    SELECT id, status
                    FROM business_behavior_presets
                    WHERE business_type_key = $1 AND version = $2
                    FOR UPDATE
                    """,
                    business_type_key,
                    version,
                )
                if not target:
                    return None
                if target["status"] == "deprecated":
                    raise ValueError("Deprecated behavior preset versions cannot be activated")
                previous_active_version = await conn.fetchval(
                    """
                    SELECT version
                    FROM business_behavior_presets
                    WHERE business_type_key = $1 AND is_active = TRUE
                    FOR UPDATE
                    """,
                    business_type_key,
                )
                await conn.execute(
                    """
                    UPDATE business_behavior_presets
                    SET is_active = FALSE,
                        status = 'inactive',
                        deactivated_at = NOW(),
                        updated_at = NOW()
                    WHERE business_type_key = $1
                      AND is_active = TRUE
                      AND version <> $2
                    """,
                    business_type_key,
                    version,
                )
                row = await conn.fetchrow(
                    """
                    UPDATE business_behavior_presets
                    SET is_active = TRUE,
                        status = 'active',
                        activated_at = NOW(),
                        deactivated_at = NULL,
                        updated_at = NOW(),
                        change_note = COALESCE($3, change_note)
                    WHERE business_type_key = $1 AND version = $2
                    RETURNING business_type_key, version, profile_json, tenant_editable_fields,
                              status, is_active, created_at, updated_at, activated_at,
                              deactivated_at, change_note
                    """,
                    business_type_key,
                    version,
                    change_note,
                )
                result = self._normalize_business_behavior_preset_record(row)
                if result is not None:
                    result["previous_active_version"] = previous_active_version
                    result["new_active_version"] = version
                return result
    async def deactivate_business_behavior_preset_version(
        self,
        business_type_key: str,
        version: int,
        actor: Optional[UUID] = None,
        status: str = "inactive",
        change_note: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Deactivate or deprecate one preset version without deleting it."""
        async with self.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1)::bigint)",
                    f"business_behavior_preset:{business_type_key}",
                )
                row = await conn.fetchrow(
                    """
                    UPDATE business_behavior_presets
                    SET is_active = FALSE,
                        status = $3,
                        deactivated_at = NOW(),
                        updated_at = NOW(),
                        change_note = COALESCE($4, change_note)
                    WHERE business_type_key = $1 AND version = $2
                    RETURNING business_type_key, version, profile_json, tenant_editable_fields,
                              status, is_active, created_at, updated_at, activated_at,
                              deactivated_at, change_note
                    """,
                    business_type_key,
                    version,
                    status,
                    change_note,
                )
                return self._normalize_business_behavior_preset_record(row)
    # ==================== FEATURE FLAGS ====================
    async def list_feature_flags(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        """Get all feature flags for a tenant."""
        async with self.tenant_context(tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT feature_key as key, is_enabled as enabled, updated_at,
                       'Tenant configuration override' as description
                FROM tenant_feature_flags
                WHERE tenant_id = $1
                """,
                tenant_id
            )
            return [dict(r) for r in rows]
    async def upsert_feature_flag(self, tenant_id: UUID, feature_key: str, is_enabled: bool, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Toggle or create a feature flag for a tenant."""
        import json
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO tenant_feature_flags (tenant_id, feature_key, is_enabled, metadata, updated_at)
                VALUES ($1, $2, $3, $4::jsonb, NOW())
                ON CONFLICT (tenant_id, feature_key)
                DO UPDATE SET is_enabled = EXCLUDED.is_enabled, metadata = COALESCE(EXCLUDED.metadata, tenant_feature_flags.metadata), updated_at = NOW()
                RETURNING feature_key as key, is_enabled as enabled, updated_at
                """,
                tenant_id, feature_key, is_enabled, json.dumps(metadata) if metadata is not None else None
            )
            return dict(row)
    # ==================== LEADS ====================
    async def list_leads(
        self, tenant_id: UUID, cursor: Optional[str] = None, limit: int = 20,
        status: Optional[str] = None, search: Optional[str] = None
    ) -> Dict[str, Any]:
        """Paginated leads for a tenant with optional filtering by status."""
        async with self.tenant_context(tenant_id) as conn:
            query = """
                SELECT l.id, l.tenant_id, l.customer_id, l.conversation_id, l.source_channel,
                       l.name, l.phone, l.email, l.interest, l.status, l.metadata,
                       l.created_at, l.updated_at,
                       cu.name AS customer_name, cu.phone_number AS customer_phone
                FROM leads l
                LEFT JOIN customers cu ON cu.id = l.customer_id AND cu.tenant_id = l.tenant_id
                WHERE l.tenant_id = $1
            """
            params = [tenant_id]
            idx = 2
            if status:
                query += f" AND l.status = ${idx}"
                params.append(status)
                idx += 1
            if search:
                query += f" AND (l.name ILIKE ${idx} OR l.phone ILIKE ${idx} OR l.email ILIKE ${idx})"
                params.append(f"%{search}%")
                idx += 1
            if cursor:
                ts, r_id = decode_cursor(cursor)
                if ts:
                    if r_id:
                        query += f" AND (l.created_at, l.id) < (${idx}::timestamptz, ${idx+1}::uuid)"
                        params.extend([ts, r_id])
                        idx += 2
                    else:
                        query += f" AND l.created_at < ${idx}::timestamptz"
                        params.append(ts)
                        idx += 1
            query += f" ORDER BY l.created_at DESC, l.id DESC LIMIT ${idx}"
            params.append(limit + 1)
            rows = await conn.fetch(query, *params)
            items = [dict(r) for r in rows[:limit]]
            next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["id"]) if len(rows) > limit else None
            return {"items": items, "next_cursor": next_cursor}

    async def get_lead(self, tenant_id: UUID, lead_id: UUID) -> Optional[Dict[str, Any]]:
        """Get a specific lead by ID."""
        async with self.tenant_context(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT l.id, l.tenant_id, l.customer_id, l.conversation_id, l.source_channel,
                       l.name, l.phone, l.email, l.interest, l.status, l.metadata,
                       l.created_at, l.updated_at,
                       cu.name AS customer_name, cu.phone_number AS customer_phone
                FROM leads l
                LEFT JOIN customers cu ON cu.id = l.customer_id AND cu.tenant_id = l.tenant_id
                WHERE l.tenant_id = $1 AND l.id = $2
                """,
                tenant_id, lead_id
            )
            return dict(row) if row else None

    async def update_lead_status(self, tenant_id: UUID, lead_id: UUID, status: str) -> bool:
        """Update a lead status (e.g. New, Contacted, Converted, Lost)."""
        async with self.tenant_context(tenant_id) as conn:
            result = await conn.execute(
                """
                UPDATE leads
                SET status = $1,
                    updated_at = NOW()
                WHERE tenant_id = $2 AND id = $3
                """,
                status, tenant_id, lead_id
            )
            return result == "UPDATE 1"
# Singleton instance
db_client = DatabaseClient(settings.DATABASE_URL)
