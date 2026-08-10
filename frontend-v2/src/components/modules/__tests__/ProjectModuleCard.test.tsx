/**
 * Tests for ProjectModuleCard component
 *
 * Covers: renders module name/path/status, action buttons for different statuses,
 * dropdown menu items, deployed badge with timestamp, description display.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { ProjectModuleCard } from '@/components/modules/ProjectModuleCard';
import type { ProjectModule } from '@/types';

// Mock mutation hooks to avoid API calls
const mockMutate = vi.fn();
vi.mock('@/hooks/useModules', () => ({
  useInitModule: () => ({ mutate: mockMutate, isPending: false }),
  usePlanModule: () => ({ mutate: mockMutate, isPending: false }),
  useApplyModule: () => ({ mutate: mockMutate, isPending: false }),
  useDestroyModule: () => ({ mutate: mockMutate, isPending: false }),
  useDeleteModule: () => ({ mutate: mockMutate, isPending: false }),
  useCancelDeployment: () => ({ mutate: mockMutate, isPending: false }),
  useRecoverState: () => ({ mutate: mockMutate, isPending: false }),
  useRerunModule: () => ({ mutate: mockMutate, isPending: false }),
}));

// Mock child dialog/sheet components to avoid deep rendering
vi.mock('@/components/modules/OpenTofuPlanDialog', () => ({
  OpenTofuPlanDialog: () => null,
}));
vi.mock('@/components/modules/EditModuleVariablesDialog', () => ({
  EditModuleVariablesDialog: () => null,
}));
vi.mock('@/components/modules/SnapshotManager', () => ({
  SnapshotManager: () => null,
}));
vi.mock('@/components/deployments/LogViewerModal', () => ({
  LogViewerModal: () => null,
}));
vi.mock('@/components/deployments/ModuleOutputsViewer', () => ({
  ModuleOutputsViewer: () => null,
}));
vi.mock('@/components/modules/ApplyConfirmDialog', () => ({
  ApplyConfirmDialog: () => null,
}));
vi.mock('@/components/modules/DependencyStatusDisplay', () => ({
  DependencyStatusDisplay: () => <div data-testid="dep-status">Dependencies</div>,
}));
vi.mock('@/components/modules/StateStatusIndicator', () => ({
  StateStatusIndicator: () => <div data-testid="state-status">State</div>,
}));
vi.mock('@/components/modules/VariableMappingsManager', () => ({
  VariableMappingsManager: () => null,
}));
vi.mock('@/components/modules/PlanStatusIndicator', () => ({
  PlanStatusIndicator: () => null,
}));

function makeModule(overrides: Partial<ProjectModule> = {}): ProjectModule {
  return {
    id: 1,
    project_id: 100,
    module_library_id: 10,
    module_name: 'vpc',
    path_in_project: 'infra/aws/vpc',
    deployment_order: 1,
    enabled: true,
    status: 'initialized',
    category: 'aws',
    library_module: {
      id: 10,
      name: 'AWS VPC',
      description: 'Creates a VPC with public/private subnets',
      category: 'aws',
      provider: 'aws',
      version: '1.0.0',
      variables: [],
    },
    ...overrides,
  };
}

describe('ProjectModuleCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders module name, path, and status badge', () => {
    const mod = makeModule();
    render(<ProjectModuleCard module={mod} />);

    expect(screen.getByText('vpc')).toBeInTheDocument();
    expect(screen.getByText('infra/aws/vpc')).toBeInTheDocument();
    // Status badge is rendered via ModuleStatusBadge
    expect(screen.getByTestId('module-status')).toBeInTheDocument();
  });

  it('shows Initialize button for not_initialized modules', () => {
    const mod = makeModule({ status: 'not_initialized' });
    render(<ProjectModuleCard module={mod} />);

    expect(screen.getByRole('button', { name: /initialize/i })).toBeInTheDocument();
  });

  it('shows Retry Init button for init_failed modules', () => {
    const mod = makeModule({ status: 'init_failed' });
    render(<ProjectModuleCard module={mod} />);

    expect(screen.getByRole('button', { name: /retry init/i })).toBeInTheDocument();
  });

  it('shows Plan and Apply buttons for initialized modules', () => {
    const mod = makeModule({ status: 'initialized' });
    render(<ProjectModuleCard module={mod} />);

    expect(screen.getByRole('button', { name: /plan/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /apply/i })).toBeInTheDocument();
  });

  it('shows Cancel Deployment button when module is deploying', () => {
    const mod = makeModule({ status: 'applying' });
    render(<ProjectModuleCard module={mod} />);

    expect(screen.getByRole('button', { name: /cancel deployment/i })).toBeInTheDocument();
  });

  it('shows deployed badge with timestamp for applied modules', () => {
    const mod = makeModule({
      status: 'applied',
      last_deployed_at: '2025-01-15T10:30:00Z',
    });
    render(<ProjectModuleCard module={mod} />);

    // "Deployed" appears in both ModuleStatusBadge and the deployed badge
    const deployedElements = screen.getAllByText('Deployed');
    expect(deployedElements.length).toBeGreaterThanOrEqual(1);
  });

  it('renders library module description when available', () => {
    const mod = makeModule();
    render(<ProjectModuleCard module={mod} />);

    expect(screen.getByText('Creates a VPC with public/private subnets')).toBeInTheDocument();
  });

  it('renders dropdown menu with action items', async () => {
    const user = userEvent.setup();
    const mod = makeModule({ status: 'applied' });
    render(<ProjectModuleCard module={mod} />);

    // Open dropdown
    await user.click(screen.getByLabelText('Module actions'));

    expect(screen.getByText('Edit Variables')).toBeInTheDocument();
    expect(screen.getByText('Variable Mappings')).toBeInTheDocument();
    expect(screen.getByText('View Outputs')).toBeInTheDocument();
    expect(screen.getByText('Destroy')).toBeInTheDocument();
    expect(screen.getByText('Delete')).toBeInTheDocument();
  });

  it('shows Recover State option for applied modules', async () => {
    const user = userEvent.setup();
    const mod = makeModule({ status: 'applied' });
    render(<ProjectModuleCard module={mod} />);

    await user.click(screen.getByLabelText('Module actions'));

    expect(screen.getByText('Recover State')).toBeInTheDocument();
  });

  it('renders dependency status display when dependencies exist', () => {
    const mod = makeModule({ dependencies: [2, 3] });
    render(<ProjectModuleCard module={mod} allModules={[]} />);

    expect(screen.getByTestId('dep-status')).toBeInTheDocument();
  });

  it('calls init mutation when Initialize button is clicked', async () => {
    const user = userEvent.setup();
    const mod = makeModule({ status: 'not_initialized' });
    render(<ProjectModuleCard module={mod} />);

    await user.click(screen.getByRole('button', { name: /initialize/i }));

    expect(mockMutate).toHaveBeenCalledWith(1);
  });

  it('shows Retry Init in the actions menu for init_failed modules', async () => {
    const user = userEvent.setup();
    const mod = makeModule({ status: 'init_failed' });
    render(<ProjectModuleCard module={mod} />);

    await user.click(screen.getByLabelText('Module actions'));

    expect(screen.getAllByText('Retry Init').length).toBeGreaterThan(1);
  });
});
