/**
 * Tests for UpgradeReleaseDialog component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { UpgradeReleaseDialog } from '@/components/helm/UpgradeReleaseDialog';

describe('UpgradeReleaseDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    clusterId: 1,
    releaseName: 'nginx-ingress',
    releaseNamespace: 'ingress-nginx',
    currentChart: 'ingress-nginx',
    currentVersion: '4.9.0',
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders dialog title with release name', () => {
    render(<UpgradeReleaseDialog {...defaultProps} />);

    expect(screen.getByText('Upgrade Helm Release')).toBeInTheDocument();
    expect(screen.getByText(/nginx-ingress/)).toBeInTheDocument();
  });

  it('renders chart reference and version fields', () => {
    render(<UpgradeReleaseDialog {...defaultProps} />);

    expect(screen.getByLabelText(/chart reference/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/version/i)).toBeInTheDocument();
  });

  it('pre-fills chart reference with currentChart', () => {
    render(<UpgradeReleaseDialog {...defaultProps} />);

    const chartInput = screen.getByLabelText(/chart reference/i);
    expect(chartInput).toHaveValue('ingress-nginx');
  });

  it('renders reuse values checkbox', () => {
    render(<UpgradeReleaseDialog {...defaultProps} />);

    expect(screen.getByText(/reuse existing values/i)).toBeInTheDocument();
  });

  it('renders YAML and JSON tabs for custom values after values load', async () => {
    render(<UpgradeReleaseDialog {...defaultProps} />);

    // Wait for values to load (MSW handler returns values data)
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /yaml/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /json/i })).toBeInTheDocument();
    });
  });

  it('renders Cancel and Upgrade buttons', () => {
    render(<UpgradeReleaseDialog {...defaultProps} />);

    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^upgrade$/i })).toBeInTheDocument();
  });

  it('calls onOpenChange when Cancel is clicked', async () => {
    const user = userEvent.setup();
    render(<UpgradeReleaseDialog {...defaultProps} />);

    await user.click(screen.getByRole('button', { name: /cancel/i }));

    expect(defaultProps.onOpenChange).toHaveBeenCalled();
  });

  it('does not render when open is false', () => {
    render(<UpgradeReleaseDialog {...defaultProps} open={false} />);

    expect(screen.queryByText('Upgrade Helm Release')).not.toBeInTheDocument();
  });
});
