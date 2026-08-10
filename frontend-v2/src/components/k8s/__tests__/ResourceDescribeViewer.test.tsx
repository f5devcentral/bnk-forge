import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { ResourceDescribeViewer } from '../ResourceDescribeViewer';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

const mockResource = {
  kind: 'Deployment',
  apiVersion: 'apps/v1',
  metadata: { name: 'nginx', namespace: 'default', uid: '123' },
  spec: {},
  status: {},
};

describe('ResourceDescribeViewer', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    resource: mockResource,
    clusterId: 1,
    namespace: 'default',
  };

  beforeEach(() => {
    server.use(
      http.get('*/api/k8s/clusters/:id/resources/:type/:name/describe', () => {
        return HttpResponse.json({
          metadata: {
            name: 'nginx',
            namespace: 'default',
            uid: 'abc-123',
            creation_timestamp: '2026-01-01T00:00:00Z',
            labels: { app: 'nginx' },
          },
          status: { replicas: 3 },
          conditions: [
            { type: 'Available', status: 'True', reason: 'MinimumReplicasAvailable', message: 'Deployment has minimum availability.' },
          ],
          events: [],
          relationships: { owned: [], related: [] },
        });
      })
    );
  });

  it('renders dialog title with resource kind and name', () => {
    render(<ResourceDescribeViewer {...defaultProps} />);
    expect(screen.getByText('Describe: Deployment / nginx')).toBeInTheDocument();
  });

  it('shows tabs for overview, conditions, events, related, raw', async () => {
    render(<ResourceDescribeViewer {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Overview')).toBeInTheDocument();
    });
    expect(screen.getByText(/Conditions/)).toBeInTheDocument();
    expect(screen.getByText(/Events/)).toBeInTheDocument();
    expect(screen.getByText('Related')).toBeInTheDocument();
    expect(screen.getByText('Raw Data')).toBeInTheDocument();
  });

  it('returns null when resource is null', () => {
    const { container } = render(<ResourceDescribeViewer {...defaultProps} resource={null} />);
    expect(container.innerHTML).toBe('');
  });
});
