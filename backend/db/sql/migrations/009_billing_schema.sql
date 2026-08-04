-- =================================================================
-- Migration: 009_billing_schema
-- Description: Adds Razorpay Subscription fields and Subscription Plans table for dynamic Admin Pricing.
-- =================================================================
BEGIN;
-- 1. Create Subscription Plans table
CREATE TABLE IF NOT EXISTS subscription_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    monthly_price_cents INT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'INR',
    razorpay_plan_id TEXT UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    features_json JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Trigger for updated_at on subscription_plans
DROP TRIGGER IF EXISTS update_subscription_plans_updated_at ON subscription_plans;
CREATE TRIGGER update_subscription_plans_updated_at
    BEFORE UPDATE ON subscription_plans
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
-- 2. Extend Tenants table with Razorpay metrics and Trial Enforcement
ALTER TABLE tenants
ADD COLUMN IF NOT EXISTS razorpay_customer_id TEXT UNIQUE,
ADD COLUMN IF NOT EXISTS razorpay_subscription_id TEXT UNIQUE,
ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS current_period_end TIMESTAMPTZ;
-- 3. Seed initial default plan (to prevent empty dashboard)
INSERT INTO subscription_plans (name, monthly_price_cents, currency, is_active, features_json)
VALUES (
    'Pro Plan',
    299900,  -- 2999.00 INR (cents representation depends on gateway, razorpay uses paise for INR)
    'INR',
    TRUE,
    '["Advanced AI Agent", "Unlimited Contacts", "Priority Support"]'
) ON CONFLICT DO NOTHING;
COMMIT;