/**
 * Tests for useRegistry hooks
 *
 * Covers: useRegistrySearch (search query), useRegistryModule (detail query
 * with enabled guard when params are missing), useImportRegistryModule
 * mutation, and error handling.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useRegistrySearch,
  useRegistryModule,
  useImportRegistryModule,
} from '@/hooks/useRegistry';
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

beforeEach(() => {
  // Add handlers not in the default set
  server.use(
    http.get('*/api/registry/modules/:namespace/:name/:provider', ({ params }) => {
      return HttpResponse.json({
        namespace: params.namespace,
        name: params.name,
        provider: params.provider,
        version: '0.1.0',
      });
    }),

    http.post('*/api/registry/import', () => {
      return HttpResponse.json({ success: true, module_id: 1 });
    })
  );
});

// ============================================================================
// useRegistrySearch
// ============================================================================

describe('useRegistrySearch', () => {
  it('searches the registry and returns results', async () => {
    const { result } = renderHook(() => useRegistrySearch('consul'), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
    expect(result.current.data).toEqual({ modules: [], total: 0 });
  });
});

// ============================================================================
// useRegistryModule
// ============================================================================

describe('useRegistryModule', () => {
  it('fetches module detail when all params are present', async () => {
    const { result } = renderHook(
      () => useRegistryModule('hashicorp', 'consul', 'aws'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
    expect(result.current.data!.namespace).toBe('hashicorp');
    expect(result.current.data!.name).toBe('consul');
    expect(result.current.data!.provider).toBe('aws');
    expect(result.current.data!.version).toBe('0.1.0');
  });

  it('is disabled when any param is null', () => {
    const { result } = renderHook(
      () => useRegistryModule(null, 'consul', 'aws'),
      { wrapper: createWrapper() }
    );

    expect(result.current.isLoading).toBe(false);
    expect(result.current.fetchStatus).toBe('idle');
    expect(result.current.data).toBeUndefined();
  });

  it('is disabled when all params are null', () => {
    const { result } = renderHook(
      () => useRegistryModule(null, null, null),
      { wrapper: createWrapper() }
    );

    expect(result.current.fetchStatus).toBe('idle');
    expect(result.current.data).toBeUndefined();
  });
});

// ============================================================================
// useImportRegistryModule
// ============================================================================

describe('useImportRegistryModule', () => {
  it('imports a registry module', async () => {
    const { result } = renderHook(() => useImportRegistryModule(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      namespace: 'hashicorp',
      name: 'consul',
      provider: 'aws',
      version: '0.1.0',
    } as Parameters<typeof result.current.mutate>[0]);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
    expect(result.current.data.success).toBe(true);
  });

  it('sends correct payload shape matching RegistryModuleImport', async () => {
    // Backend: RegistryModuleImport { namespace, name, provider, version, use_terraform_registry }
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/registry/import', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, message: 'Imported', module: { id: 42, name: 'consul' } });
      })
    );

    const { result } = renderHook(() => useImportRegistryModule(), { wrapper: createWrapper() });

    result.current.mutate({
      namespace: 'hashicorp',
      name: 'consul',
      provider: 'aws',
      version: '0.3.0',
      use_terraform_registry: true,
    } as Parameters<typeof result.current.mutate>[0]);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      namespace: 'hashicorp',
      name: 'consul',
      provider: 'aws',
      version: '0.3.0',
      use_terraform_registry: true,
    });
    expect(capturedBody).not.toHaveProperty('module');
    expect(capturedBody).not.toHaveProperty('data');
  });
});

// ============================================================================
// Error handling
// ============================================================================

describe('error handling', () => {
  it('surfaces server errors on registry search', async () => {
    server.use(
      http.get('*/api/registry/search', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Registry unavailable' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useRegistrySearch('consul'), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
    expect(result.current.data).toBeUndefined();
  });
});
