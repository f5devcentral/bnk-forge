/**
 * K8s cluster CRUD hooks (IMP-011: split from useK8s.ts).
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  K8sClusterCreateRequest,
  K8sClusterUpdateRequest,
  BnkClusterConfigCreateRequest,
  BnkClusterMemberAssignRequest,
} from '@/types';
import { QUERY_STALE_TIME } from '@/lib/constants';
import { notify } from '@/lib/notify';
import { queryKeys } from '@/lib/queryKeys';
import { logger } from '@/lib/logger';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

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
    refetchInterval: options?.pollingEnabled ? 30000 : false, // Poll every 30 seconds when enabled (reduced from 10s)
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
        logger.error('EKS detection errors:', data.errors);
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

export function useConfigureBnkCluster(options?: { silent?: boolean }) {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ clusterId, data }: { clusterId: number; data: BnkClusterConfigCreateRequest }) =>
      api.configureBnkCluster(clusterId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.all });
      if (!options?.silent) {
        notify.success('BNK cluster configuration updated', undefined, { category: 'cluster' });
      }
    },
    silent: options?.silent,
  });
}

export function useAssignBnkClusterMembers(options?: { silent?: boolean }) {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ clusterId, data }: { clusterId: number; data: BnkClusterMemberAssignRequest }) =>
      api.assignBnkClusterMembers(clusterId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.bareMetal.hosts.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.dpuProvisioning.all });
      if (!options?.silent) {
        notify.success('BNK cluster members assigned successfully', undefined, { category: 'cluster' });
      }
    },
    silent: options?.silent,
  });
}
