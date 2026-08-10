/**
 * Tests for ModuleTableRow and ModuleTableHeader components
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { ModuleTableHeader, ModuleTableRow } from '../ModuleTableRow';
import type { ProjectModule } from '@/types';

beforeEach(() => {
  vi.clearAllMocks();
});

const mockModule: ProjectModule = {
  id: 1,
  project_id: 1,
  module_name: 'bnk-vpc',
  status: 'applied',
  state_serial: 5,
  state_size: 2048,
  resource_count: 12,
  dependencies: [],
  variables: {},
  library_module: {
    category: 'Network',
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
  created_at: '2026-01-10T00:00:00Z',
  updated_at: '2026-02-01T00:00:00Z',
} as ProjectModule;

describe('ModuleTableHeader', () => {
  it('renders table column headers', () => {
    const { container: _container } = render(
      <table>
        <ModuleTableHeader />
      </table>
    );
    expect(screen.getByText('#')).toBeInTheDocument();
    expect(screen.getByText('Module')).toBeInTheDocument();
    expect(screen.getByText('Engine')).toBeInTheDocument();
    expect(screen.getByText('Status')).toBeInTheDocument();
    expect(screen.getByText('Dependencies')).toBeInTheDocument();
    expect(screen.getByText('Actions')).toBeInTheDocument();
  });
});

describe('ModuleTableRow', () => {
  const defaultProps = {
    module: mockModule,
    index: 0,
    allModules: [mockModule],
    isSelected: false,
    onSelect: vi.fn(),
    onAction: vi.fn(),
    mutations: {
      initMutation: { isPending: false },
      planMutation: { isPending: false },
      applyMutation: { isPending: false },
    },
    moduleToAction: null,
  };

  it('renders module name and category', () => {
    render(
      <table>
        <tbody>
          <ModuleTableRow {...defaultProps} />
        </tbody>
      </table>
    );
    expect(screen.getByText('bnk-vpc')).toBeInTheDocument();
    expect(screen.getByText('Network')).toBeInTheDocument();
  });

  it('renders row index number', () => {
    render(
      <table>
        <tbody>
          <ModuleTableRow {...defaultProps} />
        </tbody>
      </table>
    );
    expect(screen.getByText('1')).toBeInTheDocument(); // index 0 -> displayed as 1
  });

  it('shows dash when no dependencies', () => {
    render(
      <table>
        <tbody>
          <ModuleTableRow {...defaultProps} />
        </tbody>
      </table>
    );
    // The em-dash character for no dependencies
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('renders engine badge and no-destroy hint from capability metadata', () => {
    render(
      <table>
        <tbody>
          <ModuleTableRow {...defaultProps} />
        </tbody>
      </table>
    );

    expect(screen.getByText('Ansible')).toBeInTheDocument();
    expect(screen.getByText('No destroy')).toBeInTheDocument();
  });

  it('hides unsupported destroy action in module menu', async () => {
    const user = userEvent.setup();
    render(
      <table>
        <tbody>
          <ModuleTableRow {...defaultProps} />
        </tbody>
      </table>
    );

    await user.click(screen.getByTestId('module-actions-menu'));

    expect(screen.queryByText('Destroy')).not.toBeInTheDocument();
    expect(screen.getByText('Plan')).toBeInTheDocument();
    expect(screen.getByText('Apply')).toBeInTheDocument();
  });

  it('shows Retry Init for init_failed modules', async () => {
    const user = userEvent.setup();
    const failedInitModule = {
      ...mockModule,
      status: 'init_failed',
      library_module: {
        ...mockModule.library_module,
        lifecycle_capabilities: {
          supports_init: true,
          supports_plan: true,
          supports_apply: true,
          supports_destroy: false,
          supports_refresh: false,
          supports_drift: false,
        },
      },
    } as ProjectModule;

    render(
      <table>
        <tbody>
          <ModuleTableRow {...defaultProps} module={failedInitModule} />
        </tbody>
      </table>
    );

    await user.click(screen.getByTestId('module-actions-menu'));

    expect(screen.getByText('Retry Init')).toBeInTheDocument();
  });

  it('uses top-level project-module engine_type over library fallback for badge rendering', () => {
    const moduleWithTopLevelEngine = {
      ...mockModule,
      engine_type: 'ansible',
      library_module: {
        ...mockModule.library_module,
        engine_type: undefined,
      },
      lifecycle_capabilities: {
        supports_init: true,
        supports_plan: true,
        supports_apply: true,
        supports_destroy: false,
        supports_refresh: false,
        supports_drift: false,
      },
    } as ProjectModule;

    render(
      <table>
        <tbody>
          <ModuleTableRow {...defaultProps} module={moduleWithTopLevelEngine} />
        </tbody>
      </table>
    );

    expect(screen.getByText('Ansible')).toBeInTheDocument();
  });

  // D-034: manifest-declared module actions
  describe('Run Action menu item', () => {
    it('shows Run Action for an applied module that declares actions and dispatches run-action', async () => {
      server.use(
        http.get('*/api/project-modules/:moduleId/actions', ({ params }) => {
          return HttpResponse.json({
            module_id: Number(params.moduleId),
            actions: [
              { name: 'scenario-run', title: 'Run scenario', description: null, rating: 'green', inputs: [] },
            ],
            total: 1,
          });
        })
      );

      const user = userEvent.setup();
      const onAction = vi.fn();
      render(
        <table>
          <tbody>
            <ModuleTableRow {...defaultProps} onAction={onAction} />
          </tbody>
        </table>
      );

      await user.click(screen.getByTestId('module-actions-menu'));

      const item = await screen.findByText('Run Action…');
      await user.click(item);

      expect(onAction).toHaveBeenCalledWith(mockModule, 'run-action');
    });

    it('shows a disabled hint when the module declares no actions', async () => {
      // Default handler returns an empty actions list
      const user = userEvent.setup();
      render(
        <table>
          <tbody>
            <ModuleTableRow {...defaultProps} />
          </tbody>
        </table>
      );

      await user.click(screen.getByTestId('module-actions-menu'));

      expect(await screen.findByText('No actions declared')).toBeInTheDocument();
      expect(screen.queryByText('Run Action…')).not.toBeInTheDocument();
    });

    it('hides the item entirely for non-applied modules', async () => {
      const user = userEvent.setup();
      const notApplied = { ...mockModule, status: 'planned' } as ProjectModule;
      render(
        <table>
          <tbody>
            <ModuleTableRow {...defaultProps} module={notApplied} />
          </tbody>
        </table>
      );

      await user.click(screen.getByTestId('module-actions-menu'));

      expect(screen.queryByText('Run Action…')).not.toBeInTheDocument();
      expect(screen.queryByText('No actions declared')).not.toBeInTheDocument();
    });
  });
});
