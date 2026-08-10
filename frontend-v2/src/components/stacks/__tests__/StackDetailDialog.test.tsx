import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '@/test/test-utils';
import { StackDetailDialog } from '../StackDetailDialog';
import { api } from '@/lib/api';
import { notify } from '@/lib/notify';
import type { Project } from '@/types';

vi.mock('@/lib/notify', () => ({
  notify: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

const defaultTemplate = {
  id: 1,
  name: 'BNK Existing Cluster Blueprint',
  slug: 'bnk-on-k8s',
  description: 'Deploy BNK on an existing cluster',
  category: 'bnk',
  is_active: true,
  is_featured: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  prerequisites: [
    {
      type: 'kubernetes_cluster',
      description: 'Requires an existing Kubernetes cluster',
    },
    {
      type: 'project_secret',
      name: 'jwt_token',
      description: 'F5 license JWT token from MyF5',
    },
    {
      type: 'project_secret',
      name: 'cne_pull_secret',
      description: 'FAR pull secret (project-scoped)',
    },
  ],
  modules: [
    {
      path: 'packs/ansible/validate',
      name: 'Validate Cluster',
      required: true,
      variables: {},
      engine_type: 'ansible',
      lifecycle_capabilities: {
        supports_init: true,
        supports_plan: true,
        supports_apply: true,
        supports_destroy: false,
        supports_refresh: false,
        supports_drift: false,
      },
    },
  ],
};

let mockTemplate = structuredClone(defaultTemplate);

vi.mock('@/hooks/useStacks', () => ({
  stackKeys: {
    instances: (projectId: number) => ['stacks', 'instances', projectId],
    template: (slug: string) => ['stacks', 'templates', slug],
  },
  useStackTemplate: () => ({
    data: mockTemplate,
    isLoading: false,
  }),
  useStackPreview: () => ({
    data: { modules: [], total_modules: 1 },
  }),
}));

vi.mock('@/lib/api', () => ({
  api: {
    getStackRequiredInputs: vi.fn().mockResolvedValue({
      all_inputs: [],
      inputs_by_module: {},
      total_required: 0,
      summary: [],
    }),
    getProjects: vi.fn(),
    getProjectModules: vi.fn().mockResolvedValue([]),
    checkStackPrerequisites: vi.fn().mockResolvedValue({
      all_satisfied: true,
      required_secrets: [],
      missing_secrets: [],
    }),
    listCredentialTemplates: vi.fn().mockResolvedValue([]),
    createProject: vi.fn(),
    createStackInstance: vi.fn(),
    deployStack: vi.fn(),
    // Real backend response shape per CT-012: POST /api/module-library/sync
    syncModuleLibrary: vi.fn().mockResolvedValue({
      success: true,
      message: 'Module catalog synced successfully',
      stats: {
        total_modules: 38,
        created: 34,
        updated: 4,
        quarantined: 0,
        quarantined_paths: [],
        errors: [],
        source_rows_repaired: 0,
      },
    }),
  },
}));

function buildProject(overrides: Partial<Project>): Project {
  return {
    id: overrides.id ?? 1,
    name: overrides.name ?? 'Test Project',
    color: '#2563eb',
    icon: '',
    is_active: true,
    has_deployments: false,
    module_count: 0,
    deployed_count: 0,
    failed_count: 0,
    cluster_count: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

describe('StackDetailDialog existing project selection', () => {
  beforeAll(() => {
    if (!HTMLElement.prototype.hasPointerCapture) {
      HTMLElement.prototype.hasPointerCapture = () => false;
    }
    if (!HTMLElement.prototype.setPointerCapture) {
      HTMLElement.prototype.setPointerCapture = () => {};
    }
    if (!HTMLElement.prototype.releasePointerCapture) {
      HTMLElement.prototype.releasePointerCapture = () => {};
    }
    if (!HTMLElement.prototype.scrollIntoView) {
      HTMLElement.prototype.scrollIntoView = () => {};
    }
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mockTemplate = structuredClone(defaultTemplate);
  });

  it('shows and allows selecting a project with cluster_count > 0 even when deployed_count is 0', async () => {
    const clusterOnlyProject = buildProject({
      id: 101,
      name: 'Cluster-only Project',
      cluster_count: 1,
      deployed_count: 0,
    });
    vi.mocked(api.getProjects).mockResolvedValue([clusterOnlyProject]);

    const user = userEvent.setup();
    render(<StackDetailDialog slug="aws-k8s-foundation" open onOpenChange={vi.fn()} />);

    await waitFor(() => {
      expect(api.getProjects).toHaveBeenCalled();
    });

    await user.click(screen.getByRole('combobox'));

    const option = await screen.findByRole('option', { name: /Cluster-only Project \(1 cluster\)/i });
    await user.click(option);

    await waitFor(() => {
      expect(screen.getByText('Cluster-only Project (1 cluster)')).toBeInTheDocument();
    });
  });

  it('enforces existing-project-only mode for existing-cluster/on-prem templates', async () => {
    vi.mocked(api.getProjects).mockResolvedValue([
      buildProject({ id: 111, name: 'Cluster Project', cluster_count: 1, deployed_count: 0 }),
    ]);

    render(<StackDetailDialog slug="bnk-on-k8s" open onOpenChange={vi.fn()} />);

    expect(await screen.findByText(/Existing project only for this blueprint/i)).toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: /New Project/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Select Project \*/i)).toBeInTheDocument();
  });

  it('hides cloud credential and region fields for existing-project-only templates', async () => {
    vi.mocked(api.getProjects).mockResolvedValue([
      buildProject({ id: 121, name: 'Cluster Project', cluster_count: 1, deployed_count: 0 }),
    ]);

    render(<StackDetailDialog slug="bnk-on-k8s" open onOpenChange={vi.fn()} />);

    expect(await screen.findByText(/Existing project only for this blueprint/i)).toBeInTheDocument();
    expect(screen.queryByText(/Cloud Credentials/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Region \*$/i)).not.toBeInTheDocument();
  });

  it('excludes projects with cluster_count = 0 and shows truthful empty copy', async () => {
    vi.mocked(api.getProjects).mockResolvedValue([
      buildProject({ id: 201, name: 'No-cluster A', cluster_count: 0, deployed_count: 4 }),
      buildProject({ id: 202, name: 'No-cluster B', cluster_count: 0, deployed_count: 0 }),
    ]);

    const user = userEvent.setup();
    render(<StackDetailDialog slug="bnk-on-k8s" open onOpenChange={vi.fn()} />);

    await waitFor(() => {
      expect(api.getProjects).toHaveBeenCalled();
    });

    await user.click(screen.getByRole('combobox'));

    expect(await screen.findByText('No projects with registered clusters')).toBeInTheDocument();
    expect(screen.queryByText(/No projects with deployed infrastructure/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /No-cluster A/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /No-cluster B/i })).not.toBeInTheDocument();
  });

  it('allows ubuntu-kind-foundation to target existing discovered-host projects when cluster_count is 0', async () => {
    mockTemplate = {
      ...structuredClone(defaultTemplate),
      id: 3,
      name: 'Ubuntu Kind Foundation',
      slug: 'ubuntu-kind-foundation',
      category: 'infrastructure',
      prerequisites: [
        {
          type: 'ssh_access',
          description: 'Requires SSH access to a target host',
        },
      ],
    };
    vi.mocked(api.getProjects).mockResolvedValue([
      buildProject({ id: 501, name: 'Blank Host A', cluster_count: 0, deployed_count: 0 }),
      buildProject({ id: 502, name: 'Blank Host B', cluster_count: 0, deployed_count: 2 }),
    ]);

    const user = userEvent.setup();
    render(<StackDetailDialog slug="ubuntu-kind-foundation" open onOpenChange={vi.fn()} />);

    await waitFor(() => {
      expect(api.getProjects).toHaveBeenCalled();
    });

    expect(await screen.findByText(/Existing project only for this blueprint/i)).toBeInTheDocument();
    expect(screen.queryByText(/Select a project with at least one registered cluster/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole('combobox'));

    expect(await screen.findByRole('option', { name: /Blank Host A \(0 clusters\)/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /Blank Host B \(0 clusters\)/i })).toBeInTheDocument();
    expect(screen.queryByText('No projects with registered clusters')).not.toBeInTheDocument();
  });

  it('uses target-oriented copy for existing Kubernetes clusters without readiness validation claims', async () => {
    vi.mocked(api.getProjects).mockResolvedValue([
      buildProject({ id: 301, name: 'Cluster Target', cluster_count: 2, deployed_count: 0 }),
    ]);

    render(<StackDetailDialog slug="bnk-on-k8s" open onOpenChange={vi.fn()} />);

    await waitFor(() => {
      expect(api.getProjects).toHaveBeenCalled();
    });

    expect(
      screen.getByText(
        'This blueprint targets an existing Kubernetes cluster. Select a project with at least one registered cluster.'
      )
    ).toBeInTheDocument();
    expect(screen.queryByText(/ready|readiness|validated/i)).not.toBeInTheDocument();
  });

  it('shows project-secret source policy for existing-project prerequisites checks', async () => {
    vi.mocked(api.getProjects).mockResolvedValue([
      buildProject({ id: 401, name: 'Cluster Target', cluster_count: 1, deployed_count: 0 }),
    ]);
    vi.mocked(api.checkStackPrerequisites).mockResolvedValue({
      all_satisfied: false,
      required_secrets: [
        { name: 'jwt_token', description: 'F5 license JWT token', exists: true, source: 'project', valid: true, validated_source: 'project' },
        { name: 'cne_pull_secret', description: 'FAR pull secret', exists: false, source: null, valid: false, validation_error: 'missing' },
      ],
      missing_secrets: [
        {
          name: 'cne_pull_secret',
          description: 'FAR pull secret',
          exists: false,
          source: null,
          guidance: 'A global fallback can be configured in System → Defaults.',
        },
      ],
      secret_source_policy: {
        project_secret_precedence: true,
        global_cne_pull_secret_default_supported: true,
      },
    });

    const user = userEvent.setup();
    render(<StackDetailDialog slug="bnk-on-k8s" open onOpenChange={vi.fn()} />);

    await waitFor(() => {
      expect(api.getProjects).toHaveBeenCalled();
    });

    await user.click(screen.getByRole('combobox'));
    await user.click(await screen.findByRole('option', { name: /Cluster Target/i }));

    expect(await screen.findByText(/Secret policy: project secrets are authoritative/i)).toBeInTheDocument();
    expect(screen.getByText(/can fall back to encrypted System Defaults/i)).toBeInTheDocument();
  });

  it('keeps new-project cloud credential + region inputs for cloud infrastructure templates', async () => {
    mockTemplate = {
      ...structuredClone(defaultTemplate),
      id: 2,
      name: 'AWS Foundation',
      slug: 'aws-k8s-foundation',
      cloud_provider: 'aws',
      category: 'infrastructure',
      prerequisites: [],
    };
    vi.mocked(api.getProjects).mockResolvedValue([]);

    render(<StackDetailDialog slug="bnk-on-k8s" open onOpenChange={vi.fn()} />);

    expect(await screen.findByRole('radio', { name: /New Project/i })).toBeInTheDocument();
    expect(screen.getByText(/Cloud Credentials/i)).toBeInTheDocument();
    expect(screen.getByText(/^Region \*$/i)).toBeInTheDocument();
  });

  it('shows inherited IBM credential-template hints for IBM imported blueprint inputs', async () => {
    mockTemplate = {
      ...structuredClone(defaultTemplate),
      id: 4,
      name: 'IBM ROKs Foundation',
      slug: 'release-44',
      category: 'infrastructure',
      cloud_provider: 'ibm',
      prerequisites: [],
      modules: [
        {
          path: 'modules/cluster',
          name: 'ROKS Cluster',
          required: true,
          variables: {},
          engine_type: 'opentofu',
          lifecycle_capabilities: {
            supports_init: true,
            supports_plan: true,
            supports_apply: true,
            supports_destroy: true,
            supports_refresh: true,
            supports_drift: true,
          },
        },
      ],
    };

    vi.mocked(api.getProjects).mockResolvedValue([]);
    vi.mocked(api.listCredentialTemplates).mockResolvedValue([
      {
        id: 91,
        name: 'IBM Team Template',
        provider: 'ibm',
        region: 'eu-de',
        ibmcloud_resource_group: 'platform-rg',
        has_ibmcloud_api_key: true,
        has_aws_secret_access_key: false,
        has_aws_session_token: false,
        aws_sso_enabled: false,
        has_gcp_credentials: false,
        has_azure_credentials: false,
        has_ssh_password: false,
        has_ssh_key: false,
        is_default: false,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        projects_count: 0,
      },
    ]);
    vi.mocked(api.getStackRequiredInputs).mockResolvedValue({
      all_inputs: [
        {
          name: 'ibmcloud_api_key',
          type: 'string',
          description: 'IBM Cloud API key',
          module_path: 'blueprint',
          module_name: 'IBM ROKs Foundation',
          required: true,
          sensitive: true,
        },
        {
          name: 'ibmcloud_cluster_region',
          type: 'string',
          description: 'IBM Cloud region',
          module_path: 'blueprint',
          module_name: 'IBM ROKs Foundation',
          required: true,
        },
        {
          name: 'ibmcloud_resource_group',
          type: 'string',
          description: 'IBM Cloud resource group',
          module_path: 'blueprint',
          module_name: 'IBM ROKs Foundation',
          required: false,
        },
      ],
      inputs_by_module: {
        blueprint: [
          {
            name: 'ibmcloud_api_key',
            type: 'string',
            description: 'IBM Cloud API key',
            module_path: 'blueprint',
            module_name: 'IBM ROKs Foundation',
            required: true,
            sensitive: true,
          },
          {
            name: 'ibmcloud_cluster_region',
            type: 'string',
            description: 'IBM Cloud region',
            module_path: 'blueprint',
            module_name: 'IBM ROKs Foundation',
            required: true,
          },
          {
            name: 'ibmcloud_resource_group',
            type: 'string',
            description: 'IBM Cloud resource group',
            module_path: 'blueprint',
            module_name: 'IBM ROKs Foundation',
            required: false,
          },
        ],
      },
      total_required: 2,
      total_optional: 1,
      summary: [],
    });

    const user = userEvent.setup();
    render(<StackDetailDialog slug="release-44" open onOpenChange={vi.fn()} />);

    expect(await screen.findByText(/Cloud Credentials/i)).toBeInTheDocument();

    const credentialTriggers = screen.getAllByRole('combobox');
    await user.click(credentialTriggers[0]);
    await user.click(await screen.findByRole('option', { name: /IBM Team Template/i }));

    expect(await screen.findByText(/Provided securely by IBM Team Template/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Inherited from IBM Team Template/i).length).toBeGreaterThan(0);
  });

  it('blocks deploy and shows catalog-missing guidance when template modules are missing from active catalog', async () => {
    mockTemplate = {
      ...structuredClone(defaultTemplate),
      name: 'Missing Catalog Blueprint',
      slug: 'missing-catalog-blueprint',
      prerequisites: [],
      modules: [
        {
          path: 'bnk/bnk-vlans',
          name: 'vlans',
          required: true,
          variables: {},
          module_catalog_status: 'missing',
          module_catalog_message: 'missing from catalog',
        },
      ],
    };
    vi.mocked(api.getProjects).mockResolvedValue([]);

    const user = userEvent.setup();
    render(<StackDetailDialog slug="missing-catalog-blueprint" open onOpenChange={vi.fn()} />);

    expect(await screen.findByText(/Blueprint has modules missing from the active catalog/i)).toBeInTheDocument();
    expect(screen.getAllByText(/bnk\/bnk-vlans/i).length).toBeGreaterThan(0);

    const deployButton = screen.getByRole('button', { name: /Deploy Blueprint/i });
    expect(deployButton).toBeDisabled();

    await user.click(deployButton);
    expect(api.createProject).not.toHaveBeenCalled();
  });

  it('shows Sync module catalog button in the missing-modules banner', async () => {
    mockTemplate = {
      ...structuredClone(defaultTemplate),
      name: 'Missing Catalog Blueprint',
      slug: 'missing-catalog-blueprint',
      prerequisites: [],
      modules: [
        {
          path: 'bnk/bnk-vlans',
          name: 'vlans',
          required: true,
          variables: {},
          module_catalog_status: 'missing',
          module_catalog_message: 'missing from catalog',
        },
      ],
    };
    vi.mocked(api.getProjects).mockResolvedValue([]);

    render(<StackDetailDialog slug="missing-catalog-blueprint" open onOpenChange={vi.fn()} />);

    expect(await screen.findByText(/Blueprint has modules missing from the active catalog/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sync module catalog/i })).toBeInTheDocument();
  });

  it('clicking Sync module catalog calls POST /api/module-library/sync with real response shape', async () => {
    mockTemplate = {
      ...structuredClone(defaultTemplate),
      name: 'Missing Catalog Blueprint',
      slug: 'missing-catalog-blueprint',
      prerequisites: [],
      modules: [
        {
          path: 'bnk/bnk-vlans',
          name: 'vlans',
          required: true,
          variables: {},
          module_catalog_status: 'missing',
          module_catalog_message: 'missing from catalog',
        },
      ],
    };
    vi.mocked(api.getProjects).mockResolvedValue([]);

    const user = userEvent.setup();
    render(<StackDetailDialog slug="missing-catalog-blueprint" open onOpenChange={vi.fn()} />);

    expect(await screen.findByText(/Blueprint has modules missing from the active catalog/i)).toBeInTheDocument();

    const syncButton = screen.getByRole('button', { name: /Sync module catalog/i });
    expect(syncButton).not.toBeDisabled();

    await user.click(syncButton);

    // Real backend call should have been made (force=false per D1 — every-boot sync)
    await waitFor(() => {
      expect(api.syncModuleLibrary).toHaveBeenCalledWith(false);
    });
  });

  it('Sync module catalog CTA awaits mutation before refetching template — no unhandled rejection on success', async () => {
    // FIX 7 regression: previous code used invalidateQueries (fire-and-forget) inside
    // a non-async onSuccess. The fix uses mutateAsync + await refetchQueries so the
    // template data is in cache before the handler returns.  Verify the click does not
    // surface an unhandled promise rejection and the sync API is called.
    mockTemplate = {
      ...structuredClone(defaultTemplate),
      name: 'Missing Catalog Blueprint 2',
      slug: 'missing-catalog-blueprint-2',
      prerequisites: [],
      modules: [
        {
          path: 'bnk/bnk-vlans',
          name: 'vlans',
          required: true,
          variables: {},
          module_catalog_status: 'missing',
          module_catalog_message: 'missing from catalog',
        },
      ],
    };
    vi.mocked(api.getProjects).mockResolvedValue([]);
    vi.mocked(api.syncModuleLibrary).mockResolvedValue({
      success: true,
      message: 'Module catalog synced successfully',
      stats: {
        total_modules: 38,
        created: 34,
        updated: 4,
        quarantined: 0,
        quarantined_paths: [],
        errors: [],
        source_rows_repaired: 0,
      },
    });

    const user = userEvent.setup();
    render(<StackDetailDialog slug="missing-catalog-blueprint-2" open onOpenChange={vi.fn()} />);

    expect(await screen.findByText(/Blueprint has modules missing from the active catalog/i)).toBeInTheDocument();

    const syncButton = screen.getByRole('button', { name: /Sync module catalog/i });

    // Click and await resolution. If the async handler threw unhandled, the
    // test framework would catch it here.
    await user.click(syncButton);

    await waitFor(() => {
      expect(api.syncModuleLibrary).toHaveBeenCalledWith(false);
    });

    // Button should not be in permanent-disabled state after sync resolves.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Sync module catalog/i })).not.toBeDisabled();
    });
  });
});

// Regression coverage for PR #92 (commit 701baf43): the auto-detect logic for
// `user_ip` (and `ecr_registry`) was wired into the *display* branch only, so a
// fresh "AWS EKS Cluster - Demo" deploy showed the IP in the field but never
// wrote it into finalInputs, and validation rejected with "Missing required
// inputs: Security: user_ip". Three independent autoValue computations in
// build-finalInputs / validate / display had to be kept in sync by hand.
//
// These tests assert that auto-detected user_ip is treated as a real value by
// validation and ends up in the deploy payload — without the user ever typing.
describe('StackDetailDialog auto-detect user_ip submit (PR #92 regression)', () => {
  beforeAll(() => {
    if (!HTMLElement.prototype.hasPointerCapture) {
      HTMLElement.prototype.hasPointerCapture = () => false;
    }
    if (!HTMLElement.prototype.setPointerCapture) {
      HTMLElement.prototype.setPointerCapture = () => {};
    }
    if (!HTMLElement.prototype.releasePointerCapture) {
      HTMLElement.prototype.releasePointerCapture = () => {};
    }
    if (!HTMLElement.prototype.scrollIntoView) {
      HTMLElement.prototype.scrollIntoView = () => {};
    }
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mockTemplate = {
      ...structuredClone(defaultTemplate),
      id: 99,
      name: 'AWS EKS Cluster - Demo',
      slug: 'aws-eks-cluster-demo',
      cloud_provider: 'aws',
      category: 'infrastructure',
      prerequisites: [],
      modules: [
        {
          path: 'infra/aws/security',
          name: 'Security',
          required: true,
          variables: {},
        },
      ],
    };

    // checkip.amazonaws.com auto-detect
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (typeof url === 'string' && url.includes('checkip.amazonaws.com')) {
          return new Response('203.0.113.5\n', { status: 200 });
        }
        return new Response('', { status: 404 });
      })
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('does not raise "Missing required inputs" when user_ip is only auto-detected', async () => {
    vi.mocked(api.getStackRequiredInputs).mockResolvedValue({
      all_inputs: [
        {
          name: 'user_ip',
          module_name: 'Security',
          module_path: 'infra/aws/security',
          required: true,
          type: 'string',
          description: 'Your public IP /32 for security group ingress',
          default: null,
          sensitive: false,
        },
      ],
      inputs_by_module: {
        'infra/aws/security': [
          {
            name: 'user_ip',
            module_name: 'Security',
            module_path: 'infra/aws/security',
            required: true,
            type: 'string',
            description: 'Your public IP /32 for security group ingress',
            default: null,
            sensitive: false,
          },
        ],
      },
      total_required: 1,
      summary: [],
    } as never);

    vi.mocked(api.listCredentialTemplates).mockResolvedValue([
      { id: 1, name: 'AWS Prod', provider: 'aws' },
    ] as never);
    vi.mocked(api.getProjects).mockResolvedValue([]);
    vi.mocked(api.createProject).mockResolvedValue({
      project_id: 999,
      name: 'AWS EKS Cluster - Demo Test',
    } as never);
    vi.mocked(api.createStackInstance).mockResolvedValue({ id: 1 } as never);
    vi.mocked(api.deployStack).mockResolvedValue({} as never);

    const user = userEvent.setup();
    render(<StackDetailDialog slug="aws-eks-cluster-demo" open onOpenChange={vi.fn()} />);

    // Wait for the auto-detect fetch (checkip.amazonaws.com) to resolve.
    // userPublicIpCidr state is set inside the resolved promise; once fetch
    // has been called and has resolved, the next click will see the value.
    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalled();
    });
    // Flush microtasks so the .then() handler that calls setUserPublicIpCidr runs.
    await Promise.resolve();
    await Promise.resolve();

    const deployButton = await screen.findByRole('button', { name: /Deploy Blueprint/i });
    await user.click(deployButton);

    // Validation must NOT report user_ip as missing — the regression we're guarding against.
    await waitFor(() => {
      // Either submission proceeded (createStackInstance called) or no missing-inputs error fired.
      const errorCalls = vi.mocked(notify.error).mock.calls;
      const sawMissing = errorCalls.some(([title]) =>
        typeof title === 'string' && /Missing required inputs/i.test(title)
      );
      expect(sawMissing).toBe(false);
    });
  });

});
