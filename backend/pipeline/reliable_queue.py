"""
Redis-backed reliable list queue helpers.
Messages are claimed with BRPOPLPUSH into a processing list. The worker only
acknowledges a message after it has either completed or has been safely routed
to retry/DLQ. Stale processing entries are moved back to the main queue.
"""
from __future__ import annotations
import time
from typing import Optional
from backend.ops.logger import logger
QUEUE_KEY = "message_queue"
PROCESSING_KEY = "message_queue:processing"
PROCESSING_DEADLINES_KEY = "message_queue:processing_deadlines"
DLQ_KEY = "dlq"
# Set this key in Redis to pause all background workers. Delete it to resume.
WORKERS_PAUSED_KEY = "system:workers:paused"
WORKER_HEARTBEAT_KEY = "worker:main:heartbeat"
VISIBILITY_TIMEOUT_SECONDS = 300
STALE_RECOVERY_BATCH_SIZE = 100
WORKER_HEARTBEAT_TTL_SECONDS = 15
async def enqueue_message(redis_client, raw_msg: str) -> None:
    """Push a raw canonical message JSON payload onto the main queue."""
    await redis_client.lpush(QUEUE_KEY, raw_msg)
async def claim_message(redis_client, timeout: int = 5) -> Optional[str]:
    """
    Atomically move a queued message into the processing list.
    If the worker crashes after claiming, the processing list keeps the payload
    available for stale recovery instead of losing it after a BRPOP.
    """
    raw_msg = await redis_client.brpoplpush(QUEUE_KEY, PROCESSING_KEY, timeout=timeout)
    if raw_msg:
        await redis_client.zadd(
            PROCESSING_DEADLINES_KEY,
            {raw_msg: time.time() + VISIBILITY_TIMEOUT_SECONDS},
        )
    return raw_msg
async def ack_message(redis_client, raw_msg: str) -> None:
    """Remove a processed message from the processing list and deadline index."""
    pipe = redis_client.pipeline()
    pipe.lrem(PROCESSING_KEY, 1, raw_msg)
    pipe.zrem(PROCESSING_DEADLINES_KEY, raw_msg)
    await pipe.execute()
async def recover_stale_messages(
    redis_client,
    *,
    now: Optional[float] = None,
    limit: int = STALE_RECOVERY_BATCH_SIZE,
) -> int:
    """
    Requeue processing messages whose visibility timeout has expired.
    Untracked processing entries are also recovered. Those can happen if a
    process exits after BRPOPLPUSH and before writing the deadline zset.
    """
    now = time.time() if now is None else now
    due = await redis_client.zrangebyscore(
        PROCESSING_DEADLINES_KEY,
        min="-inf",
        max=now,
        start=0,
        num=limit,
    )
    remaining_slots = max(0, limit - len(due))
    untracked: list[str] = []
    if remaining_slots:
        processing = await redis_client.lrange(PROCESSING_KEY, 0, -1)
        if processing:
            tracked = set(await redis_client.zrange(PROCESSING_DEADLINES_KEY, 0, -1))
            untracked = [raw for raw in processing if raw not in tracked][:remaining_slots]
    candidates = list(dict.fromkeys([*due, *untracked]))
    if not candidates:
        return 0

    lua_recover_script = """
    local processing_key = KEYS[1]
    local queue_key = KEYS[2]
    local deadlines_key = KEYS[3]
    local raw_msg = ARGV[1]

    local removed = redis.call('lrem', processing_key, 1, raw_msg)
    if removed > 0 then
        redis.call('lpush', queue_key, raw_msg)
        redis.call('zrem', deadlines_key, raw_msg)
        return 1
    else
        return 0
    end
    """

    recovered = 0
    if hasattr(redis_client, "eval"):
        for raw_msg in candidates:
            res = await redis_client.eval(
                lua_recover_script,
                3,
                PROCESSING_KEY,
                QUEUE_KEY,
                PROCESSING_DEADLINES_KEY,
                raw_msg
            )
            if res == 1:
                recovered += 1
    else:
        # Fallback for unit test FakeRedis that does not support eval
        pipe = redis_client.pipeline()
        for raw_msg in candidates:
            pipe.lrem(PROCESSING_KEY, 1, raw_msg)
            pipe.lpush(QUEUE_KEY, raw_msg)
            pipe.zrem(PROCESSING_DEADLINES_KEY, raw_msg)
        results = await pipe.execute()
        for index in range(0, len(results), 3):
            if results[index]:
                recovered += 1
    if recovered:
        logger.warning(f"Recovered {recovered} stale message(s) from processing queue")
    return recovered