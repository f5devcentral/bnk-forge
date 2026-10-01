/**
 * useTopology — React Query hook for the namespace topology graph (D-018 P4).
 *
 * Fetches GET /api/k8s/clusters/{id}/topology?namespace=<ns>.
 * Gated on cluster reachability and a real (non-"all") namespace selection.
 */
import { hashKey, useQuery, type QueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { useClusterReachable } from '@/hooks/useConnectivity';
import type { components } from '@/types/api-generated';

export type TopologyGraphResponse = components['schemas']['TopologyGraphResponse'];
export type TopologyNode = components['schemas']['TopologyNode'];
export type TopologyEdge = components['schemas']['TopologyEdge'];

// Hashes of topology queries whose next fetch must bypass the backend cache.
const forceNextFetch = new Set<string>();

/** Refetch every cached topology graph of a cluster past the backend cache (explicit Refresh). */
export function refreshTopology(queryClient: QueryClient, clusterId: number) {
  const filters = { queryKey: queryKeys.k8s.clusters.topologyAll(clusterId) };
  for (const query of queryClient.getQueryCache().findAll(filters)) forceNextFetch.add(query.queryHash);
  return queryClient.invalidateQueries(filters);
}

export function useTopology(
  clusterId: number,
  namespace: string,
  options?: { enabled?: boolean },
) {
  const reachable = useClusterReachable(clusterId);
  const isNamespaceSelected = !!namespace && namespace !== 'all';

  return useQuery({
    queryKey: queryKeys.k8s.clusters.topology(clusterId, namespace),
    queryFn: ({ queryKey }) =>
      api.getTopology(clusterId, namespace, forceNextFetch.delete(hashKey(queryKey)) || undefined),
    enabled:
      options?.enabled !== false &&
      !!clusterId &&
      isNamespaceSelected &&
      reachable,
    staleTime: 30_000,
  });
}
