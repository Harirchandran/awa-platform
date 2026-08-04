import * as React from 'react';
import { Check, Copy } from 'lucide-react';
import { Button, type ButtonProps } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export interface CopyButtonProps extends ButtonProps {
  value: string;
  successDuration?: number;
}

export function CopyButton({
  value,
  successDuration = 2000,
  variant = 'outline',
  size = 'sm',
  className,
  children,
  ...props
}: CopyButtonProps) {
  const [isCopied, setIsCopied] = React.useState(false);

  const handleCopy = async () => {
    try {
      if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        await navigator.clipboard.writeText(value);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = value;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.left = '-9999px';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
      }
      setIsCopied(true);
    } catch (err) {
      console.error('Could not copy to clipboard:', err);
    }
  };

  React.useEffect(() => {
    if (!isCopied) return;
    const timer = setTimeout(() => setIsCopied(false), successDuration);
    return () => clearTimeout(timer);
  }, [isCopied, successDuration]);

  return (
    <Button
      type="button"
      variant={variant}
      size={size}
      className={cn("transition-all duration-250", className)}
      {...props}
      onClick={(e) => {
        handleCopy();
        if (props.onClick) props.onClick(e);
      }}
    >
      {isCopied ? (
        <>
          <Check className="mr-1.5 h-3.5 w-3.5 text-emerald-500 animate-in fade-in zoom-in duration-200" />
          <span>{children ? 'Copied!' : 'Copied'}</span>
        </>
      ) : (
        <>
          <Copy className="mr-1.5 h-3.5 w-3.5" />
          {children && <span>{children}</span>}
        </>
      )}
    </Button>
  );
}
