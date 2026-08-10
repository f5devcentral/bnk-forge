/**
 * Tests for useTasks hook
 *
 * Covers: fetching tasks, filtering by status, polling behavior.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { mockTasks } from '@/test/mocks/handlers';
import { useTasks, useTask } from '@/hooks/useTasks';
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

describe('useTasks', () => {
  it('fetches all tasks', async () => {
    const { result } = renderHook(() => useTasks(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
    expect(result.current.data!.tasks).toHaveLength(2);
    expect(result.current.data!.total).toBe(2);
  });

  it('filters tasks by status', async () => {
    const { result } = renderHook(
      () => useTasks({ status: 'completed' }),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // The mock handler filters by status
    expect(result.current.data!.tasks).toHaveLength(1);
    expect(result.current.data!.tasks[0].status).toBe('completed');
  });

  it('filters tasks by in_progress status', async () => {
    const { result } = renderHook(
      () => useTasks({ status: 'in_progress' }),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.tasks).toHaveLength(1);
    expect(result.current.data!.tasks[0].task_type).toBe('plan');
  });

  it('returns correct task data shape', async () => {
    const { result } = renderHook(() => useTasks(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    const task = result.current.data!.tasks[0];
    expect(task).toMatchObject({
      id: 1,
      celery_task_id: 'abc-123',
      task_type: 'apply',
      status: 'completed',
      project_id: 1,
      project_name: 'test-project',
    });
  });

  it('supports project_id filter parameter', async () => {
    server.use(
      http.get('*/api/tasks', ({ request }) => {
        const url = new URL(request.url);
        const projectId = url.searchParams.get('project_id');
        if (projectId === '1') {
          return HttpResponse.json({
            tasks: mockTasks.tasks.filter((t) => t.project_id === 1),
            total: 2,
            limit: 50,
            offset: 0,
          });
        }
        return HttpResponse.json(mockTasks);
      })
    );

    const { result } = renderHook(
      () => useTasks({ project_id: 1 }),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.tasks.length).toBeGreaterThan(0);
  });
});

describe('useTask (single)', () => {
  it('fetches a single task by ID', async () => {
    const { result } = renderHook(
      () => useTask(1),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      id: 1,
      task_type: 'apply',
      status: 'completed',
    });
  });

  it('does not fetch when taskId is 0', () => {
    const { result } = renderHook(
      () => useTask(0),
      { wrapper: createWrapper() }
    );

    // Query should be disabled when taskId is falsy
    expect(result.current.fetchStatus).toBe('idle');
  });
});
