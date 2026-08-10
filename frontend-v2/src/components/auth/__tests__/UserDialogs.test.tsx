/**
 * Validation tests for the Create / Edit user dialogs in UserManagement.tsx.
 *
 * The dialogs are not directly exported, so this suite exercises them by
 * navigating to the User Management page and opening the dialogs through
 * the UI. Coverage focuses on the Zod-driven validation rules added in the
 * RHF migration; broader page tests live alongside UserManagement.
 */
import { describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import UserManagement from '@/pages/UserManagement';
import { useAuthStore } from '@/stores/authStore';

const mockAdmin = {
  id: 1,
  username: 'admin',
  email: 'admin@example.com',
  role: 'admin' as const,
  is_active: true,
};

function asAdmin() {
  useAuthStore.setState({
    user: mockAdmin,
    accessToken: 'test-token',
    isAuthenticated: true,
  });
}

describe('CreateUserDialog (RHF + Zod)', () => {
  it('blocks submission with field-level validation messages', async () => {
    asAdmin();
    server.use(
      http.get('*/api/auth/users', () => HttpResponse.json({ users: [], total: 0 })),
    );

    const user = userEvent.setup();
    render(<UserManagement />);

    await user.click(await screen.findByRole('button', { name: /add user/i }));

    // Submitting an empty form surfaces required-field messages
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: /create user/i }),
    );

    expect(await screen.findByText('Username is required')).toBeInTheDocument();
    expect(screen.getByText('Email is required')).toBeInTheDocument();
    expect(screen.getByText('Password must be at least 8 characters')).toBeInTheDocument();
  });

  it('rejects an invalid email address', async () => {
    asAdmin();
    server.use(
      http.get('*/api/auth/users', () => HttpResponse.json({ users: [], total: 0 })),
    );

    const user = userEvent.setup();
    render(<UserManagement />);
    await user.click(await screen.findByRole('button', { name: /add user/i }));

    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Username'), 'jsmith');
    await user.type(within(dialog).getByLabelText('Email'), 'not-an-email');
    await user.type(within(dialog).getByLabelText('Password'), 'longenough');
    await user.click(within(dialog).getByRole('button', { name: /create user/i }));

    expect(await screen.findByText('Enter a valid email address')).toBeInTheDocument();
  });

  it('rejects username with invalid characters', async () => {
    asAdmin();
    server.use(
      http.get('*/api/auth/users', () => HttpResponse.json({ users: [], total: 0 })),
    );

    const user = userEvent.setup();
    render(<UserManagement />);
    await user.click(await screen.findByRole('button', { name: /add user/i }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Username'), 'has spaces');
    await user.type(within(dialog).getByLabelText('Email'), 'a@b.co');
    await user.type(within(dialog).getByLabelText('Password'), 'longenough');
    await user.click(within(dialog).getByRole('button', { name: /create user/i }));

    expect(
      await screen.findByText(/letters, numbers, dots, underscores, or hyphens/i),
    ).toBeInTheDocument();
  });

  it('submits valid input to POST /api/auth/users', async () => {
    asAdmin();
    let captured: Record<string, unknown> | null = null;
    server.use(
      http.get('*/api/auth/users', () => HttpResponse.json({ users: [], total: 0 })),
      http.post('*/api/auth/users', async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          id: 99,
          username: captured.username,
          email: captured.email,
          role: captured.role,
          is_active: true,
        });
      }),
    );

    const user = userEvent.setup();
    render(<UserManagement />);
    await user.click(await screen.findByRole('button', { name: /add user/i }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Username'), 'jsmith');
    await user.type(within(dialog).getByLabelText('Email'), 'jsmith@x.io');
    await user.type(within(dialog).getByLabelText('Password'), 'longenough');
    await user.click(within(dialog).getByRole('button', { name: /create user/i }));

    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured!.username).toBe('jsmith');
    expect(captured!.email).toBe('jsmith@x.io');
    expect(captured!.role).toBe('operator');
  });
});

// Helper imported lazily so we can use within() without polluting top-level imports.
import { within } from '@testing-library/react';
