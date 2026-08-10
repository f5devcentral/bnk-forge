/**
 * Read-only discovery hooks for project detail surfacing.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { discoveryApi, type DiscoveryTriggerRequest } from '@/lib/api/discovery';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';
import { useConnectivity } from '@/hooks/useConnectivity';

// Discovery uses SSH/jumphost paths, not the cluster API. Phase 1 only
// implements the cluster probe — once the SSH probe lands in Phase 2, we
// derive the SSH host from the project and gate on its reachability key.
// For now, always allow.
function useDiscoveryReachable(_projectId: number): boolean {
  // Subscribe so discovery hooks re-render when the SSH probe (Phase 2)
  // starts publishing state for this project's discovery target.
  void useConnectivity();
  return true;
}

export function useDiscoveryJobs(projectId: number, limit = 20) {
  const reachable = useDiscoveryReachable(projectId);
  return useQuery({
    queryKey: queryKeys.discovery.byProject(projectId),
    queryFn: () => discoveryApi.listDiscoveryJobs(projectId, limit),
    enabled: !!projectId && reachable,
    refetchInterval: (query) => {
      const jobs = query.state.data;
      if (
        jobs?.some(
          (job) =>
            job.status === 'pending' ||
            job.status === 'in_progress' ||
            job.connectivity_status === 'pending' ||
            job.connectivity_status === 'in_progress',
        )
      ) {
        return 3000;
      }
      return false;
    },
  });
}

export function useDiscoveryJob(projectId: number, jobId: number | null) {
  return useQuery({
    queryKey: queryKeys.discovery.job(projectId, jobId ?? 0),
    queryFn: () => discoveryApi.getDiscoveryJob(projectId, jobId as number),
    enabled: !!projectId && !!jobId,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      if (data.status === 'pending' || data.status === 'in_progress') return 3000;
      if (data.connectivity_status === 'pending' || data.connectivity_status === 'in_progress') return 3000;
      return false;
    },
  });
}

export function useTriggerDiscovery(projectId: number) {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (payload: DiscoveryTriggerRequest) => discoveryApi.triggerDiscovery(projectId, payload),
    onSuccess: async (job) => {
      // Use refetchQueries (not invalidateQueries) so the fresh "pending" status
      // is in cache before the polling refetchInterval evaluator runs.
      await queryClient.refetchQueries({ queryKey: queryKeys.discovery.byProject(projectId) });
      queryClient.setQueryData(queryKeys.discovery.job(projectId, job.id), job);
    },
  });
}

export function useRerunDiscoveryJob(projectId: number) {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (jobId: number) => discoveryApi.rerunDiscoveryJob(projectId, jobId),
    onSuccess: async (job) => {
      await queryClient.refetchQueries({ queryKey: queryKeys.discovery.byProject(projectId) });
      queryClient.setQueryData(queryKeys.discovery.job(projectId, job.id), job);
    },
  });
}

export function useDeleteDiscoveryJob(projectId: number) {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (jobId: number) => discoveryApi.deleteDiscoveryJob(projectId, jobId),
    onSuccess: (_data, jobId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.discovery.byProject(projectId) });
      queryClient.removeQueries({ queryKey: queryKeys.discovery.job(projectId, jobId) });
    },
  });
}

export function useRegisterNodeAsHost(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (nodeId: number) =>
      discoveryApi.registerNodeAsHost(projectId, nodeId),
    onSuccess: () => {
      // The bare-metal hosts query is keyed under a different feature
      // tree; invalidate by string-prefix match so any list/grid
      // showing them refetches without us having to wire each one.
      qc.invalidateQueries({
        predicate: (q) =>
          Array.isArray(q.queryKey)
          && q.queryKey[0] === 'bareMetal',
      });
    },
  });
}

export function useRegisterAllHostsInJob(projectId: number) {
  const qc = useQueryClient();
  return useAppMutation({
    mutationFn: (jobId: number) =>
      discoveryApi.registerAllHostsInJob(projectId, jobId),
    onSuccess: () => {
      qc.invalidateQueries({
        predicate: (q) =>
          Array.isArray(q.queryKey)
          && q.queryKey[0] === 'bareMetal',
      });
    },
  });
}
