import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { K8sClusterCreateRequest, K8sClusterUpdateRequest } from '@/types';
import { QUERY_STALE_TIME, POLL_INTERVALS } from '@/lib/constants';
import { notify } from '@/lib/notify';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// Stops polling when the tab is hidden. Mirrors the pattern used by
// useSystem / useFleet / ProcessMetricsBar so background tabs don't
// hammer the backend.
function useDocumentVisibility(): boolean {
  const [isVisible, setIsVisible] = useState(() =>
    typeof document !== 'undefined' ? !document.hidden : true
  );
  useEffect(() => {
    const handler = () => setIsVisible(!document.hidden);
    document.addEventListener('visibilitychange', handler);
    return () => document.removeEventListener('visibilitychange', handler);
  }, []);
  return isVisible;
}

// Resource Types
export function useK8sResourceTypes() {
  return useQuery({
    queryKey: queryKeys.k8s.resourceTypes(),
    queryFn: api.getK8sResourceTypes,
    staleTime: QUERY_STALE_TIME.VERY_LONG, // Cache for 5 minutes (resource types don't change often)
  });
}

// Clusters
export function useAllClusters() {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.all,
    queryFn: api.getAllClusters,
    staleTime: QUERY_STALE_TIME.DEFAULT, // Cache for 30 seconds
  });
}

export function useProjectClusters(projectId: number, options?: { pollingEnabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.byProject(projectId),
    queryFn: () => api.getProjectClusters(projectId),
    enabled: !!projectId,
    refetchInterval: options?.pollingEnabled ? POLL_INTERVALS.SLOW : false,
    placeholderData: (previousData) => previousData,
  });
}

export function useCluster(clusterId: number) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.detail(clusterId),
    queryFn: () => api.getCluster(clusterId),
    enabled: !!clusterId,
  });
}

export function useCreateCluster(options?: { silent?: boolean }) {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ projectId, data }: { projectId: number; data: K8sClusterCreateRequest }) =>
      api.createCluster(projectId, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.byProject(variables.projectId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.batchConnectivity() });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(variables.projectId) });
    },
    silent: options?.silent,
  });
}

export function useUpdateCluster(options?: { silent?: boolean }) {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ clusterId, data }: { clusterId: number; data: K8sClusterUpdateRequest }) =>
      api.updateCluster(clusterId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
    },
    silent: options?.silent,
  });
}

export function useDeleteCluster() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (clusterId: number) => api.deleteCluster(clusterId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      notify.success('Cluster deleted successfully', undefined, { category: 'cluster' });
    },
  });
}

export function useTestClusterConnection() {
  return useAppMutation({
    mutationFn: (clusterId: number) => api.testClusterConnection(clusterId),
    onSuccess: (data) => {
      if (data.success) {
        notify.success('Connection successful', `Kubernetes version: ${data.version || 'Unknown'}`, { category: 'cluster' });
      } else {
        notify.error('Connection test failed', data.message, { category: 'cluster' });
      }
    },
  });
}

export function useDetectEKSClusters() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (projectId: number) => api.detectEKSClusters(projectId),
    onSuccess: (data, projectId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.byProject(projectId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(projectId) });

      if (data.registered.length > 0) {
        notify.success(data.message, `Registered ${data.registered.length} EKS cluster(s)`, { category: 'cluster' });
      } else if (data.skipped.length > 0) {
        notify.info(data.message, 'All EKS clusters are already registered', { category: 'cluster' });
      } else {
        notify.info(data.message, undefined, { category: 'cluster' });
      }

      if (data.errors.length > 0) {
        notify.warning(`${data.errors.length} cluster(s) failed to register`, 'Check console for details', { category: 'cluster' });
      }
    },
  });
}

export function useRefreshClusterKubeconfig() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (clusterId: number) => api.refreshClusterKubeconfig(clusterId),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.all });
      notify.success(
        data.message || 'Kubeconfig refreshed successfully',
        data.refresh_method === 'ssh'
          ? 'Kubeconfig re-fetched from remote host via SSH'
          : 'The cluster now uses updated cloud credentials',
        { category: 'cluster' },
      );
    },
  });
}

// Connectivity Probes
export function useClusterConnectivity(clusterId: number, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.connectivity(clusterId),
    queryFn: () => api.getClusterConnectivity(clusterId),
    enabled: options?.enabled !== false && !!clusterId,
    staleTime: QUERY_STALE_TIME.DEFAULT,
  });
}

export function useBatchConnectivity(options?: { enabled?: boolean; pollingEnabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.batchConnectivity(),
    queryFn: api.getBatchConnectivity,
    enabled: options?.enabled !== false,
    staleTime: QUERY_STALE_TIME.DEFAULT,
    refetchInterval: options?.pollingEnabled ? POLL_INTERVALS.SLOW : false,
  });
}

// Namespaces
export function useClusterNamespaces(clusterId: number, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.namespaces(clusterId),
    queryFn: () => api.getClusterNamespaces(clusterId),
    enabled: options?.enabled !== false && !!clusterId,
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
  return useQuery({
    queryKey: queryKeys.k8s.clusters.resources(clusterId, resourceType, params),
    queryFn: () => api.getClusterResources(clusterId, resourceType, params),
    enabled: options?.enabled !== false && !!clusterId && !!resourceType,
    refetchInterval: options?.pollingEnabled ? POLL_INTERVALS.MEDIUM : false,
    placeholderData: (previousData) => previousData,
  });
}

// Per-cluster resource summary for the sidebar badges — count + unhealthy
// per kind, scoped to the currently-selected namespace (undefined/'all' =>
// cluster-wide). Polls every 30s while the tab is visible.
export function useClusterResourceSummary(
  clusterId: number,
  namespace: string | undefined,
  options?: { enabled?: boolean }
) {
  const isVisible = useDocumentVisibility();
  return useQuery({
    queryKey: ['k8s', 'clusters', clusterId, 'resource-summary', namespace ?? 'all'],
    queryFn: () => api.getClusterResourceSummary(clusterId, namespace),
    enabled: options?.enabled !== false && !!clusterId,
    // Backend caches for 60s; match that so we don't re-request until needed.
    staleTime: 30_000,
    refetchInterval: isVisible ? 30_000 : false,
    placeholderData: (previousData) => previousData,
  });
}
