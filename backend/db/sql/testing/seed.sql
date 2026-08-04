-- test_seed_data.sql
-- Relaxed constraints for testing
BEGIN;
-- 1. Mock Tenant
INSERT INTO tenants (id, name, plan_code, status)
VALUES
    ('00000000-0000-0000-0000-000000000001', 'Test Tenant', 'ENTERPRISE', 'active')
ON CONFLICT (id) DO NOTHING;
-- 2. Mock Config
INSERT INTO tenant_configs (id, tenant_id, version, config_json, is_active)
VALUES
    ('00000000-0000-0000-0000-000000000099', '00000000-0000-0000-0000-000000000001', 12, '{
        "business_name": "Acme Corp",
        "business_type": "general",
        "timezone": "Asia/Kolkata",
        "default_language": "en",
        "ai_mode": "standard",
        "ai_tone": "professional and friendly",
        "greeting_message": "Hello! Welcome to Acme Corp. How can I help you today?",
        "fallback_message": "I am not sure yet. Could you share a little more detail?",
        "escalation_message": "I am connecting you with a human agent. They will be with you shortly.",
        "working_hours": {
            "monday": {"is_open": true, "open_time": "09:00", "close_time": "18:00"},
            "tuesday": {"is_open": true, "open_time": "09:00", "close_time": "18:00"},
            "wednesday": {"is_open": true, "open_time": "09:00", "close_time": "18:00"},
            "thursday": {"is_open": true, "open_time": "09:00", "close_time": "18:00"},
            "friday": {"is_open": true, "open_time": "09:00", "close_time": "18:00"},
            "saturday": {"is_open": false, "open_time": "09:00", "close_time": "17:00"},
            "sunday": {"is_open": false, "open_time": "09:00", "close_time": "17:00"}
        },
        "services": [{"id": "consulting", "name": "Consulting", "duration_mins": 30, "price": 100, "required_fields": []}],
        "modules": {
            "booking": {
                "enabled": true,
                "fields": [
                    {"key": "full_name", "label": "Full Name", "type": "string", "required": true},
                    {"key": "date", "label": "Date", "type": "date", "required": true}
                ]
            },
            "lead_capture": {"enabled": false, "fields": []},
            "support": {"enabled": false, "fields": []},
            "faq": {"enabled": true, "fields": []},
            "human_handoff": {"enabled": true, "fields": []}
        },
        "faqs": [],
        "ignored_numbers": [],
        "limits": {}
    }', true)
ON CONFLICT (id) DO NOTHING;
-- 3. Mock User
INSERT INTO users (id, phone_number, name)
VALUES
    ('00000000-0000-0000-0000-000000000002', '+15550000000', 'Test Admin')
ON CONFLICT (id) DO NOTHING;
COMMIT;