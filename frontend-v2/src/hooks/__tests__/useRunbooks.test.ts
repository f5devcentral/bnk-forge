/**
 * Tests for useRunbooks hooks
 *
 * Covers: useRunbooks fetches available runbooks,
 * useExecuteRunbook mutation calls correct endpoint,
 * error state handling.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { useRunbooks, useExecuteRunbook } from '@/hooks/useRunbooks';
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

const mockRunbooks = {
  runbooks: [
    {
      id: 'health-check',
      name: 'Cluster Health Check',
      description: 'Validates cluster health including node status, pod health, and resource usage.',
      category: 'diagnostics',
      steps: [
        { id: 'check-nodes', name: 'Check Node Status', order: 1 },
        { id: 'check-pods', name: 'Check Pod Health', order: 2 },
        { id: 'check-resources', name: 'Check Resource Usage', order: 3 },
      ],
    },
    {
      id: 'pre-deploy',
      name: 'Pre-Deployment Checklist',
      description: 'Validates environment readiness before deploying BIG-IP Next.',
      category: 'deployment',
      steps: [
        { id: 'check-namespace', name: 'Verify Namespace', order: 1 },
        { id: 'check-rbac', name: 'Verify RBAC', order: 2 },
        { id: 'check-storage', name: 'Verify Storage Class', order: 3 },
        { id: 'check-network', name: 'Verify Network Policy', order: 4 },
      ],
    },
    {
      id: 'troubleshoot-connectivity',
      name: 'Troubleshoot Connectivity',
      description: 'Diagnoses network connectivity issues between services.',
      category: 'troubleshooting',
      steps: [
        { id: 'check-dns', name: 'Check DNS Resolution', order: 1 },
        { id: 'check-services', name: 'Check Service Endpoints', order: 2 },
      ],
    },
  ],
  total: 3,
};

const mockExecutionResult = {
  runbook_id: 'health-check',
  cluster_id: 1,
  status: 'completed',
  overall_result: 'pass',
  started_at: '2026-02-18T12:00:00Z',
  completed_at: '2026-02-18T12:01:30Z',
  duration_seconds: 90,
  steps: [
    {
      id: 'check-nodes',
      name: 'Check Node Status',
      status: 'pass',
      message: 'All 3 nodes are Ready',
      duration_ms: 450,
    },
    {
      id: 'check-pods',
      name: 'Check Pod Health',
      status: 'pass',
      message: '15/15 pods running',
      duration_ms: 800,
    },
    {
      id: 'check-resources',
      name: 'Check Resource Usage',
      status: 'pass',
      message: 'CPU: 45%, Memory: 62% — within thresholds',
      duration_ms: 350,
    },
  ],
};

// ============================================================================
// Handlers
// ============================================================================

const runbookHandlers = [
  http.get('*/api/runbooks', () => {
    return HttpResponse.json(mockRunbooks);
  }),

  http.post('*/api/runbooks/:runbookId/run', () => {
    return HttpResponse.json(mockExecutionResult);
  }),
];

describe('useRunbooks', () => {
  beforeEach(() => {
    server.use(...runbookHandlers);
  });

  it('fetches available runbooks', async () => {
    const { result } = renderHook(() => useRunbooks(), {
      wrapper: createWrapper(),
    });

    expect(result.current.isLoading).toBe(true);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.runbooks).toHaveLength(3);
    expect(result.current.data?.total).toBe(3);
  });

  it('returns correct runbook shape', async () => {
    const { result } = renderHook(() => useRunbooks(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    const healthCheck = result.current.data?.runbooks[0];
    expect(healthCheck).toMatchObject({
      id: 'health-check',
      name: 'Cluster Health Check',
      category: 'diagnostics',
    });
    expect(healthCheck?.steps).toHaveLength(3);
    expect(healthCheck?.steps[0].name).toBe('Check Node Status');
  });

  it('returns runbooks from different categories', async () => {
    const { result } = renderHook(() => useRunbooks(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    const categories = result.current.data?.runbooks.map((r: { category: string }) => r.category);
    expect(categories).toContain('diagnostics');
    expect(categories).toContain('deployment');
    expect(categories).toContain('troubleshooting');
  });

  it('handles API errors gracefully', async () => {
    server.use(
      http.get('*/api/runbooks', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Service unavailable' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useRunbooks(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });

  it('returns loading state initially', () => {
    server.use(...runbookHandlers);

    const { result } = renderHook(() => useRunbooks(), {
      wrapper: createWrapper(),
    });

    expect(result.current.isLoading).toBe(true);
    expect(result.current.data).toBeUndefined();
  });
});

describe('useExecuteRunbook', () => {
  beforeEach(() => {
    server.use(...runbookHandlers);
  });

  it('executes a runbook and returns result', async () => {
    const { result } = renderHook(() => useExecuteRunbook(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ runbookId: 'health-check', clusterId: 1 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      runbook_id: 'health-check',
      cluster_id: 1,
      status: 'completed',
      overall_result: 'pass',
    });
  });

  it('returns step results', async () => {
    const { result } = renderHook(() => useExecuteRunbook(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ runbookId: 'health-check', clusterId: 1 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.steps).toHaveLength(3);
    expect(result.current.data?.steps[0].status).toBe('pass');
    expect(result.current.data?.steps[0].message).toContain('nodes are Ready');
  });

  it('sends correct request body', async () => {
    let capturedUrl = '';
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.post('*/api/runbooks/:runbookId/run', async ({ request, params }) => {
        capturedUrl = params.runbookId as string;
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(mockExecutionResult);
      })
    );

    const { result } = renderHook(() => useExecuteRunbook(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ runbookId: 'pre-deploy', clusterId: 5 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedUrl).toBe('pre-deploy');
    expect(capturedBody).toMatchObject({ cluster_id: 5 });
  });

  it('handles execution errors', async () => {
    server.use(
      http.post('*/api/runbooks/:runbookId/run', () => {
        return HttpResponse.json(
          { error: { code: 'CLUSTER_OFFLINE', message: 'Cluster is not reachable' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useExecuteRunbook(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ runbookId: 'health-check', clusterId: 1 });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });

  it('handles runbook not found error', async () => {
    server.use(
      http.post('*/api/runbooks/:runbookId/run', () => {
        return HttpResponse.json(
          { error: { code: 'NOT_FOUND', message: 'Runbook not found' } },
          { status: 404 }
        );
      })
    );

    const { result } = renderHook(() => useExecuteRunbook(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ runbookId: 'nonexistent', clusterId: 1 });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});
