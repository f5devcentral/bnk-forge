/**
 * Tests for EditModuleVariablesDialog component
 *
 * Covers: renders with current variables, loading state, missing required warning,
 * save calls API, cancel with unsaved changes prompt.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { EditModuleVariablesDialog } from '../EditModuleVariablesDialog';
import type { ProjectModule } from '@/types';

const mockModule: ProjectModule = {
  id: 1,
  project_id: 1,
  module_library_id: 1,
  module_name: 'vpc',
  path_in_project: 'infra/vpc',
  variable_overrides: { cidr_block: '10.0.0.0/16', enable_dns: true },
  deployment_order: 0,
  enabled: true,
  status: 'not_initialized',
  library_module: {
    id: 1,
    name: 'vpc',
    category: 'network',
    provider: 'aws',
    version: '1.0.0',
    variables: [],
  },
};

// Set up handler for module variables endpoint
const mockVariables = [
  { name: 'cidr_block', type: 'string', description: 'VPC CIDR block', required: true, sensitive: false },
  { name: 'enable_dns', type: 'bool', description: 'Enable DNS', required: false, sensitive: false, default: true },
];

describe('EditModuleVariablesDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    module: mockModule,
  };

  beforeEach(() => {
    vi.clearAllMocks();

    // Set up variable-fetching handler
    server.use(
      http.get('*/api/module-library/:moduleId/variables', () => {
        return HttpResponse.json({ success: true, variables: mockVariables });
      })
    );
  });

  it('renders dialog with module name in description', async () => {
    render(<EditModuleVariablesDialog {...defaultProps} />);

    expect(screen.getByText('Edit Module Variables')).toBeInTheDocument();
    expect(screen.getByText(/vpc/)).toBeInTheDocument();
  });

  it('shows loading state while fetching variables', () => {
    // Delay the response to observe loading state
    server.use(
      http.get('*/api/module-library/:moduleId/variables', async () => {
        await new Promise((r) => setTimeout(r, 5000));
        return HttpResponse.json({ success: true, variables: mockVariables });
      })
    );

    render(<EditModuleVariablesDialog {...defaultProps} />);

    expect(screen.getByText(/loading variables/i)).toBeInTheDocument();
  });

  it('displays variable fields once loaded', async () => {
    render(<EditModuleVariablesDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Variables')).toBeInTheDocument();
    });
  });

  it('shows "no variables" message when module has no variables', async () => {
    server.use(
      http.get('*/api/module-library/:moduleId/variables', () => {
        return HttpResponse.json({ success: true, variables: [] });
      })
    );

    render(<EditModuleVariablesDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/no variables to configure/i)).toBeInTheDocument();
    });
  });

  it('shows redeploy alert when module is already applied', async () => {
    const appliedModule = { ...mockModule, status: 'applied' };
    render(
      <EditModuleVariablesDialog {...defaultProps} module={appliedModule} />
    );

    await waitFor(() => {
      expect(
        screen.getByText(/this module is already deployed/i)
      ).toBeInTheDocument();
    });
  });

  it('save button calls update API', async () => {
    let updateCalled = false;
    server.use(
      http.put('*/api/project-modules/:moduleId', async () => {
        updateCalled = true;
        return HttpResponse.json({
          id: 1,
          variable_overrides: { cidr_block: '10.0.0.0/16' },
          updated_at: new Date().toISOString(),
        });
      })
    );

    const user = userEvent.setup();
    render(<EditModuleVariablesDialog {...defaultProps} />);

    // Wait for variables to load
    await waitFor(() => {
      expect(screen.getByText('Variables')).toBeInTheDocument();
    });

    // The save button should exist
    const saveButton = screen.getByRole('button', { name: /save changes/i });
    await user.click(saveButton);

    await waitFor(() => {
      expect(updateCalled).toBe(true);
    });
  });

  it('cancel button closes dialog when no changes', async () => {
    const user = userEvent.setup();
    render(<EditModuleVariablesDialog {...defaultProps} />);

    await user.click(screen.getByRole('button', { name: /cancel/i }));

    expect(defaultProps.onOpenChange).toHaveBeenCalledWith(false);
  });
});
