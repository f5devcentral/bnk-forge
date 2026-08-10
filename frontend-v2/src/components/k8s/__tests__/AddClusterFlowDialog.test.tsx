/**
 * Tests for AddClusterFlowDialog — multi-step cluster creation flow.
 *
 * Step 1: Project picker (from useProjects)
 * Step 2a: ClusterConfigDialog (mocked — tested separately)
 * Step 2b: CreateProjectDialog if no projects exist (mocked — tested separately)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { AddClusterFlowDialog } from '../AddClusterFlowDialog';
import { useProjects } from '@/hooks/useProjects';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('@/hooks/useProjects', () => ({
  useProjects: vi.fn(),
}));

// Mock ClusterConfigDialog since it's tested separately
vi.mock('../ClusterConfigDialog', () => ({
  ClusterConfigDialog: ({ open, projectId }: { open: boolean; projectId: number }) =>
    open ? <div data-testid="cluster-config-dialog">ClusterConfigDialog for project {projectId}</div> : null,
}));

// Mock CreateProjectDialog since it's tested separately
vi.mock('@/components/projects/CreateProjectDialog', () => ({
  CreateProjectDialog: ({ open }: { open: boolean }) =>
    open ? <div data-testid="create-project-dialog">CreateProjectDialog</div> : null,
}));

const mockProjects = [
  {
    id: 1,
    name: 'Production BNK',
    description: 'Main production environment',
    color: '#3b82f6',
    icon: 'P',
    is_active: true,
    has_deployments: true,
    module_count: 5,
    deployed_count: 3,
    failed_count: 0,
    cluster_count: 2,
    environment: 'production',
    created_at: '2026-01-01',
    updated_at: '2026-01-01',
    ssh_credential_id: 10,
  },
  {
    id: 2,
    name: 'Staging Environment',
    description: 'Testing environment',
    color: '#f59e0b',
    icon: 'S',
    is_active: true,
    has_deployments: false,
    module_count: 2,
    deployed_count: 0,
    failed_count: 0,
    cluster_count: 0,
    environment: 'staging',
    created_at: '2026-01-01',
    updated_at: '2026-01-01',
  },
  {
    id: 3,
    name: 'Archived Project',
    description: 'No longer active',
    color: '#6b7280',
    icon: 'A',
    is_active: false,
    has_deployments: false,
    module_count: 0,
    deployed_count: 0,
    failed_count: 0,
    cluster_count: 1,
    created_at: '2026-01-01',
    updated_at: '2026-01-01',
  },
];

function setProjectsMock(overrides: Record<string, unknown> = {}) {
  vi.mocked(useProjects).mockReturnValue({
    data: mockProjects,
    isLoading: false,
    isError: false,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useProjects>);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('AddClusterFlowDialog', () => {
  const onOpenChange = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    setProjectsMock();
  });

  it('renders dialog with title and description when open', () => {
    render(<AddClusterFlowDialog open={true} onOpenChange={onOpenChange} />);
    expect(screen.getByText('Add Cluster')).toBeInTheDocument();
    expect(screen.getByText(/select a project to add the cluster to/i)).toBeInTheDocument();
  });

  it('does not render when open is false', () => {
    render(<AddClusterFlowDialog open={false} onOpenChange={onOpenChange} />);
    expect(screen.queryByText('Add Cluster')).not.toBeInTheDocument();
  });

  it('renders active projects only (filters out inactive)', () => {
    render(<AddClusterFlowDialog open={true} onOpenChange={onOpenChange} />);
    expect(screen.getByText('Production BNK')).toBeInTheDocument();
    expect(screen.getByText('Staging Environment')).toBeInTheDocument();
    // Archived project (is_active=false) should NOT appear
    expect(screen.queryByText('Archived Project')).not.toBeInTheDocument();
  });

  it('shows cluster count and environment for each project', () => {
    render(<AddClusterFlowDialog open={true} onOpenChange={onOpenChange} />);
    expect(screen.getByText(/2 clusters/)).toBeInTheDocument();
    expect(screen.getByText(/0 clusters/)).toBeInTheDocument();
    // Environment labels appear inside the cluster count line as "· production"
    expect(screen.getByText(/· production/i)).toBeInTheDocument();
    expect(screen.getByText(/· staging/i)).toBeInTheDocument();
  });

  it('shows loading skeletons while projects are loading', () => {
    setProjectsMock({ data: undefined, isLoading: true });
    render(<AddClusterFlowDialog open={true} onOpenChange={onOpenChange} />);
    const skeletons = document.querySelectorAll('[class*="animate-pulse"], [class*="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('shows empty state with Create Project button when no projects exist', () => {
    setProjectsMock({ data: [] });
    render(<AddClusterFlowDialog open={true} onOpenChange={onOpenChange} />);
    expect(screen.getByText('No projects yet')).toBeInTheDocument();
    expect(screen.getByText('Create Project')).toBeInTheDocument();
  });

  it('opens CreateProjectDialog when Create Project is clicked in empty state', async () => {
    const user = userEvent.setup();
    setProjectsMock({ data: [] });
    render(<AddClusterFlowDialog open={true} onOpenChange={onOpenChange} />);

    await user.click(screen.getByText('Create Project'));

    await waitFor(() => {
      expect(screen.getByTestId('create-project-dialog')).toBeInTheDocument();
    });
  });

  it('opens ClusterConfigDialog when a project is clicked', async () => {
    const user = userEvent.setup();
    render(<AddClusterFlowDialog open={true} onOpenChange={onOpenChange} />);

    await user.click(screen.getByText('Production BNK'));

    await waitFor(() => {
      expect(screen.getByTestId('cluster-config-dialog')).toBeInTheDocument();
      expect(screen.getByText('ClusterConfigDialog for project 1')).toBeInTheDocument();
    });
  });

  it('shows empty state when all projects are inactive', () => {
    setProjectsMock({
      data: [mockProjects[2]], // only the inactive project
    });
    render(<AddClusterFlowDialog open={true} onOpenChange={onOpenChange} />);
    expect(screen.getByText('No projects yet')).toBeInTheDocument();
  });
});
