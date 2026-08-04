"""
Cost Aggregator — Production with DB Persistence
Tracks daily tenant costs and enforces usage limits.
Persists to tenant_cost_daily with in-memory fallback.
"""
from uuid import UUID
from typing import Dict, Optional
from datetime import date
from backend.ops.logger import logger
class QuotaExceededException(Exception):
    def __init__(self, tenant_id: UUID, current: float, limit: float):
        self.tenant_id = tenant_id
        self.current = current
        self.limit = limit
        super().__init__(f"Tenant {tenant_id} exceeded daily quota: ${current:.2f} / ${limit:.2f}")
class CostAggregator:
    """
    Tracks costs with DB persistence via db_client.
    Falls back to in-memory tracking if DB is unavailable.
    """
    DEFAULT_DAILY_LIMIT = 10.00  # USD
    def __init__(self):
        # In-memory fallback for when DB is unavailable
        self._fallback_usage: Dict[str, float] = {}
    def _usage_key(self, tenant_id: UUID, tenant_timezone: Optional[str] = None) -> str:
        import zoneinfo
        from datetime import datetime, timezone
        try:
            tz = zoneinfo.ZoneInfo(tenant_timezone or "UTC")
        except Exception:
            tz = timezone.utc
        cost_date_str = datetime.now(tz).date().isoformat()
        return f"{tenant_id}:{cost_date_str}"

    def track_cost(self, tenant_id: UUID, cost_amount: float, tenant_timezone: Optional[str] = None):
        """Track cost in memory (sync fallback). For async DB writes, use track_cost_async()."""
        key = self._usage_key(tenant_id, tenant_timezone)
        self._fallback_usage[key] = self._fallback_usage.get(key, 0.0) + cost_amount

        # Memory leak protection: Prune keys that do not match today's date
        import zoneinfo
        from datetime import datetime, timezone
        try:
            tz = zoneinfo.ZoneInfo(tenant_timezone or "UTC")
        except Exception:
            tz = timezone.utc
        today_date_str = datetime.now(tz).date().isoformat()

        expired_keys = [k for k in self._fallback_usage.keys() if not k.endswith(f":{today_date_str}")]
        for k in expired_keys:
            self._fallback_usage.pop(k, None)

    async def track_cost_async(
        self,
        tenant_id: UUID,
        ai_cost: float = 0.0,
        compute_cost: float = 0.0,
        tenant_timezone: Optional[str] = None,
    ):
        """Persist cost to DB (async). Falls back to in-memory on failure."""
        import zoneinfo
        from datetime import datetime, timezone
        try:
            tz = zoneinfo.ZoneInfo(tenant_timezone or "UTC")
        except Exception:
            tz = timezone.utc
        cost_date = datetime.now(tz).date().isoformat()
        try:
            from backend.db.client import db_client
            await db_client.upsert_cost_daily(
                tenant_id=tenant_id,
                cost_date=cost_date,
                ai_token_cost=ai_cost,
                compute_cost=compute_cost,
                message_count=1
            )
        except Exception as e:
            logger.warning(f"Cost DB persistence failed, using in-memory: {e}")
            self.track_cost(tenant_id, ai_cost + compute_cost, tenant_timezone)

    def get_daily_usage(self, tenant_id: UUID, tenant_timezone: Optional[str] = None) -> float:
        """Get today's usage from in-memory cache (sync)."""
        key = self._usage_key(tenant_id, tenant_timezone)
        return self._fallback_usage.get(key, 0.0)

    async def get_daily_usage_async(self, tenant_id: UUID, tenant_timezone: Optional[str] = None) -> float:
        """Get today's usage from DB (async). Falls back to in-memory."""
        try:
            from backend.db.client import db_client
            return await db_client.get_tenant_daily_usage(tenant_id)
        except Exception:
            return self.get_daily_usage(tenant_id, tenant_timezone)

    def check_usage_limits(self, tenant_id: UUID, daily_limit: float = None, tenant_timezone: Optional[str] = None):
        """
        Enforce daily cost limits (sync). Raises QuotaExceededException.
        Production limitation: hard token-budget enforcement must query the daily table before execution:
        current = await self.get_daily_usage_async(tenant_id)
        """
        limit = daily_limit or self.DEFAULT_DAILY_LIMIT
        current = self.get_daily_usage(tenant_id, tenant_timezone)
        if current > limit:
            raise QuotaExceededException(tenant_id, current, limit)
cost_aggregator = CostAggregator()
