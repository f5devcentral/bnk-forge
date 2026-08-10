/**
 * ConnectivityBanner — contextual offline UI for one target.
 *
 * Renders the backend-provided ``error_context.suggested_action`` verbatim.
 * The frontend never translates machine codes into English — that lives on
 * the backend so a future SDK rollout (Vault, MCP, …) reuses the same
 * rendering path without per-component message tables.
 */
import { useState } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import type { ReachabilityState } from '@/lib/api/connectivity';

interface ConnectivityBannerProps {
  state: ReachabilityState | undefined;
  targetType: string;
  /** Friendly fallback name when the backend hasn't reported one yet. */
  displayName?: string;
  onRetry: () => Promise<void>;
}

function relativeTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function ConnectivityBanner({
  state,
  targetType,
  displayName,
  onRetry,
}: ConnectivityBannerProps) {
  const [retrying, setRetrying] = useState(false);

  const targetName = state?.target_name || displayName || `${targetType}`;
  const suggested =
    state?.error_context?.suggested_action ??
    (state?.state === 'unknown'
      ? `Checking ${targetType} connectivity…`
      : `${targetType[0].toUpperCase()}${targetType.slice(1)} '${targetName}' is unreachable.`);
  const lastSuccess = relativeTime(
    (state?.error_context?.last_success_at as string | null | undefined) ?? null,
  );

  const handleRetry = async () => {
    setRetrying(true);
    try {
      await onRetry();
    } finally {
      setRetrying(false);
    }
  };

  return (
    <Alert variant="destructive" className="my-2">
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>
        {targetType[0].toUpperCase()}
        {targetType.slice(1)} '{targetName}' unreachable
        {lastSuccess ? ` — last reachable ${lastSuccess}` : ''}
      </AlertTitle>
      <AlertDescription>
        <p>{suggested}</p>
        <div className="mt-3">
          <Button
            size="sm"
            variant="outline"
            onClick={handleRetry}
            disabled={retrying}
          >
            <RefreshCw
              className={`h-3.5 w-3.5 mr-1.5 ${retrying ? 'animate-spin' : ''}`}
            />
            {retrying ? 'Checking…' : 'Retry'}
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}
