# AWA — Always Working Assistant: Security and Operations

Last aligned with the codebase on 2026-08-04.

## Runtime Safety

`backend/core/config.py` validates production settings at startup. The audited production configuration requires `AWA_INTEGRATION_MODE=live` and `AUTH_MODE=postgres`; mock and Supabase authentication modes are blocked in production. Production validation also rejects mock WhatsApp or billing modes, mock-feature flags, weak JWT or public-chat secrets, missing required provider settings, wildcard or non-HTTPS CORS origins, and unsafe public webhook configuration.

The two independent public-chat secrets are:

- `PUBLIC_CHAT_LINK_SECRET`
- `PUBLIC_CHAT_SESSION_SECRET`

## Authentication and Authorization

| Layer | Current behavior |
| --- | --- |
| API auth | Audited production mode uses backend-owned JWT access and refresh tokens backed by PostgreSQL. A Supabase mode remains selectable outside production, but it is not the production configuration accepted by `backend/core/config.py`. |
| Tenant auth | The user must have a `user_tenants` membership for tenant-scoped routes. |
| Role checks | Backend route dependencies enforce tenant read, operations, tenant admin, or super-admin access. |
| Frontend guards | React route guards mirror backend role groups for navigation and route access. |
| Database RLS | SQL policies use tenant-aware helper functions hardened by migration `020_security_hardening.sql`. |

## Webhook Security

WhatsApp webhook handling:

- Requires `X-Hub-Signature-256` when `WHATSAPP_APP_SECRET` is configured.
- Applies replay-window checks.
- Resolves the tenant by WhatsApp phone number ID or display phone.
- Ignores configured numbers.
- Rejects inactive or expired tenants before normal processing.

Razorpay webhook handling is routed through `POST /api/v1/razorpay` and requires deployment-specific provider credentials and webhook configuration. Payment-provider paths have not been independently verified as production-ready.

## Public AWA Chat Security

Public chat uses:

- Slug-bound token hashes for public link access.
- Per-session tokens stored as hashes server-side.
- Session expiration and explicit end-chat flow.
- Redis-backed rate limiting with memory fallback.
- Tenant and link status checks on public endpoints.
- Configurable CAPTCHA and phone-possession verification modes.
- SSE where available, with polling fallback.

Live CAPTCHA, OTP, WhatsApp, payment, and LLM-provider behavior depends on external credentials, endpoints, and deployment configuration.

## Data Protection and Privacy

Implemented application controls include tenant-scoped data access, privacy export, consent updates, scheduled erasure operations, audit events, and prompt/config input guardrails. These technical controls are not a compliance certification. Legal and regulatory compliance depends on the deployment, jurisdiction, organizational policy, external-provider retention, backups, logs, and operator procedures.

## Observability

The API middleware adds request, trace, and response-time identifiers plus security headers and production HSTS. Operational APIs include health, webhook health, costs, audit logs, and admin views for health, billing events, tenants, prompts, privacy, and tool controls.

## Compose Deployment Notes

`docker-compose.yml` retains these local-development services:

- `postgres`
- `redis`
- `api`
- `worker`
- `burst-worker`
- `reminder-scheduler`
- `event-dispatcher`
- `frontend`

PostgreSQL, Redis, the API, and the frontend publish localhost-only host ports for local access. Containers communicate through the internal Compose service names `postgres` and `redis`; Redis is therefore not completely unexposed, even though its published port is restricted to `127.0.0.1`.

The retained Compose profile is a local-development configuration, not evidence of a verified public production deployment. The project provides no uptime guarantee or compliance certification. Email and SMS reminder delivery are not implemented; live integrations require external configuration.

## Operational Ownership Rule

When adding a new external integration or public endpoint, update:

1. Environment validation.
2. API route/auth contract.
3. Database/RLS needs.
4. Audit events.
5. Frontend types/hooks if exposed.
6. Current docs in `docs/architecture/current/`.
