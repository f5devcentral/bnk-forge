/**
 * Tests for useTunnels hooks
 *
 * Covers: tunnel status, all tunnels, open tunnel, close tunnel.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useTunnelStatus,
  useAllTunnels,
  useOpenTunnel,
  useCloseTunnel,
} from '../useTunnels';
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

function setupTunnelHandlers() {
  server.use(
    http.get('*/api/k8s/clusters/:clusterId/tunnel', () => {
      return HttpResponse.json({
        cluster_id: 1,
        status: 'connected',
        local_port: 8443,
        remote_host: 'tunnel.example.com',
        remote_port: 443,
        uptime_seconds: 3600,
      });
    }),
    http.get('*/api/k8s/tunnels', () => {
      return HttpResponse.json({
        tunnels: [
          { cluster_id: 1, cluster_name: 'dev-cluster', status: 'connected', local_port: 8443 },
          { cluster_id: 2, cluster_name: 'prod-cluster', status: 'disconnected', local_port: null },
        ],
        total: 2,
      });
    }),
    http.post('*/api/k8s/clusters/:clusterId/tunnel/open', () => {
      return HttpResponse.json({
        success: true,
        cluster_id: 1,
        local_port: 8443,
        message: 'SSH tunnel opened',
      });
    }),
    http.post('*/api/k8s/clusters/:clusterId/tunnel/close', () => {
      return HttpResponse.json({
        success: true,
        cluster_id: 1,
        message: 'SSH tunnel closed',
      });
    }),
  );
}

// ============================================================================
// useTunnelStatus
// ============================================================================

describe('useTunnelStatus', () => {
  it('fetches tunnel status for a cluster', async () => {
    setupTunnelHandlers();
    const { result } = renderHook(() => useTunnelStatus(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      cluster_id: 1,
      status: 'connected',
      local_port: 8443,
    });
  });

  it('does not fetch when clusterId is null', () => {
    const { result } = renderHook(() => useTunnelStatus(null), { wrapper: createWrapper() });
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('handles API error', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:clusterId/tunnel', () => {
        return HttpResponse.json({ error: { message: 'Cluster not found' } }, { status: 404 });
      }),
    );

    const { result } = renderHook(() => useTunnelStatus(99), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ============================================================================
// useAllTunnels
// ============================================================================

describe('useAllTunnels', () => {
  it('fetches all tunnels', async () => {
    setupTunnelHandlers();
    const { result } = renderHook(() => useAllTunnels(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data!.tunnels).toHaveLength(2);
    expect(result.current.data!.tunnels[0]).toMatchObject({
      cluster_id: 1,
      cluster_name: 'dev-cluster',
      status: 'connected',
    });
    expect(result.current.data!.tunnels[1]).toMatchObject({
      cluster_id: 2,
      status: 'disconnected',
    });
  });

  it('handles empty tunnel list', async () => {
    server.use(
      http.get('*/api/k8s/tunnels', () => {
        return HttpResponse.json({ tunnels: [], total: 0 });
      }),
    );

    const { result } = renderHook(() => useAllTunnels(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data!.tunnels).toHaveLength(0);
  });
});

// ============================================================================
// useOpenTunnel
// ============================================================================

describe('useOpenTunnel', () => {
  it('opens an SSH tunnel', async () => {
    setupTunnelHandlers();
    const { result } = renderHook(() => useOpenTunnel(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate(1);
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      cluster_id: 1,
      local_port: 8443,
    });
  });

  it('handles tunnel open error', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/tunnel/open', () => {
        return HttpResponse.json(
          { error: { message: 'SSH key not configured' } },
          { status: 400 },
        );
      }),
    );

    const { result } = renderHook(() => useOpenTunnel(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate(1);
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ============================================================================
// useCloseTunnel
// ============================================================================

describe('useCloseTunnel', () => {
  it('closes an SSH tunnel', async () => {
    setupTunnelHandlers();
    const { result } = renderHook(() => useCloseTunnel(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate(1);
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toMatchObject({
      success: true,
      cluster_id: 1,
      message: 'SSH tunnel closed',
    });
  });

  it('handles tunnel close error', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/tunnel/close', () => {
        return HttpResponse.json(
          { error: { message: 'No active tunnel' } },
          { status: 400 },
        );
      }),
    );

    const { result } = renderHook(() => useCloseTunnel(), { wrapper: createWrapper() });

    act(() => {
      result.current.mutate(1);
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
