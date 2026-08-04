import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Loader2 } from 'lucide-react';

interface ConfirmActionDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string | ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  variant?: 'destructive' | 'warning' | 'default';
  isLoading?: boolean;
  onConfirm: () => void;
  confirmDisabled?: boolean;
  children?: ReactNode;
}

export function ConfirmActionDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel = 'Cancel',
  variant = 'default',
  isLoading = false,
  onConfirm,
  confirmDisabled = false,
  children,
}: ConfirmActionDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "outline-none focus:ring-0",
          isLoading && "[&>button:last-child]:pointer-events-none [&>button:last-child]:opacity-0"
        )}
        onPointerDownOutside={(e) => {
          if (isLoading) {
            e.preventDefault();
          }
        }}
        onEscapeKeyDown={(e) => {
          if (isLoading) {
            e.preventDefault();
          }
        }}
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!isLoading && !confirmDisabled) {
              onConfirm();
            }
          }}
          className="space-y-4"
        >
          <DialogHeader>
            <DialogTitle className="text-[15px] font-semibold">{title}</DialogTitle>
            <div className="text-[13px] text-muted-foreground pt-1.5 leading-normal">
              {typeof description === 'string' ? (
                <DialogDescription>{description}</DialogDescription>
              ) : (
                description
              )}
            </div>
          </DialogHeader>
          {children && <div className="py-2">{children}</div>}
          <DialogFooter className="mt-4 gap-2 sm:gap-0">
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={isLoading}
              className="text-[13px] h-9 outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              {cancelLabel}
            </Button>
            <Button
              type="submit"
              variant={variant === 'destructive' ? 'destructive' : variant === 'warning' ? 'default' : 'default'}
              disabled={isLoading || confirmDisabled}
              className="text-[13px] h-9 outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />}
              {confirmLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
