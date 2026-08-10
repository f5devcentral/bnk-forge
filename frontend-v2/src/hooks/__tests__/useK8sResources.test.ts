/**
 * Tests for useK8sResources hooks
 *
 * Covers: namespaces, resource listing, resource CRUD,
 * scale deployment, pod logs, pod restart, describe, events, containers.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useClusterNamespaces,
  useClusterResources,
  useCreateK8sResource,
  useUpdateK8sResource,
  useDeleteK8sResource,
  useScaleDeployment,
  usePodLogs,
  useRestartPod,
  useDescribeResource,
  useClusterEvents,
  usePodContainers,
} from '@/hooks/useK8sResources';
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
// Namespace Queries
// ========================================================================

describe('useClusterNamespaces', () => {
  it('fetches namespaces for a cluster', async () => {
    const { result } = renderHook(
      () => useClusterNamespaces(1),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toContain('default');
    expect(result.current.data).toContain('kube-system');
  });

  it('does not fetch when disabled', () => {
    const { result } = renderHook(
      () => useClusterNamespaces(1, { enabled: false }),
      { wrapper: createWrapper() }
    );

    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ========================================================================
// Resource Listing
// ========================================================================

describe('useClusterResources', () => {
  it('fetches resources by type', async () => {
    const { result } = renderHook(
      () => useClusterResources(1, 'pods'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
  });

  it('does not fetch when resourceType is empty', () => {
    const { result } = renderHook(
      () => useClusterResources(1, ''),
      { wrapper: createWrapper() }
    );

    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when clusterId is 0', () => {
    const { result } = renderHook(
      () => useClusterResources(0, 'pods'),
      { wrapper: createWrapper() }
    );

    expect(result.current.fetchStatus).toBe('idle');
  });

  it('handles API errors', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/resources/:type', () => {
        return HttpResponse.json(
          { error: { message: 'Cluster unreachable' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(
      () => useClusterResources(1, 'pods'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});

// ========================================================================
// Resource CRUD
// ========================================================================

describe('useCreateK8sResource', () => {
  it('creates a resource successfully', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/resources/:type', () => {
        return HttpResponse.json({
          success: true,
          message: 'ConfigMap created',
          resource: { metadata: { name: 'my-cm', namespace: 'default' } },
        });
      })
    );

    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/resources/:type', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          success: true,
          message: 'Resource created',
          resource: { metadata: { name: 'my-cm', namespace: 'default' } },
        });
      })
    );

    const { result } = renderHook(() => useCreateK8sResource(), { wrapper: createWrapper() });

    result.current.mutate({
      clusterId: 1,
      resourceType: 'configmap',
      resourceYaml: 'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: my-cm',
      namespace: 'default',
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // ResourceCreateRequest: { resource_yaml, namespace?, dry_run? }
    expect(capturedBody).toMatchObject({
      resource_yaml: expect.stringContaining('ConfigMap'),
      namespace: 'default',
    });
    // Should NOT have clusterId or resourceType in the body
    expect(capturedBody).not.toHaveProperty('clusterId');
    expect(capturedBody).not.toHaveProperty('resourceType');
  });
});

describe('useUpdateK8sResource', () => {
  it('sends correct payload shape (ResourceUpdateRequest)', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/k8s/clusters/:clusterId/resources/:type/:name', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          success: true,
          message: 'Resource updated',
          resource: { metadata: { name: 'my-cm', namespace: 'default' } },
        });
      })
    );

    const { result } = renderHook(() => useUpdateK8sResource(), { wrapper: createWrapper() });

    result.current.mutate({
      clusterId: 1,
      resourceType: 'configmap',
      resourceName: 'my-cm',
      resourceYaml: 'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: my-cm',
      namespace: 'default',
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // ResourceUpdateRequest: { resource_yaml, namespace?, dry_run? }
    expect(capturedBody).toMatchObject({
      resource_yaml: expect.stringContaining('ConfigMap'),
      namespace: 'default',
    });
    expect(capturedBody).not.toHaveProperty('clusterId');
    expect(capturedBody).not.toHaveProperty('resourceName');
  });
});

describe('useDeleteK8sResource', () => {
  it('deletes a resource successfully', async () => {
    server.use(
      http.delete('*/api/k8s/clusters/:clusterId/resources/:type/:name', () => {
        return HttpResponse.json({ success: true, message: 'Resource deleted' });
      })
    );

    const { result } = renderHook(() => useDeleteK8sResource(), { wrapper: createWrapper() });

    result.current.mutate({
      clusterId: 1,
      resourceType: 'configmap',
      resourceName: 'my-cm',
      namespace: 'default',
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true });
  });

  it('handles deletion errors', async () => {
    server.use(
      http.delete('*/api/k8s/clusters/:clusterId/resources/:type/:name', () => {
        return HttpResponse.json(
          { error: { message: 'Resource not found' } },
          { status: 404 }
        );
      })
    );

    const { result } = renderHook(() => useDeleteK8sResource(), { wrapper: createWrapper() });

    result.current.mutate({
      clusterId: 1,
      resourceType: 'configmap',
      resourceName: 'nonexistent',
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});

// ========================================================================
// Scale Deployment
// ========================================================================

describe('useScaleDeployment', () => {
  it('sends correct payload shape (ScaleDeploymentRequest)', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/deployments/:name/scale', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          success: true,
          message: 'Deployment scaled to 5 replicas',
          replicas: 5,
        });
      })
    );

    const { result } = renderHook(() => useScaleDeployment(), { wrapper: createWrapper() });

    result.current.mutate({
      clusterId: 1,
      deploymentName: 'web',
      namespace: 'default',
      replicas: 5,
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // ScaleDeploymentRequest: { replicas: int, namespace: str }
    expect(capturedBody).toMatchObject({
      replicas: 5,
      namespace: 'default',
    });
    // Should NOT have clusterId or deploymentName in body
    expect(capturedBody).not.toHaveProperty('clusterId');
    expect(capturedBody).not.toHaveProperty('deploymentName');
  });
});

// ========================================================================
// Pod Operations
// ========================================================================

describe('usePodLogs', () => {
  it('fetches pod logs', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/pods/:podName/logs', () => {
        return HttpResponse.json({
          logs: '2026-01-01 Starting server...\n2026-01-01 Server ready',
          pod_name: 'web-abc',
          namespace: 'default',
        });
      })
    );

    const { result } = renderHook(
      () => usePodLogs(1, 'web-abc', 'default'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.logs).toContain('Starting server');
  });

  it('does not fetch when podName is empty', () => {
    const { result } = renderHook(
      () => usePodLogs(1, '', 'default'),
      { wrapper: createWrapper() }
    );

    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useRestartPod', () => {
  it('restarts a pod', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/pods/:podName/restart', () => {
        return HttpResponse.json({ success: true, message: 'Pod restarted' });
      })
    );

    const { result } = renderHook(() => useRestartPod(), { wrapper: createWrapper() });

    result.current.mutate({ clusterId: 1, podName: 'web-abc', namespace: 'default' });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true });
  });
});

// ========================================================================
// Describe, Events, Containers
// ========================================================================

describe('useDescribeResource', () => {
  it('fetches resource description', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/resources/:type/:name/describe', () => {
        return HttpResponse.json({
          metadata: { name: 'web', namespace: 'default' },
          conditions: [{ type: 'Available', status: 'True' }],
          events: [],
        });
      })
    );

    const { result } = renderHook(
      () => useDescribeResource(1, 'deployment', 'web', 'default'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.metadata.name).toBe('web');
  });
});

describe('useClusterEvents', () => {
  it('fetches cluster events', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/events', () => {
        return HttpResponse.json({
          events: [
            { type: 'Normal', reason: 'Scheduled', message: 'Pod scheduled' },
          ],
          count: 1,
        });
      })
    );

    const { result } = renderHook(
      () => useClusterEvents(1),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.events).toHaveLength(1);
  });
});

describe('usePodContainers', () => {
  it('fetches containers for a pod', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/pods/:podName/containers', () => {
        return HttpResponse.json({
          pod_name: 'web-abc',
          namespace: 'default',
          containers: [{ name: 'web', image: 'nginx:latest', ready: true }],
          init_containers: [],
        });
      })
    );

    const { result } = renderHook(
      () => usePodContainers(1, 'web-abc', 'default'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.containers).toHaveLength(1);
    expect(result.current.data!.containers[0].name).toBe('web');
  });

  it('does not fetch when podName is empty', () => {
    const { result } = renderHook(
      () => usePodContainers(1, '', 'default'),
      { wrapper: createWrapper() }
    );

    expect(result.current.fetchStatus).toBe('idle');
  });
});
