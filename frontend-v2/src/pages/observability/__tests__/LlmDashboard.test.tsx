/**
 * Tests for LlmDashboard — tabs render, tab switch fetches rankings, and a
 * URL model filter propagates to the histogram request (filter → refetch).
 * MSW handlers return the real contract shapes; the default clusters handler
 * (ids 1,2) backs the cluster picker.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import LlmDashboard from '../LlmDashboard';
import type { LlmHistogram, LlmRankings, LlmFilterData } from '@/types/llm-observability';

const histogram = (metric: string): LlmHistogram => ({
  available: true,
  endpoint: 'http:loki:3100',
  updated_at: '2026-07-01T12:00:00Z',
  metric,
  step_s: 60,
  series: [
    { name: 'input', points: [{ ts: '2026-07-01T11:00:00Z', value: 100 }] },
    { name: 'cached', points: [{ ts: '2026-07-01T11:00:00Z', value: 40 }] },
  ],
  errors: {},
});

const rankings: LlmRankings = {
  available: true,
  endpoint: 'http:loki:3100',
  updated_at: '2026-07-01T12:00:00Z',
  rows: [
    {
      model: 'gpt-4o',
      provider: 'openai',
      requests: 500,
      success_rate: 0.99,
      tokens: 120000,
      cost: 8.5,
      avg_latency_ms: 700,
      trend: { requests: 0.12, tokens: -0.05, cost: null },
    },
  ],
  errors: {},
};

const filterData: LlmFilterData = {
  available: true,
  endpoint: 'http:loki:3100',
  updated_at: '2026-07-01T12:00:00Z',
  models: ['gpt-4o'],
  statuses: ['200'],
  errors: {},
};

let histogramRequests: URL[] = [];

beforeEach(() => {
  vi.restoreAllMocks();
  histogramRequests = [];
  server.use(
    http.get('*/api/k8s/clusters/:id/llm-observability/histogram', ({ request }) => {
      const url = new URL(request.url);
      histogramRequests.push(url);
      return HttpResponse.json(histogram(url.searchParams.get('metric') ?? 'requests'));
    }),
    http.get('*/api/k8s/clusters/:id/llm-observability/rankings', () => HttpResponse.json(rankings)),
    http.get('*/api/k8s/clusters/:id/llm-observability/provider-usage', ({ request }) =>
      HttpResponse.json(histogram(new URL(request.url).searchParams.get('metric') ?? 'cost')),
    ),
    http.get('*/api/k8s/clusters/:id/llm-observability/filterdata', () => HttpResponse.json(filterData)),
  );
});

describe('LlmDashboard', () => {
  it('renders the three tabs', async () => {
    render(<LlmDashboard />, { initialRoute: '/observability/ai-gateway?cluster=1' });
    expect(screen.getByRole('tab', { name: 'Overview' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Model Rankings' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Provider Usage' })).toBeInTheDocument();
  });

  it('fetches and renders rankings when the Model Rankings tab is selected', async () => {
    const user = userEvent.setup();
    render(<LlmDashboard />, { initialRoute: '/observability/ai-gateway?cluster=1' });

    await user.click(screen.getByRole('tab', { name: 'Model Rankings' }));

    // gpt-4o appears in both the stacked-bar legend and the table row.
    await waitFor(() => expect(screen.getAllByText('gpt-4o').length).toBeGreaterThan(0));
    // Trend badge for cost is null → renders "new".
    expect(screen.getAllByText('new').length).toBeGreaterThan(0);
  });

  it('propagates the URL model filter to the histogram request', async () => {
    render(<LlmDashboard />, {
      initialRoute: '/observability/ai-gateway?cluster=1&model=gpt-4o',
    });

    await waitFor(() => expect(histogramRequests.length).toBeGreaterThan(0));
    expect(histogramRequests.every((u) => u.searchParams.get('model') === 'gpt-4o')).toBe(true);
  });
});
