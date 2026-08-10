/**
 * Tests for useK8s hooks
 *
 * Covers: fetching clusters, fetching resources for a cluster, empty state.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { useAllClusters, useProjectClusters, useCluster } from '@/hooks/useK8s';
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

describe('useAllClusters', () => {
  it('fetches all clusters', async () => {
    const { result } = renderHook(() => useAllClusters(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
    expect(result.current.data!.clusters).toHaveLength(2);
    expect(result.current.data!.clusters[0].name).toBe('dev-cluster');
  });

  it('returns cluster data shape', async () => {
    const { result } = renderHook(() => useAllClusters(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    const cluster = result.current.data!.clusters[0];
    expect(cluster).toMatchObject({
      id: 1,
      name: 'dev-cluster',
      cluster_type: 'eks',
      status: 'connected',
    });
  });

  it('handles empty cluster list', async () => {
    server.use(
      http.get('*/api/k8s/clusters', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname !== '/api/k8s/clusters') return;
        return HttpResponse.json({ clusters: [], count: 0 });
      })
    );

    const { result } = renderHook(() => useAllClusters(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.clusters).toHaveLength(0);
    expect(result.current.data!.count).toBe(0);
  });

  it('handles API error', async () => {
    server.use(
      http.get('*/api/k8s/clusters', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname !== '/api/k8s/clusters') return;
        return HttpResponse.json(
          { error: { message: 'Internal server error' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useAllClusters(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});

describe('useProjectClusters', () => {
  it('fetches clusters for a specific project', async () => {
    server.use(
      http.get('*/api/projects/:projectId/k8s/clusters', () => {
        return HttpResponse.json({
          clusters: [
            {
              id: 1,
              name: 'project-cluster',
              project_id: 1,
              cluster_type: 'eks',
              status: 'connected',
              api_server: 'https://eks.amazonaws.com',
              version: '1.28',
              node_count: 3,
              created_at: '2026-01-15T00:00:00Z',
              updated_at: '2026-02-01T00:00:00Z',
            },
          ],
          count: 1,
        });
      })
    );

    const { result } = renderHook(
      () => useProjectClusters(1),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toHaveLength(1);
    expect(result.current.data![0].name).toBe('project-cluster');
  });

  it('does not fetch when projectId is 0', () => {
    const { result } = renderHook(
      () => useProjectClusters(0),
      { wrapper: createWrapper() }
    );

    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useCluster', () => {
  it('fetches a single cluster by ID', async () => {
    const { result } = renderHook(
      () => useCluster(1),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      id: 1,
      name: 'dev-cluster',
      status: 'connected',
    });
  });

  it('does not fetch when clusterId is 0', () => {
    const { result } = renderHook(
      () => useCluster(0),
      { wrapper: createWrapper() }
    );

    expect(result.current.fetchStatus).toBe('idle');
  });
});
