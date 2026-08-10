import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { QueueMetricsPanel } from '../QueueMetricsPanel';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('QueueMetricsPanel', () => {
  it('renders queue names and task summary', async () => {
    // Override with shape matching what the component expects
    server.use(
      http.get('*/api/system/queue-metrics', () => {
        return HttpResponse.json({
          queues: {
            default: { pending: 5, active: 2 },
            opentofu: { pending: 12, active: 1 },
          },
          workers: { total: 4, active: 3, offline: 1 },
          tasks: { pending: 17, active: 3, completed_last_hour: 42 },
        });
      })
    );

    render(<QueueMetricsPanel />);

    // Wait for actual data to render (not just the loading state)
    await waitFor(() => {
      expect(screen.getByText('Default Queue')).toBeInTheDocument();
    });
    expect(screen.getByText('OpenTofu Queue')).toBeInTheDocument();
    expect(screen.getByText('17')).toBeInTheDocument(); // Pending tasks
    expect(screen.getByText('42')).toBeInTheDocument(); // Completed last hour
  });

  it('shows offline worker badge when workers are offline', async () => {
    server.use(
      http.get('*/api/system/queue-metrics', () => {
        return HttpResponse.json({
          queues: {
            default: { pending: 5, active: 2 },
            opentofu: { pending: 0, active: 0 },
          },
          workers: { total: 4, active: 3, offline: 1 },
          tasks: { pending: 5, active: 2, completed_last_hour: 10 },
        });
      })
    );

    render(<QueueMetricsPanel />);

    await waitFor(() => {
      expect(screen.getByText('1 offline')).toBeInTheDocument();
    });
  });
});
