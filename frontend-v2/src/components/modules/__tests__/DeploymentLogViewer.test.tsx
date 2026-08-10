/**
 * Tests for DeploymentLogViewer component
 *
 * Covers: renders log viewer container, shows connect/disconnect buttons,
 * displays connection info alert, shows module name in title.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { DeploymentLogViewer } from '@/components/modules/DeploymentLogViewer';
import type { ProjectModule } from '@/types';

const mockModule: ProjectModule = {
  id: 1,
  project_id: 1,
  module_library_id: 1,
  module_name: 'test-vpc',
  path_in_project: 'modules/vpc',
  deployment_order: 0,
  enabled: true,
  status: 'applied',
  outputs: { vpc_id: 'vpc-12345' },
};

describe('DeploymentLogViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders log viewer with module name and description', () => {
    render(<DeploymentLogViewer module={mockModule} />);

    expect(screen.getByText(/Deployment logs · test-vpc/)).toBeInTheDocument();
    expect(
      screen.getByText('Real-time streaming of OpenTofu operations.')
    ).toBeInTheDocument();
  });

  it('shows connect button when disconnected and connection info alert', () => {
    render(<DeploymentLogViewer module={mockModule} />);

    // Connect button should be visible when not connected
    expect(screen.getByRole('button', { name: /connect/i })).toBeInTheDocument();

    // Connection info alert should be visible
    expect(
      screen.getByText('Click "Connect" to stream real-time logs from OpenTofu operations')
    ).toBeInTheDocument();

    // Status badges
    expect(screen.getByText('disconnected')).toBeInTheDocument();
    expect(screen.getByText('applied')).toBeInTheDocument();
  });

  it('renders control buttons and auto-scroll checkbox', async () => {
    render(<DeploymentLogViewer module={mockModule} />);

    // Connect button
    expect(screen.getByRole('button', { name: /connect/i })).toBeInTheDocument();
    // Clear button
    expect(screen.getByRole('button', { name: /clear/i })).toBeInTheDocument();
    // Download button (disabled when no logs)
    const downloadBtn = screen.getByRole('button', { name: /download/i });
    expect(downloadBtn).toBeInTheDocument();
    expect(downloadBtn).toBeDisabled();
    // Auto-scroll checkbox
    const autoScrollCheckbox = screen.getByRole('checkbox');
    expect(autoScrollCheckbox).toBeInTheDocument();
    expect(autoScrollCheckbox).toBeChecked();
  });
});
