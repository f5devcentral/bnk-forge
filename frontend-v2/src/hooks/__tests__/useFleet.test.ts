/**
 * Contract tests for useFleet hooks.
 *
 * Verifies request payload shape for compare mutation and response shape
 * compatibility with backend fleet contracts.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { useFleetHealth, useFleetCompare, fleetKeys } from '@/hooks/useFleet';
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

// Backend contract: FleetHealthResponse (routes/operators/__init__.py)
const mockFleetHealth = {
  total_clusters: 2,
  healthy: 1,
  warning: 1,
  critical: 0,
  offline: 0,
  operators: [
    {
      operator_id: 1,
      operator_uuid: 'cluster-1',
      cluster_id: 1,
      cluster_name: 'prod-cluster',
      status: 'healthy',
      last_seen: '2026-02-18T12:00:00Z',
      bnk_version: '2.5.0',
      route_count: 10,
      tmm_count: 2,
      gateway_count: 2,
      uptime: '1h',
      health_summary: { healthy: 4, warning: 0, critical: 0 },
      kubernetes_version: 'v1.28.0',
      node_count: 3,
      operator_version: null,
      connectivity_mode: 'kubeconfig',
      detected_platform_profile: 'eks',
      detected_platform_provider: 'aws',
      cloud_provider: 'aws',
      region: 'us-east-1',
      account_id: '123456789012',
      discovery_status: 'discovered',
    },
    {
      operator_id: 2,
      operator_uuid: 'cluster-2',
      cluster_id: 2,
      cluster_name: 'staging-cluster',
      status: 'warning',
      last_seen: '2026-02-18T12:00:00Z',
      bnk_version: '2.4.0',
      route_count: 8,
      tmm_count: 1,
      gateway_count: 1,
      uptime: '45m',
      health_summary: { healthy: 2, warning: 1, critical: 0 },
      kubernetes_version: 'v1.27.0',
      node_count: 2,
      operator_version: null,
      connectivity_mode: 'kubeconfig',
      detected_platform_profile: 'generic_onprem',
      detected_platform_provider: 'baremetal',
      cloud_provider: null,
      region: null,
      account_id: null,
      discovery_status: null,
    },
  ],
  platform_context: {
    mixed_platform_profiles: true,
    detected_profiles: ['eks', 'generic_onprem'],
    clusters: [
      {
        cluster_id: 1,
        cluster_name: 'prod-cluster',
        detected_platform_profile: 'eks',
        detected_platform_provider: 'aws',
      },
      {
        cluster_id: 2,
        cluster_name: 'staging-cluster',
        detected_platform_profile: 'generic_onprem',
        detected_platform_provider: 'baremetal',
      },
    ],
    comparison_caveats: ['Fleet contains mixed detected platform profiles.'],
    support_semantics: ['Support semantics are platform-aware.'],
  },
};

// Backend contract: FleetCompareResponse (routes/operators/__init__.py)
const mockFleetCompareResult = {
  operator_a: 'prod-cluster',
  operator_b: 'staging-cluster',
  comparison_mode: 'health_report',
  total_diffs: 1,
  summary: '1 difference(s) between prod-cluster and staging-cluster',
  differences: [
    {
      field: 'Route Count',
      key: 'routes_count',
      value_a: 10,
      value_b: 8,
    },
  ],
  platform_context: {
    cluster_a: {
      name: 'prod-cluster',
      detected_platform_profile: 'eks',
      detected_platform_provider: 'aws',
      platform_capabilities: { cloud_load_balancer: true },
      platform_constraints: { managed_control_plane: true },
    },
    cluster_b: {
      name: 'staging-cluster',
      detected_platform_profile: 'generic_onprem',
      detected_platform_provider: 'baremetal',
      platform_capabilities: { network_attachment_definitions: true },
      platform_constraints: { cluster_lifecycle_external: true },
    },
    mixed_platform_profiles: true,
    comparison_caveats: ['Compared clusters use different detected platform profiles.'],
    support_semantics: ['Runtime support semantics follow detected context.'],
  },
};

describe('fleetKeys', () => {
  it('generates correct query key for health', () => {
    expect(fleetKeys.health()).toEqual(['fleet', 'health']);
  });

  it('generates correct query key for compare', () => {
    expect(fleetKeys.compare(1, 2)).toEqual(['fleet', 'compare', 1, 2]);
  });

  it('all keys start with fleet', () => {
    expect(fleetKeys.all).toEqual(['fleet']);
  });
});

describe('useFleetHealth', () => {
  it('fetches fleet health with backend contract fields', async () => {
    server.use(
      http.get('*/api/operators/fleet-health', () => HttpResponse.json(mockFleetHealth))
    );

    const { result } = renderHook(() => useFleetHealth(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.total_clusters).toBe(2);
    expect(result.current.data?.healthy).toBe(1);
    expect(result.current.data?.warning).toBe(1);
    expect(result.current.data?.operators).toHaveLength(2);

    const first = result.current.data?.operators[0];
    expect(first?.cluster_name).toBe('prod-cluster');
    expect(first?.status).toBe('healthy');
    expect(first?.route_count).toBe(10);
    expect(first?.node_count).toBe(3);
    expect(first?.detected_platform_profile).toBe('eks');
    expect(result.current.data?.platform_context?.mixed_platform_profiles).toBe(true);
  });
});

describe('useFleetCompare', () => {
  it('posts expected request schema and returns backend response contract', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/operators/fleet/compare', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(mockFleetCompareResult);
      })
    );

    const { result } = renderHook(() => useFleetCompare(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ operatorAId: 1, operatorBId: 2 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Request payload contract (backend FleetCompareRequest)
    expect(capturedBody).toMatchObject({
      operator_a_id: 1,
      operator_b_id: 2,
    });

    // Response contract
    expect(result.current.data?.comparison_mode).toBe('health_report');
    expect(result.current.data?.total_diffs).toBe(1);
    expect(result.current.data?.differences).toHaveLength(1);
    expect(result.current.data?.differences[0].key).toBe('routes_count');
  });

  it('accepts cluster-config comparison responses', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/operators/fleet/compare', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          operator_a: 'mgx1',
          operator_b: 'mgx3',
          comparison_mode: 'cluster_config',
          total_diffs: 1,
          summary: '1 difference',
          cluster_a: 'mgx1',
          cluster_b: 'mgx3',
          resources: {
            only_in_a: [{ kind: 'Gateway', name: 'gw-a', namespace: 'default' }],
            only_in_b: [],
            changed: [],
          },
          modules: {
            only_in_a: [],
            only_in_b: [],
            changed: [],
          },
          platform_context: {
            cluster_a: {
              name: 'mgx1',
              detected_platform_profile: 'eks',
              detected_platform_provider: 'aws',
              platform_capabilities: { cloud_load_balancer: true },
              platform_constraints: { managed_control_plane: true },
            },
            cluster_b: {
              name: 'mgx3',
              detected_platform_profile: 'ocp',
              detected_platform_provider: null,
              platform_capabilities: { openshift_routes: true },
              platform_constraints: { operator_required_for_networking: true },
            },
            mixed_platform_profiles: true,
            comparison_caveats: ['Compared clusters use different detected platform profiles.'],
            support_semantics: ['Runtime support semantics follow detected context.'],
          },
        });
      })
    );

    const { result } = renderHook(() => useFleetCompare(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ operatorAId: 1, operatorBId: 3 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      operator_a_id: 1,
      operator_b_id: 3,
    });
    expect(result.current.data?.comparison_mode).toBe('cluster_config');
    if (result.current.data && 'resources' in result.current.data) {
      expect(result.current.data.resources.only_in_a[0]).toMatchObject({
        kind: 'Gateway',
        name: 'gw-a',
        namespace: 'default',
      });
      expect(result.current.data.platform_context?.mixed_platform_profiles).toBe(true);
    }
  });
});
