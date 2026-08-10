import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '@/test/test-utils';
import { StackDetailDialog } from '../StackDetailDialog';
import { api } from '@/lib/api';

vi.mock('@/lib/notify', () => ({
  notify: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

const mockTemplate = {
  id: 1,
  name: 'BNK Multi-Host DPU Blueprint',
  slug: 'bnk-multi-host-dpu',
  description: 'Deploy BNK across multi-host DPU topology',
  category: 'bare-metal',
  is_active: true,
  is_featured: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  prerequisites: [
    {
      type: 'project_secret',
      name: 'jwt_token',
      description: 'F5 license JWT token',
    },
  ],
  modules: [
    {
      path: 'bare-metal/dpu-host-agent',
      name: 'DPU Host Agent',
      required: true,
      variables: {},
      engine_type: 'ssh',
      lifecycle_capabilities: {
        supports_init: true,
        supports_plan: true,
        supports_apply: true,
        supports_destroy: true,
        supports_refresh: false,
        supports_drift: false,
      },
    },
  ],
};

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
    getProjects: vi.fn().mockResolvedValue([
      {
        id: 1,
        name: 'MultiHost Test Project',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        cluster_count: 1,
      },
    ]),
    getProjectModules: vi.fn().mockResolvedValue([]),
    checkStackPrerequisites: vi.fn().mockResolvedValue({
      all_satisfied: false,
      required_secrets: [{ name: 'jwt_token', exists: false, description: 'F5 license JWT token' }],
      missing_secrets: ['jwt_token'],
    }),
    listCredentialTemplates: vi.fn().mockResolvedValue([]),
    createProject: vi.fn(),
    // Real backend response shape per CT-012: POST /api/projects/{id}/secrets/value
    createValueSecret: vi.fn().mockResolvedValue({
      success: true,
      secret: { id: 1, name: 'jwt_token', secret_type: 'value' },
    }),
    createStackInstance: vi.fn().mockResolvedValue({ id: 1 }),
    deployStack: vi.fn().mockResolvedValue({ id: 99, status: 'pending' }),
    listBareMetalHosts: vi.fn().mockResolvedValue([
      { id: 101, name: 'control-plane-host', ip_address: '10.0.0.1' },
      { id: 102, name: 'worker-host-1', ip_address: '10.0.0.2' },
    ]),
  },
}));

const initialMultiHostVariables = {
  control_plane_host_id: 101,
  worker_host_ids: [102],
  dpu_selections: {
    '101': [1],
    '102': [2],
  },
  topology: 'multi-host',
  tmfifo_pool_cidr: '192.168.100.0/24',
};

describe('StackDetailDialog Multi-Host & Inline Secrets', () => {
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
  });

  it('renders Multi-Host Cluster Topology card when initialVariables are provided', async () => {
    render(
      <StackDetailDialog
        slug="bnk-multi-host-dpu"
        initialProjectId={1}
        initialVariables={initialMultiHostVariables}
        open={true}
        onOpenChange={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Multi-Host Cluster Topology')).toBeInTheDocument();
    });

    expect(screen.getByText('Control Plane Host')).toBeInTheDocument();
    expect(screen.getByText('Worker Hosts (1)')).toBeInTheDocument();
    expect(screen.getByText('DPU Accelerator Mapping')).toBeInTheDocument();
  });

  it('allows filling inline missing secret values and enables deployment', async () => {
    const user = userEvent.setup();

    render(
      <StackDetailDialog
        slug="bnk-multi-host-dpu"
        initialProjectId={1}
        initialVariables={initialMultiHostVariables}
        open={true}
        onOpenChange={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('jwt_token')).toBeInTheDocument();
    });

    // Locate secret password input for missing secret `jwt_token`
    const jwtInput = await screen.findByPlaceholderText('Enter jwt_token...');
    expect(jwtInput).toBeInTheDocument();

    await user.type(jwtInput, 'my-secret-jwt-value');

    // Confirm badge changes to "Set in Blueprint"
    await waitFor(() => {
      expect(screen.getByText('Set in Blueprint')).toBeInTheDocument();
    });

    // Check that Add to Project button is enabled
    const addButton = screen.getByRole('button', { name: /Add to Project/i });
    expect(addButton).not.toBeDisabled();
  });

  // CT-012: secret gating and secure routing tests
  // Backend route: POST /api/projects/{project_id}/secrets/value
  // Request shape (ValueSecretCreate): { name: str, value: str, description?: str }
  // Response shape: { success: bool, secret: { id: int, name: str, secret_type: str } }
  it('gates the deploy button when required secrets are missing and not yet entered', async () => {
    render(
      <StackDetailDialog
        slug="bnk-multi-host-dpu"
        initialProjectId={1}
        initialVariables={initialMultiHostVariables}
        open={true}
        onOpenChange={vi.fn()}
      />
    );

    // Wait for the prerequisites check to load (jwt_token section appears)
    await waitFor(() => {
      expect(screen.getByText('jwt_token')).toBeInTheDocument();
    });

    // Deploy button must be disabled once prerequisitesCheck has loaded and jwt_token is missing
    await waitFor(() => {
      const addButton = screen.getByRole('button', { name: /Add to Project/i });
      expect(addButton).toBeDisabled();
    });
  });

  it('aborts deploy (never calls createStackInstance) when createValueSecret rejects', async () => {
    vi.mocked(api.createValueSecret).mockRejectedValueOnce(new Error('Server error'));

    const user = userEvent.setup();

    render(
      <StackDetailDialog
        slug="bnk-multi-host-dpu"
        initialProjectId={1}
        initialVariables={initialMultiHostVariables}
        open={true}
        onOpenChange={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('jwt_token')).toBeInTheDocument();
    });

    const jwtInput = await screen.findByPlaceholderText('Enter jwt_token...');
    await user.type(jwtInput, 'bad-value');

    const addButton = screen.getByRole('button', { name: /Add to Project/i });
    await user.click(addButton);

    await waitFor(() => {
      expect(api.createValueSecret).toHaveBeenCalled();
    });

    // createStackInstance must never be called when secret persistence fails
    expect(api.createStackInstance).not.toHaveBeenCalled();
  });

  it('routes inline-entered secret to encrypted store and excludes it from plaintext stack variables', async () => {
    const user = userEvent.setup();

    render(
      <StackDetailDialog
        slug="bnk-multi-host-dpu"
        initialProjectId={1}
        initialVariables={initialMultiHostVariables}
        open={true}
        onOpenChange={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('jwt_token')).toBeInTheDocument();
    });

    // Enter the secret value inline — input must be masked (type="password")
    const jwtInput = await screen.findByPlaceholderText('Enter jwt_token...');
    expect(jwtInput).toHaveAttribute('type', 'password');
    await user.type(jwtInput, 'my-secure-jwt-value');

    const addButton = screen.getByRole('button', { name: /Add to Project/i });
    await user.click(addButton);

    // Assert: secret persisted to encrypted store with real backend request shape
    await waitFor(() => {
      expect(api.createValueSecret).toHaveBeenCalledWith(
        1, // projectId
        expect.objectContaining({ name: 'jwt_token', value: 'my-secure-jwt-value' })
      );
    });

    // Assert: createStackInstance variables did NOT contain the secret as plaintext
    await waitFor(() => {
      expect(api.createStackInstance).toHaveBeenCalled();
      const [, instancePayload] = vi.mocked(api.createStackInstance).mock.calls[0];
      const variables = (instancePayload as { variables: Record<string, Record<string, string>> }).variables;
      const secretFoundAsVar = Object.values(variables).some(
        (modVars) => 'jwt_token' in modVars
      );
      expect(secretFoundAsVar).toBe(false);
    });
  });
});
