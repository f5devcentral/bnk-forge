/**
 * Tests for CloudCredentialsDialog component
 *
 * Covers: renders dialog, shows editing state when no template assigned,
 * shows current template when assigned, template selection, loading state.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { CloudCredentialsDialog } from '@/components/projects/CloudCredentialsDialog';
import { mockCredentialTemplates } from '@/test/test-fixtures';
import type { Project } from '@/types';

const mockProjectNoCredentials = {
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
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-15T00:00:00Z',
} as Project;

const mockProjectWithCredentials = {
  ...mockProjectNoCredentials,
  credential_template_id: 1,
  credential_template: {
    id: 1,
    name: 'AWS Production',
    provider: 'aws',
    region: 'us-east-1',
    aws_auth_method: 'access_keys',
    is_default: false,
    description: 'Production credentials',
  },
} as unknown as Project;

describe('CloudCredentialsDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    project: mockProjectNoCredentials,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders dialog with title', () => {
    render(<CloudCredentialsDialog {...defaultProps} />);

    expect(screen.getByText('Cloud Provider Credentials')).toBeInTheDocument();
  });

  it('shows security alert about credential templates', () => {
    render(<CloudCredentialsDialog {...defaultProps} />);

    expect(screen.getByText(/credential templates are created and managed in settings/i)).toBeInTheDocument();
  });

  it('shows no credentials warning when none assigned', async () => {
    render(<CloudCredentialsDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/no credentials configured/i)).toBeInTheDocument();
    });
  });

  it('shows template selection form when no credentials assigned', async () => {
    render(<CloudCredentialsDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Select Credential Template')).toBeInTheDocument();
    });
  });

  it('shows active credential template when assigned', async () => {
    render(<CloudCredentialsDialog {...defaultProps} project={mockProjectWithCredentials} />);

    await waitFor(() => {
      expect(screen.getByText('Active Credential Template')).toBeInTheDocument();
      expect(screen.getByText('AWS Production')).toBeInTheDocument();
    });
  });

  it('shows change and remove buttons for active template', async () => {
    render(<CloudCredentialsDialog {...defaultProps} project={mockProjectWithCredentials} />);

    await waitFor(() => {
      expect(screen.getByText('Change Template')).toBeInTheDocument();
      expect(screen.getByText('Remove')).toBeInTheDocument();
    });
  });

  it('switches to editing mode when Change Template is clicked', async () => {
    const user = userEvent.setup();
    render(<CloudCredentialsDialog {...defaultProps} project={mockProjectWithCredentials} />);

    await waitFor(() => {
      expect(screen.getByText('Change Template')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Change Template'));

    await waitFor(() => {
      expect(screen.getByText('Select Credential Template')).toBeInTheDocument();
    });
  });

  it('shows save button in editing mode', async () => {
    render(<CloudCredentialsDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Save Credentials')).toBeInTheDocument();
    });
  });

  it('shows loading spinner while templates are fetching', () => {
    server.use(
      http.get('*/api/credential-templates', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json(mockCredentialTemplates);
      })
    );

    render(<CloudCredentialsDialog {...defaultProps} />);

    const spinners = document.querySelectorAll('[class*="animate-spin"]');
    expect(spinners.length).toBeGreaterThan(0);
  });

  it('shows Go to Settings when no templates available', async () => {
    server.use(
      http.get('*/api/credential-templates', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname !== '/api/credential-templates') return;
        return HttpResponse.json([]);
      })
    );

    render(<CloudCredentialsDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/no credential templates found/i)).toBeInTheDocument();
      expect(screen.getByText('Go to Settings')).toBeInTheDocument();
    });
  });
});
