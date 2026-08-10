import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { DestructiveConfirmDialog } from '../destructive-confirm-dialog';

describe('DestructiveConfirmDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    title: 'Delete VPC',
    description: 'This action cannot be undone.',
    confirmText: 'DELETE',
    onConfirm: vi.fn(),
  };

  it('renders dialog with title, description, and confirmation input', () => {
    render(<DestructiveConfirmDialog {...defaultProps} />);
    expect(screen.getByText('Delete VPC')).toBeInTheDocument();
    expect(screen.getByText('This action cannot be undone.')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Type "DELETE" here')).toBeInTheDocument();
    expect(screen.getByText('Delete Permanently')).toBeDisabled();
  });

  it('enables confirm button only after typing exact confirm text', async () => {
    const user = userEvent.setup();
    render(<DestructiveConfirmDialog {...defaultProps} />);

    const input = screen.getByPlaceholderText('Type "DELETE" here');
    const confirmButton = screen.getByText('Delete Permanently');

    // Type wrong text - button stays disabled
    await user.type(input, 'DELET');
    expect(confirmButton).toBeDisabled();

    // Complete the text
    await user.type(input, 'E');
    await waitFor(() => {
      expect(confirmButton).not.toBeDisabled();
    });
  });

  it('renders warning items and consequences when provided', () => {
    render(
      <DestructiveConfirmDialog
        {...defaultProps}
        warningItems={['3 subnets', '1 NAT gateway']}
        consequences={['All data will be lost', 'Connections will be dropped']}
      />
    );
    expect(screen.getByText('The following will be affected:')).toBeInTheDocument();
    expect(screen.getByText('3 subnets')).toBeInTheDocument();
    expect(screen.getByText('1 NAT gateway')).toBeInTheDocument();
    expect(screen.getByText('Consequences:')).toBeInTheDocument();
  });
});
