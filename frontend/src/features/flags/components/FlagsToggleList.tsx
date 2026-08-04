import { useState } from 'react';
import { Switch } from '@/components/ui/switch';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { FeatureFlag } from '../api';
import { useToggleFlag } from '../api';
import { format } from 'date-fns';
import { RefreshCw } from 'lucide-react';
interface Props {
  flags: FeatureFlag[];
}
export function FlagsToggleList({ flags }: Props) {
  const { mutate: toggleFlag, isPending, variables } = useToggleFlag();
  const [pendingFlag, setPendingFlag] = useState<{ flag: FeatureFlag; enabled: boolean } | null>(null);
  return (
    <div className="space-y-4">
      {flags.map((flag) => {
        const isTogglingThis = isPending && variables?.key === flag.key;
        return (
          <Card key={flag.key} className={isTogglingThis ? 'opacity-70 pointer-events-none' : ''}>
            <CardContent className="p-6">
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <code className="text-sm font-semibold px-2 py-0.5 bg-muted rounded">
                      {flag.key}
                    </code>
                    {isTogglingThis && <RefreshCw className="h-4 w-4 animate-spin text-muted-foreground" />}
                  </div>
                  <p className="text-sm text-muted-foreground mt-2 font-medium">
                    {flag.description}
                  </p>
                  <p className="text-xs text-muted-foreground mt-1 opacity-75">
                    Last updated: {format(new Date(flag.updated_at), 'MMM d, yyyy h:mm a')}
                  </p>
                </div>
                <div className="flex flex-col items-end gap-2 shrink-0">
                  <div className="flex items-center space-x-3">
                    <span className="text-sm font-medium">
                      {flag.enabled ? 'Enabled' : 'Disabled'}
                    </span>
                    <Switch
                      checked={flag.enabled}
                      onCheckedChange={(checked) => setPendingFlag({ flag, enabled: checked })}
                      disabled={isPending}
                    />
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        );
      })}
      <Dialog open={!!pendingFlag} onOpenChange={(open) => !open && setPendingFlag(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{pendingFlag?.enabled ? 'Enable override?' : 'Disable override?'}</DialogTitle>
            <DialogDescription>
              This changes the `{pendingFlag?.flag.key}` feature flag immediately for this tenant.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setPendingFlag(null)}>Cancel</Button>
            <Button
              type="button"
              onClick={() => {
                if (pendingFlag) {
                  toggleFlag({ key: pendingFlag.flag.key, enabled: pendingFlag.enabled });
                }
                setPendingFlag(null);
              }}
              disabled={isPending}
            >
              {pendingFlag?.enabled ? 'Enable Override' : 'Disable Override'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}