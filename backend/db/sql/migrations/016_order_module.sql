-- =================================================================
-- Migration: 016_order_module
-- Description: Adds Tool 2 order persistence tables and plan/config defaults.
-- =================================================================
BEGIN;
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    channel_type TEXT NOT NULL DEFAULT 'awa_chat',
    status TEXT NOT NULL DEFAULT 'new',
    currency TEXT NOT NULL DEFAULT 'INR',
    subtotal_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    total_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    fulfillment_type TEXT NOT NULL DEFAULT 'pickup',
    payment_method TEXT NOT NULL DEFAULT 'not_collected',
    payment_status TEXT NOT NULL DEFAULT 'not_collected',
    customer_snapshot JSONB NOT NULL DEFAULT '{}',
    delivery_address TEXT,
    order_data JSONB NOT NULL DEFAULT '{}',
    idempotency_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT orders_status_check CHECK (status IN ('new', 'accepted', 'preparing', 'ready', 'completed', 'cancelled')),
    CONSTRAINT orders_fulfillment_check CHECK (fulfillment_type IN ('pickup', 'delivery', 'in_store')),
    CONSTRAINT orders_payment_status_check CHECK (payment_status IN ('not_collected', 'pending', 'paid', 'failed', 'refunded'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_orders_tenant_idempotency
    ON orders(tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_orders_tenant_created
    ON orders(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_tenant_status
    ON orders(tenant_id, status, created_at DESC);
CREATE TABLE IF NOT EXISTS order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    item_name TEXT NOT NULL,
    quantity INT NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price NUMERIC(12,2) NOT NULL DEFAULT 0,
    line_total NUMERIC(12,2) NOT NULL DEFAULT 0,
    variant_data JSONB NOT NULL DEFAULT '{}',
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_order_items_tenant_order
    ON order_items(tenant_id, order_id);
CREATE TABLE IF NOT EXISTS order_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    actor_type TEXT NOT NULL DEFAULT 'system',
    event_data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_order_events_tenant_order
    ON order_events(tenant_id, order_id, created_at);
DROP TRIGGER IF EXISTS update_orders_updated_at ON orders;
CREATE TRIGGER update_orders_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON orders;
CREATE POLICY tenant_isolation ON orders
    FOR ALL USING (tenant_id = auth_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON order_items;
CREATE POLICY tenant_isolation ON order_items
    FOR ALL USING (tenant_id = auth_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON order_events;
CREATE POLICY tenant_isolation ON order_events
    FOR ALL USING (tenant_id = auth_tenant_id());
UPDATE subscription_plans
SET
    features_json = CASE
        WHEN features_json::text LIKE '%Order tool%' THEN features_json
        ELSE (features_json::jsonb || '["Order tool"]'::jsonb)
    END,
    limits_json = jsonb_set(
        limits_json::jsonb,
        '{allowed_modules}',
        CASE
            WHEN COALESCE(limits_json::jsonb -> 'allowed_modules', '[]'::jsonb) ? 'order'
                THEN COALESCE(limits_json::jsonb -> 'allowed_modules', '[]'::jsonb)
            ELSE COALESCE(limits_json::jsonb -> 'allowed_modules', '[]'::jsonb) || '["order"]'::jsonb
        END,
        true
    )
WHERE plan_code IN ('trial', 'starter', 'pro');
COMMIT;