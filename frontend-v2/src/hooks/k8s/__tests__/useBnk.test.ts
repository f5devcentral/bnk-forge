/**
 * Tests for useBnk hooks
 *
 * Covers: unified BNK data, health/topology/policy selectors,
 * upgrade workflow (versions, current, history, detail),
 * and mutations (create plan, execute, rollback, cancel).
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useBnkData,
  useF5BNKHealth,
  useF5GatewayTopology,
  useF5PolicyGatewayAssociations,
  useBnkUpgradeVersions,
  useBnkCurrentVersion,
  useBnkUpgradeHistory,
  useBnkUpgradeDetail,
  useCreateBnkUpgradePlan,
  useExecuteBnkUpgrade,
  useRollbackBnkUpgrade,
  useCancelBnkUpgrade,
} from '../useBnk';
import React from 'react';

// ============================================================================
// Test Setup
// ============================================================================

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

const mockBnkData = {
  health: { status: 'healthy', components: [{ name: 'controller', status: 'running' }] },
  topology: [{ gateway: 'gw-1', routes: ['route-1'] }],
  dataPlane: [{ name: 'dp-1', status: 'ready' }],
  topologyCounts: { gateways: 1, routes: 1 },
  policyAssociations: [{ policy: 'p-1', gateways: ['gw-1'] }],
  policyCount: 1,
  trafficStats: {
    source: 'tmctl',
    podName: 'f5-tmm-abc',
    sampledAt: '2026-09-01T00:00:00Z',
    available: true,
    error: null,
    listeners: [],
    egresses: [],
    firewallRules: [],
  },
};

const mockBnkHealth = {
  overall: 'healthy',
  installShape: 'flo',
  installMethod: 'FLO deploy flow',
  connectivity: { status: 'connected', message: 'Kubernetes API is accessible', checkedAt: '2026-09-01T00:00:00Z' },
  integration: { status: 'healthy', operatorConnected: false, operatorMode: 'kubeconfig', operatorVersion: null, lastSeen: null, message: 'Cluster managed via kubeconfig' },
  platform: { severity: 'healthy' },
  dataPlane: { severity: 'healthy' },
  networking: { severity: 'healthy' },
  security: { severity: 'healthy' },
  ai: { severity: 'healthy', analyzers: 0, analyzerDetails: [] },
  counts: { tmm_containers: 1, gateways: 0, httpRoutes: 0, vlans: 0 },
  cluster_id: 1,
};

// Register BNK data handler
function setupBnkHandlers() {
  server.use(
    http.get('*/api/k8s/clusters/:clusterId/f5bnk/data', () => {
      return HttpResponse.json(mockBnkData);
    }),
    http.get('*/api/k8s/clusters/:clusterId/f5bnk/health', () => {
      return HttpResponse.json(mockBnkHealth);
    }),
    http.get('*/api/k8s/clusters/:clusterId/bnk/upgrade/versions', () => {
      return HttpResponse.json({
        versions: ['2.1.0', '2.0.1', '2.0.0'],
        current: '2.0.0',
      });
    }),
    http.get('*/api/k8s/clusters/:clusterId/bnk/upgrade/current', () => {
      return HttpResponse.json({
        version: '2.0.0',
        installed_at: '2026-01-01T00:00:00Z',
      });
    }),
    http.get('*/api/k8s/clusters/:clusterId/bnk/upgrade/history', () => {
      return HttpResponse.json({
        upgrades: [
          { id: 1, from_version: '1.9.0', to_version: '2.0.0', status: 'completed' },
        ],
        total: 1,
      });
    }),
    http.get('*/api/k8s/clusters/:clusterId/bnk/upgrade/:upgradeId', () => {
      return HttpResponse.json({
        id: 5,
        from_version: '2.0.0',
        to_version: '2.1.0',
        status: 'planned',
        steps: [],
      });
    }),
    http.post('*/api/k8s/clusters/:clusterId/bnk/upgrade/plan', () => {
      return HttpResponse.json({
        id: 10,
        from_version: '2.0.0',
        to_version: '2.1.0',
        status: 'planned',
      });
    }),
    http.post('*/api/k8s/clusters/:clusterId/bnk/upgrade/:upgradeId/execute', () => {
      return HttpResponse.json({ success: true, message: 'Upgrade started' });
    }),
    http.post('*/api/k8s/clusters/:clusterId/bnk/upgrade/:upgradeId/rollback', () => {
      return HttpResponse.json({ success: true, message: 'Rollback initiated' });
    }),
    http.post('*/api/k8s/clusters/:clusterId/bnk/upgrade/:upgradeId/cancel', () => {
      return HttpResponse.json({ id: 5, status: 'cancelled' });
    }),
  );
}

// ============================================================================
// useBnkData
// ============================================================================

describe('useBnkData', () => {
  it('fetches unified BNK data for a cluster', async () => {
    setupBnkHandlers();
    const { result } = renderHook(() => useBnkData(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      health: { status: 'healthy' },
      topology: expect.any(Array),
      policyAssociations: expect.any(Array),
    });
  });

  it('does not fetch when clusterId is 0', () => {
    const { result } = renderHook(() => useBnkData(0), { wrapper: createWrapper() });
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when enabled is false', () => {
    const { result } = renderHook(
      () => useBnkData(1, undefined, { enabled: false }),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('handles API error', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/f5bnk/data', () => {
        return HttpResponse.json({ error: { message: 'Server error' } }, { status: 500 });
      }),
    );
    const { result } = renderHook(() => useBnkData(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ============================================================================
// Convenience selectors
// ============================================================================

describe('useF5BNKHealth', () => {
  it('fetches health data directly from /f5bnk/health', async () => {
    setupBnkHandlers();
    const { result } = renderHook(() => useF5BNKHealth(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({ overall: 'healthy', cluster_id: 1 });
  });
});

describe('useF5GatewayTopology', () => {
  it('returns topology slice with counts', async () => {
    setupBnkHandlers();
    const { result } = renderHook(() => useF5GatewayTopology(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      topology: expect.any(Array),
      dataPlane: expect.any(Array),
      counts: { gateways: 1, routes: 1 },
      trafficStats: { source: 'tmctl', available: true },
      cluster_id: 1,
    });
  });
});

describe('useF5PolicyGatewayAssociations', () => {
  it('returns policy associations slice', async () => {
    setupBnkHandlers();
    const { result } = renderHook(() => useF5PolicyGatewayAssociations(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      associations: expect.any(Array),
      count: 1,
      trafficStats: { source: 'tmctl', available: true },
      cluster_id: 1,
    });
  });
});

// ============================================================================
// BNK Upgrade Workflow — Queries
// ============================================================================

describe('useBnkUpgradeVersions', () => {
  it('fetches available upgrade versions', async () => {
    setupBnkHandlers();
    const { result } = renderHook(() => useBnkUpgradeVersions(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      versions: ['2.1.0', '2.0.1', '2.0.0'],
      current: '2.0.0',
    });
  });

  it('does not fetch when clusterId is 0', () => {
    const { result } = renderHook(() => useBnkUpgradeVersions(0), { wrapper: createWrapper() });
    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useBnkCurrentVersion', () => {
  it('fetches current BNK version', async () => {
    setupBnkHandlers();
    const { result } = renderHook(() => useBnkCurrentVersion(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({ version: '2.0.0' });
  });
});

describe('useBnkUpgradeHistory', () => {
  it('fetches upgrade history', async () => {
    setupBnkHandlers();
    const { result } = renderHook(() => useBnkUpgradeHistory(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.upgrades).toHaveLength(1);
    expect(result.current.data?.upgrades[0]).toMatchObject({
      from_version: '1.9.0',
      to_version: '2.0.0',
      status: 'completed',
    });
  });
});

describe('useBnkUpgradeDetail', () => {
  it('fetches upgrade detail when upgradeId is provided', async () => {
    setupBnkHandlers();
    const { result } = renderHook(() => useBnkUpgradeDetail(1, 5), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      id: 5,
      from_version: '2.0.0',
      to_version: '2.1.0',
      status: 'planned',
    });
  });

  it('does not fetch when upgradeId is null', () => {
    const { result } = renderHook(() => useBnkUpgradeDetail(1, null), { wrapper: createWrapper() });
    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// BNK Upgrade Workflow — Mutations
// ============================================================================

describe('useCreateBnkUpgradePlan', () => {
  it('creates an upgrade plan', async () => {
    setupBnkHandlers();
    const { result } = renderHook(() => useCreateBnkUpgradePlan(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({ clusterId: 1, targetVersion: '2.1.0' });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      id: 10,
      to_version: '2.1.0',
      status: 'planned',
    });
  });

  it('handles mutation error', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/bnk/upgrade/plan', () => {
        return HttpResponse.json({ error: { message: 'Upgrade not available' } }, { status: 400 });
      }),
    );
    const { result } = renderHook(() => useCreateBnkUpgradePlan(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({ clusterId: 1, targetVersion: '99.0.0' });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe('useExecuteBnkUpgrade', () => {
  it('executes an upgrade', async () => {
    setupBnkHandlers();
    const { result } = renderHook(() => useExecuteBnkUpgrade(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({ clusterId: 1, upgradeId: 10 });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({ success: true });
  });
});

describe('useRollbackBnkUpgrade', () => {
  it('rolls back an upgrade', async () => {
    setupBnkHandlers();
    const { result } = renderHook(() => useRollbackBnkUpgrade(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({ clusterId: 1, upgradeId: 10 });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({ success: true, message: 'Rollback initiated' });
  });
});

describe('useCancelBnkUpgrade', () => {
  it('cancels an upgrade', async () => {
    setupBnkHandlers();
    const { result } = renderHook(() => useCancelBnkUpgrade(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate({ clusterId: 1, upgradeId: 5 });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({ id: 5, status: 'cancelled' });
  });
});
