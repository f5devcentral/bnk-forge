/**
 * Tests for BenchmarkRunsTab — the runs list table.
 *
 * Guards the Tok/s (output tokens/sec) and Errors columns: inference users track
 * output throughput over RPS, and a failed-request count must be visible (a run
 * with aiperf errors can't read as 100% success with no error indication).
 *
 * Uses MSW with the REAL BenchmarkRunListResponse shape per the CT-012 pattern.
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

import { BenchmarkRunsTab } from '@/pages/BenchmarkRunsTab';

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

const now = '2026-06-02T10:00:00Z';

// Matches BenchmarkRunResponse in api-generated.ts
function mockRun(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    tool: 'aiperf',
    proxy: 'f5-bnk',
    model: 'Qwen/Qwen3-32B',
    base_url: 'http://tmm:8080',
    run_label: null,
    tags: null,
    config_id: null,
    agent_id: 3,
    status: 'completed',
    error_message: null,
    duration_seconds: 60.0,
    total_requests: 1000,
    successful_requests: 886,
    failed_requests: 114,
    success_rate_pct: 88.6,
    latency_p50: 6.24,
    latency_p99: 26.53,
    overall_rps: 26.5,
    peak_rps: 30.0,
    tokens_per_sec: 1234.5,
    total_output_tokens: 50000,
    started_at: now,
    completed_at: now,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

const noopProps = {
  proxyFilter: '',
  onProxyFilterChange: vi.fn(),
  statusFilter: '',
  onStatusFilterChange: vi.fn(),
  searchQuery: '',
  onSearchChange: vi.fn(),
  onSelectRun: vi.fn(),
  compareRunIds: [],
  onToggleCompare: vi.fn(),
  onCompare: vi.fn(),
};

describe('BenchmarkRunsTab', () => {
  it('renders Tok/s and Errors columns with values', async () => {
    server.use(
      http.get('*/api/benchmarks/runs', () =>
        HttpResponse.json({
          runs: [
            mockRun({ id: 1, failed_requests: 114, tokens_per_sec: 1234.5 }),
            mockRun({ id: 2, proxy: 'envoy', failed_requests: 0, success_rate_pct: 100.0, tokens_per_sec: 900 }),
          ],
          total: 2,
          limit: 50,
          offset: 0,
        })
      )
    );

    render(<BenchmarkRunsTab {...noopProps} />, { wrapper: createWrapper() });

    // Column headers
    await waitFor(() => expect(screen.getByText('Tok/s')).toBeInTheDocument());
    expect(screen.getByText('Errors')).toBeInTheDocument();

    // Output throughput rendered via fmtNum (1234.5 -> "1.2k", 900 -> "900.0")
    expect(screen.getByText('1.2k')).toBeInTheDocument();
    expect(screen.getByText('900.0')).toBeInTheDocument();

    // Error count surfaced (not hidden behind a 100% success cell)
    expect(screen.getByText('114')).toBeInTheDocument();
  });

  it('shows a non-zero error count in red and a clean run as plain 0', async () => {
    server.use(
      http.get('*/api/benchmarks/runs', () =>
        HttpResponse.json({
          runs: [
            mockRun({ id: 1, failed_requests: 114 }),
            mockRun({ id: 2, failed_requests: 0 }),
          ],
          total: 2,
          limit: 50,
          offset: 0,
        })
      )
    );

    render(<BenchmarkRunsTab {...noopProps} />, { wrapper: createWrapper() });

    const errorCell = await waitFor(() => screen.getByText('114'));
    expect(errorCell).toHaveClass('text-destructive');

    // A clean run shows a muted "0", not the destructive color.
    const zeroCell = screen.getByText('0');
    expect(zeroCell).not.toHaveClass('text-destructive');
  });
});
