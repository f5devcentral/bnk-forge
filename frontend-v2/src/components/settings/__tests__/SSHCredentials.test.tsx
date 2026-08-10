/**
 * Tests for SSHCredentials settings component
 *
 * Tests loading, empty, list rendering, create (auto-setup + manual key),
 * edit, delete guard, delete confirmation, and test connection flows.
 *
 * MSW handlers return realistic response shapes matching the backend
 * SSHCredentialResponse Pydantic model (routes/ssh_credentials.py).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import SSHCredentials from '../SSHCredentials';

// ---------------------------------------------------------------------------
// Fixtures — match SSHCredentialResponse Pydantic model exactly
// (routes/ssh_credentials.py L59-84, service serialize() L41-59)
// ---------------------------------------------------------------------------

const mockCredentials = [
  {
    id: 1,
    name: 'Production Bastion',
    description: 'Main SSH bastion host',
    host: '10.176.11.91',
    port: 22,
    username: 'ubuntu',
    auth_type: 'key',
    has_password: false,
    has_private_key: true,
    is_default: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-15T00:00:00Z',
    created_by: 'admin',
    clusters_count: 2,
    projects_count: 1,
  },
  {
    id: 2,
    name: 'Staging Jump Host',
    description: null,
    host: '10.176.11.92',
    port: 2222,
    username: 'deploy',
    auth_type: 'password',
    has_password: true,
    has_private_key: false,
    is_default: false,
    created_at: '2026-02-01T00:00:00Z',
    updated_at: '2026-02-10T00:00:00Z',
    created_by: null,
    clusters_count: 0,
    projects_count: 0,
  },
];

const mockCreatedCredential = {
  id: 3,
  name: 'New Connection',
  description: null,
  host: '192.168.1.100',
  port: 22,
  username: 'root',
  auth_type: 'key',
  has_password: false,
  has_private_key: true,
  is_default: false,
  created_at: '2026-03-01T00:00:00Z',
  updated_at: '2026-03-01T00:00:00Z',
  created_by: null,
  clusters_count: 0,
  projects_count: 0,
};

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();

  server.use(
    http.get('*/api/ssh-credentials', () => {
      return HttpResponse.json(mockCredentials);
    }),
  );
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('SSHCredentials', () => {
  // ─── Loading State ──────────────────────────────────────────────────

  describe('loading state', () => {
    it('shows loading spinner while fetching credentials', () => {
      server.use(
        http.get('*/api/ssh-credentials', async () => {
          await new Promise((r) => setTimeout(r, 10000));
          return HttpResponse.json([]);
        }),
      );

      render(<SSHCredentials />);
      expect(document.querySelector('.animate-spin')).toBeInTheDocument();
    });
  });

  // ─── Empty State ────────────────────────────────────────────────────

  describe('empty state', () => {
    it('shows empty message when no credentials exist', async () => {
      server.use(
        http.get('*/api/ssh-credentials', () => {
          return HttpResponse.json([]);
        }),
      );

      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('No SSH connections configured')).toBeInTheDocument();
      });
    });

    it('shows add button even when empty', async () => {
      server.use(
        http.get('*/api/ssh-credentials', () => {
          return HttpResponse.json([]);
        }),
      );

      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Add SSH Connection')).toBeInTheDocument();
      });
    });
  });

  // ─── Credential List ───────────────────────────────────────────────

  describe('credential list', () => {
    it('renders credential names', async () => {
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Production Bastion')).toBeInTheDocument();
        expect(screen.getByText('Staging Jump Host')).toBeInTheDocument();
      });
    });

    it('shows Default badge on default credential', async () => {
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Default')).toBeInTheDocument();
      });
    });

    it('shows SSH Key badge for key-auth credential', async () => {
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('SSH Key')).toBeInTheDocument();
      });
    });

    it('shows Password badge for password-auth credential', async () => {
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Password')).toBeInTheDocument();
      });
    });

    it('shows host connection string (username@host:port)', async () => {
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('ubuntu@10.176.11.91:22')).toBeInTheDocument();
        expect(screen.getByText('deploy@10.176.11.92:2222')).toBeInTheDocument();
      });
    });

    it('shows credential description', async () => {
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Main SSH bastion host')).toBeInTheDocument();
      });
    });

    it('shows usage counts for in-use credentials', async () => {
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Used by 2 cluster(s)')).toBeInTheDocument();
        expect(screen.getByText('Used by 1 project(s)')).toBeInTheDocument();
      });
    });

    it('shows Key Configured status for key-auth credential', async () => {
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Key Configured')).toBeInTheDocument();
      });
    });

    it('shows Password Configured status for password-auth credential', async () => {
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Password Configured')).toBeInTheDocument();
      });
    });
  });

  // ─── Card Header ───────────────────────────────────────────────────

  describe('card header', () => {
    it('renders SSH Credentials title', async () => {
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('SSH connections')).toBeInTheDocument();
      });
    });

    it('renders description text', async () => {
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText(/SSH connections for on-premises/)).toBeInTheDocument();
      });
    });
  });

  // ─── Create Dialog — Auto-Setup Mode ──────────────────────────────

  describe('create dialog — auto-setup mode', () => {
    it('opens dialog when "Add SSH Connection" clicked', async () => {
      const user = userEvent.setup();
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Add SSH Connection')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Add SSH Connection'));

      await waitFor(() => {
        // Dialog opens with description text
        expect(screen.getByText(/Enter the host details and password/)).toBeInTheDocument();
      });
    });

    it('shows password field in auto-setup mode (default)', async () => {
      const user = userEvent.setup();
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Add SSH Connection')).toBeInTheDocument();
      });
      await user.click(screen.getByText('Add SSH Connection'));

      await waitFor(() => {
        expect(screen.getByLabelText('SSH Password *')).toBeInTheDocument();
      });
    });

    it('shows "Connect & Set Up Key" button in auto-setup mode', async () => {
      const user = userEvent.setup();
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Add SSH Connection')).toBeInTheDocument();
      });
      await user.click(screen.getByText('Add SSH Connection'));

      await waitFor(() => {
        expect(screen.getByText('Connect & Set Up Key')).toBeInTheDocument();
      });
    });

    it('submits auto-setup with correct payload shape', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.post('*/api/ssh-credentials/setup', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json(mockCreatedCredential);
        }),
      );

      const user = userEvent.setup();
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Add SSH Connection')).toBeInTheDocument();
      });
      await user.click(screen.getByText('Add SSH Connection'));

      // Fill form
      await user.type(screen.getByLabelText('Connection Name *'), 'New Connection');
      await user.type(screen.getByLabelText('SSH Host *'), '192.168.1.100');
      await user.type(screen.getByLabelText('SSH Username *'), 'root');
      await user.type(screen.getByLabelText('SSH Password *'), 'secret123');

      await user.click(screen.getByText('Connect & Set Up Key'));

      await waitFor(() => {
        expect(capturedBody).not.toBeNull();
      });

      // Verify payload matches SSHCredentialSetup Pydantic model
      expect(capturedBody).toMatchObject({
        name: 'New Connection',
        host: '192.168.1.100',
        username: 'root',
        password: 'secret123',
        port: 22,
        is_default: false,
      });
    });
  });

  // ─── Create Dialog — Manual Key Mode ──────────────────────────────

  describe('create dialog — manual key mode', () => {
    it('shows private key field after clicking "I already have an SSH key"', async () => {
      const user = userEvent.setup();
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Add SSH Connection')).toBeInTheDocument();
      });
      await user.click(screen.getByText('Add SSH Connection'));

      await user.click(screen.getByText('I already have an SSH key'));

      await waitFor(() => {
        expect(screen.getByLabelText('SSH Private Key *')).toBeInTheDocument();
      });
    });

    it('shows "Add Connection" button in manual key mode', async () => {
      const user = userEvent.setup();
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Add SSH Connection')).toBeInTheDocument();
      });
      await user.click(screen.getByText('Add SSH Connection'));
      await user.click(screen.getByText('I already have an SSH key'));

      await waitFor(() => {
        expect(screen.getByText('Add Connection')).toBeInTheDocument();
      });
    });

    it('submits manual create with correct payload shape', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.post('*/api/ssh-credentials', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json(mockCreatedCredential);
        }),
      );

      const user = userEvent.setup();
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Add SSH Connection')).toBeInTheDocument();
      });
      await user.click(screen.getByText('Add SSH Connection'));
      await user.click(screen.getByText('I already have an SSH key'));

      // Fill form
      await user.type(screen.getByLabelText('Connection Name *'), 'New Connection');
      await user.type(screen.getByLabelText('SSH Host *'), '192.168.1.100');
      await user.type(screen.getByLabelText('SSH Username *'), 'root');
      await user.type(screen.getByLabelText('SSH Private Key *'), '-----BEGIN OPENSSH PRIVATE KEY-----\ntest\n-----END OPENSSH PRIVATE KEY-----');

      await user.click(screen.getByText('Add Connection'));

      await waitFor(() => {
        expect(capturedBody).not.toBeNull();
      });

      // Verify payload matches SSHCredentialCreate Pydantic model
      expect(capturedBody).toMatchObject({
        name: 'New Connection',
        host: '192.168.1.100',
        username: 'root',
        auth_type: 'key',
        is_default: false,
      });
      expect(capturedBody).toHaveProperty('private_key');
    });
  });

  // ─── Delete ─────────────────────────────────────────────────────────

  describe('delete', () => {
    async function openActionsForRow(rowText: string) {
      const user = userEvent.setup();
      const row = screen.getByText(rowText).closest('tr');
      expect(row).not.toBeNull();
      const actionsBtn = row!.querySelector(
        'button[aria-label="SSH connection actions"]',
      ) as HTMLButtonElement | null;
      expect(actionsBtn).not.toBeNull();
      await user.click(actionsBtn!);
    }

    it('disables delete action for in-use credentials', async () => {
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Production Bastion')).toBeInTheDocument();
      });

      // The first credential (Production Bastion) has clusters_count=2, projects_count=1
      // → its Delete menu item should be disabled.
      await openActionsForRow('Production Bastion');
      const deleteItem = await screen.findByRole('menuitem', { name: /^delete$/i });
      expect(deleteItem).toHaveAttribute('aria-disabled', 'true');
    });

    it('enables delete action for unused credentials', async () => {
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Staging Jump Host')).toBeInTheDocument();
      });

      // The second credential (Staging Jump Host) has clusters_count=0, projects_count=0.
      await openActionsForRow('Staging Jump Host');
      const deleteItem = await screen.findByRole('menuitem', { name: /^delete$/i });
      expect(deleteItem).not.toHaveAttribute('aria-disabled', 'true');
    });
  });

  // ─── Test Connection ───────────────────────────────────────────────

  describe('test connection', () => {
    it('calls per-credential test endpoint via the row actions menu', async () => {
      let testCalled = false;
      server.use(
        // testAllMutation fires on mount — mock it so the page can settle.
        http.post('*/api/ssh-credentials/test-all', () =>
          HttpResponse.json({ tested: 0, ok: 0, failed: 0 }),
        ),
        http.post('*/api/ssh-credentials/1/test', () => {
          testCalled = true;
          return HttpResponse.json({
            success: true,
            message: 'SSH connection successful to 10.176.11.91:22',
            ssh_banner: 'SSH-2.0-OpenSSH_8.9',
            hostname: 'bastion-01',
          });
        }),
      );

      const user = userEvent.setup();
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Production Bastion')).toBeInTheDocument();
      });

      // Rows now expose actions via a per-row MoreVertical dropdown.
      // First row corresponds to "Production Bastion".
      const actionsButtons = screen.getAllByRole('button', {
        name: /SSH connection actions/i,
      });
      expect(actionsButtons.length).toBeGreaterThan(0);
      await user.click(actionsButtons[0]);

      const testItem = await screen.findByRole('menuitem', {
        name: /test connection/i,
      });
      await user.click(testItem);

      await waitFor(() => {
        expect(testCalled).toBe(true);
      });
    });
  });

  // ─── Edit Dialog ───────────────────────────────────────────────────

  describe('edit dialog', () => {
    /**
     * Open the row actions dropdown for the row containing `rowText`
     * and click the "Edit" menu item. Rows are now table-based and use
     * the per-row MoreVertical dropdown introduced in the F5 reskin.
     */
    async function openEditFromRow(rowText: string) {
      const user = userEvent.setup();
      const row = screen.getByText(rowText).closest('tr');
      expect(row).not.toBeNull();
      const actionsBtn = row!.querySelector(
        'button[aria-label="SSH connection actions"]',
      ) as HTMLButtonElement | null;
      expect(actionsBtn).not.toBeNull();
      await user.click(actionsBtn!);
      const editItem = await screen.findByRole('menuitem', { name: /^edit$/i });
      await user.click(editItem);
      return user;
    }

    it('opens edit dialog with pre-filled data', async () => {
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Staging Jump Host')).toBeInTheDocument();
      });

      await openEditFromRow('Staging Jump Host');

      await waitFor(() => {
        expect(screen.getByText('Edit SSH Connection')).toBeInTheDocument();
      });

      // Check pre-filled values
      expect(screen.getByLabelText('Connection Name *')).toHaveValue('Staging Jump Host');
      expect(screen.getByLabelText('SSH Host *')).toHaveValue('10.176.11.92');
      expect(screen.getByLabelText('SSH Username *')).toHaveValue('deploy');
    });

    it('shows "Update Connection" button in edit mode', async () => {
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Staging Jump Host')).toBeInTheDocument();
      });

      await openEditFromRow('Staging Jump Host');

      await waitFor(() => {
        expect(screen.getByText('Update Connection')).toBeInTheDocument();
      });
    });

    it('submits update with correct payload shape', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.put('*/api/ssh-credentials/2', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ ...mockCredentials[1], name: 'Updated Name' });
        }),
      );

      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Staging Jump Host')).toBeInTheDocument();
      });

      const user = await openEditFromRow('Staging Jump Host');

      await waitFor(() => {
        expect(screen.getByText('Edit SSH Connection')).toBeInTheDocument();
      });

      // Change the name
      const nameInput = screen.getByLabelText('Connection Name *');
      await user.clear(nameInput);
      await user.type(nameInput, 'Updated Name');

      await user.click(screen.getByText('Update Connection'));

      await waitFor(() => {
        expect(capturedBody).not.toBeNull();
      });

      // Verify payload matches SSHCredentialUpdate Pydantic model
      expect(capturedBody).toMatchObject({
        name: 'Updated Name',
        host: '10.176.11.92',
        username: 'deploy',
        auth_type: 'password',
      });
      // private_key should NOT be in payload when left blank
      expect(capturedBody).not.toHaveProperty('private_key');
    });
  });

  // ─── Password Auth Denied ──────────────────────────────────────────

  describe('password auth denied', () => {
    it('flips to manual key mode and shows warning when password auth denied', async () => {
      server.use(
        http.post('*/api/ssh-credentials/setup', () => {
          return HttpResponse.json(
            {
              error: {
                code: 'BAD_REQUEST',
                message: 'Password auth not allowed',
                details: {
                  error_code: 'password_auth_denied',
                  error_detail: 'Server does not allow password authentication',
                },
                path: '/api/ssh-credentials/setup',
              },
            },
            { status: 400 },
          );
        }),
      );

      const user = userEvent.setup();
      render(<SSHCredentials />);

      await waitFor(() => {
        expect(screen.getByText('Add SSH Connection')).toBeInTheDocument();
      });
      await user.click(screen.getByText('Add SSH Connection'));

      // Fill and submit auto-setup form
      await user.type(screen.getByLabelText('Connection Name *'), 'Test');
      await user.type(screen.getByLabelText('SSH Host *'), '10.0.0.1');
      await user.type(screen.getByLabelText('SSH Username *'), 'root');
      await user.type(screen.getByLabelText('SSH Password *'), 'pass');

      await user.click(screen.getByText('Connect & Set Up Key'));

      // Should flip to manual key mode with warning
      await waitFor(() => {
        expect(screen.getByText('Password authentication not supported')).toBeInTheDocument();
        expect(screen.getByLabelText('SSH Private Key *')).toBeInTheDocument();
      });
    });
  });
});
