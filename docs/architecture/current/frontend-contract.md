# AWA — Always Working Assistant: Frontend Contract
Last aligned with the codebase on 2026-08-04.
The frontend is a React SPA in `frontend/` built with React 19, Vite 8, TypeScript, Tailwind, shadcn/Radix UI primitives, lucide icons, TanStack Query, React Router 7, Zod, Zustand, and Recharts.
## App Structure
| Area | Location | Responsibility |
| --- | --- | --- |
| Routing | `frontend/src/App.tsx` | Public, tenant, and admin route trees. |
| API client | `frontend/src/lib/api.ts` | Axios base client, auth token injection, 401 handling. |
| Types | `frontend/src/types/` | API envelope, tenant config, domain types. |
| Layouts | `frontend/src/components/layouts/` | Tenant and admin shell/navigation. |
| Feature hooks | `frontend/src/features/*/api.ts` | TanStack Query hooks and endpoint wrappers. |
| Public chat | `frontend/src/routes/public/PublicChatPage.tsx` | Browser chat client. |
## Route Map
### Public Routes
| Route | Page |
| --- | --- |
| `/` | Landing page |
| `/login` | Login |
| `/signup` | Signup |
| `/onboarding` | Onboarding |
| `/chat/:slug` | Public AWA Chat |
### Tenant Routes
Tenant routes are wrapped in `ProtectedRoute` and `DashboardLayout`.
| Route | Roles |
| --- | --- |
| `/dashboard` | Tenant read roles |
| `/dashboard/conversations` | Tenant read roles |
| `/dashboard/conversations/:id` | Tenant read roles |
| `/dashboard/escalations` | Tenant operations roles |
| `/dashboard/knowledge` | Tenant admin roles |
| `/dashboard/bookings` | Tenant admin roles |
| `/dashboard/orders` | Tenant admin roles |
| `/dashboard/catalog` | Tenant admin roles |
| `/dashboard/payments` | Tenant admin roles |
| `/dashboard/reminders` | Tenant admin roles |
| `/dashboard/reviews` | Tenant admin roles |
| `/dashboard/customers` | Tenant admin roles |
| `/dashboard/config` | Tenant admin roles |
| `/dashboard/awa-chat` | Tenant admin roles |
| `/dashboard/whatsapp` | Tenant admin roles |
| `/dashboard/flags` | Tenant admin roles |
| `/dashboard/billing` | Tenant admin roles |
`/dashboard/ai` redirects to `/dashboard/config`.
### Admin Routes
Admin routes require `super_admin`.
| Route | Page |
| --- | --- |
| `/admin/tenants` | Tenant administration |
| `/admin/health` | Platform health |
| `/admin/prompts` | Prompt registry |
| `/admin/audit` | Audit logs |
| `/admin/privacy` | Privacy requests |
| `/admin/pricing` | Billing plans |
| `/admin/tools` | Tool controls |
## API Behavior
The Axios client:
- Uses `VITE_API_URL` when configured, otherwise same-origin.
- Attaches the access token issued for the selected auth mode. Audited production configuration uses backend-owned PostgreSQL auth.
- Supports a development mock token path.
- Clears local auth state and redirects to `/login` on 401.
- Expects the backend `APIResponse<T>` shape from `frontend/src/types/api.types.ts`.
## Navigation Behavior
Tenant navigation is role-aware and module-aware:
- Role checks decide whether a route is available.
- Required modules remain visible but show an `Off` badge when disabled.
- Feature pages are responsible for clear disabled states and enable/configuration prompts where implemented.
This is important: module-disabled pages are not globally hidden by the sidebar.
## Implemented Feature Surfaces
| Surface | Current implementation |
| --- | --- |
| Conversations | List/detail views, messages, status updates, escalation actions. |
| Escalations | Operator queue and reply/resolve/return-to-AWA flows. |
| Bookings | Booking list/detail/update plus booking configuration in dashboard. |
| Orders | Orders table/detail/actions plus catalog and rules editing. |
| Catalog | Category/item management, availability toggles, stock adjustments, and inventory event history. |
| Payments | Payment/deposit request list, manual lifecycle actions, linked order sync, and event timeline. |
| Reminders | Reminder rule management, scheduled message queue, snooze/cancel/send-state actions, and event timeline. |
| Reviews | Review settings, private rating requests, public-review routing, negative feedback escalation, and event timeline. |
| Customers | Customer list, profile editing, CRM tags, internal notes, metrics, and timeline. |
| AWA Chat | Public link CRUD, status updates, QR preview, copy/open actions. |
| Public Chat | Session creation, token persistence, message sending, SSE where available with polling fallback, quick replies, contact capture, end/start-new chat. |
| Config | Tenant business info, modules, working hours, FAQs, ignored numbers, AI settings. |
| WhatsApp | Account setup and embedded signup flow. |
| Billing | Tenant billing status and admin plan management. |
| Knowledge | Tenant knowledge-base management. |
## Known Frontend Gaps
| Gap | Current behavior |
| --- | --- |
| Public chat transport | Uses SSE where available with polling fallback; WebSocket transport is not implemented. |
| Some disabled module states | Orders has a clear disabled/enable path. Other pages may still rely on config panels or empty states. |
| Branch-specific UI | Branch-level product flows are not exposed as a complete route family. |
