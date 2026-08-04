"""
Burst Worker (Phase 9c)
Lightweight async loop that polls for expired burst windows and flushes
combined messages into the main message_queue.
Run alongside the main worker:
    python -m backend.pipeline.burst_worker
"""
import asyncio
import signal
from datetime import datetime, timezone
from backend.pipeline.burst_buffer import burst_buffer
from backend.ops.logger import logger
from backend.pipeline.reliable_queue import WORKERS_PAUSED_KEY
POLL_INTERVAL_SECONDS = 1  # Check for expired bursts every 1s
BURST_WORKER_HEARTBEAT_KEY = "worker:burst:heartbeat"
BURST_WORKER_HEARTBEAT_TTL_SECONDS = 15
_shutdown_event = asyncio.Event()
async def _write_heartbeat() -> None:
    await burst_buffer.redis.set(
        BURST_WORKER_HEARTBEAT_KEY,
        datetime.now(timezone.utc).isoformat(),
        ex=BURST_WORKER_HEARTBEAT_TTL_SECONDS,
    )
async def burst_worker_loop():
    """
    Continuously polls for expired burst windows and flushes them.
    """
    logger.info("Burst worker started. Polling for expired burst windows...")
    try:
        await burst_buffer.recover_stale_bursts()
    except Exception as e:
        logger.error(f"Error recovering stale bursts on startup: {e}")

    while not _shutdown_event.is_set():
        try:
            await _write_heartbeat()

            # Pause-gate: skip flush while system:workers:paused is set.
            if await burst_buffer.redis.exists(WORKERS_PAUSED_KEY):
                logger.debug("Burst worker paused by operator signal")
                await asyncio.sleep(5)
                continue

            expired = await burst_buffer.get_expired_bursts()
            for key_suffix in expired:
                try:
                    await burst_buffer.flush_burst(key_suffix)
                except Exception as e:
                    logger.error(f"Burst flush error for {key_suffix}: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Burst worker loop error: {e}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    logger.info("Burst worker shutting down gracefully...")
async def shutdown():
    """Graceful shutdown handler."""
    logger.info("Burst worker shutdown signal received")
    _shutdown_event.set()
if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(shutdown()))
        except NotImplementedError:
            pass  # Windows
    try:
        loop.run_until_complete(burst_worker_loop())
    except KeyboardInterrupt:
        loop.run_until_complete(shutdown())
    finally:
        loop.close()
