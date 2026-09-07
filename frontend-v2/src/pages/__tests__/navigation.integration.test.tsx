/**
 * Navigation & Routing Integration Tests
 *
 * Tests that the Sidebar and Header components render correct navigation
 * structure, links, breadcrumbs, and interactive elements when composed
 * together with the router and auth state.
 *
 * Lower-level auth-gated visibility (viewer vs admin) is covered in
 * auth-flow.integration.test.tsx. These tests focus on navigation structure
 * and header context for an authenticated admin user.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, act } from '@testing-library/react';
import { render } from '@/test/test-utils';
import { useAuthStore } from '@/stores/authStore';
import { mockUser } from '@/test/test-fixtures';
import { Sidebar } from '@/components/layout/Sidebar';
import { Header } from '@/components/layout/Header';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

// ---------------------------------------------------------------------------
// Navigation mock — capture navigate calls without full router integration
// ---------------------------------------------------------------------------

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

// ---------------------------------------------------------------------------
// MSW overrides for Header notification endpoints (not in default handlers)
// ---------------------------------------------------------------------------

beforeEach(() => {
  mockNavigate.mockReset();
  act(() => {
    useAuthStore.getState().logout();
  });
  localStorage.clear();

  // Add notification handlers so Header renders without unhandled request warnings
  server.use(
    http.get('*/api/notifications', () => {
      return HttpResponse.json([]);
    }),
    http.get('*/api/notifications/unread-count', () => {
      return HttpResponse.json({ count: 0 });
    }),
  );
});

// ===========================================================================
// Sidebar Tests
// ===========================================================================

describe('Navigation Integration', () => {
  // -------------------------------------------------------------------------
  // 1. Sidebar renders all navigation sections
  // -------------------------------------------------------------------------
  describe('Sidebar renders all navigation sections', () => {
    it('shows OBSERVE, BUILD, OPERATE, and SETTINGS section headings with key nav items for admin', async () => {
      act(() => {
        useAuthStore.getState().login('mock-jwt-token', mockUser);
      });

      render(<Sidebar />);

      // Wait for async data (badge counts) to settle
      await waitFor(() => {
        expect(screen.getByText('OBSERVE')).toBeInTheDocument();
      });

      // Section headings
      expect(screen.getByText('BUILD')).toBeInTheDocument();
      expect(screen.getByText('OPERATE')).toBeInTheDocument();
      expect(screen.getByText('SETTINGS')).toBeInTheDocument();

      // Command Center — standalone home item above the OBSERVE section
      expect(screen.getByText('Command Center')).toBeInTheDocument();

      // BUILD items — Catalog sits at the bottom after Operations Log.
      expect(screen.getByText('Access Methods')).toBeInTheDocument();
      expect(screen.getByText('Blueprints')).toBeInTheDocument();
      expect(screen.getByText('Projects')).toBeInTheDocument();
      expect(screen.getByText('Operations Log')).toBeInTheDocument();
      expect(screen.getByText('Catalog')).toBeInTheDocument();
      // K8S-UX-004: Helm Packages removed from sidebar — now integrated into Kubernetes page

      // OPERATE items
      expect(screen.getByText('Fleet')).toBeInTheDocument();
      expect(screen.getByText('Kubernetes')).toBeInTheDocument();
      expect(screen.getByText('F5 BNK')).toBeInTheDocument();
      // K8S-UX-005: Operators merged into Fleet page — no longer a separate sidebar entry

      // SETTINGS items (admin only)
      expect(screen.getByText('System')).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // 2. Sidebar nav links have correct hrefs
  // -------------------------------------------------------------------------
  describe('Sidebar nav links have correct hrefs', () => {
    it('renders Projects with href="/projects", Activity with href="/tasks", Blueprints with href="/stacks"', async () => {
      act(() => {
        useAuthStore.getState().login('mock-jwt-token', mockUser);
      });

      render(<Sidebar />);

      await waitFor(() => {
        expect(screen.getByText('Projects')).toBeInTheDocument();
      });

      // NavLink renders an <a> tag — find the link by its text content
      const projectsLink = screen.getByText('Projects').closest('a');
      expect(projectsLink).toHaveAttribute('href', '/projects');

      const opsLogLink = screen.getByText('Operations Log').closest('a');
      expect(opsLogLink).toHaveAttribute('href', '/tasks');

      const blueprintsLink = screen.getByText('Blueprints').closest('a');
      expect(blueprintsLink).toHaveAttribute('href', '/stacks');
    });
  });

  // -------------------------------------------------------------------------
  // 3. Header renders breadcrumbs for route
  // -------------------------------------------------------------------------
  describe('Header renders breadcrumbs for route', () => {
    it('shows "Build" breadcrumb and "Projects" page title when at /projects', async () => {
      act(() => {
        useAuthStore.getState().login('mock-jwt-token', mockUser);
      });

      render(<Header />, { initialRoute: '/projects' });

      await waitFor(() => {
        expect(screen.getByText('Build')).toBeInTheDocument();
      });

      expect(screen.getByText('Projects')).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // 4. Header search button renders with keyboard shortcut
  // -------------------------------------------------------------------------
  describe('Header search button renders with keyboard shortcut', () => {
    it('displays "Search..." text and ⌘K shortcut badge', async () => {
      act(() => {
        useAuthStore.getState().login('mock-jwt-token', mockUser);
      });

      render(<Header />, { initialRoute: '/' });

      await waitFor(() => {
        expect(screen.getByText('Search...')).toBeInTheDocument();
      });

      expect(screen.getByText('⌘K')).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // 5. Header notification bell renders
  // -------------------------------------------------------------------------
  describe('Header notification bell renders', () => {
    it('renders a notification bell button with aria-label="Notifications"', async () => {
      act(() => {
        useAuthStore.getState().login('mock-jwt-token', mockUser);
      });

      render(<Header />, { initialRoute: '/' });

      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'Notifications' })).toBeInTheDocument();
      });
    });
  });
});
