-- =================================================================
-- Migration 003: Add DPDP/GDPR Consent Fields to Customers
-- DPDP §6 / GDPR Art. 6-7: Lawful basis for processing
-- =================================================================
BEGIN;
-- Add consent tracking columns
ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS consent_status TEXT NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS consent_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS consent_version TEXT,
    ADD COLUMN IF NOT EXISTS consent_method TEXT;
-- Add index for consent filtering
CREATE INDEX IF NOT EXISTS idx_customers_consent
    ON customers(tenant_id, consent_status);
-- Add comment for documentation
COMMENT ON COLUMN customers.consent_status IS 'DPDP §6: pending | granted | withdrawn';
COMMENT ON COLUMN customers.consent_at IS 'Timestamp when consent was last updated';
COMMENT ON COLUMN customers.consent_version IS 'Version of privacy policy accepted (e.g. v1.0)';
COMMENT ON COLUMN customers.consent_method IS 'How consent was obtained (e.g. whatsapp_opt_in)';
COMMIT;