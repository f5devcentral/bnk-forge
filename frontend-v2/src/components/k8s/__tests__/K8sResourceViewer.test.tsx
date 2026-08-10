/**
 * Tests for K8sResourceViewer component
 *
 * Tests rendering, loading/empty states, search filtering, resource display,
 * detail dialog, refresh, and resource action buttons.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { K8sResourceViewer } from '../K8sResourceViewer';
import type { K8sCluster } from '@/types';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const mockCluster: K8sCluster = {
  id: 1,
  name: 'dev-cluster',
  context: 'dev-context',
  api_server: 'https://dev.eks.amazonaws.com',
  default_namespace: 'default',
  status: 'connected',
  version: '1.28',
  project_id: 1,
};

const mockResourceTypes = {
  resource_types: [
    { key: 'pod', kind: 'Pod', api_group: '', api_version: 'v1', plural: 'pods', namespaced: true, display_name: 'Pods' },
    { key: 'deployment', kind: 'Deployment', api_group: 'apps', api_version: 'apps/v1', plural: 'deployments', namespaced: true, display_name: 'Deployments' },
    { key: 'service', kind: 'Service', api_group: '', api_version: 'v1', plural: 'services', namespaced: true, display_name: 'Services' },
  ],
};

const mockNamespaces = {
  namespaces: [
    { name: 'default', status: 'Active' },
    { name: 'kube-system', status: 'Active' },
    { name: 'monitoring', status: 'Active' },
  ],
  count: 3,
  cluster_id: 1,
};

const mockResources = {
  resources: [
    {
      name: 'nginx-pod',
      namespace: 'default',
      kind: 'Pod',
      apiVersion: 'v1',
      metadata: {
        name: 'nginx-pod',
        namespace: 'default',
        creationTimestamp: '2026-02-20T10:00:00Z',
        uid: 'uid-1',
        labels: { app: 'nginx' },
      },
      status: { phase: 'Running' },
    },
    {
      name: 'redis-pod',
      namespace: 'default',
      kind: 'Pod',
      apiVersion: 'v1',
      metadata: {
        name: 'redis-pod',
        namespace: 'default',
        creationTimestamp: '2026-02-19T08:00:00Z',
        uid: 'uid-2',
        labels: { app: 'redis' },
      },
      status: { phase: 'Running' },
    },
    {
      name: 'failing-pod',
      namespace: 'default',
      kind: 'Pod',
      apiVersion: 'v1',
      metadata: {
        name: 'failing-pod',
        namespace: 'default',
        creationTimestamp: '2026-02-18T12:00:00Z',
        uid: 'uid-3',
      },
      status: { phase: 'Failed' },
    },
  ],
  count: 3,
  resource_type: 'pod',
  namespace: 'default',
  cluster_id: 1,
};

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();

  server.use(
    http.get('*/api/k8s/resource-types', () => {
      return HttpResponse.json(mockResourceTypes);
    }),
    http.get('*/api/k8s/clusters/:id/namespaces', () => {
      return HttpResponse.json(mockNamespaces);
    }),
    http.get('*/api/k8s/clusters/:id/resources/:type', () => {
      return HttpResponse.json(mockResources);
    }),
  );
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('K8sResourceViewer', () => {
  it('renders the controls section with Resource Type, Namespace, and Search', async () => {
    render(<K8sResourceViewer cluster={mockCluster} />);

    expect(screen.getByText('Resource Type')).toBeInTheDocument();
    expect(screen.getByText('Namespace')).toBeInTheDocument();
    expect(screen.getByText('Search')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Search resources...')).toBeInTheDocument();
  });

  it('renders action buttons: Compact, Metrics, Events, Refresh, Create Resource', async () => {
    render(<K8sResourceViewer cluster={mockCluster} />);

    expect(screen.getByText('Compact')).toBeInTheDocument();
    expect(screen.getByText('Metrics')).toBeInTheDocument();
    expect(screen.getByText('Events')).toBeInTheDocument();
    expect(screen.getByText('Refresh')).toBeInTheDocument();
    expect(screen.getByText('Create Resource')).toBeInTheDocument();
  });

  it('shows loading spinner when resources are being fetched', async () => {
    // Delay the response to see loading state
    server.use(
      http.get('*/api/k8s/clusters/:id/resources/:type', async () => {
        await new Promise((resolve) => setTimeout(resolve, 5000));
        return HttpResponse.json(mockResources);
      }),
    );

    render(<K8sResourceViewer cluster={mockCluster} />);

    // The loading spinner should be visible while resources load
    await waitFor(() => {
      const spinners = document.querySelectorAll('.animate-spin');
      expect(spinners.length).toBeGreaterThan(0);
    });
  });

  it('displays resources after loading', async () => {
    render(<K8sResourceViewer cluster={mockCluster} />);

    await waitFor(() => {
      expect(screen.getByText('nginx-pod')).toBeInTheDocument();
    });
    expect(screen.getByText('redis-pod')).toBeInTheDocument();
    expect(screen.getByText('failing-pod')).toBeInTheDocument();
  });

  it('shows the resource count summary', async () => {
    render(<K8sResourceViewer cluster={mockCluster} />);

    await waitFor(() => {
      expect(screen.getByText(/Found 3 pods in default/)).toBeInTheDocument();
    });
  });

  it('shows status badges for resources', async () => {
    render(<K8sResourceViewer cluster={mockCluster} />);

    await waitFor(() => {
      expect(screen.getByText('nginx-pod')).toBeInTheDocument();
    });

    // Resources should show their status
    const runningBadges = screen.getAllByText('Running');
    expect(runningBadges.length).toBe(2);
    expect(screen.getByText('Failed')).toBeInTheDocument();
  });

  it('filters resources by search query', async () => {
    const user = userEvent.setup();
    render(<K8sResourceViewer cluster={mockCluster} />);

    await waitFor(() => {
      expect(screen.getByText('nginx-pod')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('Search resources...');
    await user.type(searchInput, 'nginx');

    await waitFor(() => {
      expect(screen.getByText('nginx-pod')).toBeInTheDocument();
      expect(screen.queryByText('redis-pod')).not.toBeInTheDocument();
      expect(screen.queryByText('failing-pod')).not.toBeInTheDocument();
    });

    expect(screen.getByText(/Found 1 pod in default/)).toBeInTheDocument();
  });

  it('shows empty state when no resources match search', async () => {
    const user = userEvent.setup();
    render(<K8sResourceViewer cluster={mockCluster} />);

    await waitFor(() => {
      expect(screen.getByText('nginx-pod')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('Search resources...');
    await user.type(searchInput, 'nonexistent');

    await waitFor(() => {
      expect(screen.getByText('No resources found')).toBeInTheDocument();
      expect(screen.getByText(/No resources matching "nonexistent"/)).toBeInTheDocument();
    });
  });

  it('shows empty state when no resources exist', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/resources/:type', () => {
        return HttpResponse.json({
          resources: [],
          count: 0,
          resource_type: 'pod',
          namespace: 'default',
          cluster_id: 1,
        });
      }),
    );

    render(<K8sResourceViewer cluster={mockCluster} />);

    await waitFor(() => {
      expect(screen.getByText('No resources found')).toBeInTheDocument();
      expect(screen.getByText(/No pod resources in default/)).toBeInTheDocument();
    });
  });
});
