/**
 * useCrds — React Query hook for CRD discovery (D-018 P1).
 *
 * Fetches all installed CRDs for a cluster via GET /api/k8s/clusters/{id}/crds.
 * Gated on cluster reachability via useClusterReachable (same pattern as
 * useClusterResources / useClusterNamespaces).
 */
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { useClusterReachable } from '@/hooks/useConnectivity';
import type { components } from '@/types/api-generated';

export type CrdListEnvelope = components['schemas']['CrdListEnvelope'];
export type CRDInfo = components['schemas']['CRDInfo'];

export function useCrds(clusterId: number, options?: { enabled?: boolean; group?: string[] }) {
  const reachable = useClusterReachable(clusterId);
  return useQuery({
    queryKey: queryKeys.k8s.clusters.crds(clusterId, options?.group ? { group: options.group } : undefined),
    queryFn: () => api.getCrds(clusterId, options?.group ? { group: options.group } : undefined),
    enabled: options?.enabled !== false && !!clusterId && reachable,
    staleTime: 30_000,
  });
}
