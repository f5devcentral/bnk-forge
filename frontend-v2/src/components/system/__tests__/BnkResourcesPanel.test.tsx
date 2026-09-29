import { describe, it, expect } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { BnkResourcesPanel } from '../BnkResourcesPanel';
import type { BnkClusterConsumption, BnkConsumptionResponse } from '@/types/system';

const plane = (count: number, cpu: number, mem: number) => ({ count, cpu_millicores: cpu, memory_bytes: mem });

function cluster(id: number, metrics: boolean, cpu: number, reachable = true): BnkClusterConsumption {
  return {
    cluster_id: id,
    cluster_name: `cluster-${id}`,
    reachable,
    bnk_installed: true,
    bnk_version: null,
    status: 'connected',
    node_count: 1,
    control_plane: plane(0, 0, 0),
    data_plane: plane(0, 0, 0),
    total: plane(1, cpu, 0),
    node_capacity: { cpu_millicores: 4000, memory_bytes: 8 * 1024 ** 3 },
    metrics_available: metrics,
    metrics_error: metrics ? null : 'metrics-server unavailable',
    dpf: { detected: false, dpu_count: 0 },
    top_pods: [],
  };
}

function response(clusters: BnkClusterConsumption[]): BnkConsumptionResponse {
  return {
    timestamp: '2026-09-01T00:00:00Z',
    clusters,
    fleet_summary: {
      total_clusters: clusters.length,
      reachable_clusters: clusters.filter((c) => c.reachable).length,
      bnk_installed_clusters: clusters.length,
      total_bnk_pods: clusters.length,
      control_plane_pods: 0,
      data_plane_pods: 0,
      total_cpu_millicores: clusters.reduce((sum, c) => sum + c.total.cpu_millicores, 0),
      total_memory_bytes: 0,
      node_capacity_cpu_millicores: clusters.length * 4000,
      node_capacity_memory_bytes: clusters.length * 8 * 1024 ** 3,
      dpf_detected_clusters: 0,
      dpu_count: 0,
    },
  };
}

describe('BnkResourcesPanel', () => {
  it('shows measured usage of 0 instead of node capacity when metrics are available', () => {
    render(<BnkResourcesPanel data={response([cluster(1, true, 0)])} isLoading={false} error={null} />);
    expect(screen.queryByText('4.00 cores')).not.toBeInTheDocument();
    expect(screen.getAllByText('Across all BNK pods')).toHaveLength(2);
  });

  it('falls back to node capacity only when no cluster reports metrics', () => {
    render(<BnkResourcesPanel data={response([cluster(1, false, 0)])} isLoading={false} error={null} />);
    expect(screen.getAllByText('4.00 cores')).toHaveLength(2); // tile + table cell
    expect(screen.getAllByText('Node capacity (metrics-server unavailable)')).toHaveLength(2);
  });

  it('says when clusters without metrics are left out of the totals', () => {
    render(
      <BnkResourcesPanel
        data={response([cluster(1, true, 500), cluster(2, false, 0)])}
        isLoading={false}
        error={null}
      />,
    );
    expect(screen.getAllByText('Across all BNK pods · 1 cluster without metrics not included')).toHaveLength(2);
  });

  it('does not blame metrics-server for offline clusters', () => {
    render(<BnkResourcesPanel data={response([cluster(1, false, 0, false)])} isLoading={false} error={null} />);
    expect(screen.queryByText(/metrics-server unavailable/)).not.toBeInTheDocument();
    expect(screen.getAllByText('Across all BNK pods · 1 cluster offline')).toHaveLength(2);
  });

  it('counts offline clusters apart from clusters without metrics', () => {
    render(
      <BnkResourcesPanel
        data={response([cluster(1, true, 500), cluster(2, false, 0), cluster(3, false, 0, false)])}
        isLoading={false}
        error={null}
      />,
    );
    expect(
      screen.getAllByText('Across all BNK pods · 1 cluster without metrics not included · 1 cluster offline'),
    ).toHaveLength(2);
  });
});
