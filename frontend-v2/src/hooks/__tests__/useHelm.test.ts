/**
 * Tests for useHelm hooks
 *
 * Covers: fetching releases, release detail, history, repositories,
 * install/upgrade/rollback/uninstall mutations, error handling.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useHelmReleases,
  useHelmRelease,
  useHelmHistory,
  useHelmRepositories,
  useInstallHelmChart,
  useUpgradeHelmRelease,
  useRollbackHelmRelease,
  useUninstallHelmRelease,
  useAddHelmRepository,
  useUpdateChartValues,
} from '@/hooks/useHelm';
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
// useHelmReleases
// ============================================================================

describe('useHelmReleases', () => {
  it('fetches helm releases for cluster', async () => {
    const { result } = renderHook(() => useHelmReleases(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
    expect(Array.isArray(result.current.data)).toBe(true);
    expect(result.current.data).toHaveLength(2);
    expect(result.current.data![0].name).toBe('nginx-ingress');
    expect(result.current.data![1].name).toBe('cert-manager');
  });

  it('disabled when clusterId is null', () => {
    const { result } = renderHook(() => useHelmReleases(null), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
    expect(result.current.data).toBeUndefined();
  });
});

// ============================================================================
// useHelmRelease
// ============================================================================

describe('useHelmRelease', () => {
  it('fetches single release detail', async () => {
    const { result } = renderHook(
      () => useHelmRelease(1, 'nginx-ingress', 'ingress-nginx'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      name: 'nginx-ingress',
      namespace: 'ingress-nginx',
      status: 'deployed',
      chart: 'ingress-nginx-4.9.0',
    });
  });
});

// ============================================================================
// useHelmHistory
// ============================================================================

describe('useHelmHistory', () => {
  it('fetches release history', async () => {
    const { result } = renderHook(
      () => useHelmHistory(1, 'nginx-ingress', 'ingress-nginx'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
    expect(Array.isArray(result.current.data)).toBe(true);
    expect(result.current.data).toHaveLength(3);
    expect(result.current.data![0].revision).toBe(3);
    expect(result.current.data![0].status).toBe('deployed');
    expect(result.current.data![2].revision).toBe(1);
    expect(result.current.data![2].description).toBe('Install complete');
  });
});

// ============================================================================
// useHelmRepositories
// ============================================================================

describe('useHelmRepositories', () => {
  it('fetches helm repositories', async () => {
    const { result } = renderHook(() => useHelmRepositories(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toBeDefined();
    expect(Array.isArray(result.current.data)).toBe(true);
    expect(result.current.data).toHaveLength(2);
    expect(result.current.data![0]).toMatchObject({
      name: 'bitnami',
      url: 'https://charts.bitnami.com/bitnami',
      status: 'ready',
    });
  });
});

// ============================================================================
// useInstallHelmChart
// ============================================================================

describe('useInstallHelmChart', () => {
  it('mutation installs chart', async () => {
    const { result } = renderHook(() => useInstallHelmChart(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      clusterId: 1,
      request: {
        chart: 'bitnami/nginx',
        release_name: 'my-nginx',
        namespace: 'default',
        values: 'replicaCount: 2',
      },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      success: true,
      release: 'my-nginx',
      message: 'Chart installed successfully',
    });
  });
});

// ============================================================================
// useUpgradeHelmRelease
// ============================================================================

describe('useUpgradeHelmRelease', () => {
  it('mutation upgrades release', async () => {
    const { result } = renderHook(() => useUpgradeHelmRelease(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      clusterId: 1,
      releaseName: 'nginx-ingress',
      request: {
        chart: 'ingress-nginx/ingress-nginx',
        values: 'controller:\n  replicaCount: 3',
      },
      namespace: 'ingress-nginx',
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      success: true,
      message: 'Release upgraded successfully',
    });
  });
});

// ============================================================================
// useRollbackHelmRelease
// ============================================================================

describe('useRollbackHelmRelease', () => {
  it('mutation rolls back release', async () => {
    // The actual API calls POST /api/k8s/:clusterId/helm/releases/:releaseName/rollback
    // Override the default handler which uses a different path pattern
    server.use(
      http.post('*/api/k8s/:clusterId/helm/releases/:releaseName/rollback', () => {
        return HttpResponse.json({
          success: true,
          message: 'Rollback completed successfully',
        });
      })
    );

    const { result } = renderHook(() => useRollbackHelmRelease(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      clusterId: 1,
      releaseName: 'nginx-ingress',
      request: { revision: 2 },
      namespace: 'ingress-nginx',
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      success: true,
      message: 'Rollback completed successfully',
    });
  });
});

// ============================================================================
// useUninstallHelmRelease
// ============================================================================

describe('useUninstallHelmRelease', () => {
  it('mutation uninstalls release', async () => {
    const { result } = renderHook(() => useUninstallHelmRelease(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      clusterId: 1,
      releaseName: 'nginx-ingress',
      namespace: 'ingress-nginx',
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data).toMatchObject({
      success: true,
      message: 'Release uninstalled',
    });
  });
});

// ============================================================================
// CT-012: Payload Shape Assertions — useInstallHelmChart
// Backend: InstallChartRequest { release_name, chart, namespace, values, version, create_namespace, wait, timeout }
// ============================================================================

describe('useInstallHelmChart payload shape', () => {
  it('sends correct payload matching InstallChartRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/k8s/:clusterId/helm/install', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, result: { name: 'my-nginx', namespace: 'default', revision: 1, status: 'deployed' }, message: 'Installed' });
      })
    );

    const { result } = renderHook(() => useInstallHelmChart(), { wrapper: createWrapper() });

    result.current.mutate({
      clusterId: 1,
      request: {
        release_name: 'my-nginx',
        chart: 'bitnami/nginx',
        namespace: 'production',
        values: { replicaCount: 3 },
        version: '15.0.0',
        create_namespace: true,
        wait: true,
        timeout: '10m',
      },
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      release_name: 'my-nginx',
      chart: 'bitnami/nginx',
      namespace: 'production',
      values: { replicaCount: 3 },
      version: '15.0.0',
      create_namespace: true,
      wait: true,
      timeout: '10m',
    });
    // Not wrapped
    expect(capturedBody).not.toHaveProperty('request');
    expect(capturedBody).not.toHaveProperty('data');
  });
});

// ============================================================================
// CT-012: Payload Shape Assertions — useUpgradeHelmRelease
// Backend: UpgradeReleaseRequest { chart, values, version, install, wait, timeout }
// ============================================================================

describe('useUpgradeHelmRelease payload shape', () => {
  it('sends correct payload matching UpgradeReleaseRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/k8s/:clusterId/helm/releases/:releaseName/upgrade', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, result: { name: 'nginx', namespace: 'default', revision: 2, status: 'deployed' }, message: 'Upgraded' });
      })
    );

    const { result } = renderHook(() => useUpgradeHelmRelease(), { wrapper: createWrapper() });

    result.current.mutate({
      clusterId: 1,
      releaseName: 'nginx',
      request: {
        chart: 'bitnami/nginx',
        values: { replicaCount: 5 },
        version: '15.1.0',
        install: false,
        wait: true,
        timeout: '10m',
      },
      namespace: 'default',
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      chart: 'bitnami/nginx',
      values: { replicaCount: 5 },
      version: '15.1.0',
      install: false,
      wait: true,
      timeout: '10m',
    });
    // No wrapping — fields are top-level, no release_name in body (it's in the URL path)
    expect(capturedBody).not.toHaveProperty('release_name');
    expect(capturedBody).not.toHaveProperty('request');
  });
});

// ============================================================================
// CT-012: Payload Shape Assertions — useRollbackHelmRelease
// Backend: RollbackReleaseRequest { revision, wait, timeout }
// ============================================================================

describe('useRollbackHelmRelease payload shape', () => {
  it('sends correct payload matching RollbackReleaseRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/k8s/:clusterId/helm/releases/:releaseName/rollback', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, result: { name: 'nginx', namespace: 'default', revision: 1, status: 'deployed' }, message: 'Rolled back' });
      })
    );

    const { result } = renderHook(() => useRollbackHelmRelease(), { wrapper: createWrapper() });

    result.current.mutate({
      clusterId: 1,
      releaseName: 'nginx',
      request: { revision: 2, wait: true, timeout: '5m' },
      namespace: 'default',
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      revision: 2,
      wait: true,
      timeout: '5m',
    });
    expect(capturedBody).not.toHaveProperty('release_name');
    expect(capturedBody).not.toHaveProperty('request');
  });
});

// ============================================================================
// CT-012: Payload Shape Assertions — useAddHelmRepository
// Backend: AddRepositoryRequest { name, url, username, password }
// ============================================================================

describe('useAddHelmRepository payload shape', () => {
  it('sends correct payload matching AddRepositoryRequest', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/helm/repositories', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, message: 'Repository added', name: 'my-repo', url: 'https://charts.example.com' });
      })
    );

    const { result } = renderHook(() => useAddHelmRepository(), { wrapper: createWrapper() });

    result.current.mutate({
      name: 'my-repo',
      url: 'https://charts.example.com',
      username: 'admin',
      password: 'secret123',
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toMatchObject({
      name: 'my-repo',
      url: 'https://charts.example.com',
      username: 'admin',
      password: 'secret123',
    });
    expect(capturedBody).not.toHaveProperty('repository');
    expect(capturedBody).not.toHaveProperty('data');
  });
});

// ============================================================================
// CT-012: Payload Shape Assertions — useUpdateChartValues
// Backend: text/plain body (raw YAML string)
// ============================================================================

describe('useUpdateChartValues payload shape', () => {
  it('sends values as text/plain body', async () => {
    let capturedBody: string | null = null;
    let capturedContentType: string | null = null;
    server.use(
      http.put('*/api/helm/charts/uploaded/:chartId/values', async ({ request }) => {
        capturedContentType = request.headers.get('content-type');
        capturedBody = await request.text();
        return HttpResponse.json({ success: true, message: 'Values updated' });
      })
    );

    const { result } = renderHook(() => useUpdateChartValues(), { wrapper: createWrapper() });

    result.current.mutate({ chartId: 42, values: 'replicaCount: 3\nimage:\n  tag: latest' });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedBody).toBe('replicaCount: 3\nimage:\n  tag: latest');
    expect(capturedContentType).toContain('text/plain');
  });
});

// ============================================================================
// Error handling
// ============================================================================

describe('error handling', () => {
  it('handles 500 error on release fetch', async () => {
    server.use(
      http.get('*/api/k8s/:clusterId/helm/releases', ({ request }) => {
        const url = new URL(request.url);
        if (!url.pathname.endsWith('/helm/releases')) return;
        return HttpResponse.json(
          { error: { message: 'Internal server error' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useHelmReleases(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeDefined();
  });
});
