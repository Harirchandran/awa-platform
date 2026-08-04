-- =================================================================
-- Migration: 031_tool_catalog
-- Description: Moves workflow tool catalog metadata into the database.
-- =================================================================
BEGIN;
CREATE TABLE IF NOT EXISTS tool_catalog (
    key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'workflow',
    description TEXT NOT NULL DEFAULT '',
    icon_key TEXT NOT NULL DEFAULT 'wrench',
    route_path TEXT,
    actions JSONB NOT NULL DEFAULT '[]',
    sort_order INTEGER NOT NULL DEFAULT 100,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT tool_catalog_key_format CHECK (key ~ '^[a-z0-9_]+$'),
    CONSTRAINT tool_catalog_actions_array CHECK (jsonb_typeof(actions) = 'array')
);
DROP TRIGGER IF EXISTS update_tool_catalog_updated_at ON tool_catalog;
CREATE TRIGGER update_tool_catalog_updated_at
    BEFORE UPDATE ON tool_catalog
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
ALTER TABLE tool_catalog ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS read_all ON tool_catalog;
CREATE POLICY read_all ON tool_catalog
    FOR SELECT USING (TRUE);
INSERT INTO tool_catalog (key, label, category, description, icon_key, route_path, actions, sort_order, is_active)
VALUES
    ('booking', 'Bookings', 'Scheduling', 'Collect service, date, time, and customer details for appointments.', 'calendar_check', '/dashboard/bookings', '[]', 10, TRUE),
    ('order', 'Orders', 'Commerce', 'Take orders, manage fulfillment, and keep the customer catalog ready.', 'shopping_bag', '/dashboard/orders', '[{"label":"Orders","href":"/dashboard/orders"},{"label":"Catalog","href":"/dashboard/catalog"}]', 20, TRUE),
    ('payments', 'Payments', 'Commerce', 'Create payment requests, deposits, and manual collection records.', 'wallet', '/dashboard/payments', '[]', 30, TRUE),
    ('reminders', 'Reminders', 'Automation', 'Schedule booking, payment, order, and follow-up messages.', 'bell', '/dashboard/reminders', '[]', 40, TRUE),
    ('reviews', 'Reviews', 'Growth', 'Collect ratings and route happy customers toward public review channels.', 'star', '/dashboard/reviews', '[]', 50, TRUE),
    ('lead_capture', 'Lead Capture', 'Sales', 'Collect prospect details before customers are ready to book or buy.', 'megaphone', NULL, '[]', 60, TRUE),
    ('support', 'Support', 'Service', 'Collect issue details and route support requests with context.', 'headphones', NULL, '[]', 70, TRUE),
    ('faq', 'FAQ Answers', 'Knowledge', 'Answer common questions using the tenant knowledge and FAQ setup.', 'circle_help', NULL, '[]', 80, TRUE),
    ('human_handoff', 'Human Handoff', 'Handoff', 'Let customers ask for a person when automation should step aside.', 'user_round_check', NULL, '[]', 90, TRUE)
ON CONFLICT (key) DO UPDATE
SET label = EXCLUDED.label,
    category = EXCLUDED.category,
    description = EXCLUDED.description,
    icon_key = EXCLUDED.icon_key,
    route_path = EXCLUDED.route_path,
    actions = EXCLUDED.actions,
    sort_order = EXCLUDED.sort_order,
    is_active = EXCLUDED.is_active,
    updated_at = NOW();
COMMIT;