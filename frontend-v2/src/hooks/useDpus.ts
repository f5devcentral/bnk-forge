/**
 * React Query hooks for per-project DPUs and DPU project settings.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  dpusApi,
  type DpuCreate,
  type DpuResetType,
  type DpuUpdate,
  type ProjectDpuSettingsUpdate,
} from '@/lib/api/dpus';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

export function useDpus(projectId: number) {
  return useQuery({
    queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId),
    queryFn: () => dpusApi.list(projectId),
    enabled: !!projectId,
  });
}

export function useDpu(projectId: number, dpuId: number | null) {
  return useQuery({
    queryKey: queryKeys.dpuProvisioning.dpus.detail(projectId, dpuId ?? 0),
    queryFn: () => dpusApi.get(projectId, dpuId as number),
    enabled: !!projectId && !!dpuId,
  });
}

export function useCreateDpu(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (data: DpuCreate) => dpusApi.create(projectId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

export function useUpdateDpu(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: ({ dpuId, data }: { dpuId: number; data: DpuUpdate }) =>
      dpusApi.update(projectId, dpuId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

export function useDeleteDpu(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (dpuId: number) => dpusApi.delete(projectId, dpuId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

export function useDpuSettings(projectId: number) {
  return useQuery({
    queryKey: queryKeys.dpuProvisioning.settings.byProject(projectId),
    queryFn: () => dpusApi.getSettings(projectId),
    enabled: !!projectId,
  });
}

export function useUpdateDpuSettings(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (data: ProjectDpuSettingsUpdate) => dpusApi.updateSettings(projectId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.settings.byProject(projectId) });
    },
  });
}

export function useEnableRshimOnBmc(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (dpuId: number) => dpusApi.enableRshimOnBmc(projectId, dpuId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

export function useDisableRshim(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (dpuId: number) => dpusApi.disableRshim(projectId, dpuId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

export function useConvertToEthernet(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (dpuId: number) => dpusApi.convertToEthernet(projectId, dpuId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

export function useRegisterDpuHost(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (dpuId: number) => dpusApi.registerHost(projectId, dpuId),
    onSuccess: () => {
      // Invalidate any Bare Metal-tab queries so the newly-registered
      // host shows up there immediately. We don't wire each query key
      // explicitly — anything starting with 'bareMetal' should refetch.
      qc.invalidateQueries({
        predicate: (q) =>
          Array.isArray(q.queryKey)
          && q.queryKey[0] === 'bareMetal',
      });
    },
  });
}

export function useDiscoverDpu(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (dpuId: number) => dpusApi.discover(projectId, dpuId),
    onSuccess: async () => {
      // Use refetchQueries so the fresh "running" status is in cache
      // before the useDpusWithPolling refetchInterval evaluator runs.
      await qc.refetchQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

/**
 * Polls the DPU list every 2s while any DPU has `last_discovery_status === 'running'`.
 * Once all have settled, refetch interval reverts to no polling.
 */
export function useDpusWithPolling(projectId: number) {
  return useQuery({
    queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId),
    queryFn: () => dpusApi.list(projectId),
    enabled: !!projectId,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      const inFlight = data.dpus.some(
        (d) =>
          d.last_discovery_status === 'running'
          || d.dpu_os_status === 'probing'
          || d.flash_status === 'uploading'
          || d.flash_status === 'waiting_reboot',
      );
      return inFlight ? 2000 : false;
    },
  });
}

export function useResetDpu(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: ({ dpuId, resetType }: { dpuId: number; resetType: DpuResetType }) =>
      dpusApi.resetDpu(projectId, dpuId, resetType),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

export function useResetDpuNic(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: ({ dpuId }: { dpuId: number }) => dpusApi.resetDpuNic(projectId, dpuId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

export function useAutoAssignVlanIps(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: () => dpusApi.autoAssignVlanIps(projectId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

export function useDpuInventory(projectId: number, dpuId: number | null) {
  return useQuery({
    queryKey: [...queryKeys.dpuProvisioning.dpus.detail(projectId, dpuId ?? 0), 'inventory'],
    queryFn: () => dpusApi.getInventory(projectId, dpuId as number),
    enabled: !!projectId && !!dpuId,
  });
}

export function useDiscoverAllDpus(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: () => dpusApi.discoverAll(projectId),
    onSuccess: async () => {
      await qc.refetchQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

export function useProbeDpuOs(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (dpuId: number) => dpusApi.probeDpuOs(projectId, dpuId),
    onSuccess: async () => {
      await qc.refetchQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

export function useProbeDpuOsAll(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: () => dpusApi.probeDpuOsAll(projectId),
    onSuccess: async () => {
      await qc.refetchQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

// DPU↔DPU connectivity matrix (Phase 3). The button enqueues a Celery
// task; the hook below polls the GET endpoint while status is
// in_progress. Polling stops once status flips to completed/failed/
// skipped, matching the discovery matrix pattern.
export function useRunDpuConnectivityMatrix(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: () => dpusApi.runDpuConnectivityMatrix(projectId),
    onSuccess: async () => {
      await qc.refetchQueries({
        queryKey: queryKeys.dpuProvisioning.connectivityMatrix.byProject(projectId),
      });
    },
  });
}

export function useDpuConnectivityMatrix(projectId: number) {
  return useQuery({
    queryKey: queryKeys.dpuProvisioning.connectivityMatrix.byProject(projectId),
    queryFn: () => dpusApi.getDpuConnectivityMatrix(projectId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'in_progress' || status === 'pending' ? 2000 : false;
    },
  });
}

export function useFlashDpu(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (dpuId: number) => dpusApi.flashDpu(projectId, dpuId),
    onSuccess: async () => {
      await qc.refetchQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

export function useFlashAllDpus(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: () => dpusApi.flashAllDpus(projectId),
    onSuccess: async () => {
      await qc.refetchQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

export function useCancelFlash(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (dpuId: number) => dpusApi.cancelFlash(projectId, dpuId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

export function useRenderBfConf(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (dpuId: number) => dpusApi.renderBfConf(projectId, dpuId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}

export function useResetDpuToDefaults(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: ({
      dpuId, opts,
    }: {
      dpuId: number;
      opts: { bios: boolean; bmc: boolean; nic: boolean; bmc_deep?: boolean };
    }) => dpusApi.resetToDefaults(projectId, dpuId, opts),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.dpus.byProject(projectId) });
    },
  });
}
