import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { ResourceMetricsViewer } from '../ResourceMetricsViewer';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

describe('ResourceMetricsViewer', () => {
  const defaultProps = {
    open: true,
    onClose: vi.fn(),
    clusterId: 1,
    namespace: 'default',
  };

  beforeEach(() => {
    server.use(
      http.get('*/api/k8s/clusters/:id/top/pods', () => {
        return HttpResponse.json({
          available: true,
          metrics: [
            { name: 'nginx-pod', namespace: 'default', cpu_millicores: 250, memory_bytes: 134217728 },
          ],
        });
      }),
      http.get('*/api/k8s/clusters/:id/top/nodes', () => {
        return HttpResponse.json({
          available: true,
          metrics: [
            { name: 'node-1', cpu_millicores: 1500, memory_bytes: 4294967296, cpu_percent: 37, memory_percent: 52, allocatable_cpu_millicores: 4000, allocatable_memory_bytes: 8589934592 },
          ],
        });
      })
    );
  });

  it('renders dialog with title and pod/node tabs', () => {
    render(<ResourceMetricsViewer {...defaultProps} />);
    expect(screen.getByText('Resource Metrics')).toBeInTheDocument();
    expect(screen.getByText('Pods')).toBeInTheDocument();
    expect(screen.getByText('Nodes')).toBeInTheDocument();
  });

  it('shows pod metrics data', async () => {
    render(<ResourceMetricsViewer {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('nginx-pod')).toBeInTheDocument();
      expect(screen.getByText('250m')).toBeInTheDocument();
    });
  });

  it('shows unavailable message when metrics server is down', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/top/pods', () => {
        return HttpResponse.json({
          available: false,
          error: 'Metrics server not installed',
          metrics: [],
        });
      })
    );

    render(<ResourceMetricsViewer {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Metrics server not installed')).toBeInTheDocument();
    });
  });
});
