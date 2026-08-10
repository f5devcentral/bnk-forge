/**
 * Auth Flow Integration Tests
 *
 * Tests multi-page authentication workflows: login -> dashboard, auth guards,
 * logout, and role-based navigation visibility.
 *
 * Individual login form behavior (validation, error states, loading) is covered
 * in components/__tests__/Login.test.tsx. These tests focus on cross-component
 * auth orchestration.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { useAuthStore } from '@/stores/authStore';
import { mockUser, mockViewerUser } from '@/test/test-fixtures';
import Login from '@/pages/Login';
import { AuthGuard } from '@/components/layout/AuthGuard';
import { Sidebar } from '@/components/layout/Sidebar';

// ---------------------------------------------------------------------------
// Navigation mock — capture navigate calls without full router integration
// ---------------------------------------------------------------------------

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

// ---------------------------------------------------------------------------
// Common setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  mockNavigate.mockReset();
  act(() => {
    useAuthStore.getState().logout();
  });
  localStorage.clear();
});

// ===========================================================================
// Tests
// ===========================================================================

describe('Auth Flow Integration', () => {
  // -------------------------------------------------------------------------
  // 1. Login redirects to dashboard on success
  // -------------------------------------------------------------------------
  describe('login -> dashboard redirect', () => {
    it('updates auth store and navigates to / on successful login', async () => {
      const user = userEvent.setup();
      render(<Login />, { initialRoute: '/login' });

      await user.type(screen.getByPlaceholderText('Enter username'), 'admin');
      await user.type(screen.getByPlaceholderText('Enter password'), 'password');
      await user.click(screen.getByRole('button', { name: /sign in/i }));

      await waitFor(() => {
        const state = useAuthStore.getState();
        expect(state.isAuthenticated).toBe(true);
        expect(state.token).toBe('mock-jwt-token');
        expect(state.user?.username).toBe('admin');
      });

      expect(mockNavigate).toHaveBeenCalledWith('/');
    });
  });

  // -------------------------------------------------------------------------
  // 2. AuthGuard blocks unauthenticated access
  // -------------------------------------------------------------------------
  describe('AuthGuard protection', () => {
    it('does not render children when user is not authenticated', () => {
      render(
        <AuthGuard>
          <div data-testid="protected-content">Dashboard Content</div>
        </AuthGuard>
      );

      // AuthGuard should redirect (renders <Navigate to="/login" />) and
      // NOT render the protected children
      expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument();
      expect(screen.queryByText('Dashboard Content')).not.toBeInTheDocument();
    });

    it('renders children when user is authenticated', () => {
      act(() => {
        useAuthStore.getState().login('mock-jwt-token', mockUser);
      });

      render(
        <AuthGuard>
          <div data-testid="protected-content">Dashboard Content</div>
        </AuthGuard>
      );

      expect(screen.getByTestId('protected-content')).toBeInTheDocument();
      expect(screen.getByText('Dashboard Content')).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // 3. Logout clears state
  // -------------------------------------------------------------------------
  describe('logout flow', () => {
    it('clears auth state when logout button is clicked', async () => {
      const _user = userEvent.setup();

      // Set up authenticated state before render
      act(() => {
        useAuthStore.getState().login('mock-jwt-token', mockUser);
      });

      render(<Sidebar />);

      // Verify user is shown in the sidebar footer (username + role badge both say "admin")
      await waitFor(() => {
        expect(screen.getAllByText('admin').length).toBeGreaterThanOrEqual(1);
      });

      // Click the logout button via fireEvent (userEvent may be blocked by
      // pointer-events or layout in jsdom for small icon buttons)
      const logoutButton = screen.getByRole('button', { name: 'Logout' });
      await act(async () => {
        logoutButton.click();
      });

      // Verify navigation to login was triggered (confirms the click handler ran)
      expect(mockNavigate).toHaveBeenCalledWith('/login');

      // Verify auth store is cleared
      expect(useAuthStore.getState().user).toBeNull();
      expect(useAuthStore.getState().token).toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // 4. Admin sees all navigation items including System
  // -------------------------------------------------------------------------
  describe('admin navigation visibility', () => {
    it('shows SETTINGS section with System and Access Methods for admin users', async () => {
      act(() => {
        useAuthStore.getState().login('mock-jwt-token', mockUser);
      });

      render(<Sidebar />);

      await waitFor(() => {
        expect(screen.getByText('SETTINGS')).toBeInTheDocument();
      });

      expect(screen.getByText('System')).toBeInTheDocument();
      expect(screen.getByText('Access Methods')).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // 5. Viewer cannot see Settings section
  // -------------------------------------------------------------------------
  describe('viewer navigation restrictions', () => {
    it('hides SETTINGS section and its items for viewer users', async () => {
      act(() => {
        useAuthStore.getState().login('mock-jwt-token', mockViewerUser);
      });

      render(<Sidebar />);

      // Wait for sidebar to render non-restricted content
      await waitFor(() => {
        expect(screen.getByText('OBSERVE')).toBeInTheDocument();
      });

      // SETTINGS section and its items should not be visible
      expect(screen.queryByText('SETTINGS')).not.toBeInTheDocument();
      expect(screen.queryByText('System')).not.toBeInTheDocument();
      expect(screen.queryByText('Access Methods')).not.toBeInTheDocument();
    });
  });
});
