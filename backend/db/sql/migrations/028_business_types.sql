-- =================================================================
-- Migration: 028_business_types
-- Description: Creates the business_types table for dynamic presets and seeds it.
-- =================================================================
BEGIN;
CREATE TABLE IF NOT EXISTS business_types (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT UNIQUE NOT NULL,
    label TEXT NOT NULL,
    enabled_modules JSONB NOT NULL DEFAULT '[]',
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
DROP TRIGGER IF EXISTS update_business_types_updated_at ON business_types;
CREATE TRIGGER update_business_types_updated_at
    BEFORE UPDATE ON business_types
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
ALTER TABLE business_types ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS read_all ON business_types;
CREATE POLICY read_all ON business_types
    FOR SELECT USING (TRUE);
-- Seed initial business type presets
INSERT INTO business_types (code, label, enabled_modules, note)
VALUES
    ('salon', 'Salon / Beauty Studio', '["booking", "reminders", "reviews", "lead_capture", "faq", "human_handoff"]', 'Appointments first. Orders stay off unless the salon sells products.'),
    ('clinic', 'Clinic / Healthcare', '["booking", "reminders", "reviews", "support", "faq", "human_handoff"]', 'Appointment and patient support focused.'),
    ('restaurant', 'Restaurant / Cafe', '["order", "payments", "reminders", "reviews", "faq", "human_handoff"]', 'Ordering first. Booking can be enabled separately for table reservations.'),
    ('retail', 'Retail / Store', '["order", "payments", "reminders", "reviews", "lead_capture", "faq", "human_handoff"]', 'Product ordering and lead capture focused.'),
    ('services', 'Local Services', '["booking", "reminders", "reviews", "lead_capture", "support", "faq", "human_handoff"]', 'Good for repair, consulting, agencies, and other service businesses.'),
    ('education', 'Education / Coaching', '["booking", "reminders", "reviews", "lead_capture", "faq", "human_handoff"]', 'Inquiry and appointment focused.'),
    ('minimal', 'Minimal Assistant', '["faq", "human_handoff"]', 'Safest starting point when the business workflow is unclear.')
ON CONFLICT (code) DO UPDATE
SET label = EXCLUDED.label,
    enabled_modules = EXCLUDED.enabled_modules,
    note = EXCLUDED.note,
    updated_at = NOW();
COMMIT;