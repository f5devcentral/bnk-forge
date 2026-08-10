/**
 * Tests for Projects page
 *
 * Covers: renders project list, loading state, search filter, status filter,
 * create dialog, empty state, navigation to project detail.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import Projects from '@/pages/Projects';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

describe('Projects', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders page title and New Project button', async () => {
    render(<Projects />);
    await waitFor(() => {
      expect(screen.getByText('Projects')).toBeInTheDocument();
      expect(screen.getByText(/new project/i)).toBeInTheDocument();
    });
  });

  it('renders loading skeletons while data is fetching', () => {
    server.use(
      http.get('*/api/projects', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json({ projects: [], total: 0 });
      }),
    );
    render(<Projects />);
    const skeletons = document.querySelectorAll('[class*="animate-pulse"], [class*="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('renders project names from API data', async () => {
    render(<Projects />);
    await waitFor(() => {
      expect(screen.getByText('test-project')).toBeInTheDocument();
      expect(screen.getByText('production-infra')).toBeInTheDocument();
    });
  });

  it('renders target platform context badge in table', async () => {
    render(<Projects />);

    await waitFor(() => {
      expect(screen.getByText('Target: Amazon EKS')).toBeInTheDocument();
    });
  });

  it('filters projects by search query', async () => {
    const user = userEvent.setup();
    render(<Projects />);
    await waitFor(() => {
      expect(screen.getByText('test-project')).toBeInTheDocument();
    });
    const searchInput = screen.getByPlaceholderText(/search/i);
    await user.type(searchInput, 'production');
    await waitFor(() => {
      expect(screen.queryByText('test-project')).not.toBeInTheDocument();
      expect(screen.getByText('production-infra')).toBeInTheDocument();
    });
  });

  it('shows chip filters for project status', async () => {
    // D-020: sidebar with status filters replaced by a chip-filter row.
    // "All" chip is always present; data-dependent chips (Active, Mine, …)
    // are gated on counts so we only assert on the always-present one.
    render(<Projects />);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^All\b/ })).toBeInTheDocument();
    });
  });

  it('renders empty state when no projects exist', async () => {
    server.use(
      http.get('*/api/projects', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname !== '/api/projects') return;
        return HttpResponse.json({ projects: [], total: 0 });
      }),
    );
    render(<Projects />);
    await waitFor(() => {
      expect(screen.getByText(/no projects/i)).toBeInTheDocument();
    });
  });

  it('opens create project dialog when New Project is clicked', async () => {
    const user = userEvent.setup();
    render(<Projects />);
    await waitFor(() => {
      expect(screen.getByText(/new project/i)).toBeInTheDocument();
    });
    await user.click(screen.getByText(/new project/i));
    await waitFor(() => {
      expect(screen.getByText(/create new project/i)).toBeInTheDocument();
    });
  });

  it('navigates to project detail when project row is clicked', async () => {
    const user = userEvent.setup();
    render(<Projects />);
    await waitFor(() => {
      expect(screen.getByText('test-project')).toBeInTheDocument();
    });
    await user.click(screen.getByText('test-project'));
    expect(mockNavigate).toHaveBeenCalledWith('/projects/1');
  });
});
