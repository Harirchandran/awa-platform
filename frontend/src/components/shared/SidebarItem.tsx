import { Link } from 'react-router-dom';
import type { ComponentType } from 'react';
import { cn } from '@/lib/utils';
import { StatusBadge } from './StatusBadge';
import { motion } from 'framer-motion';

interface SidebarItemProps {
  href: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  isActive: boolean;
  workflowState?: string | null;
  onNavigate?: () => void;
}

export function SidebarItem({ href, label, icon: Icon, isActive, workflowState, onNavigate }: SidebarItemProps) {
  return (
    <Link to={href} onClick={onNavigate} className="outline-none block relative">
      <div className={cn(
        'motion-nav-item flex items-center gap-2.5 rounded-md px-2.5 py-[6px] text-[13px] transition-colors outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 relative z-10',
        isActive
          ? 'text-emerald-700 dark:text-emerald-300 font-semibold'
          : 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground'
      )}
      {...(isActive ? { 'aria-current': 'page' as const } : {})}
      >
        <Icon className="h-[15px] w-[15px] shrink-0" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {workflowState && (
          <StatusBadge
            label={workflowState}
            tone={workflowState === 'On' ? 'success' : workflowState === 'Setup' ? 'warning' : 'neutral'}
            srLabel={`Workflow state: ${workflowState}`}
            className="text-[10px] px-1 py-0"
          />
        )}
      </div>
      {isActive && (
        <motion.div
          layoutId="sidebar-active-bg"
          className="absolute inset-0 rounded-md border border-emerald-500/20 bg-emerald-500/10 z-0"
          transition={{
            type: 'spring',
            stiffness: 380,
            damping: 30,
          }}
        />
      )}
    </Link>
  );
}
