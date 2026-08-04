-- 012_normalize_tenant_config_contract.sql
-- Moves saved tenant config_json toward the canonical shape used by
-- backend.schemas.config.normalize_tenant_config and the dashboard editor.
BEGIN;
CREATE OR REPLACE FUNCTION awa_normalize_day_schedule(value jsonb)
RETURNS jsonb AS $$
DECLARE
    text_value text;
    open_part text;
    close_part text;
    is_open_value boolean;
BEGIN
    IF value IS NULL OR value = 'null'::jsonb THEN
        RETURN '{"is_open": false, "open_time": "09:00", "close_time": "17:00"}'::jsonb;
    END IF;
    IF jsonb_typeof(value) = 'object' THEN
        is_open_value := CASE lower(coalesce(value->>'is_open', 'false'))
            WHEN 'true' THEN true
            WHEN '1' THEN true
            ELSE false
        END;
        RETURN jsonb_build_object(
            'is_open', is_open_value,
            'open_time', coalesce(nullif(value->>'open_time', ''), '09:00'),
            'close_time', coalesce(nullif(value->>'close_time', ''), '17:00')
        );
    END IF;
    text_value := lower(trim(both from value #>> '{}'));
    IF text_value IS NULL OR text_value = '' OR text_value = 'closed' THEN
        RETURN '{"is_open": false, "open_time": "09:00", "close_time": "17:00"}'::jsonb;
    END IF;
    IF position('-' in text_value) > 0 THEN
        open_part := nullif(trim(split_part(text_value, '-', 1)), '');
        close_part := nullif(trim(split_part(text_value, '-', 2)), '');
        RETURN jsonb_build_object(
            'is_open', true,
            'open_time', coalesce(open_part, '09:00'),
            'close_time', coalesce(close_part, '17:00')
        );
    END IF;
    RETURN jsonb_build_object(
        'is_open', true,
        'open_time', text_value,
        'close_time', '17:00'
    );
END;
$$ LANGUAGE plpgsql IMMUTABLE;
CREATE OR REPLACE FUNCTION awa_normalize_module(value jsonb, default_enabled boolean)
RETURNS jsonb AS $$
DECLARE
    enabled_value boolean;
BEGIN
    IF value IS NULL OR value = 'null'::jsonb THEN
        RETURN jsonb_build_object('enabled', default_enabled, 'fields', '[]'::jsonb);
    END IF;
    IF jsonb_typeof(value) <> 'object' THEN
        RETURN jsonb_build_object(
            'enabled', CASE lower(value #>> '{}') WHEN 'true' THEN true WHEN '1' THEN true ELSE false END,
            'fields', '[]'::jsonb
        );
    END IF;
    enabled_value := CASE
        WHEN value ? 'enabled' THEN CASE lower(value->>'enabled') WHEN 'true' THEN true WHEN '1' THEN true ELSE false END
        ELSE default_enabled
    END;
    RETURN jsonb_build_object(
        'enabled', enabled_value,
        'fields', CASE WHEN jsonb_typeof(value->'fields') = 'array' THEN value->'fields' ELSE '[]'::jsonb END
    );
END;
$$ LANGUAGE plpgsql IMMUTABLE;
CREATE OR REPLACE FUNCTION awa_normalize_services(value jsonb)
RETURNS jsonb AS $$
DECLARE
    service jsonb;
    result jsonb := '[]'::jsonb;
    service_name text;
    service_id text;
    duration_mins integer;
    required_fields jsonb;
BEGIN
    IF value IS NULL OR jsonb_typeof(value) <> 'array' THEN
        RETURN result;
    END IF;
    FOR service IN SELECT * FROM jsonb_array_elements(value)
    LOOP
        IF jsonb_typeof(service) <> 'object' THEN
            CONTINUE;
        END IF;
        service_name := coalesce(nullif(service->>'name', ''), 'Service');
        service_id := coalesce(
            nullif(service->>'id', ''),
            nullif(regexp_replace(lower(service_name), '[^a-z0-9]+', '_', 'g'), ''),
            'service'
        );
        duration_mins := CASE
            WHEN coalesce(service->>'duration_mins', '') ~ '^[0-9]+$' THEN (service->>'duration_mins')::integer
            WHEN coalesce(service->>'duration_minutes', '') ~ '^[0-9]+$' THEN (service->>'duration_minutes')::integer
            ELSE 30
        END;
        required_fields := CASE
            WHEN jsonb_typeof(service->'required_fields') = 'array' THEN service->'required_fields'
            WHEN jsonb_typeof(service->'fields') = 'array' THEN service->'fields'
            ELSE '[]'::jsonb
        END;
        result := result || jsonb_build_array(jsonb_strip_nulls(jsonb_build_object(
            'id', service_id,
            'name', service_name,
            'duration_mins', duration_mins,
            'price', service->'price',
            'required_fields', required_fields
        )));
    END LOOP;
    RETURN result;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
UPDATE tenant_configs
SET config_json = (config_json - 'language' - 'enabled_tools' - 'usage_limits') || jsonb_build_object(
    'business_name', coalesce(nullif(config_json->>'business_name', ''), 'Business'),
    'business_type', coalesce(nullif(config_json->>'business_type', ''), 'other'),
    'timezone', coalesce(nullif(config_json->>'timezone', ''), 'UTC'),
    'default_language', coalesce(nullif(config_json->>'default_language', ''), nullif(config_json->>'language', ''), 'en'),
    'ai_mode', coalesce(nullif(config_json->>'ai_mode', ''), 'standard'),
    'ai_tone', coalesce(nullif(config_json->>'ai_tone', ''), 'professional and friendly'),
    'greeting_message', coalesce(nullif(config_json->>'greeting_message', ''), 'Hello! How can we help you today?'),
    'fallback_message', coalesce(nullif(config_json->>'fallback_message', ''), 'I am not sure yet. Could you share a little more detail?'),
    'escalation_message', coalesce(nullif(config_json->>'escalation_message', ''), 'I am connecting you with a human agent. They will be with you shortly.'),
    'working_hours', jsonb_build_object(
        'monday', awa_normalize_day_schedule(config_json #> '{working_hours,monday}'),
        'tuesday', awa_normalize_day_schedule(config_json #> '{working_hours,tuesday}'),
        'wednesday', awa_normalize_day_schedule(config_json #> '{working_hours,wednesday}'),
        'thursday', awa_normalize_day_schedule(config_json #> '{working_hours,thursday}'),
        'friday', awa_normalize_day_schedule(config_json #> '{working_hours,friday}'),
        'saturday', awa_normalize_day_schedule(config_json #> '{working_hours,saturday}'),
        'sunday', awa_normalize_day_schedule(config_json #> '{working_hours,sunday}')
    ),
    'services', awa_normalize_services(config_json->'services'),
    'modules', coalesce(config_json->'modules', '{}'::jsonb) || jsonb_build_object(
        'booking', awa_normalize_module(config_json #> '{modules,booking}', true),
        'lead_capture', awa_normalize_module(config_json #> '{modules,lead_capture}', false),
        'support', awa_normalize_module(config_json #> '{modules,support}', false),
        'faq', awa_normalize_module(config_json #> '{modules,faq}', true),
        'human_handoff', awa_normalize_module(config_json #> '{modules,human_handoff}', true)
    ),
    'faqs', CASE WHEN jsonb_typeof(config_json->'faqs') = 'array' THEN config_json->'faqs' ELSE '[]'::jsonb END,
    'ignored_numbers', CASE WHEN jsonb_typeof(config_json->'ignored_numbers') = 'array' THEN config_json->'ignored_numbers' ELSE '[]'::jsonb END,
    'limits', CASE WHEN jsonb_typeof(coalesce(config_json->'limits', config_json->'usage_limits')) = 'object' THEN coalesce(config_json->'limits', config_json->'usage_limits') ELSE '{}'::jsonb END
);
DROP FUNCTION awa_normalize_services(jsonb);
DROP FUNCTION awa_normalize_module(jsonb, boolean);
DROP FUNCTION awa_normalize_day_schedule(jsonb);
COMMIT;