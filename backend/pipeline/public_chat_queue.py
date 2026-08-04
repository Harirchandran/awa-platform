import asyncio
from typing import Any, Optional
import redis.asyncio as redis
from backend.core.config import settings
from backend.schemas.canonical_message import CanonicalMessage
from backend.pipeline.reliable_queue import enqueue_message

async def enqueue_public_chat_turn(
    canonical: CanonicalMessage,
    *,
    redis_client: Optional[Any] = None,
) -> None:
    # 1. Validate
    source_type_val = canonical.source_type.value if hasattr(canonical.source_type, "value") else canonical.source_type
    channel_type_val = canonical.channel_type.value if hasattr(canonical.channel_type, "value") else canonical.channel_type

    if source_type_val != "awa_chat":
        raise ValueError("Invalid source_type for public chat turn")
    if channel_type_val != "awa_chat":
        raise ValueError("Invalid channel_type for public chat turn")
    if canonical.whatsapp_account_id is not None:
        raise ValueError("whatsapp_account_id must be None for public chat turn")

    # 2. Serialize using the existing canonical model method
    serialized_payload = canonical.model_dump_json()

    # 4 & 5. Redis Client handling
    if redis_client is not None:
        await enqueue_message(redis_client, serialized_payload)
    else:
        # Create a Redis client using the settings & decode behavior used by _enqueue_for_worker
        client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await enqueue_message(client, serialized_payload)
        finally:
            close_method = getattr(client, "aclose", None) or getattr(client, "close", None)
            if close_method:
                res = close_method()
                if hasattr(res, "__await__"):
                    await res
