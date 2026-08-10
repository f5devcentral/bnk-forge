/**
 * Tests for useModuleReports hooks (D-034 PR-2.5, #458 — module reports viewer).
 *
 * CT-012: MSW handlers mirror the REAL backend shapes —
 * GET /api/project-modules/{id}/reports → ModuleReportsListResponse
 *     (backend/schemas/projects.py: module_id, runs[{stamp, files[{path,kind,size}]}])
 * GET /api/project-modules/{id}/reports/content?path= → ModuleReportContentResponse
 *     (path, kind, size, content) (backend/routes/project_execution.py).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { useModuleReports, useModuleReportContent } from '@/hooks/useModuleReports';
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

// Real response shape: ModuleReportsService.list_runs — newest-first runs, each
// a <stamp>/ dir with files (path relative to reports dir, kind by extension,
// byte size).
const mockReportsResponse = {
  module_id: 7,
  runs: [
    {
      stamp: '2026-07-18T07-00-00Z',
      files: [
        { path: '2026-07-18T07-00-00Z/run-poc.md', kind: 'md', size: 1200 },
        { path: '2026-07-18T07-00-00Z/scenarios/tcpl4lb.json', kind: 'json', size: 340 },
      ],
    },
    {
      stamp: '2026-07-18T06-00-00Z',
      files: [{ path: '2026-07-18T06-00-00Z/logs/00-init.log', kind: 'log', size: 90 }],
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('useModuleReports', () => {
  it('fetches the report runs newest-first with file metadata', async () => {
    server.use(
      http.get('*/api/project-modules/:moduleId/reports', ({ params }) => {
        expect(params.moduleId).toBe('7');
        return HttpResponse.json(mockReportsResponse);
      })
    );

    const { result } = renderHook(() => useModuleReports(7), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.module_id).toBe(7);
    expect(result.current.data?.runs.map((r) => r.stamp)).toEqual([
      '2026-07-18T07-00-00Z',
      '2026-07-18T06-00-00Z',
    ]);
    expect(result.current.data?.runs[0].files?.[0].kind).toBe('md');
    expect(result.current.data?.runs[0].files?.[1].kind).toBe('json');
  });

  it('does not fetch when disabled', async () => {
    const spy = vi.fn();
    server.use(
      http.get('*/api/project-modules/:moduleId/reports', () => {
        spy();
        return HttpResponse.json(mockReportsResponse);
      })
    );

    const { result } = renderHook(() => useModuleReports(7, { enabled: false }), {
      wrapper: createWrapper(),
    });

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(spy).not.toHaveBeenCalled();
    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useModuleReportContent', () => {
  it('reads a selected file and passes the path query param', async () => {
    let capturedPath: string | null = null;
    server.use(
      http.get('*/api/project-modules/:moduleId/reports/content', ({ request, params }) => {
        expect(params.moduleId).toBe('7');
        capturedPath = new URL(request.url).searchParams.get('path');
        return HttpResponse.json({
          path: '2026-07-18T07-00-00Z/run-poc.md',
          kind: 'md',
          size: 1200,
          content: '# Run report\nPASSED',
        });
      })
    );

    const { result } = renderHook(
      () => useModuleReportContent(7, '2026-07-18T07-00-00Z/run-poc.md'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(capturedPath).toBe('2026-07-18T07-00-00Z/run-poc.md');
    expect(result.current.data?.kind).toBe('md');
    expect(result.current.data?.content).toContain('PASSED');
  });

  it('does not fetch when no file is selected (path null)', async () => {
    const spy = vi.fn();
    server.use(
      http.get('*/api/project-modules/:moduleId/reports/content', () => {
        spy();
        return HttpResponse.json({ path: '', kind: 'other', size: 0, content: '' });
      })
    );

    const { result } = renderHook(() => useModuleReportContent(7, null), {
      wrapper: createWrapper(),
    });

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(spy).not.toHaveBeenCalled();
    expect(result.current.fetchStatus).toBe('idle');
  });
});
