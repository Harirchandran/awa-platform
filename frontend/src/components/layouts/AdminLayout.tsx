import { Outlet, useLocation } from 'react-router';
import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { AdminSidebar } from './AdminSidebar';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { Menu } from 'lucide-react';
import { DisplayPreferencesToggle } from '@/components/accessibility/DisplayPreferences';
const routeTitles: Record<string, string> = {
  '/admin/tenants': 'Tenants',
  '/admin/prompts': 'AI Prompts',
  '/admin/pricing': 'Billing Plans',
  '/admin/tools': 'Tool Controls',
  '/admin/health': 'Platform Health',
  '/admin/audit': 'Audit Logs',
  '/admin/privacy': 'Privacy Requests',
};
export function AdminLayout() {
  const location = useLocation();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const pageTitle = Object.entries(routeTitles).find(([path]) =>
    location.pathname === path || location.pathname.startsWith(path)
  )?.[1] || 'Administration';
  return (
    <div className="flex min-h-screen md:h-[100dvh] bg-background md:overflow-hidden">
      {/* Skip Link */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-[100] focus:px-4 focus:py-2 focus:bg-primary focus:text-primary-foreground focus:rounded-md focus:shadow-lg focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2"
      >
        Skip to main content
      </a>

      <AdminSidebar className="hidden md:flex" />
      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-[56px] md:h-[52px] flex items-center gap-3 px-4 md:px-6 border-b border-border shrink-0 bg-background/95 backdrop-blur">
          <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="md:hidden h-9 w-9" aria-label="Open navigation">
                <Menu className="h-4 w-4" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-[280px] p-0">
              <SheetTitle className="sr-only">Admin navigation</SheetTitle>
              <AdminSidebar className="w-full border-r-0" onNavigate={() => setMobileNavOpen(false)} />
            </SheetContent>
          </Sheet>
          <h1 className="truncate text-[13px] font-medium text-foreground">{pageTitle}</h1>
          <div className="ml-auto">
            <DisplayPreferencesToggle mode="inline" />
          </div>
        </header>
        <main id="main-content" className="flex-1 md:h-full md:overflow-y-auto outline-none" tabIndex={-1}>
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
              className="motion-page p-4 sm:p-5 md:p-8 mx-auto w-full max-w-[1400px]"
            >
              <Outlet />
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}
