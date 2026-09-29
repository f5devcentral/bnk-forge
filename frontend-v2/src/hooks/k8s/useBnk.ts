import { useCallback, useMemo } from 'react';
import { hashKey, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  GatewayTopologyResponse,
  F5PolicyGatewayAssociationsResponse,
  BnkTrafficStatsResponse,
  BnkHealthEndpointResponse,
} from '@/types';
import { POLL_INTERVALS, QUERY_STALE_TIME } from '@/lib/constants';
import { notify } from '@/lib/notify';
import { keepPreviousForCluster, queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// ========================================================================
// F5 BNK Unified Data Hook
//
// Single fetch for all BNK insight views. Returns health, topology,
// and policy data in one response. All BNK insight tabs share this
// cache key, so switching tabs is instant (no re-fetch).
// ========================================================================

// Hashes of bnkData queries whose next fetch must bypass the backend cache.
const forceNextFetch = new Set<string>();

/**
 * Refetch every cached BNK data variant of a cluster past the backend cache.
 * Use after mutations and for explicit Refresh; call it after any broader
 * invalidation of the same keys so this forced fetch is the one that runs.
 */
export function refreshBnkData(queryClient: QueryClient, clusterId: number) {
  const filters = { queryKey: queryKeys.k8s.clusters.bnkDataAll(clusterId) };
  for (const query of queryClient.getQueryCache().findAll(filters)) forceNextFetch.add(query.queryHash);
  return queryClient.invalidateQueries(filters);
}

export function useBnkRefresh(clusterId: number) {
  const queryClient = useQueryClient();
  return useCallback(() => refreshBnkData(queryClient, clusterId), [queryClient, clusterId]);
}

export function useBnkData(
  clusterId: number,
  params?: { namespace?: string },
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  // One key per namespace: `undefined`, `{}` and `{ namespace: undefined }` all mean "all namespaces".
  const namespace = params?.namespace || undefined;
  return useQuery({
    queryKey: queryKeys.k8s.clusters.bnkData(clusterId, namespace ? { namespace } : undefined),
    queryFn: ({ queryKey }) => {
      const force = forceNextFetch.delete(hashKey(queryKey)) || undefined;
      return api.getBnkData(clusterId, { namespace, force });
    },
    enabled: options?.enabled !== false && !!clusterId,
    staleTime: QUERY_STALE_TIME.DEFAULT,
    refetchInterval: options?.pollingEnabled !== false ? POLL_INTERVALS.SLOW : false,
    placeholderData: keepPreviousForCluster(clusterId),
  });
}

// Convenience selectors — each returns a slice of the unified data
export function useF5BNKHealth(
  clusterId: number,
  params?: { namespace?: string },
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useBnkData(clusterId, params, options);
  const health = query.data?.health;
  const data = useMemo(
    () => (health ? ({ ...health, cluster_id: clusterId } as BnkHealthEndpointResponse) : undefined),
    [health, clusterId],
  );
  return { ...query, data };
}

export function useF5GatewayTopology(
  clusterId: number,
  params?: { namespace?: string },
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useBnkData(clusterId, params, options);
  return {
    ...query,
    data: query.data ? {
      topology: query.data.topology,
      dataPlane: query.data.dataPlane,
      referenceGrants: query.data.referenceGrants ?? [],
      counts: query.data.topologyCounts,
      trafficStats: query.data.trafficStats,
      cluster_id: clusterId,
      namespace: params?.namespace ?? null,
    } satisfies GatewayTopologyResponse & { trafficStats?: BnkTrafficStatsResponse } : undefined,
  };
}

export function useF5PolicyGatewayAssociations(
  clusterId: number,
  params?: { namespace?: string },
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useBnkData(clusterId, params, options);
  return {
    ...query,
    data: query.data ? {
      associations: query.data.policyAssociations,
      count: query.data.policyCount,
      cluster_id: clusterId,
      namespace: params?.namespace,
      trafficStats: query.data.trafficStats,
    } as F5PolicyGatewayAssociationsResponse & { trafficStats?: BnkTrafficStatsResponse } : undefined,
  };
}

// ========================================================================
// BNK Upgrade Workflow
// ========================================================================

export function useBnkUpgradeVersions(clusterId: number, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.bnkUpgradeVersions(clusterId),
    queryFn: () => api.getBnkUpgradeVersions(clusterId),
    enabled: options?.enabled !== false && !!clusterId,
    staleTime: 5 * 60 * 1000,
  });
}

export function useBnkCurrentVersion(clusterId: number, options?: { enabled?: boolean; pollingEnabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.bnkCurrentVersion(clusterId),
    queryFn: () => api.getBnkCurrentVersion(clusterId),
    enabled: options?.enabled !== false && !!clusterId,
    refetchInterval: options?.pollingEnabled ? POLL_INTERVALS.SLOW : false,
  });
}

export function useBnkUpgradeHistory(clusterId: number, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.bnkUpgradeHistory(clusterId),
    queryFn: () => api.getBnkUpgradeHistory(clusterId),
    enabled: options?.enabled !== false && !!clusterId,
    staleTime: 30000,
  });
}

export function useBnkUpgradeDetail(clusterId: number, upgradeId: number | null, options?: { pollingEnabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.bnkUpgradeDetail(clusterId, upgradeId!),
    queryFn: () => api.getBnkUpgradeDetail(clusterId, upgradeId!),
    enabled: !!clusterId && !!upgradeId,
    refetchInterval: options?.pollingEnabled ? POLL_INTERVALS.FAST : false,
    placeholderData: (previousData) => previousData,
  });
}

export function useCreateBnkUpgradePlan() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ clusterId, targetVersion }: { clusterId: number; targetVersion: string }) =>
      api.createBnkUpgradePlan(clusterId, targetVersion),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.bnkUpgrade(variables.clusterId) });
      notify.success('Upgrade plan created', undefined, { category: 'cluster' });
    },
  });
}

export function useExecuteBnkUpgrade() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ clusterId, upgradeId }: { clusterId: number; upgradeId: number }) =>
      api.executeBnkUpgrade(clusterId, upgradeId),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.bnkUpgrade(variables.clusterId) });
      notify.success('Upgrade execution started', undefined, { category: 'cluster' });
    },
  });
}

export function useRollbackBnkUpgrade() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ clusterId, upgradeId }: { clusterId: number; upgradeId: number }) =>
      api.rollbackBnkUpgrade(clusterId, upgradeId),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.bnkUpgrade(variables.clusterId) });
      notify.success('Rollback started', undefined, { category: 'cluster' });
    },
  });
}

export function useCancelBnkUpgrade() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ clusterId, upgradeId }: { clusterId: number; upgradeId: number }) =>
      api.cancelBnkUpgrade(clusterId, upgradeId),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.bnkUpgrade(variables.clusterId) });
      notify.success('Upgrade cancelled', undefined, { category: 'cluster' });
    },
  });
}
