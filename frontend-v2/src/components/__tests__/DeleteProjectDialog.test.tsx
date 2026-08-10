/**
 * Tests for DeleteProjectDialog component
 *
 * Covers: renders warning, type-to-confirm, delete button disabled until
 * name matches, shows consequences, cancel closes dialog.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { DeleteProjectDialog } from '@/components/projects/DeleteProjectDialog';
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
  has_deployments: true,
  module_count: 3,
  deployed_count: 2,
  failed_count: 1,
  cluster_count: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-15T00:00:00Z',
} as Project;

describe('DeleteProjectDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    project: mockProject,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders dialog with destructive title', () => {
    render(<DeleteProjectDialog {...defaultProps} />);

    // Title and button both say "Delete Project" — check for multiple
    const elements = screen.getAllByText('Delete Project');
    expect(elements.length).toBeGreaterThanOrEqual(1);
  });

  it('renders permanent deletion warning', () => {
    render(<DeleteProjectDialog {...defaultProps} />);

    expect(screen.getByText(/this action cannot be undone/i)).toBeInTheDocument();
  });

  it('displays project name in info box', () => {
    render(<DeleteProjectDialog {...defaultProps} />);

    // Name appears in both the info box and the confirm prompt
    const elements = screen.getAllByText(mockProject.name);
    expect(elements.length).toBeGreaterThanOrEqual(1);
  });

  it('shows module count in consequences', () => {
    render(<DeleteProjectDialog {...defaultProps} />);

    expect(screen.getByText(/3 module\(s\)/)).toBeInTheDocument();
  });

  it('shows deployment history in consequences', () => {
    render(<DeleteProjectDialog {...defaultProps} />);

    expect(screen.getByText(/2 successful deployment\(s\)/)).toBeInTheDocument();
  });

  it('shows infrastructure not affected warning', () => {
    render(<DeleteProjectDialog {...defaultProps} />);

    expect(screen.getByText('Infrastructure Not Affected')).toBeInTheDocument();
  });

  it('renders confirmation input with project name prompt', () => {
    render(<DeleteProjectDialog {...defaultProps} />);

    expect(screen.getByText(/type/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(mockProject.name)).toBeInTheDocument();
  });

  it('delete button is disabled until name is typed', () => {
    render(<DeleteProjectDialog {...defaultProps} />);

    const deleteButton = screen.getByRole('button', { name: /delete project/i });
    expect(deleteButton).toBeDisabled();
  });

  it('delete button is enabled when name matches exactly', async () => {
    const user = userEvent.setup();
    render(<DeleteProjectDialog {...defaultProps} />);

    await user.type(screen.getByPlaceholderText(mockProject.name), mockProject.name);

    const deleteButton = screen.getByRole('button', { name: /delete project/i });
    expect(deleteButton).not.toBeDisabled();
  });

  it('delete button stays disabled for partial name match', async () => {
    const user = userEvent.setup();
    render(<DeleteProjectDialog {...defaultProps} />);

    await user.type(screen.getByPlaceholderText(mockProject.name), 'test-');

    const deleteButton = screen.getByRole('button', { name: /delete project/i });
    expect(deleteButton).toBeDisabled();
  });

  it('calls delete API when confirmed', async () => {
    let deleteCalled = false;
    server.use(
      http.delete('*/api/projects/:id', () => {
        deleteCalled = true;
        return HttpResponse.json({ success: true });
      })
    );

    const user = userEvent.setup();
    render(<DeleteProjectDialog {...defaultProps} />);

    await user.type(screen.getByPlaceholderText(mockProject.name), mockProject.name);
    await user.click(screen.getByRole('button', { name: /delete project/i }));

    await waitFor(() => {
      expect(deleteCalled).toBe(true);
    });
  });

  it('calls onOpenChange when cancel is clicked', async () => {
    const user = userEvent.setup();
    render(<DeleteProjectDialog {...defaultProps} />);

    await user.click(screen.getByRole('button', { name: /cancel/i }));

    expect(defaultProps.onOpenChange).toHaveBeenCalled();
  });

  it('does not render when project is null', () => {
    render(<DeleteProjectDialog open={true} onOpenChange={vi.fn()} project={null} />);

    expect(screen.queryByText('Delete Project')).not.toBeInTheDocument();
  });

  it('does not render when open is false', () => {
    render(<DeleteProjectDialog open={false} onOpenChange={vi.fn()} project={mockProject} />);

    expect(screen.queryByText('Delete Project')).not.toBeInTheDocument();
  });
});
