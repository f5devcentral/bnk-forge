/**
 * Tests for KubernetesV2 page
 *
 * Covers: renders page, project/cluster selection, empty state, search input.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { STORAGE_KEYS } from '@/lib/storage-keys';
import KubernetesV2 from '@/pages/KubernetesV2';

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

interface TestProject {
  id: number;
  name: string;
}

function resetK8sSelectionStorage() {
  window.localStorage.removeItem(STORAGE_KEYS.K8S_PROJECT);
  window.localStorage.removeItem(STORAGE_KEYS.K8S_CLUSTER);
  window.localStorage.removeItem(STORAGE_KEYS.K8S_NAMESPACE);
  window.localStorage.removeItem(STORAGE_KEYS.K8S_RESOURCE_TYPE);
  window.localStorage.removeItem(STORAGE_KEYS.K8S_UNHEALTHY_ONLY);
  window.localStorage.removeItem(STORAGE_KEYS.K8S_VIEW_MODE);
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

function mockKubernetesPageApis(params: {
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
    http.get('*/api/k8s/clusters/:clusterId/nodes/count', ({ params: pathParams }) => {
      return HttpResponse.json({ cluster_id: Number(pathParams.clusterId), node_count: 3 });
    }),
    http.get('*/api/k8s/clusters/:clusterId/namespaces', ({ params: pathParams }) => {
      return HttpResponse.json({
        namespaces: [{ name: 'default', status: 'Active', labels: null, created_at: null }],
        count: 1,
        cluster_id: Number(pathParams.clusterId),
      });
    }),
    http.get('*/api/k8s/clusters/:id/resource-summary', ({ params: pathParams, request }) => {
      const url = new URL(request.url);
      return HttpResponse.json({
        cluster_id: Number(pathParams.id),
        namespace: url.searchParams.get('namespace'),
        summary: {},
      });
    }),
    http.get('*/api/k8s/clusters/:id/resources/:type', ({ params: pathParams }) => {
      return HttpResponse.json({
        resources: [],
        count: 0,
        resource_type: String(pathParams.type),
        namespace: 'default',
        cluster_id: Number(pathParams.id),
      });
    }),
  );
}

describe('KubernetesV2', () => {
  beforeEach(() => {
    resetK8sSelectionStorage();
  });

  it('renders the Kubernetes page', async () => {
    render(<KubernetesV2 />);
    // Several elements mention "Kubernetes" (description text, the
    // auto-detect button, etc.) — assert that at least one renders.
    await waitFor(() => {
      expect(screen.getAllByText(/kubernetes/i).length).toBeGreaterThan(0);
    });
  });

  it('shows empty state when no cluster is selected', async () => {
    resetK8sSelectionStorage();
    render(<KubernetesV2 />);
    await waitFor(() => {
      expect(screen.getByText('No cluster selected')).toBeInTheDocument();
    });
  });

  it('renders search input', async () => {
    render(<KubernetesV2 />);
    await waitFor(() => {
      const searchInput = screen.queryByPlaceholderText(/search/i) || screen.queryByPlaceholderText(/filter/i);
      expect(searchInput).toBeInTheDocument();
    });
  });

  it('renders project dropdown', async () => {
    render(<KubernetesV2 />);
    await waitFor(() => {
      const projectSelector = screen.queryByText(/select project/i) || screen.queryByText(/project/i);
      expect(projectSelector).toBeInTheDocument();
    });
  });

  it('auto-selects project when exactly one project exists and none is selected', async () => {
    const singleProject = [{ id: 1, name: 'project-one' }];
    const singleCluster = [buildCluster(101, 1, 'cluster-one')];
    mockKubernetesPageApis({
      projects: singleProject,
      allClusters: singleCluster,
      clustersByProject: { 1: singleCluster },
    });

    render(<KubernetesV2 />);

    await waitFor(() => {
      expect(window.localStorage.getItem(STORAGE_KEYS.K8S_PROJECT)).toBe('1');
    });
  });

  it('auto-selects cluster when exactly one cluster exists for the selected project', async () => {
    const singleProject = [{ id: 1, name: 'project-one' }];
    const singleCluster = [buildCluster(101, 1, 'cluster-one')];
    mockKubernetesPageApis({
      projects: singleProject,
      allClusters: singleCluster,
      clustersByProject: { 1: singleCluster },
    });

    render(<KubernetesV2 />);

    await waitFor(() => {
      expect(window.localStorage.getItem(STORAGE_KEYS.K8S_CLUSTER)).toBe('101');
    });
  });

  it('does NOT auto-select when multiple projects exist', async () => {
    const projects = [
      { id: 1, name: 'project-one' },
      { id: 2, name: 'project-two' },
    ];
    const singleCluster = [buildCluster(101, 1, 'cluster-one')];
    mockKubernetesPageApis({
      projects,
      allClusters: singleCluster,
      clustersByProject: { 1: singleCluster, 2: [] },
    });

    render(<KubernetesV2 />);

    await waitFor(() => {
      expect(screen.getByText('No cluster selected')).toBeInTheDocument();
    });
    expect(window.localStorage.getItem(STORAGE_KEYS.K8S_PROJECT)).toBeFalsy();
  });

  it('does NOT auto-select when multiple clusters exist', async () => {
    const singleProject = [{ id: 1, name: 'project-one' }];
    const clusters = [
      buildCluster(101, 1, 'cluster-one'),
      buildCluster(102, 1, 'cluster-two'),
    ];
    mockKubernetesPageApis({
      projects: singleProject,
      allClusters: clusters,
      clustersByProject: { 1: clusters },
    });

    render(<KubernetesV2 />);

    // With multiple clusters the auto-select condition (1 project AND 1 cluster) is
    // not met, so neither project nor cluster should be auto-selected.
    await waitFor(() => {
      expect(screen.getByText('No cluster selected')).toBeInTheDocument();
    });
    expect(window.localStorage.getItem(STORAGE_KEYS.K8S_PROJECT)).toBeFalsy();
    expect(window.localStorage.getItem(STORAGE_KEYS.K8S_CLUSTER)).toBeFalsy();
  });

  it('does NOT auto-select while data is still loading', async () => {
    const singleProject = [{ id: 1, name: 'project-one' }];
    const singleCluster = [buildCluster(101, 1, 'cluster-one')];

    server.use(
      http.get('*/api/projects', async () => {
        await new Promise((r) => setTimeout(r, 200));
        return HttpResponse.json({ projects: singleProject, total: singleProject.length });
      }),
      http.get('*/api/k8s/clusters', () => {
        return HttpResponse.json({ clusters: singleCluster, count: singleCluster.length });
      }),
      http.get('*/api/projects/:projectId/k8s/clusters', ({ params: pathParams }) => {
        const projectId = Number(pathParams.projectId);
        const clusters = projectId === 1 ? singleCluster : [];
        return HttpResponse.json({ clusters, count: clusters.length });
      }),
      http.get('*/api/k8s/clusters/:clusterId/nodes/count', ({ params: pathParams }) => {
        return HttpResponse.json({ cluster_id: Number(pathParams.clusterId), node_count: 3 });
      }),
      http.get('*/api/k8s/clusters/:clusterId/namespaces', ({ params: pathParams }) => {
        return HttpResponse.json({
          namespaces: [{ name: 'default', status: 'Active', labels: null, created_at: null }],
          count: 1,
          cluster_id: Number(pathParams.clusterId),
        });
      }),
      http.get('*/api/k8s/clusters/:id/resource-summary', ({ params: pathParams, request }) => {
        const url = new URL(request.url);
        return HttpResponse.json({
          cluster_id: Number(pathParams.id),
          namespace: url.searchParams.get('namespace'),
          summary: {},
        });
      }),
      http.get('*/api/k8s/clusters/:id/resources/:type', ({ params: pathParams }) => {
        return HttpResponse.json({
          resources: [],
          count: 0,
          resource_type: String(pathParams.type),
          namespace: 'default',
          cluster_id: Number(pathParams.id),
        });
      }),
    );

    render(<KubernetesV2 />);

    expect(window.localStorage.getItem(STORAGE_KEYS.K8S_PROJECT)).toBeFalsy();

    await waitFor(() => {
      expect(window.localStorage.getItem(STORAGE_KEYS.K8S_PROJECT)).toBe('1');
    }, { timeout: 3000 });

    await waitFor(() => {
      expect(window.localStorage.getItem(STORAGE_KEYS.K8S_CLUSTER)).toBe('101');
    });
  });
});
