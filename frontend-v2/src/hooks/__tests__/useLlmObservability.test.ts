/**
 * Tests for AI Gateway Observability hooks (CT-012).
 *
 * MSW handlers return the REAL response-shape contract from the design doc
 * (§"Response shapes"), one handler per hook. Each test captures the outgoing
 * request URL and asserts the query params the hook sent.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import {
  useLlmStats,
  useLlmHistogram,
  useLlmRankings,
  useLlmProviderUsage,
  useLlmLogs,
  useLlmFilterData,
} from '../useLlmObservability';
import type {
  LlmStats,
  LlmHistogram,
  LlmRankings,
  LlmLogs,
  LlmFilterData,
} from '@/types/llm-observability';
import { server } from '@/test/mocks/server';

const CLUSTER = 5;
const base = `*/api/k8s/clusters/${CLUSTER}/llm-observability`;

beforeEach(() => {
  vi.restoreAllMocks();
});

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

const statsResponse: LlmStats = {
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

const histogramResponse: LlmHistogram = {
  available: true,
  endpoint: 'http:loki:3100',
  updated_at: '2026-07-01T12:00:00Z',
  metric: 'requests',
  step_s: 60,
  series: [
    { name: 'success', points: [{ ts: '2026-07-01T11:00:00Z', value: 100 }] },
    { name: 'error', points: [{ ts: '2026-07-01T11:00:00Z', value: 3 }] },
  ],
  errors: {},
};

const rankingsResponse: LlmRankings = {
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

const providerUsageResponse: LlmHistogram = {
  available: true,
  endpoint: 'http:loki:3100',
  updated_at: '2026-07-01T12:00:00Z',
  metric: 'cost',
  step_s: 60,
  series: [{ name: 'openai', points: [{ ts: '2026-07-01T11:00:00Z', value: 4.2 }] }],
  errors: {},
};

const logsResponse: LlmLogs = {
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
      req_body: '{"messages":[{"role":"user","content":"hi"}]}',
      resp_body: '{"choices":[{"message":{"role":"assistant","content":"hello"}}]}',
    },
  ],
  next_end: '1719835140000000000',
  errors: {},
};

const filterDataResponse: LlmFilterData = {
  available: true,
  endpoint: 'http:loki:3100',
  updated_at: '2026-07-01T12:00:00Z',
  models: ['gpt-4o', 'claude-3-5-sonnet'],
  statuses: ['200', '429', '500'],
  errors: {},
};

describe('useLlmStats', () => {
  it('fetches stats and sends range/model/status params', async () => {
    let captured: URL | undefined;
    server.use(
      http.get(`${base}/stats`, ({ request }) => {
        captured = new URL(request.url);
        return HttpResponse.json(statsResponse);
      }),
    );

    const { result } = renderHook(
      () => useLlmStats(CLUSTER, { range: '6h', model: 'gpt-4o', status: '200' }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.total_requests).toBe(1234);
    expect(result.current.data?.success_rate).toBe(0.985);
    expect(captured?.searchParams.get('range')).toBe('6h');
    expect(captured?.searchParams.get('model')).toBe('gpt-4o');
    expect(captured?.searchParams.get('status')).toBe('200');
  });

  it('fetches fleet aggregate when cluster is undefined', async () => {
    let captured: URL | undefined;
    server.use(
      http.get('*/api/k8s/llm-observability/stats', ({ request }) => {
        captured = new URL(request.url);
        return HttpResponse.json(statsResponse);
      }),
    );

    const { result } = renderHook(() => useLlmStats(undefined, { range: '1h' }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.total_requests).toBe(1234);
    expect(captured?.searchParams.get('range')).toBe('1h');
  });
});

describe('useLlmHistogram', () => {
  it('sends the metric param and returns series', async () => {
    let captured: URL | undefined;
    server.use(
      http.get(`${base}/histogram`, ({ request }) => {
        captured = new URL(request.url);
        return HttpResponse.json(histogramResponse);
      }),
    );

    const { result } = renderHook(
      () => useLlmHistogram(CLUSTER, { range: '1h', metric: 'requests' }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(captured?.searchParams.get('metric')).toBe('requests');
    expect(result.current.data?.series).toHaveLength(2);
    expect(result.current.data?.step_s).toBe(60);
  });
});

describe('useLlmRankings', () => {
  it('returns per-model rows with trend deltas', async () => {
    server.use(http.get(`${base}/rankings`, () => HttpResponse.json(rankingsResponse)));

    const { result } = renderHook(() => useLlmRankings(CLUSTER, { range: '24h' }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.rows[0].model).toBe('gpt-4o');
    expect(result.current.data?.rows[0].trend.cost).toBeNull();
    expect(result.current.data?.rows[0].trend.requests).toBe(0.12);
  });
});

describe('useLlmProviderUsage', () => {
  it('sends the metric param and returns provider-keyed series', async () => {
    let captured: URL | undefined;
    server.use(
      http.get(`${base}/provider-usage`, ({ request }) => {
        captured = new URL(request.url);
        return HttpResponse.json(providerUsageResponse);
      }),
    );

    const { result } = renderHook(
      () => useLlmProviderUsage(CLUSTER, { range: '1h', metric: 'cost' }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(captured?.searchParams.get('metric')).toBe('cost');
    expect(result.current.data?.series[0].name).toBe('openai');
  });
});

describe('useLlmLogs', () => {
  it('sends limit + content_search and returns rows with a cursor', async () => {
    let captured: URL | undefined;
    server.use(
      http.get(`${base}/logs`, ({ request }) => {
        captured = new URL(request.url);
        return HttpResponse.json(logsResponse);
      }),
    );

    const { result } = renderHook(
      () => useLlmLogs(CLUSTER, { range: '1h', limit: 50, content_search: 'france' }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(captured?.searchParams.get('limit')).toBe('50');
    expect(captured?.searchParams.get('content_search')).toBe('france');
    expect(result.current.data?.rows[0].message).toBe('What is the capital of France?');
    expect(result.current.data?.next_end).toBe('1719835140000000000');
  });
});

describe('useLlmFilterData', () => {
  it('returns distinct model + status filter values', async () => {
    server.use(http.get(`${base}/filterdata`, () => HttpResponse.json(filterDataResponse)));

    const { result } = renderHook(() => useLlmFilterData(CLUSTER, '1h'), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.models).toContain('gpt-4o');
    expect(result.current.data?.statuses).toContain('429');
  });
});
