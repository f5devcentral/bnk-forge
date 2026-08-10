import { describe, expect, it } from 'vitest';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';

import BlueprintCatalogPanel from '@/components/catalog/BlueprintCatalogPanel';
import { server } from '@/test/mocks/server';
import { render, screen, waitFor } from '@/test/test-utils';
import { notify } from '@/lib/notify';

vi.mock('@/lib/notify', async () => {
  const actual = await vi.importActual<typeof import('@/lib/notify')>('@/lib/notify');
  return {
    ...actual,
    notify: {
      ...actual.notify,
      success: vi.fn(),
      warning: vi.fn(),
      info: vi.fn(),
      error: vi.fn(),
    },
  };
});

describe('BlueprintCatalogPanel', () => {
  it('allows deleting a registered git blueprint source from the UI', async () => {
    const user = userEvent.setup();
    let deletedSourceId: number | null = null;

    server.use(
      http.get('*/api/blueprint-catalog/sources', () =>
        HttpResponse.json([
          {
            id: 7,
            name: 'IBM Blueprints',
            source_type: 'git',
            url: 'https://github.com/jgruberf5/bnk-forge-ibm-roks-cluster.git',
            branch: 'main',
            git_ref: null,
            sync_status: 'success',
            sync_error: null,
            last_synced_at: new Date().toISOString(),
            release_count: 1,
            is_active: true,
            is_default: false,
            description: 'Blueprint repo',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ])
      ),
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json([
          {
            id: 17,
            blueprint_source_id: 7,
            source_name: 'IBM Blueprints',
            blueprint_id: 'ibm-roks-cluster',
            blueprint_version: '1.0.0',
            blueprint_name: 'IBM ROKS Cluster',
            blueprint_description: 'Provision IBM ROKS cluster resources',
            category: 'infrastructure',
            cloud_provider: 'ibm',
            tags: ['ibm', 'roks'],
            schema_version: 1,
            source_path: 'blueprints/ibm-roks-cluster/forge-blueprint.json',
            source_ref: 'main',
            content_sha256: 'abc123',
            validation_state: 'valid',
            validation_errors: null,
            release_state: 'discovered',
            state_reason: null,
            is_active: true,
            imported_at: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: [],
          },
        ])
      ),
      http.delete('*/api/blueprint-catalog/sources/:id', ({ params }) => {
        deletedSourceId = Number(params.id);
        return HttpResponse.json({ success: true, message: 'Blueprint source deleted' });
      }),
    );

    render(<BlueprintCatalogPanel />);

    await waitFor(() => {
      expect(screen.getByText('IBM Blueprints')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /sources/i }));
    await user.click(screen.getByRole('button', { name: /delete source/i }));

    await waitFor(() => {
      expect(screen.getByText('Delete blueprint source?')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: 'Delete Source' }));

    await waitFor(() => {
      expect(deletedSourceId).toBe(7);
    });
  });

  it('shows Make Default on add blueprint source form', async () => {
    const user = userEvent.setup();

    server.use(
      http.get('*/api/blueprint-catalog/sources', () => HttpResponse.json([])),
      http.get('*/api/blueprint-catalog/releases', () => HttpResponse.json([]))
    );

    render(<BlueprintCatalogPanel />);

    await user.click(screen.getByRole('button', { name: /sources/i }));
    await user.click(screen.getByRole('button', { name: /add blueprint source/i }));

    expect(screen.getByRole('heading', { name: 'Add Blueprint Source' })).toBeInTheDocument();
    expect(screen.getByLabelText('Make Default')).toBeInTheDocument();
  });

  it('shows a browse-first layout with search, filters, sources, and sync all', async () => {
    server.use(
      http.get('*/api/blueprint-catalog/sources', () =>
        HttpResponse.json([
          {
            id: 7,
            name: 'IBM Blueprints',
            source_type: 'git',
            url: 'https://github.com/jgruberf5/bnk-forge-ibm-roks-cluster.git',
            branch: 'main',
            git_ref: null,
            sync_status: 'success',
            sync_error: null,
            last_synced_at: new Date().toISOString(),
            release_count: 1,
            is_active: true,
            is_default: true,
            description: 'Blueprint repo',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ])
      ),
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json([
          {
            id: 17,
            blueprint_source_id: 7,
            source_name: 'IBM Blueprints',
            blueprint_id: 'ibm-roks-cluster',
            blueprint_version: '1.0.0',
            blueprint_name: 'IBM ROKS Cluster',
            blueprint_description: 'Provision IBM ROKS cluster resources',
            category: 'infrastructure',
            cloud_provider: 'ibm',
            tags: ['ibm', 'roks'],
            schema_version: 1,
            source_path: 'blueprints/ibm-roks-cluster/forge-blueprint.json',
            source_ref: 'main',
            content_sha256: 'abc123',
            validation_state: 'valid',
            validation_errors: null,
            release_state: 'discovered',
            state_reason: null,
            is_active: true,
            imported_at: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: [],
          },
        ])
      )
    );

    render(<BlueprintCatalogPanel />);

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/search blueprints/i)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /sources/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /sync all/i })).toBeInTheDocument();
      expect(screen.getByText('IBM ROKS Cluster')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /deploy/i })).toBeInTheDocument();
    });
  });

  it('disables Deploy for discovered blueprint releases that are not imported yet', async () => {
    server.use(
      http.get('*/api/blueprint-catalog/sources', () =>
        HttpResponse.json([
          {
            id: 7,
            name: 'IBM Blueprints',
            source_type: 'git',
            url: 'https://github.com/jgruberf5/bnk-forge-ibm-roks-cluster.git',
            branch: 'main',
            git_ref: null,
            sync_status: 'success',
            sync_error: null,
            last_synced_at: new Date().toISOString(),
            release_count: 1,
            is_active: true,
            is_default: true,
            description: 'Blueprint repo',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ])
      ),
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json([
          {
            id: 17,
            blueprint_source_id: 7,
            source_name: 'IBM Blueprints',
            blueprint_id: 'ibm-roks-existing-cluster',
            blueprint_version: '1.0.1',
            blueprint_name: 'IBM ROKS Existing Cluster Registration',
            blueprint_description: 'Reference an existing cluster',
            category: 'infrastructure',
            cloud_provider: 'ibm',
            tags: ['ibm', 'roks'],
            schema_version: 1,
            source_path: 'blueprints/ibm-roks-existing-cluster/forge-blueprint.json',
            source_ref: 'main',
            content_sha256: 'abc123',
            validation_state: 'valid',
            validation_errors: null,
            release_state: 'discovered',
            state_reason: null,
            is_active: false,
            imported_at: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: [],
          },
        ])
      )
    );

    render(<BlueprintCatalogPanel />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /deploy/i })).toBeDisabled();
    });
  });

  it('surfaces successful syncs that found zero blueprint manifests', async () => {
    const user = userEvent.setup();

    server.use(
      http.get('*/api/blueprint-catalog/sources', () =>
        HttpResponse.json([
          {
            id: 6,
            name: 'official-bnk-forge-modules blueprints',
            source_type: 'git',
            url: 'https://github.com/JLCode-tech/bnk-forge-modules.git',
            branch: 'release/2.2',
            git_ref: 'release/2.2',
            sync_status: 'success',
            sync_error: 'No forge-blueprint.json manifests found',
            last_synced_at: new Date().toISOString(),
            release_count: 0,
            is_active: true,
            is_default: true,
            description: 'Blueprint discovery from module repo',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ])
      ),
      http.get('*/api/blueprint-catalog/releases', () => HttpResponse.json([]))
    );

    render(<BlueprintCatalogPanel />);

    await user.click(screen.getByRole('button', { name: /sources/i }));

    await waitFor(() => {
      expect(screen.getByText(/no forge-blueprint\.json manifests were found/i)).toBeInTheDocument();
    });
  });

  it('shows conflict-specific sync messaging for already-imported manifest changes', async () => {
    const user = userEvent.setup();

    server.use(
      http.get('*/api/blueprint-catalog/sources', () =>
        HttpResponse.json([
          {
            id: 8,
            name: 'bnk-forge-ibm-roks-cluster blueprints',
            source_type: 'git',
            url: 'https://github.com/jgruberf5/bnk-forge-ibm-roks-cluster',
            branch: 'main',
            git_ref: null,
            sync_status: 'success',
            sync_error: null,
            last_synced_at: new Date().toISOString(),
            release_count: 2,
            is_active: true,
            is_default: false,
            description: 'Blueprint repo',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ])
      ),
      http.get('*/api/blueprint-catalog/releases', () => HttpResponse.json([])),
      http.post('*/api/blueprint-catalog/sources/:id/sync', () =>
        HttpResponse.json({
          success: true,
          results: {
            blueprints_found: 2,
            releases_created: 0,
            releases_existing: 0,
            releases_conflicted: 2,
            releases_invalid: 0,
            errors: [
              'blueprints/one/forge-blueprint.json: Blueprint release already exists with different content for a@v1.0.0',
              'blueprints/two/forge-blueprint.json: Blueprint release already exists with different content for b@v1.0.0',
            ],
          },
        })
      )
    );

    render(<BlueprintCatalogPanel />);

    await user.click(screen.getByRole('button', { name: /sources/i }));
    await user.click(screen.getByRole('button', { name: /sync bnk-forge-ibm-roks-cluster blueprints/i }));

    await waitFor(() => {
      expect(notify.warning).toHaveBeenCalled();
    });
  });

  // ─── Grouping tests ─────────────────────────────────────────────────────────

  it('groups three releases of one blueprint into a single card with latest version primary', async () => {
    const mkSource = () => ({
      id: 10,
      name: 'AWS Blueprints',
      source_type: 'git',
      url: 'https://github.com/example/aws-blueprints.git',
      branch: 'main',
      git_ref: null,
      sync_status: 'success',
      sync_error: null,
      last_synced_at: new Date().toISOString(),
      release_count: 3,
      is_active: true,
      is_default: true,
      description: 'AWS blueprint source',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });

    const mkRelease = (id: number, version: string) => ({
      id,
      blueprint_source_id: 10,
      source_name: 'AWS Blueprints',
      blueprint_id: 'aws-eks-existing-cluster',
      blueprint_version: version,
      blueprint_name: 'AWS EKS Existing Cluster',
      blueprint_description: 'Register an existing EKS cluster',
      category: 'infrastructure',
      cloud_provider: 'aws',
      tags: ['aws', 'eks'],
      schema_version: 1,
      source_path: `blueprints/aws-eks-existing-cluster/${version}/forge-blueprint.json`,
      source_ref: 'main',
      content_sha256: `sha-${id}`,
      validation_state: 'valid',
      validation_errors: null,
      release_state: 'discovered',
      state_reason: null,
      is_active: true,
      imported_at: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      mutable_lifecycle_fields: [],
    });

    server.use(
      http.get('*/api/blueprint-catalog/sources', () => HttpResponse.json([mkSource()])),
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json([
          mkRelease(100, '0.5.0'),
          mkRelease(101, '0.7.0'),
          mkRelease(102, '0.7.1'),
        ])
      )
    );

    render(<BlueprintCatalogPanel />);

    await waitFor(() => {
      expect(screen.getByText('AWS EKS Existing Cluster')).toBeInTheDocument();
    });

    // Only ONE card heading — three releases collapsed to one group
    expect(screen.getAllByText('AWS EKS Existing Cluster')).toHaveLength(1);
    // Latest version (0.7.1) shown as primary
    expect(screen.getByText(/v0\.7\.1/)).toBeInTheDocument();
    // Older versions are NOT shown in primary face
    expect(screen.queryByText(/v0\.5\.0/)).not.toBeInTheDocument();
    expect(screen.queryByText(/v0\.7\.0/)).not.toBeInTheDocument();
  });

  it('hides removed releases by default and reveals them via toggle', async () => {
    const user = userEvent.setup();

    const mkSource = () => ({
      id: 11,
      name: 'AWS Blueprints',
      source_type: 'git',
      url: 'https://github.com/example/aws-blueprints.git',
      branch: 'main',
      git_ref: null,
      sync_status: 'success',
      sync_error: null,
      last_synced_at: new Date().toISOString(),
      release_count: 2,
      is_active: true,
      is_default: true,
      description: 'AWS blueprint source',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });

    server.use(
      http.get('*/api/blueprint-catalog/sources', () => HttpResponse.json([mkSource()])),
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json([
          {
            id: 200,
            blueprint_source_id: 11,
            source_name: 'AWS Blueprints',
            blueprint_id: 'aws-eks-new-cluster',
            blueprint_version: '1.0.0',
            blueprint_name: 'AWS EKS New Cluster',
            blueprint_description: 'Deploy a new EKS cluster',
            category: 'infrastructure',
            cloud_provider: 'aws',
            tags: [],
            schema_version: 1,
            source_path: 'blueprints/aws-eks-new-cluster/forge-blueprint.json',
            source_ref: 'main',
            content_sha256: 'sha-200',
            validation_state: 'valid',
            validation_errors: null,
            release_state: 'discovered',
            state_reason: null,
            is_active: true,
            imported_at: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: [],
          },
          {
            id: 201,
            blueprint_source_id: 11,
            source_name: 'AWS Blueprints',
            blueprint_id: 'aws-eks-removed-cluster',
            blueprint_version: '0.9.0',
            blueprint_name: 'AWS EKS Removed Blueprint',
            blueprint_description: 'A removed blueprint',
            category: 'infrastructure',
            cloud_provider: 'aws',
            tags: [],
            schema_version: 1,
            source_path: 'blueprints/aws-eks-removed-cluster/forge-blueprint.json',
            source_ref: 'main',
            content_sha256: 'sha-201',
            validation_state: 'valid',
            validation_errors: null,
            release_state: 'discovered',
            state_reason: 'Removed from deployable catalog',
            is_active: false,
            imported_at: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: [],
          },
        ])
      )
    );

    render(<BlueprintCatalogPanel />);

    await waitFor(() => {
      expect(screen.getByText('AWS EKS New Cluster')).toBeInTheDocument();
    });

    // Removed blueprint hidden by default
    expect(screen.queryByText('AWS EKS Removed Blueprint')).not.toBeInTheDocument();

    // Toggle "Show removed"
    await user.click(screen.getByRole('button', { name: /show removed/i }));

    await waitFor(() => {
      expect(screen.getByText('AWS EKS Removed Blueprint')).toBeInTheDocument();
    });
  });

  it('keeps superseded (unimported) releases visible as version history by default (D-033 #438)', async () => {
    const user = userEvent.setup();
    const mkRelease = (overrides: Record<string, unknown>) => ({
      blueprint_source_id: 11,
      source_name: 'AWS Blueprints',
      blueprint_id: 'roks-bnk-demo',
      blueprint_name: 'BNK on ROKS',
      blueprint_description: 'demo',
      category: 'bnk',
      cloud_provider: 'ibm',
      tags: [],
      schema_version: 1,
      source_path: 'blueprints/roks-bnk-demo/forge-blueprint.json',
      source_ref: 'main',
      validation_state: 'valid',
      validation_errors: null,
      is_active: true,
      imported_at: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      mutable_lifecycle_fields: [],
      ...overrides,
    });
    server.use(
      http.get('*/api/blueprint-catalog/sources', () =>
        HttpResponse.json([
          {
            id: 11,
            name: 'AWS Blueprints',
            source_type: 'git',
            url: 'https://github.com/example/blueprints.git',
            branch: 'main',
            git_ref: null,
            sync_status: 'success',
            sync_error: null,
            last_synced_at: new Date().toISOString(),
            release_count: 2,
            is_active: true,
            is_default: false,
            description: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ])
      ),
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json([
          mkRelease({
            id: 300,
            blueprint_version: '1.1.1',
            content_sha256: 'sha-300',
            release_state: 'imported',
            state_reason: 'Imported from Blueprint Catalog UI',
          }),
          mkRelease({
            id: 301,
            blueprint_version: '1.1.0',
            content_sha256: 'sha-301',
            release_state: 'discovered',
            state_reason: 'Removed from deployable catalog',
            is_active: false,
          }),
        ])
      )
    );

    render(<BlueprintCatalogPanel />);

    // The row faces the newest NON-removed release...
    await waitFor(() => {
      expect(screen.getByText('BNK on ROKS')).toBeInTheDocument();
    });
    expect(screen.getByText('v1.1.1')).toBeInTheDocument();

    // ...and the superseded release is reachable via the version disclosure
    // WITHOUT toggling "Show removed".
    await user.click(screen.getByRole('button', { name: /1 other version/i }));
    await waitFor(() => {
      expect(screen.getByText('v1.1.0')).toBeInTheDocument();
    });
  });

  it('shows "Visible on Blueprints page" toggle for imported releases', async () => {
    server.use(
      http.get('*/api/blueprint-catalog/sources', () =>
        HttpResponse.json([
          {
            id: 20,
            name: 'example-blueprints',
            source_type: 'git',
            url: 'https://github.com/example/blueprints.git',
            branch: 'main',
            git_ref: null,
            sync_status: 'success',
            sync_error: null,
            last_synced_at: new Date().toISOString(),
            release_count: 1,
            is_active: true,
            is_default: false,
            description: 'Test blueprints',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ])
      ),
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json([
          {
            id: 50,
            blueprint_source_id: 20,
            source_name: 'example-blueprints',
            blueprint_id: 'aws-eks-cluster',
            blueprint_version: '1.0.0',
            blueprint_name: 'AWS EKS Cluster',
            blueprint_description: 'EKS cluster',
            category: 'infrastructure',
            cloud_provider: 'aws',
            tags: [],
            schema_version: 1,
            source_path: 'blueprints/aws-eks-cluster/forge-blueprint.json',
            source_ref: 'main',
            content_sha256: 'abc123',
            validation_state: 'valid',
            validation_errors: null,
            release_state: 'imported',
            state_reason: null,
            is_active: true,
            is_featured: false,
            imported_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: ['is_active', 'imported_at', 'release_state', 'state_reason'],
          },
        ])
      )
    );

    render(<BlueprintCatalogPanel />);

    await waitFor(() => {
      expect(screen.getByText('AWS EKS Cluster')).toBeInTheDocument();
    });

    // Visibility toggle should be present for imported releases (aria-label on Switch)
    expect(screen.getByRole('checkbox', { name: 'Visible on Blueprints page' })).toBeInTheDocument();
  });

  it('fires PATCH visibility mutation when the "Visible on Blueprints page" switch is toggled', async () => {
    const user = userEvent.setup();
    let patchedReleaseId: number | null = null;
    let patchedBody: Record<string, unknown> | null = null;

    server.use(
      http.get('*/api/blueprint-catalog/sources', () =>
        HttpResponse.json([
          {
            id: 21,
            name: 'example-blueprints',
            source_type: 'git',
            url: 'https://github.com/example/blueprints.git',
            branch: 'main',
            git_ref: null,
            sync_status: 'success',
            sync_error: null,
            last_synced_at: new Date().toISOString(),
            release_count: 1,
            is_active: true,
            is_default: false,
            description: 'Test blueprints',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ])
      ),
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json([
          {
            id: 60,
            blueprint_source_id: 21,
            source_name: 'example-blueprints',
            blueprint_id: 'aws-eks-cluster',
            blueprint_version: '2.0.0',
            blueprint_name: 'AWS EKS Cluster v2',
            blueprint_description: 'EKS cluster v2',
            category: 'infrastructure',
            cloud_provider: 'aws',
            tags: [],
            schema_version: 1,
            source_path: 'blueprints/aws-eks-cluster/forge-blueprint.json',
            source_ref: 'main',
            content_sha256: 'abc456',
            validation_state: 'valid',
            validation_errors: null,
            release_state: 'imported',
            state_reason: null,
            is_active: true,
            is_featured: false,
            imported_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: ['is_active', 'imported_at', 'release_state', 'state_reason'],
          },
        ])
      ),
      http.patch('*/api/blueprint-catalog/releases/:id/visibility', async ({ params, request }) => {
        patchedReleaseId = Number(params.id);
        patchedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          id: 60,
          blueprint_source_id: 21,
          source_name: 'example-blueprints',
          blueprint_id: 'aws-eks-cluster',
          blueprint_version: '2.0.0',
          blueprint_name: 'AWS EKS Cluster v2',
          blueprint_description: 'EKS cluster v2',
          category: 'infrastructure',
          cloud_provider: 'aws',
          tags: [],
          schema_version: 1,
          source_path: 'blueprints/aws-eks-cluster/forge-blueprint.json',
          source_ref: 'main',
          content_sha256: 'abc456',
          validation_state: 'valid',
          validation_errors: null,
          release_state: 'imported',
          state_reason: null,
          is_active: false,
          is_featured: false,
          imported_at: new Date().toISOString(),
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          mutable_lifecycle_fields: ['is_active', 'imported_at', 'release_state', 'state_reason'],
        });
      }),
    );

    render(<BlueprintCatalogPanel />);

    await waitFor(() => {
      expect(screen.getByText('AWS EKS Cluster v2')).toBeInTheDocument();
    });

    // Visibility toggle is present for imported releases (aria-label on Switch)
    const visibilitySwitch = screen.getByRole('checkbox', { name: 'Visible on Blueprints page' });
    expect(visibilitySwitch).toBeInTheDocument();

    // Toggle the switch
    await user.click(visibilitySwitch);

    await waitFor(() => {
      expect(patchedReleaseId).toBe(60);
    });
    expect(patchedBody).toEqual({ is_visible: false });
  });

  it('shows Built-in badge for builtin source releases', async () => {
    server.use(
      http.get('*/api/blueprint-catalog/sources', () =>
        HttpResponse.json([
          {
            id: 1,
            name: 'builtin-blueprints',
            source_type: 'builtin',
            url: 'builtin://bundled',
            branch: null,
            git_ref: null,
            sync_status: 'success',
            sync_error: null,
            last_synced_at: new Date().toISOString(),
            release_count: 1,
            is_active: true,
            is_default: false,
            description: 'Bundled blueprints',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ])
      ),
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json([
          {
            id: 51,
            blueprint_source_id: 1,
            source_name: 'builtin-blueprints',
            blueprint_id: 'aws-k8s-foundation',
            blueprint_version: '1.0.0',
            blueprint_name: 'AWS EKS Cluster',
            blueprint_description: 'Foundation EKS cluster',
            category: 'infrastructure',
            cloud_provider: 'aws',
            tags: [],
            schema_version: 1,
            source_path: 'data/blueprints/aws-k8s-foundation/forge-blueprint.json',
            source_ref: null,
            content_sha256: 'def456',
            validation_state: 'valid',
            validation_errors: null,
            release_state: 'imported',
            state_reason: 'bundled builtin',
            is_active: true,
            is_featured: true,
            imported_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: ['is_active', 'imported_at', 'release_state', 'state_reason'],
          },
        ])
      )
    );

    render(<BlueprintCatalogPanel />);

    await waitFor(() => {
      expect(screen.getByText('AWS EKS Cluster')).toBeInTheDocument();
    });

    expect(screen.getByText('Built-in')).toBeInTheDocument();
    expect(screen.getByText('Featured')).toBeInTheDocument();
    // Visibility toggle still shows for imported builtins (aria-label on Switch)
    expect(screen.getByRole('checkbox', { name: 'Visible on Blueprints page' })).toBeInTheDocument();
  });

  it('"Latest only" chip suppresses version disclosure for multi-version groups', async () => {
    const user = userEvent.setup();

    const mkSource = () => ({
      id: 12,
      name: 'AWS Blueprints',
      source_type: 'git',
      url: 'https://github.com/example/aws-blueprints.git',
      branch: 'main',
      git_ref: null,
      sync_status: 'success',
      sync_error: null,
      last_synced_at: new Date().toISOString(),
      release_count: 2,
      is_active: true,
      is_default: true,
      description: 'AWS blueprint source',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });

    server.use(
      http.get('*/api/blueprint-catalog/sources', () => HttpResponse.json([mkSource()])),
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json([
          {
            id: 300,
            blueprint_source_id: 12,
            source_name: 'AWS Blueprints',
            blueprint_id: 'aws-eks-existing-cluster',
            blueprint_version: '0.6.0',
            blueprint_name: 'AWS EKS Existing Cluster',
            blueprint_description: 'Register an existing EKS cluster',
            category: 'infrastructure',
            cloud_provider: 'aws',
            tags: [],
            schema_version: 1,
            source_path: 'blueprints/aws-eks-existing-cluster/forge-blueprint.json',
            source_ref: 'main',
            content_sha256: 'sha-300',
            validation_state: 'valid',
            validation_errors: null,
            release_state: 'discovered',
            state_reason: null,
            is_active: true,
            imported_at: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: [],
          },
          {
            id: 301,
            blueprint_source_id: 12,
            source_name: 'AWS Blueprints',
            blueprint_id: 'aws-eks-existing-cluster',
            blueprint_version: '0.7.0',
            blueprint_name: 'AWS EKS Existing Cluster',
            blueprint_description: 'Register an existing EKS cluster',
            category: 'infrastructure',
            cloud_provider: 'aws',
            tags: [],
            schema_version: 1,
            source_path: 'blueprints/aws-eks-existing-cluster/forge-blueprint.json',
            source_ref: 'main',
            content_sha256: 'sha-301',
            validation_state: 'valid',
            validation_errors: null,
            release_state: 'discovered',
            state_reason: null,
            is_active: true,
            imported_at: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: [],
          },
        ])
      )
    );

    render(<BlueprintCatalogPanel />);

    await waitFor(() => {
      expect(screen.getByText('AWS EKS Existing Cluster')).toBeInTheDocument();
    });

    // Without "latest only", the disclosure toggle button should exist
    expect(screen.getByRole('button', { name: /other version/i })).toBeInTheDocument();

    // Enable "Latest only" chip
    await user.click(screen.getByRole('button', { name: /latest only/i }));

    await waitFor(() => {
      // The disclosure toggle should no longer be visible
      expect(screen.queryByRole('button', { name: /other version/i })).not.toBeInTheDocument();
    });
  });

  it('renders the validation-issue tooltip without crashing when a release has validation errors', async () => {
    // Regression: the validation-issue Tooltip rendered without a TooltipProvider,
    // crashing the Catalog page with "`Tooltip` must be used within `TooltipProvider`"
    // as soon as a blueprint with validation_errors loaded.
    server.use(
      http.get('*/api/blueprint-catalog/sources', () =>
        HttpResponse.json([
          {
            id: 30,
            name: 'AWS Blueprints',
            source_type: 'git',
            url: 'https://github.com/example/aws-blueprints.git',
            branch: 'main',
            git_ref: null,
            sync_status: 'success',
            sync_error: null,
            last_synced_at: new Date().toISOString(),
            release_count: 1,
            is_active: true,
            is_default: true,
            description: 'AWS blueprint source',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ])
      ),
      http.get('*/api/blueprint-catalog/releases', () =>
        HttpResponse.json([
          {
            id: 400,
            blueprint_source_id: 30,
            source_name: 'AWS Blueprints',
            blueprint_id: 'aws-eks-broken-cluster',
            blueprint_version: '1.0.0',
            blueprint_name: 'AWS EKS Broken Cluster',
            blueprint_description: 'A blueprint with validation problems',
            category: 'infrastructure',
            cloud_provider: 'aws',
            tags: [],
            schema_version: 1,
            source_path: 'blueprints/aws-eks-broken-cluster/forge-blueprint.json',
            source_ref: 'main',
            content_sha256: 'sha-400',
            validation_state: 'invalid',
            validation_errors: ['missing required field: region', 'unknown module ref'],
            release_state: 'discovered',
            state_reason: null,
            is_active: true,
            imported_at: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            mutable_lifecycle_fields: [],
          },
        ])
      )
    );

    render(<BlueprintCatalogPanel />);

    // If the Tooltip lacked a provider, the render would throw and the name
    // would never appear. Reaching the assertion proves the page rendered.
    await waitFor(() => {
      expect(screen.getByText('AWS EKS Broken Cluster')).toBeInTheDocument();
    });
  });
});
