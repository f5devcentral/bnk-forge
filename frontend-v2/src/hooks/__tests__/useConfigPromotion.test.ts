/**
 * Tests for useConfigPromotion hooks
 *
 * Covers: promotionKeys query key factory structure,
 * usePromoteConfig mutation for both dry-run and actual promotion,
 * usePromotionHistory query for promotion audit log,
 * and error handling.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  promotionKeys,
  usePromoteConfig,
  usePromotionHistory,
} from '@/hooks/useConfigPromotion';
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

// ============================================================================
// Mock Data
// ============================================================================

const mockPromoteResult = {
  source_cluster: { id: 1, name: 'prod-cluster' },
  target_cluster: { id: 2, name: 'staging-cluster' },
  changes: [
    { action: 'modify', kind: 'ConfigMap', name: 'app-config', namespace: 'default', diff: '-old\n+new' },
    { action: 'add', kind: 'Secret', name: 'api-key', namespace: 'default', diff: null },
  ],
  total_changes: 2,
  applied: false,
  apply_results: null,
};

const mockPromoteApplied = {
  source_cluster: { id: 1, name: 'prod-cluster' },
  target_cluster: { id: 2, name: 'staging-cluster' },
  changes: [
    { action: 'modify', kind: 'ConfigMap', name: 'app-config', namespace: 'default', diff: '-old\n+new' },
  ],
  total_changes: 1,
  applied: true,
  apply_results: {
    applied: [{ kind: 'ConfigMap', name: 'app-config', namespace: 'default' }],
    failed: [],
    skipped: [],
  },
};

const mockHistory = {
  total: 2,
  items: [
    {
      id: 1,
      source_cluster_name: 'prod-cluster',
      target_cluster_name: 'staging-cluster',
      change_count: 3,
      applied_by: 'admin',
      applied_at: '2026-02-18T10:00:00Z',
      success: true,
      applied_count: 3,
      failed_count: 0,
      skipped_count: 0,
      changes_summary: null,
    },
    {
      id: 2,
      source_cluster_name: 'staging-cluster',
      target_cluster_name: 'dev-cluster',
      change_count: 1,
      applied_by: 'admin',
      applied_at: '2026-02-17T14:00:00Z',
      success: true,
      applied_count: 1,
      failed_count: 0,
      skipped_count: 0,
      changes_summary: null,
    },
  ],
};

// ============================================================================
// Handlers
// ============================================================================

const promotionHandlers = [
  http.post('*/api/config/promote', async ({ request }) => {
    const body = (await request.json()) as { dry_run: boolean };
    if (body.dry_run) {
      return HttpResponse.json(mockPromoteResult);
    }
    return HttpResponse.json(mockPromoteApplied);
  }),

  http.get('*/api/config/promotions', () => {
    return HttpResponse.json(mockHistory);
  }),
];

// ============================================================================
// Tests
// ============================================================================

describe('promotionKeys', () => {
  it('has a stable all key', () => {
    expect(promotionKeys.all).toBeDefined();
    expect(Array.isArray(promotionKeys.all)).toBe(true);
  });

  it('generates history keys with limit and offset', () => {
    const key1 = promotionKeys.history(10, 0);
    const key2 = promotionKeys.history(20, 10);

    expect(key1).toBeDefined();
    expect(key2).toBeDefined();
    // Keys with different params should differ
    expect(JSON.stringify(key1)).not.toBe(JSON.stringify(key2));
  });
});

describe('usePromoteConfig', () => {
  beforeEach(() => {
    server.use(...promotionHandlers);
  });

  it('performs a dry-run promotion and returns diff', async () => {
    const { result } = renderHook(() => usePromoteConfig(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      sourceClusterId: 1,
      targetClusterId: 2,
      dryRun: true,
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.applied).toBe(false);
    expect(result.current.data?.total_changes).toBe(2);
    expect(result.current.data?.changes).toHaveLength(2);
    expect(result.current.data?.source_cluster.name).toBe('prod-cluster');
    expect(result.current.data?.apply_results).toBeNull();
  });

  it('performs an actual promotion and returns applied results', async () => {
    const { result } = renderHook(() => usePromoteConfig(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      sourceClusterId: 1,
      targetClusterId: 2,
      dryRun: false,
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.applied).toBe(true);
    expect(result.current.data?.total_changes).toBe(1);
    expect(result.current.data?.apply_results?.applied).toHaveLength(1);
    expect(result.current.data?.apply_results?.failed).toHaveLength(0);
  });

  it('handles promotion errors', async () => {
    server.use(
      http.post('*/api/config/promote', () => {
        return HttpResponse.json(
          { error: { code: 'FORBIDDEN', message: 'Insufficient permissions' } },
          { status: 403 }
        );
      })
    );

    const { result } = renderHook(() => usePromoteConfig(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      sourceClusterId: 1,
      targetClusterId: 2,
      dryRun: false,
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });

  it('sends correct payload shape matching PromoteRequest', async () => {
    // Backend: PromoteRequest { source_cluster_id: int, target_cluster_id: int, dry_run: bool = True }
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/config/promote', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ applied: false, total_changes: 0, changes: [], source_cluster: { id: 1, name: 'c1' }, target_cluster: { id: 2, name: 'c2' }, apply_results: null });
      })
    );

    const { result } = renderHook(() => usePromoteConfig(), { wrapper: createWrapper() });

    result.current.mutate({
      sourceClusterId: 10,
      targetClusterId: 20,
      dryRun: true,
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      source_cluster_id: 10,
      target_cluster_id: 20,
      dry_run: true,
    });
    // Verify snake_case naming (not camelCase)
    expect(capturedBody).not.toHaveProperty('sourceClusterId');
    expect(capturedBody).not.toHaveProperty('targetClusterId');
    expect(capturedBody).not.toHaveProperty('dryRun');
  });
});

describe('usePromotionHistory', () => {
  beforeEach(() => {
    server.use(...promotionHandlers);
  });

  it('fetches promotion history', async () => {
    const { result } = renderHook(() => usePromotionHistory(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.total).toBe(2);
    expect(result.current.data?.items).toHaveLength(2);
    expect(result.current.data?.items[0].source_cluster_name).toBe('prod-cluster');
    expect(result.current.data?.items[0].success).toBe(true);
  });

  it('handles API errors gracefully', async () => {
    server.use(
      http.get('*/api/config/promotions', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Database error' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => usePromotionHistory(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });
});
