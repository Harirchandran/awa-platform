from backend.billing.plans import canonicalize_plan_row, canonicalize_plan_rows, plan_sort_key
from backend.billing.usage_guard import normalize_plan_code
def test_pro_plan_alias_normalizes_to_canonical_pro():
    assert normalize_plan_code("Pro Plan") == "pro"
def test_canonical_plan_row_uses_display_name_from_plan_code():
    row = canonicalize_plan_row({
        "plan_code": "PROFESSIONAL",
        "name": "Professional Plan",
        "monthly_price_cents": 299900,
    })
    assert row["plan_code"] == "pro"
    assert row["name"] == "Pro"
def test_unmapped_plan_row_does_not_become_trial():
    row = canonicalize_plan_row({
        "plan_code": None,
        "name": "Legacy Pro Plan",
        "monthly_price_cents": 299900,
    })
    assert row["plan_code"] is None
    assert row["name"] == "Legacy Pro Plan"
def test_canonical_plan_rows_dedupes_equivalent_plan_codes():
    rows = canonicalize_plan_rows([
        {
            "plan_code": "professional",
            "name": "Professional Plan",
            "monthly_price_cents": 299900,
            "is_active": True,
        },
        {
            "plan_code": "pro",
            "name": "Pro",
            "monthly_price_cents": 299900,
            "is_active": True,
        },
    ])
    assert len(rows) == 1
    assert rows[0]["plan_code"] == "pro"
    assert rows[0]["name"] == "Pro"
def test_plan_sort_key_keeps_canonical_order_before_custom_plans():
    plans = [
        {"plan_code": "enterprise", "name": "Enterprise", "monthly_price_cents": 999900},
        {"plan_code": "starter", "name": "Starter", "monthly_price_cents": 99900},
        {"plan_code": "pro", "name": "Pro", "monthly_price_cents": 299900},
    ]
    ordered = sorted(plans, key=plan_sort_key)
    assert [plan["plan_code"] for plan in ordered] == ["starter", "pro", "enterprise"]