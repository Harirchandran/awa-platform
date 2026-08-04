"""Business event dispatcher worker entrypoint."""
import asyncio
import os
import signal
from contextlib import suppress
from datetime import datetime, timezone

import redis.asyncio as redis

from backend.core.config import settings
from backend.db.client import db_client
from backend.domains.events.dispatcher import event_dispatcher
from backend.ops.logger import logger
from backend.pipeline.reliable_queue import WORKERS_PAUSED_KEY


EVENT_DISPATCHER_HEARTBEAT_KEY = "worker:event_dispatcher:heartbeat"
EVENT_DISPATCHER_HEARTBEAT_TTL_SECONDS = 60
EVENT_DISPATCHER_LAST_SUCCESS_KEY = "worker:event_dispatcher:last_success"
EVENT_DISPATCHER_LAST_ERROR_KEY = "worker:event_dispatcher:last_error"
EVENT_DISPATCHER_BATCH_SIZE = int(os.getenv("EVENT_DISPATCHER_BATCH_SIZE", "25"))
EVENT_DISPATCHER_POLL_INTERVAL_SECONDS = int(os.getenv("EVENT_DISPATCHER_POLL_INTERVAL_SECONDS", "10"))

_shutdown_event = asyncio.Event()


async def dispatch_events_once(limit: int = EVENT_DISPATCHER_BATCH_SIZE) -> int:
    """Dispatch one controlled batch of claimed business events."""
    return await event_dispatcher.dispatch_once(limit=limit)


async def _write_heartbeat(redis_client) -> None:
    now = datetime.now(timezone.utc).isoformat()
    await redis_client.set(
        EVENT_DISPATCHER_HEARTBEAT_KEY,
        now,
        ex=EVENT_DISPATCHER_HEARTBEAT_TTL_SECONDS,
    )


async def event_dispatcher_loop() -> None:
    logger.info("Event dispatcher started")
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        while not _shutdown_event.is_set():
            try:
                await _write_heartbeat(redis_client)

                # Pause-gate: skip processing while system:workers:paused is set.
                if await redis_client.exists(WORKERS_PAUSED_KEY):
                    logger.debug("Event dispatcher paused by operator signal")
                    await asyncio.sleep(5)
                    continue

                count = await dispatch_events_once(limit=EVENT_DISPATCHER_BATCH_SIZE)
                await redis_client.set(
                    EVENT_DISPATCHER_LAST_SUCCESS_KEY,
                    datetime.now(timezone.utc).isoformat(),
                    ex=EVENT_DISPATCHER_HEARTBEAT_TTL_SECONDS * 5,
                )
                logger.info(
                    "event dispatcher heartbeat",
                    extra={
                        "domain": "events",
                        "action": "dispatcher_heartbeat",
                        "events_claimed_total": count,
                        "outcome": "success",
                    },
                )
                await asyncio.sleep(0 if count else EVENT_DISPATCHER_POLL_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "Event dispatcher loop error",
                    exc_info=True,
                    extra={
                        "domain": "events",
                        "action": "dispatcher_loop",
                        "outcome": "failed",
                        "reason_code": "loop_exception",
                    },
                )
                with suppress(Exception):
                    await redis_client.set(
                        EVENT_DISPATCHER_LAST_ERROR_KEY,
                        datetime.now(timezone.utc).isoformat(),
                        ex=EVENT_DISPATCHER_HEARTBEAT_TTL_SECONDS * 5,
                    )
                if not _shutdown_event.is_set():
                    await asyncio.sleep(EVENT_DISPATCHER_POLL_INTERVAL_SECONDS)
    finally:
        await redis_client.aclose()
    logger.info("Event dispatcher shutting down")


async def shutdown() -> None:
    logger.info("Event dispatcher shutdown signal received")
    _shutdown_event.set()


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(shutdown()))
        except NotImplementedError:
            pass
    try:
        loop.run_until_complete(event_dispatcher_loop())
    except KeyboardInterrupt:
        loop.run_until_complete(shutdown())
    finally:
        with suppress(Exception):
            loop.run_until_complete(db_client.close())
        loop.close()
