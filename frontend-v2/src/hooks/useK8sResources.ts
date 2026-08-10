/**
 * K8s resource query / mutation hooks (IMP-011: split from useK8s.ts).
 *
 * Covers: namespaces, generic resources, CRUD, scale, pod logs,
 * pod restart, describe, events, and pod containers.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { QUERY_STALE_TIME } from '@/lib/constants';
import { notify } from '@/lib/notify';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';
import { useClusterReachable } from '@/hooks/useConnectivity';

// Namespaces
export function useClusterNamespaces(clusterId: number, options?: { enabled?: boolean }) {
  const reachable = useClusterReachable(clusterId);
  return useQuery({
    queryKey: queryKeys.k8s.clusters.namespaces(clusterId),
    queryFn: () => api.getClusterNamespaces(clusterId),
    enabled: options?.enabled !== false && !!clusterId && reachable,
    staleTime: QUERY_STALE_TIME.DEFAULT, // Cache for 30 seconds
  });
}

// Resources
export function useClusterResources(
  clusterId: number,
  resourceType: string,
  params?: { namespace?: string; label_selector?: string },
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const reachable = useClusterReachable(clusterId);
  return useQuery({
    queryKey: queryKeys.k8s.clusters.resources(clusterId, resourceType, params),
    queryFn: () => api.getClusterResources(clusterId, resourceType, params),
    enabled: options?.enabled !== false && !!clusterId && !!resourceType && reachable,
    refetchInterval: options?.pollingEnabled ? 20000 : false, // Poll every 20 seconds when enabled (reduced from 5s)
    placeholderData: (previousData) => previousData,
  });
}

// Resource CRUD
export function useCreateK8sResource() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({
      clusterId,
      resourceType,
      resourceYaml,
      namespace,
      dryRun = false,
    }: {
      clusterId: number;
      resourceType: string;
      resourceYaml: string;
      namespace?: string;
      dryRun?: boolean;
    }) =>
      api.createK8sResource(clusterId, resourceType, {
        resource_yaml: resourceYaml,
        namespace,
        dry_run: dryRun,
      }),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.k8s.clusters.allResources(variables.clusterId),
      });
      if (!variables.dryRun) {
        notify.success('Resource created successfully', undefined, { category: 'cluster' });
      }
    },
  });
}

export function useUpdateK8sResource() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({
      clusterId,
      resourceType,
      resourceName,
      resourceYaml,
      namespace,
      dryRun = false,
    }: {
      clusterId: number;
      resourceType: string;
      resourceName: string;
      resourceYaml: string;
      namespace?: string;
      dryRun?: boolean;
    }) =>
      api.updateK8sResource(clusterId, resourceType, resourceName, {
        resource_yaml: resourceYaml,
        namespace,
        dry_run: dryRun,
      }),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.k8s.clusters.allResources(variables.clusterId),
      });
      if (!variables.dryRun) {
        notify.success('Resource updated successfully', undefined, { category: 'cluster' });
      }
    },
  });
}

export function useDeleteK8sResource() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({
      clusterId,
      resourceType,
      resourceName,
      namespace,
      dryRun = false,
    }: {
      clusterId: number;
      resourceType: string;
      resourceName: string;
      namespace?: string;
      dryRun?: boolean;
    }) =>
      api.deleteK8sResource(clusterId, resourceType, resourceName, {
        namespace,
        dry_run: dryRun,
      }),
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.k8s.clusters.allResources(variables.clusterId),
      });
      const message = data.message || 'Resource deleted successfully';
      notify.success(message, undefined, { category: 'cluster' });
    },
  });
}

export function useScaleDeployment() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({
      clusterId,
      deploymentName,
      namespace,
      replicas,
    }: {
      clusterId: number;
      deploymentName: string;
      namespace: string;
      replicas: number;
    }) =>
      api.scaleDeployment(clusterId, deploymentName, {
        replicas,
        namespace,
      }),
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.k8s.clusters.allResources(variables.clusterId),
      });
      const message = data.message || `Deployment scaled to ${data.replicas} replicas`;
      notify.success(message, undefined, { category: 'cluster' });
    },
  });
}

export function usePodLogs(
  clusterId: number,
  podName: string,
  namespace: string,
  options?: { container?: string; tail_lines?: number; enabled?: boolean }
) {
  const reachable = useClusterReachable(clusterId);
  return useQuery({
    // Note: includes `options` param beyond queryKeys.k8s.clusters.podLogs() to differentiate container/tail_lines
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

// Describe & Events
export function useDescribeResource(
  clusterId: number,
  resourceType: string,
  resourceName: string,
  namespace?: string,
  options?: { enabled?: boolean }
) {
  const reachable = useClusterReachable(clusterId);
  return useQuery({
    queryKey: queryKeys.k8s.clusters.describeResource(clusterId, resourceType, resourceName, namespace),
    queryFn: () => api.describeResource(clusterId, resourceType, resourceName, namespace),
    enabled: options?.enabled !== false && !!clusterId && !!resourceType && !!resourceName && reachable,
    staleTime: QUERY_STALE_TIME.DEFAULT, // Cache for 30 seconds
  });
}

export function useClusterEvents(
  clusterId: number,
  params?: { namespace?: string; resource_type?: string; resource_name?: string; event_type?: string },
  options?: { pollingEnabled?: boolean; enabled?: boolean }
) {
  const reachable = useClusterReachable(clusterId);
  return useQuery({
    queryKey: queryKeys.k8s.clusters.events(clusterId, params),
    queryFn: () => api.getClusterEvents(clusterId, params),
    enabled: options?.enabled !== false && !!clusterId && reachable,
    refetchInterval: options?.pollingEnabled ? 10000 : false, // Poll every 10 seconds when enabled
    placeholderData: (previousData) => previousData,
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
