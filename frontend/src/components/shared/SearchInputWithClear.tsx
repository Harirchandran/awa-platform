import * as React from 'react';
import { Search, X } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export interface SearchInputWithClearProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'> {
  value: string;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onClear: () => void;
  debounceMs?: number;
}

export const SearchInputWithClear = React.forwardRef<HTMLInputElement, SearchInputWithClearProps>(
  ({ value, onChange, onClear, placeholder = 'Search...', className, disabled = false, debounceMs = 300, ...props }, ref) => {
    const [localValue, setLocalValue] = React.useState(String(value || ''));
    const [prevValue, setPrevValue] = React.useState(value);

    // Sync from parent value updates (e.g. cleared or loaded externally)
    if (value !== prevValue) {
      setPrevValue(value);
      setLocalValue(String(value || ''));
    }

    // Ref to hold the latest onChange to prevent stale closures in debounce
    const onChangeRef = React.useRef(onChange);
    React.useEffect(() => {
      onChangeRef.current = onChange;
    }, [onChange]);

    // Debounced trigger
    React.useEffect(() => {
      // Don't trigger onChange on mount if localValue matches prop value
      if (localValue === value) return;

      const handler = setTimeout(() => {
        if (onChangeRef.current) {
          // Construct simulated ChangeEvent
          const simulatedEvent = {
            target: { value: localValue },
            currentTarget: { value: localValue }
          } as unknown as React.ChangeEvent<HTMLInputElement>;
          onChangeRef.current(simulatedEvent);
        }
      }, debounceMs);

      return () => clearTimeout(handler);
    }, [localValue, value, debounceMs]);

    const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      setLocalValue(e.target.value);
    };

    const handleClearClick = () => {
      setLocalValue('');
      onClear();
    };

    return (
      <div className={cn("relative flex items-center w-full max-w-sm", className)}>
        <Search className="absolute left-3 h-4 w-4 text-muted-foreground pointer-events-none" aria-hidden="true" />
        <Input
          ref={ref}
          type="text"
          placeholder={placeholder}
          value={localValue}
          onChange={handleInputChange}
          disabled={disabled}
          className="pl-9 pr-9 text-[13px] h-10 outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
          {...props}
        />
        {localValue && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={handleClearClick}
            disabled={disabled}
            className="absolute right-1.5 h-6 w-6 text-muted-foreground hover:text-foreground rounded-full outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1"
            aria-label="Clear search"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        )}
      </div>
    );
  }
);

SearchInputWithClear.displayName = 'SearchInputWithClear';
