/**
 * Tests for usePods hooks
 *
 * Covers: pod logs, restart pod, pod containers, describe resource,
 * cluster events, pod metrics, and node metrics.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  usePodLogs,
  useRestartPod,
  usePodContainers,
  useDescribeResource,
  useClusterEvents,
  usePodMetrics,
  useNodeMetrics,
} from '../usePods';
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

function setupPodHandlers() {
  server.use(
    http.get('*/api/k8s/clusters/:clusterId/pods/:podName/logs', () => {
      return HttpResponse.json({
        logs: '2026-02-20T10:00:00Z Starting server...\n2026-02-20T10:00:01Z Server started on port 8080',
        pod_name: 'nginx-pod',
        namespace: 'default',
      });
    }),
    http.post('*/api/k8s/clusters/:clusterId/pods/:podName/restart', () => {
      return HttpResponse.json({ success: true, message: 'Pod restarted successfully' });
    }),
    http.get('*/api/k8s/clusters/:clusterId/pods/:podName/containers', () => {
      return HttpResponse.json({
        pod_name: 'nginx-pod',
        namespace: 'default',
        containers: [
          { name: 'nginx', image: 'nginx:1.25', ready: true, state: 'running', restart_count: 0 },
          { name: 'sidecar', image: 'envoy:1.28', ready: true, state: 'running', restart_count: 1 },
        ],
        init_containers: [
          { name: 'init-config', image: 'busybox:1.36', ready: false, state: 'terminated', restart_count: 0 },
        ],
      });
    }),
    http.get('*/api/k8s/clusters/:clusterId/resources/:resourceType/:resourceName/describe', () => {
      return HttpResponse.json({
        metadata: { name: 'nginx-deployment', namespace: 'default', labels: { app: 'nginx' } },
        conditions: [{ type: 'Available', status: 'True', message: 'Deployment has minimum availability' }],
        events: [{ type: 'Normal', reason: 'ScalingReplicaSet', message: 'Scaled up replica set' }],
        status: { replicas: 3, readyReplicas: 3 },
      });
    }),
    http.get('*/api/k8s/clusters/:clusterId/events', () => {
      return HttpResponse.json({
        events: [
          { type: 'Normal', reason: 'Scheduled', message: 'Successfully assigned pod', namespace: 'default' },
          { type: 'Warning', reason: 'BackOff', message: 'Back-off restarting failed container', namespace: 'kube-system' },
        ],
        count: 2,
      });
    }),
    http.get('*/api/k8s/clusters/:clusterId/top/pods', () => {
      return HttpResponse.json({
        available: true,
        metrics: [
          { name: 'nginx-pod', namespace: 'default', cpu: '100m', memory: '128Mi' },
          { name: 'redis-pod', namespace: 'cache', cpu: '50m', memory: '64Mi' },
        ],
      });
    }),
    http.get('*/api/k8s/clusters/:clusterId/top/nodes', () => {
      return HttpResponse.json({
        available: true,
        metrics: [
          { name: 'node-1', cpu: '500m', memory: '2Gi', cpu_percent: 25, memory_percent: 50 },
        ],
      });
    }),
  );
}

// ============================================================================
// usePodLogs
// ============================================================================

describe('usePodLogs', () => {
  it('fetches pod logs', async () => {
    setupPodHandlers();
    const { result } = renderHook(
      () => usePodLogs(1, 'nginx-pod', 'default'),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      pod_name: 'nginx-pod',
      namespace: 'default',
    });
    expect(result.current.data!.logs).toContain('Starting server');
  });

  it('does not fetch when podName is empty', () => {
    const { result } = renderHook(
      () => usePodLogs(1, '', 'default'),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when namespace is empty', () => {
    const { result } = renderHook(
      () => usePodLogs(1, 'nginx-pod', ''),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when enabled is false', () => {
    const { result } = renderHook(
      () => usePodLogs(1, 'nginx-pod', 'default', { enabled: false }),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('handles API error', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/pods/:podName/logs', () => {
        return HttpResponse.json({ error: { message: 'Pod not found' } }, { status: 404 });
      }),
    );
    const { result } = renderHook(
      () => usePodLogs(1, 'nonexistent-pod', 'default'),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ============================================================================
// useRestartPod
// ============================================================================

describe('useRestartPod', () => {
  it('restarts a pod', async () => {
    setupPodHandlers();
    const { result } = renderHook(() => useRestartPod(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({ clusterId: 1, podName: 'nginx-pod', namespace: 'default' });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      message: 'Pod restarted successfully',
    });
  });

  it('handles restart error', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/pods/:podName/restart', () => {
        return HttpResponse.json({ error: { message: 'Cannot restart pod' } }, { status: 500 });
      }),
    );

    const { result } = renderHook(() => useRestartPod(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({ clusterId: 1, podName: 'failing-pod', namespace: 'default' });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ============================================================================
// usePodContainers
// ============================================================================

describe('usePodContainers', () => {
  it('fetches containers for a pod', async () => {
    setupPodHandlers();
    const { result } = renderHook(
      () => usePodContainers(1, 'nginx-pod', 'default'),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data!.containers).toHaveLength(2);
    expect(result.current.data!.containers[0].name).toBe('nginx');
    expect(result.current.data!.init_containers).toHaveLength(1);
    expect(result.current.data!.init_containers[0].name).toBe('init-config');
  });

  it('does not fetch when podName is empty', () => {
    const { result } = renderHook(
      () => usePodContainers(1, '', 'default'),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when enabled is false', () => {
    const { result } = renderHook(
      () => usePodContainers(1, 'nginx-pod', 'default', { enabled: false }),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// useDescribeResource
// ============================================================================

describe('useDescribeResource', () => {
  it('fetches resource description', async () => {
    setupPodHandlers();
    const { result } = renderHook(
      () => useDescribeResource(1, 'deployments', 'nginx-deployment', 'default'),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data!.metadata.name).toBe('nginx-deployment');
    expect(result.current.data!.conditions).toHaveLength(1);
    expect(result.current.data!.events).toHaveLength(1);
  });

  it('does not fetch when resourceName is empty', () => {
    const { result } = renderHook(
      () => useDescribeResource(1, 'pods', ''),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when clusterId is 0', () => {
    const { result } = renderHook(
      () => useDescribeResource(0, 'pods', 'nginx-pod'),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// useClusterEvents
// ============================================================================

describe('useClusterEvents', () => {
  it('fetches cluster events', async () => {
    setupPodHandlers();
    const { result } = renderHook(
      () => useClusterEvents(1),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data!.events).toHaveLength(2);
    expect(result.current.data!.count).toBe(2);
    expect(result.current.data!.events[0].type).toBe('Normal');
    expect(result.current.data!.events[1].type).toBe('Warning');
  });

  it('does not fetch when clusterId is 0', () => {
    const { result } = renderHook(
      () => useClusterEvents(0),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when enabled is false', () => {
    const { result } = renderHook(
      () => useClusterEvents(1, undefined, { enabled: false }),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// usePodMetrics
// ============================================================================

describe('usePodMetrics', () => {
  it('fetches pod metrics', async () => {
    setupPodHandlers();
    const { result } = renderHook(
      () => usePodMetrics(1),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data!.available).toBe(true);
    expect(result.current.data!.metrics).toHaveLength(2);
    expect(result.current.data!.metrics![0].name).toBe('nginx-pod');
  });

  it('does not fetch when clusterId is 0', () => {
    const { result } = renderHook(
      () => usePodMetrics(0),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// useNodeMetrics
// ============================================================================

describe('useNodeMetrics', () => {
  it('fetches node metrics', async () => {
    setupPodHandlers();
    const { result } = renderHook(
      () => useNodeMetrics(1),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data!.available).toBe(true);
    expect(result.current.data!.metrics).toHaveLength(1);
    expect(result.current.data!.metrics![0].name).toBe('node-1');
  });

  it('does not fetch when clusterId is 0', () => {
    const { result } = renderHook(
      () => useNodeMetrics(0),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when enabled is false', () => {
    const { result } = renderHook(
      () => useNodeMetrics(1, undefined, { enabled: false }),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });
});
