import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { ResourceEventsViewer } from '../ResourceEventsViewer';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

describe('ResourceEventsViewer', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    clusterId: 1,
    namespace: 'default',
  };

  beforeEach(() => {
    server.use(
      http.get('*/api/k8s/clusters/:id/events', () => {
        return HttpResponse.json({
          events: [
            {
              type: 'Normal',
              reason: 'Scheduled',
              message: 'Successfully assigned default/nginx-pod to node-1',
              source: 'default-scheduler',
              count: 1,
              last_timestamp: '2026-02-25T12:00:00Z',
              involved_object: { kind: 'Pod', name: 'nginx-pod', namespace: 'default' },
            },
            {
              type: 'Warning',
              reason: 'BackOff',
              message: 'Back-off restarting failed container',
              source: 'kubelet',
              count: 5,
              last_timestamp: '2026-02-25T12:05:00Z',
              involved_object: { kind: 'Pod', name: 'fail-pod', namespace: 'default' },
            },
          ],
        });
      })
    );
  });

  it('renders dialog title and event type badges', async () => {
    render(<ResourceEventsViewer {...defaultProps} />);

    expect(screen.getByText('Cluster Events')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText(/1 Normal/)).toBeInTheDocument();
      expect(screen.getByText(/1 Warning/)).toBeInTheDocument();
    });
  });

  it('shows events with reason and message', async () => {
    render(<ResourceEventsViewer {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Scheduled')).toBeInTheDocument();
      expect(screen.getByText('BackOff')).toBeInTheDocument();
      expect(screen.getByText(/Successfully assigned/)).toBeInTheDocument();
    });
  });

  it('shows resource name in title when provided', () => {
    render(<ResourceEventsViewer {...defaultProps} resourceType="pod" resourceName="nginx-pod" />);
    expect(screen.getByText('for pod/nginx-pod')).toBeInTheDocument();
  });
});
