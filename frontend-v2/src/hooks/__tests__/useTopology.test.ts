/**
 * Tests for useTopology hook — CT-012 pattern.
 *
 * Backend contract:
 *   GET /api/k8s/clusters/{cluster_id}/topology?namespace=<ns>
 *   → TopologyGraphResponse
 *   (backend/routes/k8s/topology.py, schema: backend/schemas/k8s.py)
 *
 * TopologyGraphResponse: { nodes: TopologyNode[], edges: TopologyEdge[], cluster_id, namespace, info? }
 * TopologyNode: { id, kind, name, namespace, uid?, severity, meta? }
 * TopologyEdge: { id, source, target, kind }
 *
 * Hook is gated on useClusterReachable + a non-"all" namespace.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { useTopology } from '@/hooks/useTopology';
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

/** Minimal valid TopologyNode shape (real backend schema). */
function makeNode(overrides: Partial<{
  id: string;
  kind: string;
  name: string;
  namespace: string;
  uid: string | null;
  severity: string;
  meta: Record<string, unknown>;
}> = {}) {
  return {
    id: 'Pod/default/my-pod',
    kind: 'Pod',
    name: 'my-pod',
    namespace: 'default',
    uid: 'abc-123',
    severity: 'healthy',
    meta: { phase: 'Running', container_count: 1 },
    ...overrides,
  };
}

/** Minimal valid TopologyEdge shape. */
function makeEdge(overrides: Partial<{
  id: string;
  source: string;
  target: string;
  kind: string;
}> = {}) {
  return {
    id: 'selects/Service/default/svc-1/Pod/default/my-pod',
    source: 'Service/default/svc-1',
    target: 'Pod/default/my-pod',
    kind: 'selects',
    ...overrides,
  };
}

/** Minimal valid TopologyGraphResponse. */
function makeResponse(nodes = [makeNode()], edges = [makeEdge()]) {
  return {
    nodes,
    edges,
    cluster_id: 1,
    namespace: 'default',
    info: null,
  };
}

beforeEach(() => {
  server.use(
    http.get('*/api/k8s/clusters/:clusterId/topology', () => {
      return HttpResponse.json(makeResponse());
    })
  );
});

describe('useTopology', () => {
  it('fetches and returns TopologyGraphResponse for a cluster + namespace', async () => {
    const svcNode = makeNode({ id: 'Service/default/svc-1', kind: 'Service', name: 'svc-1', severity: 'unknown' });
    const podNode = makeNode();
    const edge = makeEdge();

    server.use(
      http.get('*/api/k8s/clusters/1/topology', () =>
        HttpResponse.json(makeResponse([svcNode, podNode], [edge]))
      )
    );

    const { result } = renderHook(() => useTopology(1, 'default'), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.cluster_id).toBe(1);
    expect(result.current.data?.namespace).toBe('default');
    expect(result.current.data?.nodes).toHaveLength(2);
    expect(result.current.data?.edges).toHaveLength(1);

    const svc = result.current.data!.nodes.find((n) => n.kind === 'Service');
    expect(svc?.severity).toBe('unknown');
    expect(svc?.name).toBe('svc-1');

    const pod = result.current.data!.nodes.find((n) => n.kind === 'Pod');
    expect(pod?.severity).toBe('healthy');
    expect(pod?.meta?.phase).toBe('Running');

    expect(result.current.data!.edges[0].kind).toBe('selects');
  });

  it('exposes info when backend returns a truncation warning', async () => {
    server.use(
      http.get('*/api/k8s/clusters/1/topology', () =>
        HttpResponse.json({
          ...makeResponse(),
          info: 'Namespace contains 305 pods — graph truncated to the first 300',
        })
      )
    );

    const { result } = renderHook(() => useTopology(1, 'default'), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.info).toContain('truncated');
  });

  it('is disabled when clusterId is 0', async () => {
    const { result } = renderHook(() => useTopology(0, 'default'), { wrapper: createWrapper() });

    expect(result.current.isPending).toBe(true);
    expect(result.current.isFetching).toBe(false);
  });

  it('is disabled when namespace is "all"', async () => {
    const { result } = renderHook(() => useTopology(1, 'all'), { wrapper: createWrapper() });

    expect(result.current.isPending).toBe(true);
    expect(result.current.isFetching).toBe(false);
  });

  it('is disabled when namespace is empty string', async () => {
    const { result } = renderHook(() => useTopology(1, ''), { wrapper: createWrapper() });

    expect(result.current.isPending).toBe(true);
    expect(result.current.isFetching).toBe(false);
  });

  it('reports error when the API returns 503', async () => {
    server.use(
      http.get('*/api/k8s/clusters/1/topology', () =>
        HttpResponse.json(
          { detail: 'Cluster unreachable', code: 'NETWORK_UNREACHABLE' },
          { status: 503 }
        )
      )
    );

    const { result } = renderHook(() => useTopology(1, 'default'), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 5000 });
  });

  it('handles "owns" edge kind for workload→pod edges', async () => {
    const depNode = makeNode({ id: 'Deployment/default/my-dep', kind: 'Deployment', name: 'my-dep' });
    const podNode = makeNode();
    const ownsEdge = makeEdge({ id: 'owns/dep/pod', source: depNode.id, target: podNode.id, kind: 'owns' });

    server.use(
      http.get('*/api/k8s/clusters/1/topology', () =>
        HttpResponse.json(makeResponse([depNode, podNode], [ownsEdge]))
      )
    );

    const { result } = renderHook(() => useTopology(1, 'default'), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const edge = result.current.data!.edges[0];
    expect(edge.kind).toBe('owns');
    expect(edge.source).toBe('Deployment/default/my-dep');
  });
});
