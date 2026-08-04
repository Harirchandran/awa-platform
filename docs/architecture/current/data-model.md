# AWA — Always Working Assistant: Data Model
Last aligned with the codebase on 2026-08-04.
The canonical clean schema is `backend/db/sql/supabase_complete.sql`. Production and local upgrades are layered through ordered migrations in `backend/db/sql/migrations/`, plus the production bootstrap files in `backend/db/sql/production/`.
## Core Tenancy And Auth Tables
| Table | Purpose |
| --- | --- |
| `tenants` | Tenant/business account, plan, status, trial/subscription metadata. |
| `users` | Application user profile linked to the selected authentication subject. Audited production configuration uses backend-owned PostgreSQL auth. |
| `user_tenants` | Tenant membership and role. |
| `whatsapp_accounts` | Tenant WhatsApp Business account and phone-number metadata. |
| `branches` | Branch metadata. Branch-specific workflows are not fully exposed yet. |
RLS helper functions and policies are hardened in migration `020_security_hardening.sql`.
## Configuration And Controls
| Table | Purpose |
| --- | --- |
| `tenant_configs` | Versioned normalized tenant config JSON. Only one active version should exist per tenant. |
| `tenant_feature_flags` | Tenant-scoped feature flags and allowed modules. |
| `prompt_registry` | Prompt templates and versions, managed by super admins. |
| `retention_policies` | Data retention settings. |
Migration `022_production_hardening.sql` adds uniqueness around active config versions and other production constraints.
## Conversations And Messages
| Table | Purpose |
| --- | --- |
| `customers` | Customer identity and channel metadata. Phone can be null for public chat users. |
| `customer_tags` | Tenant-defined CRM labels for customer segmentation and operator context. |
| `customer_tag_links` | Many-to-many customer/tag assignments. |
| `customer_notes` | Internal tenant notes attached to customer profiles. |
| `conversations` | Conversation state, channel type, escalation fields, and automation pause/resume metadata. |
| `messages` | Inbound/outbound/customer/operator/assistant messages, provider IDs, channel metadata, delivery status. |
| `conversation_sessions` | Workflow state, collected data, active module, and channel session metadata. |
Migration `015_public_chat_channel.sql` adds browser-chat channel fields and public chat session/link support. Migration `021_conversation_sequence_integrity.sql` hardens sequence-number constraints.
Migration `023_crm_lite.sql` adds CRM Lite tags, tag links, notes, indexes, triggers, and RLS.
## Feature Tables
| Table | Purpose |
| --- | --- |
| `bookings` | Booking records created by the booking tool or edited through the dashboard. |
| `orders` | Order header rows created by the order tool or managed through the dashboard. |
| `order_items` | Order line items copied from the tenant config catalog at order time. |
| `order_events` | Order audit trail/status events. |
| `catalog_categories` | Tenant-managed product/service categories for DB-backed catalog operations. |
| `catalog_items` | Tenant-managed products/services with price, availability, stock, SKU, and metadata. |
| `catalog_item_variants` | Optional item variants/add-ons with SKU and stock fields. |
| `inventory_events` | Immutable stock adjustment/event trail for catalog items. |
| `payment_requests` | Tenant customer payment/deposit requests linked to customers, bookings, or orders. |
| `payment_events` | Payment lifecycle/event timeline for manual actions and future provider webhooks. |
| `reminder_rules` | Tenant follow-up/reminder templates with trigger, offset, channel, quiet-hours, and active state. |
| `scheduled_messages` | Tenant scheduled reminder/follow-up queue linked to customers, conversations, bookings, orders, payments, or future reviews. |
| `scheduled_message_events` | Scheduled message lifecycle timeline for manual and future scheduler actions. |
| `review_settings` | Tenant review collection timing, threshold, templates, public review URL, and escalation settings. |
| `review_requests` | Tenant private rating/public review requests linked to customers, conversations, bookings, or orders. |
| `review_events` | Review request lifecycle timeline for manual actions and future automation/customer events. |
| `tenant_knowledge_base` | Reviewed tenant knowledge entries with optional embeddings and enabled metadata. |
The order tables are introduced in migration `016_order_module.sql`. Migration `024_catalog_inventory.sql` adds DB-backed catalog and inventory tables. Migration `025_payment_requests.sql` adds customer payment/deposit request tracking. Migration `026_reminders_followups.sql` adds reminder rules, scheduled messages, events, and plan/tool-control seed updates. Migration `027_review_collection.sql` adds review settings, review requests, review events, and scheduled-message review links. Knowledge-base vector and lexical fallback constraints/indexes are hardened in migration `022_production_hardening.sql`.
## Public AWA Chat Tables
| Table | Purpose |
| --- | --- |
| `public_chat_links` | Tenant-created public link, slug, token hash, status, QR behavior, and metadata. |
| `public_chat_sessions` | Per-visitor public-chat sessions with token hash, customer/conversation IDs, expiry, and end timestamp. |
Public chat links are first-party browser entry points and do not require a WhatsApp provider.
## Billing, Cost, And Audit
| Table | Purpose |
| --- | --- |
| `tenant_cost_daily` | Daily message/token/credit/tool counters used by cost and usage views. |
| `audit_logs` | Security, admin, tool, config, privacy, and delivery audit entries. |
| Billing plan tables | Admin-managed plans and tenant billing state are surfaced through admin routes. |
The usage guard also relies on tenant plan/status fields and active config modules.
## Important Constraints
| Constraint area | Current behavior |
| --- | --- |
| Tenant isolation | RLS policies and API dependencies require tenant membership or super-admin access. |
| Config versions | Active tenant configs are versioned and normalized. A hardening migration enforces active-version uniqueness. |
| Booking idempotency | Booking creation has an idempotency key/index to prevent duplicate tool execution. |
| Catalog SKU uniqueness | Active catalog item and variant SKUs are unique per tenant when present. |
| Payment idempotency | Payment requests support tenant-scoped idempotency keys; provider event IDs are unique when present. |
| Reminder idempotency | Scheduled messages support tenant-scoped idempotency keys and event records for lifecycle actions. |
| Review idempotency | Review requests support tenant-scoped idempotency keys and event records for lifecycle actions. |
| Conversation identity | Migrations add active-conversation guards and sequence-number integrity. |
| Knowledge embeddings | Zero-vector embeddings are rejected/cleaned up; lexical fallback remains available. |
## Schema Ownership Rule
For new persistent behavior, update these in the same change:
1. Database schema or migration.
2. Backend DB client methods.
3. API schemas and route contracts.
4. Frontend API types/hooks when user-facing.
5. This docs folder when the behavior changes.
