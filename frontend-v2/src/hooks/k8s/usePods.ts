import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { QUERY_STALE_TIME, POLL_INTERVALS } from '@/lib/constants';
import { notify } from '@/lib/notify';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';
import { useClusterReachable } from '@/hooks/useConnectivity';

export function usePodLogs(
  clusterId: number,
  podName: string,
  namespace: string,
  options?: { container?: string; tail_lines?: number; enabled?: boolean }
) {
  const reachable = useClusterReachable(clusterId);
  return useQuery({
    queryKey: ['k8s', 'clusters', clusterId, 'pods', podName, 'logs', namespace, options],
    queryFn: () =>
      api.getPodLogs(clusterId, podName, {
        namespace,
        container: options?.container,
        tail_lines: options?.tail_lines || 100,
      }),
    enabled: options?.enabled !== false && !!clusterId && !!podName && !!namespace && reachable,
    staleTime: QUERY_STALE_TIME.MEDIUM, // Cache for 10 seconds
  });
}

export function useRestartPod() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({
      clusterId,
      podName,
      namespace,
    }: {
      clusterId: number;
      podName: string;
      namespace: string;
    }) =>
      api.restartPod(clusterId, podName, {
        namespace,
      }),
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.k8s.clusters.allResources(variables.clusterId),
      });
      const message = data.message || 'Pod restarted successfully';
      notify.success(message, undefined, { category: 'cluster' });
    },
  });
}

export function usePodContainers(
  clusterId: number,
  podName: string,
  namespace: string,
  options?: { enabled?: boolean }
) {
  const reachable = useClusterReachable(clusterId);
  return useQuery({
    queryKey: queryKeys.k8s.clusters.podContainers(clusterId, podName, namespace),
    queryFn: () => api.getPodContainers(clusterId, podName, namespace),
    enabled: options?.enabled !== false && !!clusterId && !!podName && !!namespace && reachable,
    staleTime: QUERY_STALE_TIME.LONG, // Cache for 1 minute (containers don't change often)
  });
}

// ========================================================================
// Advanced K8s Operations - Describe, Events, Metrics
// ========================================================================

export function useDescribeResource(
  clusterId: number,
  resourceType: string,
  resourceName: string,
  namespace?: string,
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.describeResource(clusterId, resourceType, resourceName, namespace),
    queryFn: () => api.describeResource(clusterId, resourceType, resourceName, namespace),
    enabled: options?.enabled !== false && !!clusterId && !!resourceType && !!resourceName,
    staleTime: QUERY_STALE_TIME.DEFAULT, // Cache for 30 seconds
  });
}

export function useClusterEvents(
  clusterId: number,
  params?: { namespace?: string; resource_type?: string; resource_name?: string; event_type?: string },
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.events(clusterId, params),
    queryFn: () => api.getClusterEvents(clusterId, params),
    enabled: options?.enabled !== false && !!clusterId,
    refetchInterval: options?.pollingEnabled ? POLL_INTERVALS.STANDARD : false,
    placeholderData: (previousData) => previousData,
  });
}

// ========================================================================
// Resource Metrics (Phase 2)
// ========================================================================

export function usePodMetrics(
  clusterId: number,
  params?: { namespace?: string; sort_by?: string },
  options?: { enabled?: boolean; pollingEnabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.podMetrics(clusterId, params),
    queryFn: () => api.getPodMetrics(clusterId, params),
    enabled: options?.enabled !== false && !!clusterId,
    refetchInterval: options?.pollingEnabled ? POLL_INTERVALS.STANDARD : false,
    staleTime: QUERY_STALE_TIME.SHORT, // Cache for 5 seconds (metrics change frequently)
  });
}

export function useNodeMetrics(
  clusterId: number,
  params?: { sort_by?: string },
  options?: { enabled?: boolean; pollingEnabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.nodeMetrics(clusterId, params),
    queryFn: () => api.getNodeMetrics(clusterId, params),
    enabled: options?.enabled !== false && !!clusterId,
    refetchInterval: options?.pollingEnabled ? POLL_INTERVALS.STANDARD : false,
    staleTime: QUERY_STALE_TIME.SHORT, // Cache for 5 seconds (metrics change frequently)
  });
}
