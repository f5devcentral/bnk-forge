import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { QUERY_STALE_TIME, POLL_INTERVALS } from '@/lib/constants';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';
import { useClusterReachable } from '@/hooks/useConnectivity';

// ========================================================================
// Deployment Rollout Management (Phase 3)
// ========================================================================

export function useRolloutHistory(
  clusterId: number,
  deploymentName: string,
  namespace: string,
  options?: { enabled?: boolean }
) {
  const reachable = useClusterReachable(clusterId);
  return useQuery({
    queryKey: queryKeys.k8s.clusters.rolloutHistory(clusterId, deploymentName, namespace),
    queryFn: () => api.getRolloutHistory(clusterId, deploymentName, namespace),
    enabled: options?.enabled !== false && !!clusterId && !!deploymentName && !!namespace && reachable,
    staleTime: QUERY_STALE_TIME.MEDIUM, // Cache for 10 seconds
  });
}

export function useRolloutStatus(
  clusterId: number,
  deploymentName: string,
  namespace: string,
  options?: { enabled?: boolean; pollingEnabled?: boolean }
) {
  const reachable = useClusterReachable(clusterId);
  return useQuery({
    queryKey: queryKeys.k8s.clusters.rolloutStatus(clusterId, deploymentName, namespace),
    queryFn: () => api.getRolloutStatus(clusterId, deploymentName, namespace),
    enabled: options?.enabled !== false && !!clusterId && !!deploymentName && !!namespace && reachable,
    refetchInterval: options?.pollingEnabled ? POLL_INTERVALS.FAST : false, // Poll every 5 seconds when enabled
    staleTime: QUERY_STALE_TIME.REALTIME, // Cache for 3 seconds (status changes frequently during rollout)
  });
}

export function useRolloutUndo() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({
      clusterId,
      deploymentName,
      namespace,
      revision,
    }: {
      clusterId: number;
      deploymentName: string;
      namespace: string;
      revision?: number;
    }) => api.rolloutUndo(clusterId, deploymentName, namespace, revision),
    onSuccess: (_, variables) => {
      // Invalidate rollout queries
      queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', variables.clusterId, 'deployments', variables.deploymentName] });
      // Invalidate resources to show updated deployment
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.allResources(variables.clusterId) });
    },
  });
}

export function useRolloutRestart() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({
      clusterId,
      deploymentName,
      namespace,
    }: {
      clusterId: number;
      deploymentName: string;
      namespace: string;
    }) => api.rolloutRestart(clusterId, deploymentName, namespace),
    onSuccess: (_, variables) => {
      // Invalidate rollout queries
      queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', variables.clusterId, 'deployments', variables.deploymentName] });
      // Invalidate resources to show restarted pods
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.allResources(variables.clusterId) });
    },
  });
}

// ========================================================================
// Node Operations (Phase 3)
// ========================================================================

export function useCordonNode() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ clusterId, nodeName }: { clusterId: number; nodeName: string }) =>
      api.cordonNode(clusterId, nodeName),
    onSuccess: (_, variables) => {
      // Invalidate resources to show updated node status
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.allResources(variables.clusterId) });
    },
  });
}

export function useUncordonNode() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ clusterId, nodeName }: { clusterId: number; nodeName: string }) =>
      api.uncordonNode(clusterId, nodeName),
    onSuccess: (_, variables) => {
      // Invalidate resources to show updated node status
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.allResources(variables.clusterId) });
    },
  });
}

export function useDrainNode() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({
      clusterId,
      nodeName,
      options,
    }: {
      clusterId: number;
      nodeName: string;
      options?: { ignore_daemonsets?: boolean; delete_emptydir_data?: boolean; force?: boolean; grace_period?: number };
    }) => api.drainNode(clusterId, nodeName, options),
    onSuccess: (_, variables) => {
      // Invalidate resources to show updated node and pod status
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.allResources(variables.clusterId) });
    },
  });
}
