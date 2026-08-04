DO $$
BEGIN
    RAISE EXCEPTION 'Deprecated split production schema. Use backend/db/sql/production/000_apply_canonical_schema.sql instead.';
END $$;
\quit 3
-- 002_core_tables.sql
-- Deprecated legacy split schema retained only as a historical reference.
BEGIN;
-- 1. TENANTS
CREATE TABLE tenants (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    plan_code TEXT NOT NULL,
    status tenant_status NOT NULL,
    ai_mode TEXT NOT NULL DEFAULT 'standard',
    trial_ends_at TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- 1.1 SUBSCRIPTION PLANS
CREATE TABLE subscription_plans (
    id UUID PRIMARY KEY,
    plan_code TEXT UNIQUE,
    name TEXT NOT NULL,
    monthly_price_cents INT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'INR',
    razorpay_plan_id TEXT UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    features_json JSONB NOT NULL DEFAULT '[]',
    limits_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT subscription_plans_monthly_price_cents_non_negative CHECK (monthly_price_cents >= 0)
);
-- 2. USERS
CREATE TABLE users (
    id UUID PRIMARY KEY,
    phone_number TEXT UNIQUE NOT NULL,
    email TEXT,
    name TEXT,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- 3. USER ↔ TENANT MAPPING
CREATE TABLE user_tenants (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    role TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, tenant_id)
);
-- 4. WHATSAPP ACCOUNTS
CREATE TABLE whatsapp_accounts (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    phone_number TEXT NOT NULL,
    provider_account_id TEXT,
    verification_status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(phone_number)
);
-- 9. TENANT CONFIG (WITH UNIQUE ACTIVE SAFETY)
CREATE TABLE tenant_configs (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    version INT NOT NULL,
    config_json JSONB NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX uniq_active_config_per_tenant
ON tenant_configs(tenant_id)
WHERE is_active = true;
-- 12. TENANT COST DAILY
CREATE TABLE tenant_cost_daily (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    cost_date DATE NOT NULL,
    ai_token_cost NUMERIC(12,4) DEFAULT 0,
    compute_cost NUMERIC(12,4) DEFAULT 0,
    ai_tokens_used INT DEFAULT 0,
    tool_action_count INT DEFAULT 0,
    total_credits_used NUMERIC(12,4) DEFAULT 0,
    message_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, cost_date)
);
COMMIT;