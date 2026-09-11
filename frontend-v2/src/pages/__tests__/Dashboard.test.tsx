/**
 * Tests for Dashboard page
 *
 * Covers: renders greeting, loading skeletons, Hero Omnisearch, Multi-Cloud Estate,
 * Fleets overview, and action buttons.
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

  it('renders Hero Omnisearch input with placeholder and filter chips', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(/search fqdn/i)
      ).toBeInTheDocument();
      expect(screen.getByText(/ingresses & vips/i)).toBeInTheDocument();
    });
  });

  it('renders Multi-Cloud Estate section with provider groups', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      expect(screen.getByText('Multi-Cloud Estate')).toBeInTheDocument();
      const awsPills = screen.getAllByText(/AWS/i);
      expect(awsPills.length).toBeGreaterThan(0);
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

  // Fleets section (fleet-entity model)
  it('renders Fleets section with all-fleets-healthy when fleets exist and are ready', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      const fleetHeadings = screen.getAllByText('Fleets');
      expect(fleetHeadings.length).toBeGreaterThan(0);
      expect(screen.getByText('All fleets healthy')).toBeInTheDocument();
      expect(screen.getByText('Fleet Dashboard')).toBeInTheDocument();
    });
  });

  it('renders BNK version in cluster cards', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      const bnkVersions = screen.getAllByText('BNK 2.3.0');
      expect(bnkVersions.length).toBeGreaterThanOrEqual(1);
    });
  });

  it('renders Fleet Dashboard link', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      expect(screen.getByText('Fleet Dashboard')).toBeInTheDocument();
    });
  });
});
