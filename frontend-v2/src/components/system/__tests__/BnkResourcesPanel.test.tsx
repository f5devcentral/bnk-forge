import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { BnkResourcesPanel } from '../BnkResourcesPanel';
import type { BnkConsumptionResponse } from '@/types/system';

const mockConsumptionData: BnkConsumptionResponse = {
  timestamp: '2026-09-07T00:00:00Z',
  fleet_summary: {
    total_clusters: 2,
    reachable_clusters: 2,
    bnk_installed_clusters: 2,
    total_bnk_pods: 10,
    control_plane_pods: 4,
    data_plane_pods: 6,
    total_cpu_millicores: 3500,
    total_memory_bytes: 4294967296,
    node_capacity_cpu_millicores: 16000,
    node_capacity_memory_bytes: 34359738368,
    dpf_detected_clusters: 0,
    dpu_count: 0,
  },
  clusters: [
    {
      cluster_id: 1,
      cluster_name: 'aws-tokyo',
      cloud_provider: 'aws',
      region: 'ap-northeast-1',
      reachable: true,
      bnk_installed: true,
      bnk_version: '2.1.0',
      status: 'healthy',
      node_count: 3,
      metrics_available: true,
      metrics_error: null,
      total: { count: 6, cpu_millicores: 2000, memory_bytes: 2147483648 },
      control_plane: { count: 2, cpu_millicores: 500, memory_bytes: 536870912 },
      data_plane: { count: 4, cpu_millicores: 1500, memory_bytes: 1610612736 },
      node_capacity: { cpu_millicores: 8000, memory_bytes: 17179869184 },
      dpf: { detected: false, dpu_count: 0 },
      top_pods: [
        {
          name: 'f5-tmm-0',
          namespace: 'f5-bnk',
          role: 'tmm',
          cpu_millicores: 1200,
          memory_bytes: 1073741824,
        },
      ],
    },
    {
      cluster_id: 2,
      cluster_name: 'azure-central',
      cloud_provider: 'azure',
      region: 'eastus',
      reachable: true,
      bnk_installed: true,
      bnk_version: '2.1.0',
      status: 'healthy',
      node_count: 3,
      metrics_available: true,
      metrics_error: null,
      total: { count: 4, cpu_millicores: 1500, memory_bytes: 2147483648 },
      control_plane: { count: 2, cpu_millicores: 500, memory_bytes: 536870912 },
      data_plane: { count: 2, cpu_millicores: 1000, memory_bytes: 1610612736 },
      node_capacity: { cpu_millicores: 8000, memory_bytes: 17179869184 },
      dpf: { detected: false, dpu_count: 0 },
      top_pods: [
        {
          name: 'f5-tmm-1',
          namespace: 'f5-bnk',
          role: 'tmm',
          cpu_millicores: 800,
          memory_bytes: 1073741824,
        },
      ],
    },
  ],
};

describe('BnkResourcesPanel', () => {
  it('renders provider filter buttons with cluster counts', () => {
    render(
      <BnkResourcesPanel
        data={mockConsumptionData}
        isLoading={false}
        error={null}
      />
    );

    expect(screen.getByRole('button', { name: /all \(2\)/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^aws$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^azure$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^gke$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^metal$/i })).toBeInTheDocument();

    expect(screen.getAllByText('aws-tokyo').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('azure-central').length).toBeGreaterThanOrEqual(1);
  });

  it('filters clusters and recalculates summary when a provider pill is clicked', () => {
    render(
      <BnkResourcesPanel
        data={mockConsumptionData}
        isLoading={false}
        error={null}
      />
    );

    // Click AWS
    fireEvent.click(screen.getByRole('button', { name: /^aws$/i }));

    expect(screen.getAllByText('aws-tokyo').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('azure-central')).not.toBeInTheDocument();

    // Click Azure
    fireEvent.click(screen.getByRole('button', { name: /^azure$/i }));

    expect(screen.queryByText('aws-tokyo')).not.toBeInTheDocument();
    expect(screen.getAllByText('azure-central').length).toBeGreaterThanOrEqual(1);

    // Click Metal (empty)
    fireEvent.click(screen.getByRole('button', { name: /^metal$/i }));
    expect(screen.getByText(/no clusters found for provider "metal"/i)).toBeInTheDocument();

    // Click All (restores all)
    fireEvent.click(screen.getByRole('button', { name: /all \(2\)/i }));
    expect(screen.getAllByText('aws-tokyo').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('azure-central').length).toBeGreaterThanOrEqual(1);
  });
});
