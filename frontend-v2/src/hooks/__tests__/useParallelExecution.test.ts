/**
 * Tests for useParallelExecution hooks
 *
 * Covers: useExecutionPlan fetches layer-based execution plan,
 * useDeployAllModules triggers parallel deploy mutation,
 * useDestroyAllModules triggers parallel destroy mutation,
 * useParallelExecutions fetches execution history,
 * useRunProgress polls new /orchestration/{run_handle} endpoint,
 * useActiveRunHandle manages string run handle state,
 * and error handling for each hook.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useExecutionPlan,
  useDeployAllModules,
  useDestroyAllModules,
  useParallelExecutions,
  useRunProgress,
  useActiveRunHandle,
} from '@/hooks/useParallelExecution';
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

// ============================================================================
// Mock Data
// ============================================================================

const mockRunProgress = {
  run_handle: 'celery-task-abc123',
  project_id: 1,
  action: 'deploy',
  status: 'in_progress',
  total_layers: 2,
  current_layer: 0,
  layers: [
    {
      layer_index: 0,
      module_ids: [1],
      module_names: ['vpc'],
      status: 'in_progress',
      results: {},
    },
    {
      layer_index: 1,
      module_ids: [2],
      module_names: ['eks'],
      status: 'pending',
      results: {},
    },
  ],
  successful_modules: [],
  failed_modules: [],
  skipped_modules: [],
  started_at: '2026-05-27T10:00:00Z',
  completed_at: null,
  duration_seconds: null,
  progress_percent: 25,
  error_message: null,
};

const mockRunProgressCompleted = {
  ...mockRunProgress,
  status: 'completed',
  progress_percent: 100,
  completed_at: '2026-05-27T10:05:00Z',
  duration_seconds: 300,
  successful_modules: [1, 2],
};

const mockExecutionPlan = {
  layers: [
    { order: 1, modules: [{ id: 1, name: 'vpc' }] },
    { order: 2, modules: [{ id: 2, name: 'eks' }, { id: 3, name: 'rds' }] },
  ],
  estimated_time: '5 min',
};

const mockDeployResponse = {
  execution_id: 1,
  status: 'pending',
  action: 'apply',
  total_layers: 3,
};

const mockDestroyResponse = {
  execution_id: 2,
  status: 'pending',
  action: 'destroy',
  total_layers: 3,
};

const mockParallelExecutionsList = [
  {
    id: 1,
    status: 'completed',
    action: 'apply',
    current_layer: 3,
    total_layers: 3,
    successful_modules: [1, 2],
    failed_modules: [],
    skipped_modules: [],
  },
  {
    id: 2,
    status: 'in_progress',
    action: 'destroy',
    current_layer: 1,
    total_layers: 2,
    successful_modules: [],
    failed_modules: [],
    skipped_modules: [],
  },
];

// ============================================================================
// Handlers
// ============================================================================

const parallelHandlers = [
  http.get('*/api/projects/:projectId/execution-plan', () => {
    return HttpResponse.json(mockExecutionPlan);
  }),

  http.post('*/api/projects/:projectId/deploy-all', () => {
    return HttpResponse.json(mockDeployResponse);
  }),

  http.post('*/api/projects/:projectId/destroy-all', () => {
    return HttpResponse.json(mockDestroyResponse);
  }),

  http.get('*/api/projects/:projectId/parallel-executions', () => {
    return HttpResponse.json(mockParallelExecutionsList);
  }),

  http.get('*/api/projects/:projectId/orchestration/:runHandle', () => {
    return HttpResponse.json(mockRunProgress);
  }),
];

// ============================================================================
// Tests
// ============================================================================

describe('useExecutionPlan', () => {
  beforeEach(() => {
    server.use(...parallelHandlers);
  });

  it('fetches execution plan with layers', async () => {
    const { result } = renderHook(() => useExecutionPlan(1), {
      wrapper: createWrapper(),
    });

    expect(result.current.isLoading).toBe(true);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.layers).toHaveLength(2);
    expect(result.current.data?.layers[0].order).toBe(1);
    expect(result.current.data?.layers[0].modules[0].name).toBe('vpc');
    expect(result.current.data?.estimated_time).toBe('5 min');
  });

  it('respects enabled parameter', () => {
    const { result } = renderHook(() => useExecutionPlan(1, false), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
  });

  it('handles API errors gracefully', async () => {
    server.use(
      http.get('*/api/projects/:projectId/execution-plan', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Failed to compute plan' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useExecutionPlan(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });
});

describe('useDeployAllModules', () => {
  beforeEach(() => {
    server.use(...parallelHandlers);
  });

  it('triggers deploy and returns pending execution', async () => {
    const { result } = renderHook(() => useDeployAllModules(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ projectId: 1, parallel: true });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      execution_id: 1,
      status: 'pending',
      action: 'apply',
    });
  });

  it('handles deploy errors', async () => {
    server.use(
      http.post('*/api/projects/:projectId/deploy-all', () => {
        return HttpResponse.json(
          { error: { code: 'CONFLICT', message: 'Deployment already in progress' } },
          { status: 409 }
        );
      })
    );

    const { result } = renderHook(() => useDeployAllModules(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ projectId: 1 });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });

  it('sends correct payload shape matching DeployAllRequest', async () => {
    // Backend: DeployAllRequest { parallel: bool = True }
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/projects/:projectId/deploy-all', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ execution_id: 3, status: 'pending', action: 'apply' });
      })
    );

    const { result } = renderHook(() => useDeployAllModules(), { wrapper: createWrapper() });

    result.current.mutate({ projectId: 1, parallel: false });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({ parallel: false });
    expect(capturedBody).not.toHaveProperty('project_id'); // project_id is in URL
    expect(capturedBody).not.toHaveProperty('action');
  });
});

describe('useDestroyAllModules', () => {
  beforeEach(() => {
    server.use(...parallelHandlers);
  });

  it('triggers destroy and returns pending execution', async () => {
    const { result } = renderHook(() => useDestroyAllModules(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ projectId: 1, parallel: true });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      execution_id: 2,
      status: 'pending',
      action: 'destroy',
    });
  });

  it('sends correct payload shape matching DestroyAllRequest', async () => {
    // Backend: DestroyAllRequest { parallel: bool = True }
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/projects/:projectId/destroy-all', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ execution_id: 4, status: 'pending', action: 'destroy' });
      })
    );

    const { result } = renderHook(() => useDestroyAllModules(), { wrapper: createWrapper() });

    result.current.mutate({ projectId: 1, parallel: true });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({ parallel: true });
    expect(capturedBody).not.toHaveProperty('project_id');
    expect(capturedBody).not.toHaveProperty('action');
  });
});

describe('useParallelExecutions', () => {
  beforeEach(() => {
    server.use(...parallelHandlers);
  });

  it('fetches parallel execution history', async () => {
    const { result } = renderHook(() => useParallelExecutions(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(Array.isArray(result.current.data)).toBe(true);
    expect(result.current.data).toHaveLength(2);
    expect(result.current.data![0].status).toBe('completed');
    expect(result.current.data![1].status).toBe('in_progress');
  });

  it('passes limit parameter', async () => {
    let capturedUrl: URL | null = null;
    server.use(
      http.get('*/api/projects/:projectId/parallel-executions', ({ request }) => {
        capturedUrl = new URL(request.url);
        return HttpResponse.json(mockParallelExecutionsList);
      })
    );

    const { result } = renderHook(() => useParallelExecutions(1, 5), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedUrl).not.toBeNull();
    expect(capturedUrl!.searchParams.get('limit')).toBe('5');
  });
});

// ============================================================================
// useRunProgress (D-001 Phase 3 S3a — new string-keyed poller)
// ============================================================================

describe('useRunProgress', () => {
  beforeEach(() => {
    server.use(...parallelHandlers);
  });

  it('fetches run progress for a given run handle', async () => {
    const { result } = renderHook(
      () => useRunProgress(1, 'celery-task-abc123'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.run_handle).toBe('celery-task-abc123');
    expect(result.current.data?.status).toBe('in_progress');
    expect(result.current.data?.action).toBe('deploy');
    expect(result.current.data?.progress_percent).toBe(25);
    expect(result.current.data?.layers).toHaveLength(2);
  });

  it('is disabled when runHandle is null', () => {
    const { result } = renderHook(
      () => useRunProgress(1, null),
      { wrapper: createWrapper() }
    );

    expect(result.current.fetchStatus).toBe('idle');
  });

  it('handles 404 when run handle not found', async () => {
    server.use(
      http.get('*/api/projects/:projectId/orchestration/:runHandle', () => {
        return HttpResponse.json({ detail: "Run 'unknown' not found" }, { status: 404 });
      })
    );

    const { result } = renderHook(
      () => useRunProgress(1, 'unknown'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });

  it('polls with correct URL shape', async () => {
    let capturedUrl: URL | null = null;
    server.use(
      http.get('*/api/projects/:projectId/orchestration/:runHandle', ({ request }) => {
        capturedUrl = new URL(request.url);
        return HttpResponse.json(mockRunProgress);
      })
    );

    const { result } = renderHook(
      () => useRunProgress(1, 'my-run-handle'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedUrl).not.toBeNull();
    expect(capturedUrl!.pathname).toContain('/orchestration/my-run-handle');
  });

  it('returns correct RunProgress shape with layers and results', async () => {
    server.use(
      http.get('*/api/projects/:projectId/orchestration/:runHandle', () => {
        return HttpResponse.json(mockRunProgressCompleted);
      })
    );

    const { result } = renderHook(
      () => useRunProgress(1, 'celery-task-abc123'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.status).toBe('completed');
    expect(result.current.data?.successful_modules).toEqual([1, 2]);
    expect(result.current.data?.duration_seconds).toBe(300);
    expect(result.current.data?.completed_at).toBe('2026-05-27T10:05:00Z');
  });
});

// ============================================================================
// useActiveRunHandle (D-001 Phase 3 S3a — session-scoped string handle state)
// ============================================================================

describe('useActiveRunHandle', () => {
  it('starts with no active run', () => {
    const { result } = renderHook(() => useActiveRunHandle());

    expect(result.current.activeRunHandle).toBeNull();
    expect(result.current.activeAction).toBeNull();
    expect(result.current.hasActiveRun).toBe(false);
  });

  it('sets and clears run handle', async () => {
    const { result } = renderHook(() => useActiveRunHandle());

    act(() => {
      result.current.setActiveRunHandle('my-run-id', 'deploy');
    });

    expect(result.current.activeRunHandle).toBe('my-run-id');
    expect(result.current.activeAction).toBe('deploy');
    expect(result.current.hasActiveRun).toBe(true);

    act(() => {
      result.current.clearActiveRunHandle();
    });

    expect(result.current.activeRunHandle).toBeNull();
    expect(result.current.hasActiveRun).toBe(false);
  });

  it('tracks destroy action separately from deploy', () => {
    const { result } = renderHook(() => useActiveRunHandle());

    act(() => {
      result.current.setActiveRunHandle('destroy-run-id', 'destroy');
    });

    expect(result.current.activeAction).toBe('destroy');
    expect(result.current.activeRunHandle).toBe('destroy-run-id');
  });

  it('supports setting handle without action', () => {
    const { result } = renderHook(() => useActiveRunHandle());

    act(() => {
      result.current.setActiveRunHandle('handle-no-action');
    });

    expect(result.current.activeRunHandle).toBe('handle-no-action');
    expect(result.current.activeAction).toBeNull();
  });
});
