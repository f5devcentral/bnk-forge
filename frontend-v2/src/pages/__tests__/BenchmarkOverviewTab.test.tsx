/**
 * Tests for BenchmarkOverviewTab — the run-first landing dashboard.
 *
 * Uses MSW with the REAL response shapes (BenchmarkTargetListResponse,
 * BenchmarkAgent[], BenchmarkConfig[], BenchmarkSummaryResponse,
 * BenchmarkRunListResponse) per the CT-012 pattern.
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { BenchmarkOverviewTab } from '@/pages/BenchmarkOverviewTab';

const now = '2026-07-20T10:00:00Z';

function mockRun(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    tool: 'aiperf',
    proxy: 'envoy',
    model: 'tinyllama',
    base_url: 'http://envoy:10080',
    run_label: 'nightly-envoy',
    tags: null,
    config_id: null,
    agent_id: 1,
    target_id: 1,
    status: 'completed',
    error_message: null,
    is_baseline: false,
    baseline_latency_p99: null,
    baseline_overall_rps: null,
    duration_seconds: 60,
    total_requests: 1000,
    successful_requests: 1000,
    failed_requests: 0,
    success_rate_pct: 100,
    latency_p50: 0.05,
    latency_p99: 0.1,
    overall_rps: 50,
    peak_rps: 60,
    tokens_per_sec: 500,
    total_output_tokens: 20000,
    started_at: now,
    completed_at: now,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

function mockSummary(overrides: Record<string, unknown> = {}) {
  return {
    total_runs: 12,
    completed_runs: 10,
    failed_runs: 2,
    running_count: 0,
    avg_latency_p50: 0.05,
    avg_rps: 42,
    avg_success_rate: 98.5,
    last_run_at: now,
    runs_last_7d: 4,
    runs_by_proxy: [],
    runs_by_tool: [],
    ...overrides,
  };
}

function setupHandlers(opts: {
  targets?: unknown[];
  agents?: unknown[];
  configs?: unknown[];
  runs?: unknown[];
} = {}) {
  const targets = opts.targets ?? [];
  const agents = opts.agents ?? [];
  const configs = opts.configs ?? [];
  const runs = opts.runs ?? [];

  server.use(
    http.get('*/api/benchmarks/targets', () =>
      HttpResponse.json({ targets, total: targets.length })),
    http.get('*/api/benchmarks/agents', () => HttpResponse.json(agents)),
    http.get('*/api/benchmarks/configs', () => HttpResponse.json(configs)),
    http.get('*/api/benchmarks/summary', () => HttpResponse.json(mockSummary())),
    http.get('*/api/benchmarks/runs', () =>
      HttpResponse.json({ runs, total: runs.length, limit: 5, offset: 0 })),
    http.get('*/api/benchmarks/trends', () =>
      HttpResponse.json({ points: [], baseline_run_id: null })),
  );
}

const noopProps = {
  onGoToSetup: vi.fn(),
  onGoToRun: vi.fn(),
  onGoToRunsList: vi.fn(),
  onGoToTrends: vi.fn(),
  onOpenWizard: vi.fn(),
};

describe('BenchmarkOverviewTab', () => {
  it('shows a setup-oriented empty state when there are no targets and no agents', async () => {
    setupHandlers({ targets: [], agents: [], configs: [], runs: [] });
    const props = { ...noopProps, onGoToSetup: vi.fn() };

    render(<BenchmarkOverviewTab {...props} />);

    await waitFor(() => expect(screen.getByText("Let's get your first benchmark running")).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /go to setup/i }));
    expect(props.onGoToSetup).toHaveBeenCalledWith('targets');
  });

  it('renders readiness counts and links a zero-state row to its Setup sub-section', async () => {
    setupHandlers({
      targets: [{ id: 1, name: 't1', last_validated: now, proxy_count: 1 }],
      agents: [{ id: 1, name: 'a1', status: 'connected', hostname: null, ip_address: null, tags: null, capabilities: null, last_heartbeat: now, created_at: now, updated_at: now }],
      configs: [],
      runs: [],
    });
    const props = { ...noopProps, onGoToSetup: vi.fn() };

    render(<BenchmarkOverviewTab {...props} />);

    await waitFor(() => expect(screen.getByText('Readiness')).toBeInTheDocument());
    expect(screen.getByText('1 (1 validated)')).toBeInTheDocument();
    expect(screen.getByText('1 connected')).toBeInTheDocument();

    await userEvent.click(screen.getByText('Configs'));
    expect(props.onGoToSetup).toHaveBeenCalledWith('configs');
  });

  it('renders the last run + recent runs and navigates to run detail on click', async () => {
    setupHandlers({
      targets: [{ id: 1, name: 't1', last_validated: now, proxy_count: 1 }],
      agents: [{ id: 1, name: 'a1', status: 'connected', hostname: null, ip_address: null, tags: null, capabilities: null, last_heartbeat: now, created_at: now, updated_at: now }],
      configs: [],
      runs: [mockRun({ id: 99, run_label: 'nightly-envoy' })],
    });
    const props = { ...noopProps, onGoToRun: vi.fn() };

    render(<BenchmarkOverviewTab {...props} />);

    await waitFor(() => expect(screen.getAllByText('nightly-envoy').length).toBeGreaterThan(0));
    await userEvent.click(screen.getAllByText('nightly-envoy')[0]);
    expect(props.onGoToRun).toHaveBeenCalledWith(99);
  });

  it('"View all runs" navigates to the Runs list', async () => {
    setupHandlers({
      targets: [{ id: 1, name: 't1', last_validated: now, proxy_count: 1 }],
      agents: [{ id: 1, name: 'a1', status: 'connected', hostname: null, ip_address: null, tags: null, capabilities: null, last_heartbeat: now, created_at: now, updated_at: now }],
      configs: [],
      runs: [mockRun({ id: 1 })],
    });
    const props = { ...noopProps, onGoToRunsList: vi.fn() };

    render(<BenchmarkOverviewTab {...props} />);

    await waitFor(() => expect(screen.getByText('View all runs')).toBeInTheDocument());
    await userEvent.click(screen.getByText('View all runs'));
    expect(props.onGoToRunsList).toHaveBeenCalled();
  });
});
