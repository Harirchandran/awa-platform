import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { lazy, Suspense } from 'react';
import { ErrorBoundary } from 'react-error-boundary';
import { GlobalErrorFallback } from './components/shared/GlobalErrorFallback';
import { ProtectedRoute } from './components/router/ProtectedRoute';
import { RoleRoute } from './components/router/RoleRoute';
import { DisplayPreferencesToggle } from './components/accessibility/DisplayPreferences';
import { AwaSplash } from './components/brand/AwaSplash';
import { TooltipProvider } from './components/ui/tooltip';
import {
  SUPER_ADMIN_ROLES,
  TENANT_ADMIN_ROLES,
  TENANT_OPERATION_ROLES,
  TENANT_READ_ROLES,
} from './features/auth/roles';
import { DashboardLayout } from './components/layouts/DashboardLayout';
import { AdminLayout } from './components/layouts/AdminLayout';
const LandingPage = lazy(() => import('./routes/LandingPage').then((module) => ({ default: module.LandingPage })));
const LoginPage = lazy(() => import('./routes/auth/LoginPage').then((module) => ({ default: module.LoginPage })));
const SignupPage = lazy(() => import('./routes/auth/SignupPage').then((module) => ({ default: module.SignupPage })));
const AccountSuspendedPage = lazy(() => import('./routes/auth/AccountSuspendedPage').then((module) => ({ default: module.AccountSuspendedPage })));
const OnboardingPage = lazy(() => import('./routes/onboarding/OnboardingPage').then((module) => ({ default: module.OnboardingPage })));
const PublicChatPage = lazy(() => import('./routes/public/PublicChatPage').then((module) => ({ default: module.PublicChatPage })));
const OverviewPage = lazy(() => import('./routes/dashboard/OverviewPage').then((module) => ({ default: module.OverviewPage })));
const ConversationsPage = lazy(() => import('./routes/dashboard/ConversationsPage').then((module) => ({ default: module.ConversationsPage })));
const ConversationPage = lazy(() => import('./routes/dashboard/ConversationPage').then((module) => ({ default: module.ConversationPage })));
const EscalationsPage = lazy(() => import('./routes/dashboard/EscalationsPage').then((module) => ({ default: module.EscalationsPage })));
const KnowledgePage = lazy(() => import('./routes/dashboard/KnowledgePage').then((module) => ({ default: module.KnowledgePage })));
const ToolsPage = lazy(() => import('./routes/dashboard/ToolsPage').then((module) => ({ default: module.ToolsPage })));
const BookingsPage = lazy(() => import('./routes/dashboard/BookingsPage').then((module) => ({ default: module.BookingsPage })));
const LeadsPage = lazy(() => import('./routes/dashboard/LeadsPage').then((module) => ({ default: module.LeadsPage })));
const OrdersPage = lazy(() => import('./routes/dashboard/OrdersPage').then((module) => ({ default: module.OrdersPage })));
const CatalogPage = lazy(() => import('./routes/dashboard/CatalogPage').then((module) => ({ default: module.CatalogPage })));
const PaymentsPage = lazy(() => import('./routes/dashboard/PaymentsPage').then((module) => ({ default: module.PaymentsPage })));
const RemindersPage = lazy(() => import('./routes/dashboard/RemindersPage').then((module) => ({ default: module.RemindersPage })));
const ReviewsPage = lazy(() => import('./routes/dashboard/ReviewsPage').then((module) => ({ default: module.ReviewsPage })));
const CustomersPage = lazy(() => import('./routes/dashboard/CustomersPage').then((module) => ({ default: module.CustomersPage })));
const ConfigPage = lazy(() => import('./routes/dashboard/ConfigPage').then((module) => ({ default: module.ConfigPage })));
const AwaChatPage = lazy(() => import('./routes/dashboard/AwaChatPage').then((module) => ({ default: module.AwaChatPage })));
const WhatsAppPage = lazy(() => import('./routes/dashboard/WhatsAppPage').then((module) => ({ default: module.WhatsAppPage })));
const FlagsPage = lazy(() => import('./routes/dashboard/FlagsPage').then((module) => ({ default: module.FlagsPage })));
const BillingPage = lazy(() => import('./routes/dashboard/BillingPage').then((module) => ({ default: module.BillingPage })));
const TenantsPage = lazy(() => import('./routes/admin/TenantsPage').then((module) => ({ default: module.TenantsPage })));
const HealthPage = lazy(() => import('./routes/admin/HealthPage').then((module) => ({ default: module.HealthPage })));
const PromptsPage = lazy(() => import('./routes/admin/PromptsPage').then((module) => ({ default: module.PromptsPage })));
const AuditPage = lazy(() => import('./routes/admin/AuditPage').then((module) => ({ default: module.AuditPage })));
const PrivacyPage = lazy(() => import('./routes/admin/PrivacyPage').then((module) => ({ default: module.PrivacyPage })));
const PricingPage = lazy(() => import('./routes/admin/PricingPage').then((module) => ({ default: module.PricingPage })));
const ToolControlsPage = lazy(() => import('./routes/admin/ToolControlsPage').then((module) => ({ default: module.ToolControlsPage })));
function RouteFallback() {
  return <AwaSplash />;
}
function RouteDisplayPreferencesToggle() {
  const { pathname } = useLocation();
  const workspaceRoute = pathname.startsWith('/dashboard') || pathname.startsWith('/admin');
  const publicChatRoute = pathname.startsWith('/chat/');
  return workspaceRoute || publicChatRoute ? null : <DisplayPreferencesToggle />;
}
export default function App() {
  return (
    <ErrorBoundary FallbackComponent={GlobalErrorFallback}>
      <TooltipProvider delayDuration={400}>
        <BrowserRouter>
          <Suspense fallback={<RouteFallback />}>
            <Routes>
            {/* Public Routes */}
            <Route path="/" element={<LandingPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/signup" element={<SignupPage />} />
            <Route path="/account-suspended" element={<AccountSuspendedPage />} />
            <Route path="/chat/:slug" element={<PublicChatPage />} />
          {/* Tenant Dashboard */}
          <Route element={<ProtectedRoute />}>
            {/* C-3: /onboarding requires authentication */}
            <Route path="/onboarding" element={<OnboardingPage />} />
            <Route element={<RoleRoute roles={TENANT_READ_ROLES} />}>
              <Route element={<DashboardLayout />}>
                <Route path="/dashboard" element={<OverviewPage />} />
                <Route path="/dashboard/conversations" element={<ConversationsPage />} />
                <Route path="/dashboard/conversations/:id" element={<ConversationPage />} />
                <Route element={<RoleRoute roles={TENANT_OPERATION_ROLES} fallbackPath="/dashboard" />}>
                  <Route path="/dashboard/escalations" element={<EscalationsPage />} />
                </Route>
                <Route element={<RoleRoute roles={TENANT_ADMIN_ROLES} fallbackPath="/dashboard" />}>
                  <Route path="/dashboard/knowledge" element={<KnowledgePage />} />
                  <Route path="/dashboard/tools" element={<ToolsPage />} />
                  <Route path="/dashboard/bookings" element={<BookingsPage />} />
                  <Route path="/dashboard/leads" element={<LeadsPage />} />
                  <Route path="/dashboard/orders" element={<OrdersPage />} />
                  <Route path="/dashboard/catalog" element={<CatalogPage />} />
                  <Route path="/dashboard/payments" element={<PaymentsPage />} />
                  <Route path="/dashboard/reminders" element={<RemindersPage />} />
                  <Route path="/dashboard/reviews" element={<ReviewsPage />} />
                  <Route path="/dashboard/customers" element={<CustomersPage />} />
                  <Route path="/dashboard/config" element={<ConfigPage />} />
                  <Route path="/dashboard/ai" element={<Navigate to="/dashboard/config" replace />} />
                  <Route path="/dashboard/awa-chat" element={<AwaChatPage />} />
                  <Route path="/dashboard/whatsapp" element={<WhatsAppPage />} />
                  <Route path="/dashboard/flags" element={<FlagsPage />} />
                  <Route path="/dashboard/billing" element={<BillingPage />} />
                </Route>
              </Route>
            </Route>
          </Route>
          {/* Super Admin */}
          <Route element={<ProtectedRoute />}>
            <Route element={<RoleRoute roles={SUPER_ADMIN_ROLES} />}>
              <Route element={<AdminLayout />}>
                <Route path="/admin/tenants" element={<TenantsPage />} />
                <Route path="/admin/health" element={<HealthPage />} />
                <Route path="/admin/prompts" element={<PromptsPage />} />
                <Route path="/admin/audit" element={<AuditPage />} />
                <Route path="/admin/privacy" element={<PrivacyPage />} />
                <Route path="/admin/pricing" element={<PricingPage />} />
                <Route path="/admin/tools" element={<ToolControlsPage />} />
              </Route>
            </Route>
          </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          <RouteDisplayPreferencesToggle />
        </Suspense>
      </BrowserRouter>
      </TooltipProvider>
    </ErrorBoundary>
  );
}
