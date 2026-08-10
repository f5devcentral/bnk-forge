import { AlertTriangle, RefreshCcw } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { parseApiError } from '@/lib/error-handler';

// ---------------------------------------------------------------------------
// UX-OPS-001: Shared ErrorState component for consistent error display.
//
// Use this whenever a React Query fetch fails, instead of ad-hoc error UI.
// Pairs with EmptyState (for no data) and Skeleton (for loading).
// ---------------------------------------------------------------------------

export interface ErrorStateProps {
  /** Raw error from React Query — auto-parsed via parseApiError() */
  error?: unknown;
  /** Override title (default: parsed from error) */
  title?: string;
  /** Override message (default: parsed from error) */
  message?: string;
  /** Retry handler — typically query.refetch() */
  onRetry?: () => void;
  /** Optional secondary action (e.g., navigate home) */
  secondaryAction?: {
    label: string;
    onClick: () => void;
  };
  className?: string;
  /** Visual size variant (default: 'md') */
  size?: 'sm' | 'md' | 'lg';
}

export function ErrorState({
  error,
  title,
  message,
  onRetry,
  secondaryAction,
  className,
  size = 'md',
}: ErrorStateProps) {
  // Parse the error for a friendly message
  const parsed = error ? parseApiError(error) : null;
  const displayTitle = title || parsed?.title || 'Something went wrong';
  const displayMessage = message || parsed?.message || 'An unexpected error occurred. Please try again.';
  const suggestion = parsed?.suggestion;

  const sizes = {
    sm: { wrapper: 'p-4', icon: 'p-2.5', iconSize: 'h-5 w-5', title: 'text-sm', desc: 'text-xs' },
    md: { wrapper: 'p-8', icon: 'p-4', iconSize: 'h-8 w-8', title: 'text-lg', desc: 'text-sm' },
    lg: { wrapper: 'p-12', icon: 'p-6', iconSize: 'h-12 w-12', title: 'text-xl', desc: 'text-base' },
  };

  const s = sizes[size];

  return (
    <div
      className={cn(
        'flex flex-1 items-center justify-center motion-safe:animate-fade-in',
        s.wrapper,
        className,
      )}
      role="alert"
      aria-label={displayTitle}
    >
      <div className="text-center max-w-md">
        {/* Icon */}
        <div className="mb-4 flex justify-center">
          <div className={cn('rounded-full bg-destructive/10', s.icon)}>
            <AlertTriangle className={cn(s.iconSize, 'text-destructive')} />
          </div>
        </div>

        {/* Title */}
        <h3 className={cn(s.title, 'font-semibold mb-2')}>{displayTitle}</h3>

        {/* Message */}
        <p className={cn(s.desc, 'text-muted-foreground mb-2 leading-relaxed')}>
          {displayMessage}
        </p>

        {/* Suggestion (from parseApiError) */}
        {suggestion && (
          <p className={cn(s.desc, 'text-muted-foreground/70 mb-6 italic leading-relaxed')}>
            {suggestion}
          </p>
        )}

        {!suggestion && <div className="mb-6" />}

        {/* Actions */}
        {(onRetry || secondaryAction) && (
          <div className="flex flex-col sm:flex-row gap-2 justify-center">
            {onRetry && (
              <Button onClick={onRetry} variant="default" size={size === 'sm' ? 'sm' : 'default'}>
                <RefreshCcw className="h-4 w-4 mr-1.5" />
                Retry
              </Button>
            )}
            {secondaryAction && (
              <Button
                onClick={secondaryAction.onClick}
                variant="outline"
                size={size === 'sm' ? 'sm' : 'default'}
              >
                {secondaryAction.label}
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
