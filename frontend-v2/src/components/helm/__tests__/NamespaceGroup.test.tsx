/**
 * Tests for NamespaceGroup component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { NamespaceGroup } from '@/components/helm/NamespaceGroup';
import type { NamespaceWithReleases, HelmReleaseWithCluster } from '@/lib/helm-grouping';

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

function makeNamespace(overrides: Partial<NamespaceWithReleases> = {}): NamespaceWithReleases {
  return {
    name: 'ingress-nginx',
    releases: [makeRelease()],
    ...overrides,
  };
}

describe('NamespaceGroup', () => {
  const onToggle = vi.fn();
  const onSelectRelease = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders namespace name and release count', () => {
    render(
      <NamespaceGroup
        namespace={makeNamespace()}
        clusterId={1}
        expanded={false}
        onToggle={onToggle}
        selectedRelease={null}
        onSelectRelease={onSelectRelease}
      />
    );

    expect(screen.getByText('ingress-nginx')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
  });

  it('calls onToggle when header is clicked', async () => {
    const user = userEvent.setup();
    render(
      <NamespaceGroup
        namespace={makeNamespace()}
        clusterId={1}
        expanded={false}
        onToggle={onToggle}
        selectedRelease={null}
        onSelectRelease={onSelectRelease}
      />
    );

    const button = screen.getByRole('button', { name: /namespace ingress-nginx/i });
    await user.click(button);

    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('sets aria-expanded correctly', () => {
    const { rerender } = render(
      <NamespaceGroup
        namespace={makeNamespace()}
        clusterId={1}
        expanded={false}
        onToggle={onToggle}
        selectedRelease={null}
        onSelectRelease={onSelectRelease}
      />
    );

    const button = screen.getByRole('button', { name: /namespace ingress-nginx/i });
    expect(button).toHaveAttribute('aria-expanded', 'false');

    rerender(
      <NamespaceGroup
        namespace={makeNamespace()}
        clusterId={1}
        expanded={true}
        onToggle={onToggle}
        selectedRelease={null}
        onSelectRelease={onSelectRelease}
      />
    );

    expect(button).toHaveAttribute('aria-expanded', 'true');
  });

  it('shows "No releases" message when namespace has no releases', () => {
    render(
      <NamespaceGroup
        namespace={makeNamespace({ releases: [] })}
        clusterId={1}
        expanded={true}
        onToggle={onToggle}
        selectedRelease={null}
        onSelectRelease={onSelectRelease}
      />
    );

    expect(screen.getByText('No releases')).toBeInTheDocument();
  });
});
