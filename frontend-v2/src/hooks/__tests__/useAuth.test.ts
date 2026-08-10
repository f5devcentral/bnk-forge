/**
 * Tests for auth store and useRole hook
 *
 * Covers: login sets token, logout clears state, role extraction works.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useAuthStore } from '@/stores/authStore';
import { useRole } from '@/hooks/useRole';
import type { User } from '@/types';

const mockAdmin: User = {
  id: 1,
  username: 'admin',
  email: 'admin@example.com',
  role: 'admin',
  is_active: true,
  must_change_password: false,
  last_login_at: '2026-02-18T00:00:00Z',
  created_at: '2026-01-01T00:00:00Z',
};

const mockOperator: User = {
  id: 2,
  username: 'operator',
  email: 'operator@example.com',
  role: 'operator',
  is_active: true,
  must_change_password: false,
  last_login_at: null,
  created_at: '2026-01-01T00:00:00Z',
};

const mockViewer: User = {
  id: 3,
  username: 'viewer',
  email: 'viewer@example.com',
  role: 'viewer',
  is_active: true,
  must_change_password: false,
  last_login_at: null,
  created_at: '2026-01-01T00:00:00Z',
};

describe('useAuthStore', () => {
  beforeEach(() => {
    // Reset store state before each test
    const { logout } = useAuthStore.getState();
    logout();
    localStorage.clear();
  });

  it('starts with unauthenticated state', () => {
    const state = useAuthStore.getState();
    expect(state.isAuthenticated).toBe(false);
    expect(state.user).toBeNull();
    expect(state.token).toBeNull();
  });

  it('login sets token and user', () => {
    const { login } = useAuthStore.getState();

    act(() => {
      login('mock-jwt-token', mockAdmin);
    });

    const state = useAuthStore.getState();
    expect(state.isAuthenticated).toBe(true);
    expect(state.token).toBe('mock-jwt-token');
    expect(state.user).toEqual(mockAdmin);
    expect(localStorage.getItem('auth_token')).toBe('mock-jwt-token');
  });

  it('logout clears all state', () => {
    const { login, logout } = useAuthStore.getState();

    act(() => {
      login('mock-jwt-token', mockAdmin);
    });

    expect(useAuthStore.getState().isAuthenticated).toBe(true);

    act(() => {
      logout();
    });

    const state = useAuthStore.getState();
    expect(state.isAuthenticated).toBe(false);
    expect(state.token).toBeNull();
    expect(state.user).toBeNull();
    expect(localStorage.getItem('auth_token')).toBeNull();
  });

  it('persists token in localStorage on login', () => {
    const { login } = useAuthStore.getState();

    act(() => {
      login('my-token-123', mockAdmin);
    });

    expect(localStorage.getItem('auth_token')).toBe('my-token-123');
  });

  it('removes token from localStorage on logout', () => {
    const { login, logout } = useAuthStore.getState();

    act(() => {
      login('my-token-123', mockAdmin);
    });

    act(() => {
      logout();
    });

    expect(localStorage.getItem('auth_token')).toBeNull();
  });
});

describe('useRole', () => {
  beforeEach(() => {
    const { logout } = useAuthStore.getState();
    logout();
    localStorage.clear();
  });

  it('returns viewer role when no user is logged in', () => {
    const { result } = renderHook(() => useRole());

    expect(result.current.role).toBe('viewer');
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.isOperator).toBe(false);
    expect(result.current.isViewer).toBe(true);
  });

  it('returns admin role for admin user', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });

    const { result } = renderHook(() => useRole());

    expect(result.current.role).toBe('admin');
    expect(result.current.isAdmin).toBe(true);
    expect(result.current.isOperator).toBe(true); // admin is also operator
    expect(result.current.isViewer).toBe(true);
    expect(result.current.roleLabel).toBe('Admin');
  });

  it('returns operator role for operator user', () => {
    act(() => {
      useAuthStore.getState().login('token', mockOperator);
    });

    const { result } = renderHook(() => useRole());

    expect(result.current.role).toBe('operator');
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.isOperator).toBe(true);
    expect(result.current.roleLabel).toBe('Operator');
  });

  it('hasRole checks specific roles', () => {
    act(() => {
      useAuthStore.getState().login('token', mockOperator);
    });

    const { result } = renderHook(() => useRole());

    expect(result.current.hasRole('operator')).toBe(true);
    expect(result.current.hasRole('admin')).toBe(false);
    expect(result.current.hasRole('viewer', 'operator')).toBe(true);
  });

  it('hasMinRole checks role hierarchy', () => {
    act(() => {
      useAuthStore.getState().login('token', mockOperator);
    });

    const { result } = renderHook(() => useRole());

    expect(result.current.hasMinRole('viewer')).toBe(true);
    expect(result.current.hasMinRole('operator')).toBe(true);
    expect(result.current.hasMinRole('admin')).toBe(false);
  });

  it('viewer cannot access operator or admin features', () => {
    act(() => {
      useAuthStore.getState().login('token', mockViewer);
    });

    const { result } = renderHook(() => useRole());

    expect(result.current.hasMinRole('viewer')).toBe(true);
    expect(result.current.hasMinRole('operator')).toBe(false);
    expect(result.current.hasMinRole('admin')).toBe(false);
  });
});
