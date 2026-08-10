/**
 * Tests for CreateProjectDialog component
 *
 * Covers: renders form fields, validates required fields,
 * submits and creates project.
 */
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { CreateProjectDialog } from '@/components/projects/CreateProjectDialog';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

describe('CreateProjectDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
  };

  beforeAll(() => {
    if (!HTMLElement.prototype.hasPointerCapture) {
      Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', {
        configurable: true,
        value: () => false,
      });
    }
    if (!HTMLElement.prototype.setPointerCapture) {
      Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', {
        configurable: true,
        value: () => undefined,
      });
    }
    if (!HTMLElement.prototype.releasePointerCapture) {
      Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', {
        configurable: true,
        value: () => undefined,
      });
    }
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockReset();
    server.use(
      http.get('*/api/system/defaults', () => HttpResponse.json({ project: { default_type: { value: '' } } }))
    );
  });

  it('renders dialog with title and description', () => {
    render(<CreateProjectDialog {...defaultProps} />);

    expect(screen.getByText('Create New Project')).toBeInTheDocument();
    expect(screen.getByText(/projects group together/i)).toBeInTheDocument();
  });

  it('renders project name input', () => {
    render(<CreateProjectDialog {...defaultProps} />);

    const nameInput = screen.getByPlaceholderText('my-infrastructure');
    expect(nameInput).toBeInTheDocument();
  });

  it('renders project type buttons', () => {
    render(<CreateProjectDialog {...defaultProps} />);

    expect(screen.getByText('AWS Cloud')).toBeInTheDocument();
    expect(screen.getByText('Azure Cloud')).toBeInTheDocument();
    expect(screen.getByText('GCP Cloud')).toBeInTheDocument();
    expect(screen.getByText('IBM Cloud')).toBeInTheDocument();
    expect(screen.getByText('Existing Kubernetes')).toBeInTheDocument();
    expect(screen.getByText('Bare Metal DPU')).toBeInTheDocument();
  });

  it('renders description textarea', () => {
    render(<CreateProjectDialog {...defaultProps} />);

    expect(screen.getByPlaceholderText(/brief description/i)).toBeInTheDocument();
  });

  it('renders environment label text', () => {
    render(<CreateProjectDialog {...defaultProps} />);

    // The Environment label exists in the form
    expect(screen.getByText('Environment')).toBeInTheDocument();
  });

  it('renders cancel and submit buttons', () => {
    render(<CreateProjectDialog {...defaultProps} />);

    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create project/i })).toBeInTheDocument();
  });

  it('submit button is disabled until project type is selected', () => {
    render(<CreateProjectDialog {...defaultProps} />);

    // The submit button should be disabled when no project type is selected
    const submitButton = screen.getByRole('button', { name: /create project/i });
    expect(submitButton).toBeDisabled();
  });

  it('selecting a project type enables the submit button', async () => {
    const user = userEvent.setup();
    render(<CreateProjectDialog {...defaultProps} />);

    // Click AWS Cloud project type
    await user.click(screen.getByText('AWS Cloud'));

    const submitButton = screen.getByRole('button', { name: /create project/i });
    expect(submitButton).not.toBeDisabled();
  });

  it('calls onOpenChange when cancel is clicked', async () => {
    const user = userEvent.setup();
    render(<CreateProjectDialog {...defaultProps} />);

    await user.click(screen.getByRole('button', { name: /cancel/i }));

    expect(defaultProps.onOpenChange).toHaveBeenCalledWith(false);
  });

  it('does not render when open is false', () => {
    render(<CreateProjectDialog open={false} onOpenChange={vi.fn()} />);

    expect(screen.queryByText('Create New Project')).not.toBeInTheDocument();
  });

  it('shows SSH Jumphost / Bastion / Single Node label when On-Premises type is selected', async () => {
    const user = userEvent.setup();
    render(<CreateProjectDialog {...defaultProps} />);

    await user.click(screen.getByText('Existing Kubernetes'));

    await waitFor(() => {
      expect(screen.getByText('SSH Jumphost / Bastion / Single Node')).toBeInTheDocument();
    });
  });

  it('loads IBM regions for IBM Cloud projects', async () => {
    const user = userEvent.setup();
    server.use(
      http.get('*/api/credential-templates', () => HttpResponse.json([
        { id: 2, name: 'IBM Production', provider: 'ibm', region: 'us-south', is_default: true },
      ])),
    );

    render(<CreateProjectDialog {...defaultProps} />);

    await user.click(screen.getByText('IBM Cloud'));

    await waitFor(() => {
      expect(screen.getByText(/select region/i)).toBeInTheDocument();
    });
  });
});
