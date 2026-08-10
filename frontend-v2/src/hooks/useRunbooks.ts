/**
 * React Query hooks for Runbook Automation (UX-010)
 */
import { useQuery } from '@tanstack/react-query';
import { runbooksApi } from '@/lib/api/runbooks';

import { QUERY_STALE_TIME } from '@/lib/constants';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

/**
 * Fetch the list of available runbooks.
 * Stale time is long since runbook definitions don't change at runtime.
 */
export function useRunbooks() {
  return useQuery({
    queryKey: ['runbooks'],
    queryFn: runbooksApi.listRunbooks,
    staleTime: QUERY_STALE_TIME.VERY_LONG,
  });
}

/**
 * Execute a runbook against a cluster.
 * Returns the full result with pass/fail per step.
 */
export function useExecuteRunbook() {
  return useAppMutation({
    mutationFn: ({ runbookId, clusterId }: { runbookId: string; clusterId: number }) =>
      runbooksApi.executeRunbook(runbookId, { cluster_id: clusterId }),
  });
}
