/**
 * DataPrefetcher — eagerly warms React Query cache on app startup.
 *
 * Problem: Every page follows a waterfall pattern:
 *   mount → useProjects() → wait → useProjectClusters() → wait → useResources() → render
 * First visit to any page blocks on this sequential chain.
 *
 * Solution: Prefetch the shared "trunk" data (projects, clusters) once at app startup,
 * so by the time the user navigates to any page, the first 1-2 levels of the waterfall
 * are already cached. Pages render instantly from cache, then refetch in the background
 * if stale.
 *
 * This is a render-less component — returns null.
 */

import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { QUERY_STALE_TIME } from '@/lib/constants';

export function DataPrefetcher() {
  const queryClient = useQueryClient();

  useEffect(() => {
    // ── Tier 1: Core data needed by almost every page ──
    // These run immediately on app load. By the time the user clicks
    // any sidebar link, this data is already cached.

    queryClient.prefetchQuery({
      queryKey: queryKeys.projects.list(),
      queryFn: api.getProjects,
      staleTime: QUERY_STALE_TIME.DEFAULT,
    });

    queryClient.prefetchQuery({
      queryKey: ['k8s', 'resource-types'],
      queryFn: api.getK8sResourceTypes,
      staleTime: QUERY_STALE_TIME.VERY_LONG,
    });

    // Fetch all clusters, then chain per-project cluster lists
    queryClient.prefetchQuery({
      queryKey: ['k8s', 'clusters'],
      queryFn: api.getAllClusters,
      staleTime: QUERY_STALE_TIME.DEFAULT,
    }).then(() => {
      // After clusters are cached, prefetch per-project cluster lists
      // so navigating to K8s / BNK pages doesn't waterfall
      const data = queryClient.getQueryData<{ clusters: Array<{ project_id?: number }> }>(['k8s', 'clusters']);
      if (!data?.clusters) return;

      const projectIds = new Set<number>();
      for (const c of data.clusters) {
        if (c.project_id) projectIds.add(c.project_id);
      }

      for (const pid of projectIds) {
        queryClient.prefetchQuery({
          queryKey: ['k8s', 'clusters', 'project', pid],
          queryFn: () => api.getProjectClusters(pid),
          staleTime: QUERY_STALE_TIME.DEFAULT,
        });
      }
    });

    // ── Tier 2: Secondary data needed by specific pages ──
    // Slight delay so we don't compete with the current page's own fetches.

    const timer = setTimeout(() => {
      queryClient.prefetchQuery({
        queryKey: ['helm-repositories'],
        queryFn: () => api.listHelmRepositories(),
        staleTime: QUERY_STALE_TIME.LONG,
      });
    }, 1000);

    return () => clearTimeout(timer);
  }, [queryClient]);

  return null;
}
