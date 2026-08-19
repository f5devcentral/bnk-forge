/**
 * Tests for Login page component
 *
 * Covers: renders form, validates required fields, submits login, shows error on failure.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { useAuthStore } from '@/stores/authStore';
import Login from '@/pages/Login';

describe('Login', () => {
  beforeEach(() => {
    useAuthStore.getState().logout();
    localStorage.clear();
  });

  it('renders login form with username and password fields', () => {
    render(<Login />);

    expect(screen.getByPlaceholderText(/enter username/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/enter password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument();
  });

  it('renders BNK-Forge branding', () => {
    render(<Login />);

    expect(screen.getAllByText('BNK-Forge').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/F5 BIG-IP Next/i).length).toBeGreaterThanOrEqual(1);
  });

  it('username and password fields are required', async () => {
    const user = userEvent.setup();
    render(<Login />);

    // Fields use zod validation (not HTML required attribute).
    // Submit with empty fields and verify validation messages appear.
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByText(/username is required/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/password is required/i)).toBeInTheDocument();
  });

  it('submits login and navigates on success', async () => {
    const user = userEvent.setup();
    render(<Login />, { initialRoute: '/login' });

    await user.type(screen.getByPlaceholderText(/enter username/i), 'admin');
    await user.type(screen.getByPlaceholderText(/enter password/i), 'password');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      const state = useAuthStore.getState();
      expect(state.isAuthenticated).toBe(true);
      expect(state.token).toBe('mock-jwt-token');
    });
  });

  it('shows error message on login failure', async () => {
    server.use(
      http.post('*/api/auth/login', () => {
        return HttpResponse.json(
          { error: { code: 'UNAUTHORIZED', message: 'Invalid credentials' } },
          { status: 401 }
        );
      })
    );

    const user = userEvent.setup();
    render(<Login />);

    await user.type(screen.getByPlaceholderText(/enter username/i), 'admin');
    await user.type(screen.getByPlaceholderText(/enter password/i), 'wrong-password');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByText(/invalid credentials/i)).toBeInTheDocument();
    });
  });

  it('shows loading state during login', async () => {
    // Hold the response open for the life of the test WITHOUT a live timer.
    // The previous 200 ms setTimeout could outlive the test: it asserted the
    // loading state and returned, vitest tore jsdom down, and ~200 ms later
    // msw delivered the response via `new ProgressEvent(...)` -- which no
    // longer existed. "ReferenceError: ProgressEvent is not defined" as an
    // unhandled rejection, failing the whole run intermittently (it took
    // down CI on #158, a PR that touches no frontend code at all). Same
    // class as #153 / the SSOAuthDialog fix in #152: async completion racing
    // environment teardown. A never-settling promise holds the request open
    // with nothing left to fire after the test ends.
    server.use(
      http.post('*/api/auth/login', () => new Promise<never>(() => {}))
    );

    const user = userEvent.setup();
    render(<Login />);

    await user.type(screen.getByPlaceholderText(/enter username/i), 'admin');
    await user.type(screen.getByPlaceholderText(/enter password/i), 'password');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    // The button text should change during loading
    expect(screen.getByRole('button', { name: /signing in/i })).toBeInTheDocument();
  });

  it('shows password change form when must_change_password is true', async () => {
    server.use(
      http.post('*/api/auth/login', () => {
        return HttpResponse.json({
          token: 'temp-token',
          user: { id: 1, username: 'admin', email: 'admin@example.com', role: 'admin', is_active: true, must_change_password: true, last_login_at: null, created_at: '2026-01-01T00:00:00Z' },
          must_change_password: true,
        });
      })
    );

    const user = userEvent.setup();
    render(<Login />);

    await user.type(screen.getByPlaceholderText(/enter username/i), 'admin');
    await user.type(screen.getByPlaceholderText(/enter password/i), 'password');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByText(/change your default password/i)).toBeInTheDocument();
    });

    expect(screen.getByPlaceholderText(/enter new password/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/confirm new password/i)).toBeInTheDocument();
  });
});
