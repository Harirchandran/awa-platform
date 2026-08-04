import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

interface PublicReviewLinkFieldProps {
  id: string;
  label?: string;
  value: string;
  onChange: (value: string) => void;
  invalid: boolean;
  placeholder?: string;
  helper?: string;
}

export function PublicReviewLinkField({
  id,
  label = 'Public review URL',
  value,
  onChange,
  invalid,
  placeholder,
  helper = 'Leave blank or use a public link that starts with http:// or https://.',
}: PublicReviewLinkFieldProps) {
  const errorId = `${id}-error`;
  const helperId = `${id}-helper`;

  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-invalid={invalid}
        aria-describedby={invalid ? errorId : helperId}
      />
      {invalid ? (
        <p id={errorId} className="text-xs text-destructive">
          Enter a valid URL starting with http:// or https://
        </p>
      ) : (
        <p id={helperId} className="text-xs text-muted-foreground">{helper}</p>
      )}
    </div>
  );
}
