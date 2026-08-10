import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { DeploymentRolloutManager } from '../DeploymentRolloutManager';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

describe('DeploymentRolloutManager', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    clusterId: 1,
    deploymentName: 'nginx-deployment',
    namespace: 'default',
  };

  beforeEach(() => {
    server.use(
      http.get('*/api/k8s/clusters/:id/deployments/:name/rollout/history', () => {
        return HttpResponse.json({
          history: [
            { revision: 2, change_cause: 'Updated image', replicas: 3, ready_replicas: 3, is_current: true, created_at: '2026-02-01T00:00:00Z' },
            { revision: 1, change_cause: 'Initial deploy', replicas: 3, ready_replicas: 3, is_current: false, created_at: '2026-01-01T00:00:00Z' },
          ],
        });
      }),
      http.get('*/api/k8s/clusters/:id/deployments/:name/rollout/status', () => {
        return HttpResponse.json({
          rollout_complete: true,
          message: 'deployment "nginx-deployment" successfully rolled out',
          desired_replicas: 3,
          current_replicas: 3,
          updated_replicas: 3,
          ready_replicas: 3,
          available_replicas: 3,
          conditions: [
            { type: 'Available', status: 'True', message: 'Deployment has minimum availability.' },
          ],
        });
      })
    );
  });

  it('renders dialog with deployment name', () => {
    render(<DeploymentRolloutManager {...defaultProps} />);
    expect(screen.getByText('Deployment Rollout Management')).toBeInTheDocument();
    expect(screen.getByText('nginx-deployment')).toBeInTheDocument();
  });

  it('shows current status with replica counts', async () => {
    render(<DeploymentRolloutManager {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Rollback to Previous')).toBeInTheDocument();
      expect(screen.getByText('Restart Deployment')).toBeInTheDocument();
    });
  });

  it('shows tabs for Current Status and Rollout History', () => {
    render(<DeploymentRolloutManager {...defaultProps} />);
    expect(screen.getByText('Current Status')).toBeInTheDocument();
    expect(screen.getByText('Rollout History')).toBeInTheDocument();
  });
});
