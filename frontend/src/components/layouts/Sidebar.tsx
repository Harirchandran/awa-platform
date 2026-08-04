import { useLocation } from 'react-router-dom';
import { useCurrentUser } from '@/features/auth/hooks';
import { authClient } from '@/lib/auth';
import { clearDevMockToken, getDevMockToken } from '@/lib/dev-auth';
import {
  Bell,
  CalendarCheck,
  Archive,
  LayoutDashboard,
  MessageSquare,
  LifeBuoy,
  Users,
  Settings,
  MessageCircle,
  MessagesSquare,
  BookOpenCheck,
  Flag,
  LogOut,
  CreditCard,
  ShoppingBag,
  Star,
  Wallet,
  Wrench,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTenantStore } from '@/stores/tenant.store';
import type { ComponentType, HTMLAttributes } from 'react';
import { safeDashboardHref } from '@/features/tools/catalog';
import { useTenantTools } from '@/features/tools/api';
import { SidebarGroup } from '../shared/SidebarGroup';
import { SidebarItem } from '../shared/SidebarItem';
import {
  hasAllowedRole,
  TENANT_ADMIN_ROLES,
  TENANT_OPERATION_ROLES,
  TENANT_READ_ROLES,
  type UserRole,
} from '@/features/auth/roles';

type NavItem = {
  href: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  roles: readonly UserRole[];
};

type NavGroup = {
  label: string;
  items: NavItem[];
  collapsible?: boolean;
  defaultExpanded?: boolean;
};


const navGroups: NavGroup[] = [
  {
    label: 'Home',
    collapsible: false,
    items: [
      { href: '/dashboard', label: 'Overview', icon: LayoutDashboard, roles: TENANT_READ_ROLES },
    ],
  },
  {
    label: 'Inbox & Support',
    collapsible: false,
    items: [
      { href: '/dashboard/conversations', label: 'Live Chats', icon: MessageSquare, roles: TENANT_READ_ROLES },
      { href: '/dashboard/escalations', label: 'Needs Team Reply', icon: LifeBuoy, roles: TENANT_OPERATION_ROLES },
      { href: '/dashboard/knowledge', label: 'AI Knowledge & FAQs', icon: BookOpenCheck, roles: TENANT_ADMIN_ROLES },
    ],
  },
  {
    label: 'Operations',
    collapsible: true,
    defaultExpanded: true,
    items: [
      { href: '/dashboard/bookings', label: 'Bookings & Services', icon: CalendarCheck, roles: TENANT_ADMIN_ROLES },
      { href: '/dashboard/orders', label: 'Orders', icon: ShoppingBag, roles: TENANT_ADMIN_ROLES },
      { href: '/dashboard/catalog', label: 'Catalog', icon: Archive, roles: TENANT_ADMIN_ROLES },
      { href: '/dashboard/payments', label: 'Payments', icon: Wallet, roles: TENANT_ADMIN_ROLES },
    ],
  },
  {
    label: 'Marketing & Growth',
    collapsible: true,
    items: [
      { href: '/dashboard/customers', label: 'Customers', icon: Users, roles: TENANT_ADMIN_ROLES },
      { href: '/dashboard/leads', label: 'Leads', icon: LifeBuoy, roles: TENANT_ADMIN_ROLES },
      { href: '/dashboard/reminders', label: 'Follow-ups', icon: Bell, roles: TENANT_ADMIN_ROLES },
      { href: '/dashboard/reviews', label: 'Reviews', icon: Star, roles: TENANT_ADMIN_ROLES },
    ],
  },
  {
    label: 'Settings',
    collapsible: true,
    items: [
      { href: '/dashboard/config', label: 'Business Profile', icon: Settings, roles: TENANT_ADMIN_ROLES },
      { href: '/dashboard/tools', label: 'Workflow Tools', icon: Wrench, roles: TENANT_ADMIN_ROLES },
      { href: '/dashboard/awa-chat', label: 'AWA Chat', icon: MessagesSquare, roles: TENANT_ADMIN_ROLES },
      { href: '/dashboard/whatsapp', label: 'WhatsApp', icon: MessageCircle, roles: TENANT_ADMIN_ROLES },
      { href: '/dashboard/billing', label: 'Billing', icon: CreditCard, roles: TENANT_ADMIN_ROLES },
      { href: '/dashboard/flags', label: 'Advanced Controls', icon: Flag, roles: TENANT_ADMIN_ROLES },
    ],
  },
];

interface SidebarProps extends HTMLAttributes<HTMLElement> {
  onNavigate?: () => void;
}

function isActiveNavItem(pathname: string, href: string) {
  const normalizedPathname = pathname === '/dashboard/ai' ? '/dashboard/config' : pathname;
  if (href === '/dashboard') {
    return normalizedPathname === '/dashboard';
  }
  return normalizedPathname === href || normalizedPathname.startsWith(`${href}/`);
}

function workflowStateForPath(tools: ReturnType<typeof useTenantTools>['data'] | undefined, href: string) {
  const tool = tools?.tools.find((item) => safeDashboardHref(item.route_path) === href);
  if (!tool) {
    return null;
  }
  const planAllowed = tool.allowed_by_plan ?? tool.plan_allowed ?? true;
  const lockedByPlan = tool.locked_by_plan ?? !planAllowed;
  if (lockedByPlan || !planAllowed) {
    return 'Plan';
  }
  if (!tool.runtime_available) {
    return 'Setup';
  }
  return 'On';
}

function formatPlanLabel(planCode?: string | null) {
  const normalized = (planCode || 'trial').trim().toLowerCase().replace(/[-\s]+/g, '_');
  return normalized.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());
}

export function Sidebar({ className, onNavigate, ...props }: SidebarProps) {
  const location = useLocation();
  const { user } = useCurrentUser();
  const effectiveRole = user?.role || (getDevMockToken() ? 'tenant_admin' : undefined);
  const activeTenantPlan = useTenantStore((state) => state.activeTenantPlan);
  const { data: toolsData } = useTenantTools();
  const activeTenantPlanLabel = formatPlanLabel(activeTenantPlan);

  return (
    <aside className={cn('flex h-full w-[220px] shrink-0 flex-col border-r border-border bg-background', className)} {...props}>
      {/* Logo */}
      <div className="h-[52px] flex items-center px-5 border-b border-border">
        <div className="min-w-0">
          <p className="text-[13px] font-semibold leading-tight text-foreground">AWA</p>
          <p className="truncate text-[10px] leading-tight text-muted-foreground">Always Working Assistant</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 space-y-3 overflow-y-auto px-2 py-3">
        {navGroups.map((group) => {
          const visibleItems = group.items.filter((item) => hasAllowedRole(effectiveRole, item.roles));
          if (visibleItems.length === 0) {
            return null;
          }

          const hasActiveChild = visibleItems.some((item) => isActiveNavItem(location.pathname, item.href));

          return (
            <SidebarGroup
              key={group.label}
              label={group.label}
              collapsible={group.collapsible}
              defaultExpanded={group.defaultExpanded}
              hasActiveChild={hasActiveChild}
            >
              {visibleItems.map((item) => {
                const isActive = isActiveNavItem(location.pathname, item.href);
                const workflowState = workflowStateForPath(toolsData, item.href);
                return (
                  <SidebarItem
                    key={item.href}
                    href={item.href}
                    label={item.label}
                    icon={item.icon}
                    isActive={isActive}
                    workflowState={workflowState}
                    onNavigate={onNavigate}
                  />
                );
              })}
            </SidebarGroup>
          );
        })}
      </nav>

      {/* User */}
      <div className="border-t border-border p-3">
        <div className="flex items-center gap-2.5 mb-2">
          <div className="w-7 h-7 rounded-full bg-secondary flex items-center justify-center text-[11px] font-semibold text-muted-foreground">
            {user?.email?.charAt(0)?.toUpperCase() || 'U'}
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-[12px] font-medium text-foreground truncate leading-tight">{user?.name || 'Admin'}</p>
            <p className="text-[11px] text-muted-foreground leading-tight">{activeTenantPlanLabel}</p>
          </div>
        </div>
        <button
          onClick={async () => {
            localStorage.removeItem('awa_onboarding');
            if (getDevMockToken()) {
              clearDevMockToken();
            } else {
              await authClient.logout();
            }
            window.location.href = '/login';
          }}
          className="motion-nav-item flex items-center gap-2 w-full px-2.5 py-[5px] rounded-md text-[12px] text-muted-foreground hover:text-foreground hover:bg-secondary/50 outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          <LogOut className="h-[14px] w-[14px]" />
          Sign out
        </button>
      </div>
    </aside>
  );
}
