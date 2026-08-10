/**
 * Tests for ManageRepositoriesDialog component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { ManageRepositoriesDialog } from '@/components/helm/ManageRepositoriesDialog';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

const mockRepos = [
  { name: 'bitnami', url: 'https://charts.bitnami.com/bitnami' },
  { name: 'ingress-nginx', url: 'https://kubernetes.github.io/ingress-nginx' },
];

describe('ManageRepositoriesDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    // Override the MSW handler to return the proper response format
    server.use(
      http.get('*/api/helm/repositories', () => {
        return HttpResponse.json({
          success: true,
          repositories: mockRepos,
          count: mockRepos.length,
        });
      })
    );
  });

  it('renders dialog title and description', () => {
    render(<ManageRepositoriesDialog {...defaultProps} />);

    expect(screen.getByText('Manage Helm Repositories')).toBeInTheDocument();
    expect(screen.getByText(/Add chart repositories to search and install/)).toBeInTheDocument();
  });

  it('renders three tabs: Configured, Popular, Add Custom', () => {
    render(<ManageRepositoriesDialog {...defaultProps} />);

    expect(screen.getByRole('tab', { name: /configured/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /popular/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /add custom/i })).toBeInTheDocument();
  });

  it('shows configured repositories from API', async () => {
    render(<ManageRepositoriesDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('bitnami')).toBeInTheDocument();
      expect(screen.getByText('ingress-nginx')).toBeInTheDocument();
    });
  });

  it('shows popular repositories in the Popular tab', async () => {
    const user = userEvent.setup();
    render(<ManageRepositoriesDialog {...defaultProps} />);

    await user.click(screen.getByRole('tab', { name: /popular/i }));

    expect(screen.getByText('jetstack')).toBeInTheDocument();
    expect(screen.getByText('prometheus-community')).toBeInTheDocument();
    expect(screen.getByText('grafana')).toBeInTheDocument();
  });

  it('renders Close button and calls onOpenChange when clicked', async () => {
    const user = userEvent.setup();
    render(<ManageRepositoriesDialog {...defaultProps} />);

    // Wait for repos to load to ensure dialog is fully rendered
    await waitFor(() => {
      expect(screen.getByText('bitnami')).toBeInTheDocument();
    });

    // Multiple close buttons exist (X in header and "Close" at bottom)
    // Use getAllByRole and pick the last one (the explicit Close button)
    const closeButtons = screen.getAllByRole('button', { name: /^close$/i });
    await user.click(closeButtons[closeButtons.length - 1]);

    expect(defaultProps.onOpenChange).toHaveBeenCalledWith(false);
  });

  it('does not render when open is false', () => {
    render(<ManageRepositoriesDialog {...defaultProps} open={false} />);

    expect(screen.queryByText('Manage Helm Repositories')).not.toBeInTheDocument();
  });
});
