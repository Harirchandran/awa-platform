import { ConfigTabs } from '@/features/config/components/ConfigTabs';
import { PageHeader } from '@/components/shared/PageHeader';

export function ConfigPage() {
  return (
    <div className="flex-1 space-y-5 flex flex-col h-full overflow-hidden">
      <PageHeader
        title="Business Setup"
        description="Business profile, hours, and advanced setup options."
        className="mb-0 pb-0 border-b-0"
      />
      <div className="flex-1 overflow-y-auto">
        <ConfigTabs />
      </div>
    </div>
  );
}