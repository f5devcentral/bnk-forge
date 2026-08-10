/**
 * Tests for EditProjectDialog component
 *
 * Covers: renders form fields, loads project data, disables name field,
 * shows project type badge, submits update, cancel closes dialog.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { EditProjectDialog } from '@/components/projects/EditProjectDialog';
import { mockProjects, mockCredentialTemplates } from '@/test/test-fixtures';
import type { Project } from '@/types';

const mockProject = {
  ...mockProjects[0],
  credential_template_id: undefined,
  credential_template: undefined,
} as unknown as Project;

const mockProjectWithType = {
  ...mockProjects[0],
  project_type: 'cloud-aws' as const,
  credential_template_id: 1,
  credential_template: { name: 'AWS Production' },
} as unknown as Project;

const mockIBMProjectWithType = {
  ...mockProjects[0],
  project_type: 'cloud-ibm' as const,
  region: 'us-south',
  credential_template_id: 2,
  credential_template: { name: 'IBM Production' },
} as unknown as Project;

describe('EditProjectDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    project: mockProject,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders dialog with title and description', async () => {
    render(<EditProjectDialog {...defaultProps} />);

    expect(screen.getByText('Edit Project')).toBeInTheDocument();
    expect(screen.getByText(/update project settings for/i)).toBeInTheDocument();
  });

  it('renders project name as disabled input', () => {
    render(<EditProjectDialog {...defaultProps} />);

    const nameInput = screen.getByLabelText('Project Name');
    expect(nameInput).toBeDisabled();
    expect(nameInput).toHaveValue(mockProject.name);
  });

  it('shows "cannot be changed" hint for name', () => {
    render(<EditProjectDialog {...defaultProps} />);

    expect(screen.getByText('Project name cannot be changed')).toBeInTheDocument();
  });

  it('renders description textarea', () => {
    render(<EditProjectDialog {...defaultProps} />);

    expect(screen.getByLabelText('Description')).toBeInTheDocument();
  });

  it('renders color input', () => {
    render(<EditProjectDialog {...defaultProps} />);

    expect(screen.getByLabelText('Color')).toBeInTheDocument();
  });

  it('renders cancel and update buttons', () => {
    render(<EditProjectDialog {...defaultProps} />);

    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /update project/i })).toBeInTheDocument();
  });

  it('shows project type badge when type is set', () => {
    render(<EditProjectDialog {...defaultProps} project={mockProjectWithType} />);

    expect(screen.getByText(/AWS Cloud/)).toBeInTheDocument();
  });

  it('shows IBM region guidance for IBM cloud projects', async () => {
    render(<EditProjectDialog {...defaultProps} project={mockIBMProjectWithType} />);

    await waitFor(() => {
      expect(screen.getByText('Regions are loaded from the selected IBM credential template or the default IBM template.')).toBeInTheDocument();
    });
  });

  it('calls onOpenChange when cancel is clicked', async () => {
    const user = userEvent.setup();
    render(<EditProjectDialog {...defaultProps} />);

    await user.click(screen.getByRole('button', { name: /cancel/i }));

    expect(defaultProps.onOpenChange).toHaveBeenCalledWith(false);
  });

  it('submits form and calls update API', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/projects/:id', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true });
      })
    );

    const user = userEvent.setup();
    render(<EditProjectDialog {...defaultProps} />);

    // Update the description
    const descInput = screen.getByLabelText('Description');
    await user.clear(descInput);
    await user.type(descInput, 'Updated description');

    await user.click(screen.getByRole('button', { name: /update project/i }));

    await waitFor(() => {
      expect(capturedBody).toBeDefined();
      expect(capturedBody!.description).toBe('Updated description');
    });
  });

  it('does not render when project is null', () => {
    render(<EditProjectDialog open={true} onOpenChange={vi.fn()} project={null} />);

    expect(screen.queryByText('Edit Project')).not.toBeInTheDocument();
  });

  it('does not render when open is false', () => {
    render(<EditProjectDialog open={false} onOpenChange={vi.fn()} project={mockProject} />);

    expect(screen.queryByText('Edit Project')).not.toBeInTheDocument();
  });

  it('shows credential template loading state', () => {
    server.use(
      http.get('*/api/credential-templates', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json(mockCredentialTemplates);
      })
    );

    render(<EditProjectDialog {...defaultProps} />);

    expect(screen.getByText('Loading templates...')).toBeInTheDocument();
  });
});
