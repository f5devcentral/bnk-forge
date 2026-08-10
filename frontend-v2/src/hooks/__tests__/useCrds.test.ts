/**
 * Tests for useCrds hook — CT-012 pattern.
 *
 * Backend contract:
 *   GET /api/k8s/clusters/{cluster_id}/crds → CrdListEnvelope
 *   (backend/routes/k8s/crds.py, schema: backend/schemas/k8s.py CrdListEnvelope + CRDInfo)
 *
 * CrdListEnvelope: { crds: CRDInfo[], count: number, cluster_id: number,
 *                    group_filter?: string[] | null, info?: string | null }
 * CRDInfo: { name, kind, plural, group, version?, namespaced, display_name?, category?, source }
 *
 * useCrds is gated on useClusterReachable. In tests, mock useConnectivity to return reachable=true.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { useCrds } from '@/hooks/useCrds';
import React from 'react';

// Make the cluster appear reachable so the query is not disabled.
vi.mock('@/hooks/useConnectivity', () => ({
  useClusterReachable: () => true,
  useConnectivity: () => ({ states: {}, retry: vi.fn() }),
  useConnectivitySession: () => undefined,
}));

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
    },
  });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

/** Minimal valid CRDInfo shape (real backend schema fields). */
function makeCrd(overrides: Partial<{
  name: string;
  kind: string;
  plural: string;
  group: string;
  version: string | null;
  namespaced: boolean;
  display_name: string | null;
  category: string | null;
  source: string;
}> = {}) {
  return {
    name: 'widgets.example.com',
    kind: 'Widget',
    plural: 'widgets',
    group: 'example.com',
    version: 'v1',
    namespaced: true,
    display_name: null,
    category: null,
    source: 'discovered',
    ...overrides,
  };
}

/** Minimal valid CrdListEnvelope. */
function makeEnvelope(crds: ReturnType<typeof makeCrd>[], extra?: { info?: string }) {
  return {
    crds,
    count: crds.length,
    cluster_id: 1,
    group_filter: null,
    info: extra?.info ?? null,
  };
}

beforeEach(() => {
  server.use(
    http.get('*/api/k8s/clusters/:clusterId/crds', () => {
      return HttpResponse.json(makeEnvelope([makeCrd()]));
    })
  );
});

describe('useCrds', () => {
  it('fetches and returns CrdListEnvelope for a cluster', async () => {
    const envelope = makeEnvelope([
      makeCrd({ name: 'foos.example.com', kind: 'Foo', source: 'registry-enriched', category: 'Traffic' }),
      makeCrd({ name: 'bars.other.io', kind: 'Bar' }),
    ]);

    server.use(
      http.get('*/api/k8s/clusters/1/crds', () => HttpResponse.json(envelope))
    );

    const { result } = renderHook(() => useCrds(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.count).toBe(2);
    expect(result.current.data?.cluster_id).toBe(1);
    expect(result.current.data?.crds).toHaveLength(2);

    const [foo, bar] = result.current.data!.crds;
    expect(foo.name).toBe('foos.example.com');
    expect(foo.source).toBe('registry-enriched');
    expect(foo.category).toBe('Traffic');
    expect(bar.name).toBe('bars.other.io');
    expect(bar.source).toBe('discovered');
  });

  it('exposes info field when backend returns an info message (reachable, no CRDs)', async () => {
    server.use(
      http.get('*/api/k8s/clusters/1/crds', () =>
        HttpResponse.json(makeEnvelope([], { info: 'No CRDs installed on this cluster' }))
      )
    );

    const { result } = renderHook(() => useCrds(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.crds).toHaveLength(0);
    expect(result.current.data?.info).toBe('No CRDs installed on this cluster');
  });

  it('reports error when the API returns 500', async () => {
    server.use(
      http.get('*/api/k8s/clusters/1/crds', () =>
        HttpResponse.json({ detail: 'Internal server error' }, { status: 500 })
      )
    );

    const { result } = renderHook(() => useCrds(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 5000 });
  });

  it('is disabled when clusterId is 0 (no cluster selected)', async () => {
    const { result } = renderHook(() => useCrds(0), { wrapper: createWrapper() });

    // Query should not fire — status stays pending/idle
    expect(result.current.isPending).toBe(true);
    expect(result.current.isFetching).toBe(false);
  });

  it('forwards the group option as repeated ?group= query params', async () => {
    let capturedUrl: URL | undefined;
    server.use(
      http.get('*/api/k8s/clusters/1/crds', ({ request }) => {
        capturedUrl = new URL(request.url);
        return HttpResponse.json(makeEnvelope([makeCrd()]));
      })
    );

    const { result } = renderHook(
      () => useCrds(1, { group: ['gateway.networking.k8s.io', 'k8s.f5net.com'] }),
      { wrapper: createWrapper() }
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(capturedUrl?.searchParams.getAll('group')).toEqual([
      'gateway.networking.k8s.io',
      'k8s.f5net.com',
    ]);
  });

  it('preserves CRDInfo fields namespaced + version correctly', async () => {
    server.use(
      http.get('*/api/k8s/clusters/1/crds', () =>
        HttpResponse.json(makeEnvelope([
          makeCrd({ namespaced: false, version: null }),
        ]))
      )
    );

    const { result } = renderHook(() => useCrds(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const crd = result.current.data!.crds[0];
    expect(crd.namespaced).toBe(false);
    expect(crd.version).toBeNull();
  });
});
