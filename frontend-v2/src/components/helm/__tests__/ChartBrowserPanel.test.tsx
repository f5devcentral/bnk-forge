/**
 * Tests for ChartBrowserPanel component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { ChartBrowserPanel } from '@/components/helm/ChartBrowserPanel';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

describe('ChartBrowserPanel', () => {
  const onClose = vi.fn();
  const onInstallChart = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders header with Chart Browser title and source name', () => {
    render(
      <ChartBrowserPanel source="bitnami" onClose={onClose} onInstallChart={onInstallChart} />
    );

    expect(screen.getByText('Chart Browser')).toBeInTheDocument();
    expect(screen.getByText('Bitnami')).toBeInTheDocument();
  });

  it('renders search input', () => {
    render(
      <ChartBrowserPanel source="bitnami" onClose={onClose} onInstallChart={onInstallChart} />
    );

    expect(screen.getByLabelText('Search Helm charts')).toBeInTheDocument();
  });

  it('calls onClose when close button is clicked', async () => {
    const user = userEvent.setup();
    render(
      <ChartBrowserPanel source="artifact-hub" onClose={onClose} onInstallChart={onInstallChart} />
    );

    // The close button uses the X icon in the header
    const buttons = screen.getAllByRole('button');
    // The close button is the first button in the header
    await user.click(buttons[0]);

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows empty state when no charts are returned', async () => {
    // Override to return empty results
    server.use(
      http.get('*/api/helm/charts/search', () => {
        return HttpResponse.json({ charts: [], count: 0 });
      })
    );

    render(
      <ChartBrowserPanel source="artifact-hub" onClose={onClose} onInstallChart={onInstallChart} />
    );

    await waitFor(() => {
      expect(screen.getByText('No Charts Found')).toBeInTheDocument();
    });
  });
});
