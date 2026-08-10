/**
 * Tests for AddModuleToProjectDialog component
 *
 * Covers: renders dialog, shows module library list, wizard steps,
 * module selection, project selection, confirm adds module.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { AddModuleToProjectDialog } from '../AddModuleToProjectDialog';
import type { ModuleLibrary } from '@/types';

const mockLibraryModule: ModuleLibrary = {
  id: 1,
  name: 'vpc',
  description: 'AWS VPC module',
  category: 'network',
  provider: 'aws',
  version: '1.0.0',
  variables: [
    { name: 'cidr_block', type: 'string', description: 'VPC CIDR block', required: true, sensitive: false, default: '10.0.0.0/16' },
  ],
  deployment_order: 0,
};

describe('AddModuleToProjectDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    module: mockLibraryModule,
    projectId: 1,
  };

  beforeEach(() => {
    vi.clearAllMocks();

    // Set up smart-defaults endpoint
    server.use(
      http.get('*/api/module-library/:moduleId/smart-defaults', () => {
        return HttpResponse.json({ success: true, defaults: { cidr_block: '10.0.0.0/16' } });
      })
    );
  });

  it('renders dialog with title and description', () => {
    render(<AddModuleToProjectDialog {...defaultProps} />);

    expect(screen.getByText('Add Module to Project')).toBeInTheDocument();
    expect(screen.getByText(/configure and add vpc to your project/i)).toBeInTheDocument();
  });

  it('shows 3-step progress indicator', () => {
    render(<AddModuleToProjectDialog {...defaultProps} />);

    // "Select Project" appears both as step label and as the Label component
    const selectProjectElements = screen.getAllByText('Select Project');
    expect(selectProjectElements.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Configure')).toBeInTheDocument();
    expect(screen.getByText('Confirm')).toBeInTheDocument();
  });

  it('shows module library search when no module prop is provided', async () => {
    render(
      <AddModuleToProjectDialog
        open={true}
        onOpenChange={vi.fn()}
        module={null}
        projectId={1}
      />
    );

    // Should show search input for module library
    await waitFor(() => {
      expect(screen.getByLabelText(/search modules/i)).toBeInTheDocument();
    });
  });

  it('displays module list from library when no module prop', async () => {
    render(
      <AddModuleToProjectDialog
        open={true}
        onOpenChange={vi.fn()}
        module={null}
        projectId={1}
      />
    );

    // Module library items should appear
    await waitFor(() => {
      expect(screen.getByText('vpc')).toBeInTheDocument();
    });
  });

  it('shows project dropdown on step 1', async () => {
    render(<AddModuleToProjectDialog {...defaultProps} />);

    // The first step should show project selection
    await waitFor(() => {
      expect(screen.getByText(/choose which project to add this module to/i)).toBeInTheDocument();
    });
  });

  it('navigates to configure step when Next is clicked', async () => {
    const user = userEvent.setup();
    render(<AddModuleToProjectDialog {...defaultProps} />);

    // Wait for project data to load
    await waitFor(() => {
      expect(screen.getByText('Next')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Next'));

    // Should advance to configure step
    await waitFor(() => {
      expect(screen.getByText('Configure Module')).toBeInTheDocument();
    });
  });

  it('shows configure step with path input and variables', async () => {
    const user = userEvent.setup();
    render(<AddModuleToProjectDialog {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Next')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Next'));

    await waitFor(() => {
      expect(screen.getByText('Configure Module')).toBeInTheDocument();
      expect(screen.getByLabelText(/logical path/i)).toBeInTheDocument();
    });
  });

  it('navigates through all 3 steps and shows confirm step', async () => {
    const user = userEvent.setup();
    render(<AddModuleToProjectDialog {...defaultProps} />);

    // Step 1 -> Step 2
    await waitFor(() => {
      expect(screen.getByText('Next')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Next'));

    await waitFor(() => {
      expect(screen.getByText('Configure Module')).toBeInTheDocument();
    });

    // Step 2 -> Step 3
    await user.click(screen.getByText('Next'));

    await waitFor(() => {
      expect(screen.getByText('Review Configuration')).toBeInTheDocument();
    });

    // Should show module info in review
    expect(screen.getByText(/vpc v1.0.0/)).toBeInTheDocument();
  });

  it('calls API when Add Module button is clicked on confirm step', async () => {
    let addCalled = false;
    server.use(
      http.post('*/api/project-modules/project/:projectId/add', async () => {
        addCalled = true;
        return HttpResponse.json({
          id: 10,
          status: 'not_initialized',
          created_at: new Date().toISOString(),
        });
      })
    );

    const user = userEvent.setup();
    render(<AddModuleToProjectDialog {...defaultProps} />);

    // Navigate to step 3
    await waitFor(() => expect(screen.getByText('Next')).toBeInTheDocument());
    await user.click(screen.getByText('Next'));
    await waitFor(() => expect(screen.getByText('Configure Module')).toBeInTheDocument());
    await user.click(screen.getByText('Next'));
    await waitFor(() => expect(screen.getByText('Review Configuration')).toBeInTheDocument());

    // Click Add Module
    await user.click(screen.getByRole('button', { name: /add module/i }));

    await waitFor(() => {
      expect(addCalled).toBe(true);
    });
  });

  it('Back button goes to previous step', async () => {
    const user = userEvent.setup();
    render(<AddModuleToProjectDialog {...defaultProps} />);

    // Go to step 2
    await waitFor(() => expect(screen.getByText('Next')).toBeInTheDocument());
    await user.click(screen.getByText('Next'));
    await waitFor(() => expect(screen.getByText('Configure Module')).toBeInTheDocument());

    // Click Back
    await user.click(screen.getByRole('button', { name: /back/i }));

    await waitFor(() => {
      expect(screen.getByText(/choose which project to add this module to/i)).toBeInTheDocument();
    });
  });

  it('uses a dedicated configuration screen with a larger fixed dialog layout', () => {
    render(<AddModuleToProjectDialog {...defaultProps} />);

    const title = screen.getByText('Add Module to Project');
    const dialogContent = title.closest('[class*="h-"]');
    expect(dialogContent).toBeTruthy();
    expect(dialogContent?.className).toContain('h-[85vh]');
    expect(dialogContent?.className).toContain('overflow-hidden');
    expect(dialogContent?.className).toContain('sm:max-w-[760px]');
  });

  it('prefills IBM credential variables from the selected project template', async () => {
    const ibmModule: ModuleLibrary = {
      ...mockLibraryModule,
      id: 7,
      name: 'ibm_roks_single_nic',
      provider: 'ibm',
      variables: [
        { name: 'ibmcloud_api_key', type: 'string', description: 'IBM API key', required: true, sensitive: true, default: undefined },
        { name: 'ibmcloud_cluster_region', type: 'string', description: 'IBM region', required: true, sensitive: false, default: undefined },
        { name: 'ibmcloud_resource_group', type: 'string', description: 'IBM resource group', required: false, sensitive: false, default: undefined },
      ],
    };

    server.use(
      http.get('*/api/module-library/:moduleId/smart-defaults', () => {
        return HttpResponse.json({
          success: true,
          defaults: {
            ibmcloud_cluster_region: 'us-south',
            ibmcloud_resource_group: 'platform-rg',
            ibmcloud_api_key: 'template-secret',
          },
        });
      })
    );

    const user = userEvent.setup();
    render(
      <AddModuleToProjectDialog
        open={true}
        onOpenChange={vi.fn()}
        module={ibmModule}
        projectId={3}
      />
    );

    await waitFor(() => expect(screen.getByText('Next')).toBeInTheDocument());
    await user.click(screen.getByText('Next'));

    await waitFor(() => {
      expect(screen.getByDisplayValue('us-south')).toBeInTheDocument();
      expect(screen.getByDisplayValue('platform-rg')).toBeInTheDocument();
    });

    const passwordInput = document.querySelector('input[type="password"]') as HTMLInputElement | null;
    expect(passwordInput).toBeTruthy();
    expect(passwordInput?.value).toBe('template-secret');
  });

  it('masks sensitive values on the review step', async () => {
    const ibmModule: ModuleLibrary = {
      ...mockLibraryModule,
      id: 7,
      name: 'ibm_roks_single_nic',
      provider: 'ibm',
      variables: [
        { name: 'ibmcloud_api_key', type: 'string', description: 'IBM API key', required: true, sensitive: true, default: undefined },
      ],
    };

    server.use(
      http.get('*/api/module-library/:moduleId/smart-defaults', () => {
        return HttpResponse.json({
          success: true,
          defaults: {
            ibmcloud_api_key: 'template-secret',
          },
        });
      })
    );

    const user = userEvent.setup();
    render(
      <AddModuleToProjectDialog
        open={true}
        onOpenChange={vi.fn()}
        module={ibmModule}
        projectId={3}
      />
    );

    await user.click(screen.getByText('Next'));
    await user.click(screen.getByText('Next'));

    await waitFor(() => {
      expect(screen.getByText('Review Configuration')).toBeInTheDocument();
      expect(screen.getByText('••••••••')).toBeInTheDocument();
    });

    expect(screen.queryByText('template-secret')).not.toBeInTheDocument();
  });
});
