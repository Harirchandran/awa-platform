import { ShieldAlert, TerminalSquare } from 'lucide-react';
import { useFeatureFlags } from '@/features/flags/api';
import { FlagsToggleList } from '@/features/flags/components/FlagsToggleList';
import { PageHeader } from '@/components/shared/PageHeader';

export function FlagsPage() {
  const { data: flags, isLoading, isError } = useFeatureFlags();
  return (
    <div className="space-y-5 max-w-3xl">
      <PageHeader
        title="Advanced Controls"
        description="Support-guided controls for tenant-specific automation behavior."
      />
      <div className="border border-yellow-500/20 bg-yellow-500/5 text-yellow-600 dark:text-yellow-500 p-3 rounded-md flex items-start gap-2.5 text-[13px]">
        <TerminalSquare className="w-4 h-4 mt-0.5 shrink-0" />
        <div>
          <p className="font-medium text-[13px]">Use only with support guidance</p>
          <p className="text-[12px] mt-0.5 opacity-80">
            These are not needed for normal setup. Changes can affect customer automation immediately, so review them with support before changing anything.
          </p>
        </div>
      </div>
      {isError && (
        <div className="border border-destructive/20 bg-destructive/5 p-3 rounded-md flex items-start gap-2.5 text-destructive text-[13px]">
          <ShieldAlert className="w-4 h-4 mt-0.5" />
          <p>Failed to load flags.</p>
        </div>
      )}
      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map(i => <div key={i} className="h-24 rounded-md bg-card border border-border animate-pulse" />)}
        </div>
      ) : flags && flags.length > 0 ? (
        <FlagsToggleList flags={flags} />
      ) : !isError && (
        <div className="text-center py-12 border border-dashed border-border rounded-md">
          <p className="text-[14px] font-medium text-foreground mb-1">No advanced controls assigned</p>
          <p className="text-[13px] text-muted-foreground">No support-guided controls are currently assigned to this workspace.</p>
        </div>
      )}
    </div>
  );
}