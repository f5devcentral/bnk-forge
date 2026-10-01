/**
 * Tests for BenchmarkTargetsTab.
 *
 * Verifies targets list and detail view display cluster name next to targets
 * so identical target names across clusters are distinguishable.
 */
import { describe, it, expect } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { BenchmarkTargetsTab } from '@/pages/BenchmarkTargetsTab';

const now = '2026-07-20T10:00:00Z';

function mockTarget(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    name: 'mcp-default-route',
    description: 'Shared route target',
    cluster_id: 10,
    cluster_name: 'cluster-alpha',
    llm_base_url: 'http://vllm.default:8000',
    llm_model: 'llama3',
    llm_namespace: 'default',
    llm_endpoint: '/v1/chat/completions',
    proxy_namespace: 'perf-proxies',
    status: 'active',
    last_validated: null,
    validation_msg: null,
    tags: null,
    proxy_count: 1,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

describe('BenchmarkTargetsTab', () => {
  it('renders Cluster column and cluster names for targets with identical names across clusters', async () => {
    server.use(
      http.get('*/api/benchmarks/targets', () =>
        HttpResponse.json({
          targets: [
            mockTarget({ id: 1, name: 'mcp-default-route', cluster_id: 10, cluster_name: 'cluster-alpha' }),
            mockTarget({ id: 2, name: 'mcp-default-route', cluster_id: 20, cluster_name: 'cluster-beta' }),
          ],
          total: 2,
        })
      ),
      http.get('*/api/k8s/clusters', () =>
        HttpResponse.json({
          clusters: [
            { id: 10, name: 'cluster-alpha' },
            { id: 20, name: 'cluster-beta' },
          ],
          total: 2,
        })
      ),
      http.get('*/api/benchmarks/agents', () => HttpResponse.json([])),
      http.get('*/api/benchmarks/scenarios', () => HttpResponse.json({ scenarios: [] }))
    );

    render(<BenchmarkTargetsTab />);

    await waitFor(() => {
      expect(screen.getByRole('columnheader', { name: /cluster/i })).toBeInTheDocument();
    });

    expect(screen.getByText('cluster-alpha')).toBeInTheDocument();
    expect(screen.getByText('cluster-beta')).toBeInTheDocument();

    const targetNames = screen.getAllByText('mcp-default-route');
    expect(targetNames).toHaveLength(2);
  });

  it('renders cluster name in target detail header and details card when selected', async () => {
    const user = userEvent.setup();

    server.use(
      http.get('*/api/benchmarks/targets', () =>
        HttpResponse.json({
          targets: [
            mockTarget({ id: 1, name: 'mcp-default-route', cluster_id: 10, cluster_name: 'cluster-alpha' }),
          ],
          total: 1,
        })
      ),
      http.get('*/api/benchmarks/targets/1', () =>
        HttpResponse.json({
          ...mockTarget({ id: 1, name: 'mcp-default-route', cluster_id: 10, cluster_name: 'cluster-alpha' }),
          proxy_deployments: [],
        })
      ),
      http.get('*/api/k8s/clusters', () =>
        HttpResponse.json({
          clusters: [{ id: 10, name: 'cluster-alpha' }],
          total: 1,
        })
      ),
      http.get('*/api/benchmarks/agents', () => HttpResponse.json([])),
      http.get('*/api/benchmarks/scenarios', () => HttpResponse.json({ scenarios: [] }))
    );

    render(<BenchmarkTargetsTab />);

    await waitFor(() => {
      expect(screen.getByText('mcp-default-route')).toBeInTheDocument();
    });

    // Click on target row to open detail view
    await user.click(screen.getByText('mcp-default-route'));

    await waitFor(() => {
      expect(screen.getByText('Target details')).toBeInTheDocument();
    });

    // Detail header badge and detail grid should display cluster-alpha
    const clusterBadges = screen.getAllByText('cluster-alpha');
    expect(clusterBadges.length).toBeGreaterThanOrEqual(1);
  });
});
