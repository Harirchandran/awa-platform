from pathlib import Path
import re


ROOT = Path("backend/db/sql")
MIGRATION = ROOT / "migrations/049_review_automation_schema_alignment.sql"

BOOKING_VALUES = {"confirmed", "cancelled", "completed"}
REVIEW_SOURCES = {
    "manual", "booking", "order", "reminder", "automation", "booking_completed_automation"
}
SCHEDULED_TRIGGERS = {
    "manual", "before_booking", "after_booking", "before_payment_expiry",
    "after_payment_failed", "after_order_completed", "after_payment_completed",
    "inactive_conversation", "custom", "review_requested",
}


def _quoted_values(text, marker):
    section = text[text.index(marker):]
    return set(re.findall(r"'([a-z_]+)'", section.split(");", 1)[0]))


def test_migration_049_preserves_authoritative_values_and_is_idempotent():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ALTER TYPE booking_status ADD VALUE IF NOT EXISTS 'completed'" in sql
    assert REVIEW_SOURCES <= _quoted_values(sql, "review_requests contains")
    assert SCHEDULED_TRIGGERS <= _quoted_values(sql, "scheduled_messages contains")
    assert "DROP CONSTRAINT IF EXISTS review_requests_source_check" in sql
    assert "DROP CONSTRAINT IF EXISTS scheduled_messages_trigger_check" in sql
    assert sql.count("RAISE EXCEPTION") == 2
    assert not re.search(r"\b(DELETE|TRUNCATE)\b", sql, re.IGNORECASE)


def test_migration_049_is_registered_immediately_after_048():
    runner = (ROOT / "docker_run_migrations.sql").read_text(encoding="utf-8")
    assert runner.index("048_booking_completion_event_idempotency.sql") < runner.index(
        "049_review_automation_schema_alignment.sql"
    )
    assert runner.count("049_review_automation_schema_alignment.sql") == 1


def test_fresh_schema_contract_matches_migration():
    schema = (ROOT / "supabase_complete.sql").read_text(encoding="utf-8")
    enum = re.search(r"CREATE TYPE booking_status AS ENUM \(([^)]+)\)", schema).group(1)
    assert BOOKING_VALUES == set(re.findall(r"'([a-z_]+)'", enum))
    assert REVIEW_SOURCES <= _quoted_values(schema, "review_requests_source_check")
    assert SCHEDULED_TRIGGERS <= _quoted_values(schema, "scheduled_messages_trigger_check")

    production_enums = (ROOT / "production/001_enums.sql").read_text(encoding="utf-8")
    production_enum = re.search(
        r"CREATE TYPE booking_status AS ENUM \(([^)]+)\)", production_enums
    ).group(1)
    assert BOOKING_VALUES == set(re.findall(r"'([a-z_]+)'", production_enum))
