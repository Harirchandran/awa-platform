"""
Session Manager
Handles conversation session lifecycle with Cache (Redis) + DB (Postgres) persistence.
"""
from datetime import datetime, timezone
import json
from uuid import UUID, uuid4
from typing import Optional
import redis.asyncio as redis
import asyncio
from backend.core.config import settings
from backend.core.context import ConversationSession, SessionState, TenantConfig
from backend.core.states import ConversationState
from backend.db.client import db_client
from backend.ops.logger import logger
from pydantic import ValidationError
from backend.pipeline.config_binding import ConfigBindingError, resolve_config_binding, session_has_active_work
STARTING_SEQUENCE = 1
CACHE_TTL_SECONDS = 3600  # 1 hour
SESSION_TIMEOUT_SECONDS = 86400  # 24 hours — sessions older than this auto-reset (Phase 9f)
class SessionLoadError(RuntimeError):
    """Typed retriable failure that never exposes stored session contents."""
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


class SessionPersistenceError(RuntimeError):
    """Exception raised when session persistence fails."""
    def __init__(self, reason_code: str):
        super().__init__(f"Session persistence failed: {reason_code}")
        self.reason_code = reason_code


class SessionManager:
    """
    Manages conversation sessions with write-through caching.
    Flow:
    1. Check Redis cache first
    2. Fall back to Postgres
    3. Create new if not found
    4. Always persist to both DB and cache
    """
    def __init__(self, redis_url: str):
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self.db = db_client
    async def get_next_sequence_number(self, tenant_id: UUID, conversation_id: UUID) -> int:
        """
        Atomically increments the sequence number for a conversation.
        Invariant: conversation_sequence_number is always monotonically increasing.
        """
        return await self.db.get_next_sequence_number(tenant_id, conversation_id)
    async def get_or_create_session(
        self,
        tenant_id: UUID,
        conversation_id: UUID
    ) -> ConversationSession:
        """
        Loads session from Cache -> DB -> New.
        Ensures Config Version is pinned on creation.
        """
        cache_key = f"session:{tenant_id}:{conversation_id}"
        # 1. Try Cache
        try:
            cached_data = await self.redis.get(cache_key)
        except Exception as exc:
            logger.warning("Session cache read failed; falling back to database", extra={"extra_data": {"reason_code": "session_cache_read_failed", "error_type": type(exc).__name__}})
            cached_data = None
        if cached_data:
            try:
                logger.debug(f"Session cache HIT for {conversation_id}")
                return ConversationSession.model_validate_json(cached_data)
            except (ValidationError, ValueError, TypeError) as exc:
                logger.warning("Invalid cached session; falling back to database", extra={"extra_data": {"reason_code": "session_cache_invalid", "error_type": type(exc).__name__}})
        logger.debug(f"Session cache MISS for {conversation_id}")
        # 2. Try Database
        try:
            db_session = await self.db.get_conversation_session(tenant_id, conversation_id)
        except Exception as exc:
            logger.error("Session database read failed", extra={"extra_data": {"reason_code": "session_load_failed", "error_type": type(exc).__name__}})
            raise SessionLoadError("session_load_failed") from exc
        if db_session is not None:
            try:
                logger.info(f"Session loaded from DB for {conversation_id}")
                # Parse state_json from DB
                state_json = db_session.get('state_json')
                if isinstance(state_json, str):
                    state_json = json.loads(state_json)
                if state_json == {} and not db_session.get("current_module") and not db_session.get("current_intent"):
                    pinned_version = await self._fetch_active_config_version(tenant_id)
                    session = ConversationSession(
                        id=db_session["id"],
                        tenant_id=db_session["tenant_id"],
                        conversation_id=db_session["conversation_id"],
                        config_version=pinned_version,
                        state=SessionState(workflow_state=ConversationState.NEW),
                        expires_at=db_session.get("expires_at"),
                    )
                    state_envelope = {
                        "config_version": session.config_version,
                        "state": session.state.model_dump(mode='json')
                    }
                    success = await self.db.update_conversation_session(
                        tenant_id=session.tenant_id,
                        conversation_id=session.conversation_id,
                        state_json=state_envelope,
                        config_version=session.config_version,
                        current_module=session.current_module,
                        current_intent=session.current_intent
                    )
                    if not success:
                        raise SessionLoadError("session_create_failed")
                    await self._cache_session(session)
                    return session
                if not isinstance(state_json, dict) or not isinstance(state_json.get("state"), dict):
                    raise ValueError("invalid session state envelope")
                config_version = db_session.get('config_version')
                mirrored_config_version = state_json.get('config_version')
                if (not isinstance(config_version, int) or isinstance(config_version, bool)
                        or config_version < 1 or mirrored_config_version != config_version):
                    raise ValueError("invalid session config binding")
                session = ConversationSession(
                    id=db_session['id'],
                    tenant_id=db_session['tenant_id'],
                    conversation_id=db_session['conversation_id'],
                    config_version=config_version,
                    current_module=db_session.get('current_module'),
                    current_intent=db_session.get('current_intent'),
                    state=SessionState(**state_json.get('state', {})),
                    expires_at=db_session.get('expires_at')
                )
                # Warm the cache
                await self._cache_session(session)
                # ---- PHASE 9f: SESSION TIMEOUT CHECK ----
                age_seconds = (datetime.now(timezone.utc) - session.state.last_updated_at).total_seconds()
                if age_seconds > SESSION_TIMEOUT_SECONDS and session.state.workflow_state != ConversationState.ESCALATED:
                    logger.info(
                        f"Session {conversation_id} expired ({age_seconds:.0f}s old > {SESSION_TIMEOUT_SECONDS}s). "
                        f"Resetting to NEW state."
                    )
                    reset_state = SessionState(workflow_state=ConversationState.NEW)
                    await self._commit_session_change(
                        session=session,
                        candidate_state=reset_state,
                        candidate_module=None,
                        candidate_intent=None,
                        candidate_config_version=session.config_version,
                    )
                logger.info("Session recovered from database", extra={"extra_data": {"reason_code": "session_recovered_from_db"}})
                return session
            except (SessionLoadError, ConfigBindingError, SessionPersistenceError):
                raise
            except Exception as exc:
                logger.error("Persisted session state is invalid", extra={"extra_data": {"reason_code": "session_state_invalid", "error_type": type(exc).__name__}})
                raise SessionLoadError("session_state_invalid") from exc
        # 3. Create New Session
        logger.info(f"Creating new session for conversation {conversation_id}")
        # Fetch active config version to pin
        pinned_version = await self._fetch_active_config_version(tenant_id)
        new_session = ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            config_version=pinned_version,
            state=SessionState(workflow_state=ConversationState.NEW),
            expires_at=None
        )
        # Persist to DB first (write-through)
        try:
            state_envelope = {
                "config_version": new_session.config_version,
                "state": new_session.state.model_dump(mode='json')
            }
            await self.db.create_conversation_session(
                session_id=new_session.id,
                tenant_id=new_session.tenant_id,
                conversation_id=new_session.conversation_id,
                state_json=state_envelope,
                config_version=new_session.config_version,
                current_module=new_session.current_module,
                current_intent=new_session.current_intent
            )
        except Exception as e:
            logger.error("Session creation failed", extra={"extra_data": {"reason_code": "session_create_failed", "error_type": type(e).__name__}})
            raise SessionLoadError("session_create_failed") from e
        # Then cache
        await self._cache_session(new_session)
        return new_session

    async def bind_config_for_turn(
        self,
        session: ConversationSession,
        *,
        active_version: Optional[int] = None,
    ) -> tuple[int, str]:
        """Resolve config authority; the worker commits it after the exact version loads."""
        if active_version is None and not session.state.orientation_config_pending and not session_has_active_work(session):
            try:
                active_version = await self.db.get_active_config_version(session.tenant_id)
            except Exception as exc:
                logger.error("Active config lookup failed", extra={"extra_data": {"reason_code": "active_config_lookup_failed", "error_type": type(exc).__name__}})
                raise ConfigBindingError("active_config_lookup_failed") from exc
        decision = resolve_config_binding(session, active_version=active_version)
        logger.info("Resolved turn config binding", extra={"extra_data": {"reason_code": decision.reason_code, "config_version": decision.version}})
        return decision.version, decision.reason_code

    async def _commit_session_change(
        self,
        session: ConversationSession,
        candidate_state: SessionState,
        candidate_module: Optional[str],
        candidate_intent: Optional[str],
        candidate_config_version: int,
    ) -> None:
        # Step 1 — Preserve authoritative values
        old_state = session.state
        old_module = session.current_module
        old_intent = session.current_intent

        previous_state_last_updated_at = old_state.last_updated_at

        # Step 2 — Deep-copy candidate
        copied_state = candidate_state.model_copy(deep=True)

        # Step 3 — Set candidate timestamp
        committed_at = datetime.now(timezone.utc)
        copied_state.last_updated_at = committed_at

        # Serialize candidate state
        state_json = {
            "config_version": candidate_config_version,
            "state": copied_state.model_dump(mode='json')
        }

        # Step 4 — Call transactional DB method
        try:
            outcome = await self.db.commit_conversation_session_state(
                tenant_id=session.tenant_id,
                conversation_id=session.conversation_id,
                state_json=state_json,
                config_version=candidate_config_version,
                current_module=candidate_module,
                current_intent=candidate_intent,
                expected_last_updated_at=previous_state_last_updated_at,
                candidate_workflow_state=copied_state.workflow_state.value,
                escalation_reason=copied_state.last_escalation_reason,
            )

            # Step 5 — Map rejected outcomes
            if not outcome.committed:
                reason = outcome.reason_code
                if reason == "session_missing":
                    raise SessionPersistenceError("session_missing")
                elif reason == "tenant_mismatch":
                    raise SessionPersistenceError("tenant_mismatch")
                elif reason == "escalation_guard":
                    raise SessionPersistenceError("escalation_guard")
                elif reason == "optimistic_conflict":
                    raise SessionPersistenceError("optimistic_conflict")
                else:
                    raise SessionPersistenceError("session_write_failed")

            # Step 8 — Successful commit behavior
            copied_state.last_updated_at = outcome.committed_at
            session.state = copied_state
            session.current_module = candidate_module
            session.current_intent = candidate_intent
            session.config_version = candidate_config_version

            # Step 9 — Cache after commit
            try:
                await self._cache_session(session)
            except Exception as exc:
                logger.warning(
                    "Session cache write failed after DB commit",
                    extra={
                        "extra_data": {
                            "event": "cache_write_failed",
                            "error_type": type(exc).__name__,
                        }
                    }
                )
                try:
                    await self.invalidate_cache(session.tenant_id, session.conversation_id)
                except Exception:
                    pass

        except SessionPersistenceError:
            try:
                await self.invalidate_cache(session.tenant_id, session.conversation_id)
            except Exception:
                pass
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                await self.invalidate_cache(session.tenant_id, session.conversation_id)
            except Exception:
                pass
            raise SessionPersistenceError("session_write_failed") from exc

    async def commit_config_for_turn(
        self,
        session: ConversationSession,
        *,
        version: int,
        reason_code: str,
    ) -> None:
        """Persist a selected binding only after that exact config loaded successfully."""
        persist_required = session.config_version != version or reason_code == "config_pinned_orientation_first_turn"
        if not persist_required:
            return

        # Deep-copy candidate state
        candidate_state = session.state.model_copy(deep=True)
        if reason_code == "config_pinned_orientation_first_turn":
            candidate_state.orientation_config_pending = False

        await self._commit_session_change(
            session=session,
            candidate_state=candidate_state,
            candidate_module=session.current_module,
            candidate_intent=session.current_intent,
            candidate_config_version=version,
        )

    async def _fetch_active_config_version(self, tenant_id: UUID) -> int:
        """
        Fetches the active config version for a tenant from database.
        Falls back to 1 if not found.
        """
        try:
            version = await self.db.get_active_config_version(tenant_id)
        except Exception as exc:
            logger.error("Active config lookup failed", extra={"extra_data": {"reason_code": "active_config_lookup_failed", "error_type": type(exc).__name__}})
            raise ConfigBindingError("active_config_lookup_failed") from exc
        if version and int(version) > 0:
            logger.debug(f"Active config version for tenant {tenant_id}: {version}")
            return int(version)
        raise ConfigBindingError("active_config_unavailable")

    async def _cache_session(self, session: ConversationSession):
        """
        Caches session in Redis.
        """
        cache_key = f"session:{session.tenant_id}:{session.conversation_id}"
        await self.redis.set(cache_key, session.model_dump_json(), ex=CACHE_TTL_SECONDS)
    async def update_state(self, session: ConversationSession, new_state: SessionState):
        """
        Updates the session state and persists to both DB and cache.
        """
        await self._commit_session_change(
            session=session,
            candidate_state=new_state,
            candidate_module=session.current_module,
            candidate_intent=session.current_intent,
            candidate_config_version=session.config_version,
        )
    async def set_current_module(self, session: ConversationSession, module_name: str):
        """
        Sets the current module being executed.
        """
        await self._commit_session_change(
            session=session,
            candidate_state=session.state,
            candidate_module=module_name,
            candidate_intent=session.current_intent,
            candidate_config_version=session.config_version,
        )

    async def set_current_intent(self, session: ConversationSession, intent: str):
        """
        Sets the classified intent.
        """
        await self._commit_session_change(
            session=session,
            candidate_state=session.state,
            candidate_module=session.current_module,
            candidate_intent=intent,
            candidate_config_version=session.config_version,
        )
    async def invalidate_cache(self, tenant_id: UUID, conversation_id: UUID):
        """
        Invalidates the cached session (forces reload from DB).
        """
        cache_key = f"session:{tenant_id}:{conversation_id}"
        await self.redis.delete(cache_key)
        logger.debug(f"Session cache invalidated: {conversation_id}")
# Singleton instance
session_manager = SessionManager(settings.REDIS_URL)
