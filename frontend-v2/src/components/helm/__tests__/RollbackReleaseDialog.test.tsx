/**
 * Tests for RollbackReleaseDialog component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '@/test/test-utils';
import { RollbackReleaseDialog } from '@/components/helm/RollbackReleaseDialog';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

describe('RollbackReleaseDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    clusterId: 1,
    releaseName: 'nginx-ingress',
    releaseNamespace: 'ingress-nginx',
    currentRevision: 3,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    // Override history handler to return proper HelmHistoryResponse format
    server.use(
      http.get('*/api/k8s/:clusterId/helm/releases/:releaseName/history', () => {
        return HttpResponse.json({
          success: true,
          history: [
            { revision: 3, status: 'deployed', chart: 'ingress-nginx-4.9.0', app_version: '1.9.5', updated: '2026-02-01T12:00:00Z', description: 'Upgrade complete' },
            { revision: 2, status: 'superseded', chart: 'ingress-nginx-4.8.0', app_version: '1.9.4', updated: '2026-01-15T10:00:00Z', description: 'Upgrade complete' },
            { revision: 1, status: 'superseded', chart: 'ingress-nginx-4.7.0', app_version: '1.9.3', updated: '2026-01-01T08:00:00Z', description: 'Install complete' },
          ],
          count: 3,
        });
      })
    );
  });

  it('renders dialog title and description', () => {
    render(<RollbackReleaseDialog {...defaultProps} />);

    expect(screen.getByText('Rollback Helm Release')).toBeInTheDocument();
    expect(screen.getByText(/nginx-ingress/)).toBeInTheDocument();
  });

  it('renders warning banner', () => {
    render(<RollbackReleaseDialog {...defaultProps} />);

    expect(screen.getByText('Important')).toBeInTheDocument();
    expect(screen.getByText(/Rolling back will create a new revision/)).toBeInTheDocument();
  });

  it('shows revision history table after loading', async () => {
    render(<RollbackReleaseDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Revision History')).toBeInTheDocument();
      expect(screen.getByText('Rev')).toBeInTheDocument();
      expect(screen.getByText('Status')).toBeInTheDocument();
    });
  });

  it('Rollback button is disabled when no revision is selected', () => {
    render(<RollbackReleaseDialog {...defaultProps} />);

    const rollbackButton = screen.getByRole('button', { name: /^rollback$/i });
    expect(rollbackButton).toBeDisabled();
  });

  it('marks current revision with CURRENT badge', async () => {
    render(<RollbackReleaseDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('CURRENT')).toBeInTheDocument();
    });
  });

  it('does not render when open is false', () => {
    render(<RollbackReleaseDialog {...defaultProps} open={false} />);

    expect(screen.queryByText('Rollback Helm Release')).not.toBeInTheDocument();
  });
});
