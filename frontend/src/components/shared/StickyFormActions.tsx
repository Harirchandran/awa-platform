import * as React from 'react';
import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { AnimatePresence, motion } from 'framer-motion';

export interface StickyBarRootProps {
  visible: boolean;
  children: React.ReactNode;
  className?: string;
}

export function StickyBarRoot({ visible, children, className }: StickyBarRootProps) {
  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          initial={{ y: 100, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: 100, opacity: 0 }}
          transition={{ type: 'spring', stiffness: 300, damping: 30 }}
          className={cn(
            "fixed inset-x-4 bottom-20 z-50 flex flex-col gap-3 rounded-lg border border-border bg-card/95 p-4 shadow-2xl backdrop-blur md:bottom-6 md:left-[calc(220px+(100vw-220px)/2)] md:right-auto md:-translate-x-1/2 md:w-[calc(100vw-220px-3rem)] md:max-w-2xl md:flex-row md:items-center md:justify-between",
            className
          )}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export interface StickyBarMessageProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
}

export function StickyBarMessage({ children, className, ...props }: StickyBarMessageProps) {
  return (
    <div className={cn("flex items-center space-x-3", className)} {...props}>
      <span className="flex h-3 w-3 relative shrink-0">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-yellow-400 opacity-75"></span>
        <span className="relative inline-flex rounded-full h-3 w-3 bg-yellow-500"></span>
      </span>
      <span className="text-sm font-medium">{children}</span>
    </div>
  );
}

export interface StickyBarActionsProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
}

export function StickyBarActions({ children, className, ...props }: StickyBarActionsProps) {
  return (
    <div className={cn("flex gap-2 md:space-x-3", className)} {...props}>
      {children}
    </div>
  );
}

export function StickyBar({ visible, children, className }: StickyBarRootProps) {
  return <StickyBarRoot visible={visible} children={children} className={className} />;
}
StickyBar.Root = StickyBarRoot;
StickyBar.Message = StickyBarMessage;
StickyBar.Actions = StickyBarActions;

interface StickyFormActionsProps {
  visible: boolean;
  isSaving?: boolean;
  onSave: () => void;
  onDiscard: () => void;
  saveLabel?: string;
  discardLabel?: string;
  message?: string;
  disabled?: boolean;
  className?: string;
}

export function StickyFormActions({
  visible,
  isSaving = false,
  onSave,
  onDiscard,
  saveLabel = 'Review & Sync',
  discardLabel = 'Discard',
  message = 'You have unsaved changes in your configuration',
  disabled = false,
  className,
}: StickyFormActionsProps) {
  return (
    <StickyBarRoot visible={visible} className={className}>
      <StickyBarMessage>{message}</StickyBarMessage>
      <StickyBarActions>
        <Button
          type="button"
          variant="ghost"
          onClick={onDiscard}
          disabled={isSaving}
          className="text-[13px] h-9 outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          {discardLabel}
        </Button>
        <Button
          type="button"
          onClick={onSave}
          disabled={isSaving || disabled}
          className="text-[13px] h-9 outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          {isSaving && <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />}
          {saveLabel}
        </Button>
      </StickyBarActions>
    </StickyBarRoot>
  );
}
