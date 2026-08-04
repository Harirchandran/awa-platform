"""
Data Purge Script (Phase 9g)
Runs the retention policy SQL functions to purge expired data.
Usage:
    python -m backend.ops.data_purge
Can be scheduled via cron or Docker entrypoint for nightly cleanup.
"""
import asyncio
from backend.db.client import db_client
from backend.ops.logger import logger
async def run_purge():
    """Execute all retention policy purge functions."""
    logger.info("=== Starting Data Purge ===")
    await db_client.connect()
    try:
        async with db_client.acquire() as conn:
            # 1. Purge old messages (default: 180 days)
            try:
                result = await conn.fetchval("SELECT purge_old_messages(180)")
                logger.info(f"Purged old messages: {result} rows deleted")
            except Exception as e:
                logger.warning(f"purge_old_messages not available or failed: {e}")
            # 2. Purge expired sessions (default: 7 days grace)
            try:
                result = await conn.fetchval("SELECT purge_expired_sessions(7)")
                logger.info(f"Purged expired sessions: {result} rows deleted")
            except Exception as e:
                logger.warning(f"purge_expired_sessions not available or failed: {e}")
            # 3. Purge old audit logs (default: 365 days)
            try:
                result = await conn.fetchval("SELECT purge_old_audit_logs(365)")
                logger.info(f"Purged old audit logs: {result} rows deleted")
            except Exception as e:
                logger.warning(f"purge_old_audit_logs not available or failed: {e}")
    finally:
        await db_client.close()
    logger.info("=== Data Purge Complete ===")
if __name__ == "__main__":
    asyncio.run(run_purge())