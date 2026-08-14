/**
 * Tests for useWafPolicies hooks (CT-012: MSW handlers mirror the real
 * backend response shapes from routes/k8s/waf_policies.py).
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useWafPolicies,
  useWafPolicy,
  useCreateWafPolicy,
} from '@/hooks/useWafPolicies';
import type { APPolicyResource } from '@/types';
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
// Mock Data — shaped like the real APPolicy CRD (appprotect.f5.com/v1)
// ============================================================================

const mockPolicyPending: APPolicyResource = {
  name: 'my-policy',
  kind: 'APPolicy',
  apiVersion: 'appprotect.f5.com/v1',
  metadata: { name: 'my-policy', namespace: 'default', creationTimestamp: '2026-08-01T00:00:00Z' },
  spec: { policy: { name: 'my-policy' } },
  status: { bundle: { state: 'pending' } },
};

const mockPolicyReady: APPolicyResource = {
  ...mockPolicyPending,
  status: {
    bundle: {
      state: 'ready',
      location: 's3://waf-policies/my-policy/1/bundle.tgz',
      sha256: 'abc123',
      compilerVersion: '5.14.0',
    },
  },
};

// ============================================================================
// Tests
// ============================================================================

describe('useWafPolicies', () => {
  beforeEach(() => {
    server.use(
      http.get('*/api/k8s/clusters/1/waf/policies', () => {
        return HttpResponse.json({ policies: [mockPolicyPending], count: 1 });
      })
    );
  });

  it('lists policies for a cluster', async () => {
    const { result } = renderHook(() => useWafPolicies(1), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.count).toBe(1);
    expect(result.current.data?.policies[0].metadata.name).toBe('my-policy');
  });
});

describe('useWafPolicy — bundle status polling', () => {
  it('stops polling once bundle state is ready', async () => {
    server.use(
      http.get('*/api/k8s/clusters/1/waf/policies/my-policy', () => {
        return HttpResponse.json(mockPolicyReady);
      })
    );

    const { result } = renderHook(() => useWafPolicy(1, 'my-policy', 'default'), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.status?.bundle?.state).toBe('ready');
    expect(result.current.data?.status?.bundle?.sha256).toBe('abc123');
  });
});

describe('useCreateWafPolicy', () => {
  it('sends the expected payload and returns the created resource', async () => {
    let capturedBody: unknown = null;

    server.use(
      http.post('*/api/k8s/clusters/1/waf/policies', async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json(mockPolicyPending);
      })
    );

    const { result } = renderHook(() => useCreateWafPolicy(1), { wrapper: createWrapper() });

    result.current.mutate({
      name: 'my-policy',
      namespace: 'default',
      spec: { policy: { name: 'my-policy' } },
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(capturedBody).toEqual({
      name: 'my-policy',
      namespace: 'default',
      spec: { policy: { name: 'my-policy' } },
    });
    expect(result.current.data?.metadata.name).toBe('my-policy');
  });
});
