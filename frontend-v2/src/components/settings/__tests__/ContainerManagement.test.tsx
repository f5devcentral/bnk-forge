/**
 * Tests for ContainerManagement component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import ContainerManagement from '../ContainerManagement';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ContainerManagement', () => {
  it('renders collapsible card header', () => {
    render(<ContainerManagement />);
    expect(screen.getByText('Container management')).toBeInTheDocument();
    expect(
      screen.getByText('View status and restart Docker containers for BNK Forge services')
    ).toBeInTheDocument();
  });

  it('expands to show container list when header is clicked', async () => {
    const user = userEvent.setup();
    render(<ContainerManagement />);

    await user.click(screen.getByText('Container management'));

    await waitFor(() => {
      expect(screen.getByText('backend')).toBeInTheDocument();
    });
    expect(screen.getByText('frontend')).toBeInTheDocument();
    expect(screen.getByText('postgres')).toBeInTheDocument();
  });

  it('shows action buttons when expanded', async () => {
    const user = userEvent.setup();
    render(<ContainerManagement />);

    await user.click(screen.getByText('Container management'));

    await waitFor(() => {
      expect(screen.getByText('Refresh Status')).toBeInTheDocument();
      expect(screen.getByText(/Restart Selected/)).toBeInTheDocument();
      expect(screen.getByText('Restart All Services')).toBeInTheDocument();
    });
  });
});
