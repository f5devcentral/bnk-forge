/**
 * Tests for useAllHelmReleases hook
 *
 * Backend contract:
 *   GET /api/k8s/clusters → {clusters: [...], count: N} (routes/k8s_clusters.py)
 *   GET /api/k8s/{clusterId}/helm/releases → {success: true, releases: [...], count: N}
 *     (routes/helm.py L78-108, response_model NOT set — returns plain dict)
 *     Schema: schemas/helm.py HelmReleaseListResponse exists but is NOT wired to route.
 *     Actual response has success+count fields the schema lacks.
 *
 * Covers: parallel cluster queries, data aggregation, grouping by cluster/namespace,
 * loading/error states, empty clusters, total counts.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { useAllHelmReleases } from '@/hooks/useAllHelmReleases';
import React from 'react';

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

// Helper: install cluster + helm handlers for a given set of clusters and releases
function setupClusterAndReleaseHandlers(
  clusters: Array<{ id: number; name: string }>,
  releasesByCluster: Record<number, Array<{ name: string; namespace: string; revision: string; status: string; chart: string; app_version: string; updated: string }>>
) {
  server.use(
    http.get('*/api/k8s/clusters', ({ request }) => {
      const url = new URL(request.url);
      if (url.pathname !== '/api/k8s/clusters') return;
      return HttpResponse.json({
        clusters: clusters.map((c) => ({
          id: c.id,
          name: c.name,
          project_id: 1,
          cluster_type: 'eks',
          status: 'connected',
          api_server: `https://${c.name}.example.com`,
          version: '1.28',
          node_count: 3,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
        })),
        count: clusters.length,
      });
    }),
    http.get('*/api/k8s/:clusterId/helm/releases', ({ params, request }) => {
      const url = new URL(request.url);
      if (!url.pathname.endsWith('/helm/releases')) return;
      const clusterId = Number(params.clusterId);
      const releases = releasesByCluster[clusterId] || [];
      return HttpResponse.json({
        success: true,
        releases,
        count: releases.length,
      });
    })
  );
}

// ============================================================================
// Basic functionality
// ============================================================================

describe('useAllHelmReleases', () => {
  it('returns grouped releases from multiple clusters', async () => {
    setupClusterAndReleaseHandlers(
      [
        { id: 1, name: 'dev-cluster' },
        { id: 2, name: 'prod-cluster' },
      ],
      {
        1: [
          { name: 'nginx', namespace: 'default', revision: '1', status: 'deployed', chart: 'nginx-1.0.0', app_version: '1.0', updated: '2026-01-01T00:00:00Z' },
        ],
        2: [
          { name: 'redis', namespace: 'cache', revision: '2', status: 'deployed', chart: 'redis-7.0.0', app_version: '7.0', updated: '2026-01-01T00:00:00Z' },
          { name: 'postgres', namespace: 'database', revision: '1', status: 'deployed', chart: 'postgresql-15.0.0', app_version: '15.0', updated: '2026-01-01T00:00:00Z' },
        ],
      }
    );

    const { result } = renderHook(() => useAllHelmReleases(), {
      wrapper: createWrapper(),
    });

    // Wait for helm queries to complete (not just cluster query)
    await waitFor(() => {
      expect(result.current.totalReleases).toBe(3);
    });

    expect(result.current.hasError).toBe(false);
    expect(result.current.totalClusters).toBe(2);
    expect(result.current.data).toHaveLength(2);
  });

  it('returns empty data when no clusters exist', async () => {
    setupClusterAndReleaseHandlers([], {});

    const { result } = renderHook(() => useAllHelmReleases(), {
      wrapper: createWrapper(),
    });

    // With no clusters, useQueries gets an empty array — no loading state
    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.data).toHaveLength(0);
    expect(result.current.totalReleases).toBe(0);
    expect(result.current.totalClusters).toBe(0);
    expect(result.current.hasError).toBe(false);
  });

  it('filters out clusters with no releases', async () => {
    setupClusterAndReleaseHandlers(
      [
        { id: 1, name: 'dev-cluster' },
        { id: 2, name: 'empty-cluster' },
      ],
      {
        1: [
          { name: 'nginx', namespace: 'default', revision: '1', status: 'deployed', chart: 'nginx-1.0.0', app_version: '1.0', updated: '2026-01-01T00:00:00Z' },
        ],
        2: [], // no releases
      }
    );

    const { result } = renderHook(() => useAllHelmReleases(), {
      wrapper: createWrapper(),
    });

    // Wait for helm queries to complete
    await waitFor(() => {
      expect(result.current.queries.length).toBe(2);
      expect(result.current.queries.every((q) => q.isSuccess)).toBe(true);
    });

    // Only dev-cluster should appear in grouped data
    expect(result.current.totalClusters).toBe(1);
    expect(result.current.totalReleases).toBe(1);
    expect(result.current.data[0].name).toBe('dev-cluster');
  });

  // ============================================================================
  // Grouping
  // ============================================================================

  it('groups releases by namespace within a cluster', async () => {
    setupClusterAndReleaseHandlers(
      [{ id: 1, name: 'dev-cluster' }],
      {
        1: [
          { name: 'nginx', namespace: 'web', revision: '1', status: 'deployed', chart: 'nginx-1.0.0', app_version: '1.0', updated: '2026-01-01T00:00:00Z' },
          { name: 'apache', namespace: 'web', revision: '1', status: 'deployed', chart: 'apache-2.0.0', app_version: '2.0', updated: '2026-01-01T00:00:00Z' },
          { name: 'redis', namespace: 'cache', revision: '1', status: 'deployed', chart: 'redis-7.0.0', app_version: '7.0', updated: '2026-01-01T00:00:00Z' },
        ],
      }
    );

    const { result } = renderHook(() => useAllHelmReleases(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.totalReleases).toBe(3);
    });

    expect(result.current.totalClusters).toBe(1);

    const cluster = result.current.data[0];
    expect(cluster.namespaces).toHaveLength(2);

    // Namespaces sorted alphabetically
    expect(cluster.namespaces[0].name).toBe('cache');
    expect(cluster.namespaces[0].releases).toHaveLength(1);
    expect(cluster.namespaces[1].name).toBe('web');
    expect(cluster.namespaces[1].releases).toHaveLength(2);
  });

  it('sorts releases alphabetically within a namespace', async () => {
    setupClusterAndReleaseHandlers(
      [{ id: 1, name: 'cluster-a' }],
      {
        1: [
          { name: 'zebra-app', namespace: 'default', revision: '1', status: 'deployed', chart: 'z-1.0.0', app_version: '1.0', updated: '2026-01-01T00:00:00Z' },
          { name: 'alpha-app', namespace: 'default', revision: '1', status: 'deployed', chart: 'a-1.0.0', app_version: '1.0', updated: '2026-01-01T00:00:00Z' },
          { name: 'mid-app', namespace: 'default', revision: '1', status: 'deployed', chart: 'm-1.0.0', app_version: '1.0', updated: '2026-01-01T00:00:00Z' },
        ],
      }
    );

    const { result } = renderHook(() => useAllHelmReleases(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.totalReleases).toBe(3);
    });

    const releases = result.current.data[0].namespaces[0].releases;
    expect(releases[0].name).toBe('alpha-app');
    expect(releases[1].name).toBe('mid-app');
    expect(releases[2].name).toBe('zebra-app');
  });

  // ============================================================================
  // Error handling
  // ============================================================================

  it('reports hasError when a cluster query fails', async () => {
    server.use(
      http.get('*/api/k8s/clusters', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname !== '/api/k8s/clusters') return;
        return HttpResponse.json({
          clusters: [
            { id: 1, name: 'dev-cluster', project_id: 1, cluster_type: 'eks', status: 'connected', api_server: 'https://dev.example.com', version: '1.28', node_count: 3, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
          ],
          count: 1,
        });
      }),
      http.get('*/api/k8s/:clusterId/helm/releases', ({ request }) => {
        const url = new URL(request.url);
        if (!url.pathname.endsWith('/helm/releases')) return;
        return HttpResponse.json(
          { error: { message: 'Connection refused' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useAllHelmReleases(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      // Wait for the helm query to fail (after retry: 1)
      expect(result.current.queries.length).toBe(1);
      expect(result.current.queries[0].isError).toBe(true);
    }, { timeout: 5000 });

    expect(result.current.hasError).toBe(true);
  });

  it('shows loading state while queries are in flight', async () => {
    // Use a delayed handler to keep queries loading
    server.use(
      http.get('*/api/k8s/clusters', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname !== '/api/k8s/clusters') return;
        return HttpResponse.json({
          clusters: [
            { id: 1, name: 'slow-cluster', project_id: 1, cluster_type: 'eks', status: 'connected', api_server: 'https://slow.example.com', version: '1.28', node_count: 3, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
          ],
          count: 1,
        });
      }),
      http.get('*/api/k8s/:clusterId/helm/releases', async ({ request }) => {
        const url = new URL(request.url);
        if (!url.pathname.endsWith('/helm/releases')) return;
        // Delay response
        await new Promise((resolve) => setTimeout(resolve, 500));
        return HttpResponse.json({ success: true, releases: [], count: 0 });
      })
    );

    const { result } = renderHook(() => useAllHelmReleases(), {
      wrapper: createWrapper(),
    });

    // Clusters query fires first, then helm releases query
    // Once clusters load but releases are still in flight, isLoading should be true
    await waitFor(() => {
      // Wait until the helm query has actually started (clusters loaded)
      expect(result.current.queries.length).toBeGreaterThan(0);
    });

    // At this point, release queries should still be loading
    expect(result.current.isLoading).toBe(true);

    // Eventually resolves
    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });
  });

  // ============================================================================
  // Enriched data
  // ============================================================================

  it('enriches releases with cluster_id and cluster_name', async () => {
    setupClusterAndReleaseHandlers(
      [{ id: 42, name: 'my-cluster' }],
      {
        42: [
          { name: 'app-release', namespace: 'production', revision: '5', status: 'deployed', chart: 'app-3.0.0', app_version: '3.0', updated: '2026-01-01T00:00:00Z' },
        ],
      }
    );

    const { result } = renderHook(() => useAllHelmReleases(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.totalReleases).toBe(1);
    });

    const release = result.current.data[0].namespaces[0].releases[0];
    expect(release.cluster_id).toBe(42);
    expect(release.cluster_name).toBe('my-cluster');
    expect(release.name).toBe('app-release');
    expect(release.namespace).toBe('production');
  });

  // ============================================================================
  // Return shape
  // ============================================================================

  it('exposes queries array for debugging', async () => {
    setupClusterAndReleaseHandlers(
      [{ id: 1, name: 'c1' }, { id: 2, name: 'c2' }],
      {
        1: [{ name: 'r1', namespace: 'ns1', revision: '1', status: 'deployed', chart: 'c-1.0', app_version: '1.0', updated: '2026-01-01T00:00:00Z' }],
        2: [{ name: 'r2', namespace: 'ns2', revision: '1', status: 'deployed', chart: 'c-2.0', app_version: '2.0', updated: '2026-01-01T00:00:00Z' }],
      }
    );

    const { result } = renderHook(() => useAllHelmReleases(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.queries).toHaveLength(2);
      expect(result.current.queries.every((q) => q.isSuccess)).toBe(true);
    });

    expect(result.current.queries[0].isSuccess).toBe(true);
    expect(result.current.queries[1].isSuccess).toBe(true);
  });
});
