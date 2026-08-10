/**
 * Tests for BenchmarkRunGroupView — parent aggregate + child-run table.
 *
 * Uses MSW with the REAL RunGroupResponse shape (from api-generated.ts) per the
 * CT-012 pattern. Renders the real useRunGroup hook against the mocked endpoint.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { BenchmarkRunGroupView } from '@/pages/BenchmarkRunGroupView';
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

const now = '2026-03-19T10:00:00Z';

// Matches RunGroupResponse / RunGroupChildSummary in api-generated.ts
function mockRunGroupResponse(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    scenario_key: 'prefix-cache',
    scenario_name: 'Prefix Cache Sweep',
    run_label: 'prefix-cache-envoy-target-a',
    status: 'completed',
    target_id: 3,
    proxy: 'envoy',
    model: 'tinyllama',
    total_runs: 2,
    completed_runs: 2,
    failed_runs: 0,
    created_at: now,
    agent_id: 1,
    base_url: 'http://envoy:10080',
    tags: null,
    error_message: null,
    aggregate_json: { variants: [] },
    avg_latency_p50: 0.05,
    avg_latency_p99: 0.15,
    peak_rps: 15.0,
    total_output_tokens: 10000,
    started_at: now,
    completed_at: now,
    updated_at: now,
    runs: [
      {
        id: 101,
        variant_label: 'c=8',
        status: 'completed',
        concurrency: 8,
        latency_p50: 0.05,
        latency_p99: 0.15,
        overall_rps: 10.0,
        tokens_per_sec: 500.0,
      },
      {
        id: 102,
        variant_label: 'c=16',
        status: 'running',
        concurrency: 16,
        latency_p50: null,
        latency_p99: null,
        overall_rps: null,
        tokens_per_sec: null,
      },
    ],
    ...overrides,
  };
}

describe('BenchmarkRunGroupView', () => {
  it('renders parent aggregate metrics and child-run rows', async () => {
    server.use(
      http.get('*/api/benchmarks/run-groups/:groupId', () =>
        HttpResponse.json(mockRunGroupResponse())
      )
    );

    render(<BenchmarkRunGroupView groupId={7} />, { wrapper: createWrapper() });

    // Scenario name + run progress header
    await waitFor(() => expect(screen.getByText('Prefix Cache Sweep')).toBeInTheDocument());
    expect(screen.getByText(/2\/2 runs complete/)).toBeInTheDocument();

    // Aggregate metric labels
    expect(screen.getByText('Peak RPS')).toBeInTheDocument();
    expect(screen.getByText('Output tokens')).toBeInTheDocument();

    // Child variant rows
    expect(screen.getByText('c=8')).toBeInTheDocument();
    expect(screen.getByText('c=16')).toBeInTheDocument();
  });

  it('shows an error card when the run-group fetch fails', async () => {
    server.use(
      http.get('*/api/benchmarks/run-groups/:groupId', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 })
      )
    );

    render(<BenchmarkRunGroupView groupId={7} />, { wrapper: createWrapper() });

    await waitFor(() =>
      expect(screen.getByText('Run group unavailable')).toBeInTheDocument()
    );
    expect(screen.getByText(/Failed to load run-group results/)).toBeInTheDocument();
    // Must not be stuck on the loading skeleton path.
    expect(screen.queryByText('No child runs yet.')).not.toBeInTheDocument();
  });

  it('shows empty-state row when no child runs yet', async () => {
    server.use(
      http.get('*/api/benchmarks/run-groups/:groupId', () =>
        HttpResponse.json(mockRunGroupResponse({ runs: [], status: 'pending', completed_runs: 0 }))
      )
    );

    render(<BenchmarkRunGroupView groupId={7} />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('No child runs yet.')).toBeInTheDocument());
  });
});
