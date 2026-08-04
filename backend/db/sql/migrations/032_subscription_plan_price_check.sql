-- =================================================================
-- Migration: 032_subscription_plan_price_check
-- Description: Prevent negative subscription plan prices.
-- =================================================================
BEGIN;
ALTER TABLE subscription_plans
    DROP CONSTRAINT IF EXISTS subscription_plans_monthly_price_cents_non_negative;
ALTER TABLE subscription_plans
    ADD CONSTRAINT subscription_plans_monthly_price_cents_non_negative
    CHECK (monthly_price_cents >= 0);
COMMIT;