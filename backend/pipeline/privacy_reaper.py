"""
Privacy Reaper — DPDP §12-13 / GDPR Art. 17
Executes hard-delete cascades for customers whose 72-hour erasure tombstone
window has expired (status='pending_erasure', erasure_scheduled_at <= NOW()).

Run as a standalone process alongside other workers:
    python -m backend.pipeline.privacy_reaper

Or schedule via cron (recommended for production):
    0 * * * * python -m backend.pipeline.privacy_reaper --once
"""
import asyncio
import signal
import sys
from contextlib import suppress
from datetime import datetime, timezone
from typing import List
from uuid import UUID

from backend.core.config import settings
from backend.db.client import db_client
from backend.ops.logger import logger
from backend.pipeline.reliable_queue import WORKERS_PAUSED_KEY

import redis.asyncio as redis


REAPER_POLL_INTERVAL_SECONDS = int(getattr(settings, "PRIVACY_REAPER_POLL_INTERVAL", 3600))  # 1 hour
REAPER_BATCH_SIZE = 50
REAPER_HEARTBEAT_KEY = "worker:privacy_reaper:heartbeat"
REAPER_HEARTBEAT_TTL_SECONDS = REAPER_POLL_INTERVAL_SECONDS * 2 + 300

_shutdown_event = asyncio.Event()


async def _resolve_tenant_id(conn, customer_id: UUID):
    return await conn.fetchval(
        "SELECT tenant_id FROM customers WHERE id = $1", customer_id
    )


async def _fetch_due_tombstones(conn) -> List[dict]:
    """
    Return up to REAPER_BATCH_SIZE customers whose erasure window has expired.
    Locks the rows FOR UPDATE SKIP LOCKED so concurrent reapers don't double-process.
    """
    rows = await conn.fetch(
        """
        SELECT id, tenant_id, erasure_scheduled_at
          FROM customers
         WHERE status = 'pending_erasure'
           AND erasure_scheduled_at <= NOW()
         ORDER BY erasure_scheduled_at ASC
         LIMIT $1
         FOR UPDATE SKIP LOCKED
        """,
        REAPER_BATCH_SIZE,
    )
    return [dict(r) for r in rows]


async def _write_audit(tenant_id: UUID, customer_id: UUID, counts: dict) -> None:
    try:
        await db_client.write_audit_log(
            tenant_id=tenant_id,
            actor_id=None,
            action="erasure_executed",
            resource_type="customer",
            resource_id=customer_id,
            details={"records_deleted": counts, "executed_by": "privacy_reaper"},
            ip_address="system",
        )
    except Exception as audit_err:
        logger.warning(
            f"REAPER: Failed to write audit log | customer={customer_id} error={audit_err}"
        )


async def reap_once() -> int:
    """
    Process one batch of expired tombstones.
    Returns the number of customers that were actually deleted.
    """
    deleted = 0
    async with db_client.acquire() as conn:
        async with conn.transaction():
            due = await _fetch_due_tombstones(conn)
            for record in due:
                customer_id: UUID = record["id"]
                tenant_id: UUID = record["tenant_id"]
                try:
                    counts = await db_client.erase_customer_data(tenant_id, customer_id, conn=conn)
                    deleted += 1
                    await _write_audit(tenant_id, customer_id, counts)
                    logger.info(
                        f"REAPER: Erased customer | tenant={tenant_id} customer={customer_id} "
                        f"counts={counts}"
                    )
                except Exception as exc:
                    logger.error(
                        f"REAPER: Failed to erase customer={customer_id} | error={exc}"
                    )

    return deleted


async def reaper_loop() -> None:
    logger.info("Privacy reaper started")
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        while not _shutdown_event.is_set():
            try:
                # Write heartbeat so health dashboards can track liveness.
                await redis_client.set(
                    REAPER_HEARTBEAT_KEY,
                    datetime.now(timezone.utc).isoformat(),
                    ex=REAPER_HEARTBEAT_TTL_SECONDS,
                )

                # Honour the global worker pause signal.
                if await redis_client.exists(WORKERS_PAUSED_KEY):
                    logger.debug("Privacy reaper paused by operator signal")
                    await asyncio.sleep(60)
                    continue

                count = await reap_once()
                if count:
                    logger.info(f"REAPER: Batch complete — {count} customer(s) hard-deleted")

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"REAPER: Loop error | {exc}")

            await asyncio.sleep(REAPER_POLL_INTERVAL_SECONDS)
    finally:
        await redis_client.aclose()
    logger.info("Privacy reaper shutting down")


async def shutdown() -> None:
    logger.info("Privacy reaper shutdown signal received")
    _shutdown_event.set()


if __name__ == "__main__":
    # Support --once flag for cron-style invocation
    run_once = "--once" in sys.argv

    loop = asyncio.new_event_loop()

    if run_once:
        try:
            count = loop.run_until_complete(reap_once())
            logger.info(f"REAPER [once]: {count} customer(s) erased")
        finally:
            with suppress(Exception):
                loop.run_until_complete(db_client.close())
            loop.close()
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.ensure_future(shutdown()))
            except NotImplementedError:
                pass  # Windows
        try:
            loop.run_until_complete(reaper_loop())
        except KeyboardInterrupt:
            loop.run_until_complete(shutdown())
        finally:
            with suppress(Exception):
                loop.run_until_complete(db_client.close())
            loop.close()
