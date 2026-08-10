/**
 * Helm Release Grouping Utilities
 *
 * Functions for grouping and filtering Helm releases by cluster and namespace
 */

import type { HelmRelease } from '@/types';

// Extended Helm Release with cluster information
export interface HelmReleaseWithCluster extends HelmRelease {
  cluster_id: number;
  cluster_name: string;
}

// Grouped data structures
export interface NamespaceWithReleases {
  name: string;
  releases: HelmReleaseWithCluster[];
}

export interface ClusterWithReleases {
  id: number;
  name: string;
  namespaces: NamespaceWithReleases[];
}

/**
 * Group releases by cluster and namespace
 */
export function groupReleasesByClusterAndNamespace(
  releasesData: Array<{ clusterId: number; clusterName: string; releases: HelmRelease[] }>
): ClusterWithReleases[] {
  const grouped = new Map<number, Map<string, HelmReleaseWithCluster[]>>();

  // Add cluster_id and cluster_name to each release
  releasesData.forEach(({ clusterId, clusterName, releases }) => {
    releases.forEach((release) => {
      const enrichedRelease: HelmReleaseWithCluster = {
        ...release,
        cluster_id: clusterId,
        cluster_name: clusterName,
      };

      if (!grouped.has(clusterId)) {
        grouped.set(clusterId, new Map());
      }
      const clusterMap = grouped.get(clusterId)!;

      if (!clusterMap.has(release.namespace)) {
        clusterMap.set(release.namespace, []);
      }
      clusterMap.get(release.namespace)!.push(enrichedRelease);
    });
  });

  // Convert to array structure and sort
  return Array.from(grouped.entries())
    .map(([clusterId, namespaces]) => {
      const firstRelease = Array.from(namespaces.values())[0]?.[0];
      return {
        id: clusterId,
        name: firstRelease?.cluster_name || `Cluster ${clusterId}`,
        namespaces: Array.from(namespaces.entries())
          .map(([name, releases]) => ({
            name,
            releases: releases.sort((a, b) => a.name.localeCompare(b.name)),
          }))
          .sort((a, b) => a.name.localeCompare(b.name)),
      };
    })
    .sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * Filter releases by search query
 */
export function filterReleasesByQuery(
  clusters: ClusterWithReleases[],
  query: string
): ClusterWithReleases[] {
  if (!query.trim()) return clusters;

  const lowerQuery = query.toLowerCase();

  return clusters
    .map((cluster) => ({
      ...cluster,
      namespaces: cluster.namespaces
        .map((ns) => ({
          ...ns,
          releases: ns.releases.filter(
            (release) =>
              release.name.toLowerCase().includes(lowerQuery) ||
              release.chart.toLowerCase().includes(lowerQuery) ||
              release.namespace.toLowerCase().includes(lowerQuery) ||
              release.cluster_name.toLowerCase().includes(lowerQuery)
          ),
        }))
        .filter((ns) => ns.releases.length > 0),
    }))
    .filter((cluster) => cluster.namespaces.length > 0);
}

/**
 * Get total release count for a cluster
 */
export function getTotalReleaseCount(cluster: ClusterWithReleases): number {
  return cluster.namespaces.reduce((sum, ns) => sum + ns.releases.length, 0);
}

/**
 * Get status color class for release status (D-020 token-only).
 */
export function getReleaseStatusColor(status: string): string {
  switch (status.toLowerCase()) {
    case 'deployed':
      return 'bg-success';
    case 'pending-install':
    case 'pending-upgrade':
      return 'bg-warning';
    case 'pending-rollback':
      return 'bg-warning';
    case 'failed':
    case 'superseded':
      return 'bg-destructive';
    case 'uninstalling':
    case 'uninstalled':
      return 'bg-muted-foreground';
    default:
      return 'bg-primary';
  }
}

/**
 * Find a release by name across all clusters
 */
export function findReleaseByName(
  clusters: ClusterWithReleases[],
  releaseName: string,
  namespace?: string
): HelmReleaseWithCluster | null {
  for (const cluster of clusters) {
    for (const ns of cluster.namespaces) {
      if (namespace && ns.name !== namespace) continue;
      const release = ns.releases.find((r) => r.name === releaseName);
      if (release) return release;
    }
  }
  return null;
}

/**
 * Get search result count summary
 */
export function getSearchResultSummary(
  filtered: ClusterWithReleases[],
  original: ClusterWithReleases[]
): string {
  const filteredCount = filtered.reduce(
    (sum, cluster) => sum + getTotalReleaseCount(cluster),
    0
  );
  const originalCount = original.reduce(
    (sum, cluster) => sum + getTotalReleaseCount(cluster),
    0
  );
  const clusterCount = filtered.length;

  if (filteredCount === originalCount) {
    return `${originalCount} release${originalCount !== 1 ? 's' : ''} in ${clusterCount} cluster${clusterCount !== 1 ? 's' : ''}`;
  }

  return `${filteredCount} result${filteredCount !== 1 ? 's' : ''} in ${clusterCount} cluster${clusterCount !== 1 ? 's' : ''}`;
}
