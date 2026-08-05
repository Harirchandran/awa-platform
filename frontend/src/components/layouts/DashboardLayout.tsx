import { Outlet, useLocation } from 'react-router';
import { useState, Suspense } from 'react';

import { Sidebar } from './Sidebar';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { Menu } from 'lucide-react';
import { DisplayPreferencesToggle } from '@/components/accessibility/DisplayPreferences';
import { isMockAuthEnabled } from '@/lib/dev-auth';
import { useBillingStatus } from '@/features/billing/api';
import { MobileDashboardNav } from '../shared/MobileDashboardNav';

const routeTitles: Record<string, string> = {
  '/dashboard': 'Overview',
  '/dashboard/conversations': 'Conversations',
  '/dashboard/escalations': 'Needs Reply',
  '/dashboard/knowledge': 'Knowledge',
  '/dashboard/tools': 'Workflow Tools',
  '/dashboard/bookings': 'Bookings',
  '/dashboard/leads': 'Leads',
  '/dashboard/orders': 'Orders',
  '/dashboard/catalog': 'Catalog',
  '/dashboard/payments': 'Payments',
  '/dashboard/reminders': 'Reminders',
  '/dashboard/reviews': 'Reviews',
  '/dashboard/customers': 'Customers',
  '/dashboard/config': 'Business Setup',
  '/dashboard/awa-chat': 'AWA Chat',
  '/dashboard/whatsapp': 'WhatsApp',
  '/dashboard/flags': 'Advanced Controls',
  '/dashboard/billing': 'Billing',
};

export function DashboardLayout() {
  const location = useLocation();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const { data: billing } = useBillingStatus();

  const pageTitle = Object.entries(routeTitles).find(([path]) =>
    location.pathname === path || (path !== '/dashboard' && location.pathname.startsWith(path))
  )?.[1] || 'Dashboard';

  const badges = getEnvironmentBadges(billing?.billing_mode, billing?.mock_billing);

  return (
    <div className="flex min-h-screen md:h-[100dvh] bg-slate-50 dark:bg-background md:overflow-hidden relative">
      {/* Skip Link */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-[100] focus:px-4 focus:py-2 focus:bg-primary focus:text-primary-foreground focus:rounded-md focus:shadow-lg focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2"
      >
        Skip to main content
      </a>

      <Sidebar className="hidden md:flex" />
      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-[56px] md:h-[52px] flex items-center gap-3 px-4 md:px-6 border-b border-border shrink-0 bg-background/95 backdrop-blur">
          <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="md:hidden h-9 w-9" aria-label="Open navigation">
                <Menu className="h-4 w-4" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-[280px] p-0">
              <SheetTitle className="sr-only">Dashboard navigation</SheetTitle>
              <Sidebar className="w-full border-r-0" onNavigate={() => setMobileNavOpen(false)} />
            </SheetContent>
          </Sheet>
          <h1 className="truncate text-[13px] font-medium text-foreground">{pageTitle}</h1>
          {badges.length > 0 && (
            <div className="flex min-w-0 items-center gap-1.5">
              {badges.map((badge, index) => (
                <span
                  key={badge}
                  className={`rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[11px] font-medium text-amber-900 dark:text-amber-100 ${index === 0 ? 'inline-block' : 'hidden md:inline-block'}`}
                >
                  {badge}
                </span>
              ))}
            </div>
          )}
          <div className="ml-auto">
            <DisplayPreferencesToggle mode="inline" />
          </div>
        </header>
        <main id="main-content" className="flex-1 md:h-full md:overflow-y-auto pb-16 md:pb-0 outline-none" tabIndex={-1}>
          <Suspense fallback={
            <div className="flex h-[50vh] w-full items-center justify-center">
              <div className="h-6 w-6 animate-spin rounded-full border-b-2 border-emerald-600"></div>
            </div>
          }>
            <div className="w-full max-w-[1440px] mx-auto p-4 sm:p-5 md:p-6">
              <Outlet />
            </div>
          </Suspense>
        </main>
      </div>
      <MobileDashboardNav onOpenMore={() => setMobileNavOpen(true)} />
    </div>
  );
}

function getEnvironmentBadges(billingMode?: string, mockBilling?: boolean) {
  const appMode = (
    import.meta.env.VITE_APP_ENV ||
    import.meta.env.VITE_AWA_INTEGRATION_MODE ||
    import.meta.env.MODE ||
    ''
  ).toLowerCase();
  const whatsappMode = (import.meta.env.VITE_WHATSAPP_MODE || import.meta.env.VITE_AWA_INTEGRATION_MODE || '').toLowerCase();
  const badges: string[] = [];

  if (appMode === 'production' || appMode === 'live') {
    badges.push('Production');
  } else if (appMode === 'staging') {
    badges.push('Staging Pilot');
  } else {
    badges.push('Local Demo');
  }

  if (isMockAuthEnabled()) badges.push('Mock Auth');
  if (mockBilling || billingMode === 'mock') badges.push('Mock Billing');
  if (whatsappMode === 'test') badges.push('Test WhatsApp');

  return badges.slice(0, 4);
}
