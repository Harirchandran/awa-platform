-- 013_plan_usage_tool_limits.sql
-- Adds plan limit metadata and usage counters needed by the internal tool registry.
BEGIN;
ALTER TABLE subscription_plans
ADD COLUMN IF NOT EXISTS plan_code TEXT,
ADD COLUMN IF NOT EXISTS limits_json JSONB NOT NULL DEFAULT '{}';
CREATE UNIQUE INDEX IF NOT EXISTS idx_subscription_plans_plan_code
ON subscription_plans(plan_code)
WHERE plan_code IS NOT NULL;
ALTER TABLE tenant_cost_daily
ADD COLUMN IF NOT EXISTS ai_tokens_used INT DEFAULT 0,
ADD COLUMN IF NOT EXISTS tool_action_count INT DEFAULT 0,
ADD COLUMN IF NOT EXISTS total_credits_used NUMERIC(12,4) DEFAULT 0;
INSERT INTO subscription_plans (plan_code, name, monthly_price_cents, currency, razorpay_plan_id, is_active, features_json, limits_json)
VALUES
    (
        'trial',
        'Free Trial',
        0,
        'INR',
        'awa_trial',
        TRUE,
        '["300 messages", "Standard AI", "Booking and FAQ tools"]',
        '{"monthly_messages":300,"daily_messages":50,"monthly_ai_tokens":100000,"monthly_credits":1000,"allowed_ai_modes":["standard"],"allowed_modules":["booking","faq","human_handoff"]}'
    ),
    (
        'starter',
        'Starter',
        99900,
        'INR',
        'awa_starter',
        TRUE,
        '["1,000 messages", "Standard AI", "Booking and FAQ tools"]',
        '{"monthly_messages":1000,"daily_messages":150,"monthly_ai_tokens":300000,"monthly_credits":3000,"allowed_ai_modes":["standard"],"allowed_modules":["booking","faq","human_handoff"]}'
    ),
    (
        'pro',
        'Pro',
        299900,
        'INR',
        'awa_pro',
        TRUE,
        '["5,000 messages", "Concierge AI", "Booking, lead, support, and FAQ tools"]',
        '{"monthly_messages":5000,"daily_messages":750,"monthly_ai_tokens":2000000,"monthly_credits":10000,"allowed_ai_modes":["standard","concierge"],"allowed_modules":["booking","lead_capture","support","faq","human_handoff"]}'
    ),
    (
        'enterprise',
        'Enterprise',
        999900,
        'INR',
        'awa_enterprise',
        TRUE,
        '["Custom usage", "Concierge AI", "All tools", "Priority support"]',
        '{"monthly_messages":null,"daily_messages":null,"monthly_ai_tokens":null,"monthly_credits":null,"allowed_ai_modes":["standard","concierge"],"allowed_modules":["*"]}'
    )
ON CONFLICT (razorpay_plan_id) DO UPDATE SET
    plan_code = EXCLUDED.plan_code,
    name = EXCLUDED.name,
    monthly_price_cents = EXCLUDED.monthly_price_cents,
    currency = EXCLUDED.currency,
    is_active = EXCLUDED.is_active,
    features_json = EXCLUDED.features_json,
    limits_json = EXCLUDED.limits_json;
COMMIT;