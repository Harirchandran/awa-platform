import { Link } from 'react-router-dom';
import { ArrowRight, CalendarCheck, Settings } from 'lucide-react';
import { Button } from '@/components/ui/button';

export function BookingEmptyState() {
  return (
    <div className="rounded-md border bg-card/70 p-8 text-center sm:p-12">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border border-primary/20 bg-primary/10">
        <CalendarCheck className="h-5 w-5 text-primary" aria-hidden="true" />
      </div>
      <h3 className="mt-4 text-[16px] font-semibold">No bookings yet</h3>
      <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
        No bookings yet. New appointment requests will appear here after customers complete a booking through AWA Chat or WhatsApp.
      </p>
      <div className="mt-5 flex flex-col justify-center gap-2 sm:flex-row">
        <Button asChild size="sm">
          <Link to="/dashboard/awa-chat">
            Open AWA Chat
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
          </Link>
        </Button>
        <Button asChild variant="outline" size="sm">
          <Link to="/dashboard/config">
            <Settings className="h-3.5 w-3.5" aria-hidden="true" />
            Review Business Setup
          </Link>
        </Button>
      </div>
    </div>
  );
}
