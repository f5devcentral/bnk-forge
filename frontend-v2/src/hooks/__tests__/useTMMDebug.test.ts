/**
 * Tests for useTMMDebug hooks
 *
 * Covers: TMM_DEBUG_KEYS factory, useTMMDebugPods query, mutation hooks
 * (useTMMDebugExec, useTMMDebugTmctl, useTMMDebugBdt, useTMMDebugConfigview,
 * useTMMDebugConfigviewUuids), including enabled/disabled states, error handling,
 * and payload shape assertions.
 *
 * Backend: routes/k8s/tmm_debug.py, services/tmm_debug_service.py
 * Response shapes: verified from route handlers (no Pydantic response models)
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  TMM_DEBUG_KEYS,
  useTMMDebugPods,
  useTMMDebugExec,
  useTMMDebugTmctl,
  useTMMDebugBdt,
  useTMMDebugConfigview,
  useTMMDebugConfigviewUuids,
} from '@/hooks/useTMMDebug';
import React from 'react';

// Mock notifyError to prevent side effects
vi.mock('@/lib/notify', () => ({
  notifyError: vi.fn(),
  notify: Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    loading: vi.fn(),
    dismiss: vi.fn(),
    undo: vi.fn(),
    progress: vi.fn(),
    promise: vi.fn(),
  }),
}));

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

afterEach(() => {
  vi.restoreAllMocks();
});

// ============================================================================
// TMM_DEBUG_KEYS
// ============================================================================

describe('TMM_DEBUG_KEYS', () => {
  it('generates correct key structure', () => {
    expect(TMM_DEBUG_KEYS.all).toEqual(['tmm-debug']);
    expect(TMM_DEBUG_KEYS.pods(5)).toEqual(['tmm-debug', 'pods', 5]);
  });
});

// ============================================================================
// useTMMDebugPods
// ============================================================================

describe('useTMMDebugPods', () => {
  // Backend: routes/k8s/tmm_debug.py GET /api/k8s/clusters/{id}/tmm-debug/pods
  // Response: { pods: [...], count: N, debug_available: N }

  it('fetches TMM pods when clusterId > 0', async () => {
    server.use(
      http.get('*/api/k8s/clusters/1/tmm-debug/pods', () => {
        return HttpResponse.json({
          pods: [
            {
              name: 'f5-tmm-znvz2',
              namespace: 'f5-bnk',
              has_debug: true,
              containers: ['tmm', 'debug'],
              phase: 'Running',
            },
          ],
          count: 1,
          debug_available: 1,
        });
      }),
    );

    const { result } = renderHook(() => useTMMDebugPods(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      pods: [
        {
          name: 'f5-tmm-znvz2',
          namespace: 'f5-bnk',
          has_debug: true,
          containers: ['tmm', 'debug'],
          phase: 'Running',
        },
      ],
      count: 1,
      debug_available: 1,
    });
  });

  it('disabled when clusterId is 0', () => {
    const { result } = renderHook(() => useTMMDebugPods(0), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
    expect(result.current.data).toBeUndefined();
  });

  it('disabled when enabled=false', () => {
    const { result } = renderHook(() => useTMMDebugPods(1, false), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
  });

  it('handles server error', async () => {
    server.use(
      http.get('*/api/k8s/clusters/1/tmm-debug/pods', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Failed' } },
          { status: 500 },
        );
      }),
    );

    const { result } = renderHook(() => useTMMDebugPods(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      // After retry=1 exhausted, query enters error state
      expect(result.current.status).toBe('error');
    }, { timeout: 5000 });
  });
});

// ============================================================================
// useTMMDebugExec
// ============================================================================

describe('useTMMDebugExec', () => {
  // Backend: routes/k8s/tmm_debug.py POST /api/k8s/clusters/{id}/tmm-debug/exec
  // Request: { pod_name, namespace, command, timeout? }
  // Response: { stdout, stderr, exit_code, duration_ms, command }

  it('sends correct payload and returns exec result', async () => {
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.post('*/api/k8s/clusters/1/tmm-debug/exec', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          stdout: 'hello world\n',
          stderr: '',
          exit_code: 0,
          duration_ms: 42,
          command: 'echo hello world',
        });
      }),
    );

    const { result } = renderHook(() => useTMMDebugExec(1), {
      wrapper: createWrapper(),
    });

    await act(async () => {
      result.current.mutate({
        pod_name: 'f5-tmm-znvz2',
        namespace: 'f5-bnk',
        command: 'echo hello world',
      });
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Verify payload matches backend Pydantic schema TMMDebugExecRequest
    expect(capturedBody).toMatchObject({
      pod_name: 'f5-tmm-znvz2',
      namespace: 'f5-bnk',
      command: 'echo hello world',
    });
    // Should NOT have extra wrapping keys
    expect(capturedBody).not.toHaveProperty('request');

    expect(result.current.data).toMatchObject({
      stdout: 'hello world\n',
      stderr: '',
      exit_code: 0,
      duration_ms: 42,
      command: 'echo hello world',
    });
  });

  it('calls notifyError on failure', async () => {
    const { notifyError } = await import('@/lib/notify');

    server.use(
      http.post('*/api/k8s/clusters/1/tmm-debug/exec', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Pod not found' } },
          { status: 500 },
        );
      }),
    );

    const { result } = renderHook(() => useTMMDebugExec(1), {
      wrapper: createWrapper(),
    });

    await act(async () => {
      result.current.mutate({
        pod_name: 'nonexistent',
        namespace: 'f5-bnk',
        command: 'ls',
      });
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(notifyError).toHaveBeenCalled();
  });
});

// ============================================================================
// useTMMDebugTmctl
// ============================================================================

describe('useTMMDebugTmctl', () => {
  // Backend: routes/k8s/tmm_debug.py POST /api/k8s/clusters/{id}/tmm-debug/tmctl
  // Request: { pod_name, namespace, table, columns?, width?, directory? }
  // Response: { columns, rows, raw, stderr, exit_code, duration_ms, command }

  it('sends correct payload and returns parsed tmctl output', async () => {
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.post('*/api/k8s/clusters/1/tmm-debug/tmctl', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          columns: ['name', 'clientside.bytes_in', 'clientside.bytes_out'],
          rows: [['/Common/vs1', '12345', '67890']],
          raw: 'name  clientside.bytes_in  clientside.bytes_out\n/Common/vs1  12345  67890',
          stderr: '',
          exit_code: 0,
          duration_ms: 85,
          command: 'tmctl -d blade virtual_server_stat -s name,clientside.bytes_in,clientside.bytes_out -w 200',
        });
      }),
    );

    const { result } = renderHook(() => useTMMDebugTmctl(1), {
      wrapper: createWrapper(),
    });

    await act(async () => {
      result.current.mutate({
        pod_name: 'f5-tmm-znvz2',
        namespace: 'f5-bnk',
        table: 'virtual_server_stat',
        columns: ['name', 'clientside.bytes_in', 'clientside.bytes_out'],
        width: 200,
        directory: 'blade',
      });
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Verify payload matches backend Pydantic schema TMMDebugTmctlRequest
    expect(capturedBody).toMatchObject({
      pod_name: 'f5-tmm-znvz2',
      namespace: 'f5-bnk',
      table: 'virtual_server_stat',
      columns: ['name', 'clientside.bytes_in', 'clientside.bytes_out'],
      width: 200,
      directory: 'blade',
    });

    expect(result.current.data).toMatchObject({
      columns: ['name', 'clientside.bytes_in', 'clientside.bytes_out'],
      rows: [['/Common/vs1', '12345', '67890']],
      exit_code: 0,
    });
  });
});

// ============================================================================
// useTMMDebugBdt
// ============================================================================

describe('useTMMDebugBdt', () => {
  // Backend: routes/k8s/tmm_debug.py POST /api/k8s/clusters/{id}/tmm-debug/bdt
  // Request: { pod_name, namespace, subcommand }
  // Response: { stdout, stderr, exit_code, duration_ms, command }

  it('sends correct payload for bdt_cli arp command', async () => {
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.post('*/api/k8s/clusters/1/tmm-debug/bdt', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          stdout: 'ARP entry 10.1.1.1 00:11:22:33:44:55\n',
          stderr: '',
          exit_code: 0,
          duration_ms: 30,
          command: 'bdt_cli -u -s tmm0:8850 arp',
        });
      }),
    );

    const { result } = renderHook(() => useTMMDebugBdt(1), {
      wrapper: createWrapper(),
    });

    await act(async () => {
      result.current.mutate({
        pod_name: 'f5-tmm-znvz2',
        namespace: 'f5-bnk',
        subcommand: 'arp',
      });
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Verify payload matches backend Pydantic schema TMMDebugBdtRequest
    expect(capturedBody).toMatchObject({
      pod_name: 'f5-tmm-znvz2',
      namespace: 'f5-bnk',
      subcommand: 'arp',
    });

    expect(result.current.data).toMatchObject({
      stdout: expect.stringContaining('ARP entry'),
      exit_code: 0,
    });
  });
});

// ============================================================================
// useTMMDebugConfigview
// ============================================================================

describe('useTMMDebugConfigview', () => {
  // Backend: routes/k8s/tmm_debug.py POST /api/k8s/clusters/{id}/tmm-debug/configview
  // Request: { pod_name, namespace, uuid }
  // Response: { stdout, stderr, exit_code, duration_ms, command }

  it('sends correct payload for configview uuid query', async () => {
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.post('*/api/k8s/clusters/1/tmm-debug/configview', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          stdout: 'request:[declTmm.vlan]:{name:"external" id:"default-net-external-vlan"}',
          stderr: '',
          exit_code: 0,
          duration_ms: 25,
          command: 'configview uuid default-net-external-vlan',
        });
      }),
    );

    const { result } = renderHook(() => useTMMDebugConfigview(1), {
      wrapper: createWrapper(),
    });

    await act(async () => {
      result.current.mutate({
        pod_name: 'f5-tmm-znvz2',
        namespace: 'f5-bnk',
        uuid: 'default-net-external-vlan',
      });
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Verify payload matches backend Pydantic schema TMMDebugConfigviewRequest
    expect(capturedBody).toMatchObject({
      pod_name: 'f5-tmm-znvz2',
      namespace: 'f5-bnk',
      uuid: 'default-net-external-vlan',
    });

    expect(result.current.data).toMatchObject({
      stdout: expect.stringContaining('declTmm.vlan'),
      exit_code: 0,
    });
  });
});

// ============================================================================
// useTMMDebugConfigviewUuids
// ============================================================================

describe('useTMMDebugConfigviewUuids', () => {
  // Backend: routes/k8s/tmm_debug.py POST /api/k8s/clusters/{id}/tmm-debug/configview/uuids
  // Request: { pod_name, namespace }
  // Response: { uuids, raw, stderr, exit_code, duration_ms, command }

  it('sends correct payload and returns UUID list', async () => {
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.post('*/api/k8s/clusters/1/tmm-debug/configview/uuids', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          uuids: ['default-net-external-vlan', 'default-net-internal-vlan'],
          raw: 'default-net-external-vlan\ndefault-net-internal-vlan\n',
          stderr: '',
          exit_code: 0,
          duration_ms: 40,
          command: 'configview list',
        });
      }),
    );

    const { result } = renderHook(() => useTMMDebugConfigviewUuids(1), {
      wrapper: createWrapper(),
    });

    await act(async () => {
      result.current.mutate({
        pod_name: 'f5-tmm-znvz2',
        namespace: 'f5-bnk',
      });
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // Verify payload matches backend Pydantic schema TMMDebugConfigviewUuidsRequest
    expect(capturedBody).toMatchObject({
      pod_name: 'f5-tmm-znvz2',
      namespace: 'f5-bnk',
    });
    // Should NOT have extra fields
    expect(Object.keys(capturedBody as object)).toEqual(['pod_name', 'namespace']);

    expect(result.current.data).toMatchObject({
      uuids: ['default-net-external-vlan', 'default-net-internal-vlan'],
      exit_code: 0,
    });
  });
});
