import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { SystemHealthPanel } from '../SystemHealthPanel';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('SystemHealthPanel', () => {
  it('renders all service statuses', async () => {
    // Override the default handler with the shape SystemHealthPanel expects
    server.use(
      http.get('*/api/system/health', () => {
        return HttpResponse.json({
          status: 'healthy',
          services: {
            backend: { status: 'healthy', response_time_ms: 12.5 },
            database: { status: 'healthy', response_time_ms: 2.1 },
            redis: { status: 'healthy', response_time_ms: 0.8 },
            celery: { status: 'healthy', workers: 3 },
          },
        });
      })
    );

    render(<SystemHealthPanel />);

    await waitFor(() => {
      expect(screen.getByText('Backend API')).toBeInTheDocument();
    });
    expect(screen.getByText('PostgreSQL')).toBeInTheDocument();
    expect(screen.getByText('Redis')).toBeInTheDocument();
    expect(screen.getByText('Celery Workers')).toBeInTheDocument();
    expect(screen.getByText('3 workers')).toBeInTheDocument();
  });

  it('shows error state when health check fails', async () => {
    server.use(
      http.get('*/api/system/health', () => {
        return HttpResponse.json({ error: 'Unavailable' }, { status: 500 });
      })
    );

    render(<SystemHealthPanel />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load system health')).toBeInTheDocument();
    });
  });
});
