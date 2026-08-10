/**
 * Tests for ApplyConfirmDialog component
 *
 * Covers: renders warning dialog, shows module name, auto-approve checkbox,
 * confirm button calls callback, cancel closes dialog.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { ApplyConfirmDialog } from '../ApplyConfirmDialog';

describe('ApplyConfirmDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    moduleName: 'vpc',
    onConfirm: vi.fn(),
    isPending: false,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the dialog with warning title and module name', () => {
    render(<ApplyConfirmDialog {...defaultProps} />);

    expect(screen.getByText('Apply Infrastructure Changes')).toBeInTheDocument();
    expect(screen.getByText(/vpc/)).toBeInTheDocument();
  });

  it('shows the list of operations that will be performed', () => {
    render(<ApplyConfirmDialog {...defaultProps} />);

    expect(screen.getByText(/execute tofu apply/i)).toBeInTheDocument();
    expect(screen.getByText(/create, update, or modify cloud resources/i)).toBeInTheDocument();
    expect(screen.getByText(/update the opentofu state file/i)).toBeInTheDocument();
  });

  it('auto-approve checkbox is checked by default', () => {
    render(<ApplyConfirmDialog {...defaultProps} />);

    // The checkbox should be checked by default (useState(true))
    const checkbox = screen.getByRole('checkbox', { name: /auto-approve/i });
    expect(checkbox).toBeChecked();
  });

  it('shows warning when auto-approve is unchecked', async () => {
    const user = userEvent.setup();
    render(<ApplyConfirmDialog {...defaultProps} />);

    const checkbox = screen.getByRole('checkbox', { name: /auto-approve/i });
    await user.click(checkbox);

    // Warning about EOF error should appear
    expect(screen.getByText(/without auto-approve/i)).toBeInTheDocument();
  });

  it('calls onConfirm with autoApprove=true and closes dialog when confirmed', async () => {
    const user = userEvent.setup();
    render(<ApplyConfirmDialog {...defaultProps} />);

    const applyButton = screen.getByRole('button', { name: /apply with auto-approve/i });
    await user.click(applyButton);

    expect(defaultProps.onConfirm).toHaveBeenCalledWith(true);
    expect(defaultProps.onOpenChange).toHaveBeenCalledWith(false);
  });

  it('calls onConfirm with autoApprove=false after unchecking', async () => {
    const user = userEvent.setup();
    render(<ApplyConfirmDialog {...defaultProps} />);

    const checkbox = screen.getByRole('checkbox', { name: /auto-approve/i });
    await user.click(checkbox);

    const applyButton = screen.getByRole('button', { name: /apply \(manual confirm\)/i });
    await user.click(applyButton);

    expect(defaultProps.onConfirm).toHaveBeenCalledWith(false);
  });

  it('cancel button closes the dialog', async () => {
    const user = userEvent.setup();
    render(<ApplyConfirmDialog {...defaultProps} />);

    await user.click(screen.getByRole('button', { name: /cancel/i }));

    expect(defaultProps.onOpenChange).toHaveBeenCalledWith(false);
  });

  it('disables buttons when isPending is true', () => {
    render(<ApplyConfirmDialog {...defaultProps} isPending={true} />);

    const cancelButton = screen.getByRole('button', { name: /cancel/i });
    const applyButton = screen.getByRole('button', { name: /applying/i });

    expect(cancelButton).toBeDisabled();
    expect(applyButton).toBeDisabled();
  });

  it('does not render when open is false', () => {
    render(<ApplyConfirmDialog {...defaultProps} open={false} />);

    expect(screen.queryByText('Apply Infrastructure Changes')).not.toBeInTheDocument();
  });
});
