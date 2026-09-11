/**
 * Tests for MultiCloudEstate component
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { MultiCloudEstate } from '../MultiCloudEstate';
import type { Project } from '@/types/project';
import type { KubernetesCluster } from '@/types/k8s';

const mockProjects: Project[] = [
  {
    id: 1,
    name: 'prod-aws-edge',
    cloud_provider: 'aws',
    region: 'us-west-2',
    module_count: 5,
    deployed_count: 5,
    failed_count: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 2,
    name: 'stage-azure-apps',
    cloud_provider: 'azure',
    region: 'australiaeast',
    module_count: 3,
    deployed_count: 2,
    failed_count: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
];

const mockClusters: KubernetesCluster[] = [
  {
    id: 1,
    name: 'eks-cluster-oregon',
    cloud_provider: 'aws',
    region: 'us-west-2',
    status: 'active',
    node_count: 10,
    detected_platform_profile: 'EKS',
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 2,
    name: 'aks-cluster-sydney',
    cloud_provider: 'azure',
    region: 'australiaeast',
    status: 'active',
    node_count: 6,
    detected_platform_profile: 'AKS',
    created_at: '2026-01-01T00:00:00Z',
  },
];

describe('MultiCloudEstate', () => {
  it('renders Multi-Cloud Estate title, summary metrics, and grouped provider sections', () => {
    render(
      <MultiCloudEstate
        projects={mockProjects}
        clusters={mockClusters}
        fleetByCluster={{}}
        connectivityStates={{}}
        projectDriftCounts={{}}
      />
    );

    expect(screen.getByText('Multi-Cloud Estate')).toBeInTheDocument();
    expect(screen.getAllByText('AWS').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Azure').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('eks-cluster-oregon')).toBeInTheDocument();
    expect(screen.getByText('prod-aws-edge')).toBeInTheDocument();
  });

  it('allows toggling between Grouped and Flat view modes', async () => {
    const user = userEvent.setup();
    render(
      <MultiCloudEstate
        projects={mockProjects}
        clusters={mockClusters}
        fleetByCluster={{}}
        connectivityStates={{}}
        projectDriftCounts={{}}
      />
    );

    const flatToggle = screen.getByTitle('Flat Grid View');
    await user.click(flatToggle);

    expect(screen.getByText('eks-cluster-oregon')).toBeInTheDocument();
    expect(screen.getByText('stage-azure-apps')).toBeInTheDocument();
  });

  it('renders empty state when no clusters or projects exist', () => {
    render(
      <MultiCloudEstate
        projects={[]}
        clusters={[]}
        fleetByCluster={{}}
        connectivityStates={{}}
        projectDriftCounts={{}}
      />
    );

    expect(screen.getByText('No multi-cloud infrastructure detected')).toBeInTheDocument();
  });
});
