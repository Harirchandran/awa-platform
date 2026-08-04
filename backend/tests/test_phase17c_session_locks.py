import pytest
import asyncio
import time
import json
from uuid import uuid4, UUID
from typing import Optional
from unittest.mock import AsyncMock
from backend.core.config import settings
from backend.pipeline.session_lock import (
    acquire_session_lock,
    extend_session_lock,
    release_session_lock,
    SessionLock,
)
from backend.pipeline.worker import process_message
from backend.core.states import ConversationState
from backend.core.context import ConversationSession, SessionState, ActionLedger, TenantConfig
from backend.logic.engine.core import agentic_engine, WorkflowResult

pytestmark = pytest.mark.anyio

@pytest.fixture(autouse=True)
def mock_customer_service_get_customer(monkeypatch):
    async def mock_get_customer(tenant_id, customer_id):
        return {"name": "Test Customer", "phone_number": "+1234567890"}
    monkeypatch.setattr("backend.domains.customers.service.customer_service.get_customer", mock_get_customer)


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.ttls = {}  # key -> expiry monotonic timestamp (ms)
        self.lpush_calls = []

    async def ping(self):
        return True

    async def aclose(self):
        pass

    async def set(self, key, value, px=None, ex=None, nx=False):
        now = time.monotonic() * 1000
        # Clean expired keys
        if key in self.ttls and self.ttls[key] < now:
            del self.store[key]
            del self.ttls[key]

        if nx and key in self.store:
            return None

        self.store[key] = str(value)
        if px:
            self.ttls[key] = now + px
        elif ex:
            self.ttls[key] = now + (ex * 1000)
        return True

    async def get(self, key):
        now = time.monotonic() * 1000
        if key in self.ttls and self.ttls[key] < now:
            del self.store[key]
            del self.ttls[key]
        return self.store.get(key)

    async def delete(self, *keys):
        count = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                if key in self.ttls:
                    del self.ttls[key]
                count += 1
        return count

    async def eval(self, script, numkeys, *keys_and_args):
        key = keys_and_args[0]
        owner_token = keys_and_args[1]
        now = time.monotonic() * 1000

        if key in self.ttls and self.ttls[key] < now:
            del self.store[key]
            del self.ttls[key]

        current_val = self.store.get(key)

        if "del" in script:
            if current_val == owner_token:
                del self.store[key]
                if key in self.ttls:
                    del self.ttls[key]
                return 1
            return 0
        elif "pexpire" in script:
            ttl_ms = int(keys_and_args[2])
            if current_val == owner_token:
                self.ttls[key] = now + ttl_ms
                return 1
            return 0
        raise ValueError(f"Unknown script in FakeRedis: {script}")

    async def incr(self, key):
        val = int(self.store.get(key, 0)) + 1
        self.store[key] = str(val)
        return val

    async def setnx(self, key, value):
        res = await self.set(key, value, nx=True)
        return 1 if res else 0

    async def expire(self, key, seconds):
        now = time.monotonic() * 1000
        if key in self.store:
            self.ttls[key] = now + (seconds * 1000)
            return True
        return False

    async def lpush(self, key, value):
        self.lpush_calls.append((key, value))
        return 1


def make_canonical_message_json(
    event_id: str,
    tenant_id: str,
    conversation_id: str,
    customer_id: str,
    text: str,
) -> str:
    return json.dumps({
        "schema_version": 2,
        "event_type": "incoming_message",
        "event_id": event_id,
        "source_type": "webhook",
        "channel_type": "awa_chat",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "conversation_sequence_number": 1,
        "customer_id": customer_id,
        "config_version": 1,
        "worker_metadata": {
            "worker_version": "1.0.0"
        },
        "message": {
            "message_id": str(uuid4()),
            "provider_message_id": f"pm_{event_id}",
            "direction": "inbound",
            "timestamp": "2026-06-17T08:00:00Z",
            "message_type": "text",
            "text": text
        },
        "processing": {
            "retry_count": 0,
            "first_received_at": "2026-06-17T08:00:00Z"
        },
        "trace": {
            "trace_id": str(uuid4()),
            "created_at": "2026-06-17T08:00:00Z"
        },
        "cost_context": {
            "ai_tokens_used": 0,
            "ai_cost": 0.0,
            "compute_cost": 0.0
        }
    })


# 1. Basic Lock acquire, release and extend (Owner verification)
async def test_lock_basic_acquire_release_extend(monkeypatch):
    redis_client = FakeRedis()
    monkeypatch.setattr(settings, "ENABLE_WORKER_SESSION_LOCKS", True)

    tenant_id = str(uuid4())
    conversation_id = str(uuid4())
    ttl_ms = 5000

    # Acquire lock
    lock = await acquire_session_lock(redis_client, tenant_id, conversation_id, ttl_ms, max_wait_ms=100)
    assert lock.acquired is True
    assert lock.key == f"lock:session:{tenant_id}:{conversation_id}"
    assert lock.owner_token is not None

    # Verify key exists with owner token in Redis
    stored = await redis_client.get(lock.key)
    assert stored == lock.owner_token

    # Extend lock with valid token
    extended = await extend_session_lock(redis_client, lock, ttl_ms=10000)
    assert extended is True

    # Attempt to extend lock with invalid/different owner token
    fake_lock = SessionLock(key=lock.key, owner_token="wrong_token", acquired=True, wait_ms=0, ttl_ms=ttl_ms)
    extended_fake = await extend_session_lock(redis_client, fake_lock, ttl_ms=10000)
    assert extended_fake is False

    # Release lock with wrong owner token
    released_fake = await release_session_lock(redis_client, fake_lock)
    assert released_fake is False
    assert await redis_client.get(lock.key) == lock.owner_token

    # Release lock with correct owner token
    released = await release_session_lock(redis_client, lock)
    assert released is True
    assert await redis_client.get(lock.key) is None

# 2. TTL Recovery
async def test_lock_ttl_recovery(monkeypatch):
    redis_client = FakeRedis()
    monkeypatch.setattr(settings, "ENABLE_WORKER_SESSION_LOCKS", True)

    tenant_id = str(uuid4())
    conversation_id = str(uuid4())

    # Acquire with very short TTL
    lock1 = await acquire_session_lock(redis_client, tenant_id, conversation_id, ttl_ms=20, max_wait_ms=50)
    assert lock1.acquired is True

    # Sleep to let it expire
    await asyncio.sleep(0.03)

    # Acquire again
    lock2 = await acquire_session_lock(redis_client, tenant_id, conversation_id, ttl_ms=5000, max_wait_ms=50)
    assert lock2.acquired is True
    assert lock2.owner_token != lock1.owner_token

# 3. Fail-Closed and Fail-Open Redis unavailability behavior
class BrokenRedis:
    async def set(self, *args, **kwargs):
        raise ConnectionError("Redis is offline")
    async def get(self, *args, **kwargs):
        raise ConnectionError("Redis is offline")
    async def incr(self, *args, **kwargs):
        raise ConnectionError("Redis is offline")

async def test_lock_fail_closed_and_fail_open(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WORKER_SESSION_LOCKS", True)
    broken_redis = BrokenRedis()

    # Fail closed by default
    monkeypatch.setattr(settings, "WORKER_SESSION_LOCK_FAIL_OPEN", False)
    lock_closed = await acquire_session_lock(broken_redis, str(uuid4()), str(uuid4()), 5000, 50)
    assert lock_closed.acquired is False

    # Fail open explicitly
    monkeypatch.setattr(settings, "WORKER_SESSION_LOCK_FAIL_OPEN", True)
    lock_open = await acquire_session_lock(broken_redis, str(uuid4()), str(uuid4()), 5000, 50)
    assert lock_open.acquired is True

async def test_worker_retry_failure_deletes_claimed_idempotency_key(monkeypatch):
    redis_client = FakeRedis()
    monkeypatch.setattr(settings, "ENABLE_WORKER_SESSION_LOCKS", False)

    async def mock_get_redis():
        return redis_client

    async def fail_load_config(*args, **kwargs):
        raise RuntimeError("config load failed")

    retry_mock = AsyncMock()
    monkeypatch.setattr("backend.pipeline.worker.get_redis", mock_get_redis)
    monkeypatch.setattr("backend.pipeline.worker.config_loader.load_config", fail_load_config)
    monkeypatch.setattr("backend.pipeline.worker.handle_retry", retry_mock)

    tenant_id = str(uuid4())
    conversation_id = str(uuid4())
    customer_id = str(uuid4())
    event_id = str(uuid4())
    raw_msg = make_canonical_message_json(event_id, tenant_id, conversation_id, customer_id, "hello")

    result = await process_message(raw_msg, time.time() * 1000, time.monotonic() * 1000)

    assert result is True
    assert f"processed:{tenant_id}:{event_id}" not in redis_client.store
    retry_mock.assert_awaited_once()

# 4. Outermost lock wrap and same-session serial concurrent execution
async def test_concurrency_same_session_serialization(monkeypatch):
    redis_client = FakeRedis()
    monkeypatch.setattr(settings, "ENABLE_WORKER_SESSION_LOCKS", True)
    monkeypatch.setattr(settings, "WORKER_SESSION_LOCK_MAX_WAIT_MS", 2000)
    monkeypatch.setattr(settings, "WORKER_SESSION_LOCK_TTL_MS", 5000)
    monkeypatch.setattr(settings, "WORKER_SESSION_LOCK_RENEW_INTERVAL_MS", 200)

    # Mock dependencies
    async def mock_get_redis():
        return redis_client
    monkeypatch.setattr("backend.pipeline.worker.get_redis", mock_get_redis)

    async def mock_load_config(tenant_id, version=None):
        return TenantConfig(
            tenant_id=UUID(str(tenant_id)),
            version=version or 1,
            config_data={}
        )
    monkeypatch.setattr("backend.core.config_loader.config_loader.load_config", mock_load_config)

    t1_uuid = str(uuid4())
    async def mock_get_tenant(*args, **kwargs):
        return {"id": t1_uuid, "name": "Tenant 1", "plan_code": "concierge", "status": "active", "ai_mode": "concierge"}
    monkeypatch.setattr("backend.db.client.db_client.get_tenant", mock_get_tenant)

    async def mock_save_message(*args, **kwargs):
        return {"created": True}
    monkeypatch.setattr("backend.db.client.db_client.save_message", mock_save_message)

    async def mock_check_sent(*args, **kwargs):
        return False
    monkeypatch.setattr("backend.db.client.db_client.check_outbound_sent", mock_check_sent)

    async def mock_get_session(tenant_id, conversation_id):
        tenant_uuid = UUID(str(tenant_id))
        conv_uuid = UUID(str(conversation_id))
        return ConversationSession(
            id=conv_uuid,
            tenant_id=tenant_uuid,
            conversation_id=conv_uuid,
            config_version=1,
            state=SessionState(
                workflow_state=ConversationState.NEW,
                action_ledger=ActionLedger()
            )
        )
    monkeypatch.setattr("backend.pipeline.session_manager.session_manager.get_or_create_session", mock_get_session)

    async def mock_update_state(*args, **kwargs):
        pass
    monkeypatch.setattr("backend.pipeline.session_manager.session_manager.update_state", mock_update_state)

    async def mock_resolve_ai_mode(*args, **kwargs):
        return "concierge"
    monkeypatch.setattr("backend.billing.usage_guard.usage_guard.resolve_ai_mode", mock_resolve_ai_mode)

    async def mock_record_message_processed(*args, **kwargs):
        pass
    monkeypatch.setattr("backend.billing.usage_guard.usage_guard.record_message_processed", mock_record_message_processed)

    async def mock_publish_event(*args, **kwargs):
        pass
    monkeypatch.setattr("backend.realtime.public_chat_events.publish_public_chat_message_event", mock_publish_event)

    async def mock_catalog_cards(*args, **kwargs):
        return []
    monkeypatch.setattr("backend.pipeline.worker._catalog_cards_for_context", mock_catalog_cards)

    # Track execute_step calls and timestamps
    execution_timeline = []

    async def mock_execute_step(context):
        task_id = context.incoming_message.get("text")
        start_time = time.monotonic()
        execution_timeline.append((task_id, "start", start_time))
        # Sleep to simulate long execution
        await asyncio.sleep(0.3)
        end_time = time.monotonic()
        execution_timeline.append((task_id, "end", end_time))
        return WorkflowResult(
            next_state=None,
            requires_response=True,
            response_text=f"Response to {task_id}"
        )
    monkeypatch.setattr(agentic_engine, "execute_step", mock_execute_step)

    # Enqueue two messages for same tenant & conversation
    c1_uuid = str(uuid4())
    cust1_uuid = str(uuid4())
    e1_uuid = str(uuid4())
    e2_uuid = str(uuid4())

    msg1_raw = make_canonical_message_json(e1_uuid, t1_uuid, c1_uuid, cust1_uuid, "Task 1")
    msg2_raw = make_canonical_message_json(e2_uuid, t1_uuid, c1_uuid, cust1_uuid, "Task 2")

    # Run concurrently
    res1, res2 = await asyncio.gather(
        process_message(msg1_raw, time.time() * 1000, time.monotonic() * 1000),
        process_message(msg2_raw, time.time() * 1000, time.monotonic() * 1000),
        return_exceptions=True
    )

    assert res1 is True
    assert res2 is True

    # Verify execution timeline: Task 2 must not start before Task 1 ends
    timeline = {f"{task_id}_{event}": val for task_id, event, val in execution_timeline}
    assert "Task 1_start" in timeline
    assert "Task 1_end" in timeline
    assert "Task 2_start" in timeline
    assert "Task 2_end" in timeline

    # Strict ordering assertion: Task 2 must not start while Task 1 is running
    assert timeline["Task 2_start"] >= timeline["Task 1_end"]

# 5. Different conversations execute independently in parallel
async def test_concurrency_different_sessions_parallel(monkeypatch):
    redis_client = FakeRedis()
    monkeypatch.setattr(settings, "ENABLE_WORKER_SESSION_LOCKS", True)
    monkeypatch.setattr(settings, "WORKER_SESSION_LOCK_MAX_WAIT_MS", 2000)

    # Mock dependencies
    async def mock_get_redis():
        return redis_client
    monkeypatch.setattr("backend.pipeline.worker.get_redis", mock_get_redis)

    async def mock_load_config(tenant_id, version=None):
        return TenantConfig(
            tenant_id=UUID(str(tenant_id)),
            version=version or 1,
            config_data={}
        )
    monkeypatch.setattr("backend.core.config_loader.config_loader.load_config", mock_load_config)

    t1_uuid = str(uuid4())
    async def mock_get_tenant(*args, **kwargs):
        return {"id": t1_uuid, "name": "Tenant 1", "plan_code": "concierge", "status": "active", "ai_mode": "concierge"}
    monkeypatch.setattr("backend.db.client.db_client.get_tenant", mock_get_tenant)

    async def mock_save_message(*args, **kwargs):
        return {"created": True}
    monkeypatch.setattr("backend.db.client.db_client.save_message", mock_save_message)

    async def mock_check_sent(*args, **kwargs):
        return False
    monkeypatch.setattr("backend.db.client.db_client.check_outbound_sent", mock_check_sent)

    async def mock_get_session(tenant_id, conversation_id):
        tenant_uuid = UUID(str(tenant_id))
        conv_uuid = UUID(str(conversation_id))
        return ConversationSession(
            id=conv_uuid,
            tenant_id=tenant_uuid,
            conversation_id=conv_uuid,
            config_version=1,
            state=SessionState(workflow_state=ConversationState.NEW, action_ledger=ActionLedger())
        )
    monkeypatch.setattr("backend.pipeline.session_manager.session_manager.get_or_create_session", mock_get_session)

    async def mock_update_state(*args, **kwargs):
        pass
    monkeypatch.setattr("backend.pipeline.session_manager.session_manager.update_state", mock_update_state)

    async def mock_resolve_ai_mode(*args, **kwargs):
        return "concierge"
    monkeypatch.setattr("backend.billing.usage_guard.usage_guard.resolve_ai_mode", mock_resolve_ai_mode)

    async def mock_record_message_processed(*args, **kwargs):
        pass
    monkeypatch.setattr("backend.billing.usage_guard.usage_guard.record_message_processed", mock_record_message_processed)

    async def mock_publish_event(*args, **kwargs):
        pass
    monkeypatch.setattr("backend.realtime.public_chat_events.publish_public_chat_message_event", mock_publish_event)

    async def mock_catalog_cards(*args, **kwargs):
        return []
    monkeypatch.setattr("backend.pipeline.worker._catalog_cards_for_context", mock_catalog_cards)

    execution_timeline = []
    async def mock_execute_step(context):
        task_id = context.incoming_message.get("text")
        start_time = time.monotonic()
        execution_timeline.append((task_id, "start", start_time))
        await asyncio.sleep(0.3)
        end_time = time.monotonic()
        execution_timeline.append((task_id, "end", end_time))
        return WorkflowResult(
            next_state=None, requires_response=True, response_text=f"Response to {task_id}"
        )
    monkeypatch.setattr(agentic_engine, "execute_step", mock_execute_step)

    # Enqueue two messages for different conversations: c1 and c2
    c1_uuid = str(uuid4())
    c2_uuid = str(uuid4())
    cust1_uuid = str(uuid4())
    cust2_uuid = str(uuid4())
    e1_uuid = str(uuid4())
    e2_uuid = str(uuid4())

    msg1_raw = make_canonical_message_json(e1_uuid, t1_uuid, c1_uuid, cust1_uuid, "Task 1")
    msg2_raw = make_canonical_message_json(e2_uuid, t1_uuid, c2_uuid, cust2_uuid, "Task 2")

    # Run concurrently
    res1, res2 = await asyncio.gather(
        process_message(msg1_raw, time.time() * 1000, time.monotonic() * 1000),
        process_message(msg2_raw, time.time() * 1000, time.monotonic() * 1000)
    )

    assert res1 is True
    assert res2 is True

    timeline = {f"{task_id}_{event}": val for task_id, event, val in execution_timeline}
    # Parallel execution assertion: Task 2 must start BEFORE Task 1 ends
    assert timeline["Task 2_start"] < timeline["Task 1_end"]

# 6. Regression / safety checks (Mandatory Correction 10)
# A. Escalated conversation suppresses AI under locks
async def test_escalated_conversation_suppresses_ai_under_locks(monkeypatch):
    redis_client = FakeRedis()
    monkeypatch.setattr(settings, "ENABLE_WORKER_SESSION_LOCKS", True)

    async def mock_get_redis():
        return redis_client
    monkeypatch.setattr("backend.pipeline.worker.get_redis", mock_get_redis)

    async def mock_load_config(tenant_id, version=None):
        return TenantConfig(
            tenant_id=UUID(str(tenant_id)),
            version=version or 1,
            config_data={}
        )
    monkeypatch.setattr("backend.core.config_loader.config_loader.load_config", mock_load_config)

    t1_uuid = str(uuid4())
    async def mock_get_tenant(*args, **kwargs):
        return {"id": t1_uuid, "name": "Tenant 1", "plan_code": "concierge", "status": "active", "ai_mode": "concierge"}
    monkeypatch.setattr("backend.db.client.db_client.get_tenant", mock_get_tenant)

    async def mock_save_message(*args, **kwargs):
        return {"created": True}
    monkeypatch.setattr("backend.db.client.db_client.save_message", mock_save_message)

    async def mock_check_sent(*args, **kwargs):
        return False
    monkeypatch.setattr("backend.db.client.db_client.check_outbound_sent", mock_check_sent)

    # Session is ESCALATED
    async def mock_get_session(tenant_id, conversation_id):
        tenant_uuid = UUID(str(tenant_id))
        conv_uuid = UUID(str(conversation_id))
        return ConversationSession(
            id=conv_uuid,
            tenant_id=tenant_uuid,
            conversation_id=conv_uuid,
            config_version=1,
            state=SessionState(
                workflow_state=ConversationState.ESCALATED,
                action_ledger=ActionLedger()
            )
        )
    monkeypatch.setattr("backend.pipeline.session_manager.session_manager.get_or_create_session", mock_get_session)

    async def mock_update_state(*args, **kwargs):
        pass
    monkeypatch.setattr("backend.pipeline.session_manager.session_manager.update_state", mock_update_state)

    async def mock_resolve_ai_mode(*args, **kwargs):
        return "concierge"
    monkeypatch.setattr("backend.billing.usage_guard.usage_guard.resolve_ai_mode", mock_resolve_ai_mode)

    async def mock_record_message_processed(*args, **kwargs):
        pass
    monkeypatch.setattr("backend.billing.usage_guard.usage_guard.record_message_processed", mock_record_message_processed)

    async def mock_catalog_cards(*args, **kwargs):
        return []
    monkeypatch.setattr("backend.pipeline.worker._catalog_cards_for_context", mock_catalog_cards)

    executed_step = False
    async def mock_execute_step(context):
        nonlocal executed_step
        executed_step = True
        return WorkflowResult(
            next_state=ConversationState.ESCALATED, # Kept escalated
            requires_response=True,
            response_text="Will escalate standard"
        )
    monkeypatch.setattr(agentic_engine, "execute_step", mock_execute_step)

    # Dispatched response is tracked
    dispatched = False
    async def mock_dispatch_response(*args, **kwargs):
        nonlocal dispatched
        dispatched = True
    monkeypatch.setattr("backend.pipeline.worker._dispatch_response", mock_dispatch_response)

    c1_uuid = str(uuid4())
    cust1_uuid = str(uuid4())
    e1_uuid = str(uuid4())
    msg_raw = make_canonical_message_json(e1_uuid, t1_uuid, c1_uuid, cust1_uuid, "Hi")

    res = await process_message(msg_raw, time.time() * 1000, time.monotonic() * 1000)
    assert res is True
    # The step executes, but _dispatch_response is suppressed because conversation remains escalated
    assert executed_step is True
    assert dispatched is False

    # Verify that the lock is released
    assert len(redis_client.store) == 0 or f"lock:session:{t1_uuid}:{c1_uuid}" not in redis_client.store
