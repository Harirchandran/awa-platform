import type { HTMLAttributes } from 'react';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
type TableSkeletonProps = {
  columns?: number;
  rows?: number;
  mobileCards?: boolean;
  className?: string;
};
export function TableSkeleton({ columns = 5, rows = 5, mobileCards = true, className }: TableSkeletonProps) {
  return (
    <div className={cn('motion-enter space-y-3', className)} aria-busy="true" aria-label="Loading table data">
      {mobileCards && (
        <div className="space-y-3 md:hidden">
          {Array.from({ length: Math.min(rows, 4) }).map((_, rowIndex) => (
            <div key={rowIndex} className="rounded-md border bg-card p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1 space-y-2">
                  <Skeleton className="h-4 w-2/3" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
                <Skeleton className="h-6 w-16 rounded-full" />
              </div>
              <div className="mt-4 grid grid-cols-2 gap-3">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            </div>
          ))}
        </div>
      )}
      <div className={cn('overflow-hidden rounded-md border bg-card', mobileCards && 'hidden md:block')}>
        <div className="grid border-b border-border bg-muted/30 px-4 py-3" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
          {Array.from({ length: columns }).map((_, columnIndex) => (
            <Skeleton key={columnIndex} className="h-3 w-2/3" />
          ))}
        </div>
        <div className="divide-y divide-border">
          {Array.from({ length: rows }).map((_, rowIndex) => (
            <div key={rowIndex} className="grid gap-4 px-4 py-4" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
              {Array.from({ length: columns }).map((__, columnIndex) => (
                <Skeleton key={columnIndex} className={cn('h-4', columnIndex === 0 ? 'w-4/5' : 'w-2/3')} />
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
type ConfigSkeletonProps = {
  tabs?: number;
  rows?: number;
  label?: string;
  description?: string;
};
export function ConfigSkeleton({
  tabs = 6,
  rows = 8,
  label = 'Loading setup',
  description,
}: ConfigSkeletonProps) {
  return (
    <div className="motion-enter space-y-3" aria-busy="true" aria-label={label}>
      <div className="rounded-md border border-border bg-card p-3 text-[13px] text-muted-foreground">
        <p className="font-medium text-foreground">{label}</p>
        {description && <p className="mt-1 text-[12px] leading-5">{description}</p>}
      </div>
      <div className="grid w-full items-start gap-4 lg:grid-cols-[12rem_minmax(0,1fr)] lg:gap-6">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:flex lg:flex-col">
        {Array.from({ length: tabs }).map((_, index) => (
          <Skeleton key={index} className="h-9 w-full" />
        ))}
      </div>
      <div className="rounded-lg border bg-card p-4 shadow-sm sm:p-6">
        <Skeleton className="h-5 w-44" />
        <Skeleton className="mt-3 h-3 w-72 max-w-full" />
        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          {Array.from({ length: rows }).map((_, index) => (
            <div key={index} className="space-y-2">
              <Skeleton className="h-3 w-28" />
              <Skeleton className="h-9 w-full" />
            </div>
          ))}
        </div>
      </div>
      </div>
    </div>
  );
}
export function DetailSheetSkeleton({ className }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('motion-enter space-y-6', className)} aria-busy="true" aria-label="Loading details">
      <div className="space-y-2 border-b border-border pb-4">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-3 w-56" />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
      <div className="space-y-3">
        <Skeleton className="h-4 w-28" />
        <Skeleton className="h-24 w-full" />
      </div>
      <div className="space-y-3">
        <Skeleton className="h-4 w-28" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    </div>
  );
}
export function MessageThreadSkeleton({ className }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('motion-enter space-y-4 p-4', className)} aria-busy="true" aria-label="Loading messages">
      {Array.from({ length: 6 }).map((_, index) => (
        <Skeleton
          key={index}
          className={cn(
            'h-14 max-w-[78%] rounded-2xl',
            index % 2 === 0 ? 'w-2/3 rounded-tl-sm' : 'ml-auto w-3/4 rounded-tr-sm'
          )}
        />
      ))}
    </div>
  );
}
export function KnowledgeSkeleton() {
  return (
    <div className="motion-enter space-y-3" aria-busy="true" aria-label="Loading saved answers">
      {Array.from({ length: 4 }).map((_, index) => (
        <div key={index} className="rounded-md border bg-card p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1 space-y-2">
              <Skeleton className="h-4 w-2/5" />
              <Skeleton className="h-3 w-24" />
            </div>
            <Skeleton className="h-8 w-24" />
          </div>
          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            <Skeleton className="h-28 w-full" />
            <Skeleton className="h-28 w-full" />
          </div>
        </div>
      ))}
    </div>
  );
}