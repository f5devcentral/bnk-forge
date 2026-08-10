/**
 * Tests for useSnapshots hooks
 *
 * Covers: useSnapshots fetches list, useCreateSnapshot mutation,
 * useRestoreSnapshot mutation, error state handling.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useSnapshots,
  useSnapshot,
  useCreateSnapshot,
  useDiffSnapshots,
  useRestoreSnapshot,
  useDeleteSnapshot,
} from '@/hooks/useSnapshots';
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

const mockSnapshotsList = {
  snapshots: [
    {
      id: 1,
      project_id: 1,
      label: 'pre-upgrade',
      trigger: 'manual',
      created_by: 'admin',
      resource_count: 12,
      module_count: 3,
      cluster_name: 'prod-cluster',
      created_at: '2026-02-18T10:00:00Z',
    },
    {
      id: 2,
      project_id: 1,
      label: 'auto-backup',
      trigger: 'auto_pre_deploy',
      created_by: 'system',
      resource_count: 12,
      module_count: 3,
      cluster_name: 'prod-cluster',
      created_at: '2026-02-17T10:00:00Z',
    },
  ],
  total: 2,
};

const mockSnapshotDetail = {
  id: 1,
  project_id: 1,
  label: 'pre-upgrade',
  trigger: 'manual',
  created_by: 'admin',
  resource_count: 12,
  module_count: 3,
  cluster_name: 'prod-cluster',
  created_at: '2026-02-18T10:00:00Z',
  config_data: {
    modules: [{ name: 'vpc', config: { cidr: '10.0.0.0/16' } }],
    resources: [{ kind: 'ConfigMap', name: 'app-config', namespace: 'default' }],
  },
};

const mockCreateResponse = {
  id: 3,
  label: 'my-snapshot',
  message: 'Snapshot created successfully',
  resource_count: 12,
};

const mockRestoreResponse = {
  target_cluster: 'prod-cluster',
  results: {
    applied: [
      { kind: 'ConfigMap', name: 'app-config', namespace: 'default' },
      { kind: 'Secret', name: 'app-secret', namespace: 'default' },
    ],
    failed: [],
    skipped: [],
  },
};

// ============================================================================
// Handlers
// ============================================================================

const snapshotHandlers = [
  http.get('*/api/projects/:projectId/snapshots', () => {
    return HttpResponse.json(mockSnapshotsList);
  }),

  http.get('*/api/snapshots/:id', ({ params }) => {
    if (Number(params.id) === 1) {
      return HttpResponse.json(mockSnapshotDetail);
    }
    return new HttpResponse(null, { status: 404 });
  }),

  http.post('*/api/projects/:projectId/snapshots', () => {
    return HttpResponse.json(mockCreateResponse);
  }),

  http.post('*/api/snapshots/:id/restore', () => {
    return HttpResponse.json(mockRestoreResponse);
  }),

  http.delete('*/api/snapshots/:id', () => {
    return HttpResponse.json({ message: 'Snapshot deleted', id: 1 });
  }),
];

describe('useSnapshots', () => {
  beforeEach(() => {
    server.use(...snapshotHandlers);
  });

  it('fetches snapshot list for a project', async () => {
    const { result } = renderHook(() => useSnapshots(1), {
      wrapper: createWrapper(),
    });

    expect(result.current.isLoading).toBe(true);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.snapshots).toHaveLength(2);
    expect(result.current.data?.total).toBe(2);
    expect(result.current.data?.snapshots[0].label).toBe('pre-upgrade');
  });

  it('does not fetch when projectId is undefined', () => {
    const { result } = renderHook(() => useSnapshots(undefined), {
      wrapper: createWrapper(),
    });

    expect(result.current.isLoading).toBe(false);
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('handles API errors gracefully', async () => {
    server.use(
      http.get('*/api/projects/:projectId/snapshots', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Database error' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useSnapshots(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });

  it('returns correct snapshot shape', async () => {
    const { result } = renderHook(() => useSnapshots(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    const snapshot = result.current.data?.snapshots[0];
    expect(snapshot).toMatchObject({
      id: 1,
      project_id: 1,
      label: 'pre-upgrade',
      trigger: 'manual',
      created_by: 'admin',
      resource_count: 12,
    });
  });
});

describe('useSnapshot (single)', () => {
  beforeEach(() => {
    server.use(...snapshotHandlers);
  });

  it('fetches snapshot detail by id', async () => {
    const { result } = renderHook(() => useSnapshot(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.label).toBe('pre-upgrade');
    expect(result.current.data?.config_data).toBeDefined();
    expect(result.current.data?.config_data.modules).toHaveLength(1);
  });

  it('does not fetch when snapshotId is undefined', () => {
    const { result } = renderHook(() => useSnapshot(undefined), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useCreateSnapshot', () => {
  beforeEach(() => {
    server.use(...snapshotHandlers);
  });

  it('creates a snapshot and returns result', async () => {
    const { result } = renderHook(() => useCreateSnapshot(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      projectId: 1,
      data: { label: 'my-snapshot' },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      id: 3,
      label: 'my-snapshot',
    });
  });

  it('sends correct request body', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/projects/:projectId/snapshots', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(mockCreateResponse);
      })
    );

    const { result } = renderHook(() => useCreateSnapshot(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      projectId: 1,
      data: { label: 'my-snapshot' },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({ label: 'my-snapshot' });
  });

  it('handles creation errors', async () => {
    server.use(
      http.post('*/api/projects/:projectId/snapshots', () => {
        return HttpResponse.json(
          { error: { code: 'VALIDATION_ERROR', message: 'Label already exists' } },
          { status: 409 }
        );
      })
    );

    const { result } = renderHook(() => useCreateSnapshot(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      projectId: 1,
      data: { label: 'duplicate' },
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});

describe('useRestoreSnapshot', () => {
  beforeEach(() => {
    server.use(...snapshotHandlers);
  });

  it('restores a snapshot and returns result', async () => {
    const { result } = renderHook(() => useRestoreSnapshot(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.target_cluster).toBe('prod-cluster');
    expect(result.current.data?.results.applied).toHaveLength(2);
    expect(result.current.data?.results.failed).toHaveLength(0);
  });

  it('handles restore errors', async () => {
    server.use(
      http.post('*/api/snapshots/:id/restore', () => {
        return HttpResponse.json(
          { error: { code: 'CLUSTER_OFFLINE', message: 'Cluster is not reachable' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useRestoreSnapshot(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });
});

describe('useDeleteSnapshot', () => {
  beforeEach(() => {
    server.use(...snapshotHandlers);
  });

  it('deletes a snapshot', async () => {
    const { result } = renderHook(() => useDeleteSnapshot(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      message: 'Snapshot deleted',
      id: 1,
    });
  });

  it('handles delete errors', async () => {
    server.use(
      http.delete('*/api/snapshots/:id', () => {
        return HttpResponse.json(
          { error: { code: 'NOT_FOUND', message: 'Snapshot not found' } },
          { status: 404 }
        );
      })
    );

    const { result } = renderHook(() => useDeleteSnapshot(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(999);

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});

// ============================================================================
// CT-025: Payload Shape Assertions — useDiffSnapshots
// Backend: DiffSnapshotsRequest { snapshot_a_id: int, snapshot_b_id: int }
// ============================================================================

describe('useDiffSnapshots', () => {
  it('sends correct payload shape matching DiffSnapshotsRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/snapshots/diff', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ differences: [], snapshot_a: { id: 1 }, snapshot_b: { id: 2 } });
      })
    );

    const { result } = renderHook(() => useDiffSnapshots(), { wrapper: createWrapper() });

    result.current.mutate({ snapshot_a_id: 1, snapshot_b_id: 2 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      snapshot_a_id: 1,
      snapshot_b_id: 2,
    });
    expect(capturedBody).not.toHaveProperty('snapshotAId');
    expect(capturedBody).not.toHaveProperty('snapshotBId');
  });
});
