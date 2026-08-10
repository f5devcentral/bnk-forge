/**
 * useTopology — React Query hook for the namespace topology graph (D-018 P4).
 *
 * Fetches GET /api/k8s/clusters/{id}/topology?namespace=<ns>.
 * Gated on cluster reachability and a real (non-"all") namespace selection.
 */
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { useClusterReachable } from '@/hooks/useConnectivity';
import type { components } from '@/types/api-generated';

export type TopologyGraphResponse = components['schemas']['TopologyGraphResponse'];
export type TopologyNode = components['schemas']['TopologyNode'];
export type TopologyEdge = components['schemas']['TopologyEdge'];

export function useTopology(
  clusterId: number,
  namespace: string,
  options?: { enabled?: boolean },
) {
  const reachable = useClusterReachable(clusterId);
  const isNamespaceSelected = !!namespace && namespace !== 'all';

  return useQuery({
    queryKey: queryKeys.k8s.clusters.topology(clusterId, namespace),
    queryFn: () => api.getTopology(clusterId, namespace),
    enabled:
      options?.enabled !== false &&
      !!clusterId &&
      isNamespaceSelected &&
      reachable,
    staleTime: 30_000,
  });
}
