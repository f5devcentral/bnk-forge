/**
 * Tests for useResources hooks
 *
 * Covers: create/update/delete K8s resources, scale deployment,
 * cluster scan, scan result, and adaptive module plan.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useCreateK8sResource,
  useUpdateK8sResource,
  useDeleteK8sResource,
  useScaleDeployment,
  useClusterScan,
  useClusterScanResult,
  useAdaptiveModulePlan,
} from '../useResources';
import React from 'react';
import * as notifyModule from '@/lib/notify';

// Module-level mock so notify.success / notify internal calls are interceptable
vi.mock('@/lib/notify', () => ({
  notifyError: vi.fn(),
  notify: Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

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

function setupResourceHandlers() {
  server.use(
    http.post('*/api/k8s/clusters/:clusterId/resources/:resourceType', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({
        success: true,
        message: 'Resource created successfully',
        resource: { name: 'new-resource', namespace: body.namespace || 'default' },
      });
    }),
    http.put('*/api/k8s/clusters/:clusterId/resources/:resourceType/:resourceName', async ({ params, request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({
        success: true,
        message: 'Resource updated successfully',
        resource: { name: params.resourceName, namespace: body.namespace || 'default' },
      });
    }),
    http.delete('*/api/k8s/clusters/:clusterId/resources/:resourceType/:resourceName', () => {
      return HttpResponse.json({ success: true, message: 'Resource deleted successfully' });
    }),
    http.post('*/api/k8s/clusters/:clusterId/deployments/:deploymentName/scale', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({
        success: true,
        message: `Deployment scaled to ${body.replicas} replicas`,
        replicas: body.replicas,
      });
    }),
    http.post('*/api/k8s/clusters/:clusterId/adaptive-modules', () => {
      return HttpResponse.json({
        plan: {
          modules: ['module-a', 'module-b'],
          excluded: ['module-c'],
        },
        template_slug: 'default',
      });
    }),
  );
}

// ============================================================================
// useCreateK8sResource
// ============================================================================

describe('useCreateK8sResource', () => {
  beforeEach(() => {
    vi.mocked(notifyModule.notifyError).mockClear();
    vi.mocked(notifyModule.notify).mockClear();
    vi.mocked(notifyModule.notify.success).mockClear();
  });

  it('creates a K8s resource', async () => {
    setupResourceHandlers();
    const { result } = renderHook(() => useCreateK8sResource(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        resourceType: 'deployments',
        resourceYaml: 'apiVersion: apps/v1\nkind: Deployment\n...',
        namespace: 'default',
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      message: 'Resource created successfully',
    });
  });

  it('posts success bell notification on non-dry-run create', async () => {
    setupResourceHandlers();
    const { result } = renderHook(() => useCreateK8sResource(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        resourceType: 'deployments',
        resourceYaml: 'apiVersion: apps/v1\nkind: Deployment\n...',
        namespace: 'default',
        dryRun: false,
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(vi.mocked(notifyModule.notify.success)).toHaveBeenCalledWith(
      'Resource created successfully',
      undefined,
      { category: 'cluster' },
    );
  });

  it('does NOT post success bell notification on dry-run create', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/resources/:resourceType', () => {
        return HttpResponse.json({
          success: true,
          message: 'Dry run: resource would be created',
          resource: { name: 'test-resource', namespace: 'default' },
        });
      }),
    );
    const { result } = renderHook(() => useCreateK8sResource(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        resourceType: 'services',
        resourceYaml: 'apiVersion: v1\nkind: Service\n...',
        namespace: 'default',
        dryRun: true,
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(vi.mocked(notifyModule.notify.success)).not.toHaveBeenCalled();
  });

  it('supports dry run mode', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/resources/:resourceType', () => {
        return HttpResponse.json({
          success: true,
          message: 'Dry run: resource would be created',
          resource: { name: 'test-resource', namespace: 'default' },
        });
      }),
    );

    const { result } = renderHook(() => useCreateK8sResource(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        resourceType: 'services',
        resourceYaml: 'apiVersion: v1\nkind: Service\n...',
        namespace: 'default',
        dryRun: true,
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data!.message).toContain('Dry run');
  });

  it('handles creation error', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/resources/:resourceType', () => {
        return HttpResponse.json(
          { error: { message: 'Invalid YAML' } },
          { status: 400 },
        );
      }),
    );

    const { result } = renderHook(() => useCreateK8sResource(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        resourceType: 'deployments',
        resourceYaml: 'invalid yaml',
      });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it('forwards OCP parse context into notify flow on create failure', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/resources/:resourceType', () => {
        return HttpResponse.json(
          {
            error: {
              code: 'K8S_API_ERROR',
              detail: 'forbidden: unable to validate against any security context constraint',
            },
          },
          { status: 403 },
        );
      }),
    );

    const parseContext = {
      detectedPlatformProfile: 'ocp' as const,
      platformCapabilities: { scc: true },
    };

    const { result } = renderHook(() => useCreateK8sResource(parseContext), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        resourceType: 'pods',
        resourceYaml: 'apiVersion: v1\nkind: Pod\n...',
        namespace: 'default',
      });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));

    const notifySpy = vi.mocked(notifyModule.notifyError);
    expect(notifySpy).toHaveBeenCalled();
    const lastCall = notifySpy.mock.calls.at(-1);
    expect(lastCall?.[2]).toEqual(parseContext);
  });

  it('does not forward OCP parse context when hook is called without context', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/resources/:resourceType', () => {
        return HttpResponse.json(
          {
            error: {
              code: 'K8S_API_ERROR',
              detail: 'forbidden: unable to validate against any security context constraint',
            },
          },
          { status: 403 },
        );
      }),
    );

    const { result } = renderHook(() => useCreateK8sResource(), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        resourceType: 'pods',
        resourceYaml: 'apiVersion: v1\nkind: Pod\n...',
        namespace: 'default',
      });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));

    const notifySpy = vi.mocked(notifyModule.notifyError);
    expect(notifySpy).toHaveBeenCalled();
    const lastCall = notifySpy.mock.calls.at(-1);
    expect(lastCall?.[2]).toBeUndefined();
  });
});

// ============================================================================
// useUpdateK8sResource
// ============================================================================

describe('useUpdateK8sResource', () => {
  beforeEach(() => {
    vi.mocked(notifyModule.notify).mockClear();
    vi.mocked(notifyModule.notify.success).mockClear();
  });

  it('updates a K8s resource', async () => {
    setupResourceHandlers();
    const { result } = renderHook(() => useUpdateK8sResource(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        resourceType: 'deployments',
        resourceName: 'nginx-deployment',
        resourceYaml: 'apiVersion: apps/v1\nkind: Deployment\n...',
        namespace: 'default',
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      message: 'Resource updated successfully',
    });
  });

  it('posts success bell notification on non-dry-run update', async () => {
    setupResourceHandlers();
    const { result } = renderHook(() => useUpdateK8sResource(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        resourceType: 'deployments',
        resourceName: 'nginx-deployment',
        resourceYaml: 'apiVersion: apps/v1\nkind: Deployment\n...',
        namespace: 'default',
        dryRun: false,
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(vi.mocked(notifyModule.notify.success)).toHaveBeenCalledWith(
      'Resource updated successfully',
      undefined,
      { category: 'cluster' },
    );
  });

  it('does NOT post success bell notification on dry-run update', async () => {
    server.use(
      http.put('*/api/k8s/clusters/:clusterId/resources/:resourceType/:resourceName', () => {
        return HttpResponse.json({
          success: true,
          message: 'Dry run: resource would be updated',
          resource: { name: 'nginx-deployment', namespace: 'default' },
        });
      }),
    );
    const { result } = renderHook(() => useUpdateK8sResource(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        resourceType: 'deployments',
        resourceName: 'nginx-deployment',
        resourceYaml: 'apiVersion: apps/v1\nkind: Deployment\n...',
        namespace: 'default',
        dryRun: true,
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(vi.mocked(notifyModule.notify.success)).not.toHaveBeenCalled();
  });

  it('handles update error', async () => {
    server.use(
      http.put('*/api/k8s/clusters/:clusterId/resources/:resourceType/:resourceName', () => {
        return HttpResponse.json(
          { error: { message: 'Resource not found' } },
          { status: 404 },
        );
      }),
    );

    const { result } = renderHook(() => useUpdateK8sResource(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        resourceType: 'deployments',
        resourceName: 'nonexistent',
        resourceYaml: 'apiVersion: apps/v1\n...',
      });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ============================================================================
// useDeleteK8sResource
// ============================================================================

describe('useDeleteK8sResource', () => {
  it('deletes a K8s resource', async () => {
    setupResourceHandlers();
    const { result } = renderHook(() => useDeleteK8sResource(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        resourceType: 'deployments',
        resourceName: 'nginx-deployment',
        namespace: 'default',
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      message: 'Resource deleted successfully',
    });
  });

  it('handles delete error', async () => {
    server.use(
      http.delete('*/api/k8s/clusters/:clusterId/resources/:resourceType/:resourceName', () => {
        return HttpResponse.json(
          { error: { message: 'Cannot delete system resource' } },
          { status: 403 },
        );
      }),
    );

    const { result } = renderHook(() => useDeleteK8sResource(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        resourceType: 'namespaces',
        resourceName: 'kube-system',
      });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ============================================================================
// useScaleDeployment
// ============================================================================

describe('useScaleDeployment', () => {
  it('scales a deployment', async () => {
    setupResourceHandlers();
    const { result } = renderHook(() => useScaleDeployment(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        deploymentName: 'nginx-deployment',
        namespace: 'default',
        replicas: 5,
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      replicas: 5,
    });
  });

  it('handles scale error', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/deployments/:deploymentName/scale', () => {
        return HttpResponse.json(
          { error: { message: 'Insufficient resources' } },
          { status: 422 },
        );
      }),
    );

    const { result } = renderHook(() => useScaleDeployment(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        deploymentName: 'nginx-deployment',
        namespace: 'default',
        replicas: 100,
      });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ============================================================================
// useClusterScan
// ============================================================================

describe('useClusterScan', () => {
  it('scans a cluster', async () => {
    const { result } = renderHook(() => useClusterScan(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate(1);
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      resources_found: 42,
      namespaces: 5,
    });
  });

  it('handles scan error', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/scan', () => {
        // Use 400 instead of 503 to avoid triggering axios retry interceptor
        return HttpResponse.json(
          { error: { message: 'Cluster unreachable' } },
          { status: 400 },
        );
      }),
    );

    const { result } = renderHook(() => useClusterScan(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate(99);
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ============================================================================
// useClusterScanResult
// ============================================================================

describe('useClusterScanResult', () => {
  it('returns undefined data when query is disabled (enabled: false)', () => {
    const { result } = renderHook(
      () => useClusterScanResult(1),
      { wrapper: createWrapper() },
    );

    // enabled: false means it never auto-fetches
    expect(result.current.data).toBeUndefined();
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('handles null clusterId', () => {
    const { result } = renderHook(
      () => useClusterScanResult(null),
      { wrapper: createWrapper() },
    );

    expect(result.current.data).toBeUndefined();
    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// useAdaptiveModulePlan
// ============================================================================

describe('useAdaptiveModulePlan', () => {
  it('generates an adaptive module plan', async () => {
    setupResourceHandlers();
    const { result } = renderHook(() => useAdaptiveModulePlan(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({ clusterId: 1, templateSlug: 'default' });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      plan: {
        modules: expect.any(Array),
      },
    });
  });

  it('handles plan generation error', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/adaptive-modules', () => {
        return HttpResponse.json(
          { error: { message: 'Cannot connect to cluster' } },
          { status: 500 },
        );
      }),
    );

    const { result } = renderHook(() => useAdaptiveModulePlan(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({ clusterId: 99 });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
