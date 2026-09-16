/**
 * F5 BNK release-registry hooks.
 *
 * IMP-011: data/health/topology/policy hooks live in `hooks/k8s/useBnk.ts`
 * and are re-exported here for backward compatibility. Release-registry
 * hooks remain here because they are the only remaining callers of this
 * module.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { BnkReleaseRegistryResponse } from '@/types';
import { notify } from '@/lib/notify';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// Re-export canonical BNK insight hooks from the k8s domain folder.
export {
  useBnkData,
  useF5BNKHealth,
  useF5GatewayTopology,
  useF5PolicyGatewayAssociations,
} from './k8s/useBnk';

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
    refetchInterval: options?.pollingEnabled ? 30000 : false,
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
    refetchInterval: options?.pollingEnabled ? 5000 : false,
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

// ========================================================================
// BNK Release Registry (issue #217)
// ========================================================================

export function useBnkReleases(activeOnly = true) {
  return useQuery<BnkReleaseRegistryResponse>({
    queryKey: ['bnk', 'releases', activeOnly],
    queryFn: () => api.getBnkReleases(activeOnly),
    staleTime: 5 * 60 * 1000, // 5 min — registry changes infrequently
  });
}

export function useSyncBnkReleasesFromOci(clusterId: number) {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: () => api.syncBnkReleasesFromOci(clusterId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['bnk', 'releases'] });
      notify.success('OCI sync complete — release registry updated', undefined, { category: 'cluster' });
    },
  });
}
