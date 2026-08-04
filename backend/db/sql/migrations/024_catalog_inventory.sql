-- =================================================================
-- Migration: 024_catalog_inventory
-- Description: Adds tenant catalog, inventory tracking, and stock events.
-- =================================================================
BEGIN;
CREATE TABLE IF NOT EXISTS catalog_categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    sort_order INT NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT catalog_categories_name_nonempty CHECK (length(btrim(name)) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_catalog_categories_tenant_name_active
    ON catalog_categories(tenant_id, lower(name))
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_catalog_categories_tenant_sort
    ON catalog_categories(tenant_id, sort_order, created_at DESC)
    WHERE deleted_at IS NULL;
DROP TRIGGER IF EXISTS update_catalog_categories_updated_at ON catalog_categories;
CREATE TRIGGER update_catalog_categories_updated_at
    BEFORE UPDATE ON catalog_categories
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TABLE IF NOT EXISTS catalog_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    category_id UUID REFERENCES catalog_categories(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    description TEXT,
    price NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (price >= 0),
    currency TEXT NOT NULL DEFAULT 'INR',
    sku TEXT,
    image_url TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_available BOOLEAN NOT NULL DEFAULT TRUE,
    stock_tracking_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    stock_quantity NUMERIC(12,2) NOT NULL DEFAULT 0,
    low_stock_threshold NUMERIC(12,2),
    metadata JSONB NOT NULL DEFAULT '{}',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT catalog_items_name_nonempty CHECK (length(btrim(name)) > 0),
    CONSTRAINT catalog_items_currency_nonempty CHECK (length(btrim(currency)) > 0)
);
CREATE INDEX IF NOT EXISTS idx_catalog_items_tenant_category
    ON catalog_items(tenant_id, category_id, created_at DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_catalog_items_tenant_lookup
    ON catalog_items(tenant_id, lower(name))
    WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uniq_catalog_items_tenant_sku_active
    ON catalog_items(tenant_id, lower(sku))
    WHERE sku IS NOT NULL AND deleted_at IS NULL;
DROP TRIGGER IF EXISTS update_catalog_items_updated_at ON catalog_items;
CREATE TRIGGER update_catalog_items_updated_at
    BEFORE UPDATE ON catalog_items
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TABLE IF NOT EXISTS catalog_item_variants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    item_id UUID NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    price_delta NUMERIC(12,2) NOT NULL DEFAULT 0,
    sku TEXT,
    stock_quantity NUMERIC(12,2),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT catalog_item_variants_name_nonempty CHECK (length(btrim(name)) > 0)
);
CREATE INDEX IF NOT EXISTS idx_catalog_item_variants_item
    ON catalog_item_variants(tenant_id, item_id, created_at DESC)
    WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uniq_catalog_item_variants_tenant_sku_active
    ON catalog_item_variants(tenant_id, lower(sku))
    WHERE sku IS NOT NULL AND deleted_at IS NULL;
DROP TRIGGER IF EXISTS update_catalog_item_variants_updated_at ON catalog_item_variants;
CREATE TRIGGER update_catalog_item_variants_updated_at
    BEFORE UPDATE ON catalog_item_variants
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TABLE IF NOT EXISTS inventory_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    item_id UUID NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
    variant_id UUID REFERENCES catalog_item_variants(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    quantity_delta NUMERIC(12,2) NOT NULL DEFAULT 0,
    reason TEXT,
    order_id UUID REFERENCES orders(id) ON DELETE SET NULL,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT inventory_events_type_check CHECK (
        event_type IN ('manual_adjustment', 'order_reserved', 'order_confirmed', 'order_cancelled', 'restock', 'correction')
    )
);
CREATE INDEX IF NOT EXISTS idx_inventory_events_item_created
    ON inventory_events(tenant_id, item_id, created_at DESC);
ALTER TABLE catalog_categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog_item_variants ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON catalog_categories;
CREATE POLICY tenant_isolation ON catalog_categories
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON catalog_items;
CREATE POLICY tenant_isolation ON catalog_items
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON catalog_item_variants;
CREATE POLICY tenant_isolation ON catalog_item_variants
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON inventory_events;
CREATE POLICY tenant_isolation ON inventory_events
    FOR SELECT USING (auth_has_tenant_access(tenant_id));
COMMIT;