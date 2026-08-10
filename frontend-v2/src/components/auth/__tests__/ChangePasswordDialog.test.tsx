import { describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { ChangePasswordDialog } from '@/components/auth/ChangePasswordDialog';

function setup() {
  const onOpenChange = vi.fn();
  render(<ChangePasswordDialog open={true} onOpenChange={onOpenChange} />);
  return {
    onOpenChange,
    user: userEvent.setup(),
    current: () => screen.getByLabelText('Current Password') as HTMLInputElement,
    next: () => screen.getByLabelText('New Password') as HTMLInputElement,
    confirm: () => screen.getByLabelText('Confirm New Password') as HTMLInputElement,
    submit: () => screen.getByRole('button', { name: /change password/i }),
  };
}

describe('ChangePasswordDialog', () => {
  it('renders all three password fields', () => {
    setup();
    expect(screen.getByLabelText('Current Password')).toBeInTheDocument();
    expect(screen.getByLabelText('New Password')).toBeInTheDocument();
    expect(screen.getByLabelText('Confirm New Password')).toBeInTheDocument();
  });

  it('rejects submit when new password is shorter than 8 characters', async () => {
    const { user, current, next, confirm, submit } = setup();
    await user.type(current(), 'oldpass1');
    await user.type(next(), 'short');
    await user.type(confirm(), 'short');
    await user.click(submit());

    expect(
      await screen.findByText('New password must be at least 8 characters'),
    ).toBeInTheDocument();
    // Field-level aria-invalid must flip on the offending input
    expect(next()).toHaveAttribute('aria-invalid', 'true');
  });

  it('rejects when new and confirm passwords do not match', async () => {
    const { user, current, next, confirm, submit } = setup();
    await user.type(current(), 'oldpass12');
    await user.type(next(), 'newpass12');
    await user.type(confirm(), 'different12');
    await user.click(submit());

    expect(await screen.findByText('Passwords do not match')).toBeInTheDocument();
    expect(confirm()).toHaveAttribute('aria-invalid', 'true');
  });

  it('rejects when new password equals current password', async () => {
    const { user, current, next, confirm, submit } = setup();
    await user.type(current(), 'samepass1');
    await user.type(next(), 'samepass1');
    await user.type(confirm(), 'samepass1');
    await user.click(submit());

    expect(
      await screen.findByText('New password must be different from current password'),
    ).toBeInTheDocument();
  });

  it('preserves entered values across a failed validation', async () => {
    const { user, current, next, confirm, submit } = setup();
    await user.type(current(), 'oldpass12');
    await user.type(next(), 'short');
    await user.type(confirm(), 'short');
    await user.click(submit());

    expect(
      await screen.findByText('New password must be at least 8 characters'),
    ).toBeInTheDocument();
    expect(current().value).toBe('oldpass12');
    expect(next().value).toBe('short');
    expect(confirm().value).toBe('short');
  });

  it('calls the API and closes the dialog on success', async () => {
    let captured: { current_password?: string; new_password?: string } = {};
    server.use(
      http.post('*/api/auth/change-password', async ({ request }) => {
        captured = (await request.json()) as typeof captured;
        return HttpResponse.json({ success: true, message: 'Password changed' });
      }),
    );

    const { user, current, next, confirm, submit, onOpenChange } = setup();
    await user.type(current(), 'oldpass12');
    await user.type(next(), 'newpass34');
    await user.type(confirm(), 'newpass34');
    await user.click(submit());

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(captured.current_password).toBe('oldpass12');
    expect(captured.new_password).toBe('newpass34');
  });

  it('shows the server error and keeps the dialog open on API failure', async () => {
    server.use(
      http.post('*/api/auth/change-password', () =>
        HttpResponse.json(
          { error: { message: 'Current password is incorrect' } },
          { status: 400 },
        ),
      ),
    );

    const { user, current, next, confirm, submit, onOpenChange } = setup();
    await user.type(current(), 'wrongpass1');
    await user.type(next(), 'newpass34');
    await user.type(confirm(), 'newpass34');
    await user.click(submit());

    expect(
      await screen.findByText('Current password is incorrect'),
    ).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});
