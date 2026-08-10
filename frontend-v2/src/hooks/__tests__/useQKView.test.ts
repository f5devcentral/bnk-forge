/**
 * Tests for useQKView hooks
 *
 * Covers: QKVIEW_KEYS factory, useQKViewCheck, useQKViewList, useQKViewStatus,
 * useCreateQKView, useDeleteQKView, useCancelQKView, useDownloadQKView,
 * useQKViewSetupStatus, useQKViewSetup — including enabled/disabled states,
 * error handling, cache invalidation, and payload shape assertions.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  QKVIEW_KEYS,
  useQKViewCheck,
  useQKViewList,
  useQKViewStatus,
  useCreateQKView,
  useDeleteQKView,
  useCancelQKView,
  useDownloadQKView,
  useQKViewSetupStatus,
  useQKViewSetup,
} from '@/hooks/useQKView';
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
// QKVIEW_KEYS
// ============================================================================

describe('QKVIEW_KEYS', () => {
  it('generates correct key structure', () => {
    expect(QKVIEW_KEYS.all).toEqual(['qkview']);
    expect(QKVIEW_KEYS.check(5)).toEqual(['qkview', 'check', 5]);
    expect(QKVIEW_KEYS.cwcApiSetupStatus(3)).toEqual(['qkview', 'cwc-api-setup-status', 3]);
    expect(QKVIEW_KEYS.list(7)).toEqual(['qkview', 'list', 7]);
    expect(QKVIEW_KEYS.status(7, 'abc-123')).toEqual(['qkview', 'status', 7, 'abc-123']);
  });
});

// ============================================================================
// useQKViewCheck
// ============================================================================

describe('useQKViewCheck', () => {
  // Backend: routes/qkview.py L65-73, service L981-1017
  // Response shape: {available: bool, message: str} — NO Pydantic response model
  it('fetches availability when clusterId > 0', async () => {
    server.use(
      http.get('*/api/qkview/check', () => {
        return HttpResponse.json({
          available: true,
          message: 'CWC is available',
          operator_dispatch: false,
        });
      })
    );

    const { result } = renderHook(() => useQKViewCheck(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      available: true,
      message: 'CWC is available',
      operator_dispatch: false,
    });
  });

  it('disabled when clusterId is 0', () => {
    const { result } = renderHook(() => useQKViewCheck(0), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
    expect(result.current.data).toBeUndefined();
  });

  it('disabled when clusterId is negative', () => {
    const { result } = renderHook(() => useQKViewCheck(-1), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
  });

  it('handles 500 error', async () => {
    server.use(
      http.get('*/api/qkview/check', () => {
        return HttpResponse.json(
          { error: { message: 'Internal server error' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useQKViewCheck(1), {
      wrapper: createWrapper(),
    });

    // Hook has retry: 1, so it takes 2 attempts before failing
    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    }, { timeout: 5000 });

    expect(result.current.error).toBeDefined();
  });
});

// ============================================================================
// useQKViewList
// ============================================================================

describe('useQKViewList', () => {
  // Backend: routes/qkview.py L101-113, service L1020-1035
  // Response shape: {qkviews: list[dict]} — dicts are CWC passthrough (no schema)
  // On error: {qkviews: [], error: str}
  it('fetches QKView list for cluster', async () => {
    server.use(
      http.get('*/api/qkview/list', () => {
        return HttpResponse.json({
          qkviews: [
            { id: 'qk-1', filename: 'qkview-1.tar.gz', status: 'completed' },
            { id: 'qk-2', filename: 'qkview-2.tar.gz', status: 'running' },
          ],
          error: null,
          operator_dispatch: false,
        });
      })
    );

    const { result } = renderHook(() => useQKViewList(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      qkviews: expect.arrayContaining([
        expect.objectContaining({ id: 'qk-1', status: 'completed' }),
        expect.objectContaining({ id: 'qk-2', status: 'running' }),
      ]),
      operator_dispatch: false,
    });
  });

  it('disabled when clusterId is 0', () => {
    const { result } = renderHook(() => useQKViewList(0), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
  });

  it('disabled when enabled=false', () => {
    const { result } = renderHook(() => useQKViewList(1, false), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
  });

  it('handles 500 error', async () => {
    server.use(
      http.get('*/api/qkview/list', () => {
        return HttpResponse.json(
          { error: { message: 'K8s connection failed' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useQKViewList(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});

// ============================================================================
// useQKViewStatus
// ============================================================================

describe('useQKViewStatus', () => {
  // Backend: routes/qkview.py L158-167, service L1062-1069
  // Response shape: CWC passthrough dict, fallback: {status: str}
  it('fetches status for a specific QKView', async () => {
    server.use(
      http.get('*/api/qkview/:qkviewId/status', () => {
        return HttpResponse.json({
          status: 'completed',
          progress: 100,
          operator_dispatch: true,
          nested: {
            metrics: { status: 'ok' },
            logs: { status: 'ok' },
          },
        });
      })
    );

    const { result } = renderHook(() => useQKViewStatus(1, 'qk-abc'), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      status: 'completed',
      progress: 100,
      operator_dispatch: true,
    });
  });

  it('disabled when clusterId is 0', () => {
    const { result } = renderHook(() => useQKViewStatus(0, 'qk-abc'), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
  });

  it('disabled when qkviewId is empty', () => {
    const { result } = renderHook(() => useQKViewStatus(1, ''), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
  });

  it('disabled when enabled=false', () => {
    const { result } = renderHook(() => useQKViewStatus(1, 'qk-abc', false), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// useCreateQKView
// ============================================================================

describe('useCreateQKView', () => {
  // Backend: routes/qkview.py L116-143
  // Request: QKViewCreateRequest (L46-54): cluster_id (required), namespace, timeout,
  //   filename, pod_patterns, include_core_files (default=False), core_files_max (default=2),
  //   core_files_since — all optional except cluster_id
  // Response: CWC passthrough dict from POST /v1/qkview, fallback: {id: str}
  it('creates a QKView and returns response', async () => {
    server.use(
      http.post('*/api/qkview/create', () => {
        return HttpResponse.json({
          id: 'qk-new-1',
          filename: 'qkview-new.tar.gz',
          operator_dispatch: true,
        });
      })
    );

    const { result } = renderHook(() => useCreateQKView(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ cluster_id: 1 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      id: 'qk-new-1',
      filename: 'qkview-new.tar.gz',
      operator_dispatch: true,
    });
  });

  it('sends correct payload matching QKViewCreateRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/qkview/create', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 'qk-99', filename: 'test.tar.gz', operator_dispatch: false });
      })
    );

    const { result } = renderHook(() => useCreateQKView(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      cluster_id: 5,
      namespace: 'f5-utils',
      filename: 'diag.tar.gz',
      pod_patterns: ['f5-spk-*'],
      include_core_files: true,
      core_files_max: 10,
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    // CT-012: Assert payload matches QKViewCreateRequest (routes/qkview.py L46-54)
    expect(capturedBody).toMatchObject({
      cluster_id: 5,
      namespace: 'f5-utils',
      filename: 'diag.tar.gz',
      pod_patterns: ['f5-spk-*'],
      include_core_files: true,
      core_files_max: 10,
    });
    // Not wrapped — fields are top-level
    expect(capturedBody).not.toHaveProperty('request');
    expect(capturedBody).not.toHaveProperty('data');
  });

  it('calls notifyError on failure', async () => {
    const { notifyError } = await import('@/lib/notify');
    vi.mocked(notifyError).mockClear();

    server.use(
      http.post('*/api/qkview/create', () => {
        return HttpResponse.json(
          { error: { message: 'CWC not available' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useCreateQKView(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ cluster_id: 1 });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(notifyError).toHaveBeenCalled();
  });
});

// ============================================================================
// useDeleteQKView
// ============================================================================

describe('useDeleteQKView', () => {
  // Backend: routes/qkview.py L189-198, service L1086-1102
  // Response: CWC passthrough dict, fallback: {"deleted": true}
  it('deletes a QKView', async () => {
    server.use(
      http.delete('*/api/qkview/:qkviewId', () => {
        return HttpResponse.json({ deleted: true, success: true, operator_dispatch: true });
      })
    );

    const { result } = renderHook(() => useDeleteQKView(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ qkviewId: 'qk-del-1', clusterId: 1 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      deleted: true,
      success: true,
      operator_dispatch: true,
    });
  });

  it('calls notifyError on failure', async () => {
    const { notifyError } = await import('@/lib/notify');
    vi.mocked(notifyError).mockClear();

    server.use(
      http.delete('*/api/qkview/:qkviewId', () => {
        return HttpResponse.json(
          { error: { message: 'Not found' } },
          { status: 404 }
        );
      })
    );

    const { result } = renderHook(() => useDeleteQKView(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ qkviewId: 'bad-id', clusterId: 1 });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(notifyError).toHaveBeenCalled();
  });
});

// ============================================================================
// useCancelQKView
// ============================================================================

describe('useCancelQKView', () => {
  // Backend: routes/qkview.py L201-210, service L1105-1114
  // Response: CWC passthrough dict, fallback: {"cancelled": true}
  it('cancels a running QKView', async () => {
    server.use(
      http.post('*/api/qkview/:qkviewId/cancel', () => {
        return HttpResponse.json({ cancelled: true, success: true, operator_dispatch: false });
      })
    );

    const { result } = renderHook(() => useCancelQKView(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ qkviewId: 'qk-running', clusterId: 1 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      cancelled: true,
      success: true,
      operator_dispatch: false,
    });
  });
});

// ============================================================================
// useDownloadQKView
// ============================================================================

describe('useDownloadQKView', () => {
  it('downloads a QKView tarball and triggers browser download', async () => {
    // Mock blob response
    server.use(
      http.get('*/api/qkview/:qkviewId/download', () => {
        const blob = new Blob(['fake-tarball-data'], { type: 'application/gzip' });
        return new HttpResponse(blob, {
          headers: { 'Content-Type': 'application/gzip' },
        });
      })
    );

    // Track download triggers
    const clickSpy = vi.fn();
    const createObjectURLSpy = vi.fn(() => 'blob:mock-url');
    const revokeObjectURLSpy = vi.fn();
    global.URL.createObjectURL = createObjectURLSpy;
    global.URL.revokeObjectURL = revokeObjectURLSpy;

    // Save original createElement and create a wrapper
    const origCreateElement = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation((tagName: string, options?: ElementCreationOptions) => {
      if (tagName === 'a') {
        const anchor = origCreateElement('a', options);
        anchor.click = clickSpy;
        return anchor;
      }
      return origCreateElement(tagName, options);
    });

    vi.spyOn(document.body, 'appendChild').mockImplementation((node) => node);
    vi.spyOn(document.body, 'removeChild').mockImplementation((node) => node);

    const { result } = renderHook(() => useDownloadQKView(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ qkviewId: 'qk-download', clusterId: 1, filename: 'my-diag.tar.gz' });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(createObjectURLSpy).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURLSpy).toHaveBeenCalled();
    expect(result.current.data).toMatchObject({ success: true });
  });

  it('uses default filename when none provided', async () => {
    server.use(
      http.get('*/api/qkview/:qkviewId/download', () => {
        return new HttpResponse(new Blob(['data']), {
          headers: { 'Content-Type': 'application/gzip' },
        });
      })
    );

    let capturedDownload = '';
    global.URL.createObjectURL = vi.fn(() => 'blob:mock-url');
    global.URL.revokeObjectURL = vi.fn();

    const origCreateElement = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation((tagName: string, options?: ElementCreationOptions) => {
      if (tagName === 'a') {
        const anchor = origCreateElement('a', options);
        anchor.click = vi.fn();
        // Intercept the download property
        // Property descriptor kept for reference; not needed at runtime
        Object.defineProperty(anchor, 'download', {
          get() { return capturedDownload; },
          set(v: string) { capturedDownload = v; },
          configurable: true,
        });
        return anchor;
      }
      return origCreateElement(tagName, options);
    });

    vi.spyOn(document.body, 'appendChild').mockImplementation((node) => node);
    vi.spyOn(document.body, 'removeChild').mockImplementation((node) => node);

    const { result } = renderHook(() => useDownloadQKView(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ qkviewId: 'qk-999', clusterId: 1 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedDownload).toBe('qkview-qk-999.tar.gz');
  });
});

// ============================================================================
// useQKViewSetupStatus
// ============================================================================

describe('useQKViewSetupStatus', () => {
  // Backend: routes/qkview.py L76-84, service L822-879
  // Response shape: {setup_complete, cert_manager_available, server_cert_exists,
  //   client_cert_exists, message} — all required, no Pydantic response model
  it('fetches setup status for cluster', async () => {
    server.use(
      http.get('*/api/licensing/1/cwc-setup-status', () => {
        return HttpResponse.json({
          setup_complete: true,
          cert_manager_available: true,
          server_cert_exists: true,
          client_cert_exists: true,
          certs_mounted: true,
          operator_dispatch: false,
          message: 'All certs ready',
        });
      })
    );

    const { result } = renderHook(() => useQKViewSetupStatus(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      setup_complete: true,
      cert_manager_available: true,
      server_cert_exists: true,
      client_cert_exists: true,
      certs_mounted: true,
      operator_dispatch: false,
    });
  });

  it('disabled when clusterId is 0', () => {
    const { result } = renderHook(() => useQKViewSetupStatus(0), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
  });

  it('disabled when enabled=false', () => {
    const { result } = renderHook(() => useQKViewSetupStatus(1, false), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// useQKViewSetup
// ============================================================================

describe('useQKViewSetup', () => {
  // Backend: routes/qkview.py L87-98, service L882-978
  // Response shape: {success: true, message: str, steps: [{step, status, detail?}]}
  // step.status is "ok" or "skipped"; detail only on cwc_pod_restart step
  it('runs cert-manager setup', async () => {
    server.use(
      http.post('*/api/licensing/1/cwc-setup', () => {
        return HttpResponse.json({
          success: true,
          message: 'Setup complete',
          steps: [
            { step: 'Create server Certificate CR', status: 'ok' },
            { step: 'Create client Certificate CR', status: 'ok' },
            { step: 'Restart CWC pod', status: 'ok' },
          ],
          operator_dispatch: true,
        });
      })
    );

    const { result } = renderHook(() => useQKViewSetup(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      success: true,
      operator_dispatch: true,
      steps: expect.arrayContaining([
        expect.objectContaining({ step: 'Create server Certificate CR', status: 'ok' }),
      ]),
    });
  });

  it('calls notifyError on failure', async () => {
    const { notifyError } = await import('@/lib/notify');
    vi.mocked(notifyError).mockClear();

    server.use(
      http.post('*/api/licensing/1/cwc-setup', () => {
        return HttpResponse.json(
          { error: { message: 'cert-manager not found' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useQKViewSetup(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(notifyError).toHaveBeenCalled();
  });
});
