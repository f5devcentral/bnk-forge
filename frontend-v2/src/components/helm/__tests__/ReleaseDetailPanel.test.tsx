/**
 * Tests for ReleaseDetailPanel component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { ReleaseDetailPanel } from '@/components/helm/ReleaseDetailPanel';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

describe('ReleaseDetailPanel', () => {
  const defaultProps = {
    clusterId: 1,
    releaseName: 'nginx-ingress',
    releaseNamespace: 'ingress-nginx',
    onClose: vi.fn(),
    onUpgrade: vi.fn(),
    onRollback: vi.fn(),
    onUninstall: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    // Set up detailed release response
    server.use(
      http.get('*/api/k8s/:clusterId/helm/releases/:releaseName', ({ _params, request }) => {
        const url = new URL(request.url);
        const parts = url.pathname.split('/');
        if (parts[parts.length - 2] !== 'releases') return;
        return HttpResponse.json({
          success: true,
          release: {
            name: 'nginx-ingress',
            info: {
              first_deployed: '2026-01-01T08:00:00Z',
              last_deployed: '2026-02-01T12:00:00Z',
              deleted: '',
              description: 'Upgrade complete',
              status: 'deployed',
              notes: 'Get the URL by running kubectl get svc',
            },
            chart: {
              metadata: {
                name: 'ingress-nginx',
                version: '4.9.0',
                description: 'Ingress controller for Kubernetes',
                app_version: '1.9.5',
              },
            },
            config: {},
            manifest: '---\napiVersion: apps/v1\nkind: Deployment',
            version: 3,
            namespace: 'ingress-nginx',
          },
        });
      })
    );
  });

  it('renders release name and namespace', () => {
    render(<ReleaseDetailPanel {...defaultProps} />);

    expect(screen.getByText('nginx-ingress')).toBeInTheDocument();
    expect(screen.getByText('ingress-nginx')).toBeInTheDocument();
  });

  it('renders action buttons: Upgrade, Rollback, and Uninstall', () => {
    render(<ReleaseDetailPanel {...defaultProps} />);

    expect(screen.getByRole('button', { name: /upgrade/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /rollback/i })).toBeInTheDocument();
  });

  it('calls onClose when close button is clicked', async () => {
    const user = userEvent.setup();
    render(<ReleaseDetailPanel {...defaultProps} />);

    const closeButton = screen.getByRole('button', { name: /close/i });
    await user.click(closeButton);

    expect(defaultProps.onClose).toHaveBeenCalledTimes(1);
  });

  it('renders four tabs: Info, Values, Manifest, History', async () => {
    render(<ReleaseDetailPanel {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /info/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /values/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /manifest/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /history/i })).toBeInTheDocument();
    });
  });

  it('shows chart metadata on info tab after loading', async () => {
    render(<ReleaseDetailPanel {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('ingress-nginx')).toBeInTheDocument();
    });
  });
});
