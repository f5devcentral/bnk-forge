/**
 * K8s operational hooks: scanner, tunnels, adaptive modules, metrics,
 * deployment rollout, and node operations (IMP-011: split from useK8s.ts).
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { QUERY_STALE_TIME } from '@/lib/constants';
import { notify, notifyError } from '@/lib/notify';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// ========================================================================
// Cluster Scanner
// ========================================================================

export function useClusterScan() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (clusterId: number) => api.scanCluster(clusterId),
    onSuccess: (_data, clusterId) => {
      // Cache the scan result so ClusterScanResults can display it
      queryClient.setQueryData(queryKeys.k8s.clusters.scan(clusterId), _data);
      notify.success('Cluster scan complete', undefined, { category: 'cluster' });
    },
  });
}

export function useClusterScanResult(clusterId: number | null) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.scan(clusterId!),
    queryFn: () => api.scanCluster(clusterId!),
    enabled: false, // Only populated by the mutation above; never auto-fetches
    staleTime: 5 * 60 * 1000, // Cache for 5 minutes
  });
}

// ========================================================================
// SSH Tunnel Management
// ========================================================================

export function useTunnelStatus(clusterId: number | null) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.tunnel(clusterId!),
    queryFn: () => api.getTunnelStatus(clusterId!),
    enabled: !!clusterId,
    refetchInterval: 30000, // Poll every 30s for health updates
    staleTime: 10000,
  });
}

export function useAllTunnels() {
  return useQuery({
    queryKey: queryKeys.k8s.tunnels(),
    queryFn: () => api.listAllTunnels(),
    refetchInterval: 30000,
    staleTime: 10000,
  });
}

export function useOpenTunnel() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (clusterId: number) => api.openTunnel(clusterId),
    onSuccess: (_data, clusterId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.tunnel(clusterId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.tunnels() });
      notify.success('SSH tunnel opened', undefined, { category: 'cluster' });
    },
    onError: (error) => notifyError(error),
  });
}

export function useCloseTunnel() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (clusterId: number) => api.closeTunnel(clusterId),
    onSuccess: (_data, clusterId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.tunnel(clusterId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.tunnels() });
      notify.success('SSH tunnel closed', undefined, { category: 'cluster' });
    },
    onError: (error) => notifyError(error),
  });
}

// ========================================================================
// Adaptive Module Selection
// ========================================================================

export function useAdaptiveModulePlan() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ clusterId, templateSlug, modulePaths, sizingProfile }: { clusterId: number; templateSlug?: string; modulePaths?: string[]; sizingProfile?: string }) =>
      api.getAdaptiveModulePlan(clusterId, templateSlug, modulePaths, sizingProfile),
    onSuccess: (_data, { clusterId, templateSlug }) => {
      queryClient.setQueryData(queryKeys.k8s.clusters.adaptiveModules(clusterId, templateSlug), _data);
      notify.success('Deployment plan generated', undefined, { category: 'cluster' });
    },
  });
}

// ========================================================================
// Resource Metrics
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
    refetchInterval: options?.pollingEnabled ? 10000 : false, // Poll every 10 seconds when enabled
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
    refetchInterval: options?.pollingEnabled ? 10000 : false, // Poll every 10 seconds when enabled
    staleTime: QUERY_STALE_TIME.SHORT, // Cache for 5 seconds (metrics change frequently)
  });
}

// ========================================================================
// Deployment Rollout Management
// ========================================================================

export function useRolloutHistory(
  clusterId: number,
  deploymentName: string,
  namespace: string,
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.rolloutHistory(clusterId, deploymentName, namespace),
    queryFn: () => api.getRolloutHistory(clusterId, deploymentName, namespace),
    enabled: options?.enabled !== false && !!clusterId && !!deploymentName && !!namespace,
    staleTime: QUERY_STALE_TIME.MEDIUM, // Cache for 10 seconds
  });
}

export function useRolloutStatus(
  clusterId: number,
  deploymentName: string,
  namespace: string,
  options?: { enabled?: boolean; pollingEnabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.rolloutStatus(clusterId, deploymentName, namespace),
    queryFn: () => api.getRolloutStatus(clusterId, deploymentName, namespace),
    enabled: options?.enabled !== false && !!clusterId && !!deploymentName && !!namespace,
    refetchInterval: options?.pollingEnabled ? 5000 : false, // Poll every 5 seconds when enabled
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
      // Invalidate rollout queries (partial key for prefix matching — no factory equivalent)
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
      // Invalidate rollout queries (partial key for prefix matching — no factory equivalent)
      queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', variables.clusterId, 'deployments', variables.deploymentName] });
      // Invalidate resources to show restarted pods
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.allResources(variables.clusterId) });
    },
  });
}

// ========================================================================
// Node Operations
// ========================================================================

export function useCordonNode() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ clusterId, nodeName }: { clusterId: number; nodeName: string }) =>
      api.cordonNode(clusterId, nodeName),
    onSuccess: (_, variables) => {
      // Invalidate nodes to show updated status
      queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', variables.clusterId, 'resources', 'node'] });
    },
  });
}

export function useUncordonNode() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ clusterId, nodeName }: { clusterId: number; nodeName: string }) =>
      api.uncordonNode(clusterId, nodeName),
    onSuccess: (_, variables) => {
      // Invalidate nodes to show updated status
      queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', variables.clusterId, 'resources', 'node'] });
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
      // Invalidate nodes and pods to show updated status
      queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', variables.clusterId, 'resources', 'node'] });
      queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', variables.clusterId, 'resources', 'pod'] });
    },
  });
}
