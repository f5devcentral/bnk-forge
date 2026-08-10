/**
 * Tests for HelmSidebar component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { HelmSidebar } from '@/components/helm/HelmSidebar';
import type { ClusterWithReleases, HelmReleaseWithCluster } from '@/lib/helm-grouping';

function makeRelease(overrides: Partial<HelmReleaseWithCluster> = {}): HelmReleaseWithCluster {
  return {
    name: 'nginx-ingress',
    namespace: 'ingress-nginx',
    revision: '3',
    updated: '2026-02-01T12:00:00Z',
    status: 'deployed',
    chart: 'ingress-nginx-4.9.0',
    app_version: '1.9.5',
    cluster_id: 1,
    cluster_name: 'dev-cluster',
    ...overrides,
  };
}

function makeCluster(): ClusterWithReleases {
  return {
    id: 1,
    name: 'dev-cluster',
    namespaces: [
      { name: 'ingress-nginx', releases: [makeRelease()] },
    ],
  };
}

describe('HelmSidebar', () => {
  const defaultProps = {
    releases: [makeCluster()],
    selectedRelease: null as HelmReleaseWithCluster | null,
    onSelectRelease: vi.fn(),
    selectedChartSource: null as string | null,
    onSelectChartSource: vi.fn(),
    expandedClusters: new Set<number>(),
    expandedNamespaces: new Set<string>(),
    onToggleCluster: vi.fn(),
    onToggleNamespace: vi.fn(),
    expandedSections: new Set(['releases', 'charts']),
    onToggleSection: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders Releases and Charts section headers', () => {
    render(<HelmSidebar {...defaultProps} />);

    expect(screen.getByText('Releases')).toBeInTheDocument();
    expect(screen.getByText('Charts')).toBeInTheDocument();
  });

  it('renders chart source options when charts section is expanded', () => {
    render(<HelmSidebar {...defaultProps} />);

    expect(screen.getByText('Artifact Hub')).toBeInTheDocument();
    expect(screen.getByText('Bitnami')).toBeInTheDocument();
    expect(screen.getByText('Uploaded Charts')).toBeInTheDocument();
    expect(screen.getByText('Cloned Charts')).toBeInTheDocument();
  });

  it('calls onSelectChartSource when a chart source is clicked', async () => {
    const user = userEvent.setup();
    render(<HelmSidebar {...defaultProps} />);

    await user.click(screen.getByText('Bitnami'));

    expect(defaultProps.onSelectChartSource).toHaveBeenCalledWith('bitnami');
  });

  it('shows empty state when no releases', () => {
    render(<HelmSidebar {...defaultProps} releases={[]} />);

    expect(screen.getByText('No Helm Releases')).toBeInTheDocument();
  });

  it('toggles sections when header is clicked', async () => {
    const user = userEvent.setup();
    render(<HelmSidebar {...defaultProps} />);

    const releasesButton = screen.getByRole('button', { name: /releases/i });
    await user.click(releasesButton);

    expect(defaultProps.onToggleSection).toHaveBeenCalledWith('releases');
  });
});
