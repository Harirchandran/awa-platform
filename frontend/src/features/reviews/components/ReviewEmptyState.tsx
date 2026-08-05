import { Link } from 'react-router';
import { Plus, Star } from 'lucide-react';
import { Button } from '@/components/ui/button';

export function ReviewEmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex min-h-[360px] items-center justify-center rounded-md border bg-card">
      <div className="max-w-sm px-6 text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border bg-background">
          <Star className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
        </div>
        <h3 className="mt-4 text-[15px] font-semibold text-foreground">No review requests</h3>
        <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
          No review requests yet. Send a request after a booking, order, or support interaction.
        </p>
        <div className="mt-4 flex flex-col justify-center gap-2 sm:flex-row">
          <Button type="button" onClick={onCreate}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            New Review Request
          </Button>
          <Button asChild variant="outline">
            <Link to="/dashboard/tools">Workflow Tools</Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
