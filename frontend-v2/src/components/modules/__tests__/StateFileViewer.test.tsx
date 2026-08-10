/**
 * Tests for StateFileViewer component
 *
 * Covers: renders state data with outputs, shows "no state" when module
 * has no outputs, displays output key/values, copy buttons.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { StateFileViewer } from '@/components/modules/StateFileViewer';
import type { ProjectModule } from '@/types';

const mockModuleWithOutputs: ProjectModule = {
  id: 1,
  project_id: 1,
  module_library_id: 1,
  module_name: 'test-vpc',
  path_in_project: 'modules/vpc',
  deployment_order: 0,
  enabled: true,
  status: 'applied',
  outputs: {
    vpc_id: 'vpc-12345',
    subnet_ids: ['subnet-1', 'subnet-2'],
  },
};

const mockModuleNoOutputs: ProjectModule = {
  id: 2,
  project_id: 1,
  module_library_id: 1,
  module_name: 'test-eks',
  path_in_project: 'modules/eks',
  deployment_order: 1,
  enabled: true,
  status: 'not_initialized',
  outputs: {},
};

describe('StateFileViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders state data with outputs and v2 architecture notice', async () => {
    render(<StateFileViewer module={mockModuleWithOutputs} />);

    await waitFor(() => {
      expect(screen.getByText('Module Outputs')).toBeInTheDocument();
    });

    // v2 Architecture notice
    expect(screen.getByText(/v2 Architecture:/)).toBeInTheDocument();
    expect(
      screen.getByText(/State files are stored locally for security/)
    ).toBeInTheDocument();

    // Output keys
    expect(screen.getByText('vpc_id')).toBeInTheDocument();
    expect(screen.getByText('subnet_ids')).toBeInTheDocument();

    // Output values
    expect(screen.getByText('vpc-12345')).toBeInTheDocument();

    // Output count
    expect(screen.getByText('2 outputs from database')).toBeInTheDocument();
  });

  it('shows "State Not Available" when module has no outputs', async () => {
    render(<StateFileViewer module={mockModuleNoOutputs} />);

    await waitFor(() => {
      expect(screen.getByText('State Not Available')).toBeInTheDocument();
    });

    expect(
      screen.getByText('Module not applied. No state file exists yet.')
    ).toBeInTheDocument();
  });

  it('renders refresh button and copy buttons for each output', async () => {
    render(<StateFileViewer module={mockModuleWithOutputs} />);

    await waitFor(() => {
      expect(screen.getByText('Module Outputs')).toBeInTheDocument();
    });

    // Refresh button
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument();

    // Copy buttons (one per output key: vpc_id and subnet_ids)
    // The copy buttons use the Copy icon and don't have text labels,
    // so we check for the button count. There should be at least 2 ghost buttons.
    const allButtons = screen.getAllByRole('button');
    // At least refresh + 2 copy buttons
    expect(allButtons.length).toBeGreaterThanOrEqual(3);
  });
});
