/**
 * Custom hook to fetch Helm releases from all clusters
 */

import { useQueries } from '@tanstack/react-query';
import { useAllClusters } from './useK8s';
import { api } from '@/lib/api';
import { QUERY_STALE_TIME } from '@/lib/constants';
import { groupReleasesByClusterAndNamespace, type ClusterWithReleases } from '@/lib/helm-grouping';

export function useAllHelmReleases() {
  const { data: clustersData } = useAllClusters();
  const clusters = clustersData?.clusters || [];

  // Fetch releases from each cluster
  const queries = useQueries({
    queries: clusters.map((cluster) => ({
      queryKey: ['helm-releases', cluster.id, 'all-namespaces'],
      queryFn: () => api.listHelmReleases(cluster.id, undefined, true),
      enabled: !!cluster.id,
      staleTime: QUERY_STALE_TIME.DEFAULT,
      refetchInterval: QUERY_STALE_TIME.DEFAULT,
      // Don't fail the entire query if one cluster fails
      retry: 1,
    })),
  });

  // Check if all queries are loading
  const isLoading = queries.some((q) => q.isLoading);

  // Check if any query has an error
  const hasError = queries.some((q) => q.isError);

  // Combine results from all clusters
  const releasesData = clusters
    .map((cluster, index) => ({
      clusterId: cluster.id,
      clusterName: cluster.name,
      releases: queries[index]?.data?.releases || [],
    }))
    .filter((data) => data.releases.length > 0); // Filter out clusters with no releases

  // Group by cluster and namespace
  const groupedReleases: ClusterWithReleases[] = groupReleasesByClusterAndNamespace(releasesData);

  // Calculate total counts
  const totalReleases = groupedReleases.reduce(
    (sum, cluster) =>
      sum + cluster.namespaces.reduce((nsSum, ns) => nsSum + ns.releases.length, 0),
    0
  );

  const totalClusters = groupedReleases.length;

  return {
    data: groupedReleases,
    isLoading,
    hasError,
    totalReleases,
    totalClusters,
    // Expose individual query results for debugging
    queries,
  };
}
