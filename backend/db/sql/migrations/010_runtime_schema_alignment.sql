-- =================================================================
-- Migration: 010_runtime_schema_alignment
-- Description: Adds columns used by current API/worker code but missing from older DB volumes.
-- =================================================================
BEGIN;
ALTER TABLE customers
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
DROP TRIGGER IF EXISTS update_customers_updated_at ON customers;
CREATE TRIGGER update_customers_updated_at
    BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
ALTER TABLE conversations
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
DROP TRIGGER IF EXISTS update_conversations_updated_at ON conversations;
CREATE TRIGGER update_conversations_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
ALTER TABLE messages
ADD COLUMN IF NOT EXISTS delivery_status TEXT;
ALTER TABLE whatsapp_accounts
ADD COLUMN IF NOT EXISTS display_name TEXT;
COMMIT;