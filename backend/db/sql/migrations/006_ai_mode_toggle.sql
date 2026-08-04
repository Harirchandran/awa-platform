-- =================================================================
-- Migration 006: AI Mode Toggle
-- Allows tenants to switch between deterministic and agentic AI.
-- =================================================================
BEGIN;
-- Add ai_mode to the core tenants table.
-- Valid values: 'standard' (Deterministic state machine), 'concierge' (LangChain Agent)
ALTER TABLE tenants
ADD COLUMN IF NOT EXISTS ai_mode TEXT NOT NULL DEFAULT 'standard';
COMMENT ON COLUMN tenants.ai_mode IS 'Plan-gated AI capability profile. Runtime uses the unified agent for all profiles; profile controls available autonomy/capability semantics.';
COMMIT;