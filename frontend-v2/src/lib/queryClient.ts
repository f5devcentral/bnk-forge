/**
 * Singleton QueryClient for use outside React component trees.
 *
 * Non-hook code (e.g. createBellNotification in notify.ts) needs to
 * invalidate queries after a successful POST so the bell badge updates
 * immediately instead of waiting for the next poll interval.
 *
 * main.tsx imports this and passes it to <QueryClientProvider>.
 */

import { QueryClient } from '@tanstack/react-query';
import { QUERY_STALE_TIME } from './constants';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: QUERY_STALE_TIME.DEFAULT, // 30 seconds
      retry: (failureCount, error) => {
        // Never retry auth errors — the interceptor handles logout
        const status = (error as { response?: { status?: number } })?.response?.status;
        if (status === 401 || status === 403) return false;
        return failureCount < 1;
      },
      refetchOnWindowFocus: false,
      refetchOnReconnect: true,
      refetchOnMount: true,
    },
  },
});
