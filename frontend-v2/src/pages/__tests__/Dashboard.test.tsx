/**
 * Tests for Dashboard page
 *
 * Covers: renders greeting, loading skeletons, data display (projects, clusters,
 * fleet health, stats), action buttons, enriched cluster cards with BNK data.
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import Dashboard from '@/pages/Dashboard';

// Mock navigation
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

describe('Dashboard', () => {
  it('renders greeting text', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      const greeting = screen.queryByText(/good (morning|afternoon|evening)/i);
      expect(greeting).toBeInTheDocument();
    });
  });

  it('renders loading skeletons while data is fetching', () => {
    server.use(
      http.get('*/api/projects', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json({ projects: [], total: 0 });
      }),
    );
    render(<Dashboard />);
    const skeletons = document.querySelectorAll('[class*="animate-pulse"], [class*="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('renders action buttons (Add Cluster, New Project)', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      expect(screen.getByText(/add cluster/i)).toBeInTheDocument();
      expect(screen.getByText(/new project/i)).toBeInTheDocument();
    });
  });

  it('navigates to /projects?action=create when New Project is clicked', async () => {
    const user = userEvent.setup();
    render(<Dashboard />);
    await waitFor(() => {
      expect(screen.getByText(/new project/i)).toBeInTheDocument();
    });
    await user.click(screen.getByText(/new project/i));
    expect(mockNavigate).toHaveBeenCalledWith('/projects?action=create');
  });

  it('renders Projects section heading', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      const projectsElements = screen.getAllByText('Projects');
      expect(projectsElements.length).toBeGreaterThan(0);
    });
  });

  it('renders project names after data loads', async () => {
    render(<Dashboard />);
    await waitFor(
      () => {
        const projectElements = screen.getAllByText('test-project');
        expect(projectElements.length).toBeGreaterThan(0);
      },
      { timeout: 3000 },
    );
  });

  it('renders empty state for projects when none exist', async () => {
    server.use(
      http.get('*/api/projects', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname !== '/api/projects') return;
        return HttpResponse.json({ projects: [], total: 0 });
      }),
    );
    render(<Dashboard />);
    await waitFor(() => {
      // Should still render the page
      expect(screen.getByText(/good (morning|afternoon|evening)/i)).toBeInTheDocument();
    });
  });

  // D-022 P6: Fleets section (fleet-entity model)
  it('renders Fleets section with all-fleets-healthy when fleets exist and are ready', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      // The global MSW handler returns mockFleetTargets (1 fleet, worst_state: 'ready'), so the section mounts on data.
      const fleetHeadings = screen.getAllByText('Fleets');
      expect(fleetHeadings.length).toBeGreaterThan(0);
      // Assert: 1 fleet with worst_state='ready' → healthStateFromRollup returns 'green' → no attention needed → "All fleets healthy"
      expect(screen.getByText('All fleets healthy')).toBeInTheDocument();
      expect(screen.getByText('Fleet Dashboard')).toBeInTheDocument();
    });
  });

  it('renders BNK version in enriched cluster cards', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      // BNK version appears in the enriched cluster cards (Clusters section)
      const bnkVersions = screen.getAllByText('BNK 2.3.0');
      expect(bnkVersions.length).toBeGreaterThanOrEqual(1);
    });
  });

  // Stats row includes Fleet
  it('renders Fleet stat card', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      expect(screen.getByText('Fleet')).toBeInTheDocument();
    });
  });

  it('renders Clusters section heading', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      const clusterElements = screen.getAllByText('Clusters');
      expect(clusterElements.length).toBeGreaterThan(0);
    });
  });

  it('renders Fleet Dashboard link', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      expect(screen.getByText('Fleet Dashboard')).toBeInTheDocument();
    });
  });

  it('renders Recent Operations section', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      expect(screen.getByText('Recent Operations')).toBeInTheDocument();
    });
  });
});
