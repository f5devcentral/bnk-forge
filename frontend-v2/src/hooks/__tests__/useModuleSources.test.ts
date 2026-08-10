/**
 * Tests for useModuleSources hooks
 *
 * Covers: useModuleSources (list query), useModuleSource (single detail query
 * with enabled guard), useCreateModuleSource / useDeleteModuleSource /
 * useSyncModuleSource mutations, cache invalidation, and error handling.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useModuleSources,
  useModuleSource,
  useCreateModuleSource,
  useUpdateModuleSource,
  useDeleteModuleSource,
  useSyncModuleSource,
} from '@/hooks/useModuleSources';
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

const mockSingleSource = {
  id: 1,
  name: 'github-org',
  source_type: 'github',
  url: 'https://github.com/org',
  is_active: true,
};

beforeEach(() => {
  // Add single-source handler (not in default handlers)
  server.use(
    http.get('*/api/module-sources/:id', ({ params }) => {
      if (Number(params.id) === 1) {
        return HttpResponse.json(mockSingleSource);
      }
      return new HttpResponse(null, { status: 404 });
    })
  );
});

// ============================================================================
// useModuleSources
// ============================================================================

describe('useModuleSources', () => {
  it('fetches the module sources list', async () => {
    const { result } = renderHook(() => useModuleSources(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
    expect(Array.isArray(result.current.data)).toBe(true);
    expect(result.current.data![0].name).toBe('Official BNK Modules');
  });
});

// ============================================================================
// useModuleSource
// ============================================================================

describe('useModuleSource', () => {
  it('fetches a single module source by id', async () => {
    const { result } = renderHook(() => useModuleSource(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toEqual(mockSingleSource);
    expect(result.current.data!.name).toBe('github-org');
    expect(result.current.data!.source_type).toBe('github');
  });

  it('is disabled when sourceId is 0 (falsy)', () => {
    const { result } = renderHook(() => useModuleSource(0), {
      wrapper: createWrapper(),
    });

    // enabled: !!sourceId → false when 0, so query should not fetch
    expect(result.current.isLoading).toBe(false);
    expect(result.current.fetchStatus).toBe('idle');
    expect(result.current.data).toBeUndefined();
  });
});

// ============================================================================
// useCreateModuleSource
// ============================================================================

describe('useCreateModuleSource', () => {
  it('creates a module source', async () => {
    const { result } = renderHook(() => useCreateModuleSource(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      name: 'new-source',
      source_type: 'git',
      url: 'https://github.com/new/repo.git',
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
    expect(result.current.data.name).toBe('new-source');
    expect(result.current.data.is_active).toBe(true);
  });

  it('invalidates module and blueprint queries after create', async () => {
    const invalidateSpy = vi
      .spyOn(QueryClient.prototype, 'invalidateQueries')
      .mockResolvedValue(undefined as never);

    const { result } = renderHook(() => useCreateModuleSource(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      name: 'new-source',
      source_type: 'git',
      url: 'https://github.com/new/repo.git',
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(invalidateSpy).toHaveBeenCalled();
    invalidateSpy.mockRestore();
  });

  it('sends correct payload shape matching ModuleSourceCreate', async () => {
    // Backend: ModuleSourceCreate { name, source_type, url, branch, git_ref, auth_type, auth_token, auto_sync, sync_interval_hours, description }
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/module-sources', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 5, ...capturedBody, is_active: true });
      })
    );

    const { result } = renderHook(() => useCreateModuleSource(), { wrapper: createWrapper() });

    result.current.mutate({
      name: 'custom-modules',
      source_type: 'git',
      url: 'https://github.com/org/modules.git',
      branch: 'main',
      auth_type: 'token',
      auth_token: 'ghp_abc123',
      auto_sync: true,
      sync_interval_hours: 12,
      description: 'Custom module source',
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      name: 'custom-modules',
      source_type: 'git',
      url: 'https://github.com/org/modules.git',
      branch: 'main',
      auth_type: 'token',
      auto_sync: true,
      sync_interval_hours: 12,
    });
    expect(capturedBody).not.toHaveProperty('source');
    expect(capturedBody).not.toHaveProperty('data');
  });
});

// ============================================================================
// CT-020: Payload Shape Assertions — useUpdateModuleSource
// Backend: ModuleSourceUpdate { name, url, branch, git_ref, auth_type, auth_token, auto_sync, sync_interval_hours, is_active, description }
// ============================================================================

describe('useUpdateModuleSource', () => {
  it('sends correct payload shape matching ModuleSourceUpdate', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/module-sources/:sourceId', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 1, ...capturedBody });
      })
    );

    const { result } = renderHook(() => useUpdateModuleSource(), { wrapper: createWrapper() });

    result.current.mutate({
      sourceId: 1,
      data: {
        name: 'updated-source',
        branch: 'develop',
        auto_sync: false,
        is_active: false,
      },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      name: 'updated-source',
      branch: 'develop',
      auto_sync: false,
      is_active: false,
    });
    // source_type is NOT updatable
    expect(capturedBody).not.toHaveProperty('source_type');
    expect(capturedBody).not.toHaveProperty('source_id');
  });
});

// ============================================================================
// useDeleteModuleSource
// ============================================================================

describe('useDeleteModuleSource', () => {
  it('deletes a module source', async () => {
    const { result } = renderHook(() => useDeleteModuleSource(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toEqual({ success: true });
  });
});

// ============================================================================
// useSyncModuleSource
// ============================================================================

describe('useSyncModuleSource', () => {
  it('syncs a module source and returns results', async () => {
    const syncResult = {
      success: true,
      results: { modules_created: 3, modules_updated: 2, errors: [] },
    };

    server.use(
      http.post('*/api/module-sources/:id/sync', () => {
        return HttpResponse.json(syncResult);
      })
    );

    const { result } = renderHook(() => useSyncModuleSource(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toEqual(syncResult);
    expect(result.current.data.results.modules_created).toBe(3);
    expect(result.current.data.results.modules_updated).toBe(2);
    expect(result.current.data.results.errors).toHaveLength(0);
  });

  it('invalidates module source and module library lists after sync', async () => {
    const syncResult = {
      success: true,
      results: { modules_created: 1, modules_updated: 0, errors: [] },
    };

    server.use(
      http.post('*/api/module-sources/:id/sync', () => {
        return HttpResponse.json(syncResult);
      })
    );

    const invalidateSpy = vi
      .spyOn(QueryClient.prototype, 'invalidateQueries')
      .mockResolvedValue(undefined as never);

    const { result } = renderHook(() => useSyncModuleSource(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(invalidateSpy).toHaveBeenCalled();
    invalidateSpy.mockRestore();
  });
});

// ============================================================================
// Error handling
// ============================================================================

describe('error handling', () => {
  it('surfaces server errors on fetch', async () => {
    server.use(
      http.get('*/api/module-sources', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Database error' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useModuleSources(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
    expect(result.current.data).toBeUndefined();
  });
});
