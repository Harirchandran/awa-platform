# AWA Tenant Config Schema
This directory contains the shared JSON Schema used to validate tenant `config_json` before activation.
## Files
- `config_schema.json` - JSON Schema draft-07 for the canonical tenant configuration.
## Required Fields
Every saved tenant config should contain:
| Field | Type | Example |
|---|---|---|
| `business_name` | string | "SmileDent Dental Clinic" |
| `business_type` | enum | general, clinic, salon, restaurant, other |
| `timezone` | string | Asia/Kolkata |
| `default_language` | string | en |
| `ai_mode` | enum | standard, concierge |
| `working_hours` | object | `{ "monday": { "is_open": true, "open_time": "09:00", "close_time": "18:00" } }` |
| `services` | array | `[{ "id": "checkup", "name": "General Checkup", "duration_mins": 30, "required_fields": [] }]` |
| `modules` | object | `{ "booking": { "enabled": true, "fields": [...] } }` |
## Runtime Rule
- The backend accepts older keys such as `language`, `duration_minutes`, string working hours, and `enabled_tools`.
- `backend.schemas.config.normalize_tenant_config` converts those legacy shapes into the canonical contract before config is returned, saved, or loaded into the runtime.
- Modules must continue to access config defensively with `dict.get()` plus defaults because future tenant modules can add new keys.