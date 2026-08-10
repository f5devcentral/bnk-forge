/**
 * BnkReleasesPanel — kind-aware dialog field tests (ADR-494).
 *
 * (a) Switching kind to OCI auto-fills url with 'repo.f5.com' when url was blank.
 * (b) Switching kind to manual hides both URL and credential fields.
 */
import { beforeAll, describe, expect, it } from 'vitest';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';

import BnkReleasesPanel from '@/components/catalog/BnkReleasesPanel';
import { server } from '@/test/mocks/server';
import { fireEvent, render, screen, waitFor } from '@/test/test-utils';

vi.mock('@/hooks/useRole', () => ({
  useRole: () => ({
    isAdmin: true,
    isOperator: true,
    isViewer: true,
    role: 'admin' as const,
    hasRole: () => true,
    hasMinRole: () => true,
  }),
}));

// Radix UI Select triggers pointer events that rely on pointer capture APIs which
// jsdom does not implement. Stub them so click interactions open the dropdown.
beforeAll(() => {
  window.HTMLElement.prototype.hasPointerCapture = () => false;
  window.HTMLElement.prototype.setPointerCapture = () => {};
  window.HTMLElement.prototype.releasePointerCapture = () => {};
});

/** Register handlers so both panel sections render without errors. */
function usePanelHandlers() {
  server.use(
    http.get('*/api/bare-metal/release-sources', () => HttpResponse.json([])),
    http.get('*/api/bare-metal/deployable-releases', () => HttpResponse.json([])),
  );
}

describe('BnkReleasesPanel — Add Release Source dialog, kind-aware fields', () => {
  it('(b) opens with kind=manual and hides URL and credential fields', async () => {
    usePanelHandlers();
    render(<BnkReleasesPanel />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /add source/i })).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole('button', { name: /add source/i }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    // URL and credential must NOT be rendered for manual kind
    expect(screen.queryByLabelText(/^url$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/service-account key/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/pull-secret \/ token/i)).not.toBeInTheDocument();
  });

  it('(a) switching kind to OCI auto-fills repo.f5.com and shows OCI credential label', async () => {
    usePanelHandlers();
    render(<BnkReleasesPanel />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /add source/i })).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /add source/i }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();

    // Open the Kind select and choose OCI registry
    await user.click(screen.getByRole('combobox'));
    await user.click(screen.getByRole('option', { name: /oci registry/i }));

    // URL field must now be visible and auto-filled with OCI_DEFAULT_URL
    await waitFor(() => {
      expect(screen.getByDisplayValue('repo.f5.com')).toBeInTheDocument();
    });

    // Credential label must reflect OCI kind
    expect(screen.getByText('Service-account key (base64)')).toBeInTheDocument();
  });

  it('switching kind to mirror shows mirror-specific placeholder and credential label', async () => {
    usePanelHandlers();
    render(<BnkReleasesPanel />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /add source/i })).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /add source/i }));

    await user.click(screen.getByRole('combobox'));
    await user.click(screen.getByRole('option', { name: /mirror \/ proxy/i }));

    await waitFor(() => {
      expect(screen.getByPlaceholderText('https://internal-mirror.example.com')).toBeInTheDocument();
    });

    expect(screen.getByText('Pull-secret / token')).toBeInTheDocument();
  });

  it('switching from OCI back to manual hides URL and credential again', async () => {
    usePanelHandlers();
    render(<BnkReleasesPanel />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /add source/i })).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /add source/i }));

    // Switch to OCI first
    await user.click(screen.getByRole('combobox'));
    await user.click(screen.getByRole('option', { name: /oci registry/i }));

    await waitFor(() => {
      expect(screen.getByDisplayValue('repo.f5.com')).toBeInTheDocument();
    });

    // Switch back to manual
    await user.click(screen.getByRole('combobox'));
    await user.click(screen.getByRole('option', { name: /manual \(paste yaml\)/i }));

    await waitFor(() => {
      expect(screen.queryByLabelText(/^url$/i)).not.toBeInTheDocument();
    });
    expect(screen.queryByText(/service-account key/i)).not.toBeInTheDocument();
  });

  it('does not overwrite a user-typed URL when switching to OCI', async () => {
    usePanelHandlers();
    render(<BnkReleasesPanel />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /add source/i })).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /add source/i }));

    // First switch to mirror so the URL field is visible
    await user.click(screen.getByRole('combobox'));
    await user.click(screen.getByRole('option', { name: /mirror \/ proxy/i }));

    await waitFor(() => {
      expect(screen.getByPlaceholderText('https://internal-mirror.example.com')).toBeInTheDocument();
    });

    // Type a custom URL
    const urlInput = screen.getByLabelText(/^url$/i);
    await user.clear(urlInput);
    await user.type(urlInput, 'https://my-custom-registry.example.com');

    // Switch to OCI — existing non-blank URL must NOT be overwritten
    await user.click(screen.getByRole('combobox'));
    await user.click(screen.getByRole('option', { name: /oci registry/i }));

    await waitFor(() => {
      expect(screen.getByDisplayValue('https://my-custom-registry.example.com')).toBeInTheDocument();
    });
  });

  it('(cred-file-a) loading a raw JSON file base64-encodes it and shows SA-key hint', async () => {
    usePanelHandlers();
    render(<BnkReleasesPanel />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /add source/i })).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /add source/i }));

    // Switch to OCI to reveal the credential field
    await user.click(screen.getByRole('combobox'));
    await user.click(screen.getByRole('option', { name: /oci registry/i }));

    await waitFor(() => {
      expect(screen.getByText('Service-account key (base64)')).toBeInTheDocument();
    });

    const saKeyContent = JSON.stringify({ type: 'service_account', project_id: 'my-project' });
    const file = new File([saKeyContent], 'sa-key.json', { type: 'application/json' });

    const credFileInput = document.querySelector(
      'input[accept=".json,.b64,.txt,text/plain,application/json"]',
    ) as HTMLInputElement;
    expect(credFileInput).not.toBeNull();

    fireEvent.change(credFileInput, { target: { files: [file] } });

    // Hint must confirm SA-key JSON branch fired
    await waitFor(() => {
      expect(screen.getByText(/Loaded sa-key\.json \(detected: SA-key JSON → base64\)/)).toBeInTheDocument();
    });

    // Credential input must hold the base64-encoded content
    const credInput = screen.getByLabelText(/service-account key/i) as HTMLInputElement;
    expect(credInput.value).toBe(btoa(saKeyContent));
  });

  it('(cred-file-b) loading a non-JSON file stores it verbatim and shows stored-as-is hint', async () => {
    usePanelHandlers();
    render(<BnkReleasesPanel />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /add source/i })).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /add source/i }));

    // Switch to OCI to reveal the credential field
    await user.click(screen.getByRole('combobox'));
    await user.click(screen.getByRole('option', { name: /oci registry/i }));

    await waitFor(() => {
      expect(screen.getByText('Service-account key (base64)')).toBeInTheDocument();
    });

    const tokenContent = 'dGhpcyBpcyBhIHRva2Vu'; // not valid JSON
    const file = new File([tokenContent], 'token.b64', { type: 'text/plain' });

    const credFileInput = document.querySelector(
      'input[accept=".json,.b64,.txt,text/plain,application/json"]',
    ) as HTMLInputElement;
    expect(credFileInput).not.toBeNull();

    fireEvent.change(credFileInput, { target: { files: [file] } });

    // Hint must confirm stored-as-is branch fired
    await waitFor(() => {
      expect(screen.getByText(/Loaded token\.b64 \(stored as-is\)/)).toBeInTheDocument();
    });

    // Credential input must hold the verbatim trimmed content
    const credInput = screen.getByLabelText(/service-account key/i) as HTMLInputElement;
    expect(credInput.value).toBe(tokenContent.trim());
  });
});

describe('BnkReleasesPanel — TagPicker, fetch → list → add flow', () => {
  it('fetches tags on button click, pre-selects non-catalog non-prerelease, adds via pull API', async () => {
    const tagListResponse = {
      tags: [
        { tag: '2.3.1-3.2598.3-0.0.304', in_catalog: false, prerelease: false },
        { tag: '2.2.1-3.2226.0-0.0.511', in_catalog: true, prerelease: false },
        { tag: '2.4.0-laiq', in_catalog: false, prerelease: true },
      ],
      list_error: null,
    };
    let capturedPullBody: { tags: string[] } | null = null;

    server.use(
      http.get('*/api/bare-metal/deployable-releases', () => HttpResponse.json([])),
      http.get('*/api/bare-metal/release-sources/:id/tags', () =>
        HttpResponse.json(tagListResponse),
      ),
      http.post('*/api/bare-metal/release-sources/:id/tags\\:pull', async ({ request }) => {
        capturedPullBody = (await request.json()) as { tags: string[] };
        return HttpResponse.json({ added: ['2.3.1-3.2598.3-0.0.304'], skipped: [], failed: [] });
      }),
    );

    render(<BnkReleasesPanel />);

    await waitFor(() => {
      expect(screen.getByTitle('Sync')).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByTitle('Sync'));

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    // Before fetch: no tags listed yet
    expect(screen.queryByText('2.3.1-3.2598.3-0.0.304')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /fetch tags/i }));

    // After fetch: all tags visible
    await waitFor(() => {
      expect(screen.getByText('2.3.1-3.2598.3-0.0.304')).toBeInTheDocument();
      expect(screen.getByText('2.2.1-3.2226.0-0.0.511')).toBeInTheDocument();
      expect(screen.getByText('2.4.0-laiq')).toBeInTheDocument();
    });

    // "Add selected" button should be enabled (non-catalog non-prerelease tag is pre-selected)
    const addBtn = screen.getByRole('button', { name: /add selected/i });
    expect(addBtn).not.toBeDisabled();

    await user.click(addBtn);

    await waitFor(() => {
      expect(capturedPullBody).not.toBeNull();
    });

    // Only the non-catalog, non-prerelease tag should be sent
    expect(capturedPullBody!.tags).toContain('2.3.1-3.2598.3-0.0.304');
    expect(capturedPullBody!.tags).not.toContain('2.2.1-3.2226.0-0.0.511'); // in_catalog
    expect(capturedPullBody!.tags).not.toContain('2.4.0-laiq'); // prerelease
  });
});

describe('BnkReleasesPanel — edit source, credential omission (ADR-494 review fix)', () => {
  const ociSourceWithCred = {
    id: 1,
    name: 'repo.f5.com',
    kind: 'oci',
    url: 'oci://repo.f5.com/release/f5-bigip-k8s-manifest',
    has_credential: true,
    is_active: true,
    auto_sync: false,
    sync_interval_hours: null,
    last_synced_at: null,
    sync_status: 'idle',
    sync_error: null,
    release_count: 0,
    description: null,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  };

  it('editing oci source with blank credential omits the credential key from the update payload', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.get('*/api/bare-metal/release-sources', () => HttpResponse.json([ociSourceWithCred])),
      http.get('*/api/bare-metal/deployable-releases', () => HttpResponse.json([])),
      http.patch('*/api/bare-metal/release-sources/:id', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(ociSourceWithCred);
      }),
    );

    render(<BnkReleasesPanel />);

    await waitFor(() => {
      expect(screen.getByTitle('Edit')).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByTitle('Edit'));

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    // Submit without typing a credential — the credential field is left blank.
    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // The credential key must be absent so the backend's exclude_unset path
    // keeps the existing encrypted credential intact.
    expect('credential' in capturedBody!).toBe(false);
  });

  it('editing oci source with a typed credential includes the credential key in the update payload', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.get('*/api/bare-metal/release-sources', () => HttpResponse.json([ociSourceWithCred])),
      http.get('*/api/bare-metal/deployable-releases', () => HttpResponse.json([])),
      http.patch('*/api/bare-metal/release-sources/:id', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(ociSourceWithCred);
      }),
    );

    render(<BnkReleasesPanel />);

    await waitFor(() => {
      expect(screen.getByTitle('Edit')).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByTitle('Edit'));

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    // Type a new credential value.
    const credInput = screen.getByLabelText(/service-account key/i);
    await user.type(credInput, 'new-sa-key-b64');

    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    expect('credential' in capturedBody!).toBe(true);
    expect(capturedBody!.credential).toBe('new-sa-key-b64');
  });
});

describe('BnkReleasesPanel — SyncDialog, kind-aware flow', () => {
  it('oci sync dialog renders TagPicker directly and hides YAML paste and Sync YAML button', async () => {
    // Global handlers.ts already returns an OCI source; add the missing deployable-releases handler.
    server.use(
      http.get('*/api/bare-metal/deployable-releases', () => HttpResponse.json([])),
    );
    render(<BnkReleasesPanel />);

    // Wait for the source row to render and the per-row Sync button to appear.
    await waitFor(() => {
      expect(screen.getByTitle('Sync')).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByTitle('Sync'));

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    // TagPicker must be rendered — its "Fetch tags" button is the primary entry point.
    expect(screen.getByRole('button', { name: /fetch tags/i })).toBeInTheDocument();
    // Manifest YAML textarea must NOT be present for oci sources.
    expect(screen.queryByPlaceholderText('Paste manifest YAML here...')).not.toBeInTheDocument();
    // "Sync YAML" footer button must NOT be present for oci sources.
    expect(screen.queryByRole('button', { name: /sync yaml/i })).not.toBeInTheDocument();
  });

  it('manual sync dialog shows YAML paste area and Sync YAML button, not TagPicker', async () => {
    server.use(
      http.get('*/api/bare-metal/release-sources', () =>
        HttpResponse.json([
          {
            id: 2,
            name: 'Offline Manifest',
            kind: 'manual',
            url: null,
            has_credential: false,
            is_active: true,
            auto_sync: false,
            sync_interval_hours: null,
            last_synced_at: null,
            sync_status: 'idle',
            sync_error: null,
            release_count: 0,
            description: null,
            created_at: '2024-01-01T00:00:00Z',
            updated_at: '2024-01-01T00:00:00Z',
          },
        ]),
      ),
      http.get('*/api/bare-metal/deployable-releases', () => HttpResponse.json([])),
    );
    render(<BnkReleasesPanel />);

    await waitFor(() => {
      expect(screen.getByTitle('Sync')).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByTitle('Sync'));

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    // YAML paste textarea must be present for manual sources.
    expect(screen.getByPlaceholderText('Paste manifest YAML here...')).toBeInTheDocument();
    // "Sync YAML" footer button must be present for manual sources.
    expect(screen.getByRole('button', { name: /sync yaml/i })).toBeInTheDocument();
    // TagPicker "Fetch tags" button must NOT be present for manual sources.
    expect(screen.queryByRole('button', { name: /fetch tags/i })).not.toBeInTheDocument();
  });
});
