# AWA — Always Working Assistant: Tenant Config Contract
Last aligned with the codebase on 2026-08-04.
Tenant behavior is controlled by the active `tenant_configs.config_json` document. Backend validation lives in `backend/schemas/config.py`, with normalization helpers used by config APIs, onboarding, and admin tool controls.
## Top-Level Shape
The active config includes:
| Field | Purpose |
| --- | --- |
| `business_name` | Display/business name used in prompts and public chat. |
| `business_type` | Business category, also used by tool-control presets. |
| `timezone` | Tenant timezone. |
| `default_language` | Default customer language. |
| `ai_mode` | `standard` or `concierge`. Concierge requires a plan that permits it. |
| `ai_tone` | Tenant tone guidance. |
| `greeting_message` | Initial message fallback. |
| `fallback_message` | Fallback when automation cannot help. |
| `escalation_message` | Message shown when escalating to a human. |
| `working_hours` | Weekly open/close rules. |
| `services` | Booking service catalog. |
| `modules` | Enabled tool/module configuration. |
| `faqs` | Configured FAQ pairs. |
| `escalation` | Human handoff contacts/rules. |
| `custom_instructions` | Tenant-specific AI instructions. |
| `ignored_numbers` | WhatsApp numbers ignored by inbound processing. |
| `limits` | Tenant-specific operational limits. |
| `booking_settings` | Booking collection, advanced questions, reminders, and rules settings. |
Tenant prompt fields are validated against obvious prompt-injection patterns before being accepted.
## Module Configuration
The canonical module keys are:
| Module | Purpose |
| --- | --- |
| `booking` | Service appointment collection and booking creation. |
| `order` | Config-catalog order capture and order creation. |
| `payments` | Customer payment/deposit request management. |
| `reminders` | Reminder rules and scheduled follow-up message management. |
| `reviews` | Private rating collection and public-review routing. |
| `lead_capture` | Lead field collection into customer metadata. |
| `support` | Support capture and escalation. |
| `faq` | FAQ/knowledge answer flow. |
| `human_handoff` | Escalation to operators. |
Admin tool controls and billing plan policies decide which modules are allowed. Tenant config decides which allowed modules are enabled.
## Booking Config
Booking uses:
- `services`: service IDs/names/durations/prices.
- `booking_settings`: default duration, buffer, cancellation policy, required fields, custom questions, reminders, and advanced rules.
- `working_hours`: high-level operating schedule.
Current booking behavior validates required field formats and creates booking records. It does not yet enforce branch capacity, external calendar availability, or automatic reminder creation.
## Order Config
The order module reads catalog and rules from `modules.order.config`.
Important fields:
| Field | Purpose |
| --- | --- |
| `currency` | Display/cost currency. |
| `fulfillment_types` | Supported fulfillment modes such as pickup or delivery. |
| `payment_methods` | Allowed payment methods. |
| `requires_confirmation` | Whether orders require customer confirmation before execution. |
| `categories` | Catalog categories with items, prices, descriptions, and availability. |
Orders copy item snapshots into `order_items` so historical orders are not rewritten when the config catalog changes.
## AI Mode
| Mode | Runtime behavior |
| --- | --- |
| `standard` | Deterministic router/workflow engine handles module flows. |
| `concierge` | Agentic engine can handle broader conversation while respecting tool, plan, usage, and safety guards. |
## Config Versioning
Config APIs create and activate versions. Runtime processing uses the active version recorded in canonical messages and conversation/session state. When tool controls change allowed/enabled modules, they update the normalized tenant config rather than maintaining a separate product truth.
