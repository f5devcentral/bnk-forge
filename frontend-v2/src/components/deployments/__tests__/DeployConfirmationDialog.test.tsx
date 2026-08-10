/**
 * Tests for DeployConfirmationDialog component
 *
 * Covers: renders dialog with module info, shows warning list, confirm calls
 * callbacks and closes, cancel closes dialog, deploying state disables buttons.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { DeployConfirmationDialog } from '../DeployConfirmationDialog';

describe('DeployConfirmationDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    moduleName: 'vpc',
    modulePath: 'modules/vpc',
    onConfirm: vi.fn(),
    isDeploying: false,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the dialog with module name and path', () => {
    render(<DeployConfirmationDialog {...defaultProps} />);

    expect(screen.getByText('Confirm Deployment')).toBeInTheDocument();
    expect(screen.getByText('vpc')).toBeInTheDocument();
    expect(screen.getByText('modules/vpc')).toBeInTheDocument();
    expect(
      screen.getByText(
        'You are about to deploy infrastructure changes. Please review before proceeding.'
      )
    ).toBeInTheDocument();
  });

  it('shows warning messages about infrastructure changes', () => {
    render(<DeployConfirmationDialog {...defaultProps} />);

    expect(
      screen.getByText('This will apply infrastructure changes')
    ).toBeInTheDocument();
    expect(
      screen.getByText('Resources may be created, modified, or destroyed')
    ).toBeInTheDocument();
    expect(
      screen.getByText('Changes will be applied to your cloud provider')
    ).toBeInTheDocument();
    expect(
      screen.getByText('This action cannot be easily undone')
    ).toBeInTheDocument();
    expect(
      screen.getByText('Estimated deployment time: 2-5 minutes')
    ).toBeInTheDocument();
  });

  it('calls onConfirm and closes dialog when Deploy Now is clicked', async () => {
    const user = userEvent.setup();
    render(<DeployConfirmationDialog {...defaultProps} />);

    const deployButton = screen.getByRole('button', { name: /deploy now/i });
    await user.click(deployButton);

    expect(defaultProps.onConfirm).toHaveBeenCalledTimes(1);
    expect(defaultProps.onOpenChange).toHaveBeenCalledWith(false);
  });

  it('calls onOpenChange(false) when Cancel is clicked', async () => {
    const user = userEvent.setup();
    render(<DeployConfirmationDialog {...defaultProps} />);

    const cancelButton = screen.getByRole('button', { name: /cancel/i });
    await user.click(cancelButton);

    expect(defaultProps.onOpenChange).toHaveBeenCalledWith(false);
    expect(defaultProps.onConfirm).not.toHaveBeenCalled();
  });

  it('disables buttons and shows "Deploying..." when isDeploying is true', () => {
    render(<DeployConfirmationDialog {...defaultProps} isDeploying={true} />);

    const cancelButton = screen.getByRole('button', { name: /cancel/i });
    const deployButton = screen.getByRole('button', { name: /deploying/i });

    expect(cancelButton).toBeDisabled();
    expect(deployButton).toBeDisabled();
  });

  it('does not render when open is false', () => {
    render(<DeployConfirmationDialog {...defaultProps} open={false} />);

    expect(screen.queryByText('Confirm Deployment')).not.toBeInTheDocument();
  });
});
