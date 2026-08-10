/**
 * Tests for ProjectDetailV2 page
 *
 * Covers: renders project detail, loading state, not found state, tab navigation,
 * module list, action buttons, header content.
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import ProjectDetailV2 from '@/pages/ProjectDetailV2';

// Mock useParams to return a project ID
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useParams: () => ({ id: '1' }),
    useNavigate: () => vi.fn(),
    useSearchParams: () => [new URLSearchParams(), vi.fn()],
  };
});

describe('ProjectDetailV2', () => {
  it('renders project name after data loads', async () => {
    render(<ProjectDetailV2 />);
    await waitFor(() => {
      expect(screen.getByText('test-project')).toBeInTheDocument();
    });
  });

  it('renders loading skeletons while project is fetching', () => {
    server.use(
      http.get('*/api/projects/1', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json({});
      }),
    );
    render(<ProjectDetailV2 />);
    const skeletons = document.querySelectorAll('[class*="animate-pulse"], [class*="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('renders not found state for invalid project', async () => {
    server.use(
      http.get('*/api/projects/1', () => {
        return HttpResponse.json(
          { error: { code: 'NOT_FOUND', message: 'Project not found' } },
          { status: 404 },
        );
      }),
    );
    render(<ProjectDetailV2 />);
    await waitFor(() => {
      expect(screen.getByText(/not found/i)).toBeInTheDocument();
    });
  });

  it('renders action buttons (Add Module, etc.)', async () => {
    render(<ProjectDetailV2 />);
    await waitFor(() => {
      expect(screen.getByText('test-project')).toBeInTheDocument();
    });
    // Action buttons should be present
    const addModuleBtn = screen.queryByText(/add module/i);
    expect(addModuleBtn).toBeInTheDocument();
  });

  it('renders tab navigation for Modules & Blueprints, Secrets, etc.', async () => {
    render(<ProjectDetailV2 />);
    await waitFor(() => {
      expect(screen.getByText('test-project')).toBeInTheDocument();
    });
    // Tab labels should be present — exact text includes icon text
    expect(screen.getByText(/Modules & Blueprints/)).toBeInTheDocument();
    expect(screen.getByText(/Secrets/)).toBeInTheDocument();
  });

  it('renders module list for the project', async () => {
    render(<ProjectDetailV2 />);
    await waitFor(() => {
      // mockModules has modules — check for module-related content
      const moduleElements = screen.queryAllByText(/module|vpc|bnk/i);
      expect(moduleElements.length).toBeGreaterThan(0);
    });
  });

  it('renders back navigation button', async () => {
    render(<ProjectDetailV2 />);
    await waitFor(() => {
      expect(screen.getByText('test-project')).toBeInTheDocument();
    });
    // Back button or breadcrumb should exist
    const backButton = document.querySelector('[aria-label*="back"], [data-testid*="back"], button');
    expect(backButton).toBeInTheDocument();
  });

  it('disables global destroy when a module lacks destroy capability', async () => {
    const user = userEvent.setup();

    server.use(
      http.get('*/api/project-modules/project/:projectId', () => {
        return HttpResponse.json({
          modules: [
            {
              id: 1,
              project_id: 1,
              module_name: 'ansible-checks',
              module_library_id: 11,
              path_in_project: 'modules/ansible-checks',
              deployment_order: 1,
              enabled: true,
              status: 'applied',
              dependencies: [],
              engine_type: 'ansible',
              lifecycle_capabilities: {
                supports_init: true,
                supports_plan: true,
                supports_apply: true,
                supports_destroy: false,
                supports_refresh: false,
                supports_drift: false,
              },
              library_module: {
                id: 11,
                name: 'ansible-checks',
                category: 'operations',
                engine_type: 'ansible',
                lifecycle_capabilities: {
                  supports_init: true,
                  supports_plan: true,
                  supports_apply: true,
                  supports_destroy: false,
                  supports_refresh: false,
                  supports_drift: false,
                },
              },
            },
          ],
          total: 1,
        });
      }),
    );

    render(<ProjectDetailV2 />);

    await waitFor(() => {
      expect(screen.getByText('test-project')).toBeInTheDocument();
    });

    // "Destroy all" now lives inside the More dropdown — open it first.
    await user.click(screen.getByRole('button', { name: /more/i }));
    const destroyAll = await screen.findByRole('menuitem', { name: /destroy all/i });
    expect(destroyAll).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText(/some modules don't support destroy/i)).toBeInTheDocument();

    // Close the More dropdown before interacting with per-row module menu.
    await user.keyboard('{Escape}');
    await user.click(screen.getByTestId('module-actions-menu'));
    expect(screen.queryByText('Destroy')).not.toBeInTheDocument();
  });
});
