"""
Cost API Routes
GET /api/v1/costs/daily — daily cost report with date range filter
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import date, datetime, timedelta, timezone
from typing import Any
from backend.api.dependencies import get_current_user
from backend.db.client import db_client
from backend.schemas.response import APIResponse
router = APIRouter(prefix="/api/v1", tags=["Costs"])
def _to_float(value: Any) -> float:
    return float(value or 0)
def _to_int(value: Any) -> int:
    return int(value or 0)
def _serialize_cost_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        cost_date = record.get("cost_date")
        ai_cost = _to_float(record.get("ai_token_cost"))
        compute_cost = _to_float(record.get("compute_cost"))
        rows.append({
            "date": cost_date.isoformat() if hasattr(cost_date, "isoformat") else str(cost_date),
            "cost_usd": ai_cost + compute_cost,
            "total_tokens": _to_int(record.get("ai_tokens_used")),
            "message_count": _to_int(record.get("message_count")),
            "tenant_id": str(record.get("tenant_id") or ""),
        })
    return rows
def _date_range(date_from: date, date_to: date) -> list[date]:
    return [date_from + timedelta(days=offset) for offset in range((date_to - date_from).days + 1)]
def _serialize_cost_window(records: list[dict[str, Any]], date_from: date, date_to: date) -> list[dict[str, Any]]:
    by_date = {}
    for row in _serialize_cost_rows(records):
        by_date[row["date"]] = {
            **by_date.get(row["date"], {
                "date": row["date"],
                "cost_usd": 0.0,
                "total_tokens": 0,
                "message_count": 0,
                "tenant_id": row["tenant_id"],
            }),
            "cost_usd": by_date.get(row["date"], {}).get("cost_usd", 0.0) + row["cost_usd"],
            "total_tokens": by_date.get(row["date"], {}).get("total_tokens", 0) + row["total_tokens"],
            "message_count": by_date.get(row["date"], {}).get("message_count", 0) + row["message_count"],
            "tenant_id": row["tenant_id"],
        }
    default_tenant_id = next((row.get("tenant_id", "") for row in by_date.values() if row.get("tenant_id")), "")
    return [
        by_date.get(day.isoformat(), {
            "date": day.isoformat(),
            "cost_usd": 0.0,
            "total_tokens": 0,
            "message_count": 0,
            "tenant_id": default_tenant_id,
        })
        for day in _date_range(date_from, date_to)
    ]
@router.get("/costs")
async def get_cost_summary(
    days: int = Query(30, ge=1, le=366, description="Number of trailing days to summarize"),
    user: dict = Depends(get_current_user)
):
    """Get dashboard cost summary for the trailing window."""
    date_to = datetime.now(timezone.utc).date()
    date_from = date_to - timedelta(days=days - 1)
    records = await db_client.get_cost_daily(user["tenant_id"], date_from, date_to)
    items = _serialize_cost_window(records, date_from, date_to)
    return APIResponse.create_success(data={
        "items": items,
        "total_spend": sum(item["cost_usd"] for item in items),
        "total_tokens": sum(item["total_tokens"] for item in items),
        "total_messages": sum(item["message_count"] for item in items),
    })
@router.get("/costs/daily")
async def get_daily_costs(
    date_from: date = Query(..., description="Start date (YYYY-MM-DD)"),
    date_to: date = Query(..., description="End date (YYYY-MM-DD)"),
    user: dict = Depends(get_current_user)
):
    """Get daily cost breakdown for the tenant."""
    if date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be before or equal to date_to")
    if (date_to - date_from).days > 366:
        raise HTTPException(status_code=422, detail="Date range cannot exceed 366 days")
    records = await db_client.get_cost_daily(user["tenant_id"], date_from, date_to)
    return APIResponse.create_success(data=_serialize_cost_window(records, date_from, date_to))