/**
 * Tests for LlmLogs — the request table renders rows and clicking a row opens
 * the detail drawer with a transcript parsed from req_body/resp_body.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import LlmLogs from '../LlmLogs';
import type { LlmStats, LlmHistogram, LlmLogs as LlmLogsType, LlmFilterData } from '@/types/llm-observability';

const stats: LlmStats = {
  available: true,
  endpoint: 'http:loki:3100',
  updated_at: '2026-07-01T12:00:00Z',
  total_requests: 1234,
  success_rate: 0.985,
  avg_latency_ms: 842.3,
  total_tokens: 456789,
  total_cost: 12.34,
  models: 3,
  errors: {},
};

const histogram: LlmHistogram = {
  available: true,
  endpoint: 'http:loki:3100',
  updated_at: '2026-07-01T12:00:00Z',
  metric: 'requests',
  step_s: 60,
  series: [{ name: 'success', points: [{ ts: '2026-07-01T11:00:00Z', value: 100 }] }],
  errors: {},
};

const logs: LlmLogsType = {
  available: true,
  endpoint: 'http:loki:3100',
  updated_at: '2026-07-01T12:00:00Z',
  rows: [
    {
      ts: '2026-07-01T11:59:00Z',
      type: 'chat',
      message: 'What is the capital of France?',
      model: 'gpt-4o',
      latency_ms: 640,
      prompt_tk: 12,
      comp_tk: 8,
      total_tk: 20,
      cost: 0.0012,
      status: '200',
      req_body: '{"messages":[{"role":"user","content":"capital of France?"}]}',
      resp_body: '{"choices":[{"message":{"role":"assistant","content":"Paris."}}]}',
    },
  ],
  next_end: null,
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

beforeEach(() => {
  vi.restoreAllMocks();
  server.use(
    http.get('*/api/k8s/clusters/:id/llm-observability/stats', () => HttpResponse.json(stats)),
    http.get('*/api/k8s/clusters/:id/llm-observability/histogram', () => HttpResponse.json(histogram)),
    http.get('*/api/k8s/clusters/:id/llm-observability/logs', () => HttpResponse.json(logs)),
    http.get('*/api/k8s/clusters/:id/llm-observability/filterdata', () => HttpResponse.json(filterData)),
  );
});

describe('LlmLogs', () => {
  it('renders request rows and the stat strip', async () => {
    render(<LlmLogs />, { initialRoute: '/observability/ai-gateway/logs?cluster=1' });

    await waitFor(() =>
      expect(screen.getByText('What is the capital of France?')).toBeInTheDocument(),
    );
    // Success rate stat tile (0.985 → 98.5%).
    expect(screen.getByText('98.5%')).toBeInTheDocument();
  });

  it('opens the detail drawer with a parsed transcript when a row is clicked', async () => {
    render(<LlmLogs />, { initialRoute: '/observability/ai-gateway/logs?cluster=1' });

    const cell = await screen.findByText('What is the capital of France?');
    fireEvent.click(cell);

    // Drawer shows the parsed transcript turns (request + response).
    await waitFor(() => expect(screen.getByText('capital of France?')).toBeInTheDocument());
    expect(screen.getByText('Paris.')).toBeInTheDocument();
    expect(screen.getByText('Transcript')).toBeInTheDocument();
  });
});
