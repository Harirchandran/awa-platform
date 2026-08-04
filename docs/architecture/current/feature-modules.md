# AWA — Always Working Assistant: Feature Modules
Last aligned with the codebase on 2026-08-04.
This document summarizes the implemented feature modules and the parts that remain future work.
## Status Matrix
| Module | Status | Current scope |
| --- | --- | --- |
| Booking | Implemented core flow | Collect service/date/time/name, validate fields, create bookings idempotently, list/update in dashboard. |
| Order | Implemented core flow | Config catalog fallback plus DB catalog item matching, quantity/fulfillment/customer fields, create orders/items/events, dashboard order management. |
| Catalog / Inventory | Implemented MVP | Tenant categories/items, price/SKU/availability/stock fields, inventory event history, dashboard management, order execution lookup. |
| Payments / Deposits | Implemented MVP | Tenant payment requests, manual/external provider tracking, lifecycle actions, order payment status sync, dashboard management. |
| Reminders / Follow-Ups | Implemented MVP | Reminder rules, scheduled message queue, manual scheduling, snooze/cancel/send-state actions, event timeline, dashboard management. |
| Review Collection | Implemented MVP | Review settings, manual/private rating requests, public-review routing, negative feedback escalation, event timeline, dashboard management. |
| Lead capture | Implemented | Collects lead fields and writes customer metadata. |
| Support | Implemented | Captures support request and escalates. |
| FAQ | Implemented | Configured FAQs are matched directly from tenant config, with saved escalation knowledge as fallback. |
| Human handoff | Implemented | Escalation, human replies, resolve, return to automation, optional knowledge save. |
| AWA Chat | Implemented | Public browser chat links, sessions, QR, SSE where available with polling fallback, contact capture, human replies. |
| Tool controls | Implemented | Super-admin presets, plan-aware module availability, tenant config updates. |
| Billing guard | Implemented | Plan/module/mode/message/token/credit checks and tenant billing status. |
| CRM Lite | Implemented MVP | Customer metrics, tags, internal notes, privacy export coverage, timeline API, and dashboard CRM tab. |
## Booking Module
Code:
- `backend/modules/booking_module.py`
- `frontend/src/routes/dashboard/BookingsPage.tsx`
- `frontend/src/features/bookings/`
Current behavior:
- Uses tenant `services` and `booking_settings`.
- Collects `service_id`, `preferred_date`, `preferred_time`, and `customer_name` by default.
- Handles fuzzy date/time terms and prevents past dates.
- Creates booking records with an idempotency key.
- Supports dashboard listing, detail, and status updates.
Future work:
- Branch/capacity-aware availability.
- Calendar integrations.
- Reminder scheduler.
- Full booking reschedule/cancel automation.
## Order Module
Code:
- `backend/modules/order_module.py`
- `backend/api/routes/orders.py`
- `frontend/src/routes/dashboard/OrdersPage.tsx`
- `frontend/src/features/orders/`
Current behavior:
- Reads catalog categories/items from `modules.order.config`.
- During order execution, falls back to DB-backed active and available catalog items when config matching does not find an item.
- Collects item, quantity, fulfillment type, customer name, phone, and delivery address when needed.
- Creates `orders` and `order_items` rows idempotently.
- Emits order events and supports dashboard status/actions.
- Provides catalog and rule editing in the tenant dashboard.
Future work:
- Chat-triggered payment collection and customer payment messaging.
- Async workflow option loading from DB catalog so collection prompts can list DB catalog items.
- Branch-specific fulfillment routing.
- Delivery-provider integrations.
## Catalog / Inventory
Code:
- `backend/api/routes/catalog.py`
- `backend/db/sql/migrations/024_catalog_inventory.sql`
- `frontend/src/routes/dashboard/CatalogPage.tsx`
- `frontend/src/features/catalog/`
Current behavior:
- Tenant admins can create/update/delete categories and items.
- Items support price, currency, SKU, description, image URL, active state, availability state, stock tracking, stock quantity, and low-stock threshold.
- Stock adjustments append `inventory_events` and update item stock.
- Dashboard route `/dashboard/catalog` provides category management, item management, availability toggles, and recent stock event history.
- Order execution can resolve a DB catalog item if the older config catalog does not match.
Future work:
- One-time import from `modules.order.config.categories`.
- Variant/add-on management UI and APIs.
- Branch-specific stock and stock reservations.
- Automated decrement/reservation rules tied to order lifecycle and payments.
## Payments / Deposits
Code:
- `backend/api/routes/payments.py`
- `backend/db/sql/migrations/025_payment_requests.sql`
- `frontend/src/routes/dashboard/PaymentsPage.tsx`
- `frontend/src/features/payments/`
Current behavior:
- Tenant admins can enable the `payments` module and manage `/dashboard/payments`.
- Payment requests can link to customers, bookings, and orders; order-linked requests can infer amount/currency from the order.
- Requests support manual, external, Razorpay, UPI, cash, and bank-transfer provider labels.
- Configured Razorpay customer payment-link creation is implemented, but the provider path is not independently verified as production-ready.
- Lifecycle actions record events: send, cancel, mark paid, mark failed, mark refunded, and expire.
- Linked orders sync `payment_status` to pending, paid, failed, refunded, or not collected according to the action.
- Starter and Pro plan policies include the `payments` module; trial remains without payments by default.
Future work:
- Razorpay customer-payment webhook routing separate from subscription billing events.
- Chat-triggered deposit/order payment requests.
- Receipt/customer notification templates.
- Refund and partial-refund provider reconciliation.
## Reminders / Follow-Ups
Code:
- `backend/api/routes/reminders.py`
- `backend/db/sql/migrations/026_reminders_followups.sql`
- `frontend/src/routes/dashboard/RemindersPage.tsx`
- `frontend/src/features/reminders/`
Current behavior:
- Tenant admins can enable the `reminders` module and manage `/dashboard/reminders`.
- Reminder rules define trigger type, offset minutes, channel, active state, optional quiet-hours fields, and template text.
- Scheduled messages can link to customers, conversations, bookings, orders, payments, and future review requests.
- The dashboard supports scheduled message filtering, manual scheduling, due/failed/scheduled metrics, and rule management.
- Lifecycle actions record events: cancel, snooze, mark sent, mark failed, skip, mark due, and retry.
- The reminder scheduler claims due messages and supports configured WhatsApp delivery; email is a non-delivering stub and SMS is not connected.
- Starter and Pro plan policies include the `reminders` module; trial remains without reminders by default.
Future work:
- Automatic message creation from booking, payment, order, inactive conversation, and review events.
- Complete channel delivery coverage, including email and SMS.
- Linked-record cancellation hooks for cancelled bookings, paid payments, completed/skipped reviews, and closed conversations.
- Channel reachability checks and fallback behavior for WhatsApp and AWA Chat delivery.
## Review Collection
Code:
- `backend/api/routes/reviews.py`
- `backend/db/sql/migrations/027_review_collection.sql`
- `frontend/src/routes/dashboard/ReviewsPage.tsx`
- `frontend/src/features/reviews/`
Current behavior:
- Tenant admins can enable the `reviews` module and manage `/dashboard/reviews`.
- Review settings store public review URL, booking/order trigger toggles, delay minutes, positive-rating threshold, templates, and negative feedback escalation toggle.
- Review requests can link to customers, conversations, bookings, and orders.
- Lifecycle actions record events: send, submit rating, request public review, escalate negative feedback, skip, expire, and reopen.
- Positive ratings route to `public_review_requested` when a public review URL is available; lower ratings route to `negative_feedback_escalated` when enabled.
- Scheduled messages now support `review_request_id` links for delayed review asks once the reminder worker is implemented.
- Starter and Pro plan policies include the `reviews` module; trial remains without reviews by default.
Future work:
- Automatic review request creation after completed bookings and delivered/completed orders.
- Customer-facing chat flow for submitting ratings and feedback without dashboard operators entering them manually.
- Review reminder creation through the scheduled-message worker.
- Negative feedback integration with escalation/support queues.
- Public review click tracking and conversion analytics.
## AWA Chat
Code:
- `backend/api/routes/awa_chat.py`
- `backend/api/routes/public_chat.py`
- `frontend/src/routes/dashboard/AwaChatPage.tsx`
- `frontend/src/routes/public/PublicChatPage.tsx`
Current behavior:
- Tenants create public chat links with slugs and statuses.
- QR endpoints return SVG.
- Public users create tokenized sessions.
- Messages are processed through the same canonical worker path as WhatsApp.
- Bot and human replies are persisted and delivered through SSE where available, with polling fallback.
- Contact capture can update customer name, phone, and email.
Future work:
- Link rotation UI/action.
- WebSocket transport.
- Independent verification of live CAPTCHA/OTP provider behavior.
## Escalation And Knowledge
Escalation is implemented across conversation APIs and dashboard views:
- Manual escalation.
- Operator reply.
- Resolve.
- Return to AWA.
- Optional save-to-knowledge.
- Public chat delivery fallback when a session is no longer reachable.
Knowledge base search uses embeddings when available and lexical fallback when embeddings are missing or unusable. Current knowledge-base rows are created from tenant escalation replies saved by an operator; configured FAQs remain in tenant config and are not automatically embedded.
## CRM Lite
Code:
- `backend/api/routes/customers.py`
- `backend/db/sql/migrations/023_crm_lite.sql`
- `frontend/src/features/customers/`
Current behavior:
- Customer list/detail include conversation, booking, order, order-value, metadata, and tag context.
- Tenant admins can create tags, attach/detach tags, and create/delete internal notes.
- Customer timeline combines notes, conversations, bookings, and orders.
- Privacy export includes customer tags and notes; erase counts CRM notes/tag links before customer deletion.
- Dashboard customer profile has Profile, CRM, and Timeline tabs.
Future work:
- Customer lifecycle/status field.
- Duplicate customer merge.
- Saved segments and bulk actions.
- Automation-safe CRM memory rules for public chat identity verification.
## Billing And Tool Controls
The usage guard enforces plan policies:
| Plan | Key current limits |
| --- | --- |
| trial | 300 monthly messages, 50 daily messages, standard mode, booking/order/faq/handoff. |
| starter | 1000 monthly messages, 150 daily messages, standard mode, booking/order/payments/reminders/reviews/faq/handoff. |
| pro | 5000 monthly messages, 750 daily messages, standard/concierge, adds lead/support on top of starter tools. |
| enterprise | Unlimited policy values, wildcard modules. |
Admin tool controls expose presets for common business types and update tenant config modules while respecting plan-allowed modules.
