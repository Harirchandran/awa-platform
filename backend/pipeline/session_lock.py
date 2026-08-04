import uuid
import time
import asyncio
from dataclasses import dataclass
from typing import Optional
from backend.ops.logger import logger
from backend.core.config import settings

@dataclass
class SessionLock:
    key: str
    owner_token: str
    acquired: bool
    wait_ms: int
    ttl_ms: int

LUA_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

LUA_EXTEND_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
else
    return 0
end
"""

async def acquire_session_lock(
    redis_client,
    tenant_id: str,
    conversation_id: str,
    ttl_ms: int,
    max_wait_ms: int
) -> SessionLock:
    """
    Acquire a Redis-backed session lock.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required for session lock")
    if not conversation_id:
        raise ValueError("conversation_id is required for session lock")

    key = f"lock:session:{tenant_id}:{conversation_id}"
    owner_token = uuid.uuid4().hex

    if not settings.ENABLE_WORKER_SESSION_LOCKS:
        return SessionLock(key=key, owner_token=owner_token, acquired=True, wait_ms=0, ttl_ms=ttl_ms)

    start_ns = time.perf_counter_ns()
    wait_ms = 0

    while True:
        try:
            # Set key with PX and NX
            res = await redis_client.set(key, owner_token, px=ttl_ms, nx=True)
            if res:
                elapsed_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000.0)
                return SessionLock(
                    key=key,
                    owner_token=owner_token,
                    acquired=True,
                    wait_ms=elapsed_ms,
                    ttl_ms=ttl_ms
                )
        except Exception as e:
            logger.error(f"Redis error during session lock acquire: {e}")
            if settings.WORKER_SESSION_LOCK_FAIL_OPEN:
                logger.warning("Session lock fail-open allowed by config")
                elapsed_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000.0)
                return SessionLock(
                    key=key,
                    owner_token=owner_token,
                    acquired=True,
                    wait_ms=elapsed_ms,
                    ttl_ms=ttl_ms
                )
            else:
                elapsed_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000.0)
                return SessionLock(
                    key=key,
                    owner_token=owner_token,
                    acquired=False,
                    wait_ms=elapsed_ms,
                    ttl_ms=ttl_ms
                )

        # Calculate remaining wait time
        wait_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000.0)
        remaining = max_wait_ms - wait_ms
        if remaining <= 0:
            logger.warning(f"Lock acquisition timed out for key {key} after {wait_ms}ms")
            try:
                await redis_client.incr("worker:metrics:lock_wait_timeout_count")
            except Exception as e:
                logger.debug(f"Failed to increment lock timeout counter: {e}")
            return SessionLock(
                key=key,
                owner_token=owner_token,
                acquired=False,
                wait_ms=wait_ms,
                ttl_ms=ttl_ms
            )

        # Sleep and retry
        sleep_time = min(50, remaining)
        await asyncio.sleep(sleep_time / 1000.0)

async def extend_session_lock(redis_client, lock: SessionLock, ttl_ms: int) -> bool:
    """
    Extend the session lock TTL if current owner matches.
    """
    if not settings.ENABLE_WORKER_SESSION_LOCKS:
        return True
    if not lock.acquired:
        return False

    try:
        if hasattr(redis_client, "eval"):
            res = await redis_client.eval(LUA_EXTEND_SCRIPT, 1, lock.key, lock.owner_token, ttl_ms)
            return bool(res)
        else:
            # Fallback for FakeRedis in tests
            val = await redis_client.get(lock.key)
            if val == lock.owner_token:
                await redis_client.set(lock.key, lock.owner_token, px=ttl_ms)
                return True
            return False
    except Exception as e:
        logger.error(f"Redis error during session lock extend: {e}")
        return settings.WORKER_SESSION_LOCK_FAIL_OPEN

async def release_session_lock(redis_client, lock: SessionLock) -> bool:
    """
    Release the session lock atomically if owner matches.
    """
    if not settings.ENABLE_WORKER_SESSION_LOCKS:
        return True
    if not lock.acquired:
        return False

    try:
        if hasattr(redis_client, "eval"):
            res = await redis_client.eval(LUA_RELEASE_SCRIPT, 1, lock.key, lock.owner_token)
            return bool(res)
        else:
            # Fallback for FakeRedis in tests
            val = await redis_client.get(lock.key)
            if val == lock.owner_token:
                await redis_client.delete(lock.key)
                return True
            return False
    except Exception as e:
        logger.error(f"Redis error during session lock release: {e}")
        return settings.WORKER_SESSION_LOCK_FAIL_OPEN
