from __future__ import annotations
from typing import Any, Iterable, Mapping
from backend.billing.usage_guard import normalize_plan_code
CANONICAL_PLAN_NAMES = {
    "trial": "Free Trial",
    "starter": "Starter",
    "pro": "Pro",
    "enterprise": "Enterprise",
}
PLAN_SORT_ORDER = {
    "trial": 0,
    "starter": 1,
    "pro": 2,
    "enterprise": 3,
}
def canonical_plan_name(plan_code: str | None, fallback: str | None = None) -> str:
    if not plan_code or not str(plan_code).strip():
        return fallback or "Unmapped Plan"
    normalized = normalize_plan_code(plan_code)
    return CANONICAL_PLAN_NAMES.get(normalized, fallback or normalized.replace("_", " ").title())
def canonicalize_plan_row(plan: Mapping[str, Any]) -> dict[str, Any]:
    raw_plan_code = plan.get("plan_code")
    normalized = normalize_plan_code(raw_plan_code) if raw_plan_code else None
    row = dict(plan)
    row["plan_code"] = normalized
    row["name"] = canonical_plan_name(normalized, row.get("name"))
    return row
def canonicalize_plan_rows(plans: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted((canonicalize_plan_row(plan) for plan in plans), key=plan_sort_key)
    by_code: dict[str, dict[str, Any]] = {}
    for row in rows:
        plan_code = row.get("plan_code")
        if not plan_code:
            continue
        existing = by_code.get(plan_code)
        if existing is None or (row.get("is_active") and not existing.get("is_active")):
            by_code[plan_code] = row
    return sorted(by_code.values(), key=plan_sort_key)
def plan_sort_key(plan: Mapping[str, Any]) -> tuple[int, int, str]:
    raw_plan_code = plan.get("plan_code")
    normalized = normalize_plan_code(raw_plan_code) if raw_plan_code else ""
    return (
        PLAN_SORT_ORDER.get(normalized, 100),
        int(plan.get("monthly_price_cents") or 0),
        canonical_plan_name(normalized, plan.get("name")),
    )