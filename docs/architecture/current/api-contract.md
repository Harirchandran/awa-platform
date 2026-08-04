# AWA — Always Working Assistant: API Contract
Last aligned with the codebase on 2026-08-04.
The FastAPI app lives in `backend/api/app.py`. Most API routes use the `/api/v1` prefix and return the shared `APIResponse` envelope from `backend/schemas/response.py`.
## Response Envelope
Successful responses normally use:
```json
{
  "success": true,
  "data": {},
  "meta": {},
  "timestamp": "2026-05-21T00:00:00Z",
  "request_id": "uuid-or-trace-id"
}
```
Errors normally use:
```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable message",
    "details": {}
  },
  "timestamp": "2026-05-21T00:00:00Z",
  "request_id": "uuid-or-trace-id"
}
```
Known intentional exceptions:
| Endpoint type | Behavior |
| --- | --- |
| Meta webhook verification | `GET /api/v1/webhook` returns the raw challenge text on success. |
| Razorpay webhook | `POST /api/v1/razorpay` returns a compact status object. |
| QR endpoints | AWA Chat QR endpoints return SVG image responses. |
| OpenAPI docs | `/docs` and `/redoc` are disabled when `ENV=production`. |
## Authentication And Roles
Authenticated tenant routes validate the token for the selected auth mode and apply DB tenant-role lookup in `backend/api/dependencies.py`. Audited production configuration requires backend-owned PostgreSQL auth; `backend/core/config.py` rejects Supabase and mock auth in production.
| Role group | Roles |
| --- | --- |
| Tenant read | `member`, `operator`, `tenant_admin`, `admin`, `owner`, `super_admin` |
| Tenant operations | `operator`, `tenant_admin`, `admin`, `owner`, `super_admin` |
| Tenant write/admin | `tenant_admin`, `admin`, `owner`, `super_admin` |
| Platform admin | `super_admin` only |
The frontend mirrors these sets in `frontend/src/features/auth/roles.ts`.
## Live Route Inventory
This inventory was generated from the FastAPI app and grouped by responsibility.
### Public And Health
| Method | Path | Auth | Notes |
| --- | --- | --- | --- |
| GET | `/` | Public | Root service metadata. |
| GET | `/api/v1/health` | Public | API health envelope. |
| GET | `/api/v1/webhook/health` | Public | Webhook subsystem health. |
| GET | `/api/v1/webhook` | Public | Meta webhook challenge verification. |
| POST | `/api/v1/webhook` | Public signed webhook | WhatsApp inbound webhook. |
| POST | `/api/v1/razorpay` | Public signed webhook | Razorpay billing webhook. |
### Tenant And User
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/tenant` | Tenant read |
| GET | `/api/v1/me` | Tenant read |
### Customers
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/customers` | Tenant read |
| GET | `/api/v1/customers/{customer_id}` | Tenant read |
| PATCH | `/api/v1/customers/{customer_id}` | Tenant operations/write |
| GET | `/api/v1/customers/{customer_id}/timeline` | Tenant read |
| GET | `/api/v1/customer-tags` | Tenant read |
| POST | `/api/v1/customer-tags` | Tenant write |
| PATCH | `/api/v1/customer-tags/{tag_id}` | Tenant write |
| DELETE | `/api/v1/customer-tags/{tag_id}` | Tenant write |
| POST | `/api/v1/customers/{customer_id}/tags` | Tenant write |
| DELETE | `/api/v1/customers/{customer_id}/tags/{tag_id}` | Tenant write |
| GET | `/api/v1/customers/{customer_id}/notes` | Tenant read |
| POST | `/api/v1/customers/{customer_id}/notes` | Tenant write |
| PATCH | `/api/v1/customers/{customer_id}/notes/{note_id}` | Tenant write |
| DELETE | `/api/v1/customers/{customer_id}/notes/{note_id}` | Tenant write |
### Conversations And Escalations
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/conversations` | Tenant read |
| GET | `/api/v1/escalations` | Tenant read |
| GET | `/api/v1/conversations/{conversation_id}` | Tenant read |
| PATCH | `/api/v1/conversations/{conversation_id}` | Tenant operations |
| GET | `/api/v1/conversations/{conversation_id}/messages` | Tenant read |
| POST | `/api/v1/conversations/{conversation_id}/escalation` | Tenant operations |
| POST | `/api/v1/conversations/{conversation_id}/escalation/reply` | Tenant operations |
| POST | `/api/v1/conversations/{conversation_id}/escalation/return-to-awa` | Tenant operations |
| POST | `/api/v1/conversations/{conversation_id}/escalation/resolve` | Tenant operations |
### Bookings
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/bookings` | Tenant read |
| GET | `/api/v1/bookings/{booking_id}` | Tenant read |
| PATCH | `/api/v1/bookings/{booking_id}` | Tenant operations/write |
### Orders
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/orders` | Tenant read |
| GET | `/api/v1/orders/{order_id}` | Tenant read |
| PATCH | `/api/v1/orders/{order_id}` | Tenant operations/write |
| POST | `/api/v1/orders/{order_id}/actions` | Tenant operations/write |
### Catalog And Inventory
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/catalog/categories` | Tenant read |
| POST | `/api/v1/catalog/categories` | Tenant write |
| PATCH | `/api/v1/catalog/categories/{category_id}` | Tenant write |
| DELETE | `/api/v1/catalog/categories/{category_id}` | Tenant write |
| GET | `/api/v1/catalog/items` | Tenant read |
| POST | `/api/v1/catalog/items` | Tenant write |
| GET | `/api/v1/catalog/items/{item_id}` | Tenant read |
| PATCH | `/api/v1/catalog/items/{item_id}` | Tenant write |
| DELETE | `/api/v1/catalog/items/{item_id}` | Tenant write |
| POST | `/api/v1/catalog/items/{item_id}/stock-adjustments` | Tenant write |
| GET | `/api/v1/catalog/items/{item_id}/inventory-events` | Tenant read |
### Payments And Deposits
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/payments` | Tenant read |
| POST | `/api/v1/payments` | Tenant write |
| GET | `/api/v1/payments/{payment_id}` | Tenant read |
| PATCH | `/api/v1/payments/{payment_id}` | Tenant write |
| POST | `/api/v1/payments/{payment_id}/actions` | Tenant write |
| GET | `/api/v1/payments/{payment_id}/events` | Tenant read |
### Reminders And Follow-Ups
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/reminder-rules` | Tenant read |
| POST | `/api/v1/reminder-rules` | Tenant write |
| PATCH | `/api/v1/reminder-rules/{rule_id}` | Tenant write |
| DELETE | `/api/v1/reminder-rules/{rule_id}` | Tenant write |
| GET | `/api/v1/scheduled-messages` | Tenant read |
| POST | `/api/v1/scheduled-messages` | Tenant write |
| GET | `/api/v1/scheduled-messages/{message_id}` | Tenant read |
| POST | `/api/v1/scheduled-messages/{message_id}/actions` | Tenant write |
| GET | `/api/v1/scheduled-messages/{message_id}/events` | Tenant read |
### Review Collection
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/reviews/settings` | Tenant read |
| PUT | `/api/v1/reviews/settings` | Tenant write |
| GET | `/api/v1/reviews` | Tenant read |
| POST | `/api/v1/reviews` | Tenant write |
| GET | `/api/v1/reviews/{review_id}` | Tenant read |
| PATCH | `/api/v1/reviews/{review_id}` | Tenant write |
| POST | `/api/v1/reviews/{review_id}/actions` | Tenant write |
| GET | `/api/v1/reviews/{review_id}/events` | Tenant read |
### AWA Chat Tenant APIs
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/awa-chat/links` | Tenant read |
| POST | `/api/v1/awa-chat/links` | Tenant write |
| PATCH | `/api/v1/awa-chat/links/{link_id}` | Tenant write |
| GET | `/api/v1/awa-chat/links/{link_id}/qr` | Tenant read, SVG response |
### Public AWA Chat APIs
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/public-chat/links/{slug}` | Public signed link |
| GET | `/api/v1/public-chat/links/{slug}/qr` | Public SVG |
| POST | `/api/v1/public-chat/links/{slug}/sessions` | Public signed link |
| POST | `/api/v1/public-chat/sessions/{session_id}/messages` | Session token |
| POST | `/api/v1/public-chat/sessions/{session_id}/contact` | Session token |
| POST | `/api/v1/public-chat/sessions/{session_id}/end` | Session token |
| POST | `/api/v1/public-chat/sessions/{session_id}/messages/list` | Session token |
| GET | `/api/v1/public-chat/sessions/{session_id}/events` | Session token, SSE where available |
Public chat uses slug-bound token hashes, per-session tokens, and Redis-backed rate limiting with an in-memory fallback.
### Tenant Configuration, Flags, Costs, And Audit
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/config` | Tenant read |
| POST | `/api/v1/config` | Tenant write |
| PUT | `/api/v1/config` | Tenant write |
| POST | `/api/v1/config/{config_id}/activate` | Tenant write |
| GET | `/api/v1/feature-flags` | Tenant read |
| PUT | `/api/v1/feature-flags/{feature_key}` | Tenant write |
| GET | `/api/v1/costs` | Tenant read |
| GET | `/api/v1/costs/daily` | Tenant read |
| GET | `/api/v1/audit-logs` | Tenant/admin scoped |
### WhatsApp Accounts
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/whatsapp-accounts` | Tenant read |
| POST | `/api/v1/whatsapp-accounts` | Tenant write |
| POST | `/api/v1/whatsapp-accounts/embedded-signup` | Tenant write |
### Knowledge Base
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/knowledge-base` | Tenant read |
| PATCH | `/api/v1/knowledge-base/{entry_id}` | Tenant write |
| DELETE | `/api/v1/knowledge-base/{entry_id}` | Tenant write |
### Prompts
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/prompts` | Super admin |
| PUT | `/api/v1/prompts/{prompt_name}` | Super admin |
### Billing And Signup
| Method | Path | Auth |
| --- | --- | --- |
| POST | `/api/v1/auth/register` | Public |
| GET | `/api/v1/billing/status` | Tenant read |
| POST | `/api/v1/billing/subscribe` | Tenant write |
### Privacy
| Method | Path | Auth |
| --- | --- | --- |
| DELETE | `/api/v1/privacy/erase/{customer_id}` | Tenant admin |
| GET | `/api/v1/privacy/export/{customer_id}` | Tenant admin |
| POST | `/api/v1/privacy/consent/{customer_id}` | Tenant admin |
### Platform Admin
| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/v1/admin/tenants` | Super admin |
| POST | `/api/v1/admin/tenants` | Super admin |
| GET | `/api/v1/admin/tool-controls` | Super admin |
| GET | `/api/v1/admin/tenants/{tenant_id}/tool-controls` | Super admin |
| PUT | `/api/v1/admin/tenants/{tenant_id}/tool-controls` | Super admin |
| GET | `/api/v1/admin/plans` | Super admin |
| POST | `/api/v1/admin/plans` | Super admin |
| PATCH | `/api/v1/admin/plans/{plan_id}` | Super admin |
| GET | `/api/v1/admin/billing/tenants` | Super admin |
| PATCH | `/api/v1/admin/billing/tenants/{tenant_id}` | Super admin |
| GET | `/api/v1/admin/billing/events` | Super admin |
