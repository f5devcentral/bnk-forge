/**
 * Tests for BenchmarkTargetsTab — cluster indicator and filtering.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

vi.mock('@/context/ThemeContext', () => ({
  useTheme: () => ({ isDark: true, theme: 'dark', setTheme: vi.fn() }),
  ThemeProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import { BenchmarkTargetsTab } from '@/pages/BenchmarkTargetsTab';

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

const mockTargets = [
  {
    id: 1,
    name: 'vllm-aws-target',
    description: 'AWS GPU cluster target',
    cluster_id: 10,
    cluster_name: 'aws-eks-cluster',
    llm_base_url: 'http://vllm-openai.default:8000',
    llm_model: 'meta-llama/Llama-3-8B-Instruct',
    llm_namespace: 'default',
    llm_endpoint: '/v1/chat/completions',
    proxy_namespace: 'perf-proxies',
    status: 'ready',
    last_validated: '2026-06-02T10:00:00Z',
    validation_msg: null,
    tags: null,
    proxy_count: 2,
    created_at: '2026-06-02T10:00:00Z',
    updated_at: '2026-06-02T10:00:00Z',
  },
  {
    id: 2,
    name: 'vllm-gcp-target',
    description: 'GCP GKE target',
    cluster_id: 20,
    cluster_name: 'gke-cluster-prod',
    llm_base_url: 'http://vllm-gcp.default:8000',
    llm_model: 'mistralai/Mistral-7B',
    llm_namespace: 'default',
    llm_endpoint: '/v1/chat/completions',
    proxy_namespace: 'perf-proxies',
    status: 'ready',
    last_validated: null,
    validation_msg: null,
    tags: null,
    proxy_count: 1,
    created_at: '2026-06-02T10:00:00Z',
    updated_at: '2026-06-02T10:00:00Z',
  },
];

describe('BenchmarkTargetsTab', () => {
  it('renders target table with Cluster column and cluster name', async () => {
    server.use(
      http.get('*/api/benchmarks/targets', () =>
        HttpResponse.json({
          targets: mockTargets,
          total: 2,
        })
      ),
      http.get('*/api/kubernetes/clusters', () =>
        HttpResponse.json({
          clusters: [
            { id: 10, name: 'aws-eks-cluster', status: 'active' },
            { id: 20, name: 'gke-cluster-prod', status: 'active' },
          ],
        })
      ),
      http.get('*/api/benchmarks/agents', () =>
        HttpResponse.json([])
      ),
      http.get('*/api/benchmarks/scenarios', () =>
        HttpResponse.json({ scenarios: [] })
      )
    );

    render(<BenchmarkTargetsTab />, { wrapper: createWrapper() });

    // Cluster column header
    await waitFor(() => expect(screen.getByText('Cluster')).toBeInTheDocument());
    expect(screen.getByText('vllm-aws-target')).toBeInTheDocument();
    expect(screen.getByText('aws-eks-cluster')).toBeInTheDocument();
    expect(screen.getByText('vllm-gcp-target')).toBeInTheDocument();
    expect(screen.getByText('gke-cluster-prod')).toBeInTheDocument();
  });

  it('filters targets by cluster_id query param when selectedClusterId is passed', async () => {
    let capturedUrl: string | null = null;
    server.use(
      http.get('*/api/benchmarks/targets', ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json({
          targets: [mockTargets[0]],
          total: 1,
        });
      }),
      http.get('*/api/kubernetes/clusters', () =>
        HttpResponse.json({
          clusters: [{ id: 10, name: 'aws-eks-cluster', status: 'active' }],
        })
      ),
      http.get('*/api/benchmarks/agents', () =>
        HttpResponse.json([])
      ),
      http.get('*/api/benchmarks/scenarios', () =>
        HttpResponse.json({ scenarios: [] })
      )
    );

    render(<BenchmarkTargetsTab selectedClusterId={10} />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('vllm-aws-target')).toBeInTheDocument());
    expect(capturedUrl).toContain('cluster_id=10');
  });
});
