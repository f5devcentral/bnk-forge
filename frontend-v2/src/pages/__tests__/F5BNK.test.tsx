/**
 * Tests for F5BNK (BIG-IP Next for Kubernetes) page
 *
 * Covers: page rendering, project/cluster empty states, sidebar categories,
 * search input, resource badge display.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render, screen } from '@/test/test-utils';
import { waitFor } from '@testing-library/react';
import { STORAGE_KEYS } from '@/lib/storage-keys';
import F5BNK from '@/pages/F5BNK';

const storageState = new Map<string, string>();
const localStorageMock: Storage = {
  get length() {
    return storageState.size;
  },
  clear: () => {
    storageState.clear();
  },
  getItem: (key: string) => storageState.get(key) ?? null,
  key: (index: number) => Array.from(storageState.keys())[index] ?? null,
  removeItem: (key: string) => {
    storageState.delete(key);
  },
  setItem: (key: string, value: string) => {
    storageState.set(key, value);
  },
};

Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
  configurable: true,
});

function resetBnkSelectionStorage() {
  window.localStorage.removeItem(STORAGE_KEYS.BNK_PROJECT);
  window.localStorage.removeItem(STORAGE_KEYS.BNK_CLUSTER);
  window.localStorage.removeItem('bnk-forge-bnk-initial-view');
}

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('@/hooks/useDebounce', () => ({
  useDebounce: vi.fn((value: string) => value),
}));

vi.mock('@/hooks/useKeyboardShortcuts', () => ({
  useKeyboardShortcuts: vi.fn(),
}));

vi.mock('@/components/k8s/ResourceDescribeViewer', () => ({
  ResourceDescribeViewer: () => null,
}));

vi.mock('@/components/k8s/ResourceDeleteDialog', () => ({
  ResourceDeleteDialog: () => null,
}));

vi.mock('@/components/k8s/ResourceEditDialog', () => ({
  ResourceEditDialog: () => null,
}));

vi.mock('@/components/k8s/ResourceCreateDialog', () => ({
  ResourceCreateDialog: () => null,
}));

vi.mock('@/components/k8s/F5BNKPolicyViewer', () => ({
  F5BNKPolicyViewer: () => null,
}));

vi.mock('@/components/k8s/F5AIAnalyzerViewer', () => ({
  F5AIAnalyzerViewer: () => null,
}));

vi.mock('@/components/k8s/F5iRuleViewer', () => ({
  F5iRuleViewer: () => null,
}));

vi.mock('@/components/k8s/F5BNKTopologyViewer', () => ({
  F5BNKTopologyViewer: () => null,
}));

vi.mock('@/components/k8s/BNKHealthDashboard', () => ({
  BNKHealthDashboard: () => <div data-testid="health-dashboard">Health Dashboard</div>,
}));

vi.mock('@/components/k8s/BNKUpgradePanel', () => ({
  BNKUpgradePanel: () => null,
}));

vi.mock('@/components/runbooks/RunbookWizard', () => ({
  RunbookWizard: () => null,
}));

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

interface TestProject {
  id: number;
  name: string;
}

interface TestCluster {
  id: number;
  name: string;
  context: string;
  api_server: string;
  cloud_provider: string | null;
  detected_platform_profile: string;
  detected_platform_provider: string | null;
  platform_capabilities: Record<string, boolean | null>;
  platform_constraints: Record<string, boolean | null>;
  region: string | null;
  default_namespace: string;
  status: string;
  version: string | null;
  project_id: number;
  ssh_tunnel_enabled: boolean;
  ssh_remote_k8s_host: string | null;
  ssh_remote_k8s_port: number | null;
  ssh_credential_id: number | null;
  ssh_host_override: string | null;
  last_synced_at: string | null;
  created_at: string | null;
}

function buildCluster(id: number, projectId: number, name: string): TestCluster {
  return {
    id,
    name,
    context: `${name}-ctx`,
    api_server: `https://${name}.example.local`,
    cloud_provider: null,
    detected_platform_profile: 'unknown',
    detected_platform_provider: null,
    platform_capabilities: {},
    platform_constraints: {},
    region: null,
    default_namespace: 'default',
    status: 'active',
    version: '1.29',
    project_id: projectId,
    ssh_tunnel_enabled: false,
    ssh_remote_k8s_host: null,
    ssh_remote_k8s_port: null,
    ssh_credential_id: null,
    ssh_host_override: null,
    last_synced_at: null,
    created_at: null,
  };
}

function mockF5PageApis(params: {
  projects: TestProject[];
  allClusters: TestCluster[];
  clustersByProject: Record<number, TestCluster[]>;
}) {
  server.use(
    http.get('*/api/projects', ({ request }) => {
      return HttpResponse.json({ projects: params.projects, total: params.projects.length });
    }),
    http.get('*/api/k8s/clusters', ({ request }) => {
      return HttpResponse.json({ clusters: params.allClusters, count: params.allClusters.length });
    }),
    http.get('*/api/projects/:projectId/k8s/clusters', ({ params: pathParams }) => {
      const projectId = Number(pathParams.projectId);
      const clusters = params.clustersByProject[projectId] || [];
      return HttpResponse.json({ clusters, count: clusters.length });
    }),
    http.get('*/api/k8s/clusters/:clusterId/namespaces', ({ params: pathParams }) => {
      return HttpResponse.json({
        namespaces: [{ name: 'default', status: 'Active', labels: null, created_at: null }],
        count: 1,
        cluster_id: Number(pathParams.clusterId),
      });
    }),
  );
}

describe('F5BNK', () => {
  beforeEach(() => {
    resetBnkSelectionStorage();
  });

  it('renders the F5 BNK page without crashing', () => {
    render(<F5BNK />);
    // The page should render sidebar categories
    const pageContent = screen.queryByText(/health/i) || screen.queryByText(/gateway/i);
    expect(pageContent).toBeInTheDocument();
  });

  it('shows "No project selected" empty state when no project is chosen', () => {
    render(<F5BNK />);
    expect(screen.getByText('No project selected')).toBeInTheDocument();
  });

  it('shows "No cluster selected" when project is set but no cluster', () => {
    window.localStorage.setItem('bnk-forge-bnk-project', '1');
    render(<F5BNK />);
    expect(screen.getByText('No cluster selected')).toBeInTheDocument();
  });

  it('renders search input', () => {
    render(<F5BNK />);
    const searchInput = screen.queryByPlaceholderText(/search/i) || screen.queryByPlaceholderText(/filter/i);
    expect(searchInput).toBeInTheDocument();
  });

  it('renders the active category\'s items in the sidebar', () => {
    render(<F5BNK />);
    // D-020: top-level categories are now header tabs; the sidebar shows the
    // active category's items flat. The default selection lands on the
    // Insights category, so its items (e.g. Health Dashboard) are listed.
    const sidebarItem = screen.queryByText(/health dashboard/i) || screen.queryByText(/traffic flow/i);
    expect(sidebarItem).toBeInTheDocument();
  });

  it('auto-selects project when exactly one project exists and none is selected', async () => {
    const singleProject = [{ id: 1, name: 'project-one' }];
    const singleCluster = [buildCluster(101, 1, 'cluster-one')];
    mockF5PageApis({
      projects: singleProject,
      allClusters: singleCluster,
      clustersByProject: { 1: singleCluster },
    });

    render(<F5BNK />);

    await waitFor(() => {
      expect(window.localStorage.getItem(STORAGE_KEYS.BNK_PROJECT)).toBe('1');
    });
  });

  it('auto-selects cluster when exactly one cluster exists for the selected project', async () => {
    const singleProject = [{ id: 1, name: 'project-one' }];
    const singleCluster = [buildCluster(101, 1, 'cluster-one')];
    mockF5PageApis({
      projects: singleProject,
      allClusters: singleCluster,
      clustersByProject: { 1: singleCluster },
    });

    render(<F5BNK />);

    await waitFor(() => {
      expect(window.localStorage.getItem(STORAGE_KEYS.BNK_CLUSTER)).toBe('101');
    });
  });

  it('does NOT auto-select when multiple projects exist', async () => {
    const projects = [
      { id: 1, name: 'project-one' },
      { id: 2, name: 'project-two' },
    ];
    const singleCluster = [buildCluster(101, 1, 'cluster-one')];
    mockF5PageApis({
      projects,
      allClusters: singleCluster,
      clustersByProject: { 1: singleCluster, 2: [] },
    });

    render(<F5BNK />);

    await waitFor(() => {
      expect(screen.getByText('No project selected')).toBeInTheDocument();
    });
    expect(window.localStorage.getItem(STORAGE_KEYS.BNK_PROJECT)).toBeFalsy();
  });

  it('does NOT auto-select when multiple clusters exist', async () => {
    const singleProject = [{ id: 1, name: 'project-one' }];
    const clusters = [
      buildCluster(101, 1, 'cluster-one'),
      buildCluster(102, 1, 'cluster-two'),
    ];
    mockF5PageApis({
      projects: singleProject,
      allClusters: clusters,
      clustersByProject: { 1: clusters },
    });

    render(<F5BNK />);

    await waitFor(() => {
      expect(screen.getByText('No project selected')).toBeInTheDocument();
    });
    expect(window.localStorage.getItem(STORAGE_KEYS.BNK_PROJECT)).toBeFalsy();
    expect(window.localStorage.getItem(STORAGE_KEYS.BNK_CLUSTER)).toBeFalsy();
  });
});
