import type { BookingSection } from '../constants';
import { BOOKING_SECTIONS } from '../constants';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { cn } from '@/lib/utils';

interface BookingSectionNavProps {
  activeSection: BookingSection;
  onSectionChange: (section: BookingSection) => void;
}

export function BookingSectionNav({ activeSection, onSectionChange }: BookingSectionNavProps) {
  return (
    <div className="space-y-3">
      <div className="md:hidden">
        <Select value={activeSection} onValueChange={(value) => onSectionChange(value as BookingSection)}>
          <SelectTrigger className="h-10 w-full text-[13px]" aria-label="Choose booking section">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {BOOKING_SECTIONS.map((section) => (
              <SelectItem key={section.value} value={section.value}>{section.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="mt-2 text-[12px] text-muted-foreground">
          {BOOKING_SECTIONS.find((section) => section.value === activeSection)?.description}
        </p>
      </div>

      <div className="hidden rounded-md border bg-card p-1 md:grid md:grid-cols-3 xl:grid-cols-6" aria-label="Booking setup sections">
        {BOOKING_SECTIONS.map((section) => {
          const active = section.value === activeSection;
          return (
            <button
              key={section.value}
              type="button"
              aria-pressed={active}
              onClick={() => onSectionChange(section.value)}
              className={cn(
                'rounded px-3 py-2 text-left text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                active ? 'bg-primary/10 text-primary shadow-sm' : 'text-muted-foreground hover:bg-muted hover:text-foreground',
              )}
            >
              {section.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
