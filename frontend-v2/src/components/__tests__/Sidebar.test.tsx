/**
 * Tests for Sidebar component
 *
 * Covers: renders navigation sections, nav items, role-based filtering,
 * collapse/expand, project count badges, logo, user footer, version display.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { act } from '@testing-library/react';
import { render } from '@/test/test-utils';
import { Sidebar } from '@/components/layout/Sidebar';
import { useAuthStore } from '@/stores/authStore';
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

describe('Sidebar', () => {
  beforeEach(() => {
    useAuthStore.getState().logout();
    localStorage.clear();
  });

  it('renders the BNK Forge logo text', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    render(<Sidebar />);

    expect(screen.getByText('BNK Forge')).toBeInTheDocument();
  });

  it('renders the Infrastructure subtitle', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    render(<Sidebar />);

    expect(screen.getAllByText('Infrastructure').length).toBeGreaterThanOrEqual(1);
  });

  it('renders OBSERVE section with Command Center', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    render(<Sidebar />);

    expect(screen.getByText('OBSERVE')).toBeInTheDocument();
    expect(screen.getByText('Command Center')).toBeInTheDocument();
  });

  it('renders BUILD section with navigation items', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    render(<Sidebar />);

    expect(screen.getByText('BUILD')).toBeInTheDocument();
    expect(screen.getByText('Access Methods')).toBeInTheDocument();
    expect(screen.getByText('Blueprints')).toBeInTheDocument();
    expect(screen.getByText('Projects')).toBeInTheDocument();
    expect(screen.getByText('Operations Log')).toBeInTheDocument();
    // Catalog lives at the bottom of BUILD, after Operations Log. Helm
    // Packages is gone (now part of the Kubernetes page).
    expect(screen.getByText('Catalog')).toBeInTheDocument();
  });

  it('renders OPERATE section with navigation items', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    render(<Sidebar />);

    expect(screen.getByText('OPERATE')).toBeInTheDocument();
    expect(screen.getByText('Fleet')).toBeInTheDocument();
    expect(screen.getByText('Kubernetes')).toBeInTheDocument();
    expect(screen.getByText('F5 BNK')).toBeInTheDocument();
    // K8S-UX-005: Operators merged into Fleet page — no longer a separate sidebar entry
  });

  it('renders SETTINGS section for admin', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    render(<Sidebar />);

    expect(screen.getByText('SETTINGS')).toBeInTheDocument();
    expect(screen.getByText('System')).toBeInTheDocument();
  });

  it('hides SETTINGS section for viewer', () => {
    act(() => {
      useAuthStore.getState().login('token', mockViewer);
    });
    render(<Sidebar />);

    expect(screen.queryByText('SETTINGS')).not.toBeInTheDocument();
  });

  it('hides System link for non-admin users', () => {
    act(() => {
      useAuthStore.getState().login('token', mockViewer);
    });
    render(<Sidebar />);

    expect(screen.queryByText('System')).not.toBeInTheDocument();
  });

  it('renders system operational status', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    render(<Sidebar />);

    expect(screen.getByText('All systems operational')).toBeInTheDocument();
  });

  it('renders version number', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    render(<Sidebar />);

    // __APP_VERSION__ is defined as '2.10.10-test' in vitest.config.ts
    expect(screen.getByText(/version/i)).toBeInTheDocument();
  });

  it('renders collapse button', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    render(<Sidebar />);

    expect(screen.getByLabelText('Collapse sidebar')).toBeInTheDocument();
  });

  it('collapses sidebar when collapse button is clicked', async () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    const user = userEvent.setup();
    render(<Sidebar />);

    await user.click(screen.getByLabelText('Collapse sidebar'));

    // After collapse, the expand button should be visible
    expect(screen.getByLabelText('Expand sidebar')).toBeInTheDocument();
    // Logo text should be hidden
    expect(screen.queryByText('BNK Forge')).not.toBeInTheDocument();
  });

  it('renders user info in footer when logged in', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    render(<Sidebar />);

    // Username appears in sidebar footer — may have multiple matches (mock data)
    const elements = screen.getAllByText('admin');
    expect(elements.length).toBeGreaterThan(0);
  });

  it('renders logout button', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    render(<Sidebar />);

    expect(screen.getByLabelText('Logout')).toBeInTheDocument();
  });

  it('renders sidebar as aside with aria-label', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    render(<Sidebar />);

    expect(screen.getByLabelText('Application sidebar')).toBeInTheDocument();
  });

  it('renders main navigation with aria-label', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    render(<Sidebar />);

    expect(screen.getByLabelText('Main navigation')).toBeInTheDocument();
  });

  it('renders updated workflow ordering for sidebar links', () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    render(<Sidebar />);

    const catalog = screen.getByText('Catalog');
    const blueprints = screen.getByText('Blueprints');
    const accessTemplates = screen.getByText('Access Methods');
    const projects = screen.getByText('Projects');
    const operationsLog = screen.getByText('Operations Log');
    const fleet = screen.getByText('Fleet');
    const kubernetes = screen.getByText('Kubernetes');

    // BUILD: Catalog → Blueprints → Access Methods → Projects → Operations Log
    expect(catalog.compareDocumentPosition(blueprints) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(blueprints.compareDocumentPosition(accessTemplates) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(accessTemplates.compareDocumentPosition(projects) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(projects.compareDocumentPosition(operationsLog) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // BUILD → OPERATE
    expect(operationsLog.compareDocumentPosition(fleet) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(fleet.compareDocumentPosition(kubernetes) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('shows project count badge after data loads', async () => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
    render(<Sidebar />);

    // Projects should show a count badge from mock data (2 projects)
    await waitFor(() => {
      const badges = screen.getAllByText('2');
      expect(badges.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });
});
