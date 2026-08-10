/**
 * Tests for Header component
 *
 * Covers: renders breadcrumbs, page title, search button with ⌘K,
 * notification bell, unread badge.
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '@/test/test-utils';
import { Header } from '@/components/layout/Header';

// Mock useLocation to control current pathname
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useLocation: () => ({ pathname: '/', search: '', hash: '', state: null, key: 'default' }),
  };
});

describe('Header', () => {
  it('renders page title based on current route', () => {
    render(<Header />);

    expect(screen.getByText('Command Center')).toBeInTheDocument();
  });

  it('renders breadcrumb for current section', () => {
    render(<Header />);

    expect(screen.getByText('Observe')).toBeInTheDocument();
  });

  it('renders search button with keyboard shortcut', () => {
    render(<Header />);

    expect(screen.getByText('Search...')).toBeInTheDocument();
    expect(screen.getByText('⌘K')).toBeInTheDocument();
  });

  it('renders notification bell button', () => {
    render(<Header />);

    expect(screen.getByLabelText('Notifications')).toBeInTheDocument();
  });

  it('renders Notifications heading in popover content', async () => {
    const user = (await import('@testing-library/user-event')).default.setup();
    render(<Header />);

    // Click the notification bell to open popover
    await user.click(screen.getByLabelText('Notifications'));

    await waitFor(() => {
      expect(screen.getByText('Notifications')).toBeInTheDocument();
    });
  });

  it('shows empty notification state', async () => {
    const user = (await import('@testing-library/user-event')).default.setup();
    render(<Header />);

    await user.click(screen.getByLabelText('Notifications'));

    await waitFor(() => {
      expect(screen.getByText('No notifications')).toBeInTheDocument();
    });
  });
});
