/**
 * Tests for useSystem hooks
 *
 * Covers: fetching system health, queue metrics, performance metrics,
 * recent errors, database stats, cleanup database mutation, container
 * status, restart containers mutation, and error handling.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useSystemHealth,
  useQueueMetrics,
  usePerformanceMetrics,
  useRecentErrors,
  useDatabaseStats,
  useCleanupDatabase,
  useContainerStatus,
  useRestartContainers,
} from '@/hooks/useSystem';
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
// useSystemHealth
// ============================================================================

describe('useSystemHealth', () => {
  it('fetches system health status', async () => {
    const { result } = renderHook(() => useSystemHealth(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      status: 'healthy',
      version: '2.10.49',
      uptime_seconds: 86400,
      database: { status: 'healthy' },
      redis: { status: 'healthy' },
      celery: { status: 'healthy', workers: 2 },
    });
  });
});

// ============================================================================
// useQueueMetrics
// ============================================================================

describe('useQueueMetrics', () => {
  it('fetches queue metrics', async () => {
    const { result } = renderHook(() => useQueueMetrics(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      total_pending: 3,
      total_active: 1,
      workers: 2,
    });
    expect(result.current.data!.queues).toHaveLength(2);
    expect(result.current.data!.queues[0]).toMatchObject({
      name: 'default',
      pending: 3,
      active: 1,
    });
  });
});

// ============================================================================
// usePerformanceMetrics
// ============================================================================

describe('usePerformanceMetrics', () => {
  it('fetches performance metrics', async () => {
    const { result } = renderHook(() => usePerformanceMetrics(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      cpu_percent: 23.5,
      memory_percent: 45.2,
      memory_used_mb: 512,
      memory_total_mb: 1024,
      disk_percent: 60.0,
      request_rate_per_minute: 120,
      avg_response_time_ms: 45,
    });
  });
});

// ============================================================================
// useRecentErrors
// ============================================================================

describe('useRecentErrors', () => {
  it('fetches recent errors', async () => {
    const { result } = renderHook(() => useRecentErrors(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      total: 1,
    });
    expect(result.current.data!.errors).toHaveLength(1);
    expect(result.current.data!.errors[0]).toMatchObject({
      task_id: 101,
      type: 'connection_error',
      error: 'Connection refused to K8s API',
      project: 'test-project',
    });
  });

  it('accepts a custom limit parameter', async () => {
    let capturedUrl: string | null = null;
    server.use(
      http.get('*/api/system/errors', ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json({ errors: [], total: 0 });
      })
    );

    const { result } = renderHook(() => useRecentErrors(5), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedUrl).toContain('limit=5');
  });
});

// ============================================================================
// useDatabaseStats
// ============================================================================

describe('useDatabaseStats', () => {
  it('fetches database statistics', async () => {
    const { result } = renderHook(() => useDatabaseStats(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      size_mb: 128,
      table_count: 14,
      total_rows: 15000,
    });
    expect(result.current.data!.tables).toHaveLength(3);
    expect(result.current.data!.tables[0]).toMatchObject({
      name: 'tasks',
      rows: 5000,
    });
  });
});

// ============================================================================
// useCleanupDatabase
// ============================================================================

describe('useCleanupDatabase', () => {
  it('sends cleanup request with type and olderThanDays', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/system/database/cleanup', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          success: true,
          deleted_count: 150,
          cleanup_type: 'tasks',
          cutoff_date: '2026-01-01T00:00:00Z',
        });
      })
    );

    const { result } = renderHook(() => useCleanupDatabase(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ type: 'tasks' as const, olderThanDays: 30 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      cleanup_type: 'tasks',
      older_than_days: 30,
    });
    expect(result.current.data).toMatchObject({
      success: true,
      deleted_count: 150,
    });
  });
});

// ============================================================================
// useContainerStatus
// ============================================================================

describe('useContainerStatus', () => {
  it('fetches container status', async () => {
    const { result } = renderHook(() => useContainerStatus(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      total: 6,
    });
    expect(result.current.data!.containers).toHaveLength(6);
    expect(result.current.data!.containers[0]).toMatchObject({
      service: 'backend',
      status: 'running',
      state: 'healthy',
    });
  });
});

// ============================================================================
// useRestartContainers
// ============================================================================

describe('useRestartContainers', () => {
  it('sends restart request with specified services', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/system/containers/restart', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          message: 'Restart initiated',
          results: [{ service: 'backend', status: 'restarted' }],
          note: 'Services will be back shortly',
        });
      })
    );

    const { result } = renderHook(() => useRestartContainers(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(['backend']);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      services: ['backend'],
    });
    expect(result.current.data).toMatchObject({
      message: 'Restart initiated',
      results: [{ service: 'backend', status: 'restarted' }],
    });
  });
});

// ============================================================================
// Error handling
// ============================================================================

describe('useSystem error handling', () => {
  it('handles API errors on system health', async () => {
    server.use(
      http.get('*/api/system/health', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Service unavailable' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useSystemHealth(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });

  it('handles API errors on database stats', async () => {
    server.use(
      http.get('*/api/system/database/stats', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Database unreachable' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useDatabaseStats(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });
});
