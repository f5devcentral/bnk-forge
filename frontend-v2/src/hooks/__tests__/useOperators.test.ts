/**
 * Tests for useOperators hooks
 *
 * Covers: fetching operators list, fetching single operator, disabled query
 * when operatorId is empty, deleting operators, sending operator commands,
 * linking operators to clusters, setting connectivity modes, and error handling.
 *
 * Note: Registration token hook tests (useRegistrationTokens, useCreateRegistrationToken,
 * useGenerateInstallCommand) were removed in D3-CLEANUP.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useOperators,
  useOperator,
  useDeleteOperator,
  useSendOperatorCommand,
  useLinkOperatorToCluster,
  useSetOperatorConnectivity,
} from '@/hooks/useOperators';
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
// useOperators
// ============================================================================

describe('useOperators', () => {
  it('fetches the operators list', async () => {
    const { result } = renderHook(() => useOperators(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
    expect(result.current.data).toHaveLength(2);
    expect(result.current.data![0]).toMatchObject({
      id: 'op-1',
      name: 'prod-operator',
      status: 'connected',
    });
  });

  it('passes connectedOnly parameter', async () => {
    let capturedUrl: string | null = null;
    server.use(
      http.get('*/api/operators', ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json([]);
      })
    );

    const { result } = renderHook(() => useOperators(true), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedUrl).toContain('connected_only=true');
  });
});

// ============================================================================
// useOperator (single)
// ============================================================================

describe('useOperator', () => {
  it('fetches a single operator by ID', async () => {
    const { result } = renderHook(() => useOperator('op-1'), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      id: 'op-1',
      name: 'prod-operator',
      status: 'connected',
      version: '2.10.45',
    });
  });

  it('is disabled when operatorId is empty string', () => {
    const { result } = renderHook(() => useOperator(''), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
    expect(result.current.data).toBeUndefined();
  });
});

// ============================================================================
// useDeleteOperator
// ============================================================================

describe('useDeleteOperator', () => {
  it('deletes an operator', async () => {
    const { result } = renderHook(() => useDeleteOperator(), {
      wrapper: createWrapper(),
    });

    result.current.mutate('op-1');

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      success: true,
      message: 'Operator deleted',
    });
  });
});

// ============================================================================
// useSendOperatorCommand
// ============================================================================

describe('useSendOperatorCommand', () => {
  it('sends a command to an operator', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/operators/:operatorId/command', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          success: true,
          command_id: 'cmd-test',
          result: { action: capturedBody.action, status: 'completed' },
        });
      })
    );

    const { result } = renderHook(() => useSendOperatorCommand(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      operatorId: 'op-1',
      action: 'status',
      payload: { verbose: true },
      timeout: 30,
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      action: 'status',
      payload: { verbose: true },
      timeout: 30,
    });
    expect(result.current.data).toMatchObject({
      success: true,
      command_id: 'cmd-test',
    });
  });
});

// ============================================================================
// CT-017: Payload Shape Assertions — useLinkOperatorToCluster
// Backend: LinkClusterRequest { cluster_id }
// ============================================================================

describe('useLinkOperatorToCluster payload shape', () => {
  it('sends correct payload matching LinkClusterRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/operators/:operatorId/link-cluster', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, message: 'Linked', operator: { id: 'op-1' } });
      })
    );

    const { result } = renderHook(() => useLinkOperatorToCluster(), { wrapper: createWrapper() });

    result.current.mutate({ operatorId: 'op-1', clusterId: 42 });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({ cluster_id: 42 });
    // Not wrapped — cluster_id is top-level
    expect(capturedBody).not.toHaveProperty('clusterId'); // camelCase would be wrong
    expect(capturedBody).not.toHaveProperty('operator_id'); // not in body (it's in URL)
  });
});

// ============================================================================
// CT-017: Payload Shape Assertions — useSetOperatorConnectivity
// Backend: SetConnectivityModeRequest { mode, config }
// ============================================================================

describe('useSetOperatorConnectivity payload shape', () => {
  it('sends correct payload matching SetConnectivityModeRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/operators/:operatorId/connectivity', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ operator: { id: 'op-1' }, setup: { mode: 'direct_ws', setup_status: 'ready' } });
      })
    );

    const { result } = renderHook(() => useSetOperatorConnectivity(), { wrapper: createWrapper() });

    result.current.mutate({
      operatorId: 'op-1',
      mode: 'reverse_ssh',
      config: { ssh_host: '10.0.0.1', ssh_port: 22 },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      mode: 'reverse_ssh',
      config: { ssh_host: '10.0.0.1', ssh_port: 22 },
    });
    expect(capturedBody).not.toHaveProperty('operator_id');
    expect(capturedBody).not.toHaveProperty('operatorId');
  });
});

// ============================================================================
// Error handling
// ============================================================================

describe('useOperators error handling', () => {
  it('handles API errors on operators list', async () => {
    server.use(
      http.get('*/api/operators', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Server error' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useOperators(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });
});
