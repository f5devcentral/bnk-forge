import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { QUERY_STALE_TIME, POLL_INTERVALS } from '@/lib/constants';
import { useClusterReachable } from '@/hooks/useConnectivity';
import type {
  InstallChartRequest,
  UpgradeReleaseRequest,
  RollbackReleaseRequest,
} from '@/types';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

/**
 * Hook to list all Helm releases in a cluster
 */
export function useHelmReleases(
  clusterId: number | null,
  namespace?: string,
  allNamespaces?: boolean,
  options?: { enabled?: boolean }
) {
  const reachable = useClusterReachable(clusterId ?? undefined);
  return useQuery({
    queryKey: queryKeys.helm.releases.byCluster(clusterId, namespace, allNamespaces),
    queryFn: () => {
      if (!clusterId) throw new Error('No cluster selected');
      return api.listHelmReleases(clusterId, namespace, allNamespaces);
    },
    enabled: !!clusterId && (options?.enabled !== false) && reachable,
    refetchInterval: POLL_INTERVALS.SLOW,
  });
}

/**
 * Hook to get details of a specific Helm release
 */
export function useHelmRelease(
  clusterId: number | null,
  releaseName: string | null,
  namespace: string = 'default',
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.helm.releases.detail(clusterId, releaseName, namespace),
    queryFn: () => {
      if (!clusterId || !releaseName) throw new Error('Missing required parameters');
      return api.getHelmRelease(clusterId, releaseName, namespace);
    },
    enabled: !!clusterId && !!releaseName && (options?.enabled !== false),
  });
}

/**
 * Hook to get release history
 */
export function useHelmHistory(
  clusterId: number | null,
  releaseName: string | null,
  namespace: string = 'default',
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.helm.releases.history(clusterId, releaseName, namespace),
    queryFn: () => {
      if (!clusterId || !releaseName) throw new Error('Missing required parameters');
      return api.getHelmHistory(clusterId, releaseName, namespace);
    },
    enabled: !!clusterId && !!releaseName && (options?.enabled !== false),
  });
}

/**
 * Hook to get release values
 */
export function useHelmValues(
  clusterId: number | null,
  releaseName: string | null,
  namespace: string = 'default',
  allValues: boolean = false,
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.helm.releases.values(clusterId, releaseName, namespace, allValues),
    queryFn: () => {
      if (!clusterId || !releaseName) throw new Error('Missing required parameters');
      return api.getHelmValues(clusterId, releaseName, namespace, allValues);
    },
    enabled: !!clusterId && !!releaseName && (options?.enabled !== false),
  });
}

/**
 * Hook to list Helm repositories
 */
export function useHelmRepositories(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.helm.repos.all,
    queryFn: () => api.listHelmRepositories(),
    enabled: options?.enabled !== false,
    staleTime: QUERY_STALE_TIME.LONG, // 1 minute — repos rarely change, no need to refetch on every mount
  });
}

/**
 * Hook to search Helm charts
 */
export function useHelmChartSearch(
  keyword: string,
  repository?: string,
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.helm.charts.search(keyword, repository),
    queryFn: () => api.searchHelmCharts(keyword, repository),
    enabled: keyword.length >= 3 && (options?.enabled !== false), // Require at least 3 characters
    staleTime: QUERY_STALE_TIME.EXTENDED,
    gcTime: 1800000, // Keep in cache for 30 minutes
    refetchOnWindowFocus: false, // Don't refetch on window focus
    refetchOnMount: false, // Use cached data on mount if available
  });
}

/**
 * Hook to browse Helm charts from a repository
 */
export function useHelmChartBrowse(
  repository?: string | null,
  options?: { enabled?: boolean; maxResults?: number }
) {
  return useQuery({
    queryKey: queryKeys.helm.charts.browse(repository),
    queryFn: () => api.browseHelmCharts(repository || undefined, options?.maxResults || 100),
    enabled: !!repository && (options?.enabled !== false),
    staleTime: QUERY_STALE_TIME.EXTENDED,
    gcTime: 1800000, // Keep in cache for 30 minutes
    refetchOnWindowFocus: false,
    refetchOnMount: false,
  });
}

/**
 * Mutation to install a Helm chart
 */
export function useInstallHelmChart() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ clusterId, request }: { clusterId: number; request: InstallChartRequest }) =>
      api.installHelmChart(clusterId, request),
    onSuccess: () => {
      // Invalidate all releases queries for this cluster (all namespaces)
      queryClient.invalidateQueries({
        queryKey: queryKeys.helm.releases.all,
      });
    },
  });
}

/**
 * Mutation to upgrade a Helm release
 */
export function useUpgradeHelmRelease() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({
      clusterId,
      releaseName,
      request,
      namespace,
    }: {
      clusterId: number;
      releaseName: string;
      request: UpgradeReleaseRequest;
      namespace?: string;
    }) => api.upgradeHelmRelease(clusterId, releaseName, request, namespace),
    onSuccess: (_, variables) => {
      // Invalidate related queries
      queryClient.invalidateQueries({
        queryKey: queryKeys.helm.releases.all,
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.helm.releases.detail(variables.clusterId, variables.releaseName, variables.namespace),
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.helm.releases.history(variables.clusterId, variables.releaseName, variables.namespace),
      });
    },
  });
}

/**
 * Mutation to rollback a Helm release
 */
export function useRollbackHelmRelease() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({
      clusterId,
      releaseName,
      request,
      namespace,
    }: {
      clusterId: number;
      releaseName: string;
      request: RollbackReleaseRequest;
      namespace?: string;
    }) => api.rollbackHelmRelease(clusterId, releaseName, request, namespace),
    onSuccess: (_, variables) => {
      // Invalidate related queries
      queryClient.invalidateQueries({
        queryKey: queryKeys.helm.releases.all,
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.helm.releases.detail(variables.clusterId, variables.releaseName, variables.namespace),
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.helm.releases.history(variables.clusterId, variables.releaseName, variables.namespace),
      });
    },
  });
}

/**
 * Mutation to uninstall a Helm release
 */
export function useUninstallHelmRelease() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({
      clusterId,
      releaseName,
      namespace,
      keepHistory,
    }: {
      clusterId: number;
      releaseName: string;
      namespace?: string;
      keepHistory?: boolean;
    }) => api.uninstallHelmRelease(clusterId, releaseName, namespace, keepHistory),
    onSuccess: () => {
      // Invalidate all releases queries for this cluster (all namespaces)
      queryClient.invalidateQueries({
        queryKey: queryKeys.helm.releases.all,
      });
    },
  });
}

// Chart browse/search queries cache aggressively (staleTime EXTENDED, gcTime
// 30min, refetchOnMount false). Repo mutations must invalidate them too,
// otherwise an empty-charts result from before a repo was added persists.
function invalidateAllHelmCaches(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: queryKeys.helm.repos.all });
  queryClient.invalidateQueries({ queryKey: ['helm-charts-browse'] });
  queryClient.invalidateQueries({ queryKey: ['helm-charts-search'] });
}

/**
 * Mutation to add a Helm repository
 */
export function useAddHelmRepository() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({
      name,
      url,
      username,
      password,
    }: {
      name: string;
      url: string;
      username?: string;
      password?: string;
    }) => api.addHelmRepository(name, url, username, password),
    onSuccess: () => invalidateAllHelmCaches(queryClient),
  });
}

/**
 * Mutation to remove a Helm repository
 */
export function useRemoveHelmRepository() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (name: string) => api.removeHelmRepository(name),
    onSuccess: () => invalidateAllHelmCaches(queryClient),
  });
}

/**
 * Mutation to update all Helm repositories
 */
export function useUpdateHelmRepositories() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: () => api.updateHelmRepositories(),
    onSuccess: () => invalidateAllHelmCaches(queryClient),
  });
}

/**
 * Hook to list uploaded custom Helm charts
 */
export function useUploadedCharts(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.helm.charts.uploaded,
    queryFn: () => api.listUploadedCharts(),
    enabled: options?.enabled !== false,
  });
}

/**
 * Mutation to upload a custom Helm chart
 */
export function useUploadHelmChart() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (file: File) => api.uploadHelmChart(file),
    onSuccess: () => {
      // Invalidate uploaded charts query
      queryClient.invalidateQueries({
        queryKey: queryKeys.helm.charts.uploaded,
      });
    },
  });
}

/**
 * Mutation to delete an uploaded chart
 */
export function useDeleteUploadedChart() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (chartId: number) => api.deleteUploadedChart(chartId),
    onSuccess: () => {
      // Invalidate uploaded charts query
      queryClient.invalidateQueries({
        queryKey: queryKeys.helm.charts.uploaded,
      });
    },
  });
}

/**
 * Hook to get chart values
 */
export function useChartValues(
  chartId: number | null,
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.helm.charts.values(chartId),
    queryFn: () => {
      if (!chartId) throw new Error('Chart ID required');
      return api.getChartValues(chartId);
    },
    enabled: !!chartId && (options?.enabled !== false),
  });
}

/**
 * Mutation to update chart values
 */
export function useUpdateChartValues() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ chartId, values }: { chartId: number; values: string }) =>
      api.updateChartValues(chartId, values),
    onSuccess: (_, variables) => {
      // Invalidate chart values query
      queryClient.invalidateQueries({
        queryKey: queryKeys.helm.charts.values(variables.chartId),
      });
    },
  });
}

/**
 * Mutation to clone a Helm chart from a repository
 */
export function useCloneHelmChart() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ chart, version, clonedBy }: { chart: string; version?: string; clonedBy?: string }) =>
      api.cloneHelmChart(chart, version, clonedBy),
    onSuccess: () => {
      // Invalidate uploaded charts query to show the new cloned chart
      queryClient.invalidateQueries({
        queryKey: queryKeys.helm.charts.uploaded,
      });
    },
  });
}

/**
 * Hook to compare two Helm release revisions
 */
export function useCompareRevisions(
  clusterId: number | null,
  releaseName: string | null,
  revision1: number | null,
  revision2: number | null,
  namespace: string = 'default',
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.helm.releases.compare(clusterId, releaseName, revision1, revision2, namespace),
    queryFn: () => {
      if (!clusterId || !releaseName || !revision1 || !revision2) {
        throw new Error('Missing parameters');
      }
      return api.compareHelmRevisions(clusterId, releaseName, revision1, revision2, namespace);
    },
    enabled: !!clusterId && !!releaseName && !!revision1 && !!revision2 && (options?.enabled !== false),
  });
}
