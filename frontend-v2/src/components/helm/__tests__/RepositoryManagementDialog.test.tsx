/**
 * Tests for RepositoryManagementDialog component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { RepositoryManagementDialog } from '@/components/helm/RepositoryManagementDialog';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

const mockRepos = [
  { name: 'bitnami', url: 'https://charts.bitnami.com/bitnami', charts_count: 42 },
  { name: 'ingress-nginx', url: 'https://kubernetes.github.io/ingress-nginx', charts_count: 5 },
];

describe('RepositoryManagementDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    // Override the MSW handler to return proper response format
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
    render(<RepositoryManagementDialog {...defaultProps} />);

    expect(screen.getByText('Helm Repositories')).toBeInTheDocument();
    expect(screen.getByText(/Manage Helm chart repositories/)).toBeInTheDocument();
  });

  it('renders Add Repository and Update All buttons', () => {
    render(<RepositoryManagementDialog {...defaultProps} />);

    expect(screen.getByRole('button', { name: /add repository/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /update all/i })).toBeInTheDocument();
  });

  it('shows repository list from API', async () => {
    render(<RepositoryManagementDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('bitnami')).toBeInTheDocument();
      expect(screen.getByText('ingress-nginx')).toBeInTheDocument();
    });
  });

  it('shows add form when Add Repository button is clicked', async () => {
    const user = userEvent.setup();
    render(<RepositoryManagementDialog {...defaultProps} />);

    await user.click(screen.getByRole('button', { name: /add repository/i }));

    expect(screen.getByLabelText(/repository name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/repository url/i)).toBeInTheDocument();
  });

  it('does not render when open is false', () => {
    render(<RepositoryManagementDialog {...defaultProps} open={false} />);

    expect(screen.queryByText('Helm Repositories')).not.toBeInTheDocument();
  });
});
