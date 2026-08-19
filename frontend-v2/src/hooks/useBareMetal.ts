/**
 * React Query hooks for bare-metal DPU deployment.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  bareMetalHostsApi,
  bareMetalDeploymentsApi,
  deployableReleasesApi,
} from '@/lib/api/bare-metal';
import { queryKeys } from '@/lib/queryKeys';
import type {
  BareMetalHostCreate,
  BareMetalHostUpdate,
  BareMetalDiscoveryRequest,
  BareMetalDeploymentCreate,
  BareMetalDeploymentResumeRequest,
} from '@/types/bare-metal';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// --- Host hooks ---

export function useBareMetalHosts(projectId: number) {
  return useQuery({
    queryKey: queryKeys.bareMetal.hosts.byProject(projectId),
    queryFn: () => bareMetalHostsApi.listHosts(projectId),
    enabled: !!projectId,
  });
}

/**
 * Polls the host list every 2s while any host has an active discovery status.
 * Once all have settled (completed/failed/null), polling stops.
 */
export function useBareMetalHostsWithPolling(projectId: number) {
  return useQuery({
    queryKey: queryKeys.bareMetal.hosts.byProject(projectId),
    queryFn: () => bareMetalHostsApi.listHosts(projectId),
    enabled: !!projectId,
    refetchInterval: (query) => {
      const hosts = query.state.data;
      if (!hosts) return false;
      const anyActive = hosts.some(
        (h) =>
          h.last_discovery_status !== null &&
          h.last_discovery_status !== 'completed' &&
          h.last_discovery_status !== 'failed',
      );
      return anyActive ? 2000 : false;
    },
  });
}

export function useBareMetalHost(projectId: number, hostId: number | null) {
  return useQuery({
    queryKey: queryKeys.bareMetal.hosts.detail(projectId, hostId ?? 0),
    queryFn: () => bareMetalHostsApi.getHost(projectId, hostId as number),
    enabled: !!projectId && !!hostId,
  });
}

export function useCreateBareMetalHost(projectId: number) {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (data: BareMetalHostCreate) => bareMetalHostsApi.createHost(projectId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.bareMetal.hosts.byProject(projectId) });
    },
  });
}

export function useUpdateBareMetalHost(projectId: number) {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ hostId, data }: { hostId: number; data: BareMetalHostUpdate }) =>
      bareMetalHostsApi.updateHost(projectId, hostId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.bareMetal.hosts.byProject(projectId) });
    },
  });
}

export function useDeleteBareMetalHost(projectId: number) {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (hostId: number) => bareMetalHostsApi.deleteHost(projectId, hostId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.bareMetal.hosts.byProject(projectId) });
    },
  });
}

export function useTriggerBareMetalDiscovery(projectId: number) {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ hostId, data }: { hostId: number; data: BareMetalDiscoveryRequest }) =>
      bareMetalHostsApi.triggerDiscovery(projectId, hostId, data),
    onSuccess: async () => {
      // Use refetchQueries (not invalidateQueries) so the fresh "pending" status
      // is in cache before the polling refetchInterval evaluator runs.
      // invalidateQueries marks stale but defers fetch; the interval evaluator
      // can fire on stale "completed" data and decide not to poll.
      await queryClient.refetchQueries({ queryKey: queryKeys.bareMetal.hosts.byProject(projectId) });
    },
  });
}

export function useBareMetalDiscoveryResult(projectId: number, hostId: number | null) {
  return useQuery({
    queryKey: queryKeys.bareMetal.hosts.discoveryResult(projectId, hostId ?? 0),
    queryFn: () => bareMetalHostsApi.getDiscoveryResult(projectId, hostId as number),
    enabled: !!projectId && !!hostId,
    staleTime: 5 * 60 * 1000, // Cache for 5 minutes
  });
}

// --- Deployment hooks ---

export function useBareMetalDeployments(projectId: number, hostId?: number) {
  return useQuery({
    queryKey: hostId
      ? queryKeys.bareMetal.deployments.byHost(projectId, hostId)
      : queryKeys.bareMetal.deployments.byProject(projectId),
    queryFn: () => bareMetalDeploymentsApi.listDeployments(projectId, hostId),
    enabled: !!projectId,
  });
}

export function useBareMetalDeployment(projectId: number, deploymentId: number | null) {
  return useQuery({
    queryKey: queryKeys.bareMetal.deployments.detail(projectId, deploymentId ?? 0),
    queryFn: () => bareMetalDeploymentsApi.getDeployment(projectId, deploymentId as number),
    enabled: !!projectId && !!deploymentId,
    // Poll while in progress
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (
        status === 'pending' ||
        status === 'discovering' ||
        status === 'planning' ||
        status === 'in_progress'
      ) {
        return 3000; // 3s poll
      }
      return false;
    },
  });
}

export function useCreateBareMetalDeployment(projectId: number) {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (data: BareMetalDeploymentCreate) =>
      bareMetalDeploymentsApi.createDeployment(projectId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.bareMetal.deployments.byProject(projectId),
      });
    },
  });
}

export function useResumeBareMetalDeployment(projectId: number) {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({
      deploymentId,
      data,
    }: {
      deploymentId: number;
      data: BareMetalDeploymentResumeRequest;
    }) => bareMetalDeploymentsApi.resumeDeployment(projectId, deploymentId, data),
    onSuccess: async (_result, { deploymentId }) => {
      // Use refetchQueries so the fresh "in_progress" status is in cache
      // before the useBareMetalDeployment polling refetchInterval evaluator runs.
      await queryClient.refetchQueries({
        queryKey: queryKeys.bareMetal.deployments.detail(projectId, deploymentId),
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.bareMetal.deployments.byProject(projectId),
      });
    },
  });
}

export function useCancelBareMetalDeployment(projectId: number) {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (deploymentId: number) =>
      bareMetalDeploymentsApi.cancelDeployment(projectId, deploymentId),
    onSuccess: (_result, deploymentId) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.bareMetal.deployments.detail(projectId, deploymentId),
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.bareMetal.deployments.byProject(projectId),
      });
    },
  });
}

export function useDeploymentStepLogs(
  projectId: number,
  deploymentId: number | null,
  stepIndex: number | null
) {
  return useQuery({
    queryKey: queryKeys.bareMetal.deployments.stepLogs(
      projectId,
      deploymentId ?? 0,
      stepIndex ?? 0
    ),
    queryFn: () =>
      bareMetalDeploymentsApi.getStepLogs(
        projectId,
        deploymentId as number,
        stepIndex as number
      ),
    enabled: !!projectId && !!deploymentId && stepIndex !== null,
  });
}

// --- Deployable Release hooks ---

export function useDeployableReleases() {
  return useQuery({
    queryKey: queryKeys.bareMetal.releases.all,
    queryFn: () => deployableReleasesApi.listReleases(),
  });
}

export function useDeployableRelease(releaseId: number | null) {
  return useQuery({
    queryKey: queryKeys.bareMetal.releases.detail(releaseId ?? 0),
    queryFn: () => deployableReleasesApi.getRelease(releaseId as number),
    enabled: !!releaseId,
  });
}

export function useActivateDeployableRelease() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ releaseId, isActive }: { releaseId: number; isActive: boolean }) =>
      deployableReleasesApi.activate(releaseId, isActive),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.bareMetal.releases.all });
    },
  });
}

export function useSetDefaultDeployableRelease() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (releaseId: number) => deployableReleasesApi.setDefault(releaseId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.bareMetal.releases.all });
    },
  });
}
