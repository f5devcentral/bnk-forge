/**
 * Tests for K8sClusterList component
 *
 * Tests loading state, empty state (no clusters), cluster cards with
 * status badges, SSH tunnel display, delete confirmation dialog,
 * EKS detection button, and Add Cluster button.
 *
 * MSW handlers return realistic response shapes from the backend.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { K8sClusterList } from '../K8sClusterList';

// ---------------------------------------------------------------------------
// Mock heavy child component
// ---------------------------------------------------------------------------

vi.mock('../ClusterConfigDialog', () => ({
  ClusterConfigDialog: ({ open }: { open: boolean }) =>
    open ? <div data-testid="cluster-config-dialog">Config Dialog</div> : null,
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

// Fixture matches backend serialize_cluster() — routes/k8s/clusters.py
// Note: project-scoped list uses include_project_id=False so project_id is omitted
const mockClusters = [
  {
    id: 1,
    name: 'prod-cluster',
    cloud_provider: 'aws',
    region: 'us-east-1',
    context: 'arn:aws:eks:us-east-1:123456:cluster/prod',
    api_server: 'https://ABC123.gr7.us-east-1.eks.amazonaws.com',
    default_namespace: 'default',
    status: 'active',
    version: '1.28',
    ssh_tunnel_enabled: false,
    detected_platform_profile: 'eks',
    detected_platform_provider: 'aws',
    platform_capabilities: { cloud_load_balancer: true },
    platform_constraints: { managed_control_plane: true },
    ssh_remote_k8s_host: null,
    ssh_remote_k8s_port: null,
    ssh_credential_id: null,
    last_synced_at: new Date(Date.now() - 300000).toISOString(), // 5 min ago
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 2,
    name: 'staging-cluster',
    cloud_provider: 'on-prem',
    region: null,
    context: 'kubernetes-admin@staging',
    api_server: 'https://10.176.11.91:6443',
    default_namespace: 'default',
    status: 'error',
    version: '1.27',
    ssh_tunnel_enabled: true,
    detected_platform_profile: 'generic_onprem',
    detected_platform_provider: 'baremetal',
    platform_capabilities: { network_attachment_definitions: true },
    platform_constraints: { cluster_lifecycle_external: true },
    ssh_remote_k8s_host: '10.176.11.91',
    ssh_remote_k8s_port: 6443,
    ssh_credential_id: 1,
    last_synced_at: null,
    created_at: '2026-01-05T00:00:00Z',
  },
];

const mockTunnels = {
  tunnels: [
    {
      cluster_id: 2,
      local_port: 6443,
      ssh_host: '10.176.11.91',
      is_healthy: true,
      idle_seconds: 30,
    },
  ],
};

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();

  server.use(
    http.get(/\/api\/projects\/\d+\/k8s\/clusters/, () => {
      return HttpResponse.json({ clusters: mockClusters, count: 2 });
    }),
    http.get(/\/api\/k8s\/tunnels/, () => {
      return HttpResponse.json(mockTunnels);
    }),
    // Batch connectivity probe — return a "connected" result for prod-cluster (id=1)
    // and no result for staging-cluster (id=2, status='error' → fallback badge).
    http.get('*/api/k8s/clusters/connectivity', () => {
      return HttpResponse.json({
        results: [
          {
            cluster_id: 1,
            cluster_name: 'prod-cluster',
            api_server: 'https://ABC123.gr7.us-east-1.eks.amazonaws.com',
            status: 'connected',
            message: 'Cluster reachable and K8s API healthy',
            suggestion: null,
            icmp: { reachable: true, latency_ms: 12.5 },
            tcp: { open: true, connect_ms: 14.2, port: 443 },
            k8s_api: { accessible: true, version: '1.28', status_code: 200 },
            checked_at: new Date().toISOString(),
          },
        ],
        summary: { total: 2, connected: 1, reachable: 0, partial: 0, unreachable: 0, unknown: 1 },
      });
    }),
  );
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('K8sClusterList', () => {
  // ─── Loading State ──────────────────────────────────────────────────

  describe('loading state', () => {
    it('shows loading spinner while fetching clusters', () => {
      server.use(
        http.get(/\/api\/projects\/\d+\/k8s\/clusters/, async () => {
          await new Promise((r) => setTimeout(r, 10000));
          return HttpResponse.json({ clusters: [], count: 0 });
        }),
      );

      render(<K8sClusterList projectId={1} />);
      // The Loader2 spinner is rendered (the parent has animate-spin class)
      expect(document.querySelector('.animate-spin')).toBeInTheDocument();
    });
  });

  // ─── Empty State ────────────────────────────────────────────────────

  describe('empty state', () => {
    it('shows empty message when no clusters exist', async () => {
      server.use(
        http.get(/\/api\/projects\/\d+\/k8s\/clusters/, () => {
          return HttpResponse.json({ clusters: [], count: 0 });
        }),
      );

      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText('No clusters configured')).toBeInTheDocument();
      });
    });

    it('shows "Add Your First Cluster" button when no SSH credential', async () => {
      server.use(
        http.get(/\/api\/projects\/\d+\/k8s\/clusters/, () => {
          return HttpResponse.json({ clusters: [], count: 0 });
        }),
      );

      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Add Your First Cluster')).toBeInTheDocument();
      });
    });

    it('shows "Discover Clusters via SSH" when SSH credential is set', async () => {
      server.use(
        http.get(/\/api\/projects\/\d+\/k8s\/clusters/, () => {
          return HttpResponse.json({ clusters: [], count: 0 });
        }),
      );

      render(<K8sClusterList projectId={1} projectSshCredentialId={5} />);

      await waitFor(() => {
        expect(screen.getByText('Discover Clusters via SSH')).toBeInTheDocument();
      });
    });
  });

  // ─── Cluster Cards ─────────────────────────────────────────────────

  describe('cluster cards', () => {
    it('renders cluster names', async () => {
      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText('prod-cluster')).toBeInTheDocument();
        expect(screen.getByText('staging-cluster')).toBeInTheDocument();
      });
    });

    it.skip('shows Active status badge (TODO: broken — requires MSW mock for SSH test endpoint; see ClusterReachabilityBadge)', async () => {
      render(<K8sClusterList projectId={1} />);

      // Active cluster now shows a "Connected" connectivity badge (from the
      // probe result) rather than the raw "Active" status — the raw status
      // badge is only rendered as a fallback when no probe data is available.
      await waitFor(() => {
        expect(screen.getByText('Reachable')).toBeInTheDocument();
      });
    });

    it.skip('shows Unreachable status badge for errored clusters (TODO: see sibling test)', async () => {
      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Unreachable')).toBeInTheDocument();
      });
    });

    it.skip('uses connected status as primary truth even when probe says partial (TODO: see sibling test)', async () => {
      server.use(
        http.get('*/api/k8s/clusters/connectivity', () => {
          return HttpResponse.json({
            results: [
              {
                cluster_id: 1,
                cluster_name: 'prod-cluster',
                api_server: 'https://ABC123.gr7.us-east-1.eks.amazonaws.com',
                status: 'partial',
                message: 'API port blocked',
                suggestion: 'Open firewall rule',
                icmp: { reachable: true, latency_ms: 12.5 },
                tcp: { open: false, connect_ms: null, port: 443 },
                k8s_api: { accessible: false, version: null, status_code: null },
                checked_at: new Date().toISOString(),
              },
            ],
            summary: { total: 2, connected: 0, reachable: 0, partial: 1, unreachable: 0, unknown: 1 },
          });
        }),
      );

      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Reachable')).toBeInTheDocument();
      });
      // Diagnostic badge is secondary and hidden when primary status is connected.
      expect(screen.queryByText('Partial')).not.toBeInTheDocument();
    });

    it('shows Kubernetes version badge', async () => {
      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText('v1.28')).toBeInTheDocument();
      });
    });

    it('shows cloud provider name', async () => {
      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText('aws')).toBeInTheDocument();
        expect(screen.getByText('on-prem')).toBeInTheDocument();
      });
    });

    it('shows detected platform label from backend metadata', async () => {
      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Detected: Amazon EKS')).toBeInTheDocument();
        expect(screen.getByText('Detected: Generic On-Prem')).toBeInTheDocument();
      });
    });

    it('shows region when present', async () => {
      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText('us-east-1')).toBeInTheDocument();
      });
    });

    it('shows context name', async () => {
      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/kubernetes-admin@staging/)).toBeInTheDocument();
      });
    });

    it('shows "Last synced" for clusters with sync time', async () => {
      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/Last synced/)).toBeInTheDocument();
      });
    });
  });

  // ─── SSH Tunnel Display ────────────────────────────────────────────

  describe('SSH tunnel display', () => {
    it('shows SSH tunnel status for tunnel-enabled clusters', async () => {
      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        // Cluster 2 has ssh_tunnel_enabled=true and an active tunnel
        expect(screen.getByText(/SSH Connected/)).toBeInTheDocument();
      });
    });

    it('shows tunnel port number', async () => {
      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/:6443/)).toBeInTheDocument();
      });
    });
  });

  // ─── Header Actions ────────────────────────────────────────────────

  describe('header actions', () => {
    it('renders "Add Cluster" button', async () => {
      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Add Cluster')).toBeInTheDocument();
      });
    });

    it('renders "Detect EKS" button for AWS projects', async () => {
      render(<K8sClusterList projectId={1} cloudProvider="aws" />);

      await waitFor(() => {
        expect(screen.getByText('Detect Managed Clusters')).toBeInTheDocument();
      });
    });

    it('renders "Detect EKS" button when target platform is EKS', async () => {
      render(<K8sClusterList projectId={1} targetPlatformProfile="eks" />);

      await waitFor(() => {
        expect(screen.getByText('Detect Managed Clusters')).toBeInTheDocument();
      });
    });

    it('renders managed-cluster detect button for IBM/ROKS projects', async () => {
      render(<K8sClusterList projectId={1} cloudProvider="ibm" targetPlatformProfile="roks" />);

      await waitFor(() => {
        expect(screen.getByText('Detect Managed Clusters')).toBeInTheDocument();
      });
    });

    it('does not render "Detect EKS" for non-AWS projects', async () => {
      render(<K8sClusterList projectId={1} cloudProvider="on-prem" targetPlatformProfile="generic_onprem" />);

      await waitFor(() => {
        expect(screen.getByText('prod-cluster')).toBeInTheDocument();
      });

      expect(screen.queryByText('Detect Managed Clusters')).not.toBeInTheDocument();
    });

    it('shows target mismatch badge when target and detected differ', async () => {
      render(<K8sClusterList projectId={1} targetPlatformProfile="ocp" />);

      await waitFor(() => {
        expect(screen.getAllByText('Target mismatch').length).toBeGreaterThan(0);
      });
    });

    it('suppresses target mismatch badge when all platform_capabilities values are null (not yet scanned)', async () => {
      // All-null capabilities = register-time placeholder; no scan has run yet.
      const unscannedCluster = {
        ...mockClusters[0],
        detected_platform_profile: 'generic_onprem',
        platform_capabilities: {
          multus: null,
          sriov: null,
          hugepages: null,
          gateway_api: null,
          cloud_load_balancer: null,
          network_attachment_definitions: null,
          cert_manager: null,
          ingress_controller: null,
          cni_calico: null,
          cni_cilium: null,
        },
      };

      server.use(
        http.get(/\/api\/projects\/\d+\/k8s\/clusters/, () => {
          return HttpResponse.json({ clusters: [unscannedCluster], count: 1 });
        }),
      );

      render(<K8sClusterList projectId={1} targetPlatformProfile="ocp" />);

      await waitFor(() => {
        expect(screen.getByText('prod-cluster')).toBeInTheDocument();
      });

      // Banner must be absent — capabilities not yet observed.
      expect(screen.queryByText('Target mismatch')).not.toBeInTheDocument();
    });

    it('shows target mismatch badge when at least one capability is non-null and profiles differ', async () => {
      // One truthy capability means the scan ran and observed platform data.
      const scannedMismatchCluster = {
        ...mockClusters[0],
        detected_platform_profile: 'generic_onprem',
        platform_capabilities: {
          multus: null,
          sriov: null,
          cloud_load_balancer: true,  // scanner populated this
        },
      };

      server.use(
        http.get(/\/api\/projects\/\d+\/k8s\/clusters/, () => {
          return HttpResponse.json({ clusters: [scannedMismatchCluster], count: 1 });
        }),
      );

      render(<K8sClusterList projectId={1} targetPlatformProfile="ocp" />);

      await waitFor(() => {
        expect(screen.getByText('Target mismatch')).toBeInTheDocument();
      });
    });

    it('renders "Detect Clusters" button when SSH credential is set', async () => {
      render(<K8sClusterList projectId={1} projectSshCredentialId={5} />);

      await waitFor(() => {
        expect(screen.getByText('Detect Clusters')).toBeInTheDocument();
      });
    });

    it('opens config dialog when "Add Cluster" is clicked', async () => {
      const user = userEvent.setup();
      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Add Cluster')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Add Cluster'));
      expect(screen.getByTestId('cluster-config-dialog')).toBeInTheDocument();
    });
  });

  // ─── Page Header ──────────────────────────────────────────────────

  describe('page header', () => {
    it('renders section title', async () => {
      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Kubernetes Clusters')).toBeInTheDocument();
      });
    });

    it('renders description text', async () => {
      render(<K8sClusterList projectId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/Monitor resources across EKS, AKS, GKE/)).toBeInTheDocument();
      });
    });

    it('renders OCP-aware support semantics description for OCP-targeted project context', async () => {
      render(<K8sClusterList projectId={1} targetPlatformProfile="ocp" />);

      await waitFor(() => {
        expect(screen.getByText(/OpenShift\/OKD and other Kubernetes clusters/)).toBeInTheDocument();
        expect(screen.getByText(/Runtime support semantics follow detected platform context/)).toBeInTheDocument();
      });
    });
  });

  // ─── Delete Dialog ─────────────────────────────────────────────────

  describe('delete dialog', () => {
    it('shows delete confirmation with cluster name when delete is triggered', async () => {
      const user = userEvent.setup();
      render(<K8sClusterList projectId={1} />);

      // Wait for clusters to load
      await waitFor(() => {
        expect(screen.getByText('prod-cluster')).toBeInTheDocument();
      });

      // Open the dropdown menu for the first cluster
      // The cluster cards have "more" dropdown buttons — find them by the MoreVertical icon
      const dropdownTriggers = screen.getAllByRole('button');
      // The dropdown trigger buttons have the MoreVertical icon
      for (const btn of dropdownTriggers) {
        if (btn.querySelector('.lucide-more-vertical')) {
          await user.click(btn);
          break;
        }
      }

      // Click Delete in the dropdown
      const deleteItem = await screen.findByText('Delete');
      await user.click(deleteItem);

      // Verify the AlertDialog appears
      await waitFor(() => {
        expect(screen.getByText('Delete Cluster?')).toBeInTheDocument();
        expect(screen.getByText(/remove cluster/i)).toBeInTheDocument();
      });
    });
  });
});
