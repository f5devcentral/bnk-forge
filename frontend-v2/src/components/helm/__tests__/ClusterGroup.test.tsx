/**
 * Tests for ClusterGroup component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { ClusterGroup } from '@/components/helm/ClusterGroup';
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

function makeCluster(overrides: Partial<ClusterWithReleases> = {}): ClusterWithReleases {
  return {
    id: 1,
    name: 'dev-cluster',
    namespaces: [
      {
        name: 'ingress-nginx',
        releases: [makeRelease()],
      },
      {
        name: 'cert-manager',
        releases: [makeRelease({ name: 'cert-manager', namespace: 'cert-manager' })],
      },
    ],
    ...overrides,
  };
}

describe('ClusterGroup', () => {
  const onToggle = vi.fn();
  const onSelectRelease = vi.fn();
  const onToggleNamespace = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders cluster name and total release count', () => {
    render(
      <ClusterGroup
        cluster={makeCluster()}
        expanded={false}
        onToggle={onToggle}
        selectedRelease={null}
        onSelectRelease={onSelectRelease}
        expandedNamespaces={new Set()}
        onToggleNamespace={onToggleNamespace}
      />
    );

    expect(screen.getByText('dev-cluster')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('calls onToggle when cluster header is clicked', async () => {
    const user = userEvent.setup();
    render(
      <ClusterGroup
        cluster={makeCluster()}
        expanded={false}
        onToggle={onToggle}
        selectedRelease={null}
        onSelectRelease={onSelectRelease}
        expandedNamespaces={new Set()}
        onToggleNamespace={onToggleNamespace}
      />
    );

    const button = screen.getByRole('button', { name: /cluster dev-cluster/i });
    await user.click(button);

    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('sets aria-expanded correctly', () => {
    render(
      <ClusterGroup
        cluster={makeCluster()}
        expanded={true}
        onToggle={onToggle}
        selectedRelease={null}
        onSelectRelease={onSelectRelease}
        expandedNamespaces={new Set()}
        onToggleNamespace={onToggleNamespace}
      />
    );

    const button = screen.getByRole('button', { name: /cluster dev-cluster/i });
    expect(button).toHaveAttribute('aria-expanded', 'true');
  });

  it('shows "No releases deployed" when cluster has no namespaces', () => {
    render(
      <ClusterGroup
        cluster={makeCluster({ namespaces: [] })}
        expanded={true}
        onToggle={onToggle}
        selectedRelease={null}
        onSelectRelease={onSelectRelease}
        expandedNamespaces={new Set()}
        onToggleNamespace={onToggleNamespace}
      />
    );

    expect(screen.getByText('No releases deployed')).toBeInTheDocument();
  });
});
