/**
 * Tests for useDrift hooks
 *
 * Covers: useDriftChecks returns array directly (new API format),
 * useGlobalDriftSummary returns summary object,
 * useTriggerDriftCheck calls correct endpoint.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useDriftChecks,
  useGlobalDriftSummary,
  useTriggerDriftCheck,
  useUpdateDriftSettings,
  useDriftSettings,
  useProjectDriftSummary,
} from '@/hooks/useDrift';
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

const mockDriftChecks = [
  {
    id: 1,
    project_id: 1,
    module_id: 1,
    module_name: 'vpc',
    status: 'drift_detected',
    drift_detected: true,
    changes_detected: 2,
    started_at: '2026-02-18T10:00:00Z',
    completed_at: '2026-02-18T10:01:00Z',
  },
  {
    id: 2,
    project_id: 1,
    module_id: 2,
    module_name: 'eks',
    status: 'no_drift',
    drift_detected: false,
    changes_detected: 0,
    started_at: '2026-02-18T11:00:00Z',
    completed_at: '2026-02-18T11:01:00Z',
  },
];

const mockGlobalDriftSummary = {
  total_modules: 15,
  modules_with_drift: 3,
  modules_without_drift: 10,
  modules_pending: 2,
  last_check_at: '2026-02-18T12:00:00Z',
};

const mockDriftSettings = {
  enabled: true,
  schedule_interval: 3600,
  notify_on_drift: true,
  auto_remediate: false,
};

// ============================================================================
// Handlers
// ============================================================================

const driftHandlers = [
  // Drift checks — returns DriftCheck[] directly (new format)
  http.get('*/api/projects/:projectId/drift/checks', () => {
    return HttpResponse.json(mockDriftChecks);
  }),

  // Global drift summary
  http.get('*/api/drift/summary', () => {
    return HttpResponse.json(mockGlobalDriftSummary);
  }),

  // Project drift summary
  http.get('*/api/projects/:projectId/drift/summary', () => {
    return HttpResponse.json(mockGlobalDriftSummary);
  }),

  // Drift settings
  http.get('*/api/projects/:projectId/drift/settings', () => {
    return HttpResponse.json(mockDriftSettings);
  }),

  // Trigger drift check
  http.post('*/api/projects/:projectId/drift/check-now', () => {
    return HttpResponse.json({
      message: 'Drift check has been queued for execution.',
      task_ids: [101, 102],
    });
  }),
];

describe('useDriftChecks', () => {
  beforeEach(() => {
    server.use(...driftHandlers);
  });

  it('returns array of drift checks directly', async () => {
    const { result } = renderHook(() => useDriftChecks(1), {
      wrapper: createWrapper(),
    });

    expect(result.current.isLoading).toBe(true);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // New API format: DriftCheck[] directly, not { checks, total }
    expect(Array.isArray(result.current.data)).toBe(true);
    expect(result.current.data).toHaveLength(2);
    expect(result.current.data![0].module_name).toBe('vpc');
    expect(result.current.data![0].drift_detected).toBe(true);
    expect(result.current.data![1].module_name).toBe('eks');
    expect(result.current.data![1].drift_detected).toBe(false);
  });

  it('does not fetch when projectId is undefined', () => {
    const { result } = renderHook(() => useDriftChecks(undefined), {
      wrapper: createWrapper(),
    });

    // Should not be loading since query is disabled
    expect(result.current.isLoading).toBe(false);
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('handles API errors gracefully', async () => {
    server.use(
      http.get('*/api/projects/:projectId/drift/checks', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Database error' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useDriftChecks(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });

  it('passes query params for moduleId, limit, offset', async () => {
    let capturedUrl: URL | null = null;
    server.use(
      http.get('*/api/projects/:projectId/drift/checks', ({ request }) => {
        capturedUrl = new URL(request.url);
        return HttpResponse.json(mockDriftChecks);
      })
    );

    const { result } = renderHook(
      () => useDriftChecks(1, { moduleId: 5, limit: 10, offset: 20 }),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedUrl).not.toBeNull();
    expect(capturedUrl!.searchParams.get('module_id')).toBe('5');
    expect(capturedUrl!.searchParams.get('limit')).toBe('10');
    expect(capturedUrl!.searchParams.get('offset')).toBe('20');
  });
});

describe('useGlobalDriftSummary', () => {
  beforeEach(() => {
    server.use(...driftHandlers);
  });

  it('returns global drift summary object', async () => {
    const { result } = renderHook(() => useGlobalDriftSummary(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      total_modules: 15,
      modules_with_drift: 3,
      modules_without_drift: 10,
      modules_pending: 2,
    });
    expect(result.current.data?.last_check_at).toBeDefined();
  });

  it('handles API errors gracefully', async () => {
    server.use(
      http.get('*/api/drift/summary', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Service unavailable' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useGlobalDriftSummary(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});

describe('useDriftSettings', () => {
  beforeEach(() => {
    server.use(...driftHandlers);
  });

  it('fetches drift settings for a project', async () => {
    const { result } = renderHook(() => useDriftSettings(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      enabled: true,
      schedule_interval: 3600,
    });
  });

  it('does not fetch when projectId is undefined', () => {
    const { result } = renderHook(() => useDriftSettings(undefined), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useProjectDriftSummary', () => {
  beforeEach(() => {
    server.use(...driftHandlers);
  });

  it('fetches project drift summary', async () => {
    const { result } = renderHook(() => useProjectDriftSummary(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.total_modules).toBe(15);
  });
});

describe('useTriggerDriftCheck', () => {
  beforeEach(() => {
    server.use(...driftHandlers);
  });

  it('calls correct endpoint and returns result', async () => {
    const { result } = renderHook(() => useTriggerDriftCheck(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ projectId: 1 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      message: 'Drift check has been queued for execution.',
      task_ids: [101, 102],
    });
  });

  it('sends correct payload shape matching TriggerDriftCheckRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/projects/:projectId/drift/check-now', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ message: 'Queued', task_ids: [201] });
      })
    );

    const { result } = renderHook(() => useTriggerDriftCheck(), { wrapper: createWrapper() });

    result.current.mutate({ projectId: 1, data: { module_ids: [5, 10] } });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Backend: TriggerDriftCheckRequest { module_ids: list[int] | None }
    expect(capturedBody).toMatchObject({ module_ids: [5, 10] });
    expect(capturedBody).not.toHaveProperty('project_id'); // project_id is in URL
  });

  it('handles mutation errors', async () => {
    server.use(
      http.post('*/api/projects/:projectId/drift/check-now', () => {
        return HttpResponse.json(
          { error: { code: 'RATE_LIMITED', message: 'Too many drift checks' } },
          { status: 429 }
        );
      })
    );

    const { result } = renderHook(() => useTriggerDriftCheck(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ projectId: 1 });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });
});

// ============================================================================
// CT-018: Payload Shape Assertions — useUpdateDriftSettings
// Backend: DriftSettingsRequest { enabled, schedule_type, schedule_value, check_all_modules, ... }
// ============================================================================

describe('useUpdateDriftSettings', () => {
  it('sends correct payload shape matching DriftSettingsRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/projects/:projectId/drift/settings', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, ...capturedBody });
      })
    );

    const { result } = renderHook(() => useUpdateDriftSettings(), { wrapper: createWrapper() });

    result.current.mutate({
      projectId: 1,
      data: {
        enabled: true,
        schedule_type: 'cron',
        schedule_value: '0 4 * * *',
        check_all_modules: false,
        module_ids: [1, 2, 3],
        notify_on_drift: true,
        ignore_insignificant_changes: true,
      },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      enabled: true,
      schedule_type: 'cron',
      schedule_value: '0 4 * * *',
      check_all_modules: false,
      module_ids: [1, 2, 3],
      notify_on_drift: true,
      ignore_insignificant_changes: true,
    });
    expect(capturedBody).not.toHaveProperty('project_id');
    expect(capturedBody).not.toHaveProperty('settings');
  });
});
