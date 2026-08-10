/**
 * Error Handling Integration Tests
 *
 * Tests error handling scenarios across the application:
 *   1. NotFound renders 404 page
 *   2. API error doesn't crash page
 *   3. Multiple API failures handled gracefully
 *   4. Network timeout handled
 *
 * ErrorBoundary uses useRouteError() and requires a react-router errorElement
 * context, so it cannot be tested in isolation here. NotFound is a standalone
 * component and is tested directly.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { useAuthStore } from '@/stores/authStore';
import { mockUser } from '@/test/test-fixtures';
import { NotFound } from '@/components/ErrorBoundary';
import Projects from '@/pages/Projects';
import Dashboard from '@/pages/Dashboard';

// ---------------------------------------------------------------------------
// Navigation mock
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
  vi.clearAllMocks();
  useAuthStore.getState().logout();
  localStorage.clear();
  useAuthStore.getState().login('mock-jwt-token', mockUser);
});

// ===========================================================================
// Tests
// ===========================================================================

describe('Error Handling (integration)', () => {
  // -------------------------------------------------------------------------
  // 1. NotFound renders 404 page
  // -------------------------------------------------------------------------
  it('NotFound renders 404 page with correct content and link', () => {
    render(<NotFound />);

    // Large "404" text
    expect(screen.getByText('404')).toBeInTheDocument();

    // Heading
    expect(screen.getByText('Page not found')).toBeInTheDocument();

    // Description
    expect(
      screen.getByText(
        "The page you're looking for doesn't exist or has been moved.",
      ),
    ).toBeInTheDocument();

    // "Back to Dashboard" link pointing to "/"
    const link = screen.getByRole('link', { name: /back to dashboard/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', '/');
  });

  // -------------------------------------------------------------------------
  // 2. API error doesn't crash page
  // -------------------------------------------------------------------------
  it('API error on Projects page does not crash the page', async () => {
    server.use(
      http.get('*/api/projects', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname !== '/api/projects') return;
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Database unavailable' } },
          { status: 500 },
        );
      }),
    );

    render(<Projects />);

    // Page should still render without crashing — shows empty or error state
    await waitFor(
      () => {
        const hasEmptyState = screen.queryByText(/no projects/i);
        const hasErrorIndicator = screen.queryByText(/error/i);
        const hasPageTitle = screen.queryByText('Projects');
        // At least one of these should be present, proving the page didn't crash
        expect(hasEmptyState || hasErrorIndicator || hasPageTitle).toBeTruthy();
      },
      { timeout: 3000 },
    );
  });

  // -------------------------------------------------------------------------
  // 3. Multiple API failures handled gracefully
  // -------------------------------------------------------------------------
  it('multiple API failures on Dashboard do not crash the page', async () => {
    // Override multiple endpoints to return 500
    server.use(
      http.get('*/api/projects', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname !== '/api/projects') return;
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Server error' } },
          { status: 500 },
        );
      }),
      http.get('*/api/tasks', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname !== '/api/tasks') return;
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Server error' } },
          { status: 500 },
        );
      }),
      http.get('*/api/k8s/clusters', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname !== '/api/k8s/clusters') return;
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Server error' } },
          { status: 500 },
        );
      }),
      http.get('*/api/stacks/templates', ({ request }) => {
        const url = new URL(request.url);
        if (!url.pathname.endsWith('/templates')) return;
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Server error' } },
          { status: 500 },
        );
      }),
      http.get('*/api/drift/summary', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Server error' } },
          { status: 500 },
        );
      }),

    );

    render(<Dashboard />);

    // Dashboard should still render its greeting even when all APIs fail
    await waitFor(
      () => {
        const greeting = screen.queryByText(/good (morning|afternoon|evening)/i);
        expect(greeting).toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  });

  // -------------------------------------------------------------------------
  // 4. Network timeout handled
  // -------------------------------------------------------------------------
  it('network timeout shows loading state and does not crash', async () => {
    // Override with a very long delay to simulate a timeout
    server.use(
      http.get('*/api/projects', async () => {
        await new Promise((r) => setTimeout(r, 60_000));
        return HttpResponse.json({ projects: [], total: 0 });
      }),
    );

    render(<Projects />);

    // Loading skeletons should appear while the request is pending
    const skeletons = document.querySelectorAll(
      '[class*="animate-pulse"], [class*="skeleton"]',
    );
    expect(skeletons.length).toBeGreaterThan(0);

    // Page title should still be rendered (page is not blank/crashed)
    expect(screen.getByText('Projects')).toBeInTheDocument();
  });
});
