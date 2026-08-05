import { Link, useLocation } from 'react-router';
import { useCurrentUser } from '@/features/auth/hooks';
import { authClient } from '@/lib/auth';
import { clearDevMockToken, getDevMockToken } from '@/lib/dev-auth';
import {
  Building2,
  TerminalSquare,
  Activity,
  ScrollText,
  ShieldAlert,
  LogOut,
  DollarSign,
  Wrench
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { HTMLAttributes } from 'react';
const navItems = [
  { href: '/admin/tenants', label: 'Tenants', icon: Building2 },
  { href: '/admin/prompts', label: 'AI Prompts', icon: TerminalSquare },
  { href: '/admin/pricing', label: 'Billing Plans', icon: DollarSign },
  { href: '/admin/tools', label: 'Tool Controls', icon: Wrench },
  { href: '/admin/health', label: 'Platform Health', icon: Activity },
  { href: '/admin/audit', label: 'Audit Logs', icon: ScrollText },
  { href: '/admin/privacy', label: 'Privacy Requests', icon: ShieldAlert },
];
interface AdminSidebarProps extends HTMLAttributes<HTMLElement> {
  onNavigate?: () => void;
}
export function AdminSidebar({ className, onNavigate, ...props }: AdminSidebarProps) {
  const location = useLocation();
  const { user } = useCurrentUser();
  return (
    <aside className={cn('flex h-full w-[220px] shrink-0 flex-col border-r border-border bg-background', className)} {...props}>
      <div className="h-[52px] flex items-center px-5 border-b border-border">
        <div className="min-w-0">
          <p className="text-[13px] font-semibold leading-tight text-foreground">AWA Admin</p>
          <p className="truncate text-[10px] leading-tight text-muted-foreground">Always Working Assistant</p>
        </div>
      </div>
      <nav className="flex-1 py-2 px-2 overflow-y-auto">
        {navItems.map((item) => {
          const isActive = location.pathname === item.href || location.pathname.startsWith(`${item.href}/`);
          return (
            <Link key={item.href} to={item.href} onClick={onNavigate}>
              <div className={cn(
                'motion-nav-item flex items-center gap-2.5 px-2.5 py-[6px] rounded-md text-[13px] mb-[1px]',
                isActive
                  ? 'bg-secondary text-foreground font-medium'
                  : 'text-muted-foreground hover:text-foreground hover:bg-secondary/50'
              )}>
                <item.icon className="h-[15px] w-[15px] shrink-0" />
                {item.label}
              </div>
            </Link>
          );
        })}
      </nav>
      <div className="border-t border-border p-3">
        <div className="flex items-center gap-2.5 mb-2">
          <div className="w-7 h-7 rounded-full bg-secondary flex items-center justify-center text-[11px] font-semibold text-muted-foreground">
            {user?.email?.charAt(0)?.toUpperCase() || 'A'}
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-[12px] font-medium text-foreground truncate leading-tight">{user?.name || 'Administrator'}</p>
            <p className="text-[11px] text-muted-foreground truncate leading-tight">{user?.email}</p>
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
          className="motion-nav-item flex items-center gap-2 w-full px-2.5 py-[5px] rounded-md text-[12px] text-muted-foreground hover:text-foreground hover:bg-secondary/50"
        >
          <LogOut className="h-[14px] w-[14px]" />
          Sign out
        </button>
      </div>
    </aside>
  );
}
