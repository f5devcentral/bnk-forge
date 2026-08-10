/**
 * Tests for AppShell layout component
 *
 * Covers: renders sidebar, header, main content area, skip navigation link,
 * suspense fallback.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '@/test/test-utils';
import { act } from '@testing-library/react';
import { useAuthStore } from '@/stores/authStore';
import type { User } from '@/types';

// Mock all the hooks that AppShell uses
vi.mock('@/hooks/useKeyboardShortcuts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/useKeyboardShortcuts')>();
  return {
    ...actual,
    useKeyboardShortcuts: vi.fn(),
    useNavigationShortcuts: vi.fn(),
  };
});

vi.mock('@/hooks/useAccessibility', () => ({
  useRouteAnnouncer: vi.fn(),
  useScrollToTop: vi.fn(),
}));

// Need to mock Outlet since we're not inside a Router with routes
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    Outlet: () => <div data-testid="outlet">Page Content</div>,
    useNavigate: () => vi.fn(),
    useLocation: () => ({ pathname: '/', search: '', hash: '', state: null, key: 'default' }),
  };
});

// Mock scrollIntoView (used by cmdk via CommandPalette)
Element.prototype.scrollIntoView = vi.fn();

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

// Cache the dynamic import at module load time. Importing inside each test
// adds 5-10s of compile cost under heavy parallel load on WSL, which can
// exhaust the per-test timeout before the first paint.
const appShellPromise = import('@/components/layout/AppShell');

describe('AppShell', () => {
  beforeEach(() => {
    act(() => {
      useAuthStore.getState().login('token', mockAdmin);
    });
  });

  it('renders skip navigation link', async () => {
    const { AppShell } = await appShellPromise;
    render(<AppShell />);

    expect(await screen.findByText('Skip to main content')).toBeInTheDocument();
  }, 30_000);

  it('renders main content area with id', async () => {
    const { AppShell } = await appShellPromise;
    render(<AppShell />);

    await screen.findByText('Skip to main content');
    expect(document.getElementById('main-content')).toBeInTheDocument();
  }, 30_000);

  it('renders sidebar component', async () => {
    const { AppShell } = await appShellPromise;
    render(<AppShell />);

    expect(await screen.findByLabelText('Application sidebar')).toBeInTheDocument();
  }, 30_000);

  it('renders outlet for page content', async () => {
    const { AppShell } = await appShellPromise;
    render(<AppShell />);

    expect(await screen.findByTestId('outlet')).toBeInTheDocument();
  }, 30_000);

  it('renders BNK Forge branding in sidebar', async () => {
    const { AppShell } = await appShellPromise;
    render(<AppShell />);

    expect(await screen.findByText('BNK Forge')).toBeInTheDocument();
  }, 30_000);
});
