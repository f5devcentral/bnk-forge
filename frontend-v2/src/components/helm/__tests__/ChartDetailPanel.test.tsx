/**
 * Tests for ChartDetailPanel component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { ChartDetailPanel } from '@/components/helm/ChartDetailPanel';

const baseChart = {
  name: 'nginx',
  version: '15.4.0',
  app_version: '1.25.3',
  description: 'NGINX Open Source is a web server',
  home: 'https://nginx.org',
  sources: ['https://github.com/bitnami/charts'],
  keywords: ['web', 'http', 'proxy'],
  maintainers: [
    { name: 'Bitnami', email: 'containers@bitnami.com' },
  ],
};

describe('ChartDetailPanel', () => {
  const onClose = vi.fn();
  const onInstall = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders chart name, version, and description', () => {
    render(<ChartDetailPanel chart={baseChart} onClose={onClose} onInstall={onInstall} />);

    expect(screen.getByText('nginx')).toBeInTheDocument();
    expect(screen.getByText('v15.4.0')).toBeInTheDocument();
    expect(screen.getByText('NGINX Open Source is a web server')).toBeInTheDocument();
  });

  it('renders app version badge', () => {
    render(<ChartDetailPanel chart={baseChart} onClose={onClose} onInstall={onInstall} />);

    expect(screen.getByText('App v1.25.3')).toBeInTheDocument();
  });

  it('renders keywords as badges', () => {
    render(<ChartDetailPanel chart={baseChart} onClose={onClose} onInstall={onInstall} />);

    expect(screen.getByText('web')).toBeInTheDocument();
    expect(screen.getByText('http')).toBeInTheDocument();
    expect(screen.getByText('proxy')).toBeInTheDocument();
  });

  it('renders maintainers section', () => {
    render(<ChartDetailPanel chart={baseChart} onClose={onClose} onInstall={onInstall} />);

    expect(screen.getByText('Bitnami')).toBeInTheDocument();
    expect(screen.getByText('containers@bitnami.com')).toBeInTheDocument();
  });

  it('renders install command with chart name', () => {
    render(<ChartDetailPanel chart={baseChart} onClose={onClose} onInstall={onInstall} />);

    expect(screen.getByText(/helm install my-release nginx/)).toBeInTheDocument();
  });

  it('calls onInstall when Install Chart button is clicked', async () => {
    const user = userEvent.setup();
    render(<ChartDetailPanel chart={baseChart} onClose={onClose} onInstall={onInstall} />);

    const installButton = screen.getByRole('button', { name: /install chart/i });
    await user.click(installButton);

    expect(onInstall).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when close button is clicked', async () => {
    const user = userEvent.setup();
    render(<ChartDetailPanel chart={baseChart} onClose={onClose} onInstall={onInstall} />);

    const closeButton = screen.getByRole('button', { name: /close/i });
    await user.click(closeButton);

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('renders homepage link when home is provided', () => {
    render(<ChartDetailPanel chart={baseChart} onClose={onClose} onInstall={onInstall} />);

    const homepageLink = screen.getByText('Homepage');
    expect(homepageLink.closest('a')).toHaveAttribute('href', 'https://nginx.org');
  });
});
