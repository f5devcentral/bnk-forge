/**
 * Tests for useDeployments hooks
 *
 * Covers: fetching all modules, recent deployments filtering/sorting,
 * deployment stats aggregation, loading states, and error handling.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { useDeployments, useRecentDeployments, useDeploymentStats } from '@/hooks/useDeployments';
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

// Mock data for GET /api/projects/modules/all (getAllModules)
const mockAllModules = [
  {
    id: 1,
    module_name: 'vpc',
    project_id: 1,
    project_name: 'test-project',
    status: 'applied',
    last_deployed_at: '2026-02-20T12:00:00Z',
    path_in_project: 'modules/vpc',
    deployment_error: null,
    library_module: { id: 1, name: 'vpc', category: 'network', path: 'modules/vpc' },
  },
  {
    id: 2,
    module_name: 'eks',
    project_id: 1,
    project_name: 'test-project',
    status: 'applied',
    last_deployed_at: '2026-02-18T10:00:00Z',
    path_in_project: 'modules/eks',
    deployment_error: null,
    library_module: { id: 2, name: 'eks', category: 'kubernetes', path: 'modules/eks' },
  },
  {
    id: 3,
    module_name: 'security',
    project_id: 2,
    project_name: 'production-infra',
    status: 'not_initialized',
    last_deployed_at: null,
    path_in_project: 'modules/security',
    deployment_error: null,
    library_module: { id: 3, name: 'security', category: 'security', path: 'modules/security' },
  },
];

beforeEach(() => {
  // Override the handler for getAllModules — the actual endpoint is
  // GET /api/projects/modules/all?page_size=200 and returns { items, pagination }
  server.use(
    http.get('*/api/projects/modules/all', () => {
      return HttpResponse.json({
        items: mockAllModules,
        pagination: {
          page: 1,
          page_size: 200,
          total_items: mockAllModules.length,
          total_pages: 1,
          has_next: false,
          has_prev: false,
        },
      });
    })
  );
});

// ============================================================================
// useDeployments
// ============================================================================

describe('useDeployments', () => {
  it('fetches all modules', async () => {
    const { result } = renderHook(() => useDeployments(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toHaveLength(3);
    expect(result.current.data![0].module_name).toBe('vpc');
    expect(result.current.data![1].module_name).toBe('eks');
    expect(result.current.data![2].module_name).toBe('security');
  });

  it('returns loading state initially', () => {
    const { result } = renderHook(() => useDeployments(), { wrapper: createWrapper() });

    expect(result.current.isLoading).toBe(true);
    expect(result.current.data).toBeUndefined();
  });
});

// ============================================================================
// useRecentDeployments
// ============================================================================

describe('useRecentDeployments', () => {
  it('returns only deployed modules sorted by date (most recent first)', async () => {
    const { result } = renderHook(() => useRecentDeployments(10), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Module 3 has last_deployed_at: null, so it should be filtered out
    expect(result.current.data).toHaveLength(2);
    // Most recent first: vpc (Feb 20) before eks (Feb 18)
    expect(result.current.data![0].module_name).toBe('vpc');
    expect(result.current.data![1].module_name).toBe('eks');
  });

  it('respects limit parameter', async () => {
    const { result } = renderHook(() => useRecentDeployments(1), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toHaveLength(1);
    expect(result.current.data![0].module_name).toBe('vpc');
  });

  it('filters out modules without last_deployed_at', async () => {
    const { result } = renderHook(() => useRecentDeployments(10), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    const hasNullDates = result.current.data!.some((m) => m.last_deployed_at === null);
    expect(hasNullDates).toBe(false);

    // The 'security' module (id: 3) with null last_deployed_at should not appear
    const moduleNames = result.current.data!.map((m) => m.module_name);
    expect(moduleNames).not.toContain('security');
  });
});

// ============================================================================
// useDeploymentStats
// ============================================================================

describe('useDeploymentStats', () => {
  it('aggregates stats from all projects', async () => {
    // The default handler for GET /api/projects already returns mockProjects
    // with module_count, deployed_count, failed_count fields.
    // mockProjects[0]: module_count=2, deployed_count=1, failed_count=0
    // mockProjects[1]: module_count=5, deployed_count=5, failed_count=0
    // mockProjects[2]: module_count=3, deployed_count=3, failed_count=0
    const { result } = renderHook(() => useDeploymentStats(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toEqual({
      totalProjects: 3,
      activeModules: 10,     // 2 + 5 + 3
      deployedModules: 9,    // 1 + 5 + 3
      failedModules: 0,      // 0 + 0 + 0
    });
  });

  it('handles error state', async () => {
    server.use(
      http.get('*/api/projects', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Database unavailable' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useDeploymentStats(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
    expect(result.current.data).toBeUndefined();
  });
});
