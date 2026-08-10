/**
 * Tests for RoleGuard component
 *
 * Covers: renders children for correct role, hides for wrong role,
 * shows fallback content when role is insufficient.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '@/test/test-utils';
import { act } from '@testing-library/react';
import { useAuthStore } from '@/stores/authStore';
import { RoleGuard } from '@/components/layout/RoleGuard';
import type { User } from '@/types';

const mockAdmin: User = {
  id: 1,
  username: 'admin',
  email: 'admin@example.com',
  role: 'admin',
  is_active: true,
  must_change_password: false,
  last_login_at: null,
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

describe('RoleGuard', () => {
  beforeEach(() => {
    useAuthStore.getState().logout();
    localStorage.clear();
  });

  it('renders children when user has required admin role', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });

    render(
      <RoleGuard require="admin">
        <div>Admin Content</div>
      </RoleGuard>
    );

    expect(screen.getByText('Admin Content')).toBeInTheDocument();
  });

  it('renders children when user has higher role than required', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });

    render(
      <RoleGuard require="operator">
        <div>Operator Content</div>
      </RoleGuard>
    );

    // Admin has higher role than operator, so content should show
    expect(screen.getByText('Operator Content')).toBeInTheDocument();
  });

  it('hides children when user has insufficient role', () => {
    act(() => {
      useAuthStore.getState().login('token', mockViewer);
    });

    render(
      <RoleGuard require="admin">
        <div>Admin Content</div>
      </RoleGuard>
    );

    expect(screen.queryByText('Admin Content')).not.toBeInTheDocument();
  });

  it('shows fallback when role is insufficient', () => {
    act(() => {
      useAuthStore.getState().login('token', mockViewer);
    });

    render(
      <RoleGuard require="admin" fallback={<div>Read Only</div>}>
        <div>Admin Content</div>
      </RoleGuard>
    );

    expect(screen.queryByText('Admin Content')).not.toBeInTheDocument();
    expect(screen.getByText('Read Only')).toBeInTheDocument();
  });

  it('operator can see operator-required content', () => {
    act(() => {
      useAuthStore.getState().login('token', mockOperator);
    });

    render(
      <RoleGuard require="operator">
        <div>Deploy Button</div>
      </RoleGuard>
    );

    expect(screen.getByText('Deploy Button')).toBeInTheDocument();
  });

  it('operator cannot see admin-required content', () => {
    act(() => {
      useAuthStore.getState().login('token', mockOperator);
    });

    render(
      <RoleGuard require="admin">
        <div>Delete System</div>
      </RoleGuard>
    );

    expect(screen.queryByText('Delete System')).not.toBeInTheDocument();
  });

  it('viewer can see viewer-required content', () => {
    act(() => {
      useAuthStore.getState().login('token', mockViewer);
    });

    render(
      <RoleGuard require="viewer">
        <div>Dashboard</div>
      </RoleGuard>
    );

    expect(screen.getByText('Dashboard')).toBeInTheDocument();
  });

  it('unauthenticated user defaults to viewer role', () => {
    // No login — defaults to viewer
    render(
      <RoleGuard require="viewer">
        <div>Public Content</div>
      </RoleGuard>
    );

    expect(screen.getByText('Public Content')).toBeInTheDocument();
  });

  it('unauthenticated user cannot see operator content', () => {
    // No login — defaults to viewer
    render(
      <RoleGuard require="operator">
        <div>Protected Content</div>
      </RoleGuard>
    );

    expect(screen.queryByText('Protected Content')).not.toBeInTheDocument();
  });

  it('renders nothing by default when role check fails', () => {
    act(() => {
      useAuthStore.getState().login('token', mockViewer);
    });

    const { container } = render(
      <RoleGuard require="admin">
        <div>Secret Admin Panel</div>
      </RoleGuard>
    );

    // Should render empty (no fallback specified)
    expect(container.textContent).toBe('');
  });
});
