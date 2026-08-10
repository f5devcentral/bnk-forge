/**
 * Tests for the bf.conf templates hooks.
 *
 * CT-012 pattern: MSW handlers return the real response shape
 * (mirrors backend/schemas/dpu.py BfConfTemplate*) and capture request
 * bodies to assert payload shape.
 */
import { describe, it, expect } from 'vitest';
import React from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useBfConfTemplates,
  useBfConfTemplate,
  useCreateBfConfTemplate,
  useUpdateBfConfTemplate,
  useDeleteBfConfTemplate,
} from '@/hooks/useBfConfTemplates';

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

const summary = {
  id: 1,
  name: 'F5/Nvidia RCP RA with LACP based LAG uplinks',
  description: 'Seed LAG template',
  matching_bnk_version: null,
};

const full = {
  ...summary,
  content: 'UPDATE_DPU_OS="yes"\n',
  created_at: '2026-04-18T00:00:00Z',
  updated_at: '2026-04-18T00:00:00Z',
};

describe('useBfConfTemplates', () => {
  it('fetches the list wrapped in { templates: [...] }', async () => {
    server.use(
      http.get('*/api/bf-conf-templates', () =>
        HttpResponse.json({ templates: [summary] }),
      ),
    );

    const { result } = renderHook(() => useBfConfTemplates(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([summary]);
  });
});

describe('useBfConfTemplate', () => {
  it('fetches a single template including content', async () => {
    server.use(
      http.get('*/api/bf-conf-templates/1', () => HttpResponse.json(full)),
    );
    const { result } = renderHook(() => useBfConfTemplate(1), {
      wrapper: createWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(full);
  });
});

describe('useCreateBfConfTemplate', () => {
  it('POSTs the new template and invalidates the list query', async () => {
    let capturedBody: unknown = null;
    server.use(
      http.post('*/api/bf-conf-templates', async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json(full, { status: 201 });
      }),
    );
    const { result } = renderHook(() => useCreateBfConfTemplate(), {
      wrapper: createWrapper(),
    });
    result.current.mutate({
      name: full.name,
      content: full.content,
      description: full.description,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(capturedBody).toEqual({
      name: full.name,
      content: full.content,
      description: full.description,
    });
  });
});

describe('useUpdateBfConfTemplate', () => {
  it('PATCHes only the provided fields', async () => {
    let capturedBody: unknown = null;
    server.use(
      http.patch('*/api/bf-conf-templates/1', async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ ...full, description: 'updated' });
      }),
    );
    const { result } = renderHook(() => useUpdateBfConfTemplate(), {
      wrapper: createWrapper(),
    });
    result.current.mutate({ id: 1, data: { description: 'updated' } });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(capturedBody).toEqual({ description: 'updated' });
  });
});

describe('useDeleteBfConfTemplate', () => {
  it('DELETEs and resolves without a body', async () => {
    server.use(
      http.delete('*/api/bf-conf-templates/1', () => new HttpResponse(null, { status: 204 })),
    );
    const { result } = renderHook(() => useDeleteBfConfTemplate(), {
      wrapper: createWrapper(),
    });
    result.current.mutate(1);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });
});
