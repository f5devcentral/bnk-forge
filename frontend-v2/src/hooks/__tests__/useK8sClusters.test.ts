/**
 * Tests for useK8sClusters hooks
 *
 * Covers: resource types, cluster CRUD, project clusters,
 * connection testing, EKS detection, kubeconfig refresh.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
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
  useConfigureBnkCluster,
  useAssignBnkClusterMembers,
} from '@/hooks/useK8sClusters';
import type { K8sClusterCreateRequest, K8sClusterUpdateRequest } from '@/types';
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

// ========================================================================
// Resource Types
// ========================================================================

describe('useK8sResourceTypes', () => {
  it('fetches resource types', async () => {
    server.use(
      http.get('*/api/k8s/resource-types', () => {
        return HttpResponse.json({
          resource_types: ['pods', 'deployments', 'services', 'configmaps'],
        });
      })
    );

    const { result } = renderHook(() => useK8sResourceTypes(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toContain('pods');
    expect(result.current.data).toContain('deployments');
  });
});

// ========================================================================
// Cluster Queries
// ========================================================================

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
  it('fetches clusters for a project', async () => {
    const { result } = renderHook(
      () => useProjectClusters(1),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
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
  it('fetches a single cluster', async () => {
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

// ========================================================================
// Cluster Mutations
// ========================================================================

describe('useCreateCluster', () => {
  it('sends correct payload shape (ClusterCreateRequest)', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/projects/:projectId/k8s/clusters', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          id: 10,
          name: capturedBody.name,
          cloud_provider: capturedBody.cloud_provider,
          status: 'connected',
        });
      })
    );

    const { result } = renderHook(() => useCreateCluster(), { wrapper: createWrapper() });

    result.current.mutate({
      projectId: 1,
      data: {
        name: 'new-cluster',
        kubeconfig: 'base64encodedconfig',
        cloud_provider: 'aws',
        region: 'us-west-2',
        default_namespace: 'production',
      } as K8sClusterCreateRequest,
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // ClusterCreateRequest: fields at top level (no wrapper)
    expect(capturedBody).toMatchObject({
      name: 'new-cluster',
      kubeconfig: 'base64encodedconfig',
      cloud_provider: 'aws',
      region: 'us-west-2',
      default_namespace: 'production',
    });
    // Should NOT be wrapped in a 'data' key
    expect(capturedBody).not.toHaveProperty('data');
    expect(capturedBody).not.toHaveProperty('projectId');
  });
});

describe('useUpdateCluster', () => {
  it('sends correct payload shape (ClusterUpdateRequest)', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/k8s/clusters/:clusterId', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          id: 1,
          name: capturedBody.name || 'dev-cluster',
          status: 'connected',
        });
      })
    );

    const { result } = renderHook(() => useUpdateCluster(), { wrapper: createWrapper() });

    result.current.mutate({
      clusterId: 1,
      data: {
        name: 'updated-cluster',
        default_namespace: 'staging',
      } as K8sClusterUpdateRequest,
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // ClusterUpdateRequest: fields at top level (no wrapper)
    expect(capturedBody).toMatchObject({
      name: 'updated-cluster',
      default_namespace: 'staging',
    });
    // Should NOT have clusterId or 'data' wrapper in body
    expect(capturedBody).not.toHaveProperty('clusterId');
    expect(capturedBody).not.toHaveProperty('data');
  });
});

describe('useDeleteCluster', () => {
  it('deletes a cluster successfully', async () => {
    const { result } = renderHook(() => useDeleteCluster(), { wrapper: createWrapper() });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true });
  });

  it('handles deletion errors', async () => {
    server.use(
      http.delete('*/api/k8s/clusters/:clusterId', () => {
        return HttpResponse.json(
          { error: { message: 'Cluster has active resources' } },
          { status: 409 }
        );
      })
    );

    const { result } = renderHook(() => useDeleteCluster(), { wrapper: createWrapper() });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});

// ========================================================================
// Connection Testing & EKS
// ========================================================================

describe('useTestClusterConnection', () => {
  it('tests connection and reports success', async () => {
    const { result } = renderHook(() => useTestClusterConnection(), { wrapper: createWrapper() });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true, version: '1.28' });
  });

  it('handles failed connection test', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/test', () => {
        return HttpResponse.json({ success: false, error: 'Connection refused' });
      })
    );

    const { result } = renderHook(() => useTestClusterConnection(), { wrapper: createWrapper() });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.success).toBe(false);
  });
});

describe('useDetectClusters', () => {
  it('detects clusters from credential templates for a project', async () => {
    server.use(
      http.post('*/api/projects/:projectId/k8s/clusters/detect-credentials', () => {
        return HttpResponse.json({
          success: true,
          message: 'Discovered 2 cluster(s) from credential templates, registered 1 new cluster(s)',
          registered: [{ id: 10, name: 'eks-prod', provider: 'aws', status: 'registered' }],
          skipped: [],
          errors: [],
        });
      })
    );

    const { result } = renderHook(() => useDetectEKSClusters(), { wrapper: createWrapper() });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.registered).toHaveLength(1);
    expect(result.current.data!.registered[0].provider).toBe('aws');
  });
});

describe('useRefreshClusterKubeconfig', () => {
  it('refreshes kubeconfig successfully', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/refresh-kubeconfig', () => {
        return HttpResponse.json({ success: true, message: 'Kubeconfig refreshed' });
      })
    );

    const { result } = renderHook(() => useRefreshClusterKubeconfig(), { wrapper: createWrapper() });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true });
  });
});

describe('useConfigureBnkCluster', () => {
  it('posts bnk-config and returns updated BnkClusterConfigSummary', async () => {
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.post('*/api/k8s/clusters/:clusterId/bnk-config', async ({ request, params }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          id: 10,
          cluster_id: Number(params.clusterId),
          tmfifo_pool_cidr: capturedBody.tmfifo_pool_cidr || '192.168.100.0/22',
          join_transport: capturedBody.join_transport || 'rshim',
          control_plane_host_id: capturedBody.control_plane_host_id || 1,
        });
      })
    );

    const { result } = renderHook(() => useConfigureBnkCluster(), { wrapper: createWrapper() });

    result.current.mutate({
      clusterId: 42,
      data: {
        tmfifo_pool_cidr: '192.168.200.0/22',
        join_transport: 'rshim',
        control_plane_host_id: 1,
      },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toEqual({
      tmfifo_pool_cidr: '192.168.200.0/22',
      join_transport: 'rshim',
      control_plane_host_id: 1,
    });
    expect(result.current.data).toEqual({
      id: 10,
      cluster_id: 42,
      tmfifo_pool_cidr: '192.168.200.0/22',
      join_transport: 'rshim',
      control_plane_host_id: 1,
    });
  });
});

describe('useAssignBnkClusterMembers', () => {
  it('posts bnk-members and returns assigned members summary', async () => {
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.post('*/api/k8s/clusters/:clusterId/bnk-members', async ({ request, params }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        // Real backend shape: BnkClusterMemberAssignResponse
        // assigned_dpus entries: { dpu_id, dpu_name, host_tmfifo_ip, dpu_tmfifo_ip, subnet_cidr }
        // bnk_config: BnkClusterConfigSummary (includes host_ids + dpu_ids)
        return HttpResponse.json({
          cluster_id: Number(params.clusterId),
          control_plane_host_id: capturedBody.control_plane_host_id,
          host_ids: capturedBody.host_ids,
          assigned_dpus: [
            {
              dpu_id: 1,
              dpu_name: 'dpu-1',
              host_tmfifo_ip: '192.168.100.1',
              dpu_tmfifo_ip: '192.168.100.2',
              subnet_cidr: '192.168.100.0/30',
            },
          ],
          bnk_config: {
            id: 10,
            cluster_id: Number(params.clusterId),
            tmfifo_pool_cidr: '192.168.100.0/22',
            join_transport: 'rshim',
            control_plane_host_id: capturedBody.control_plane_host_id,
            host_ids: capturedBody.host_ids ?? [],
            dpu_ids: [1],
          },
        });
      })
    );

    const { result } = renderHook(() => useAssignBnkClusterMembers(), { wrapper: createWrapper() });

    result.current.mutate({
      clusterId: 42,
      data: {
        control_plane_host_id: 1,
        host_ids: [1, 2],
        dpu_ids: [1],
        tmfifo_pool_cidr: '192.168.100.0/22',
      },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toEqual({
      control_plane_host_id: 1,
      host_ids: [1, 2],
      dpu_ids: [1],
      tmfifo_pool_cidr: '192.168.100.0/22',
    });
    expect(result.current.data!.host_ids).toEqual([1, 2]);
    expect(result.current.data!.control_plane_host_id).toBe(1);
  });
});
