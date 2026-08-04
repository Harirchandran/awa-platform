-- Migration: 042_rls_enforcement
-- Description: Update auth_tenant_id to check app.current_tenant session config, and enable FORCE ROW LEVEL SECURITY on all tenant tables.

BEGIN;

-- Helper function to get current tenant_id from either app.current_tenant session variable or JWT
CREATE OR REPLACE FUNCTION auth_tenant_id()
RETURNS UUID AS $$
DECLARE
    t_id TEXT;
BEGIN
    t_id := current_setting('app.current_tenant', true);
    IF t_id IS NOT NULL AND t_id <> '' THEN
        RETURN t_id::UUID;
    END IF;
    RETURN (auth.jwt() -> 'app_metadata' ->> 'tenant_id')::UUID;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Force RLS on all tenant tables so owner/superuser connection is also subject to tenant isolation policies
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
ALTER TABLE whatsapp_accounts FORCE ROW LEVEL SECURITY;
ALTER TABLE branches FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_configs FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_feature_flags FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_cost_daily FORCE ROW LEVEL SECURITY;
ALTER TABLE customers FORCE ROW LEVEL SECURITY;
ALTER TABLE customer_tags FORCE ROW LEVEL SECURITY;
ALTER TABLE customer_tag_links FORCE ROW LEVEL SECURITY;
ALTER TABLE customer_notes FORCE ROW LEVEL SECURITY;
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;
ALTER TABLE messages FORCE ROW LEVEL SECURITY;
ALTER TABLE conversation_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE bookings FORCE ROW LEVEL SECURITY;
ALTER TABLE orders FORCE ROW LEVEL SECURITY;
ALTER TABLE order_items FORCE ROW LEVEL SECURITY;
ALTER TABLE order_events FORCE ROW LEVEL SECURITY;
ALTER TABLE catalog_categories FORCE ROW LEVEL SECURITY;
ALTER TABLE catalog_items FORCE ROW LEVEL SECURITY;
ALTER TABLE catalog_item_variants FORCE ROW LEVEL SECURITY;
ALTER TABLE inventory_events FORCE ROW LEVEL SECURITY;
ALTER TABLE payment_requests FORCE ROW LEVEL SECURITY;
ALTER TABLE payment_events FORCE ROW LEVEL SECURITY;
ALTER TABLE reminder_rules FORCE ROW LEVEL SECURITY;
ALTER TABLE scheduled_messages FORCE ROW LEVEL SECURITY;
ALTER TABLE scheduled_message_events FORCE ROW LEVEL SECURITY;
ALTER TABLE review_settings FORCE ROW LEVEL SECURITY;
ALTER TABLE review_requests FORCE ROW LEVEL SECURITY;
ALTER TABLE review_events FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;
ALTER TABLE prompt_registry FORCE ROW LEVEL SECURITY;
ALTER TABLE retention_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_knowledge_base FORCE ROW LEVEL SECURITY;
ALTER TABLE public_chat_links FORCE ROW LEVEL SECURITY;
ALTER TABLE public_chat_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_invitations FORCE ROW LEVEL SECURITY;
ALTER TABLE leads FORCE ROW LEVEL SECURITY;
ALTER TABLE business_events FORCE ROW LEVEL SECURITY;
ALTER TABLE media_assets FORCE ROW LEVEL SECURITY;

COMMIT;
