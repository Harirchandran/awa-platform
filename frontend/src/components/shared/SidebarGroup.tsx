import { useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils';
import { AnimatePresence, motion } from 'framer-motion';

interface SidebarGroupProps {
  label: string;
  collapsible?: boolean;
  defaultExpanded?: boolean;
  children: ReactNode;
  hasActiveChild?: boolean;
}

export function SidebarGroup({ label, collapsible = false, defaultExpanded = false, children, hasActiveChild = false }: SidebarGroupProps) {
  const [isExpanded, setIsExpanded] = useState(() => {
    if (!collapsible) return true;
    return hasActiveChild || defaultExpanded;
  });
  const expanded = !collapsible || hasActiveChild || isExpanded;

  const contentId = `group-content-${label.replace(/\s+/g, '-').toLowerCase()}`;

  return (
    <div className="space-y-1">
      {collapsible ? (
        <button
          type="button"
          onClick={() => setIsExpanded(!isExpanded)}
          aria-expanded={expanded}
          aria-controls={contentId}
          className={cn(
            "flex w-full items-center justify-between px-2.5 py-1 text-left text-[10px] font-semibold uppercase tracking-wide text-muted-foreground/70 hover:text-foreground transition-colors rounded-md outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
          )}
        >
          <span className="flex items-center gap-1.5 min-w-0">
            {hasActiveChild && (
              <span className="h-1.5 w-1.5 rounded-full bg-primary shrink-0" aria-hidden="true" />
            )}
            <span className="truncate">{label}</span>
          </span>
          <motion.div
            animate={{ rotate: expanded ? 0 : -90 }}
            transition={{ duration: 0.2, ease: 'easeInOut' }}
            className="shrink-0"
          >
            <ChevronDown className="h-3 w-3" aria-hidden="true" />
          </motion.div>
        </button>
      ) : (
        <div className="px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground/70 flex items-center gap-1.5">
          {hasActiveChild && (
            <span className="h-1.5 w-1.5 rounded-full bg-primary shrink-0" aria-hidden="true" />
          )}
          <span>{label}</span>
        </div>
      )}

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            id={contentId}
            initial="collapsed"
            animate="open"
            exit="collapsed"
            variants={{
              open: { opacity: 1, height: 'auto' },
              collapsed: { opacity: 0, height: 0 }
            }}
            transition={{ duration: 0.2, ease: [0.04, 0.62, 0.23, 0.98] }}
            className="overflow-hidden space-y-[1px] pl-1.5"
          >
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
