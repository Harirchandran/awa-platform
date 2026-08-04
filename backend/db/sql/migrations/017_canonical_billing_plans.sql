-- =================================================================
-- Migration: 017_canonical_billing_plans
-- Description: Deactivates legacy unmapped billing rows and normalizes canonical labels.
-- =================================================================
BEGIN;
UPDATE subscription_plans
SET
    name = CASE lower(plan_code)
        WHEN 'trial' THEN 'Free Trial'
        WHEN 'starter' THEN 'Starter'
        WHEN 'pro' THEN 'Pro'
        WHEN 'enterprise' THEN 'Enterprise'
        ELSE name
    END,
    updated_at = NOW()
WHERE plan_code IS NOT NULL;
UPDATE subscription_plans
SET
    is_active = FALSE,
    name = 'Legacy Pro Plan',
    updated_at = NOW()
WHERE plan_code IS NULL
  AND lower(name) = 'pro plan'
  AND monthly_price_cents = 299900;
COMMIT;