import { useMemo, useState } from 'react';
import { Ban, Loader2, Plus, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { useTenantConfig, useUpdateConfig } from '@/features/config/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { getApiErrorMessage } from '@/lib/api-errors';
function normalizeWhatsAppNumber(value: string) {
  return value.replace(/\D/g, '');
}
export function IgnoredNumbersManager() {
  const { data: config, isLoading } = useTenantConfig();
  const updateConfig = useUpdateConfig();
  const [newNumber, setNewNumber] = useState('');
  const [pendingRemoveNumber, setPendingRemoveNumber] = useState<string | null>(null);
  const ignoredNumbers = useMemo(() => config?.ignored_numbers || [], [config?.ignored_numbers]);
  const isSaving = updateConfig.isPending;
  const addNumberHelpText = !newNumber.trim()
    ? 'Enter a full WhatsApp sender number to enable Add.'
    : isLoading
      ? 'Loading WhatsApp settings before numbers can be changed.'
      : isSaving
        ? 'Saving the do-not-auto-reply list.'
        : 'Adds this number to the do-not-auto-reply list.';
  const addNumber = () => {
    if (!config) return;
    const normalized = normalizeWhatsAppNumber(newNumber);
    if (normalized.length < 10 || normalized.length > 15) {
      toast.error('Enter the full WhatsApp number with country code.');
      return;
    }
    if (ignoredNumbers.includes(normalized)) {
      toast.error('This number is already in the list.');
      return;
    }
    updateConfig.mutate(
      { ...config, ignored_numbers: [...ignoredNumbers, normalized] },
      {
        onSuccess: () => {
          toast.success('AI auto-reply disabled for this number.');
          setNewNumber('');
        },
        onError: (error: unknown) => toast.error(getApiErrorMessage(error, 'Failed to update the do-not-reply list.')),
      },
    );
  };
  const removeNumber = (number: string) => {
    if (!config) return;
    updateConfig.mutate(
      { ...config, ignored_numbers: ignoredNumbers.filter((item) => item !== number) },
      {
        onSuccess: () => toast.success('AI auto-reply restored for this number.'),
        onError: (error: unknown) => toast.error(getApiErrorMessage(error, 'Failed to update the do-not-reply list.')),
      },
    );
  };
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-border bg-background">
            <Ban className="h-4 w-4 text-muted-foreground" />
          </div>
          <div>
            <CardTitle className="text-[15px]">Do Not Auto-Reply</CardTitle>
            <CardDescription className="mt-1 text-[12px]">
              Add customer numbers where staff should reply manually. AWA will receive the message but stay silent.
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            value={newNumber}
            onChange={(event) => setNewNumber(event.target.value)}
            placeholder="919876543210"
            inputMode="tel"
            disabled={isLoading || isSaving}
            aria-describedby="ignored-number-add-help"
          />
          <Button
            type="button"
            onClick={addNumber}
            disabled={isLoading || isSaving || !newNumber.trim()}
            className="sm:w-32"
            aria-describedby="ignored-number-add-help"
            title={addNumberHelpText}
          >
            {isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            Add
          </Button>
        </div>
        <p id="ignored-number-add-help" className="text-[12px] text-muted-foreground">
          Use the full WhatsApp sender number with country code. For India, save it like 91XXXXXXXXXX.
          {!newNumber.trim() ? ' Add is disabled until a number is entered.' : ''}
        </p>
        {isLoading ? (
          <div className="h-20 rounded-md border border-border bg-muted/30 animate-pulse" />
        ) : ignoredNumbers.length > 0 ? (
          <div className="divide-y rounded-md border border-border">
            {ignoredNumbers.map((number) => (
              <div key={number} className="flex items-center justify-between gap-3 p-3">
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-medium text-foreground">{number}</p>
                  <p className="text-[12px] text-muted-foreground">AI replies paused</p>
                </div>
                <Button type="button" variant="ghost" size="icon" onClick={() => setPendingRemoveNumber(number)} disabled={isSaving} aria-label={`Remove ${number}`}>
                  <Trash2 className="h-4 w-4 text-muted-foreground" />
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-border p-4 text-center">
            <p className="text-[13px] font-medium text-foreground">No muted numbers</p>
            <p className="mt-1 text-[12px] text-muted-foreground">AWA will auto-reply based on your active tools and business rules.</p>
          </div>
        )}
      </CardContent>
      <Dialog open={!!pendingRemoveNumber} onOpenChange={(open) => !open && setPendingRemoveNumber(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Restore AI auto-reply?</DialogTitle>
            <DialogDescription>
              AWA will start auto-replying to {pendingRemoveNumber} again after this number is removed from the do-not-auto-reply list.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setPendingRemoveNumber(null)}>Cancel</Button>
            <Button
              type="button"
              onClick={() => {
                if (pendingRemoveNumber) {
                  removeNumber(pendingRemoveNumber);
                }
                setPendingRemoveNumber(null);
              }}
              disabled={isSaving}
            >
              Restore Auto-Reply
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}