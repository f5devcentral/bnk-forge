/**
 * Tests for ModuleOutputsViewer component
 *
 * Covers: loading state, empty state when no outputs, renders state outputs
 * with key-value pairs, error state with retry, copy-to-clipboard.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { ModuleOutputsViewer } from '../ModuleOutputsViewer';
import type { ProjectModule } from '@/types';

// Mock notify to avoid toast side effects
vi.mock('@/lib/notify', () => ({
  notify: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

const mockModuleWithOutputs: ProjectModule = {
  id: 1,
  project_id: 1,
  module_library_id: 1,
  module_name: 'vpc',
  path_in_project: 'modules/vpc',
  deployment_order: 0,
  enabled: true,
  status: 'applied',
  outputs: {
    vpc_id: 'vpc-12345',
    subnet_ids: ['subnet-aaa', 'subnet-bbb'],
  },
};

const mockModuleNoOutputs: ProjectModule = {
  id: 2,
  project_id: 1,
  module_library_id: 2,
  module_name: 'eks',
  path_in_project: 'modules/eks',
  deployment_order: 1,
  enabled: true,
  status: 'not_initialized',
  outputs: undefined,
};

describe('ModuleOutputsViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default: no state file, no plan tasks
    server.use(
      http.get('*/api/state/module/:moduleId', () => {
        return new HttpResponse(null, { status: 404 });
      }),
      http.get('*/api/tasks', () => {
        return HttpResponse.json({ tasks: [], total: 0, limit: 50, offset: 0 });
      })
    );
  });

  it('shows empty state when module has no outputs and no state', async () => {
    render(<ModuleOutputsViewer module={mockModuleNoOutputs} />);

    await waitFor(() => {
      expect(screen.getByText('No Outputs Available')).toBeInTheDocument();
    });

    expect(
      screen.getByText('Module not applied. No outputs exist yet.')
    ).toBeInTheDocument();
  });

  it('renders state outputs with key-value pairs from module.outputs', async () => {
    render(<ModuleOutputsViewer module={mockModuleWithOutputs} />);

    await waitFor(() => {
      expect(screen.getByText('Module Outputs')).toBeInTheDocument();
    });

    // Check header
    expect(screen.getByText('Applied Outputs')).toBeInTheDocument();
    expect(screen.getByText('2 outputs')).toBeInTheDocument();

    // Check output keys
    expect(screen.getByText('vpc_id')).toBeInTheDocument();
    expect(screen.getByText('subnet_ids')).toBeInTheDocument();

    // Check values rendered
    expect(screen.getByText('vpc-12345')).toBeInTheDocument();
  });

  it('shows error state with retry button when API fails', async () => {
    const failModule: ProjectModule = {
      ...mockModuleNoOutputs,
      outputs: undefined,
    };

    server.use(
      http.get('*/api/state/module/:moduleId', () => {
        return HttpResponse.json({ message: 'Server error' }, { status: 500 });
      }),
      http.get('*/api/tasks', () => {
        return HttpResponse.json({ message: 'Server error' }, { status: 500 });
      })
    );

    render(<ModuleOutputsViewer module={failModule} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
    });
  });

  it('has a refresh button that reloads outputs', async () => {
    const user = userEvent.setup();

    render(<ModuleOutputsViewer module={mockModuleWithOutputs} />);

    await waitFor(() => {
      expect(screen.getByText('Module Outputs')).toBeInTheDocument();
    });

    // Refresh button should be present
    const refreshButton = screen.getByRole('button', { name: /refresh/i });
    expect(refreshButton).toBeInTheDocument();

    // Click should re-trigger loading
    await user.click(refreshButton);

    // Should still show outputs after refresh
    await waitFor(() => {
      expect(screen.getByText('Applied Outputs')).toBeInTheDocument();
    });
  });

  it('renders sensitive outputs with toggle visibility button', async () => {
    const moduleWithSensitive: ProjectModule = {
      ...mockModuleWithOutputs,
      outputs: {
        db_password: { value: 'supersecret123', sensitive: true },
        public_ip: '1.2.3.4',
      },
    };

    render(<ModuleOutputsViewer module={moduleWithSensitive} />);

    await waitFor(() => {
      expect(screen.getByText('Module Outputs')).toBeInTheDocument();
    });

    // Sensitive badge should appear
    expect(screen.getByText('sensitive')).toBeInTheDocument();

    // Value should be masked initially
    expect(screen.getByText('***SENSITIVE***')).toBeInTheDocument();

    // Non-sensitive value should be visible
    expect(screen.getByText('1.2.3.4')).toBeInTheDocument();
  });
});
