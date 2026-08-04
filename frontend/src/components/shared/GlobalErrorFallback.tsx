import { useState } from 'react';
import type { FallbackProps } from 'react-error-boundary';
import { AlertTriangle, RefreshCw, ChevronDown, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';

export function GlobalErrorFallback({ error, resetErrorBoundary }: FallbackProps) {
  const [showDetails, setShowDetails] = useState(false);

  const handleReload = () => {
    // Reset boundary first
    if (resetErrorBoundary) {
      resetErrorBoundary();
    }
    // Reload full page to clear stale memory/assets
    window.location.reload();
  };

  const typedError = error instanceof Error ? error : error ? new Error(String(error)) : null;

  return (
    <div className="min-h-[100dvh] flex items-center justify-center bg-background p-4 sm:p-6 md:p-8 select-none">
      <div className="w-full max-w-md bg-card border border-border rounded-lg p-6 md:p-8 shadow-lg motion-splash-mark flex flex-col items-center text-center">
        {/* Warning Icon */}
        <div className="h-12 w-12 rounded-full bg-danger/10 text-danger flex items-center justify-center mb-5 shrink-0">
          <AlertTriangle className="h-6 w-6" aria-hidden="true" />
        </div>

        {/* Heading */}
        <h1 className="text-lg font-semibold text-foreground tracking-tight mb-2">
          Something went wrong
        </h1>
        <p className="text-sm text-muted-foreground mb-6 max-w-sm">
          An unexpected error has occurred. You can reload the page or return to the dashboard.
        </p>

        {/* Action Buttons */}
        <div className="flex flex-col sm:flex-row items-center gap-3 w-full mb-6">
          <Button
            onClick={handleReload}
            className="w-full flex items-center justify-center gap-2 motion-button-press"
          >
            <RefreshCw className="h-4 w-4" />
            Reload Page
          </Button>
          <Button
            variant="outline"
            onClick={() => window.location.replace('/')}
            className="w-full motion-button-press"
          >
            Go to Home
          </Button>
        </div>

        {/* Developer Logs Accordion */}
        {typedError && (
          <div className="w-full border-t border-border pt-4 text-left">
            <button
              onClick={() => setShowDetails(!showDetails)}
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors font-medium focus:outline-none"
              aria-expanded={showDetails}
            >
              {showDetails ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
              <span>{showDetails ? 'Hide technical details' : 'Show technical details'}</span>
            </button>

            {showDetails && (
              <div className="mt-3 p-3 bg-muted rounded border border-border text-[11px] font-mono text-muted-foreground overflow-auto max-h-[160px] whitespace-pre-wrap break-all select-text">
                <div className="font-semibold text-foreground mb-1 select-text">
                  {typedError.name}: {typedError.message}
                </div>
                {typedError.stack && (
                  <div className="text-[10px] leading-relaxed select-text">{typedError.stack}</div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
