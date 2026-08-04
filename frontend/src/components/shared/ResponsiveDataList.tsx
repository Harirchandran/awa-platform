import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { useMediaQuery } from '@/hooks/use-media-query';

interface ResponsiveDataListProps {
  desktop: ReactNode;
  mobile: ReactNode;
  className?: string;
}

export function ResponsiveDataList({
  desktop,
  mobile,
  className,
}: ResponsiveDataListProps) {
  const isDesktop = useMediaQuery('(min-width: 768px)');

  return (
    <div className={cn("w-full", className)}>
      {isDesktop ? desktop : mobile}
    </div>
  );
}
