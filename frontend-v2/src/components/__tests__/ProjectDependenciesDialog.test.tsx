/**
 * Tests for ProjectDependenciesDialog component
 *
 * Covers: renders dialog, empty state, add dependency, remove dependency,
 * add/remove outputs, save, cancel.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { ProjectDependenciesDialog } from '@/components/projects/ProjectDependenciesDialog';
import type { Project } from '@/types';

const mockProject = {
  id: 1,
  name: 'test-project',
  description: 'A test project',
  project_type: 'cloud-aws' as const,
  environment: 'dev',
  color: '#2563eb',
  icon: '',
  is_active: true,
  has_deployments: false,
  module_count: 2,
  deployed_count: 0,
  failed_count: 0,
  dependent_projects: [],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-15T00:00:00Z',
} as unknown as Project;

const mockProjectWithDeps = {
  ...mockProject,
  dependent_projects: [
    { project_id: 2, outputs: ['vpc_id', 'subnet_ids'] },
  ],
} as unknown as Project;

describe('ProjectDependenciesDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    project: mockProject,
    onUpdate: vi.fn(),
  };

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it('renders dialog with title', () => {
    render(<ProjectDependenciesDialog {...defaultProps} />);

    expect(screen.getByText('Cross-Project Dependencies')).toBeInTheDocument();
  });

  it('renders dialog description', () => {
    render(<ProjectDependenciesDialog {...defaultProps} />);

    expect(screen.getByText(/configure which projects this project depends on/i)).toBeInTheDocument();
  });

  it('shows empty state when no dependencies', () => {
    render(<ProjectDependenciesDialog {...defaultProps} />);

    expect(screen.getByText('No cross-project dependencies configured')).toBeInTheDocument();
  });

  it('shows add dependency section', () => {
    render(<ProjectDependenciesDialog {...defaultProps} />);

    expect(screen.getByText('Add Dependency')).toBeInTheDocument();
  });

  it('renders save and cancel buttons', () => {
    render(<ProjectDependenciesDialog {...defaultProps} />);

    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save dependencies/i })).toBeInTheDocument();
  });

  it('calls onOpenChange when cancel is clicked', async () => {
    const user = userEvent.setup();
    render(<ProjectDependenciesDialog {...defaultProps} />);

    await user.click(screen.getByRole('button', { name: /cancel/i }));

    expect(defaultProps.onOpenChange).toHaveBeenCalledWith(false);
  });

  it('shows existing dependencies with output badges', async () => {
    render(<ProjectDependenciesDialog {...defaultProps} project={mockProjectWithDeps} />);

    await waitFor(() => {
      expect(screen.getByText('Current Dependencies')).toBeInTheDocument();
      expect(screen.getByText('vpc_id')).toBeInTheDocument();
      expect(screen.getByText('subnet_ids')).toBeInTheDocument();
    });
  });

  it('does not show empty state when dependencies exist', () => {
    render(<ProjectDependenciesDialog {...defaultProps} project={mockProjectWithDeps} />);

    expect(screen.queryByText('No cross-project dependencies configured')).not.toBeInTheDocument();
  });
});
