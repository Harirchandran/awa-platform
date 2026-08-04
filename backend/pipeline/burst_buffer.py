"""
Sliding-Window Burst Buffer (Phase 9c)
Combines rapid back-to-back messages from the same customer into a single
workflow invocation, reducing AI token usage and preventing fragmented replies.
Architecture:
  - Webhook pushes each message into a Redis list keyed by tenant+customer
  - A TTL sentinel key tracks the sliding window (3s idle timer)
  - When the sentinel expires, burst_worker.py detects it and flushes
"""
import json
from uuid import UUID, uuid4
from datetime import datetime, timezone
from typing import Optional
import redis.asyncio as redis
from redis.exceptions import ResponseError
from backend.core.config import settings
from backend.ops.logger import logger
from backend.schemas.canonical_message import CanonicalMessage, MessageDirection, MessageType
from backend.pipeline.reliable_queue import enqueue_message
BURST_WINDOW_SECONDS = 3  # Flush after 3s of silence
BURST_LIST_PREFIX = "burst:msgs:"      # burst:msgs:{tenant_id}:{customer_phone}
BURST_SENTINEL_PREFIX = "burst:ttl:"   # burst:ttl:{tenant_id}:{customer_phone}
BURST_ACTIVE_SET = "burst:active"      # Set of all active burst keys
class BurstBuffer:
    """Redis-backed sliding window burst buffer."""
    def __init__(self, redis_url: str):
        self.redis = redis.from_url(redis_url, decode_responses=True)
    async def add_message(
        self,
        tenant_id: UUID,
        customer_phone: str,
        canonical_json: str
    ):
        """
        Add a message to the sliding-window buffer.
        Resets the idle timer on each new message.
        """
        key_suffix = f"{tenant_id}:{customer_phone}"
        list_key = f"{BURST_LIST_PREFIX}{key_suffix}"
        sentinel_key = f"{BURST_SENTINEL_PREFIX}{key_suffix}"
        pipe = self.redis.pipeline()
        # 1. Append message to the burst list
        pipe.rpush(list_key, canonical_json)
        # 2. Set/reset the sliding-window sentinel with TTL
        pipe.set(sentinel_key, "1", ex=BURST_WINDOW_SECONDS)
        # 3. Track this burst as active
        pipe.sadd(BURST_ACTIVE_SET, key_suffix)
        await pipe.execute()
        logger.debug(f"Burst buffer: added message for {customer_phone} (window reset to {BURST_WINDOW_SECONDS}s)")
    async def get_expired_bursts(self) -> list[str]:
        """
        Check for burst windows that have expired (sentinel TTL gone).
        Returns list of key suffixes (tenant_id:customer_phone) ready to flush.
        """
        active_keys = await self.redis.smembers(BURST_ACTIVE_SET)
        expired = []
        for key_suffix in active_keys:
            sentinel_key = f"{BURST_SENTINEL_PREFIX}{key_suffix}"
            exists = await self.redis.exists(sentinel_key)
            if not exists:
                expired.append(key_suffix)
        return expired
    async def flush_burst(self, key_suffix: str) -> Optional[str]:
        """
        Flush an expired burst: combine all buffered messages into one
        CanonicalMessage and push it to the main message_queue.
        Returns the combined canonical JSON, or None if empty.
        """
        list_key = f"{BURST_LIST_PREFIX}{key_suffix}"
        proc_key = f"burst:proc:{key_suffix}"
        if hasattr(self.redis, "rename"):
            try:
                await self.redis.rename(list_key, proc_key)
            except ResponseError:
                return None
            await self.redis.srem(BURST_ACTIVE_SET, key_suffix)
            return await self._flush_proc_key(proc_key, key_suffix)
        else:
            # Fallback for mock/test environment FakeRedis that lacks 'rename'
            pipe = self.redis.pipeline()
            pipe.lrange(list_key, 0, -1)
            pipe.delete(list_key)
            pipe.srem(BURST_ACTIVE_SET, key_suffix)
            results = await pipe.execute()
            raw_messages = results[0]
            if not raw_messages:
                return None
            messages = []
            for raw in raw_messages:
                try:
                    messages.append(CanonicalMessage.model_validate_json(raw))
                except Exception as e:
                    logger.warning(f"Burst buffer fallback: skipping unparseable message: {e}")
            if not messages:
                return None
            base = messages[0]
            combined_texts = []
            for m in messages:
                if m.message.text:
                    combined_texts.append(m.message.text)
            combined_text = " ".join(combined_texts) if combined_texts else "[No text content]"
            base.message.text = combined_text
            combined_json = base.model_dump_json()
            await enqueue_message(self.redis, combined_json)
            return combined_json

    async def _flush_proc_key(self, proc_key: str, key_suffix: str) -> Optional[str]:
        raw_messages = await self.redis.lrange(proc_key, 0, -1)
        if not raw_messages:
            await self.redis.delete(proc_key)
            return None
        # Parse all canonical messages
        messages = []
        for raw in raw_messages:
            try:
                messages.append(CanonicalMessage.model_validate_json(raw))
            except Exception as e:
                logger.warning(f"Burst buffer: skipping unparseable message: {e}")
        if not messages:
            await self.redis.delete(proc_key)
            return None
        # Use the first message as the base, combine text from all
        base = messages[0]
        combined_texts = []
        for m in messages:
            if m.message.text:
                combined_texts.append(m.message.text)
        combined_text = " ".join(combined_texts) if combined_texts else "[No text content]"
        # Update the base message with combined text
        base.message.text = combined_text
        combined_json = base.model_dump_json()
        # Push to main queue
        await enqueue_message(self.redis, combined_json)
        # Safely delete processing list since it has been successfully queued
        await self.redis.delete(proc_key)
        logger.info(
            f"Burst buffer: flushed {len(messages)} messages for {key_suffix} "
            f"into single combined message"
        )
        return combined_json

    async def recover_stale_bursts(self) -> None:
        """
        Recover leftover processing keys from crashed flush attempts.
        """
        cursor = 0
        pattern = "burst:proc:*"
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
            for proc_key in keys:
                prefix_len = len("burst:proc:")
                key_suffix = proc_key[prefix_len:]
                try:
                    logger.warning(f"Burst buffer recovery: recovering stale processing key {proc_key}")
                    await self._flush_proc_key(proc_key, key_suffix)
                except Exception as e:
                    logger.error(f"Failed to recover stale burst key {proc_key}: {e}")
            if cursor == 0:
                break
# Singleton
burst_buffer = BurstBuffer(settings.REDIS_URL)