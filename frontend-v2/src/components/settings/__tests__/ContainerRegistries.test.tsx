/**
 * Tests for the ContainerRegistries settings component.
 *
 * Covers loading, empty, list rendering, create (default ghcr standalone
 * type), delete confirmation, and test-connection flows.
 *
 * CT-012: MSW handlers return the REAL ContainerRegistryResponse shape from
 * backend/routes/container_registries.py (service serialize() in
 * services/container_registry_service.py). The create handler captures
 * request.json() and asserts the payload matches ContainerRegistryCreate.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import ContainerRegistries from '../ContainerRegistries';

// Radix Select relies on pointer-capture + scrollIntoView APIs jsdom lacks.
if (!HTMLElement.prototype.hasPointerCapture) {
  HTMLElement.prototype.hasPointerCapture = () => false;
  HTMLElement.prototype.releasePointerCapture = () => {};
  HTMLElement.prototype.scrollIntoView = () => {};
}

// ---------------------------------------------------------------------------
// Fixtures — match ContainerRegistryResponse exactly
// (routes/container_registries.py L59-77, service serialize() L75-94)
// ---------------------------------------------------------------------------

const mockRegistries = [
  {
    id: 1,
    name: 'GHCR (jgruberf5)',
    description: 'GitHub Container Registry',
    type: 'ghcr',
    registry_host: 'ghcr.io',
    username: 'jgruberf5',
    has_token: true,
    has_far_service_account: false,
    credential_template_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-15T00:00:00Z',
    created_by: 'admin',
    last_test_status: 'ok',
    last_test_at: '2026-01-15T00:00:00Z',
    last_test_message: 'Authenticated to ghcr.io.',
  },
  {
    id: 2,
    name: 'Prod ICR pull',
    description: null,
    type: 'icr',
    registry_host: 'us.icr.io',
    username: null,
    has_token: false,
    has_far_service_account: false,
    credential_template_id: 7,
    created_at: '2026-02-01T00:00:00Z',
    updated_at: '2026-02-10T00:00:00Z',
    created_by: null,
    last_test_status: null,
    last_test_at: null,
    last_test_message: null,
  },
];

const mockCreatedRegistry = {
  id: 3,
  name: 'New Registry',
  description: null,
  type: 'ghcr',
  registry_host: 'ghcr.io',
  username: 'robot',
  has_token: true,
  has_far_service_account: false,
  credential_template_id: null,
  created_at: '2026-03-01T00:00:00Z',
  updated_at: '2026-03-01T00:00:00Z',
  created_by: null,
  last_test_status: null,
  last_test_at: null,
  last_test_message: null,
};

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();

  server.use(
    http.get('*/api/container-registries', () => HttpResponse.json(mockRegistries)),
    http.get('*/api/credential-templates', () => HttpResponse.json([])),
  );
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('ContainerRegistries', () => {
  describe('loading state', () => {
    it('shows loading spinner while fetching registries', () => {
      server.use(
        http.get('*/api/container-registries', async () => {
          await new Promise((r) => setTimeout(r, 10000));
          return HttpResponse.json([]);
        }),
      );

      render(<ContainerRegistries />);
      expect(document.querySelector('.animate-spin')).toBeInTheDocument();
    });
  });

  describe('empty state', () => {
    it('shows empty message when no registries exist', async () => {
      server.use(http.get('*/api/container-registries', () => HttpResponse.json([])));

      render(<ContainerRegistries />);
      await waitFor(() => {
        expect(screen.getByText('No container registries configured')).toBeInTheDocument();
      });
    });
  });

  describe('list rendering', () => {
    it('renders each registry with its host and type label', async () => {
      render(<ContainerRegistries />);

      await waitFor(() => {
        expect(screen.getByText('GHCR (jgruberf5)')).toBeInTheDocument();
      });
      expect(screen.getByText('Prod ICR pull')).toBeInTheDocument();
      expect(screen.getByText('ghcr.io')).toBeInTheDocument();
      expect(screen.getByText('us.icr.io')).toBeInTheDocument();
      // Type labels rendered as badges (the label may also appear in the
      // closed create-dialog's type Select, so allow more than one match).
      expect(screen.getAllByText('GitHub Container Registry').length).toBeGreaterThan(0);
      expect(screen.getAllByText('IBM Cloud ICR').length).toBeGreaterThan(0);
    });
  });

  describe('create flow', () => {
    it('posts a ContainerRegistryCreate payload for a standalone ghcr registry', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.post('*/api/container-registries', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json(mockCreatedRegistry, { status: 201 });
        }),
        // Auto-test fired on create success.
        http.post('*/api/container-registries/3/test', () =>
          HttpResponse.json({
            success: true,
            message: 'Authenticated to ghcr.io.',
            last_test_status: 'ok',
            last_test_at: '2026-03-01T00:00:00Z',
            last_test_message: 'Authenticated to ghcr.io.',
          }),
        ),
      );

      const user = userEvent.setup();
      render(<ContainerRegistries />);

      await waitFor(() => screen.getByText('Add Registry'));
      await user.click(screen.getByText('Add Registry'));

      await user.type(screen.getByLabelText(/Registry Name/i), 'New Registry');
      // Host defaults to ghcr.io for the default ghcr type.
      await user.type(screen.getByLabelText(/Username/i), 'robot');
      await user.type(screen.getByLabelText(/Token \/ Password/i), 'ghp_secrettoken');

      await user.click(screen.getByRole('button', { name: /^Add Registry$/i }));

      await waitFor(() => {
        expect(capturedBody).not.toBeNull();
      });

      expect(capturedBody).toMatchObject({
        name: 'New Registry',
        type: 'ghcr',
        registry_host: 'ghcr.io',
        username: 'robot',
        token: 'ghp_secrettoken',
      });
      // Standalone ghcr must not carry FAR/derived fields.
      expect(capturedBody).toMatchObject({
        far_service_account: null,
        credential_template_id: null,
      });
    });

    it('defaults the Registry Host per type — F5 Artifact Registry is repo.f5.com', async () => {
      const user = userEvent.setup();
      render(<ContainerRegistries />);

      await waitFor(() => screen.getByText('Add Registry'));
      await user.click(screen.getByText('Add Registry'));

      // Host starts at the default ghcr host.
      expect(screen.getByLabelText(/Registry Host/i)).toHaveValue('ghcr.io');

      // Selecting F5 Artifact Registry updates the host to its default.
      await user.click(screen.getByRole('combobox'));
      await user.click(await screen.findByRole('option', { name: 'F5 Artifact Registry' }));

      expect(screen.getByLabelText(/Registry Host/i)).toHaveValue('repo.f5.com');
    });

    it('loads a selected service-account .json into the FAR Auth Key field', async () => {
      const user = userEvent.setup();
      render(<ContainerRegistries />);

      await waitFor(() => screen.getByText('Add Registry'));
      await user.click(screen.getByText('Add Registry'));

      // Switch to FAR so the file dropzone + FAR Auth Key field render.
      await user.click(screen.getByRole('combobox'));
      await user.click(await screen.findByRole('option', { name: 'F5 Artifact Registry' }));

      const json = '{"type":"service_account","project_id":"x"}';
      const file = new File([json], 'sa.json', { type: 'application/json' });
      await user.upload(screen.getByLabelText('Choose file'), file);

      await waitFor(() => {
        expect(screen.getByLabelText(/FAR Auth Key/i)).toHaveValue(json);
      });
    });

    it('leaves the Registry Host blank for self-hostable types (Harbor)', async () => {
      const user = userEvent.setup();
      render(<ContainerRegistries />);

      await waitFor(() => screen.getByText('Add Registry'));
      await user.click(screen.getByText('Add Registry'));
      expect(screen.getByLabelText(/Registry Host/i)).toHaveValue('ghcr.io');

      await user.click(screen.getByRole('combobox'));
      await user.click(await screen.findByRole('option', { name: 'Harbor' }));

      // No canonical host for a self-hosted registry — suggestion cleared.
      expect(screen.getByLabelText(/Registry Host/i)).toHaveValue('');
    });

    it('shows per-type credential help that updates with the selected type', async () => {
      const user = userEvent.setup();
      render(<ContainerRegistries />);

      await waitFor(() => screen.getByText('Add Registry'));
      await user.click(screen.getByText('Add Registry'));

      // Default ghcr → GitHub token page + PAT credential text.
      expect(screen.getByText(/Personal Access Token/i)).toBeInTheDocument();
      const link = screen.getByRole('link', { name: /How to create this credential/i });
      expect(link.getAttribute('href')).toBe('https://github.com/settings/tokens');

      // Derived type → guidance points at a Cloud Credential Template.
      await user.click(screen.getByRole('combobox'));
      await user.click(await screen.findByRole('option', { name: 'Amazon ECR' }));
      // Text unique to the help block (the derived branch also renders a
      // "Cloud Credential Template" field label).
      expect(screen.getByText(/not a registry token/i)).toBeInTheDocument();
      expect(
        screen.getByRole('link', { name: /How to create this credential/i }).getAttribute('href'),
      ).toContain('aws.amazon.com');
    });

    it('posts far_service_account (and no username/token) for a FAR registry', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.post('*/api/container-registries', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ ...mockCreatedRegistry, type: 'far' }, { status: 201 });
        }),
        http.post('*/api/container-registries/3/test', () =>
          HttpResponse.json({ success: true, message: 'ok', last_test_status: 'ok' }),
        ),
      );

      const user = userEvent.setup();
      render(<ContainerRegistries />);

      await waitFor(() => screen.getByText('Add Registry'));
      await user.click(screen.getByText('Add Registry'));

      await user.type(screen.getByLabelText(/Registry Name/i), 'FAR prod');
      await user.click(screen.getByRole('combobox'));
      await user.click(await screen.findByRole('option', { name: 'F5 Artifact Registry' }));

      // paste, not type — userEvent.type reads '{' as its own escape syntax.
      const sa = '{"type":"service_account","project_id":"f5-far"}';
      await user.click(screen.getByLabelText(/FAR Auth Key/i));
      await user.paste(sa);
      await user.click(screen.getByRole('button', { name: /^Add Registry$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody).toMatchObject({
        type: 'far',
        registry_host: 'repo.f5.com',
        far_service_account: sa,
        // FAR authenticates via the service account only.
        username: null,
        token: null,
        credential_template_id: null,
      });
    });

    it('posts credential_template_id (and no standalone secret) for a derived registry', async () => {
      server.use(
        http.get('*/api/credential-templates', () =>
          HttpResponse.json([{ id: 7, name: 'ibm-prod', provider: 'ibm' }]),
        ),
      );
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.post('*/api/container-registries', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ ...mockCreatedRegistry, type: 'icr' }, { status: 201 });
        }),
        http.post('*/api/container-registries/3/test', () =>
          HttpResponse.json({ success: true, message: 'ok', last_test_status: 'ok' }),
        ),
      );

      const user = userEvent.setup();
      render(<ContainerRegistries />);

      await waitFor(() => screen.getByText('Add Registry'));
      await user.click(screen.getByText('Add Registry'));

      await user.type(screen.getByLabelText(/Registry Name/i), 'ICR prod');
      const [typeSelect] = screen.getAllByRole('combobox');
      await user.click(typeSelect);
      await user.click(await screen.findByRole('option', { name: 'IBM Cloud ICR' }));

      // Derived types pick a Cloud Credential Template instead of a secret.
      const selects = screen.getAllByRole('combobox');
      await user.click(selects[selects.length - 1]);
      await user.click(await screen.findByRole('option', { name: /ibm-prod/i }));

      await user.click(screen.getByRole('button', { name: /^Add Registry$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody).toMatchObject({
        type: 'icr',
        credential_template_id: 7,
        // Derived registries never carry their own secret.
        username: null,
        token: null,
        far_service_account: null,
      });
    });
  });

  describe('edit flow', () => {
    it('omits token from the PUT when the field is left blank (write-only credential)', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.put('*/api/container-registries/1', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json(mockRegistries[0]);
        }),
        http.post('*/api/container-registries/1/test', () =>
          HttpResponse.json({ success: true, message: 'ok', last_test_status: 'ok' }),
        ),
      );

      const user = userEvent.setup();
      render(<ContainerRegistries />);

      await waitFor(() => screen.getByText('GHCR (jgruberf5)'));
      await user.click(screen.getAllByLabelText('Container registry actions')[0]);
      await user.click(await screen.findByText('Edit'));

      // The token field opens blank — the stored secret is never sent to the UI.
      expect(screen.getByLabelText(/Token \/ Password/i)).toHaveValue('');

      // Change only a non-secret field and save.
      const description = screen.getByLabelText(/Description/i);
      await user.clear(description);
      await user.type(description, 'rotated description');
      await user.click(screen.getByRole('button', { name: /^Update Registry$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody).toMatchObject({ description: 'rotated description' });
      // Blank token must NOT be sent — otherwise the backend would wipe/overwrite
      // the stored credential on every metadata-only edit.
      expect(capturedBody).not.toHaveProperty('token');
    });

    it('sends the token on the PUT when the user enters a new one', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.put('*/api/container-registries/1', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json(mockRegistries[0]);
        }),
        http.post('*/api/container-registries/1/test', () =>
          HttpResponse.json({ success: true, message: 'ok', last_test_status: 'ok' }),
        ),
      );

      const user = userEvent.setup();
      render(<ContainerRegistries />);

      await waitFor(() => screen.getByText('GHCR (jgruberf5)'));
      await user.click(screen.getAllByLabelText('Container registry actions')[0]);
      await user.click(await screen.findByText('Edit'));

      await user.type(screen.getByLabelText(/Token \/ Password/i), 'ghp_rotated');
      await user.click(screen.getByRole('button', { name: /^Update Registry$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody).toMatchObject({ token: 'ghp_rotated' });
    });
  });

  describe('test connection flow', () => {
    it('calls the test endpoint for a registry', async () => {
      let tested = false;
      server.use(
        http.post('*/api/container-registries/1/test', () => {
          tested = true;
          return HttpResponse.json({
            success: true,
            message: 'Authenticated to ghcr.io.',
            last_test_status: 'ok',
            last_test_at: '2026-03-01T00:00:00Z',
            last_test_message: 'Authenticated to ghcr.io.',
          });
        }),
      );

      const user = userEvent.setup();
      render(<ContainerRegistries />);

      await waitFor(() => screen.getByText('GHCR (jgruberf5)'));
      const actionButtons = screen.getAllByLabelText('Container registry actions');
      await user.click(actionButtons[0]);
      await user.click(await screen.findByText('Test connection'));

      await waitFor(() => expect(tested).toBe(true));
    });
  });

  describe('delete flow', () => {
    it('confirms then deletes a registry', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(true);
      let deleted = false;
      server.use(
        http.delete('*/api/container-registries/1', () => {
          deleted = true;
          return new HttpResponse(null, { status: 204 });
        }),
      );

      const user = userEvent.setup();
      render(<ContainerRegistries />);

      await waitFor(() => screen.getByText('GHCR (jgruberf5)'));
      const actionButtons = screen.getAllByLabelText('Container registry actions');
      await user.click(actionButtons[0]);
      await user.click(await screen.findByText('Delete'));

      await waitFor(() => expect(deleted).toBe(true));
    });
  });
});
