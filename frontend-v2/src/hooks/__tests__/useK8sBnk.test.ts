/**
 * Tests for useK8sBnk hooks
 *
 * Covers: BNK unified data, convenience selectors (health, topology, policy),
 * upgrade workflow (versions, current, history, detail, create, execute, rollback, cancel).
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
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
} from '@/hooks/useK8sBnk';
import React from 'react';

const mockBnkData = {
  health: { overall_status: 'healthy', components: [] },
  topology: [{ gateway: 'gw-1', backends: ['svc-1'] }],
  dataPlane: { pods: 2, ready: 2 },
  topologyCounts: { gateways: 1, routes: 2 },
  policyAssociations: [{ policy: 'rate-limit', gateway: 'gw-1' }],
  policyCount: 1,
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

function setupBnkDataHandler() {
  server.use(
    http.get('*/api/k8s/clusters/:clusterId/f5bnk/data', () => {
      return HttpResponse.json(mockBnkData);
    }),
    http.get('*/api/k8s/clusters/:clusterId/f5bnk/health', () => {
      return HttpResponse.json(mockBnkHealth);
    })
  );
}

// ========================================================================
// BNK Unified Data
// ========================================================================

describe('useBnkData', () => {
  it('fetches unified BNK data', async () => {
    setupBnkDataHandler();

    const { result } = renderHook(
      () => useBnkData(1),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      health: { overall_status: 'healthy' },
    });
  });

  it('does not fetch when clusterId is 0', () => {
    const { result } = renderHook(
      () => useBnkData(0),
      { wrapper: createWrapper() }
    );

    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when disabled', () => {
    const { result } = renderHook(
      () => useBnkData(1, undefined, { enabled: false }),
      { wrapper: createWrapper() }
    );

    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ========================================================================
// Convenience Selectors
// ========================================================================

describe('useF5BNKHealth', () => {
  it('fetches health data directly from /f5bnk/health', async () => {
    setupBnkDataHandler();

    const { result } = renderHook(
      () => useF5BNKHealth(1),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ overall_status: 'healthy', cluster_id: 1 });
  });
});

describe('useF5GatewayTopology', () => {
  it('returns topology slice of BNK data', async () => {
    setupBnkDataHandler();

    const { result } = renderHook(
      () => useF5GatewayTopology(1),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      cluster_id: 1,
      counts: { gateways: 1, routes: 2 },
    });
    expect(result.current.data!.topology).toHaveLength(1);
  });
});

describe('useF5PolicyGatewayAssociations', () => {
  it('returns policy associations slice of BNK data', async () => {
    setupBnkDataHandler();

    const { result } = renderHook(
      () => useF5PolicyGatewayAssociations(1),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      cluster_id: 1,
      count: 1,
    });
    expect(result.current.data!.associations).toHaveLength(1);
  });
});

// ========================================================================
// BNK Upgrade Workflow — Queries
// ========================================================================

describe('useBnkUpgradeVersions', () => {
  it('fetches available upgrade versions', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/bnk/upgrade/versions', () => {
        return HttpResponse.json({
          versions: ['2.0.0', '2.1.0', '2.2.0'],
          current_version: '2.0.0',
        });
      })
    );

    const { result } = renderHook(
      () => useBnkUpgradeVersions(1),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.versions).toContain('2.1.0');
  });
});

describe('useBnkCurrentVersion', () => {
  it('fetches current BNK version', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/bnk/upgrade/current', () => {
        return HttpResponse.json({ version: '2.0.0', installed_at: '2026-01-15T00:00:00Z' });
      })
    );

    const { result } = renderHook(
      () => useBnkCurrentVersion(1),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.version).toBe('2.0.0');
  });
});

describe('useBnkUpgradeHistory', () => {
  it('fetches upgrade history', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/bnk/upgrade/history', () => {
        return HttpResponse.json({
          upgrades: [
            { id: 1, from_version: '1.9.0', to_version: '2.0.0', status: 'completed' },
          ],
          count: 1,
        });
      })
    );

    const { result } = renderHook(
      () => useBnkUpgradeHistory(1),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data!.upgrades).toHaveLength(1);
  });
});

describe('useBnkUpgradeDetail', () => {
  it('fetches upgrade detail when upgradeId is provided', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/bnk/upgrade/:upgradeId', () => {
        return HttpResponse.json({
          id: 5,
          from_version: '2.0.0',
          to_version: '2.1.0',
          status: 'in_progress',
          steps: [],
        });
      })
    );

    const { result } = renderHook(
      () => useBnkUpgradeDetail(1, 5),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ id: 5, status: 'in_progress' });
  });

  it('does not fetch when upgradeId is null', () => {
    const { result } = renderHook(
      () => useBnkUpgradeDetail(1, null),
      { wrapper: createWrapper() }
    );

    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ========================================================================
// BNK Upgrade Workflow — Mutations
// ========================================================================

describe('useCreateBnkUpgradePlan', () => {
  it('creates an upgrade plan', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/bnk/upgrade/plan', () => {
        return HttpResponse.json({
          id: 10,
          from_version: '2.0.0',
          to_version: '2.1.0',
          status: 'planned',
        });
      })
    );

    const { result } = renderHook(() => useCreateBnkUpgradePlan(), { wrapper: createWrapper() });

    result.current.mutate({ clusterId: 1, targetVersion: '2.1.0' });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ status: 'planned' });
  });

  it('sends correct payload shape matching CreateUpgradePlanRequest', async () => {
    // Backend schema: CreateUpgradePlanRequest { target_version: str }
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/bnk/upgrade/plan', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          id: 11,
          from_version: '2.0.0',
          to_version: '2.2.0',
          status: 'planned',
        });
      })
    );

    const { result } = renderHook(() => useCreateBnkUpgradePlan(), { wrapper: createWrapper() });

    result.current.mutate({ clusterId: 1, targetVersion: '2.2.0' });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Payload matches backend CreateUpgradePlanRequest schema
    expect(capturedBody).toMatchObject({
      target_version: '2.2.0',
    });
    // No wrapping — target_version is top-level
    expect(capturedBody).not.toHaveProperty('targetVersion'); // camelCase would be wrong
    expect(capturedBody).not.toHaveProperty('version');
    expect(capturedBody).not.toHaveProperty('data');
  });
});

describe('useExecuteBnkUpgrade', () => {
  it('executes an upgrade', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/bnk/upgrade/:upgradeId/execute', () => {
        return HttpResponse.json({ success: true, status: 'executing' });
      })
    );

    const { result } = renderHook(() => useExecuteBnkUpgrade(), { wrapper: createWrapper() });

    result.current.mutate({ clusterId: 1, upgradeId: 10 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true });
  });
});

describe('useRollbackBnkUpgrade', () => {
  it('rolls back an upgrade', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/bnk/upgrade/:upgradeId/rollback', () => {
        return HttpResponse.json({ success: true, status: 'rolling_back' });
      })
    );

    const { result } = renderHook(() => useRollbackBnkUpgrade(), { wrapper: createWrapper() });

    result.current.mutate({ clusterId: 1, upgradeId: 10 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ success: true });
  });
});

describe('useCancelBnkUpgrade', () => {
  it('cancels an upgrade', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/bnk/upgrade/:upgradeId/cancel', () => {
        return HttpResponse.json({
          id: 10,
          status: 'cancelled',
        });
      })
    );

    const { result } = renderHook(() => useCancelBnkUpgrade(), { wrapper: createWrapper() });

    result.current.mutate({ clusterId: 1, upgradeId: 10 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({ status: 'cancelled' });
  });

  it('handles cancellation errors', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/bnk/upgrade/:upgradeId/cancel', () => {
        return HttpResponse.json(
          { error: { message: 'Upgrade already completed' } },
          { status: 409 }
        );
      })
    );

    const { result } = renderHook(() => useCancelBnkUpgrade(), { wrapper: createWrapper() });

    result.current.mutate({ clusterId: 1, upgradeId: 10 });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});
