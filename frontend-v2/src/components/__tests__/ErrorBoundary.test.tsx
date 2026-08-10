/**
 * Tests for ErrorBoundary and NotFound components
 *
 * Covers: renders children when no error, shows error message when child throws,
 * shows "new version available" for chunk load errors, reload button calls
 * window.location.reload, NotFound component renders with link to dashboard.
 *
 * Note: ErrorBoundary uses useRouteError from react-router, so we test it by
 * mocking that hook rather than rendering it as a traditional error boundary.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { NotFound } from '@/components/ErrorBoundary';

// We need to mock react-router-dom's useRouteError and isRouteErrorResponse
// to test ErrorBoundary in isolation
const mockUseRouteError = vi.fn();
const mockIsRouteErrorResponse = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useRouteError: () => mockUseRouteError(),
    isRouteErrorResponse: (error: unknown) => mockIsRouteErrorResponse(error),
  };
});

// Import ErrorBoundary AFTER the mock is set up
import { ErrorBoundary } from '@/components/ErrorBoundary';

describe('ErrorBoundary', () => {
  const originalReload = window.location.reload;

  beforeEach(() => {
    mockUseRouteError.mockReset();
    mockIsRouteErrorResponse.mockReset();
    mockIsRouteErrorResponse.mockReturnValue(false);

    // Mock window.location.reload
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { ...window.location, reload: vi.fn() },
    });
  });

  afterEach(() => {
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { ...window.location, reload: originalReload },
    });
  });

  it('shows generic error message for standard errors', () => {
    mockUseRouteError.mockReturnValue(new Error('Something broke'));

    render(<ErrorBoundary />);

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('Something broke')).toBeInTheDocument();
  });

  it('shows "new version available" for chunk load errors', () => {
    mockUseRouteError.mockReturnValue(
      new Error('Failed to fetch dynamically imported module')
    );

    render(<ErrorBoundary />);

    expect(screen.getByText('A new version is available')).toBeInTheDocument();
    expect(
      screen.getByText(/The application has been updated/)
    ).toBeInTheDocument();
  });

  it('shows "new version available" for Loading chunk errors', () => {
    mockUseRouteError.mockReturnValue(
      new Error('Loading chunk abc123 failed')
    );

    render(<ErrorBoundary />);

    expect(screen.getByText('A new version is available')).toBeInTheDocument();
  });

  it('shows "new version available" for Loading CSS chunk errors', () => {
    mockUseRouteError.mockReturnValue(
      new Error('Loading CSS chunk styles-abc failed')
    );

    render(<ErrorBoundary />);

    expect(screen.getByText('A new version is available')).toBeInTheDocument();
  });

  it('shows "new version available" for ChunkLoadError name', () => {
    const error = new Error('chunk error');
    error.name = 'ChunkLoadError';
    mockUseRouteError.mockReturnValue(error);

    render(<ErrorBoundary />);

    expect(screen.getByText('A new version is available')).toBeInTheDocument();
  });

  it('renders Reload button', () => {
    mockUseRouteError.mockReturnValue(new Error('test'));

    render(<ErrorBoundary />);

    expect(screen.getByRole('button', { name: /reload/i })).toBeInTheDocument();
  });

  it('reload button calls window.location.reload', async () => {
    mockUseRouteError.mockReturnValue(new Error('test'));

    render(<ErrorBoundary />);

    const reloadButton = screen.getByRole('button', { name: /reload/i });
    await userEvent.click(reloadButton);

    expect(window.location.reload).toHaveBeenCalled();
  });

  it('renders Dashboard link', () => {
    mockUseRouteError.mockReturnValue(new Error('test'));

    render(<ErrorBoundary />);

    const dashboardLink = screen.getByRole('link', { name: /dashboard/i });
    expect(dashboardLink).toBeInTheDocument();
    expect(dashboardLink).toHaveAttribute('href', '/');
  });

  it('shows route error response status and text', () => {
    const routeError = { status: 500, statusText: 'Internal Server Error' };
    mockUseRouteError.mockReturnValue(routeError);
    mockIsRouteErrorResponse.mockReturnValue(true);

    render(<ErrorBoundary />);

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('500 — Internal Server Error')).toBeInTheDocument();
  });

  it('shows fallback text for non-Error, non-route-error objects', () => {
    mockUseRouteError.mockReturnValue({ weird: 'object' });
    mockIsRouteErrorResponse.mockReturnValue(false);

    render(<ErrorBoundary />);

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('An unexpected error occurred.')).toBeInTheDocument();
  });
});

describe('NotFound', () => {
  it('renders 404 text', () => {
    render(<NotFound />);

    expect(screen.getByText('404')).toBeInTheDocument();
    expect(screen.getByText('Page not found')).toBeInTheDocument();
  });

  it('renders explanation text', () => {
    render(<NotFound />);

    expect(
      screen.getByText(/The page you're looking for doesn't exist/)
    ).toBeInTheDocument();
  });

  it('renders link back to dashboard', () => {
    render(<NotFound />);

    const link = screen.getByRole('link', { name: /back to dashboard/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', '/');
  });
});
