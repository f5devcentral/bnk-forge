/**
 * MSW Request Handlers
 *
 * Default API mock handlers for all endpoints.
 * Individual tests can override these with server.use(...).
 *
 * Note: Uses wildcard URL matching to handle both relative
 * and absolute URLs (jsdom resolves relative URLs to localhost).
 *
 * Mock data is imported from test-fixtures.ts (single source of truth).
 */
import { http, HttpResponse } from 'msw';
import {
  mockProjects,
  mockUser,
  mockUsers,
  mockTasks,
  mockTaskStats,
  mockClusters,
  mockK8sResources,
  mockSystemHealth,
  mockSystemDefaults,
  mockQueueMetrics,
  mockPerformanceMetrics,
  mockRecentErrors,
  mockDatabaseStats,
  mockContainerStatus,
  mockSystemVersion,
  mockBackupStatus,
  mockMaintenanceStatus,
  mockModules,
  mockModuleLibrary,
  mockModuleSources,
  mockHelmReleases,
  mockHelmRepositories,
  mockStackTemplates,
  mockStackInstances,
  mockSecrets,
  mockOperators,
  mockConnectivityModes,
  mockCredentialTemplates,
  mockDriftSummary,
  mockDriftSettings,
  mockDriftChecks,
  mockDriftStats,
  mockAlertChannels,
  mockAlertHistory,
  mockSnapshots,
  mockSnapshotDetail,
  mockFleetHealth,
  mockFleetTargets,
  mockFleetCompareResult,
  mockPromoteResponse,
  mockPromotionHistory,
  mockRunbooks,
  mockRunbookResult,
  mockParallelExecution,
  mockParallelExecutionPending,
  mockVariableMappings,
  mockAuditEntries,
} from '../test-fixtures';

// Re-export fixtures for backward compatibility (tests that imported from handlers.ts)
export {
  mockProjects,
  mockUser,
  mockTasks,
  mockClusters,
  mockSystemHealth,
  mockModules,
  mockHelmReleases,
  mockStackTemplates,
  mockSecrets,
  mockOperators,
  mockDriftSummary,
  mockBackupStatus,
  mockMaintenanceStatus,
  mockBackupStatusInProgress,
  mockMaintenanceActive,
} from '../test-fixtures';

// ============================================================================
// Handlers
// ============================================================================

export const handlers = [
  // ========================================================================
  // Auth
  // ========================================================================

  http.post('*/api/auth/login', async ({ request }) => {
    const body = (await request.json()) as { username: string; password: string };
    if (body.username === 'admin' && (body.password === 'password' || body.password === 'changeme')) {
      return HttpResponse.json({
        token: 'mock-jwt-token',
        user: mockUser,
        must_change_password: false,
      });
    }
    return HttpResponse.json(
      { error: { code: 'UNAUTHORIZED', message: 'Invalid credentials' } },
      { status: 401 }
    );
  }),

  http.get('*/api/auth/me', () => {
    return HttpResponse.json({ user: mockUser });
  }),

  http.post('*/api/auth/change-password', () => {
    return HttpResponse.json({ success: true, message: 'Password changed' });
  }),

  http.get('*/api/auth/users', () => {
    return HttpResponse.json({ users: mockUsers, total: mockUsers.length });
  }),

  // ========================================================================
  // Backup / Restore
  // ========================================================================

  http.get('*/api/system/backup/status', () => {
    return HttpResponse.json(mockBackupStatus);
  }),

  http.get('*/api/system/maintenance', () => {
    return HttpResponse.json(mockMaintenanceStatus);
  }),

  http.post('*/api/system/backup', async ({ request }) => {
    const body = (await request.json()) as { passphrase: string };
    if (body.passphrase.length < 12) {
      return HttpResponse.json(
        { error: { code: 'invalid_passphrase', message: 'Passphrase must be at least 12 characters' } },
        { status: 400 }
      );
    }

    return new HttpResponse(new Blob(['fake-archive-data']), {
      headers: {
        'Content-Type': 'application/gzip',
        'Content-Disposition': 'attachment; filename="bnkforge-backup-default.tar.gz"',
      },
    });
  }),

  http.post('*/api/auth/users', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      success: true,
      user: { id: 100, ...body, is_active: true, must_change_password: true, created_at: new Date().toISOString() },
    });
  }),

  // MU-005: Update user
  http.put('*/api/auth/users/:userId', async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      success: true,
      user: { id: Number(params.userId), username: 'testuser', email: body.email || 'test@test.com', role: body.role || 'operator', is_active: body.is_active ?? true, must_change_password: false, created_at: new Date().toISOString() },
    });
  }),

  http.delete('*/api/auth/users/:userId', () => {
    return HttpResponse.json({ success: true, message: 'User deleted' });
  }),

  // ========================================================================
  // Projects
  // ========================================================================

  http.get('*/api/projects', ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname !== '/api/projects') return;
    return HttpResponse.json({ projects: mockProjects, total: mockProjects.length });
  }),

  http.get('*/api/projects/:id', ({ params, request }) => {
    const url = new URL(request.url);
    if (url.pathname.split('/').length > 4) return;
    const project = mockProjects.find((p) => p.id === Number(params.id));
    if (!project) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json(project);
  }),

  http.post('*/api/projects', async ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname !== '/api/projects') return;
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      success: true,
      project_id: 3,
      name: body.name,
      message: 'Project created successfully',
    });
  }),

  http.put('*/api/projects/:id', async ({ params, request }) => {
    const url = new URL(request.url);
    if (url.pathname.split('/').length > 4) return;
    const body = (await request.json()) as Record<string, unknown>;
    const project = mockProjects.find((p) => p.id === Number(params.id));
    if (!project) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json({ ...project, ...body, updated_at: new Date().toISOString() });
  }),

  // useConnectivitySession (mounted via AppShell on every authenticated page)
  // calls /api/connectivity/state on mount and opens an SSE stream at
  // /api/connectivity/watch. Without a handler each XHR + EventSource
  // attempt produced an Error: AggregateError from jsdom, and the
  // cumulative event flood appeared to be crashing the vitest orchestrator
  // mid-suite on CI (deterministic ~1m30s cancel before the 5-7 minute
  // suite could finish). Stub both endpoints with empty success responses
  // — tests that care about connectivity state override with server.use().
  http.get('*/api/connectivity/state', () =>
    HttpResponse.json({ states: [] }),
  ),
  // SSE endpoint — return 503 so fetchEventSource closes immediately rather
  // than hanging on a never-ending event stream. The hook catches the error
  // and gives up retrying for the duration of the test.
  http.get('*/api/connectivity/watch', () =>
    new HttpResponse('', { status: 503 }),
  ),

  // Both the canonical /ibm/regions and the legacy /regions/ibm alias are
  // intercepted with the same handler so tests don't have to know which
  // path the frontend currently uses. Backend keeps both routes wired
  // (the legacy one is hidden from the schema but still callable).
  ...(['*/api/cloud-auth/ibm/regions', '*/api/cloud-auth/regions/ibm'].map((path) =>
    http.get(path, ({ request }) => {
      const url = new URL(request.url);
      const templateId = url.searchParams.get('template_id');

      if (templateId === '2') {
        return HttpResponse.json({
          provider: 'ibm',
          regions: [
            { value: 'us-south', label: 'US South (Dallas)' },
            { value: 'eu-de', label: 'EU Germany (Frankfurt)' },
          ],
        });
      }

      return HttpResponse.json({
        provider: 'ibm',
        regions: [
          { value: 'us-south', label: 'US South (Dallas)' },
          { value: 'us-east', label: 'US East (Washington DC)' },
        ],
      });
    })
  )),

  http.post('*/api/cloud-auth/regions/query', async ({ request }) => {
    const body = (await request.json()) as { provider: string; ibmcloud_api_key?: string };
    if (body.provider !== 'ibm') {
      return HttpResponse.json({ error: { code: 'BAD_REQUEST', message: 'Unsupported provider' } }, { status: 400 });
    }
    if (!body.ibmcloud_api_key) {
      return HttpResponse.json({ error: { code: 'BAD_REQUEST', message: 'IBM Cloud API key is required' } }, { status: 400 });
    }

    return HttpResponse.json({
      provider: 'ibm',
      regions: [
        { value: 'us-south', label: 'US South (Dallas)' },
        { value: 'eu-de', label: 'EU Germany (Frankfurt)' },
      ],
    });
  }),

  http.delete('*/api/projects/:id', ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname.split('/').length > 4) return;
    return HttpResponse.json({ success: true, message: 'Project deleted' });
  }),

  // MU-009: Transfer ownership
  http.post('*/api/projects/:id/transfer', async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      success: true,
      project_id: Number(params.id),
      project_name: 'Test Project',
      previous_owner: 'admin',
      new_owner: 'new-owner',
      message: `Transferred project to user #${body.new_owner_id}`,
    });
  }),

  // ========================================================================
  // Project Modules
  // ========================================================================

  http.get('*/api/projects/:projectId/modules', () => {
    return HttpResponse.json(mockModules);
  }),

  http.get('*/api/project-modules/project/:projectId', () => {
    return HttpResponse.json({ modules: mockModules, total: mockModules.length });
  }),

  http.post('*/api/projects/:projectId/modules', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      id: 10,
      ...body,
      status: 'not_initialized',
      created_at: new Date().toISOString(),
    });
  }),

  http.put('*/api/project-modules/:moduleId', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ id: Number((await request.clone().json() as Record<string, unknown>).id) || 1, ...body, updated_at: new Date().toISOString() });
  }),

  http.delete('*/api/project-modules/:moduleId', () => {
    return HttpResponse.json({ success: true, message: 'Module removed' });
  }),

  http.get('*/api/project-modules/:moduleId/status', ({ params }) => {
    return HttpResponse.json({ module_id: Number(params.moduleId), status: 'applied', last_action: 'apply' });
  }),

  // Module actions (D-034) — default: no actions declared (non-container module)
  http.get('*/api/project-modules/:moduleId/actions', ({ params }) => {
    return HttpResponse.json({ module_id: Number(params.moduleId), actions: [], total: 0 });
  }),

  // Module execution (init, plan, apply, destroy)
  http.post('*/api/project-modules/:moduleId/init', () => {
    return HttpResponse.json({ task_id: 'task-init-001', status: 'queued' });
  }),

  http.post('*/api/project-modules/:moduleId/plan', () => {
    return HttpResponse.json({ task_id: 'task-plan-001', status: 'queued' });
  }),

  http.post('*/api/project-modules/:moduleId/apply', () => {
    return HttpResponse.json({ task_id: 'task-apply-001', status: 'queued' });
  }),

  http.post('*/api/project-modules/:moduleId/destroy', () => {
    return HttpResponse.json({ task_id: 'task-destroy-001', status: 'queued' });
  }),

  http.post('*/api/project-modules/:moduleId/cancel', () => {
    return HttpResponse.json({ success: true, message: 'Operation cancelled' });
  }),

  http.post('*/api/project-modules/:moduleId/recover-state', () => {
    return HttpResponse.json({ success: true, message: 'State recovered' });
  }),

  // ========================================================================
  // Variable Mappings
  // ========================================================================

  http.get('*/api/project-modules/:moduleId/variable-mappings', () => {
    return HttpResponse.json(mockVariableMappings);
  }),

  http.post('*/api/project-modules/:moduleId/variable-mappings', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ id: 100, ...body, created_at: new Date().toISOString() });
  }),

  http.delete('*/api/project-modules/:moduleId/variable-mappings/:mappingId', () => {
    return HttpResponse.json({ success: true });
  }),

  // ========================================================================
  // Parallel Execution
  // ========================================================================

  http.post('*/api/projects/:projectId/deploy', () => {
    return HttpResponse.json(mockParallelExecutionPending);
  }),

  http.post('*/api/projects/:projectId/destroy-all', () => {
    return HttpResponse.json({ ...mockParallelExecutionPending, action: 'destroy' });
  }),

  http.get('*/api/projects/:projectId/execution-status', () => {
    return HttpResponse.json(mockParallelExecution);
  }),

  // ========================================================================
  // Secrets
  // ========================================================================

  http.get('*/api/projects/:projectId/secrets', () => {
    return HttpResponse.json(mockSecrets);
  }),

  http.post('*/api/projects/:projectId/secrets/value', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      id: 10,
      name: body.name,
      is_file: false,
      created_at: new Date().toISOString(),
    });
  }),

  http.delete('*/api/projects/:projectId/secrets/:secretId', () => {
    return HttpResponse.json({ success: true });
  }),

  // ========================================================================
  // Module Library / Catalog
  // ========================================================================

  http.get('*/api/module-library', ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname !== '/api/module-library') return;
    return HttpResponse.json({ modules: mockModuleLibrary, total: mockModuleLibrary.length });
  }),

  http.get('*/api/module-library/:moduleId', ({ params }) => {
    const mod = mockModuleLibrary.find((m) => m.id === Number(params.moduleId));
    if (!mod) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json({ ...mod, variables: [], outputs: [] });
  }),

  http.get('*/api/module-library/categories', () => {
    return HttpResponse.json(['network', 'kubernetes', 'security', 'compute']);
  }),

  http.get('*/api/module-library/providers', () => {
    return HttpResponse.json(['aws', 'f5', 'kubernetes']);
  }),

  // ========================================================================
  // Registry
  // ========================================================================

  http.get('*/api/registry/search', () => {
    return HttpResponse.json({ modules: [], total: 0 });
  }),

  http.post('*/api/registry/import', async () => {
    return HttpResponse.json({
      success: true,
      message: 'Module imported',
      module: { id: 9001, name: 'mock-imported-module', category: 'infra', provider: 'aws', version: '1.0.0' },
    });
  }),

  http.post('*/api/registry/import-git', async () => {
    return HttpResponse.json({
      success: true,
      message: 'Module imported from git',
      module: { id: 9002, name: 'mock-git-module', category: 'infra', provider: 'git', version: 'v1.0.0' },
    });
  }),

  http.post('*/api/registry/import-tfc', async () => {
    return HttpResponse.json({
      success: true,
      message: 'TFC module imported',
      module: { id: 9003, name: 'mock-tfc-module', category: 'infra', provider: 'aws', version: '1.0.0' },
    });
  }),

  // ========================================================================
  // Variable Mapping Templates
  // ========================================================================

  http.get('*/api/project-modules/variable-mapping-templates', () => {
    return HttpResponse.json({ templates: [] });
  }),

  // ========================================================================
  // Module Sources
  // ========================================================================

  http.get('*/api/module-sources', () => {
    return HttpResponse.json(mockModuleSources);
  }),

  http.post('*/api/module-sources', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ id: 10, ...body, is_active: true, module_count: 0, created_at: new Date().toISOString() });
  }),

  http.put('*/api/module-sources/:id', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ id: 1, ...body, updated_at: new Date().toISOString() });
  }),

  http.delete('*/api/module-sources/:id', () => {
    return HttpResponse.json({ success: true });
  }),

  http.post('*/api/module-sources/:id/sync', ({ params }) => {
    return HttpResponse.json({
      success: true,
      source_id: Number(params.id),
      source_name: 'mock-source',
      sync_status: 'completed',
      results: {
        modules_found: 5,
        modules_created: 5,
        modules_updated: 0,
        errors: [],
      },
    });
  }),

  // ========================================================================
  // Blueprint Catalog
  // ========================================================================

  http.get('*/api/blueprint-catalog/sources', () => {
    return HttpResponse.json([]);
  }),

  http.post('*/api/blueprint-catalog/sources', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      id: 20,
      ...body,
      source_type: 'git',
      sync_status: 'pending',
      sync_error: null,
      last_synced_at: null,
      release_count: 0,
      is_active: true,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
  }),

  http.post('*/api/blueprint-catalog/sources/:id/sync', () => {
    return HttpResponse.json({
      success: true,
      source_id: 20,
      source_name: 'example-blueprints',
      sync_status: 'success',
      results: {
        blueprints_found: 1,
        releases_created: 1,
        releases_existing: 0,
        releases_invalid: 0,
        errors: [],
      },
    });
  }),

  http.get('*/api/blueprint-catalog/releases', () => {
    return HttpResponse.json([
      {
        id: 31,
        blueprint_source_id: 20,
        source_name: 'example-blueprints',
        source_type: 'git',
        blueprint_id: 'ibm-roks-bnk-2-3-ehf.single-nic',
        blueprint_version: '2.3.0-ehf-2-3.2598.3-0.0.17',
        blueprint_name: 'BIG-IP Next for Kubernetes on IBM ROKS Single NIC',
        blueprint_description: 'Imported blueprint release',
        schema_version: 1,
        source_path: 'blueprints/bigip-next-roks-single-nic/forge-blueprint.json',
        source_ref: 'refs/heads/main',
        content_sha256: 'a'.repeat(64),
        validation_state: 'valid',
        validation_errors: null,
        release_state: 'imported',
        state_reason: 'catalog import',
        is_active: true,
        is_featured: false,
        imported_at: new Date().toISOString(),
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        mutable_lifecycle_fields: ['imported_at', 'is_active', 'release_state', 'state_reason'],
      },
    ]);
  }),

  http.post('*/api/blueprint-catalog/releases/:id/import', () => {
    return HttpResponse.json({
      id: 32,
      blueprint_source_id: 20,
      source_name: 'example-blueprints',
      source_type: 'git',
      blueprint_id: 'ibm-roks-bnk-2-3-ehf.single-nic',
      blueprint_version: '2.3.0-ehf-2-3.2598.3-0.0.17',
      blueprint_name: 'BIG-IP Next for Kubernetes on IBM ROKS Single NIC',
      blueprint_description: 'Imported blueprint release',
      schema_version: 1,
      source_path: 'blueprints/bigip-next-roks-single-nic/forge-blueprint.json',
      source_ref: 'refs/heads/main',
      content_sha256: 'b'.repeat(64),
      validation_state: 'valid',
      validation_errors: null,
      release_state: 'imported',
      state_reason: 'Imported from Blueprint Catalog UI',
      is_active: true,
      is_featured: false,
      imported_at: new Date().toISOString(),
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      mutable_lifecycle_fields: ['imported_at', 'is_active', 'release_state', 'state_reason'],
    });
  }),

  http.patch('*/api/blueprint-catalog/releases/:id/visibility', async ({ params, request }) => {
    const body = await request.json() as { is_visible: boolean };
    return HttpResponse.json({
      id: Number(params.id),
      blueprint_source_id: 20,
      source_name: 'example-blueprints',
      source_type: 'git',
      blueprint_id: 'ibm-roks-bnk-2-3-ehf.single-nic',
      blueprint_version: '2.3.0-ehf-2-3.2598.3-0.0.17',
      blueprint_name: 'BIG-IP Next for Kubernetes on IBM ROKS Single NIC',
      blueprint_description: 'Imported blueprint release',
      schema_version: 1,
      source_path: 'blueprints/bigip-next-roks-single-nic/forge-blueprint.json',
      source_ref: 'refs/heads/main',
      content_sha256: 'a'.repeat(64),
      validation_state: 'valid',
      validation_errors: null,
      release_state: 'imported',
      state_reason: null,
      is_active: body.is_visible,
      is_featured: false,
      imported_at: new Date().toISOString(),
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      mutable_lifecycle_fields: ['imported_at', 'is_active', 'release_state', 'state_reason'],
    });
  }),

  http.delete('*/api/blueprint-catalog/releases/:id', () => {
    return HttpResponse.json({
      success: true,
      message: 'Blueprint release deleted',
    });
  }),

  http.get('*/api/stacks/releases/:id', ({ params }) => {
    const id = Number(params.id);
    return HttpResponse.json({
      id: 1000000 + id,
      name: 'BIG-IP Next for Kubernetes on IBM ROKS Single NIC',
      slug: `release-${id}`,
      description: 'Imported blueprint release',
      category: 'bnk',
      cloud_provider: 'ibm',
      icon: 'Layers',
      color: 'blue',
      estimated_time: '20-30 minutes',
      estimated_cost: 'IBM Cloud usage-based',
      difficulty: 'intermediate',
      modules: [
        {
          path: 'modules/cluster',
          name: 'Cluster',
          required: true,
          description: 'Configures the target ROKS cluster.',
          variables: {},
          module_catalog_status: 'available',
        },
      ],
      variable_templates: {},
      prerequisites: [
        {
          type: 'project_secret',
          name: 'ibmcloud_api_key',
          description: 'IBM Cloud API key with access to the target account.',
        },
      ],
      tags: ['ibm', 'roks', 'bnk'],
      maturity: 'reference',
      outcomes: [
        'Single-NIC deployment topology',
        'Imported project flow',
      ],
      platform_defaults: {},
      is_active: true,
      is_featured: false,
      version: '2.3.0-ehf-2-3.2598.3-0.0.17',
      created_by: 'blueprint-catalog',
      is_public: true,
      forked_from: null,
      source_kind: 'blueprint_release',
      blueprint_release_id: id,
      blueprint_source_id: 20,
      release_state: 'imported',
      validation_state: 'valid',
      source_path: 'blueprints/bigip-next-roks-single-nic/forge-blueprint.json',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
  }),

  http.get('*/api/stacks/releases/:id/preview', () => {
    return HttpResponse.json({
      modules: [
        {
          path: 'modules/cluster',
          name: 'cluster',
          required: true,
          description: 'Cluster module',
          variables: {},
          module_catalog_status: 'available',
        },
      ],
      prerequisites: [],
      estimated_cost: null,
      estimated_time: 'Varies',
      total_modules: 1,
    });
  }),

  http.get('*/api/stacks/releases/:id/required-inputs', () => {
    return HttpResponse.json({
      template_slug: 'release-31',
      template_name: 'BIG-IP Next for Kubernetes on IBM ROKS Single NIC',
      inputs_by_module: {
        blueprint: [
          {
            name: 'ibmcloud_api_key',
            type: 'string',
            description: 'IBM Cloud API key',
            example: 'xxxxxxxx-xxxx',
            default: null,
            module_path: 'blueprint',
            module_name: 'BIG-IP Next for Kubernetes on IBM ROKS Single NIC',
            required: true,
            sensitive: true,
            source: 'credential_template',
            source_field: 'ibmcloud_api_key',
          },
          {
            name: 'ibmcloud_cluster_region',
            type: 'string',
            description: 'IBM Cloud region',
            example: 'us-south',
            default: null,
            module_path: 'blueprint',
            module_name: 'BIG-IP Next for Kubernetes on IBM ROKS Single NIC',
            required: true,
            source: 'project',
            source_field: 'region',
          },
          {
            name: 'ibmcloud_resource_group',
            type: 'string',
            description: 'IBM Cloud resource group',
            example: 'default',
            default: 'default',
            module_path: 'blueprint',
            module_name: 'BIG-IP Next for Kubernetes on IBM ROKS Single NIC',
            required: false,
            source: 'credential_template',
            source_field: 'ibmcloud_resource_group',
          },
        ],
      },
      all_inputs: [
        {
          name: 'ibmcloud_api_key',
          type: 'string',
          description: 'IBM Cloud API key',
          example: 'xxxxxxxx-xxxx',
          default: null,
          module_path: 'blueprint',
          module_name: 'BIG-IP Next for Kubernetes on IBM ROKS Single NIC',
          required: true,
          sensitive: true,
          source: 'credential_template',
          source_field: 'ibmcloud_api_key',
        },
        {
          name: 'ibmcloud_cluster_region',
          type: 'string',
          description: 'IBM Cloud region',
          example: 'us-south',
          default: null,
          module_path: 'blueprint',
          module_name: 'BIG-IP Next for Kubernetes on IBM ROKS Single NIC',
          required: true,
          source: 'project',
          source_field: 'region',
        },
        {
          name: 'ibmcloud_resource_group',
          type: 'string',
          description: 'IBM Cloud resource group',
          example: 'default',
          default: 'default',
          module_path: 'blueprint',
          module_name: 'BIG-IP Next for Kubernetes on IBM ROKS Single NIC',
          required: false,
          source: 'credential_template',
          source_field: 'ibmcloud_resource_group',
        },
      ],
      total_required: 2,
      total_optional: 1,
      missing_modules: [],
      summary: [{ label: 'Input guidance', value: 'Provide IBM Cloud and cluster details.' }],
    });
  }),

  http.post('*/api/stacks/releases/:id/projects', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      success: true,
      project_id: 77,
      project_name: body.name || 'Imported Blueprint Project',
      blueprint_release_id: Number(31),
      module_count: 2,
      created_module_ids: [201, 202],
      message: 'Created project from imported blueprint release.',
    });
  }),

  http.post('*/api/project-modules/:id/apply', ({ params }) => {
    return HttpResponse.json({
      task_id: `task-apply-${params.id}`,
      module_id: Number(params.id),
      status: 'queued',
    });
  }),

  http.post('*/api/stacks/projects/:projectId/releases/:releaseId', async ({ params }) => {
    return HttpResponse.json({
      success: true,
      project_id: Number(params.projectId),
      project_name: 'Existing Project',
      blueprint_release_id: Number(params.releaseId),
      module_count: 2,
      created_module_ids: [301, 302],
      message: "Added blueprint modules to project 'Existing Project'.",
    });
  }),

  // ========================================================================
  // Tasks
  // ========================================================================

  http.get('*/api/tasks', ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname !== '/api/tasks') return;
    const status = url.searchParams.get('status');
    if (status) {
      const filtered = mockTasks.tasks.filter((t) => t.status === status);
      return HttpResponse.json({ ...mockTasks, tasks: filtered, total: filtered.length });
    }
    return HttpResponse.json(mockTasks);
  }),

  http.get('*/api/tasks/:id', ({ params, request }) => {
    const url = new URL(request.url);
    if (url.pathname.split('/').length > 4) return;
    const task = mockTasks.tasks.find((t) => t.id === Number(params.id));
    if (!task) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json(task);
  }),

  http.post('*/api/tasks/:taskId/cancel', ({ params }) => {
    return HttpResponse.json({ success: true, message: 'Task cancelled', task_id: Number(params.taskId) });
  }),

  http.get('*/api/tasks/stats/summary', () => {
    return HttpResponse.json(mockTaskStats);
  }),

  http.delete('*/api/tasks/cleanup', () => {
    return HttpResponse.json({ success: true, deleted_count: 15, cutoff_date: '2026-01-01T00:00:00Z' });
  }),

  // ========================================================================
  // K8s Clusters
  // ========================================================================

  http.get('*/api/k8s/clusters', ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname !== '/api/k8s/clusters') return;
    return HttpResponse.json(mockClusters);
  }),

  http.get('*/api/k8s/clusters/:id', ({ params, request }) => {
    const url = new URL(request.url);
    if (url.pathname.split('/').length > 5) return;
    const cluster = mockClusters.clusters.find((c) => c.id === Number(params.id));
    if (!cluster) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json(cluster);
  }),

  http.post('*/api/projects/:projectId/k8s/clusters', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ id: 10, ...body, status: 'connecting', created_at: new Date().toISOString() });
  }),

  http.put('*/api/k8s/clusters/:clusterId', async ({ request, params }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ id: Number(params.clusterId), ...body, updated_at: new Date().toISOString() });
  }),

  http.delete('*/api/k8s/clusters/:clusterId', () => {
    return HttpResponse.json({ success: true, message: 'Cluster deleted' });
  }),

  http.post('*/api/k8s/clusters/:clusterId/test', () => {
    return HttpResponse.json({ success: true, message: 'Connection successful', version: '1.28' });
  }),

  http.post('*/api/k8s/clusters/:clusterId/scan', () => {
    return HttpResponse.json({ success: true, resources_found: 42, namespaces: 5 });
  }),

  // Project clusters
  http.get('*/api/projects/:projectId/k8s/clusters', () => {
    return HttpResponse.json({ clusters: mockClusters.clusters, count: mockClusters.count });
  }),

  // K8s Resources
  http.get('*/api/k8s/clusters/:id/resources/:type', () => {
    return HttpResponse.json(mockK8sResources);
  }),

  http.get('*/api/k8s/clusters/:id/resource-summary', ({ params, request }) => {
    const url = new URL(request.url);
    return HttpResponse.json({
      cluster_id: Number(params.id),
      namespace: url.searchParams.get('namespace'),
      summary: {},
    });
  }),

  http.get('*/api/k8s/resource-types', () => {
    return HttpResponse.json([
      'pods', 'deployments', 'services', 'configmaps', 'secrets',
      'ingresses', 'namespaces', 'nodes', 'persistentvolumeclaims',
    ]);
  }),

  http.get('*/api/k8s/clusters/:clusterId/namespaces', () => {
    return HttpResponse.json(['default', 'kube-system', 'ingress-nginx', 'cert-manager']);
  }),

  // ========================================================================
  // Helm
  // ========================================================================

  http.get('*/api/k8s/:clusterId/helm/releases', ({ request }) => {
    const url = new URL(request.url);
    if (!url.pathname.endsWith('/helm/releases')) return;
    return HttpResponse.json(mockHelmReleases);
  }),

  http.get('*/api/k8s/:clusterId/helm/releases/:releaseName', ({ params, request }) => {
    const url = new URL(request.url);
    // Don't match deeper paths like /releases/name/history
    const parts = url.pathname.split('/');
    if (parts[parts.length - 2] !== 'releases') return;
    const release = mockHelmReleases.find((r) => r.name === params.releaseName);
    if (!release) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json(release);
  }),

  http.post('*/api/k8s/:clusterId/helm/install', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      success: true,
      release: body.release_name || 'new-release',
      message: 'Chart installed successfully',
    });
  }),

  http.post('*/api/k8s/:clusterId/helm/upgrade', () => {
    return HttpResponse.json({ success: true, message: 'Release upgraded successfully' });
  }),

  http.put('*/api/k8s/:clusterId/helm/releases/:releaseName/upgrade', () => {
    return HttpResponse.json({ success: true, message: 'Release upgraded successfully' });
  }),

  http.post('*/api/k8s/:clusterId/helm/rollback', () => {
    return HttpResponse.json({ success: true, message: 'Rollback completed successfully' });
  }),

  http.delete('*/api/k8s/:clusterId/helm/releases/:name', () => {
    return HttpResponse.json({ success: true, message: 'Release uninstalled' });
  }),

  http.get('*/api/k8s/:clusterId/helm/releases/:releaseName/history', () => {
    return HttpResponse.json([
      { revision: 3, status: 'deployed', chart: 'ingress-nginx-4.9.0', updated: '2026-02-01T12:00:00Z', description: 'Upgrade complete' },
      { revision: 2, status: 'superseded', chart: 'ingress-nginx-4.8.0', updated: '2026-01-15T10:00:00Z', description: 'Upgrade complete' },
      { revision: 1, status: 'superseded', chart: 'ingress-nginx-4.7.0', updated: '2026-01-01T08:00:00Z', description: 'Install complete' },
    ]);
  }),

  http.get('*/api/k8s/:clusterId/helm/releases/:releaseName/values', () => {
    return HttpResponse.json({ controller: { replicaCount: 2, service: { type: 'LoadBalancer' } } });
  }),

  http.get('*/api/k8s/:clusterId/helm/releases/:releaseName/manifest', () => {
    return HttpResponse.json({ manifest: '---\napiVersion: apps/v1\nkind: Deployment\n...' });
  }),

  http.post('*/api/k8s/:clusterId/helm/releases/:releaseName/test', () => {
    return HttpResponse.json({ success: true, message: 'All tests passed', results: [] });
  }),

  // Helm Repositories
  http.get('*/api/helm/repositories', () => {
    return HttpResponse.json(mockHelmRepositories);
  }),

  http.post('*/api/helm/repositories', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ name: body.name, url: body.url, status: 'ready' });
  }),

  http.delete('*/api/helm/repositories', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ success: true, message: `Repository ${body.name} removed` });
  }),

  http.post('*/api/helm/repositories/update', () => {
    return HttpResponse.json({ success: true, message: 'All repositories updated' });
  }),

  // Helm Charts
  http.get('*/api/helm/charts/search', () => {
    return HttpResponse.json([
      { name: 'nginx', version: '15.4.0', description: 'NGINX web server', repo: 'bitnami' },
    ]);
  }),

  http.get('*/api/helm/charts/browse', () => {
    return HttpResponse.json([
      { name: 'nginx', version: '15.4.0', description: 'NGINX web server', repo: 'bitnami' },
      { name: 'postgresql', version: '15.0.0', description: 'PostgreSQL database', repo: 'bitnami' },
    ]);
  }),

  // ========================================================================
  // Stack Templates & Instances
  // ========================================================================

  http.get('*/api/stacks/templates', ({ request }) => {
    const url = new URL(request.url);
    if (!url.pathname.endsWith('/templates')) return;
    return HttpResponse.json(mockStackTemplates);
  }),

  http.get('*/api/stacks/templates/:slug', ({ params, request }) => {
    const url = new URL(request.url);
    const parts = url.pathname.split('/');
    if (parts.length > 5) return;
    const template = mockStackTemplates.find((t) => t.slug === params.slug || t.id === Number(params.slug));
    if (!template) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json(template);
  }),

  http.get('*/api/stacks/templates/:slug/preview', () => {
    return HttpResponse.json({ modules: mockModuleLibrary.slice(0, 2), estimated_time: '15 min', estimated_cost: '$150/mo' });
  }),

  http.get('*/api/stacks/templates/:slug/required-inputs', () => {
    return HttpResponse.json({ required: ['region', 'vpc_cidr'], optional: ['cluster_name'] });
  }),

  http.get('*/api/stacks/templates/:slug/check-prerequisites', () => {
    return HttpResponse.json({ satisfied: true, missing_secrets: [], missing_templates: [] });
  }),

  http.post('*/api/stacks/projects/:projectId/stacks', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ id: 10, ...body, status: 'created', created_at: new Date().toISOString() });
  }),

  http.get('*/api/stacks/projects/:projectId/stacks', () => {
    return HttpResponse.json(mockStackInstances);
  }),

  http.post('*/api/stacks/projects/:projectId/stacks/:stackId/deploy', () => {
    return HttpResponse.json({ status: 'deploying', message: 'Stack deployment started', current_step: 1, total_steps: 4, deployed_modules: [] });
  }),

  http.post('*/api/stacks/projects/:projectId/stacks/:stackId/run-deploy', () => {
    return HttpResponse.json({ status: 'queued', message: 'Stack deployment queued', queued_modules: 4, total_modules: 4 });
  }),

  http.get('*/api/stacks/projects/:projectId/stacks/:stackId/status', () => {
    return HttpResponse.json({ status: 'deployed', modules_deployed: 4, modules_total: 4, progress: 100 });
  }),

  http.delete('*/api/stacks/projects/:projectId/stacks/:stackId', () => {
    return HttpResponse.json({ status: 'destroying', message: 'Stack destruction started' });
  }),

  // ========================================================================
  // Operators
  // ========================================================================

  http.get('*/api/operators', ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname !== '/api/operators') return;
    return HttpResponse.json(mockOperators);
  }),

  http.get('*/api/operators/:operatorId', ({ params, request }) => {
    const url = new URL(request.url);
    if (url.pathname.split('/').length > 4) return;
    // Don't match specific sub-resources that have their own handlers
    const reserved = ['connectivity-modes', 'fleet-health'];
    if (reserved.includes(params.operatorId as string)) return;
    const op = mockOperators.find((o) => o.id === params.operatorId);
    if (!op) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json(op);
  }),

  http.delete('*/api/operators/:operatorId', () => {
    return HttpResponse.json({ success: true, message: 'Operator deleted' });
  }),

  http.post('*/api/operators/:operatorId/command', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ success: true, command_id: `cmd-${Date.now()}`, result: { action: body.action, status: 'completed' } });
  }),

  http.post('*/api/operators/:operatorId/link-cluster', () => {
    return HttpResponse.json({ success: true, message: 'Operator linked', operator: mockOperators[0] });
  }),

  http.post('*/api/operators/:operatorId/unlink-cluster', () => {
    return HttpResponse.json({ success: true, message: 'Operator unlinked', operator: { ...mockOperators[0], cluster_name: null } });
  }),

  // Connectivity
  http.get('*/api/operators/connectivity-modes', () => {
    return HttpResponse.json(mockConnectivityModes);
  }),

  http.post('*/api/operators/:operatorId/connectivity', () => {
    return HttpResponse.json({ operator: mockOperators[0], setup: { mode: 'websocket', instructions: [] } });
  }),

  http.post('*/api/operators/:operatorId/connectivity/preview', () => {
    return HttpResponse.json({ mode: 'websocket', instructions: ['Configure firewall rule'] });
  }),

  http.post('*/api/operators/:operatorId/reverse-tunnel/open', () => {
    return HttpResponse.json({ success: true, ssh_host: 'tunnel.example.com', remote_port: 8443, local_port: 8000, message: 'Tunnel opened' });
  }),

  // ========================================================================
  // SSH Credentials
  // ========================================================================

  http.get('*/api/ssh-credentials', ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname !== '/api/ssh-credentials') return;
    return HttpResponse.json([]);
  }),

  http.get('*/api/ssh-credentials/:id', () => {
    return new HttpResponse(null, { status: 404 });
  }),

  http.post('*/api/ssh-credentials/:id/test', ({ params }) => {
    return HttpResponse.json({
      success: true,
      message: 'SSH connection successful',
      credential_id: Number(params.id),
      host: '10.176.11.91',
      port: 22,
      auth_type: 'key',
    });
  }),

  // ========================================================================
  // Credential Templates
  // ========================================================================

  http.get('*/api/credential-templates', ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname !== '/api/credential-templates') return;
    return HttpResponse.json(mockCredentialTemplates);
  }),

  http.get('*/api/credential-templates/:id', ({ params, request }) => {
    const url = new URL(request.url);
    if (url.pathname.split('/').length > 4) return;
    const template = mockCredentialTemplates.find((t) => t.id === Number(params.id));
    if (!template) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json(template);
  }),

  http.post('*/api/credential-templates', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ id: 10, ...body, is_active: true, created_at: new Date().toISOString() });
  }),

  http.put('*/api/credential-templates/:id', async ({ request, params }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ id: Number(params.id), ...body, updated_at: new Date().toISOString() });
  }),

  http.delete('*/api/credential-templates/:id', () => {
    return HttpResponse.json({ success: true });
  }),

  http.post('*/api/credential-templates/:id/test', () => {
    return HttpResponse.json({ success: true, message: 'Credentials valid', account_id: '123456789012' });
  }),

  http.delete('*/api/blueprint-catalog/sources/:id', () => {
    return HttpResponse.json({ success: true, message: 'Blueprint source deleted' });
  }),

  // ========================================================================
  // Drift Detection
  // ========================================================================

  http.get('*/api/drift/summary', () => {
    return HttpResponse.json(mockDriftSummary);
  }),

  http.get('*/api/drift/recent', () => {
    return HttpResponse.json(mockDriftSummary.drifted_modules);
  }),

  http.get('*/api/drift/stats', () => {
    return HttpResponse.json(mockDriftStats);
  }),

  http.get('*/api/projects/:projectId/drift/settings', () => {
    return HttpResponse.json(mockDriftSettings);
  }),

  http.put('*/api/projects/:projectId/drift/settings', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ ...mockDriftSettings, ...body });
  }),

  http.post('*/api/projects/:projectId/drift/enable', () => {
    return HttpResponse.json({ success: true, message: 'Drift detection enabled' });
  }),

  http.post('*/api/projects/:projectId/drift/disable', () => {
    return HttpResponse.json({ success: true, message: 'Drift detection disabled' });
  }),

  http.post('*/api/projects/:projectId/drift/check-now', () => {
    return HttpResponse.json({ success: true, task_id: 'drift-check-001' });
  }),

  http.get('*/api/projects/:projectId/drift/checks', () => {
    return HttpResponse.json(mockDriftChecks);
  }),

  http.post('*/api/project-modules/:moduleId/drift/check-now', () => {
    return HttpResponse.json({ success: true, task_id: 'drift-module-001' });
  }),

  // ========================================================================
  // Alert Channels
  // ========================================================================

  http.get('*/api/alert-channels', ({ request }) => {
    const url = new URL(request.url);
    if (!url.pathname.endsWith('/alert-channels')) return;
    return HttpResponse.json(mockAlertChannels);
  }),

  http.get('*/api/alert-channels/:id', ({ params, request }) => {
    const url = new URL(request.url);
    if (url.pathname.split('/').length > 4) return;
    const channel = mockAlertChannels.find((c) => c.id === Number(params.id));
    if (!channel) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json(channel);
  }),

  http.post('*/api/alert-channels', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ id: 10, ...body, enabled: true, created_at: new Date().toISOString() });
  }),

  http.put('*/api/alert-channels/:id', async ({ request, params }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ id: Number(params.id), ...body, updated_at: new Date().toISOString() });
  }),

  http.delete('*/api/alert-channels/:id', () => {
    return HttpResponse.json({ success: true });
  }),

  http.post('*/api/alert-channels/:id/test', () => {
    return HttpResponse.json({ success: true, message: 'Test alert sent', duration_ms: 250 });
  }),

  http.post('*/api/alert-channels/:id/toggle', ({ params }) => {
    return HttpResponse.json({ success: true, enabled: true, message: `Alert channel ${params.id} toggled` });
  }),

  http.get('*/api/alert-channels/history/recent', () => {
    return HttpResponse.json(mockAlertHistory);
  }),

  // ========================================================================
  // Snapshots
  // ========================================================================

  http.get('*/api/projects/:projectId/snapshots', () => {
    return HttpResponse.json(mockSnapshots);
  }),

  http.post('*/api/projects/:projectId/snapshots', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ id: 10, ...body, snapshot_type: 'manual', created_at: new Date().toISOString(), created_by: 'admin' });
  }),

  http.get('*/api/snapshots/:snapshotId', ({ params }) => {
    if (Number(params.snapshotId) === 1) return HttpResponse.json(mockSnapshotDetail);
    return new HttpResponse(null, { status: 404 });
  }),

  http.post('*/api/snapshots/:snapshotId/restore', () => {
    return HttpResponse.json({ success: true, message: 'Snapshot restored', restored_modules: 2 });
  }),

  http.post('*/api/snapshots/diff', () => {
    return HttpResponse.json({ differences: [{ key: 'vpc.cidr_block', snapshot_a: '10.0.0.0/16', snapshot_b: '10.1.0.0/16' }], identical_count: 5 });
  }),

  http.delete('*/api/snapshots/:snapshotId', () => {
    return HttpResponse.json({ message: 'Snapshot deleted', id: 1 });
  }),

  // ========================================================================
  // Fleet
  // ========================================================================

  http.get('*/api/operators/fleet-health', () => {
    return HttpResponse.json(mockFleetHealth);
  }),

  http.get('*/api/fleet/targets', ({ request }) => {
    const url = new URL(request.url);
    // Batch rollup endpoint shares the /targets prefix but has a different path shape
    if (url.pathname !== '/api/fleet/targets') return;
    return HttpResponse.json(mockFleetTargets);
  }),

  http.get('*/api/fleet/targets/rollup', () => {
    return HttpResponse.json([
      {
        fleet_id: 1,
        member_count: 1,
        lifecycle: { new: 0, active: 1, decommissioned: 0 },
        unreachable_count: 0,
        compliant_count: 1,
        total_evaluated: 1,
        drift_count: 0,
        policy_count: 0,
        worst_state: 'ready',
        last_evaluated_at: null,
        active_run_count: 0,
        failed_run_count: 0,
        last_run_status: null,
        ops_state: 'green',
      },
    ]);
  }),

  http.post('*/api/operators/fleet/compare', () => {
    return HttpResponse.json(mockFleetCompareResult);
  }),

  // ========================================================================
  // Config Promotion
  // ========================================================================

  http.post('*/api/config/promote', () => {
    return HttpResponse.json(mockPromoteResponse);
  }),

  http.get('*/api/config/promotions', () => {
    return HttpResponse.json(mockPromotionHistory);
  }),

  // ========================================================================
  // Runbooks
  // ========================================================================

  http.get('*/api/runbooks', () => {
    return HttpResponse.json(mockRunbooks);
  }),

  http.post('*/api/runbooks/:runbookId/run', () => {
    return HttpResponse.json(mockRunbookResult);
  }),

  // ========================================================================
  // System
  // ========================================================================

  http.get('*/api/system/health', () => {
    return HttpResponse.json(mockSystemHealth);
  }),

  http.get('*/api/system/defaults', () => {
    return HttpResponse.json(mockSystemDefaults);
  }),

  http.get('*/api/system/queue-metrics', () => {
    return HttpResponse.json(mockQueueMetrics);
  }),

  http.get('*/api/system/performance', () => {
    return HttpResponse.json(mockPerformanceMetrics);
  }),

  http.get('*/api/system/errors', () => {
    return HttpResponse.json(mockRecentErrors);
  }),

  http.get('*/api/system/database/stats', () => {
    return HttpResponse.json(mockDatabaseStats);
  }),

  http.post('*/api/system/database/cleanup', () => {
    return HttpResponse.json({ success: true, deleted_count: 150, cleanup_type: 'tasks', cutoff_date: '2026-01-01T00:00:00Z' });
  }),

  http.post('*/api/system/database/vacuum', () => {
    return HttpResponse.json({ success: true, message: 'Database vacuumed', freed_mb: 12 });
  }),

  http.get('*/api/system/containers/status', () => {
    return HttpResponse.json(mockContainerStatus);
  }),

  http.post('*/api/system/containers/restart', async ({ request }) => {
    const body = (await request.json()) as { services?: string[] };
    const services = body.services || ['backend'];
    return HttpResponse.json({
      message: 'Restart initiated',
      results: services.map((s) => ({ service: s, status: 'restarted' })),
      note: 'Services will be back shortly',
    });
  }),

  http.get('*/api/system/version', () => {
    return HttpResponse.json(mockSystemVersion);
  }),

  http.post('*/api/system/upgrade', () => {
    return HttpResponse.json({ status: 'upgrading', message: 'Upgrade started: 2.10.49 -> 2.10.50', old_version: '2.10.49', new_version: '2.10.50' });
  }),

  http.get('*/api/system/upgrade/status', () => {
    return HttpResponse.json({ status: 'none', log: [] });
  }),

  http.get('*/api/system/upgrade/verify', () => {
    return HttpResponse.json({
      verdict: 'healthy',
      checks: {
        version: { status: 'pass', current: '2.10.50', expected: '2.10.50' },
        database: { status: 'pass' },
        redis: { status: 'pass' },
        celery: { status: 'pass', workers: 2 },
        migrations: { status: 'pass', current_revision: 'abc123' },
      },
      timestamp: new Date().toISOString(),
    });
  }),

  // ========================================================================
  // Audit
  // ========================================================================

  http.get('*/api/audit', () => {
    return HttpResponse.json({ entries: mockAuditEntries, total: mockAuditEntries.length });
  }),

  // ========================================================================
  // Notifications
  // ========================================================================

  http.post('*/api/notifications', () => {
    return HttpResponse.json({ success: true });
  }),

  http.get('*/api/notifications', () => {
    // Backend returns a bare array of notifications.
    return HttpResponse.json([]);
  }),

  http.get('*/api/notifications/unread-count', () => {
    return HttpResponse.json({ count: 0 });
  }),

  // ========================================================================
  // Background polling endpoints used by AppShell. The Header opens a
  // notifications popover that triggers /api/notifications on first paint;
  // the sidebar fetches process metrics + a project-modules summary. Without
  // these handlers MSW lets the requests passthrough to localhost:3000 which
  // fails with ECONNREFUSED and stalls renders during component tests.
  // ========================================================================

  http.get('*/api/system/process-metrics', () => {
    return HttpResponse.json({
      processes: [],
      total_cpu_percent: 0,
      total_memory_mb: 0,
    });
  }),

  http.get('*/api/projects/modules/all', () => {
    return HttpResponse.json({
      items: [],
      pagination: {
        page: 1,
        page_size: 200,
        total_items: 0,
        total_pages: 0,
        has_next: false,
        has_prev: false,
      },
    });
  }),

  http.post('*/api/ssh-credentials/test-all', () => {
    return HttpResponse.json({ results: [] });
  }),

  // BNKHealthDashboard polls per-cluster licensing status on mount; without
  // this handler tests fall through to localhost:3000 and spam ECONNREFUSED.
  http.get('*/api/licensing/:clusterId/status', () => {
    return HttpResponse.json({ success: true });
  }),
];
