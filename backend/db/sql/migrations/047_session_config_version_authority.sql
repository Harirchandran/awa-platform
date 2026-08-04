BEGIN;

WITH selected AS (
    SELECT id,
           CASE
               WHEN jsonb_typeof(state_json -> 'config_version') IN ('number', 'string')
                    AND (state_json ->> 'config_version') ~ '^[1-9][0-9]*$'
                    AND (state_json ->> 'config_version')::NUMERIC <= 2147483647
                   THEN (state_json ->> 'config_version')::INT
               WHEN config_version >= 1 THEN config_version
               ELSE 1
           END AS selected_version
    FROM conversation_sessions
)
UPDATE conversation_sessions AS sessions
SET config_version = selected.selected_version,
    state_json = jsonb_set(
        CASE WHEN jsonb_typeof(sessions.state_json) = 'object' THEN sessions.state_json ELSE '{}'::jsonb END,
        '{config_version}',
        to_jsonb(selected.selected_version),
        TRUE
    )
FROM selected
WHERE sessions.id = selected.id
  AND (
      sessions.config_version IS DISTINCT FROM selected.selected_version
      OR sessions.state_json -> 'config_version' IS DISTINCT FROM to_jsonb(selected.selected_version)
  );

ALTER TABLE conversation_sessions
    DROP CONSTRAINT IF EXISTS chk_conversation_sessions_config_version_authority;

ALTER TABLE conversation_sessions
    ALTER COLUMN state_json SET DEFAULT '{"config_version": 1, "state": {}}'::jsonb;

ALTER TABLE conversation_sessions
    ADD CONSTRAINT chk_conversation_sessions_config_version_authority CHECK (
        config_version >= 1
        AND jsonb_typeof(state_json -> 'config_version') = 'number'
        AND (state_json ->> 'config_version')::INT = config_version
    );

COMMIT;
