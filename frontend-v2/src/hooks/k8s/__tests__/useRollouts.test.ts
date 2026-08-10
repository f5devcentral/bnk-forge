/**
 * Tests for useRollouts hooks
 *
 * Covers: rollout history, rollout status, rollout undo, rollout restart,
 * cordon/uncordon/drain node operations.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useRolloutHistory,
  useRolloutStatus,
  useRolloutUndo,
  useRolloutRestart,
  useCordonNode,
  useUncordonNode,
  useDrainNode,
} from '../useRollouts';
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

function setupRolloutHandlers() {
  server.use(
    http.get('*/api/k8s/clusters/:clusterId/deployments/:deploymentName/rollout/history', () => {
      return HttpResponse.json({
        history: [
          { revision: 3, change_cause: 'Updated image to v1.3', created_at: '2026-02-20T10:00:00Z' },
          { revision: 2, change_cause: 'Updated image to v1.2', created_at: '2026-02-15T10:00:00Z' },
          { revision: 1, change_cause: 'Initial deployment', created_at: '2026-02-01T10:00:00Z' },
        ],
        deployment_name: 'nginx-deployment',
        namespace: 'default',
      });
    }),
    http.get('*/api/k8s/clusters/:clusterId/deployments/:deploymentName/rollout/status', () => {
      return HttpResponse.json({
        deployment_name: 'nginx-deployment',
        namespace: 'default',
        status: 'complete',
        replicas: 3,
        ready_replicas: 3,
        updated_replicas: 3,
        available_replicas: 3,
        conditions: [
          { type: 'Available', status: 'True', message: 'Deployment has minimum availability' },
          { type: 'Progressing', status: 'True', message: 'ReplicaSet has successfully progressed' },
        ],
      });
    }),
    http.post('*/api/k8s/clusters/:clusterId/deployments/:deploymentName/rollout/undo', () => {
      return HttpResponse.json({ success: true, message: 'Rollback to revision 2 complete' });
    }),
    http.post('*/api/k8s/clusters/:clusterId/deployments/:deploymentName/rollout/restart', () => {
      return HttpResponse.json({ success: true, message: 'Rolling restart initiated' });
    }),
    http.post('*/api/k8s/clusters/:clusterId/nodes/:nodeName/cordon', () => {
      return HttpResponse.json({ success: true, message: 'Node cordoned' });
    }),
    http.post('*/api/k8s/clusters/:clusterId/nodes/:nodeName/uncordon', () => {
      return HttpResponse.json({ success: true, message: 'Node uncordoned' });
    }),
    http.post('*/api/k8s/clusters/:clusterId/nodes/:nodeName/drain', () => {
      return HttpResponse.json({
        success: true,
        message: 'Node drained',
        evicted_pods: [{ name: 'nginx-pod', namespace: 'default' }],
        failed_pods: [],
      });
    }),
  );
}

// ============================================================================
// useRolloutHistory
// ============================================================================

describe('useRolloutHistory', () => {
  it('fetches rollout history for a deployment', async () => {
    setupRolloutHandlers();
    const { result } = renderHook(
      () => useRolloutHistory(1, 'nginx-deployment', 'default'),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data!.history).toHaveLength(3);
    expect(result.current.data!.history[0].revision).toBe(3);
    expect(result.current.data!.deployment_name).toBe('nginx-deployment');
  });

  it('does not fetch when deploymentName is empty', () => {
    const { result } = renderHook(
      () => useRolloutHistory(1, '', 'default'),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when namespace is empty', () => {
    const { result } = renderHook(
      () => useRolloutHistory(1, 'nginx-deployment', ''),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when enabled is false', () => {
    const { result } = renderHook(
      () => useRolloutHistory(1, 'nginx-deployment', 'default', { enabled: false }),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// useRolloutStatus
// ============================================================================

describe('useRolloutStatus', () => {
  it('fetches rollout status for a deployment', async () => {
    setupRolloutHandlers();
    const { result } = renderHook(
      () => useRolloutStatus(1, 'nginx-deployment', 'default'),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      deployment_name: 'nginx-deployment',
      status: 'complete',
      replicas: 3,
      ready_replicas: 3,
    });
  });

  it('does not fetch when clusterId is 0', () => {
    const { result } = renderHook(
      () => useRolloutStatus(0, 'nginx-deployment', 'default'),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when enabled is false', () => {
    const { result } = renderHook(
      () => useRolloutStatus(1, 'nginx-deployment', 'default', { enabled: false }),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('handles API error', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/deployments/:deploymentName/rollout/status', () => {
        return HttpResponse.json({ error: { message: 'Deployment not found' } }, { status: 404 });
      }),
    );

    const { result } = renderHook(
      () => useRolloutStatus(1, 'nonexistent', 'default'),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ============================================================================
// useRolloutUndo
// ============================================================================

describe('useRolloutUndo', () => {
  it('undoes a rollout to a previous revision', async () => {
    setupRolloutHandlers();
    const { result } = renderHook(() => useRolloutUndo(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        deploymentName: 'nginx-deployment',
        namespace: 'default',
        revision: 2,
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      message: 'Rollback to revision 2 complete',
    });
  });

  it('handles undo error', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/deployments/:deploymentName/rollout/undo', () => {
        return HttpResponse.json(
          { error: { message: 'Cannot rollback' } },
          { status: 400 },
        );
      }),
    );

    const { result } = renderHook(() => useRolloutUndo(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        deploymentName: 'nginx-deployment',
        namespace: 'default',
      });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ============================================================================
// useRolloutRestart
// ============================================================================

describe('useRolloutRestart', () => {
  it('restarts a deployment rollout', async () => {
    setupRolloutHandlers();
    const { result } = renderHook(() => useRolloutRestart(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        deploymentName: 'nginx-deployment',
        namespace: 'default',
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      message: 'Rolling restart initiated',
    });
  });
});

// ============================================================================
// useCordonNode
// ============================================================================

describe('useCordonNode', () => {
  it('cordons a node', async () => {
    setupRolloutHandlers();
    const { result } = renderHook(() => useCordonNode(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({ clusterId: 1, nodeName: 'node-1' });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      message: 'Node cordoned',
    });
  });
});

// ============================================================================
// useUncordonNode
// ============================================================================

describe('useUncordonNode', () => {
  it('uncordons a node', async () => {
    setupRolloutHandlers();
    const { result } = renderHook(() => useUncordonNode(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({ clusterId: 1, nodeName: 'node-1' });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      message: 'Node uncordoned',
    });
  });
});

// ============================================================================
// useDrainNode
// ============================================================================

describe('useDrainNode', () => {
  it('drains a node', async () => {
    setupRolloutHandlers();
    const { result } = renderHook(() => useDrainNode(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({
        clusterId: 1,
        nodeName: 'node-1',
        options: { ignore_daemonsets: true, delete_emptydir_data: true },
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      message: 'Node drained',
    });
    expect(result.current.data!.evicted_pods).toHaveLength(1);
    expect(result.current.data!.failed_pods).toHaveLength(0);
  });

  it('handles drain error', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/nodes/:nodeName/drain', () => {
        return HttpResponse.json(
          { error: { message: 'Cannot evict pods' } },
          { status: 500 },
        );
      }),
    );

    const { result } = renderHook(() => useDrainNode(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({ clusterId: 1, nodeName: 'node-1' });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
