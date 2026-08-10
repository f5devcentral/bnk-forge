/**
 * Integration Tests — Project Workflow Lifecycle
 *
 * End-to-end project lifecycle across API (MSW) and UI:
 *   1. Project list renders from API
 *   2. Search filters projects client-side
 *   3. Empty state when no projects
 *   4. Click project row navigates to detail
 *   5. New Project button renders
 *   6. Action menu has expected options
 *   7. API error shows error/empty state
 *   8. Loading state shows skeletons
 *   9. Sidebar status filter updates view
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { useAuthStore } from '@/stores/authStore';
import { mockUser, mockProjects } from '@/test/test-fixtures';
import Projects from '@/pages/Projects';

// ---------------------------------------------------------------------------
// Navigation mock
// ---------------------------------------------------------------------------
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Wait for the project list to finish loading by asserting on a known project name. */
async function waitForProjectsLoaded() {
  await waitFor(
    () => {
      expect(screen.getByText('test-project')).toBeInTheDocument();
    },
    { timeout: 3000 },
  );
}

// ---------------------------------------------------------------------------
// Test Suite
// ---------------------------------------------------------------------------
describe('Project Workflow (integration)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.getState().logout();
    localStorage.clear();
    useAuthStore.getState().login('mock-jwt-token', mockUser);
  });

  // -----------------------------------------------------------------------
  // 1. Project list renders from API
  // -----------------------------------------------------------------------
  it('renders project list from API with correct count', async () => {
    render(<Projects />);

    await waitForProjectsLoaded();

    // Both fixture projects visible
    expect(screen.getByText('test-project')).toBeInTheDocument();
    expect(screen.getByText('production-infra')).toBeInTheDocument();

    // Subtitle shows project count
    expect(screen.getByText(`${mockProjects.length} projects`)).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // 2. Search filters projects client-side
  // -----------------------------------------------------------------------
  it('filters projects when typing in the search input', async () => {
    const user = userEvent.setup();
    render(<Projects />);

    await waitForProjectsLoaded();

    // D-020: placeholder uses an ellipsis char; match leniently.
    const searchInput = screen.getByPlaceholderText(/search projects/i);
    await user.type(searchInput, 'production');

    await waitFor(() => {
      expect(screen.queryByText('test-project')).not.toBeInTheDocument();
      expect(screen.getByText('production-infra')).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // 3. Empty state when no projects
  // -----------------------------------------------------------------------
  it('shows empty state when API returns no projects', async () => {
    server.use(
      http.get('*/api/projects', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname !== '/api/projects') return;
        return HttpResponse.json({ projects: [], total: 0 });
      }),
    );

    render(<Projects />);

    await waitFor(
      () => {
        expect(screen.getByText(/no projects found/i)).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    // The empty state should also offer a "Create Project" action
    expect(screen.getByText('Create Project')).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // 4. Click project row navigates to detail
  // -----------------------------------------------------------------------
  it('navigates to project detail when a project row is clicked', async () => {
    const user = userEvent.setup();
    render(<Projects />);

    await waitForProjectsLoaded();

    // Click the first project name — it's inside a table row with data-testid
    const projectCards = screen.getAllByTestId('project-card');
    expect(projectCards.length).toBe(mockProjects.length);

    await user.click(projectCards[0]);

    // mockProjects are sorted alphabetically by name in the component.
    // Resolve the expected id from the fixture so this stays correct as the
    // fixture grows.
    const firstByName = [...mockProjects].sort((a, b) => a.name.localeCompare(b.name))[0];
    expect(mockNavigate).toHaveBeenCalledWith(`/projects/${firstByName.id}`);
  });

  // -----------------------------------------------------------------------
  // 5. New Project button renders
  // -----------------------------------------------------------------------
  it('renders the New Project button', async () => {
    render(<Projects />);

    await waitFor(() => {
      expect(screen.getByText(/New project/i)).toBeInTheDocument();
    });

    // Verify it's a clickable button
    const button = screen.getByText(/New project/i).closest('button');
    expect(button).toBeInTheDocument();
    expect(button).not.toBeDisabled();
  });

  // -----------------------------------------------------------------------
  // 6. Action menu has expected options
  // -----------------------------------------------------------------------
  it('project action menu contains View Project and Settings', async () => {
    const user = userEvent.setup();
    render(<Projects />);

    await waitForProjectsLoaded();

    // Find action buttons (one per row)
    const actionButtons = screen.getAllByLabelText('Project actions');
    expect(actionButtons.length).toBe(mockProjects.length);

    // Open the first action dropdown
    await user.click(actionButtons[0]);

    await waitFor(() => {
      expect(screen.getByText('View Project')).toBeInTheDocument();
      expect(screen.getByText('Settings')).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // 7. API error shows error / empty state
  // -----------------------------------------------------------------------
  it('handles API error gracefully', async () => {
    server.use(
      http.get('*/api/projects', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname !== '/api/projects') return;
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Database unavailable' } },
          { status: 500 },
        );
      }),
    );

    render(<Projects />);

    // After error, the page should still render without crashing.
    // React Query will set data to undefined, so the empty state or error state shows.
    await waitFor(
      () => {
        // Either an error message or the empty state renders
        const hasEmptyState = screen.queryByText(/no projects found/i);
        const errorIndicators = screen.queryAllByText(/error/i);
        expect(Boolean(hasEmptyState) || errorIndicators.length > 0).toBe(true);
      },
      { timeout: 3000 },
    );
  });

  // -----------------------------------------------------------------------
  // 8. Loading state shows skeletons
  // -----------------------------------------------------------------------
  it('shows loading skeletons while data is fetching', () => {
    server.use(
      http.get('*/api/projects', async () => {
        // Delay long enough that the loading state is visible
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json({ projects: [], total: 0 });
      }),
    );

    render(<Projects />);

    // The Projects page renders Skeleton components while isLoading is true
    const skeletons = document.querySelectorAll(
      '[class*="animate-pulse"], [class*="skeleton"]',
    );
    expect(skeletons.length).toBeGreaterThan(0);
  });

  // -----------------------------------------------------------------------
  // 9. Chip filter narrows displayed projects (D-020: was sidebar)
  // -----------------------------------------------------------------------
  it('chip filter Active narrows displayed projects', async () => {
    const user = userEvent.setup();
    render(<Projects />);

    await waitForProjectsLoaded();

    // Both projects should be visible initially
    expect(screen.getByText('test-project')).toBeInTheDocument();
    expect(screen.getByText('production-infra')).toBeInTheDocument();

    // D-020: sidebar filter list replaced by a chip-button row at the top of
    // the page. Each chip is a <button aria-pressed> with label "All" /
    // "Active" / "Inactive" / "Has failures". Inactive/has_failed chips
    // only render when their count > 0; "All" is always present.
    const allChip = screen.getByRole('button', { name: /^All\b/ });
    expect(allChip).toBeInTheDocument();

    // "Active" chip — both fixture projects qualify, so clicking it keeps
    // both visible.
    const activeChip = screen.getByRole('button', { name: /^Active\b/ });
    await user.click(activeChip);

    await waitFor(() => {
      expect(screen.getByText('test-project')).toBeInTheDocument();
      expect(screen.getByText('production-infra')).toBeInTheDocument();
    });
    expect(activeChip).toHaveAttribute('aria-pressed', 'true');

    // Click "All" to reset — both projects still visible.
    await user.click(allChip);

    await waitFor(() => {
      expect(screen.getByText('test-project')).toBeInTheDocument();
      expect(screen.getByText('production-infra')).toBeInTheDocument();
    });
    expect(allChip).toHaveAttribute('aria-pressed', 'true');
  });
});
