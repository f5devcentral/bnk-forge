import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  BnkHealthResponse,
  GatewayTopologyResponse,
  F5PolicyGatewayAssociationsResponse,
  BnkTrafficStatsResponse,
} from '@/types';
import { POLL_INTERVALS } from '@/lib/constants';
import { notify } from '@/lib/notify';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// ========================================================================
// F5 BNK Unified Data Hook
//
// Single fetch for all BNK insight views. Returns health, topology,
// and policy data in one response. All BNK insight tabs share this
// cache key, so switching tabs is instant (no re-fetch).
// ========================================================================

export function useBnkData(
  clusterId: number,
  params?: { namespace?: string },
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.bnkData(clusterId, params),
    queryFn: () => api.getBnkData(clusterId, params),
    enabled: options?.enabled !== false && !!clusterId,
    refetchInterval: options?.pollingEnabled !== false ? POLL_INTERVALS.SLOW : false,
    placeholderData: (previousData) => previousData,
  });
}

// Convenience selectors — each returns a slice of the unified data
export function useF5BNKHealth(
  clusterId: number,
  params?: { namespace?: string },
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const query = useBnkData(clusterId, params, options);
  return {
    ...query,
    data: query.data?.health as BnkHealthResponse | undefined,
  };
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
