/**
 * Integration tests for System — System Administration page workflow
 *
 * Covers: page rendering with tabs, System Monitor content, tab switching,
 * Appearance tab theme toggle, and loading states during data fetch.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { useAuthStore } from '@/stores/authStore';
import { mockUser } from '@/test/test-fixtures';
import System from '@/pages/System';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

describe('System Administration — Integration', () => {
  beforeEach(() => {
    useAuthStore.getState().logout();
    localStorage.clear();
    mockNavigate.mockReset();
    useAuthStore.getState().login('mock-jwt-token', mockUser);
  });

  // ────────────────────────────────────────────────────────────────────────
  // 1. System page renders with tabs
  // ────────────────────────────────────────────────────────────────────────
  it('renders page title, subtitle, and all tab labels', async () => {
    render(<System />, { initialRoute: '/system' });

    await waitFor(() => {
      expect(screen.getByText('System Administration')).toBeInTheDocument();
    });

    expect(screen.getByText(/Monitor system health/i)).toBeInTheDocument();

    // Tabs visible now that Module Library + Helm Repos moved to /catalog.
    const tabs = screen.getAllByRole('tab');
    const tabLabels = tabs.map((tab) => tab.textContent);
    expect(tabLabels).toContain('System Monitor');
    expect(tabLabels).toContain('Audit Log');
    expect(tabLabels).toContain('Alerts');
    expect(tabLabels).toContain('Defaults');
    expect(tabLabels).toContain('Appearance');
    expect(tabLabels).toContain('Version & Upgrade');
    expect(tabLabels).not.toContain('Module Library');
    expect(tabLabels).not.toContain('Helm Repos');
  });

  // ────────────────────────────────────────────────────────────────────────
  // 2. System Monitor tab shows health components
  // ────────────────────────────────────────────────────────────────────────
  it('shows performance and health content on System Monitor tab', async () => {
    const user = userEvent.setup();
    render(<System />, { initialRoute: '/system' });

    // The System Monitor tab is the default — wait for sub-components to load.
    await waitFor(
      () => {
        // SectionCard eyebrow title — sentence case now ("Container management").
        expect(screen.getByText(/container management/i)).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    // Switch to Version & Upgrade tab to verify version information
    await user.click(screen.getByRole('tab', { name: /version & upgrade/i }));

    // SystemUpgrade should render version information from the mock
    await waitFor(
      () => {
        expect(screen.getByText(/2\.10\.49/)).toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  });

  // ────────────────────────────────────────────────────────────────────────
  // 3. Tab switching works
  // ────────────────────────────────────────────────────────────────────────
  it('switches between tabs and renders corresponding content', async () => {
    const user = userEvent.setup();
    render(<System />, { initialRoute: '/system' });

    // Wait for page to render
    await waitFor(() => {
      expect(screen.getByText('System Administration')).toBeInTheDocument();
    });

    // Click "Audit Log" tab
    await user.click(screen.getByRole('tab', { name: /audit log/i }));

    // Audit tab should become active
    await waitFor(() => {
      const auditTab = screen.getByRole('tab', { name: /audit log/i });
      expect(auditTab).toHaveAttribute('aria-selected', 'true');
    });

    // Click "Appearance" tab
    await user.click(screen.getByRole('tab', { name: /appearance/i }));

    await waitFor(() => {
      // D-020: label sentence-cased to "Dark mode".
      expect(screen.getByText('Dark mode')).toBeInTheDocument();
    });
  });

  // ────────────────────────────────────────────────────────────────────────
  // 4. Appearance tab shows theme toggle
  // ────────────────────────────────────────────────────────────────────────
  it('renders Appearance tab with Dark mode toggle and theme badges via URL param', async () => {
    // D-020: "Dark Mode" → "Dark mode"; description now ends with a period.
    render(<System />, { initialRoute: '/system?tab=appearance' });

    await waitFor(() => {
      expect(screen.getByText('Dark mode')).toBeInTheDocument();
    });

    // Description text — lenient match avoids trailing-period brittleness.
    expect(
      screen.getByText(/switch between light and dark themes/i),
    ).toBeInTheDocument();

    // Theme badges
    expect(screen.getByText('Light')).toBeInTheDocument();
    expect(screen.getByText('Dark')).toBeInTheDocument();

    // The Appearance tab in the tablist should be selected
    const appearanceTab = screen.getByRole('tab', { name: /appearance/i });
    expect(appearanceTab).toHaveAttribute('aria-selected', 'true');
  });

  // ────────────────────────────────────────────────────────────────────────
  // 5. Loading state on data fetch
  // ────────────────────────────────────────────────────────────────────────
  it('displays loading indicators while system data is fetching', async () => {
    // Delay all system API responses so we can observe the loading state
    server.use(
      http.get('*/api/system/health', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json({});
      }),
      http.get('*/api/system/performance', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json({});
      }),
      http.get('*/api/system/version', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json({});
      }),
      http.get('*/api/system/database/stats', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json({});
      }),
      http.get('*/api/system/containers/status', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json({});
      }),
      http.get('*/api/system/errors', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json({});
      }),
      http.get('*/api/system/queue-metrics', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json({});
      }),
    );

    render(<System />, { initialRoute: '/system' });

    // The page title should render immediately
    expect(screen.getByText('System Administration')).toBeInTheDocument();

    // Sub-components should show loading indicators (spinners, skeletons, or animate-pulse)
    await waitFor(
      () => {
        const loadingElements = document.querySelectorAll(
          '[class*="animate-spin"], [class*="animate-pulse"], [class*="skeleton"]',
        );
        expect(loadingElements.length).toBeGreaterThan(0);
      },
      { timeout: 3000 },
    );
  });
});
