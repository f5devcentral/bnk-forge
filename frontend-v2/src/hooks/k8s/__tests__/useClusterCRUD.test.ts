/**
 * Tests for useClusterCRUD hooks
 *
 * Covers: resource types, all clusters, project clusters, single cluster,
 * create/update/delete mutations, test connection, EKS detection,
 * kubeconfig refresh, namespaces, and cluster resources.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useK8sResourceTypes,
  useAllClusters,
  useProjectClusters,
  useCluster,
  useCreateCluster,
  useUpdateCluster,
  useDeleteCluster,
  useTestClusterConnection,
  useDetectEKSClusters,
  useRefreshClusterKubeconfig,
  useClusterNamespaces,
  useClusterResources,
} from '../useClusterCRUD';
import type { K8sClusterCreateRequest, K8sClusterUpdateRequest } from '@/types';
import React from 'react';

// ============================================================================
// Test Setup
// ============================================================================

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

// ============================================================================
// useK8sResourceTypes
// ============================================================================

describe('useK8sResourceTypes', () => {
  it('fetches K8s resource types', async () => {
    // Override the default handler to return the shape the API function expects
    server.use(
      http.get('*/api/k8s/resource-types', () => {
        return HttpResponse.json({
          resource_types: [
            'pods', 'deployments', 'services', 'configmaps', 'secrets',
            'ingresses', 'namespaces', 'nodes', 'persistentvolumeclaims',
          ],
        });
      }),
    );

    const { result } = renderHook(() => useK8sResourceTypes(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toBeDefined();
    expect(Array.isArray(result.current.data)).toBe(true);
    expect(result.current.data).toContain('pods');
    expect(result.current.data).toContain('deployments');
  });
});

// ============================================================================
// useAllClusters
// ============================================================================

describe('useAllClusters', () => {
  it('fetches all clusters', async () => {
    const { result } = renderHook(() => useAllClusters(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toBeDefined();
    expect(result.current.data!.clusters).toHaveLength(2);
    expect(result.current.data!.clusters[0].name).toBe('dev-cluster');
    expect(result.current.data!.clusters[1].name).toBe('prod-cluster');
  });

  it('handles empty cluster list', async () => {
    server.use(
      http.get('*/api/k8s/clusters', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname !== '/api/k8s/clusters') return;
        return HttpResponse.json({ clusters: [], count: 0 });
      }),
    );

    const { result } = renderHook(() => useAllClusters(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data!.clusters).toHaveLength(0);
  });

  it('handles API error', async () => {
    server.use(
      http.get('*/api/k8s/clusters', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname !== '/api/k8s/clusters') return;
        return HttpResponse.json({ error: { message: 'Server error' } }, { status: 500 });
      }),
    );

    const { result } = renderHook(() => useAllClusters(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ============================================================================
// useProjectClusters
// ============================================================================

describe('useProjectClusters', () => {
  it('fetches clusters for a project', async () => {
    const { result } = renderHook(() => useProjectClusters(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toBeDefined();
    expect(Array.isArray(result.current.data)).toBe(true);
  });

  it('does not fetch when projectId is 0', () => {
    const { result } = renderHook(() => useProjectClusters(0), { wrapper: createWrapper() });
    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// useCluster
// ============================================================================

describe('useCluster', () => {
  it('fetches a single cluster by ID', async () => {
    const { result } = renderHook(() => useCluster(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      id: 1,
      name: 'dev-cluster',
      status: 'connected',
    });
  });

  it('does not fetch when clusterId is 0', () => {
    const { result } = renderHook(() => useCluster(0), { wrapper: createWrapper() });
    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// useCreateCluster
// ============================================================================

describe('useCreateCluster', () => {
  it('creates a new cluster', async () => {
    const { result } = renderHook(() => useCreateCluster(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        projectId: 1,
        data: {
          name: 'new-cluster',
          cluster_type: 'eks',
          api_server: 'https://new.eks.amazonaws.com',
        } as K8sClusterCreateRequest,
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      id: 10,
      name: 'new-cluster',
      status: 'connecting',
    });
  });
});

// ============================================================================
// useUpdateCluster
// ============================================================================

describe('useUpdateCluster', () => {
  it('updates an existing cluster', async () => {
    const { result } = renderHook(() => useUpdateCluster(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        data: { name: 'updated-cluster' } as K8sClusterUpdateRequest,
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      id: 1,
      name: 'updated-cluster',
    });
  });
});

// ============================================================================
// useDeleteCluster
// ============================================================================

describe('useDeleteCluster', () => {
  it('deletes a cluster', async () => {
    const { result } = renderHook(() => useDeleteCluster(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate(1);
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      message: 'Cluster deleted',
    });
  });
});

// ============================================================================
// useTestClusterConnection
// ============================================================================

describe('useTestClusterConnection', () => {
  it('tests cluster connection successfully', async () => {
    const { result } = renderHook(() => useTestClusterConnection(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate(1);
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      version: '1.28',
    });
  });

  it('handles connection failure', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/test', () => {
        return HttpResponse.json({ success: false, error: 'Connection refused' });
      }),
    );

    const { result } = renderHook(() => useTestClusterConnection(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate(99);
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: false,
      error: 'Connection refused',
    });
  });
});

// ============================================================================
// useDetectEKSClusters
// ============================================================================

describe('useDetectEKSClusters', () => {
  it('detects and registers EKS clusters', async () => {
    server.use(
      http.post('*/api/projects/:projectId/k8s/clusters/detect-eks', () => {
        return HttpResponse.json({
          success: true,
          message: 'Found 2 EKS clusters',
          registered: [{ id: 10, name: 'eks-1', module_id: 1, status: 'connected' }],
          skipped: [],
          errors: [],
        });
      }),
    );

    const { result } = renderHook(() => useDetectEKSClusters(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate(1);
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data!.registered).toHaveLength(1);
    expect(result.current.data!.registered[0].name).toBe('eks-1');
  });
});

// ============================================================================
// useRefreshClusterKubeconfig
// ============================================================================

describe('useRefreshClusterKubeconfig', () => {
  it('refreshes kubeconfig for a cluster', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/refresh-kubeconfig', () => {
        return HttpResponse.json({ success: true, message: 'Kubeconfig refreshed' });
      }),
    );

    const { result } = renderHook(() => useRefreshClusterKubeconfig(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate(1);
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      message: 'Kubeconfig refreshed',
    });
  });
});

// ============================================================================
// useClusterNamespaces
// ============================================================================

describe('useClusterNamespaces', () => {
  it('fetches namespaces for a cluster', async () => {
    const { result } = renderHook(() => useClusterNamespaces(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toBeDefined();
    expect(result.current.data).toContain('default');
    expect(result.current.data).toContain('kube-system');
  });

  it('does not fetch when clusterId is 0', () => {
    const { result } = renderHook(() => useClusterNamespaces(0), { wrapper: createWrapper() });
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when enabled is false', () => {
    const { result } = renderHook(
      () => useClusterNamespaces(1, { enabled: false }),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// useClusterResources
// ============================================================================

describe('useClusterResources', () => {
  it('fetches resources for a cluster by type', async () => {
    const { result } = renderHook(
      () => useClusterResources(1, 'deployments'),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toBeDefined();
    expect(result.current.data!.resources).toHaveLength(2);
    expect(result.current.data!.resources[0].name).toBe('nginx-deployment');
  });

  it('does not fetch when clusterId is 0', () => {
    const { result } = renderHook(
      () => useClusterResources(0, 'pods'),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when resourceType is empty', () => {
    const { result } = renderHook(
      () => useClusterResources(1, ''),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when enabled is false', () => {
    const { result } = renderHook(
      () => useClusterResources(1, 'pods', undefined, { enabled: false }),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });
});
