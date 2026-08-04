import { Link, useLocation } from 'react-router-dom';
import { LayoutDashboard, MessageSquare, Briefcase, MoreHorizontal } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTenantTools } from '@/features/tools/api';

interface MobileDashboardNavProps {
  onOpenMore: () => void;
}

export function MobileDashboardNav({ onOpenMore }: MobileDashboardNavProps) {
  const location = useLocation();
  const pathname = location.pathname;
  const { data: toolsData } = useTenantTools();

  const getOperationsHref = () => {
    if (!toolsData?.tools) return '/dashboard';
    const targetKeys = ['booking', 'order', 'payments'];
    for (const key of targetKeys) {
      const tool = toolsData.tools.find((t) => t.key === key && t.runtime_available);
      if (tool && tool.route_path) {
        return tool.route_path;
      }
    }
    return '/dashboard';
  };

  const operationsHref = getOperationsHref();

  const items = [
    { href: '/dashboard', label: 'Home', icon: LayoutDashboard, exact: true },
    { href: '/dashboard/conversations', label: 'Inbox', icon: MessageSquare, exact: false },
    { href: operationsHref, label: 'Operations', icon: Briefcase, exact: false },
  ];

  return (
    <nav className="md:hidden fixed bottom-0 left-0 right-0 h-16 border-t border-border bg-background/95 backdrop-blur z-40 flex items-center justify-around px-2 pb-safe">
      {items.map((item) => {
        const isActive = item.exact
          ? pathname === item.href
          : pathname === item.href || pathname.startsWith(`${item.href}/`);

        return (
          <Link
            key={item.label}
            to={item.href}
            className="flex flex-col items-center justify-center flex-1 h-full outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-md transition-colors"
          >
            <item.icon className={cn("h-5 w-5", isActive ? "text-primary" : "text-muted-foreground")} aria-hidden="true" />
            <span className={cn("text-[10px] mt-1 font-medium", isActive ? "text-primary" : "text-muted-foreground")}>
              {item.label}
            </span>
          </Link>
        );
      })}

      <button
        type="button"
        onClick={onOpenMore}
        className="flex flex-col items-center justify-center flex-1 h-full outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-md transition-colors"
        aria-label="Open settings and setup menu"
      >
        <MoreHorizontal className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
        <span className="text-[10px] mt-1 font-medium text-muted-foreground">More</span>
      </button>
    </nav>
  );
}
