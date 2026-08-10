/**
 * usePageRefresh — the standard page-level refresh used by the D-020 PageHeader
 * refresh button.
 *
 * Invalidating with no key marks every query stale; React Query then refetches
 * the *active* ones — i.e. exactly the data currently mounted on the page. That
 * makes one hook correct for every page without each page having to enumerate
 * its own query keys, so the refresh button behaves identically everywhere.
 *
 * Usage:
 *   const { refresh, isRefreshing } = usePageRefresh();
 *   <PageHeader title="…" onRefresh={refresh} isRefreshing={isRefreshing} />
 */
import { useState, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';

export function usePageRefresh() {
  const queryClient = useQueryClient();
  const [isRefreshing, setIsRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    setIsRefreshing(true);
    try {
      await queryClient.invalidateQueries();
    } finally {
      setIsRefreshing(false);
    }
  }, [queryClient]);

  return { refresh, isRefreshing };
}
