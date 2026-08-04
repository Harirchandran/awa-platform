import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const statusBadgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-[11px] font-medium shrink-0 transition-colors',
  {
    variants: {
      tone: {
        neutral: 'border-border bg-background text-muted-foreground',
        success: 'border-success/30 bg-success/10 text-success',
        warning: 'border-warning/30 bg-warning/10 text-warning',
        danger: 'border-danger/30 bg-danger/10 text-danger',
        destructive: 'border-destructive/30 bg-destructive/10 text-destructive',
        info: 'border-info/30 bg-info/10 text-info',
        muted: 'border-muted bg-muted/40 text-muted-foreground',
      },
    },
    defaultVariants: {
      tone: 'neutral',
    },
  }
);

export interface StatusBadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof statusBadgeVariants> {
  label: string;
  icon?: React.ComponentType<{ className?: string }>;
  srLabel?: string;
}

export const StatusBadge = React.forwardRef<HTMLSpanElement, StatusBadgeProps>(
  ({ label, tone, icon: Icon, srLabel, className, ...props }, ref) => {
    return (
      <span
        ref={ref}
        className={cn(statusBadgeVariants({ tone, className }))}
        {...props}
      >
        {Icon && <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />}
        <span>{label}</span>
        {srLabel && <span className="sr-only">({srLabel})</span>}
      </span>
    );
  }
);

StatusBadge.displayName = 'StatusBadge';
