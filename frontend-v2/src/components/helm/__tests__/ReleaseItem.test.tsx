/**
 * Tests for ReleaseItem component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { ReleaseItem } from '@/components/helm/ReleaseItem';
import type { HelmReleaseWithCluster } from '@/lib/helm-grouping';

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

describe('ReleaseItem', () => {
  const onSelect = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders release name and revision badge', () => {
    render(<ReleaseItem release={makeRelease()} selected={false} onSelect={onSelect} />);

    expect(screen.getByText('nginx-ingress')).toBeInTheDocument();
    expect(screen.getByText('v3')).toBeInTheDocument();
  });

  it('calls onSelect when clicked', async () => {
    const user = userEvent.setup();
    render(<ReleaseItem release={makeRelease()} selected={false} onSelect={onSelect} />);

    const button = screen.getByRole('button');
    await user.click(button);

    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('shows aria-current when selected', () => {
    render(<ReleaseItem release={makeRelease()} selected={true} onSelect={onSelect} />);

    const button = screen.getByRole('button');
    expect(button).toHaveAttribute('aria-current', 'true');
  });

  it('includes release status in aria-label', () => {
    render(<ReleaseItem release={makeRelease({ status: 'failed' })} selected={false} onSelect={onSelect} />);

    const button = screen.getByRole('button');
    expect(button).toHaveAttribute('aria-label', expect.stringContaining('failed'));
  });
});
