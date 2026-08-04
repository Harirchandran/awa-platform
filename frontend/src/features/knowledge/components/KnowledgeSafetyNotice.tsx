import { ShieldAlert } from 'lucide-react';

interface KnowledgeSafetyNoticeProps {
  activeCount: number;
  disabledCount: number;
}

export function KnowledgeSafetyNotice({ activeCount, disabledCount }: KnowledgeSafetyNoticeProps) {
  return (
    <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-4 text-[13px] text-amber-950 dark:text-amber-100">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex gap-2.5">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Review Knowledge before a friendly pilot</p>
            <p className="mt-1 max-w-3xl text-[12px] leading-5 opacity-90">
              AWA can use active Knowledge in customer replies. Before sharing AWA Chat, remove personal IDs, medical records, financial records, legal documents, confidential customer data, and irrelevant uploaded documents.
            </p>
          </div>
        </div>
        <div className="grid shrink-0 grid-cols-2 gap-2 text-center">
          <div className="rounded-md border border-amber-500/25 bg-background/60 px-3 py-2">
            <p className="text-[16px] font-semibold leading-none">{activeCount}</p>
            <p className="mt-1 text-[11px] opacity-80">Active</p>
          </div>
          <div className="rounded-md border border-amber-500/25 bg-background/60 px-3 py-2">
            <p className="text-[16px] font-semibold leading-none">{disabledCount}</p>
            <p className="mt-1 text-[11px] opacity-80">Disabled</p>
          </div>
        </div>
      </div>
    </div>
  );
}
