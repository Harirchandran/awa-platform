# AWA — Always Working Assistant: Runtime Pipeline
Last aligned with the codebase on 2026-08-04.
The runtime path is built around canonical message events, Redis buffering/queueing, and a worker that either runs the deterministic workflow engine or the concierge agentic engine.
## Canonical Message Contract
`backend/schemas/canonical_message.py` defines canonical message version 2.
Key fields:
| Field | Purpose |
| --- | --- |
| `schema_version` | Currently `2`. |
| `event_id` | Idempotency key across webhook/public-chat processing. |
| `source` | `webhook`, `awa_chat`, `scheduler`, `manual`, or `internal_event`. |
| `channel_type` | `whatsapp` or `awa_chat`. |
| `tenant_id` | Tenant isolation boundary. |
| `whatsapp_account_id` | WhatsApp account when the message came from Meta. |
| `channel_account_id` | Channel account/link identifier. |
| `session_id` | Public chat session or conversation session reference. |
| `conversation_id` | Conversation row used for continuity. |
| `customer_id` | Customer row. |
| `config_version` | Active tenant config version used by the worker. |
| `processing_metadata` | Retry count, priority, source metadata, and timestamps. |
| `trace` | Request/trace correlation. |
## WhatsApp Inbound Flow
1. Meta calls `POST /api/v1/webhook`.
2. The webhook validates `X-Hub-Signature-256` with `WHATSAPP_APP_SECRET`, applies replay protection, resolves the tenant by phone number ID or display phone, and rejects inactive/trial-expired tenants.
3. Status events update message delivery state and audit failures.
4. Message events load the active tenant config, apply ignored-number checks, get or create the customer and conversation, and build a canonical WhatsApp event.
5. The event enters the burst buffer so quick consecutive customer messages can be combined before processing.
6. The burst worker publishes the canonical event to the reliable queue.
## Public AWA Chat Inbound Flow
1. The browser loads `GET /api/v1/public-chat/links/{slug}` and then creates a session with `POST /api/v1/public-chat/links/{slug}/sessions`.
2. Public chat validates the slug-bound token hash and checks link and tenant status.
3. A session token is returned to the browser and stored client-side for subsequent requests.
4. `POST /api/v1/public-chat/sessions/{session_id}/messages` persists the inbound public-chat message and publishes a canonical `awa_chat` event directly to the reliable queue.
5. The browser uses `GET /api/v1/public-chat/sessions/{session_id}/events` for SSE where available and polls `POST /api/v1/public-chat/sessions/{session_id}/messages/list` as a fallback and safety check.
Public chat does not use Meta or WhatsApp provider IDs. Outbound replies are stored in `messages` and delivered through SSE where available, with polling fallback.
## Reliable Redis Queue
`backend/pipeline/reliable_queue.py` owns the queue. The implementation uses a processing list and a deadline sorted set instead of a destructive pop-only pattern.
| Key | Purpose |
| --- | --- |
| `message_queue` | Ready queue. |
| `message_queue:processing` | Claimed but not yet acknowledged events. |
| `message_queue:processing_deadlines` | Visibility-timeout deadlines for claimed events. |
| `dlq` | Dead-letter events after retry exhaustion or malformed payloads. |
| `processed:{tenant_id}:{event_id}` | Idempotency marker for successfully processed events. |
Important behavior:
- The worker claims with `BRPOPLPUSH`, then acknowledges with `LREM` and removes the deadline.
- Stale processing entries are recovered every 30 seconds and requeued.
- Default visibility timeout is 300 seconds.
- Retry backoff uses `2 ** retry_count`.
- Events that exceed retry limits are moved to the dead-letter queue with metadata.
## Worker Processing
`backend/pipeline/worker.py` performs the main runtime loop:
1. Recover stale queue entries.
2. Claim one canonical event.
3. Validate schema and idempotency.
4. Load tenant, active config, customer, conversation, and conversation session.
5. Persist the inbound message.
6. Choose deterministic workflow or concierge agent:
   - Deterministic workflow is used for standard mode and for explicit module flows.
   - Concierge mode can use the agentic engine for broader conversation, while still respecting tool and usage guards.
7. Update conversation/session state.
8. Dispatch the response:
   - For `awa_chat`, persist an outbound `messages` row for browser polling.
   - For `whatsapp`, send text or interactive payloads through the WhatsApp client and persist provider metadata.
9. Acknowledge the queue entry only after processing succeeds.
## Intent And Workflow
The router and workflow engine live in:
- `backend/pipeline/message_router.py`
- `backend/logic/engine/core.py`
- `backend/core/states.py`
State values include:
`NEW`, `INTENT_IDENTIFIED`, `COLLECTING_DATA`, `WAITING_CUSTOMER`, `VALIDATING`, `EXECUTING`, `COMPLETED`, `FAILED`, `CANCELLED`, and `ESCALATED`.
Key behavior:
- Booking and order intents are detected from tenant config and user text.
- Order detection only routes when the order module is enabled and text matches order keywords or configured catalog items.
- FAQ can answer from configured tenant FAQs directly. If no configured FAQ matches, reviewed tenant knowledge saved from escalation replies can be searched through the tenant knowledge base.
- Prompt-security, urgency, repeated confusion, or unsupported requests can trigger escalation.
- Completed terminal states are reset for the next inbound customer message so the conversation can continue naturally.
## Knowledge Sources
Current runtime knowledge is split across two paths:
| Source | Current storage | Current runtime behavior |
| --- | --- | --- |
| Configured FAQs | Active `tenant_configs.config_json.faqs` | Matched directly from tenant config and included in concierge prompt context. These FAQs are not automatically embedded into `tenant_knowledge_base`. |
| Services, hours, profile, and tool settings | Active tenant config | Included as structured tenant context for deterministic and concierge replies. |
| Saved escalation answers | `tenant_knowledge_base` | Created only when a tenant operator replies to an escalation with save-to-knowledge enabled. Search uses embeddings when available and lexical fallback otherwise. |
| Websites and uploaded documents | Not implemented | No first-party ingestion path currently converts these sources into searchable tenant knowledge. |
Website and document ingestion remains future work and is not part of this staging snapshot.
## Outbound Dispatch
| Channel | Dispatch behavior |
| --- | --- |
| WhatsApp | Sends through Graph API using the tenant WhatsApp account, then stores outbound message and provider ID. Interactive payloads fall back to plain text when needed. |
| AWA Chat | Stores outbound messages in PostgreSQL and signals the browser through SSE where available, with polling fallback. |
| Human escalation replies | Tenant operators can reply through conversation APIs. AWA Chat replies persist for SSE or polling delivery; WhatsApp replies go through provider delivery. |
## Burst Buffer
`backend/pipeline/burst_buffer.py` and `backend/pipeline/burst_worker.py` implement a short sliding window so rapid customer messages are processed as one combined input. This reduces fragmented state transitions such as a customer sending service, date, and time in separate quick messages.
