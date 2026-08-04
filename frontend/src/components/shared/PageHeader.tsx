import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface PageHeaderProps {
  title: string;
  description?: string;
  eyebrow?: string;
  actions?: ReactNode;
  badge?: ReactNode;
  className?: string;
}

export function PageHeader({
  title,
  description,
  eyebrow,
  actions,
  badge,
  className,
}: PageHeaderProps) {
  return (
    <div className={cn("flex flex-col gap-2 md:flex-row md:items-center md:justify-between border-b pb-4 mb-5", className)}>
      <div className="space-y-1">
        {eyebrow && (
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground block">
            {eyebrow}
          </span>
        )}
        <div className="flex items-center gap-2">
          <h1 className="text-[20px] font-bold tracking-tight text-foreground sm:text-[22px]">{title}</h1>
          {badge}
        </div>
        {description && (
          <p className="text-[13px] text-muted-foreground max-w-2xl leading-normal">{description}</p>
        )}
      </div>
      {actions && (
        <div className="flex items-center gap-2 mt-2 md:mt-0 shrink-0">
          {actions}
        </div>
      )}
    </div>
  );
}
