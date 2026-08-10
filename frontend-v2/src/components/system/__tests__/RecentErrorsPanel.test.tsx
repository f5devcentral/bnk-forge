import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { RecentErrorsPanel } from '../RecentErrorsPanel';

// The default handler serves mockRecentErrors at */api/system/errors

beforeEach(() => {
  vi.clearAllMocks();
});

describe('RecentErrorsPanel', () => {
  it('renders recent errors with type badge and project name', async () => {
    render(<RecentErrorsPanel />);

    await waitFor(() => {
      expect(screen.getByText('Recent Errors')).toBeInTheDocument();
    });
    // Wait for the data to actually render
    await waitFor(() => {
      expect(screen.getByText('connection_error')).toBeInTheDocument();
    });
    expect(screen.getByText('test-project')).toBeInTheDocument();
    expect(screen.getByText('Connection refused to K8s API')).toBeInTheDocument();
  });

  it('shows empty state when no errors', async () => {
    server.use(
      http.get('*/api/system/errors', () => {
        return HttpResponse.json({ errors: [], total: 0 });
      })
    );

    render(<RecentErrorsPanel />);

    await waitFor(() => {
      expect(screen.getByText('No recent errors')).toBeInTheDocument();
    });
  });
});
