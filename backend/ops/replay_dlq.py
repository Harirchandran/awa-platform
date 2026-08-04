import asyncio
import sys
import redis.asyncio as redis
from backend.core.config import settings
from backend.schemas.canonical_message import CanonicalMessage
DLQ_KEY = "dlq"
MAIN_QUEUE_KEY = "message_queue"
async def replay_dlq():
    r = redis.from_url(settings.REDIS_URL, decode_responses=True)
    count = 0
    print("Checking DLQ...")
    while True:
        # Pop from DLQ
        raw_msg = await r.rpop(DLQ_KEY)
        if not raw_msg:
            break
        try:
            # Validate integrity
            msg = CanonicalMessage.model_validate_json(raw_msg)
            # Reset retry state for fresh processing
            msg.processing.retry_count = 0
            msg.dlq_metadata = None
            # Push back to main queue with reset state
            await r.lpush(MAIN_QUEUE_KEY, msg.model_dump_json())
            print(f"Replayed: {msg.event_id}")
            count += 1
        except Exception as e:
            print(f"Failed to replay message: {e}")
            # Potentially push back to DLQ or 'poison_queue'
    print(f"Replayed {count} messages.")
    await r.close()
if __name__ == "__main__":
    asyncio.run(replay_dlq())