/**
 * FU-006: lib/helm-grouping — all grouping & filtering utilities
 */
import { describe, it, expect } from 'vitest';
import type { HelmRelease } from '@/types';
import {
  groupReleasesByClusterAndNamespace,
  filterReleasesByQuery,
  getTotalReleaseCount,
  getReleaseStatusColor,
  findReleaseByName,
  getSearchResultSummary,
  type ClusterWithReleases,
} from '../helm-grouping';

// Helper to create a mock HelmRelease
function makeRelease(overrides: Partial<HelmRelease> = {}): HelmRelease {
  return {
    name: 'nginx',
    namespace: 'default',
    revision: '1',
    updated: '2026-02-25T12:00:00Z',
    status: 'deployed',
    chart: 'nginx-1.0.0',
    app_version: '1.25.0',
    ...overrides,
  };
}

// Sample grouped data for reuse
function makeSampleClusters(): ClusterWithReleases[] {
  return groupReleasesByClusterAndNamespace([
    {
      clusterId: 1,
      clusterName: 'prod-cluster',
      releases: [
        makeRelease({ name: 'nginx', namespace: 'default' }),
        makeRelease({ name: 'redis', namespace: 'default' }),
        makeRelease({ name: 'grafana', namespace: 'monitoring', chart: 'grafana-7.0.0' }),
      ],
    },
    {
      clusterId: 2,
      clusterName: 'dev-cluster',
      releases: [makeRelease({ name: 'postgres', namespace: 'db', chart: 'postgresql-15.0.0' })],
    },
  ]);
}

describe('groupReleasesByClusterAndNamespace()', () => {
  it('groups releases by cluster and namespace', () => {
    const result = makeSampleClusters();

    expect(result).toHaveLength(2);
    // Sorted by cluster name: dev-cluster < prod-cluster
    expect(result[0].name).toBe('dev-cluster');
    expect(result[1].name).toBe('prod-cluster');
  });

  it('sorts namespaces within cluster', () => {
    const result = makeSampleClusters();
    const prod = result.find((c) => c.name === 'prod-cluster')!;

    expect(prod.namespaces).toHaveLength(2);
    expect(prod.namespaces[0].name).toBe('default');
    expect(prod.namespaces[1].name).toBe('monitoring');
  });

  it('sorts releases within namespace by name', () => {
    const result = makeSampleClusters();
    const prod = result.find((c) => c.name === 'prod-cluster')!;
    const defaultNs = prod.namespaces.find((ns) => ns.name === 'default')!;

    expect(defaultNs.releases[0].name).toBe('nginx');
    expect(defaultNs.releases[1].name).toBe('redis');
  });

  it('enriches releases with cluster_id and cluster_name', () => {
    const result = makeSampleClusters();
    const dev = result.find((c) => c.name === 'dev-cluster')!;
    const release = dev.namespaces[0].releases[0];

    expect(release.cluster_id).toBe(2);
    expect(release.cluster_name).toBe('dev-cluster');
  });

  it('handles empty input', () => {
    expect(groupReleasesByClusterAndNamespace([])).toEqual([]);
  });

  it('handles cluster with no releases', () => {
    const result = groupReleasesByClusterAndNamespace([
      { clusterId: 1, clusterName: 'empty', releases: [] },
    ]);
    expect(result).toEqual([]);
  });
});

describe('filterReleasesByQuery()', () => {
  it('returns all clusters for empty query', () => {
    const clusters = makeSampleClusters();
    expect(filterReleasesByQuery(clusters, '')).toEqual(clusters);
    expect(filterReleasesByQuery(clusters, '  ')).toEqual(clusters);
  });

  it('filters by release name', () => {
    const clusters = makeSampleClusters();
    const result = filterReleasesByQuery(clusters, 'redis');
    expect(result).toHaveLength(1);
    expect(result[0].namespaces[0].releases).toHaveLength(1);
    expect(result[0].namespaces[0].releases[0].name).toBe('redis');
  });

  it('filters by chart name', () => {
    const clusters = makeSampleClusters();
    const result = filterReleasesByQuery(clusters, 'grafana');
    expect(result).toHaveLength(1);
  });

  it('filters by cluster name', () => {
    const clusters = makeSampleClusters();
    const result = filterReleasesByQuery(clusters, 'dev-cluster');
    expect(result).toHaveLength(1);
    expect(result[0].name).toBe('dev-cluster');
  });

  it('case-insensitive filtering', () => {
    const clusters = makeSampleClusters();
    const result = filterReleasesByQuery(clusters, 'NGINX');
    expect(result).toHaveLength(1);
  });

  it('returns empty when no matches', () => {
    const clusters = makeSampleClusters();
    expect(filterReleasesByQuery(clusters, 'nonexistent')).toEqual([]);
  });
});

describe('getTotalReleaseCount()', () => {
  it('sums releases across namespaces', () => {
    const clusters = makeSampleClusters();
    const prod = clusters.find((c) => c.name === 'prod-cluster')!;
    expect(getTotalReleaseCount(prod)).toBe(3);
  });

  it('returns 0 for cluster with no namespaces', () => {
    const cluster: ClusterWithReleases = { id: 1, name: 'empty', namespaces: [] };
    expect(getTotalReleaseCount(cluster)).toBe(0);
  });
});

describe('getReleaseStatusColor()', () => {
  // D-020: assertions retuned to the semantic tokens the lib now emits.
  it('returns success for deployed', () => {
    expect(getReleaseStatusColor('deployed')).toBe('bg-success');
  });

  it('returns warning for pending states', () => {
    expect(getReleaseStatusColor('pending-install')).toBe('bg-warning');
    expect(getReleaseStatusColor('pending-upgrade')).toBe('bg-warning');
  });

  it('returns warning for pending-rollback', () => {
    expect(getReleaseStatusColor('pending-rollback')).toBe('bg-warning');
  });

  it('returns destructive for failed/superseded', () => {
    expect(getReleaseStatusColor('failed')).toBe('bg-destructive');
    expect(getReleaseStatusColor('superseded')).toBe('bg-destructive');
  });

  it('returns muted-foreground for uninstalling/uninstalled', () => {
    expect(getReleaseStatusColor('uninstalling')).toBe('bg-muted-foreground');
    expect(getReleaseStatusColor('uninstalled')).toBe('bg-muted-foreground');
  });

  it('returns primary for unknown status', () => {
    expect(getReleaseStatusColor('something-else')).toBe('bg-primary');
  });

  it('is case-insensitive', () => {
    expect(getReleaseStatusColor('DEPLOYED')).toBe('bg-success');
  });
});

describe('findReleaseByName()', () => {
  it('finds release across clusters', () => {
    const clusters = makeSampleClusters();
    const release = findReleaseByName(clusters, 'postgres');
    expect(release).not.toBeNull();
    expect(release!.cluster_name).toBe('dev-cluster');
  });

  it('filters by namespace when provided', () => {
    const clusters = makeSampleClusters();
    // nginx is in default namespace, not monitoring
    expect(findReleaseByName(clusters, 'nginx', 'monitoring')).toBeNull();
    expect(findReleaseByName(clusters, 'nginx', 'default')).not.toBeNull();
  });

  it('returns null when not found', () => {
    const clusters = makeSampleClusters();
    expect(findReleaseByName(clusters, 'nonexistent')).toBeNull();
  });
});

describe('getSearchResultSummary()', () => {
  it('shows total count when no filter applied', () => {
    const clusters = makeSampleClusters();
    const summary = getSearchResultSummary(clusters, clusters);
    expect(summary).toBe('4 releases in 2 clusters');
  });

  it('shows filtered count when filter applied', () => {
    const clusters = makeSampleClusters();
    const filtered = filterReleasesByQuery(clusters, 'postgres');
    const summary = getSearchResultSummary(filtered, clusters);
    expect(summary).toBe('1 result in 1 cluster');
  });

  it('handles singular correctly', () => {
    const single: ClusterWithReleases[] = [
      {
        id: 1,
        name: 'solo',
        namespaces: [
          {
            name: 'default',
            releases: [{ ...makeRelease(), cluster_id: 1, cluster_name: 'solo' }],
          },
        ],
      },
    ];
    expect(getSearchResultSummary(single, single)).toBe('1 release in 1 cluster');
  });
});
