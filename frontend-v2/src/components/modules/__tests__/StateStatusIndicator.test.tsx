/**
 * Tests for StateStatusIndicator component
 *
 * Covers: renders empty for not_initialized module, shows "Checking state..."
 * while loading, renders status badge with state info after API call.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { StateStatusIndicator } from '@/components/modules/StateStatusIndicator';
import type { ProjectModule } from '@/types';

const mockModuleApplied: ProjectModule = {
  id: 1,
  project_id: 1,
  module_library_id: 1,
  module_name: 'test-vpc',
  path_in_project: 'modules/vpc',
  deployment_order: 0,
  enabled: true,
  status: 'applied',
  outputs: { vpc_id: 'vpc-12345' },
};

const mockModuleNotInitialized: ProjectModule = {
  id: 2,
  project_id: 1,
  module_library_id: 1,
  module_name: 'test-eks',
  path_in_project: 'modules/eks',
  deployment_order: 1,
  enabled: true,
  status: 'not_initialized',
};

describe('StateStatusIndicator', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders empty div for not_initialized module', () => {
    const { container } = render(
      <StateStatusIndicator module={mockModuleNotInitialized} />
    );

    // Should render an empty div (no text content)
    // The component returns <div ref={containerRef} /> for not_initialized
    expect(container.querySelector('div')).toBeInTheDocument();
    expect(screen.queryByText(/State/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Checking state/)).not.toBeInTheDocument();
  });

  it('shows "Checking state..." indicator for initialized module before data loads', () => {
    // The IntersectionObserver mock doesn't trigger isVisible=true,
    // so the component stays in the loading placeholder state
    server.use(
      http.get('*/api/project-modules/:moduleId/state-info', () => {
        return HttpResponse.json({
          state_exists: true,
          serial: 5,
          resources_count: 10,
          status: 'applied',
        });
      })
    );

    render(<StateStatusIndicator module={mockModuleApplied} />);

    // The mock IntersectionObserver doesn't trigger, so it shows loading state
    expect(screen.getByText('Checking state...')).toBeInTheDocument();
  });

  it('renders state badge when API returns state exists (with visibility triggered)', async () => {
    // Override IntersectionObserver to immediately call the callback as visible
    const originalIO = window.IntersectionObserver;
    window.IntersectionObserver = class MockIO {
      constructor(callback: IntersectionObserverCallback) {
        // Immediately trigger with isIntersecting: true
        setTimeout(() => {
          callback(
            [{ isIntersecting: true } as IntersectionObserverEntry],
            this as unknown as IntersectionObserver
          );
        }, 0);
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof IntersectionObserver;

    server.use(
      http.get('*/api/project-modules/:moduleId/state-info', () => {
        return HttpResponse.json({
          module_id: 1,
          module_name: 'test-vpc',
          state_exists: true,
          serial: 5,
          resources_count: 10,
          status: 'applied',
          lineage: 'abc-def-123-456-789',
          state_modified_at: '2026-02-18T10:00:00Z',
        });
      })
    );

    render(<StateStatusIndicator module={mockModuleApplied} />);

    await waitFor(() => {
      expect(screen.getByText(/State #5/)).toBeInTheDocument();
    });

    expect(screen.getByText(/10 resources/)).toBeInTheDocument();

    // Restore original IntersectionObserver
    window.IntersectionObserver = originalIO;
  });
});
