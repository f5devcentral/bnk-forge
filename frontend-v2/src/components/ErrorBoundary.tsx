/**
 * Route-level error boundary for react-router.
 *
 * Catches chunk load failures (lazy imports) and renders a friendly
 * "new version available" prompt. For all other errors, shows a
 * generic recovery message with a reload button.
 */

import { useEffect } from 'react';
import { useRouteError, isRouteErrorResponse, Link } from 'react-router-dom';
import { AlertTriangle, RefreshCw, Home } from 'lucide-react';

/** Key used to detect reload loops (stale chunk → reload → still stale). */
const CHUNK_RELOAD_KEY = 'bnk_chunk_reload_ts';

function isChunkLoadError(error: unknown): boolean {
  if (error instanceof Error) {
    // Vite / webpack chunk load errors
    return (
      error.message.includes('Failed to fetch dynamically imported module') ||
      error.message.includes('Loading chunk') ||
      error.message.includes('Loading CSS chunk') ||
      error.name === 'ChunkLoadError'
    );
  }
  return false;
}

export function ErrorBoundary() {
  const error = useRouteError();
  const chunkError = isChunkLoadError(error);

  // Auto-reload once for chunk errors (new deployment invalidated old chunks).
  // Guard against infinite reload loops by checking a timestamp flag.
  useEffect(() => {
    if (!chunkError) return;
    const lastReload = Number(sessionStorage.getItem(CHUNK_RELOAD_KEY) || 0);
    if (Date.now() - lastReload > 10_000) {
      sessionStorage.setItem(CHUNK_RELOAD_KEY, String(Date.now()));
      window.location.reload();
    }
  }, [chunkError]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="mx-auto max-w-md text-center">
        <div className="mb-6 inline-flex h-16 w-16 items-center justify-center rounded-full bg-destructive/10">
          <AlertTriangle className="h-8 w-8 text-destructive" />
        </div>

        {chunkError ? (
          <>
            <h1 className="mb-2 text-xl font-bold text-foreground">
              A new version is available
            </h1>
            <p className="mb-6 text-sm text-muted-foreground">
              The application has been updated. Click below to reload and get the
              latest version.
            </p>
          </>
        ) : (
          <>
            <h1 className="mb-2 text-xl font-bold text-foreground">
              Something went wrong
            </h1>
            <p className="mb-6 text-sm text-muted-foreground">
              {isRouteErrorResponse(error)
                ? `${error.status} — ${error.statusText}`
                : error instanceof Error
                  ? error.message
                  : 'An unexpected error occurred.'}
            </p>
          </>
        )}

        <div className="flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            <RefreshCw className="h-4 w-4" />
            Reload
          </button>
          <Link
            to="/"
            className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-muted/50 transition-colors"
          >
            <Home className="h-4 w-4" />
            Dashboard
          </Link>
        </div>
      </div>
    </div>
  );
}

export function NotFound() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center px-4">
      <div className="mx-auto max-w-md text-center">
        <div className="mb-4 text-6xl font-bold text-muted-foreground/40">404</div>
        <h1 className="mb-2 text-xl font-bold text-foreground">Page not found</h1>
        <p className="mb-6 text-sm text-muted-foreground">
          The page you're looking for doesn't exist or has been moved.
        </p>
        <Link
          to="/"
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          <Home className="h-4 w-4" />
          Back to Dashboard
        </Link>
      </div>
    </div>
  );
}
