import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { PerformanceMetricsPanel } from '../PerformanceMetricsPanel';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('PerformanceMetricsPanel', () => {
  it('renders API, database, and task performance metrics', async () => {
    server.use(
      http.get('*/api/system/performance', () => {
        return HttpResponse.json({
          api: {
            avg_response_time_ms: 45.3,
            requests_last_hour: 1200,
            failed_requests_last_hour: 3,
          },
          database: {
            size_mb: 128.5,
            connections: 12,
            slow_queries_last_hour: 2,
          },
          tasks: {
            avg_duration_seconds: 8.5,
            longest_running: { type: 'apply', duration: 120 },
          },
        });
      })
    );

    render(<PerformanceMetricsPanel />);

    // Wait for actual data to render (not just the loading header)
    await waitFor(() => {
      expect(screen.getByText('API Performance')).toBeInTheDocument();
    });
    // avg_response_time_ms.toFixed(0) = "45" + "ms"
    expect(screen.getByText('45ms')).toBeInTheDocument();
    expect(screen.getByText('1200')).toBeInTheDocument();
    expect(screen.getByText('Database')).toBeInTheDocument();
    expect(screen.getByText('128.5 MB')).toBeInTheDocument();
    expect(screen.getByText('Task Execution')).toBeInTheDocument();
    expect(screen.getByText('8.5s')).toBeInTheDocument();
  });

  it('shows error badge when failed requests exist', async () => {
    server.use(
      http.get('*/api/system/performance', () => {
        return HttpResponse.json({
          api: {
            avg_response_time_ms: 45.3,
            requests_last_hour: 1200,
            failed_requests_last_hour: 3,
          },
          database: {
            size_mb: 128.5,
            connections: 12,
            slow_queries_last_hour: 0,
          },
          tasks: {
            avg_duration_seconds: 8.5,
            longest_running: null,
          },
        });
      })
    );

    render(<PerformanceMetricsPanel />);

    await waitFor(() => {
      expect(screen.getByText('Errors')).toBeInTheDocument();
    });
  });
});
