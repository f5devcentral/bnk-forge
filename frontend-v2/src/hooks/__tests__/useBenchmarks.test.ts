/**
 * Tests for useBenchmarks hooks (CT-012 pattern)
 *
 * Covers all 27 hooks:
 *   - Config: list, detail, create, update, delete (5)
 *   - Runs: list, detail, create, cancel, delete (5)
 *   - Agents: list, delete (2)
 *   - Comparison & Summary (2)
 *   - Targets: list, detail, create, update, delete, validate (6)
 *   - Proxy deployments: list, deploy, update, delete, redeploy (5)
 *   - Discovery: proxies, targets (2)
 *   - Orchestration: trigger run (1)
 *
 * Each mutation test captures the request payload and verifies it matches
 * the backend Pydantic schema (CT-012 contract testing pattern).
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useBenchmarkConfigs,
  useBenchmarkConfig,
  useCreateBenchmarkConfig,
  useUpdateBenchmarkConfig,
  useDeleteBenchmarkConfig,
  useBenchmarkRuns,
  useBenchmarkRun,
  useCreateBenchmarkRun,
  useCancelBenchmarkRun,
  useDeleteBenchmarkRun,
  useBenchmarkAgents,
  useDeleteBenchmarkAgent,
  useBenchmarkCompare,
  useBenchmarkSummary,
  useBenchmarkTargets,
  useBenchmarkTarget,
  useCreateBenchmarkTarget,
  useUpdateBenchmarkTarget,
  useDeleteBenchmarkTarget,
  useValidateBenchmarkTarget,
  useProxyDeployments,
  useDeployProxy,
  useUpdateProxyDeployment,
  useDeleteProxyDeployment,
  useRedeployProxy,
  useDiscoverProxies,
  useDiscoverTargets,
  useTriggerRun,
  useScenarios,
  useRunScenario,
  useRunGroup,
} from '@/hooks/useBenchmarks';
import React from 'react';

// ============================================================================
// Test Infrastructure
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

// ============================================================================
// Mock Response Factories — match backend Pydantic response_model shapes
// ============================================================================

const now = '2026-03-19T10:00:00Z';

function mockConfigResponse(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    name: 'test-config',
    description: null,
    tool: 'aiperf',
    config_json: { url: 'http://localhost:8000', request_count: 10 },
    created_by: 'admin',
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

function mockRunResponse(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    tool: 'aiperf',
    proxy: 'nodeport',
    model: 'tinyllama',
    base_url: 'http://vllm:8000',
    run_label: null,
    tags: null,
    config_id: null,
    agent_id: null,
    status: 'completed',
    error_message: null,
    duration_seconds: 60.0,
    total_requests: 100,
    successful_requests: 95,
    failed_requests: 5,
    success_rate_pct: 95.0,
    latency_p50: 0.05,
    latency_p99: 0.15,
    overall_rps: 10.0,
    peak_rps: 15.0,
    tokens_per_sec: 500.0,
    total_output_tokens: 10000,
    started_at: now,
    completed_at: now,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

function mockRunDetailResponse(overrides: Record<string, unknown> = {}) {
  return {
    ...mockRunResponse(),
    result_json: { some: 'data' },
    config_snapshot: { url: 'http://localhost:8000' },
    config: null,
    agent: null,
    ...overrides,
  };
}

function mockAgentResponse(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    name: 'agent-1',
    hostname: 'bench-host',
    ip_address: '10.0.0.1',
    tags: null,
    capabilities: null,
    status: 'connected',
    last_heartbeat: now,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

function mockTargetResponse(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    name: 'target-a',
    description: null,
    cluster_id: 10,
    llm_base_url: 'http://vllm.default:8000',
    llm_model: 'tinyllama',
    llm_namespace: 'default',
    llm_endpoint: '/v1/chat/completions',
    proxy_namespace: 'perf-proxies',
    status: 'active',
    last_validated: null,
    validation_msg: null,
    tags: null,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

function mockTargetDetailResponse(overrides: Record<string, unknown> = {}) {
  return {
    ...mockTargetResponse(),
    proxy_deployments: [],
    ...overrides,
  };
}

function mockProxyResponse(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    target_id: 1,
    proxy_type: 'envoy',
    helm_release: 'perf-envoy-target-a',
    helm_chart: 'oci://docker.io/envoyproxy/gateway-helm',
    helm_version: 'v1.7.1',
    helm_values: null,
    proxy_url: 'http://envoy.perf-proxies:10080',
    external_url: null,
    status: 'ready',
    status_message: 'Proxy ready',
    celery_task_id: null,
    deployed_at: now,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

function mockSummaryResponse() {
  return {
    total_runs: 10,
    completed_runs: 8,
    failed_runs: 1,
    running_count: 1,
    avg_latency_p50: 0.05,
    avg_rps: 100.0,
    avg_success_rate: 95.0,
    last_run_at: now,
    runs_last_7d: 5,
    runs_by_proxy: [{ proxy: 'envoy', count: 5, avg_p50: 0.04, avg_rps: 120.0 }],
    runs_by_tool: [{ tool: 'aiperf', count: 10 }],
  };
}

function mockScenarioCatalogResponse() {
  // Matches ScenarioCatalogResponse / ScenarioCatalogItem (api-generated.ts)
  return {
    scenarios: [
      {
        key: 'prefix-cache',
        name: 'Prefix Cache Sweep',
        description: 'Concurrency sweep with shared prefixes',
        trace_driven: false,
        child_run_count: 4,
        tags: ['cache'],
      },
      {
        key: 'mooncake',
        name: 'Mooncake Production Trace',
        description: 'Replay of the Mooncake production trace',
        trace_driven: true,
        child_run_count: 3,
        tags: ['trace'],
      },
    ],
    total: 2,
  };
}

function mockRunGroupChild(overrides: Record<string, unknown> = {}) {
  // Matches RunGroupChildSummary
  return {
    id: 101,
    variant_label: 'c=8',
    status: 'completed',
    concurrency: 8,
    latency_p50: 0.05,
    latency_p99: 0.15,
    overall_rps: 10.0,
    tokens_per_sec: 500.0,
    ...overrides,
  };
}

function mockRunGroupResponse(overrides: Record<string, unknown> = {}) {
  // Matches RunGroupResponse
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
    runs: [mockRunGroupChild(), mockRunGroupChild({ id: 102, variant_label: 'c=16', concurrency: 16 })],
    ...overrides,
  };
}

function mockCompareResponse() {
  return {
    runs: [
      {
        run_id: 1,
        proxy: 'envoy',
        model: 'tinyllama',
        tool: 'aiperf',
        run_label: null,
        status: 'completed',
        total_requests: 100,
        success_rate_pct: 95.0,
        latency_p50: 0.05,
        latency_p99: 0.15,
        overall_rps: 10.0,
        peak_rps: 15.0,
        tokens_per_sec: 500.0,
        duration_seconds: 60.0,
      },
      {
        run_id: 2,
        proxy: 'nginx',
        model: 'tinyllama',
        tool: 'aiperf',
        run_label: null,
        status: 'completed',
        total_requests: 100,
        success_rate_pct: 94.0,
        latency_p50: 0.06,
        latency_p99: 0.18,
        overall_rps: 9.0,
        peak_rps: 13.0,
        tokens_per_sec: 450.0,
        duration_seconds: 65.0,
      },
    ],
    winners: { latency_p50: 1, overall_rps: 1 },
  };
}

// ============================================================================
// 1. Config Queries / Mutations
// ============================================================================

describe('useBenchmarkConfigs', () => {
  it('fetches config list', async () => {
    server.use(
      http.get('*/api/benchmarks/configs', () =>
        HttpResponse.json([mockConfigResponse(), mockConfigResponse({ id: 2, name: 'cfg-2' })])
      )
    );

    const { result } = renderHook(() => useBenchmarkConfigs(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(2);
    expect(result.current.data![0]).toMatchObject({ id: 1, name: 'test-config', tool: 'aiperf' });
  });
});

describe('useBenchmarkConfig', () => {
  it('fetches single config by id', async () => {
    server.use(
      http.get('*/api/benchmarks/configs/:configId', () =>
        HttpResponse.json(mockConfigResponse())
      )
    );

    const { result } = renderHook(() => useBenchmarkConfig(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toMatchObject({ id: 1, name: 'test-config' });
  });

  it('disabled when configId is undefined', () => {
    const { result } = renderHook(() => useBenchmarkConfig(undefined), {
      wrapper: createWrapper(),
    });
    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useCreateBenchmarkConfig', () => {
  it('sends correct payload matching BenchmarkConfigCreate schema', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/benchmarks/configs', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(mockConfigResponse({ id: 5, name: 'new-cfg' }), { status: 201 });
      })
    );

    const { result } = renderHook(() => useCreateBenchmarkConfig(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      name: 'new-cfg',
      tool: 'aiperf',
      config_json: { url: 'http://vllm:8000', request_count: 50 },
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // CT-012: Verify payload matches BenchmarkConfigCreate Pydantic schema
    expect(capturedBody).toMatchObject({
      name: 'new-cfg',
      tool: 'aiperf',
      config_json: { url: 'http://vllm:8000', request_count: 50 },
    });
    // No wrapping
    expect(capturedBody).not.toHaveProperty('data');
    expect(capturedBody).not.toHaveProperty('request');
  });

  it('handles server error', async () => {
    server.use(
      http.post('*/api/benchmarks/configs', () =>
        HttpResponse.json({ error: 'conflict' }, { status: 409 })
      )
    );

    const { result } = renderHook(() => useCreateBenchmarkConfig(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ name: 'dup', config_json: { url: 'x' } });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe('useUpdateBenchmarkConfig', () => {
  it('sends correct payload matching BenchmarkConfigUpdate schema', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/benchmarks/configs/:configId', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(mockConfigResponse({ name: 'updated-cfg' }));
      })
    );

    const { result } = renderHook(() => useUpdateBenchmarkConfig(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      configId: 1,
      data: { name: 'updated-cfg', config_json: { url: 'http://new' } },
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // CT-012: payload matches BenchmarkConfigUpdate (all optional)
    expect(capturedBody).toMatchObject({
      name: 'updated-cfg',
      config_json: { url: 'http://new' },
    });
    expect(capturedBody).not.toHaveProperty('configId');
  });
});

describe('useDeleteBenchmarkConfig', () => {
  it('calls DELETE endpoint', async () => {
    let deleteCalled = false;
    server.use(
      http.delete('*/api/benchmarks/configs/:configId', () => {
        deleteCalled = true;
        return new HttpResponse(null, { status: 204 });
      })
    );

    const { result } = renderHook(() => useDeleteBenchmarkConfig(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(deleteCalled).toBe(true);
  });
});

// ============================================================================
// 2. Run Queries / Mutations
// ============================================================================

describe('useBenchmarkRuns', () => {
  it('fetches paginated run list', async () => {
    server.use(
      http.get('*/api/benchmarks/runs', () =>
        HttpResponse.json({
          runs: [mockRunResponse()],
          total: 1,
          limit: 50,
          offset: 0,
        })
      )
    );

    const { result } = renderHook(() => useBenchmarkRuns(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toMatchObject({
      runs: expect.arrayContaining([expect.objectContaining({ id: 1, proxy: 'nodeport' })]),
      total: 1,
      limit: 50,
      offset: 0,
    });
  });
});

describe('useBenchmarkRun', () => {
  it('fetches single run detail', async () => {
    server.use(
      http.get('*/api/benchmarks/runs/:runId', () =>
        HttpResponse.json(mockRunDetailResponse())
      )
    );

    const { result } = renderHook(() => useBenchmarkRun(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toMatchObject({
      id: 1,
      result_json: { some: 'data' },
      config_snapshot: { url: 'http://localhost:8000' },
    });
  });

  it('disabled when runId is undefined', () => {
    const { result } = renderHook(() => useBenchmarkRun(undefined), {
      wrapper: createWrapper(),
    });
    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useCreateBenchmarkRun', () => {
  it('sends correct payload matching BenchmarkRunCreate schema', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/benchmarks/runs', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          mockRunResponse({ id: 5, status: 'pending', model: 'tinyllama' }),
          { status: 201 }
        );
      })
    );

    const { result } = renderHook(() => useCreateBenchmarkRun(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      model: 'tinyllama',
      base_url: 'http://vllm:8000',
      proxy: 'envoy',
      tool: 'aiperf',
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // CT-012: matches BenchmarkRunCreate — model and base_url required
    expect(capturedBody).toMatchObject({
      model: 'tinyllama',
      base_url: 'http://vllm:8000',
      proxy: 'envoy',
      tool: 'aiperf',
    });
    expect(capturedBody).not.toHaveProperty('data');
  });
});

describe('useCancelBenchmarkRun', () => {
  it('calls POST cancel endpoint', async () => {
    let cancelUrl = '';
    server.use(
      http.post('*/api/benchmarks/runs/:runId/cancel', ({ request }) => {
        cancelUrl = new URL(request.url).pathname;
        return HttpResponse.json(mockRunResponse({ status: 'cancelled' }));
      })
    );

    const { result } = renderHook(() => useCancelBenchmarkRun(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(42);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(cancelUrl).toBe('/api/benchmarks/runs/42/cancel');
    expect(result.current.data).toMatchObject({ status: 'cancelled' });
  });
});

describe('useDeleteBenchmarkRun', () => {
  it('calls DELETE endpoint', async () => {
    let deleteUrl = '';
    server.use(
      http.delete('*/api/benchmarks/runs/:runId', ({ request }) => {
        deleteUrl = new URL(request.url).pathname;
        return new HttpResponse(null, { status: 204 });
      })
    );

    const { result } = renderHook(() => useDeleteBenchmarkRun(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(7);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(deleteUrl).toBe('/api/benchmarks/runs/7');
  });
});

// ============================================================================
// 3. Agent Queries / Mutations
// ============================================================================

describe('useBenchmarkAgents', () => {
  it('fetches agent list', async () => {
    server.use(
      http.get('*/api/benchmarks/agents', () =>
        HttpResponse.json([mockAgentResponse(), mockAgentResponse({ id: 2, name: 'agent-2' })])
      )
    );

    const { result } = renderHook(() => useBenchmarkAgents(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(2);
  });
});

describe('useDeleteBenchmarkAgent', () => {
  it('calls DELETE endpoint', async () => {
    let deleteUrl = '';
    server.use(
      http.delete('*/api/benchmarks/agents/:agentId', ({ request }) => {
        deleteUrl = new URL(request.url).pathname;
        return new HttpResponse(null, { status: 204 });
      })
    );

    const { result } = renderHook(() => useDeleteBenchmarkAgent(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(3);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(deleteUrl).toBe('/api/benchmarks/agents/3');
  });
});

// ============================================================================
// 4. Comparison & Summary
// ============================================================================

describe('useBenchmarkCompare', () => {
  it('fetches comparison when 2+ run IDs provided', async () => {
    let capturedUrl = '';
    server.use(
      http.post('*/api/benchmarks/compare', async ({ request }) => {
        capturedUrl = new URL(request.url).pathname;
        const body = (await request.json()) as Record<string, unknown>;
        expect(body).toMatchObject({ run_ids: [1, 2] });
        return HttpResponse.json(mockCompareResponse());
      })
    );

    const { result } = renderHook(() => useBenchmarkCompare([1, 2]), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toMatchObject({
      runs: expect.arrayContaining([expect.objectContaining({ run_id: 1 })]),
      winners: expect.objectContaining({ latency_p50: 1 }),
    });
  });

  it('disabled when fewer than 2 run IDs', () => {
    server.use(
      http.post('*/api/benchmarks/compare', () =>
        HttpResponse.json(mockCompareResponse())
      )
    );

    const { result } = renderHook(() => useBenchmarkCompare([1]), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useBenchmarkSummary', () => {
  it('fetches dashboard summary', async () => {
    server.use(
      http.get('*/api/benchmarks/summary', () =>
        HttpResponse.json(mockSummaryResponse())
      )
    );

    const { result } = renderHook(() => useBenchmarkSummary(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toMatchObject({
      total_runs: 10,
      completed_runs: 8,
      failed_runs: 1,
      running_count: 1,
      runs_by_proxy: expect.arrayContaining([
        expect.objectContaining({ proxy: 'envoy', count: 5 }),
      ]),
    });
  });
});

// ============================================================================
// 5. Target Queries / Mutations
// ============================================================================

describe('useBenchmarkTargets', () => {
  it('fetches target list', async () => {
    server.use(
      http.get('*/api/benchmarks/targets', () =>
        HttpResponse.json({
          targets: [mockTargetResponse()],
          total: 1,
        })
      )
    );

    const { result } = renderHook(() => useBenchmarkTargets(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toMatchObject({
      targets: [expect.objectContaining({ id: 1, name: 'target-a', status: 'active' })],
      total: 1,
    });
  });
});

describe('useBenchmarkTarget', () => {
  it('fetches single target detail with proxy_deployments', async () => {
    server.use(
      http.get('*/api/benchmarks/targets/:targetId', () =>
        HttpResponse.json(mockTargetDetailResponse({
          proxy_deployments: [mockProxyResponse()],
        }))
      )
    );

    const { result } = renderHook(() => useBenchmarkTarget(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toMatchObject({
      id: 1,
      proxy_deployments: [expect.objectContaining({ proxy_type: 'envoy' })],
    });
  });

  it('disabled when targetId is undefined', () => {
    const { result } = renderHook(() => useBenchmarkTarget(undefined), {
      wrapper: createWrapper(),
    });
    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useCreateBenchmarkTarget', () => {
  it('sends correct payload matching BenchmarkTargetCreate schema', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/benchmarks/targets', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          mockTargetResponse({ id: 5, name: 'new-target' }),
          { status: 201 }
        );
      })
    );

    const { result } = renderHook(() => useCreateBenchmarkTarget(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      name: 'new-target',
      cluster_id: 10,
      llm_base_url: 'http://vllm:8000',
      llm_model: 'tinyllama',
      llm_namespace: 'ml',
      proxy_namespace: 'perf-proxies',
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // CT-012: matches BenchmarkTargetCreate — name, cluster_id, llm_base_url, llm_model required
    expect(capturedBody).toMatchObject({
      name: 'new-target',
      cluster_id: 10,
      llm_base_url: 'http://vllm:8000',
      llm_model: 'tinyllama',
      llm_namespace: 'ml',
      proxy_namespace: 'perf-proxies',
    });
    expect(capturedBody).not.toHaveProperty('data');
    expect(capturedBody).not.toHaveProperty('request');
  });
});

describe('useUpdateBenchmarkTarget', () => {
  it('sends correct payload matching BenchmarkTargetUpdate schema', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    let capturedUrl = '';
    server.use(
      http.put('*/api/benchmarks/targets/:targetId', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        capturedUrl = new URL(request.url).pathname;
        return HttpResponse.json(mockTargetResponse({ name: 'updated' }));
      })
    );

    const { result } = renderHook(() => useUpdateBenchmarkTarget(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      targetId: 1,
      data: { name: 'updated', llm_model: 'llama-70b' },
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(capturedUrl).toBe('/api/benchmarks/targets/1');
    // CT-012: BenchmarkTargetUpdate — all optional
    expect(capturedBody).toMatchObject({
      name: 'updated',
      llm_model: 'llama-70b',
    });
    expect(capturedBody).not.toHaveProperty('targetId');
  });
});

describe('useDeleteBenchmarkTarget', () => {
  it('calls DELETE endpoint', async () => {
    let deleteUrl = '';
    server.use(
      http.delete('*/api/benchmarks/targets/:targetId', ({ request }) => {
        deleteUrl = new URL(request.url).pathname;
        return new HttpResponse(null, { status: 204 });
      })
    );

    const { result } = renderHook(() => useDeleteBenchmarkTarget(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(5);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(deleteUrl).toBe('/api/benchmarks/targets/5');
  });
});

describe('useValidateBenchmarkTarget', () => {
  it('calls POST validate endpoint', async () => {
    let validateUrl = '';
    server.use(
      http.post('*/api/benchmarks/targets/:targetId/validate', ({ request }) => {
        validateUrl = new URL(request.url).pathname;
        return HttpResponse.json(
          mockTargetResponse({ status: 'active', last_validated: now })
        );
      })
    );

    const { result } = renderHook(() => useValidateBenchmarkTarget(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(validateUrl).toBe('/api/benchmarks/targets/1/validate');
    expect(result.current.data).toMatchObject({ status: 'active' });
  });
});

// ============================================================================
// 6. Proxy Deployment Queries / Mutations
// ============================================================================

describe('useProxyDeployments', () => {
  it('fetches proxy list for target', async () => {
    server.use(
      http.get('*/api/benchmarks/targets/:targetId/proxies', () =>
        HttpResponse.json([mockProxyResponse(), mockProxyResponse({ id: 2, proxy_type: 'nginx' })])
      )
    );

    const { result } = renderHook(() => useProxyDeployments(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(2);
    expect(result.current.data![0]).toMatchObject({ proxy_type: 'envoy' });
  });

  it('disabled when targetId is undefined', () => {
    const { result } = renderHook(() => useProxyDeployments(undefined), {
      wrapper: createWrapper(),
    });
    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useDeployProxy', () => {
  it('sends correct payload matching ProxyDeployRequest schema', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    let capturedUrl = '';
    server.use(
      http.post('*/api/benchmarks/targets/:targetId/proxies', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        capturedUrl = new URL(request.url).pathname;
        return HttpResponse.json(
          mockProxyResponse({ id: 3, proxy_type: 'envoy', status: 'pending' }),
          { status: 201 }
        );
      })
    );

    const { result } = renderHook(() => useDeployProxy(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      targetId: 1,
      data: { proxy_type: 'envoy', helm_values: { replicas: 2 } },
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(capturedUrl).toBe('/api/benchmarks/targets/1/proxies');
    // CT-012: matches ProxyDeployRequest — proxy_type required
    expect(capturedBody).toMatchObject({
      proxy_type: 'envoy',
      helm_values: { replicas: 2 },
    });
    expect(capturedBody).not.toHaveProperty('targetId');
    expect(capturedBody).not.toHaveProperty('data');
  });
});

describe('useUpdateProxyDeployment', () => {
  it('sends correct payload matching ProxyDeploymentUpdate schema', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    let capturedUrl = '';
    server.use(
      http.put('*/api/benchmarks/targets/:targetId/proxies/:proxyId', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        capturedUrl = new URL(request.url).pathname;
        return HttpResponse.json(
          mockProxyResponse({ proxy_url: 'http://new-envoy:10080' })
        );
      })
    );

    const { result } = renderHook(() => useUpdateProxyDeployment(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      targetId: 1,
      proxyId: 2,
      data: { proxy_url: 'http://new-envoy:10080', status: 'ready' },
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(capturedUrl).toBe('/api/benchmarks/targets/1/proxies/2');
    // CT-012: matches ProxyDeploymentUpdate — all optional
    expect(capturedBody).toMatchObject({
      proxy_url: 'http://new-envoy:10080',
      status: 'ready',
    });
    expect(capturedBody).not.toHaveProperty('targetId');
    expect(capturedBody).not.toHaveProperty('proxyId');
  });
});

describe('useDeleteProxyDeployment', () => {
  it('calls DELETE endpoint (returns 202)', async () => {
    let deleteUrl = '';
    server.use(
      http.delete('*/api/benchmarks/targets/:targetId/proxies/:proxyId', ({ request }) => {
        deleteUrl = new URL(request.url).pathname;
        return new HttpResponse(null, { status: 202 });
      })
    );

    const { result } = renderHook(() => useDeleteProxyDeployment(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ targetId: 1, proxyId: 3 });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(deleteUrl).toBe('/api/benchmarks/targets/1/proxies/3');
  });
});

describe('useRedeployProxy', () => {
  it('calls POST redeploy endpoint', async () => {
    let redeployUrl = '';
    server.use(
      http.post('*/api/benchmarks/targets/:targetId/proxies/:proxyId/redeploy', ({ request }) => {
        redeployUrl = new URL(request.url).pathname;
        return HttpResponse.json(mockProxyResponse({ status: 'pending' }));
      })
    );

    const { result } = renderHook(() => useRedeployProxy(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ targetId: 1, proxyId: 2 });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(redeployUrl).toBe('/api/benchmarks/targets/1/proxies/2/redeploy');
    expect(result.current.data).toMatchObject({ status: 'pending' });
  });
});

// ============================================================================
// 7. Discovery
// ============================================================================

describe('useDiscoverProxies', () => {
  it('calls POST discover-proxies and returns response contract', async () => {
    let discoverUrl = '';
    server.use(
      http.post('*/api/benchmarks/targets/:targetId/discover-proxies', ({ request }) => {
        discoverUrl = new URL(request.url).pathname;
        return HttpResponse.json({
          target_id: 1,
          target_name: 'target-a',
          cluster_id: 10,
          results: [
            { proxy_type: 'envoy', found: true, proxy_url: 'http://envoy:10080' },
            { proxy_type: 'nginx', found: false },
          ],
          discovered_count: 1,
          total_scanned: 2,
        });
      })
    );

    const { result } = renderHook(() => useDiscoverProxies(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(discoverUrl).toBe('/api/benchmarks/targets/1/discover-proxies');
    expect(result.current.data).toMatchObject({
      target_id: 1,
      discovered_count: 1,
      total_scanned: 2,
    });
  });
});

describe('useDiscoverTargets', () => {
  it('sends correct payload matching DiscoverTargetsRequest schema', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/benchmarks/discover-targets', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          cluster_id: 10,
          cluster_name: 'test-cluster',
          discovered_services: [],
          discovered_count: 0,
          created_targets: [],
          proxy_results: [],
          created_configs: [],
        });
      })
    );

    const { result } = renderHook(() => useDiscoverTargets(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      cluster_id: 10,
      auto_create: true,
      selected_services: ['http://vllm:8000'],
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // CT-012: matches DiscoverTargetsRequest — cluster_id required
    expect(capturedBody).toMatchObject({
      cluster_id: 10,
      auto_create: true,
      selected_services: ['http://vllm:8000'],
    });
    expect(capturedBody).not.toHaveProperty('data');
  });
});

// ============================================================================
// 8. Run Orchestration
// ============================================================================

describe('useTriggerRun', () => {
  it('sends correct payload matching TriggerRunRequest schema', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    let capturedUrl = '';
    server.use(
      http.post('*/api/benchmarks/targets/:targetId/proxies/:proxyId/run', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        capturedUrl = new URL(request.url).pathname;
        return HttpResponse.json(
          {
            run_id: 42,
            agent_id: 1,
            proxy_id: 2,
            target_id: 3,
            status: 'running',
            message: 'Run #42 dispatched to agent',
          },
          { status: 201 }
        );
      })
    );

    const { result } = renderHook(() => useTriggerRun(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      targetId: 3,
      proxyId: 2,
      data: {
        agent_id: 1,
        total_requests: 100,
        max_tokens: 256,
        timeout: 600,
        run_label: 'perf-test-1',
      },
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(capturedUrl).toBe('/api/benchmarks/targets/3/proxies/2/run');
    // CT-012: matches TriggerRunRequest — all fields optional
    expect(capturedBody).toMatchObject({
      agent_id: 1,
      total_requests: 100,
      max_tokens: 256,
      timeout: 600,
      run_label: 'perf-test-1',
    });
    expect(capturedBody).not.toHaveProperty('targetId');
    expect(capturedBody).not.toHaveProperty('proxyId');
    expect(capturedBody).not.toHaveProperty('data');

    // Verify response matches TriggerRunResponse schema
    expect(result.current.data).toMatchObject({
      run_id: 42,
      agent_id: 1,
      proxy_id: 2,
      target_id: 3,
      status: 'running',
      message: expect.any(String),
    });
  });

  it('handles error response', async () => {
    server.use(
      http.post('*/api/benchmarks/targets/:targetId/proxies/:proxyId/run', () =>
        HttpResponse.json({ error: 'Proxy not ready' }, { status: 400 })
      )
    );

    const { result } = renderHook(() => useTriggerRun(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      targetId: 1,
      proxyId: 2,
      data: { agent_id: 1 },
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ============================================================================
// 8b. Scenarios + Run-Groups
// ============================================================================

describe('useScenarios', () => {
  it('fetches scenario catalog matching ScenarioCatalogResponse', async () => {
    server.use(
      http.get('*/api/benchmarks/scenarios', () =>
        HttpResponse.json(mockScenarioCatalogResponse())
      )
    );

    const { result } = renderHook(() => useScenarios(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toMatchObject({ total: 2 });
    expect(result.current.data!.scenarios).toHaveLength(2);
    expect(result.current.data!.scenarios[0]).toMatchObject({
      key: 'prefix-cache',
      name: 'Prefix Cache Sweep',
      trace_driven: false,
      child_run_count: 4,
    });
    expect(result.current.data!.scenarios[1]).toMatchObject({
      key: 'mooncake',
      trace_driven: true,
    });
  });
});

describe('useRunScenario', () => {
  it('sends correct payload matching ScenarioRunRequest schema', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    let capturedUrl = '';
    server.use(
      http.post('*/api/benchmarks/targets/:targetId/proxies/:proxyId/run-scenario', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        capturedUrl = new URL(request.url).pathname;
        return HttpResponse.json(
          {
            run_group_id: 7,
            scenario_key: 'prefix-cache',
            agent_id: 1,
            proxy_id: 2,
            target_id: 3,
            status: 'running',
            total_runs: 4,
            dispatched_runs: 4,
            message: 'Scenario expanded into run-group #7 (4 runs dispatched)',
          },
          { status: 201 }
        );
      })
    );

    const { result } = renderHook(() => useRunScenario(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      targetId: 3,
      proxyId: 2,
      data: {
        scenario_key: 'prefix-cache',
        agent_id: 1,
        run_label: 'prefix-cache-envoy',
      },
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(capturedUrl).toBe('/api/benchmarks/targets/3/proxies/2/run-scenario');
    // CT-012: payload matches ScenarioRunRequest — scenario_key required
    expect(capturedBody).toMatchObject({
      scenario_key: 'prefix-cache',
      agent_id: 1,
      run_label: 'prefix-cache-envoy',
    });
    expect(capturedBody).not.toHaveProperty('targetId');
    expect(capturedBody).not.toHaveProperty('proxyId');
    expect(capturedBody).not.toHaveProperty('data');

    // Response matches ScenarioRunResponse
    expect(result.current.data).toMatchObject({
      run_group_id: 7,
      scenario_key: 'prefix-cache',
      total_runs: 4,
      dispatched_runs: 4,
    });
  });

  it('handles error response', async () => {
    server.use(
      http.post('*/api/benchmarks/targets/:targetId/proxies/:proxyId/run-scenario', () =>
        HttpResponse.json({ error: 'No connected agent' }, { status: 400 })
      )
    );

    const { result } = renderHook(() => useRunScenario(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ targetId: 1, proxyId: 2, data: { scenario_key: 'prefix-cache' } });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe('useRunGroup', () => {
  it('fetches run-group detail with child runs', async () => {
    server.use(
      http.get('*/api/benchmarks/run-groups/:groupId', () =>
        HttpResponse.json(mockRunGroupResponse())
      )
    );

    const { result } = renderHook(() => useRunGroup(7), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toMatchObject({
      id: 7,
      scenario_key: 'prefix-cache',
      status: 'completed',
      total_runs: 2,
      avg_latency_p50: 0.05,
      peak_rps: 15.0,
    });
    expect(result.current.data!.runs).toHaveLength(2);
    expect(result.current.data!.runs![0]).toMatchObject({
      id: 101,
      variant_label: 'c=8',
      concurrency: 8,
      status: 'completed',
    });
  });

  it('disabled when groupId is undefined', () => {
    const { result } = renderHook(() => useRunGroup(undefined), {
      wrapper: createWrapper(),
    });
    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// 9. Response Contract Verification
// ============================================================================

describe('Response contract: BenchmarkConfigResponse', () => {
  it('contains all Pydantic fields', async () => {
    server.use(
      http.get('*/api/benchmarks/configs', () =>
        HttpResponse.json([mockConfigResponse()])
      )
    );

    const { result } = renderHook(() => useBenchmarkConfigs(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const config = result.current.data![0];
    // All fields from BenchmarkConfigResponse Pydantic schema
    expect(config).toHaveProperty('id');
    expect(config).toHaveProperty('name');
    expect(config).toHaveProperty('description');
    expect(config).toHaveProperty('tool');
    expect(config).toHaveProperty('config_json');
    expect(config).toHaveProperty('created_by');
    expect(config).toHaveProperty('created_at');
    expect(config).toHaveProperty('updated_at');
  });
});

describe('Response contract: BenchmarkSummaryResponse', () => {
  it('contains all Pydantic fields', async () => {
    server.use(
      http.get('*/api/benchmarks/summary', () =>
        HttpResponse.json(mockSummaryResponse())
      )
    );

    const { result } = renderHook(() => useBenchmarkSummary(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const summary = result.current.data!;
    // All fields from BenchmarkSummaryResponse Pydantic schema
    expect(summary).toHaveProperty('total_runs');
    expect(summary).toHaveProperty('completed_runs');
    expect(summary).toHaveProperty('failed_runs');
    expect(summary).toHaveProperty('running_count');
    expect(summary).toHaveProperty('avg_latency_p50');
    expect(summary).toHaveProperty('avg_rps');
    expect(summary).toHaveProperty('avg_success_rate');
    expect(summary).toHaveProperty('last_run_at');
    expect(summary).toHaveProperty('runs_last_7d');
    expect(summary).toHaveProperty('runs_by_proxy');
    expect(summary).toHaveProperty('runs_by_tool');
  });
});

describe('Response contract: BenchmarkTargetResponse', () => {
  it('contains all Pydantic fields', async () => {
    server.use(
      http.get('*/api/benchmarks/targets', () =>
        HttpResponse.json({ targets: [mockTargetResponse()], total: 1 })
      )
    );

    const { result } = renderHook(() => useBenchmarkTargets(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const target = result.current.data!.targets[0];
    // All fields from BenchmarkTargetResponse Pydantic schema
    expect(target).toHaveProperty('id');
    expect(target).toHaveProperty('name');
    expect(target).toHaveProperty('description');
    expect(target).toHaveProperty('cluster_id');
    expect(target).toHaveProperty('llm_base_url');
    expect(target).toHaveProperty('llm_model');
    expect(target).toHaveProperty('llm_namespace');
    expect(target).toHaveProperty('llm_endpoint');
    expect(target).toHaveProperty('proxy_namespace');
    expect(target).toHaveProperty('status');
    expect(target).toHaveProperty('last_validated');
    expect(target).toHaveProperty('validation_msg');
    expect(target).toHaveProperty('tags');
    expect(target).toHaveProperty('created_at');
    expect(target).toHaveProperty('updated_at');
  });
});

describe('Response contract: ProxyDeploymentResponse', () => {
  it('contains all Pydantic fields', async () => {
    server.use(
      http.get('*/api/benchmarks/targets/:targetId/proxies', () =>
        HttpResponse.json([mockProxyResponse()])
      )
    );

    const { result } = renderHook(() => useProxyDeployments(1), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const proxy = result.current.data![0];
    // All fields from ProxyDeploymentResponse Pydantic schema
    expect(proxy).toHaveProperty('id');
    expect(proxy).toHaveProperty('target_id');
    expect(proxy).toHaveProperty('proxy_type');
    expect(proxy).toHaveProperty('helm_release');
    expect(proxy).toHaveProperty('helm_chart');
    expect(proxy).toHaveProperty('helm_version');
    expect(proxy).toHaveProperty('helm_values');
    expect(proxy).toHaveProperty('proxy_url');
    expect(proxy).toHaveProperty('external_url');
    expect(proxy).toHaveProperty('status');
    expect(proxy).toHaveProperty('status_message');
    expect(proxy).toHaveProperty('deployed_at');
    expect(proxy).toHaveProperty('created_at');
    expect(proxy).toHaveProperty('updated_at');
  });
});
