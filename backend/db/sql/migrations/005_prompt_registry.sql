-- =================================================================
-- Migration 005: Prompt Registry
-- Moves AI prompt management from local files to the database
-- =================================================================
BEGIN;
CREATE TABLE IF NOT EXISTS prompt_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE, -- NULL means platform default
    prompt_name TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    version INT DEFAULT 1,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, prompt_name, version)
);
COMMENT ON TABLE prompt_registry IS 'Stores platform and tenant-specific AI prompts dynamically.';
-- Insert Default Platform Prompts
INSERT INTO prompt_registry (tenant_id, prompt_name, prompt_text)
VALUES
(NULL, 'system_prompt',
'You are a helpful WhatsApp assistant for {business_name}.
Business Type: {business_type}
Tone: {ai_tone}
Available Services:
{services}
Working Hours:
{working_hours}
{custom_instructions_section}
{faqs_section}
Current Conversation State: {state}
Current Module: {module}
RULES:
- Only respond in the context of {business_name}''s services
- If you are unsure about the user''s intent, ask for clarification
- If the user needs human help, tell them you''re connecting them to an agent
- Keep responses concise and professional
- If Current Conversation State is NEW, use the greeting message: {greeting_message}
- If Current Conversation State is NOT NEW, DO NOT repeat the greeting message. Acknowledge and move the conversation forward.
')
ON CONFLICT DO NOTHING;
COMMIT;