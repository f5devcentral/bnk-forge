/**
 * Tests for useK8sOperations hooks
 *
 * Covers: cluster scan, tunnel management, adaptive modules,
 * metrics, rollout operations, and node operations.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useClusterScan,
  useClusterScanResult,
  useTunnelStatus,
  useAllTunnels,
  useOpenTunnel,
  useCloseTunnel,
  useAdaptiveModulePlan,
  usePodMetrics,
  useNodeMetrics,
  useRolloutHistory,
  useRolloutStatus,
  useRolloutUndo,
  useRolloutRestart,
  useCordonNode,
  useUncordonNode,
  useDrainNode,
} from '@/hooks/useK8sOperations';
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
// Cluster Scanner
// ========================================================================

describe('useClusterScan', () => {
  it('performs a cluster scan mutation', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/scan', () => {
        return HttpResponse.json({ success: true, resources_found: 42, namespaces: 5 });
      })
    );

    const { result } = renderHook(() => useClusterScan(), { wrapper: createWrapper() });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true, resources_found: 42 });
  });

  it('handles scan errors', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/scan', () => {
        return HttpResponse.json(
          { error: { message: 'Cluster unreachable' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useClusterScan(), { wrapper: createWrapper() });

    result.current.mutate(999);

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});

describe('useClusterScanResult', () => {
  it('returns idle state since enabled is false', () => {
    const { result } = renderHook(() => useClusterScanResult(1), { wrapper: createWrapper() });

    expect(result.current.fetchStatus).toBe('idle');
    expect(result.current.data).toBeUndefined();
  });
});

// ========================================================================
// SSH Tunnel Management
// ========================================================================

describe('useTunnelStatus', () => {
  it('fetches tunnel status for a cluster', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/tunnel', () => {
        return HttpResponse.json({ cluster_id: 1, status: 'connected', local_port: 8443 });
      })
    );

    const { result } = renderHook(() => useTunnelStatus(1), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ status: 'connected', local_port: 8443 });
  });

  it('does not fetch when clusterId is null', () => {
    const { result } = renderHook(() => useTunnelStatus(null), { wrapper: createWrapper() });

    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useAllTunnels', () => {
  it('fetches all tunnels', async () => {
    server.use(
      http.get('*/api/k8s/tunnels', () => {
        return HttpResponse.json({
          tunnels: [
            { cluster_id: 1, status: 'connected', local_port: 8443 },
            { cluster_id: 2, status: 'disconnected' },
          ],
          count: 2,
        });
      })
    );

    const { result } = renderHook(() => useAllTunnels(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ count: 2 });
  });
});

describe('useOpenTunnel', () => {
  it('opens a tunnel successfully', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/tunnel/open', () => {
        return HttpResponse.json({ success: true, local_port: 8443 });
      })
    );

    const { result } = renderHook(() => useOpenTunnel(), { wrapper: createWrapper() });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true, local_port: 8443 });
  });
});

describe('useCloseTunnel', () => {
  it('closes a tunnel successfully', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/tunnel/close', () => {
        return HttpResponse.json({ success: true, message: 'Tunnel closed' });
      })
    );

    const { result } = renderHook(() => useCloseTunnel(), { wrapper: createWrapper() });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true });
  });
});

// ========================================================================
// Adaptive Module Selection
// ========================================================================

describe('useAdaptiveModulePlan', () => {
  it('generates an adaptive module plan', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/adaptive-modules', () => {
        return HttpResponse.json({
          modules: [{ path: 'modules/vpc', recommended: true }],
          plan_id: 'plan-123',
        });
      })
    );

    const { result } = renderHook(() => useAdaptiveModulePlan(), { wrapper: createWrapper() });

    result.current.mutate({ clusterId: 1, templateSlug: 'web-app' });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ plan_id: 'plan-123' });
  });

  it('sends correct payload shape matching AdaptiveModuleRequest', async () => {
    // Backend schema: AdaptiveModuleRequest { template_slug: str | None, module_paths: list[str] | None }
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/adaptive-modules', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ modules: [], plan_id: 'plan-456' });
      })
    );

    const { result } = renderHook(() => useAdaptiveModulePlan(), { wrapper: createWrapper() });

    result.current.mutate({ clusterId: 1, templateSlug: 'f5-bnk-2.2', modulePaths: ['modules/vpc', 'modules/nginx'] });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Payload matches backend AdaptiveModuleRequest schema
    expect(capturedBody).toMatchObject({
      template_slug: 'f5-bnk-2.2',
      module_paths: ['modules/vpc', 'modules/nginx'],
    });
    // No wrapping — fields are top-level
    expect(capturedBody).not.toHaveProperty('data');
    expect(capturedBody).not.toHaveProperty('request');
  });

  it('sends null defaults when optional fields omitted', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/adaptive-modules', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ modules: [], plan_id: 'plan-789' });
      })
    );

    const { result } = renderHook(() => useAdaptiveModulePlan(), { wrapper: createWrapper() });

    // Both optional — should send nulls
    result.current.mutate({ clusterId: 1 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      template_slug: null,
      module_paths: null,
    });
  });
});

// ========================================================================
// Metrics
// ========================================================================

describe('usePodMetrics', () => {
  it('fetches pod metrics for a cluster', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/top/pods', () => {
        return HttpResponse.json({
          available: true,
          metrics: [{ pod_name: 'web-abc', cpu: '50m', memory: '128Mi' }],
        });
      })
    );

    const { result } = renderHook(
      () => usePodMetrics(1, { namespace: 'default' }),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ available: true });
    expect(result.current.data!.metrics).toHaveLength(1);
  });

  it('does not fetch when disabled', () => {
    const { result } = renderHook(
      () => usePodMetrics(1, undefined, { enabled: false }),
      { wrapper: createWrapper() }
    );

    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useNodeMetrics', () => {
  it('fetches node metrics for a cluster', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/top/nodes', () => {
        return HttpResponse.json({
          available: true,
          metrics: [{ node_name: 'node-1', cpu: '500m', memory: '2Gi' }],
        });
      })
    );

    const { result } = renderHook(
      () => useNodeMetrics(1),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.metrics).toHaveLength(1);
  });
});

// ========================================================================
// Rollout Management
// ========================================================================

describe('useRolloutHistory', () => {
  it('fetches rollout history', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/deployments/:name/rollout/history', () => {
        return HttpResponse.json({
          history: [
            { revision: 3, status: 'current' },
            { revision: 2, status: 'superseded' },
          ],
          deployment_name: 'web',
          namespace: 'default',
        });
      })
    );

    const { result } = renderHook(
      () => useRolloutHistory(1, 'web', 'default'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.history).toHaveLength(2);
  });
});

describe('useRolloutStatus', () => {
  it('fetches rollout status', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/deployments/:name/rollout/status', () => {
        return HttpResponse.json({
          deployment_name: 'web',
          ready_replicas: 3,
          desired_replicas: 3,
          status: 'complete',
        });
      })
    );

    const { result } = renderHook(
      () => useRolloutStatus(1, 'web', 'default'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ status: 'complete', ready_replicas: 3 });
  });
});

describe('useRolloutUndo', () => {
  it('rolls back a deployment', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/deployments/:name/rollout/undo', () => {
        return HttpResponse.json({ success: true, message: 'Rollback initiated' });
      })
    );

    const { result } = renderHook(() => useRolloutUndo(), { wrapper: createWrapper() });

    result.current.mutate({
      clusterId: 1,
      deploymentName: 'web',
      namespace: 'default',
      revision: 2,
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true });
  });
});

describe('useRolloutRestart', () => {
  it('restarts a deployment rollout', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/deployments/:name/rollout/restart', () => {
        return HttpResponse.json({ success: true, message: 'Restart initiated' });
      })
    );

    const { result } = renderHook(() => useRolloutRestart(), { wrapper: createWrapper() });

    result.current.mutate({ clusterId: 1, deploymentName: 'web', namespace: 'default' });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true });
  });
});

// ========================================================================
// Node Operations
// ========================================================================

describe('useCordonNode', () => {
  it('cordons a node', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/nodes/:nodeName/cordon', () => {
        return HttpResponse.json({ success: true, message: 'Node cordoned' });
      })
    );

    const { result } = renderHook(() => useCordonNode(), { wrapper: createWrapper() });

    result.current.mutate({ clusterId: 1, nodeName: 'node-1' });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true });
  });
});

describe('useUncordonNode', () => {
  it('uncordons a node', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/nodes/:nodeName/uncordon', () => {
        return HttpResponse.json({ success: true, message: 'Node uncordoned' });
      })
    );

    const { result } = renderHook(() => useUncordonNode(), { wrapper: createWrapper() });

    result.current.mutate({ clusterId: 1, nodeName: 'node-1' });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true });
  });
});

describe('useDrainNode', () => {
  it('drains a node with options', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/nodes/:nodeName/drain', () => {
        return HttpResponse.json({
          success: true,
          message: 'Node drained',
          evicted_pods: [{ name: 'web-abc', namespace: 'default' }],
          failed_pods: [],
        });
      })
    );

    const { result } = renderHook(() => useDrainNode(), { wrapper: createWrapper() });

    result.current.mutate({
      clusterId: 1,
      nodeName: 'node-1',
      options: { ignore_daemonsets: true, force: false },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.evicted_pods).toHaveLength(1);
    expect(result.current.data!.failed_pods).toHaveLength(0);
  });

  it('handles drain errors', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/nodes/:nodeName/drain', () => {
        return HttpResponse.json(
          { error: { message: 'PDB violation' } },
          { status: 409 }
        );
      })
    );

    const { result } = renderHook(() => useDrainNode(), { wrapper: createWrapper() });

    result.current.mutate({ clusterId: 1, nodeName: 'node-1' });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});
