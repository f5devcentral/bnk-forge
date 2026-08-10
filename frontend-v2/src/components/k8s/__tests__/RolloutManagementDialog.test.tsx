import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { RolloutManagementDialog } from '../RolloutManagementDialog';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

describe('RolloutManagementDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    clusterId: 1,
    namespace: 'default',
    deploymentName: 'api-server',
  };

  beforeEach(() => {
    server.use(
      http.get('*/api/k8s/clusters/:id/deployments/:name/rollout/history', () => {
        return HttpResponse.json({
          history: [
            { revision: 3, change_cause: 'Image update', created_at: '2026-02-01T00:00:00Z' },
            { revision: 2, change_cause: 'Config change', created_at: '2026-01-15T00:00:00Z' },
          ],
        });
      }),
      http.get('*/api/k8s/clusters/:id/deployments/:name/rollout/status', () => {
        return HttpResponse.json({
          message: 'deployment "api-server" successfully rolled out',
          desired_replicas: 2,
          current_replicas: 2,
          ready_replicas: 2,
          updated_replicas: 2,
          conditions: [],
        });
      })
    );
  });

  it('renders dialog title with deployment name', () => {
    render(<RolloutManagementDialog {...defaultProps} />);
    expect(screen.getByText(/Rollout Management: api-server/)).toBeInTheDocument();
  });

  it('shows three tabs: Status, History, Actions', () => {
    render(<RolloutManagementDialog {...defaultProps} />);
    expect(screen.getByText('Status')).toBeInTheDocument();
    expect(screen.getByText('History')).toBeInTheDocument();
    expect(screen.getByText('Actions')).toBeInTheDocument();
  });

  it('shows status message when loaded', async () => {
    render(<RolloutManagementDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/successfully rolled out/)).toBeInTheDocument();
    });
  });
});
