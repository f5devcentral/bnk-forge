import { useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { notify } from '@/lib/notify';
import { POLL_INTERVALS } from '@/lib/constants';
import type { DriftSettingsRequest, TriggerDriftCheckRequest } from '@/types';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// Query: Get drift settings for a project
export const useDriftSettings = (projectId: number | undefined) => {
  return useQuery({
    queryKey: queryKeys.driftExtended.settings(projectId),
    queryFn: () => api.getDriftSettings(projectId!),
    enabled: !!projectId,
  });
};

// Query: Get drift checks for a project
export const useDriftChecks = (
  projectId: number | undefined,
  options?: { moduleId?: number; limit?: number; offset?: number; pollingEnabled?: boolean }
) => {
  return useQuery({
    queryKey: queryKeys.driftExtended.checks(projectId, options?.moduleId, options?.limit, options?.offset),
    queryFn: () => api.getDriftChecks(projectId!, { module_id: options?.moduleId, limit: options?.limit, offset: options?.offset }),
    enabled: !!projectId,
    refetchInterval: options?.pollingEnabled ? POLL_INTERVALS.VERY_SLOW : false,
  });
};

// Query: Get single drift check details
export const useDriftCheck = (checkId: number | undefined) => {
  return useQuery({
    queryKey: queryKeys.driftExtended.check(checkId),
    queryFn: () => api.getDriftCheck(checkId!),
    enabled: !!checkId,
  });
};

// Query: Get project drift summary
export const useProjectDriftSummary = (projectId: number | undefined) => {
  return useQuery({
    queryKey: queryKeys.driftExtended.projectSummary(projectId),
    queryFn: () => api.getProjectDriftSummary(projectId!),
    enabled: !!projectId,
    refetchInterval: POLL_INTERVALS.BACKGROUND,
  });
};

// Query: Get global drift summary
export const useGlobalDriftSummary = () => {
  return useQuery({
    queryKey: queryKeys.driftExtended.globalSummary(),
    queryFn: () => api.getGlobalDriftSummary(),
    refetchInterval: POLL_INTERVALS.BACKGROUND,
  });
};

// Query: Get recent drifted modules (global, for dashboard attention section)
export const useRecentDrifted = (limit: number = 10) => {
  return useQuery({
    queryKey: queryKeys.driftExtended.recentDrifted(limit),
    queryFn: () => api.getRecentDrifted({ limit }),
    refetchInterval: POLL_INTERVALS.BACKGROUND,
  });
};

// Query: Get cluster drift status (for health dashboard)
export const useClusterDriftStatus = (clusterId: number | undefined) => {
  return useQuery({
    queryKey: queryKeys.driftExtended.clusterStatus(clusterId),
    queryFn: () => api.getClusterDriftStatus(clusterId!),
    enabled: !!clusterId,
    refetchInterval: POLL_INTERVALS.BACKGROUND,
  });
};

// Query: Get drift stats
export const useDriftStats = (options?: { projectId?: number; days?: number }) => {
  return useQuery({
    queryKey: queryKeys.driftExtended.stats(options?.projectId, options?.days),
    queryFn: () => api.getDriftStats({ project_id: options?.projectId, days: options?.days }),
    refetchInterval: POLL_INTERVALS.BACKGROUND,
  });
};

// Mutation: Update drift settings
export const useUpdateDriftSettings = () => {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ projectId, data }: { projectId: number; data: DriftSettingsRequest }) =>
      api.updateDriftSettings(projectId, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.driftExtended.settings(variables.projectId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.driftExtended.projectSummary(variables.projectId) });
    },
  });
};

// Mutation: Enable drift detection
export const useEnableDriftDetection = () => {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (projectId: number) => api.enableDriftDetection(projectId),
    onSuccess: (_, projectId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.driftExtended.settings(projectId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.driftExtended.projectSummary(projectId) });
      notify.success('Drift detection has been enabled for this project.', undefined, { category: 'system' });
    },
  });
};

// Mutation: Disable drift detection
export const useDisableDriftDetection = () => {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (projectId: number) => api.disableDriftDetection(projectId),
    onSuccess: (_, projectId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.driftExtended.settings(projectId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.driftExtended.projectSummary(projectId) });
      notify.success('Drift detection has been disabled for this project.', undefined, { category: 'system' });
    },
  });
};

// Mutation: Trigger drift check
export const useTriggerDriftCheck = () => {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ projectId, data }: { projectId: number; data?: TriggerDriftCheckRequest }) =>
      api.triggerDriftCheck(projectId, data),
    onSuccess: (result, variables) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.driftExtended.checks(variables.projectId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.driftExtended.projectSummary(variables.projectId) });
      notify.success(result.message || 'Drift check has been queued for execution.', undefined, { category: 'system' });
    },
  });
};

// Derived: Per-project drift count map (shared by Dashboard, Projects, Sidebar)
export const useProjectDriftCounts = (limit: number = 20) => {
  const { data: recentDrifted } = useRecentDrifted(limit);
  return useMemo(() => {
    const counts: Record<number, number> = {};
    if (recentDrifted) {
      for (const item of recentDrifted) {
        counts[item.project_id] = (counts[item.project_id] || 0) + 1;
      }
    }
    return counts;
  }, [recentDrifted]);
};

// Derived: Global drift count (shared by Dashboard, Sidebar)
export const useGlobalDriftCount = () => {
  const { data: driftSummary } = useGlobalDriftSummary();
  return driftSummary?.modules_with_drift ?? 0;
};

// Mutation: Trigger module drift check
export const useTriggerModuleDriftCheck = () => {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (moduleId: number) => api.triggerModuleDriftCheck(moduleId),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.driftExtended.checksAll });
      notify.success(result.message || 'Module drift check has been queued for execution.', undefined, { category: 'system' });
    },
  });
};
