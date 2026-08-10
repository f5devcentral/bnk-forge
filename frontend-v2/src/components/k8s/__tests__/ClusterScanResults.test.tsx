/**
 * Tests for ClusterScanResults component
 *
 * Tests the scan CTA state, error state, scan results with prerequisites,
 * and BNK installation display.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { ClusterScanResults } from '../ClusterScanResults';

// jsdom doesn't implement Element.hasPointerCapture/setPointerCapture/
// releasePointerCapture, which Radix UI's Select uses on pointerdown/up —
// without a stub, opening the template dropdown throws "target.
// hasPointerCapture is not a function" and kills the test run.
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false;
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {};
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {};
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const mockScanResult = {
  cluster_info: {
    version: '1.28.4',
    distribution: 'EKS',
    node_count: 3,
    nodes_ready: 3,
    namespaces: 12,
    hp_nodes: 1,
    cloud_provider: 'aws',
    region: 'us-east-1',
    hp_node_details: [],
  },
  prerequisites: {
    cert_manager: {
      status: 'detected',
      version: '1.14.0',
      crd_count: 6,
      crds_installed: true,
      pods: { total_running: 3, controller: 1, webhook: 1, cainjector: 1 },
    },
    multus: {
      status: 'detected',
      nad_crd_installed: true,
      running_pods: 3,
      daemonset: { name: 'kube-multus-ds', ready: 3, desired: 3 },
    },
    sriov: {
      status: 'missing',
      device_plugin: null,
      nodes_with_vfs: 0,
      total_vfs: 0,
    },
    hugepages: {
      status: 'detected',
      nodes_with_hugepages: 1,
      node_details: [],
    },
    storage: {
      status: 'detected',
      count: 2,
      default: 'gp3',
      classes: [
        { name: 'gp3', provisioner: 'ebs.csi.aws.com', is_default: true },
        { name: 'gp2', provisioner: 'ebs.csi.aws.com', is_default: false },
      ],
    },
    gateway_api: {
      status: 'missing',
      crds_installed: 0,
      gatewayclasses: 0,
      gateways: 0,
      api_versions: [],
      standard_crds_missing: [],
    },
  },
  bnk_install: {
    status: 'not_installed',
    namespaces: { f5_operator: false, f5_utils: false },
    crds: { total: 0 },
    flo: { running: 0, pods: 0 },
    tmm: { running: 0, pods: 0 },
    controller: { running: 0, pods: 0 },
    analyzer: { running: 0, pods: 0 },
    crd_installer: { completed: false },
  },
  recommendations: [
    {
      id: 'sriov',
      category: 'prerequisite',
      title: 'Deploy SR-IOV',
      description: 'SR-IOV is required for high-performance networking',
      severity: 'required',
      status: 'deploy',
      module: 'sriov-cni',
    },
    {
      id: 'gateway-api',
      category: 'prerequisite',
      title: 'Gateway API CRDs not installed',
      description: 'Gateway API CRDs will be installed automatically by FLO.',
      severity: 'info',
      status: 'skip',
      module: null,
    },
  ],
  scan_metadata: {
    duration_ms: 1523,
    scanned_at: '2026-02-20T10:00:00Z',
  },
  platform_context: {
    detected_platform_profile: 'eks',
  },
  // Show every prereq card in tests; the component now hides any prereq
  // not listed here, defaulting to a smaller set.
  enabled_prerequisites: ['cert-manager', 'multus', 'sriov', 'hugepages', 'storage', 'gateway-api'],
};

const mockScanResultOptionalOnlyMissing = {
  ...mockScanResult,
  prerequisites: {
    ...mockScanResult.prerequisites,
    sriov: {
      status: 'detected',
      device_plugin: { name: 'sriovdp', ready: 3, desired: 3 },
      nodes_with_vfs: 2,
      total_vfs: 32,
      node_details: [],
    },
    gateway_api: {
      status: 'detected',
      crds_installed: 6,
      gatewayclasses: 1,
      gateways: 1,
      api_versions: ['v1'],
      standard_crds_missing: [],
    },
    dpf: {
      status: 'missing',
      version: null,
      crds_installed: 0,
      core_crds_found: [],
      core_crds_missing: [],
      service_crds_found: [],
      operator: {
        configured: false,
        ready: false,
        conditions: [],
      },
      devices: {
        total: 0,
        ready: 0,
      },
      dpusets: 0,
      dpuclusters: 0,
      dpuservices: 0,
      bfbs: 0,
      helm_release: null,
    },
    kamaji: {
      status: 'missing',
      version: null,
      crds_installed: 0,
      core_crds_found: [],
      core_crds_missing: [],
      tenant_control_planes: 0,
      pods_running: 0,
      helm_release: null,
    },
  },
  recommendations: [
    {
      id: 'skip-dpf',
      title: 'DPF not required',
      description: 'DPF is optional when BlueField DPU management is not used',
      severity: 'info',
      status: 'skip',
      module: null,
    },
    {
      id: 'skip-kamaji',
      title: 'Kamaji not required',
      description: 'Kamaji is optional for this cluster profile',
      severity: 'info',
      status: 'skip',
      module: null,
    },
  ],
};

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();

  // Override the default scan handler to return our complete mock data
  server.use(
    http.post('*/api/k8s/clusters/:clusterId/scan', () => {
      return HttpResponse.json(mockScanResult);
    }),
    // Adaptive module plan
    http.post('*/api/k8s/clusters/:clusterId/adaptive-modules', () => {
      return HttpResponse.json({
        template_name: 'F5 BNK 2.2',
        modules: [],
        summary: { deploy: 0, skip: 0, investigate: 0, blocked: 0 },
        is_ready: false,
        suggested_variables: {},
      });
    }),
    // Node readiness probe — default handler so the auto-run-on-local-cluster
    // effect (issue: FE polish for #387) doesn't spam MSW "unhandled request"
    // warnings in tests that don't care about its result.
    http.post('*/api/k8s/clusters/:clusterId/node-readiness/probe', () => {
      return HttpResponse.json({
        cluster_id: 1,
        job_name: 'bnk-node-readiness-default',
        is_kind: true,
        is_local: true,
        nodes: [],
        all_ready: true,
        message: 'ok',
      });
    }),
  );
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('ClusterScanResults', () => {
  it('shows scan CTA when no scan has been run', () => {
    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);

    expect(screen.getByText('Scan Cluster Prerequisites')).toBeInTheDocument();
    expect(screen.getByText(/Detect installed prerequisites/i)).toBeInTheDocument();
    // The scan button text is "Scan Cluster"
    expect(screen.getByRole('button', { name: 'Scan Cluster' })).toBeInTheDocument();
  });

  it('shows cluster name in the CTA description', () => {
    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);

    expect(screen.getByText(/dev-cluster/)).toBeInTheDocument();
  });

  it('shows scan results after clicking scan button', async () => {
    const user = userEvent.setup();
    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);

    // Click scan button
    const scanButton = screen.getByRole('button', { name: /Scan Cluster/ });
    await user.click(scanButton);

    // Wait for results to display
    await waitFor(() => {
      expect(screen.getByText('Prerequisites Missing')).toBeInTheDocument();
    });

    // Check cluster info displays
    expect(screen.getByText(/EKS 1.28.4/)).toBeInTheDocument();
    expect(screen.getByText(/3\/3 nodes ready/)).toBeInTheDocument();
  });

  it('displays prerequisite status cards after scan', async () => {
    const user = userEvent.setup();
    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);

    await user.click(screen.getByRole('button', { name: /Scan Cluster/ }));

    await waitFor(() => {
      expect(screen.getByText('cert-manager')).toBeInTheDocument();
    });

    expect(screen.getByText('Multus CNI')).toBeInTheDocument();
    expect(screen.getByText('SR-IOV')).toBeInTheDocument();
    expect(screen.getByText('HugePages')).toBeInTheDocument();
    expect(screen.getByText('Storage')).toBeInTheDocument();
    expect(screen.getByText('Gateway API')).toBeInTheDocument();
  });

  it('displays recommendations for missing prerequisites', async () => {
    const user = userEvent.setup();
    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);

    await user.click(screen.getByRole('button', { name: /Scan Cluster/ }));

    await waitFor(() => {
      expect(screen.getByText('Recommendations')).toBeInTheDocument();
    });

    expect(screen.getByText('Deploy SR-IOV')).toBeInTheDocument();
    expect(screen.getByText(/SR-IOV is required/)).toBeInTheDocument();
  });

  it('hides the FLO Operator / FLO Version / CRD Installer rows for a helm/manual BNK install', async () => {
    const user = userEvent.setup();
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/scan', () => {
        return HttpResponse.json({
          ...mockScanResult,
          bnk_install: {
            status: 'installed',
            install_shape: 'helm',
            namespaces: { f5_operator: true, f5_utils: true },
            crds: { total: 5 },
            flo: { running: 0, pods: 0 },
            tmm: { running: 2, pods: 2 },
            controller: { running: 1, pods: 1 },
            analyzer: { running: 0, pods: 0 },
            crd_installer: { completed: false },
          },
        });
      }),
    );

    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);
    await user.click(screen.getByRole('button', { name: /Scan Cluster/ }));

    await waitFor(() => {
      expect(screen.getByText('F5 BNK Installation')).toBeInTheDocument();
    });

    expect(screen.getByText('Helm / manual')).toBeInTheDocument();
    expect(screen.queryByText('FLO Operator')).not.toBeInTheDocument();
    expect(screen.queryByText('FLO Version')).not.toBeInTheDocument();
    expect(screen.queryByText('CRD Installer')).not.toBeInTheDocument();
    // Rows that still apply to helm installs stay visible.
    expect(screen.getByText('TMM Pods')).toBeInTheDocument();
    expect(screen.getByText('CNE Controller')).toBeInTheDocument();
  });

  it('shows the FLO Operator / FLO Version / CRD Installer rows for a confirmed FLO deploy flow install', async () => {
    const user = userEvent.setup();
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/scan', () => {
        return HttpResponse.json({
          ...mockScanResult,
          bnk_install: {
            status: 'installed',
            install_shape: 'flo',
            namespaces: { f5_operator: true, f5_utils: true },
            crds: { total: 5 },
            flo: { running: 1, pods: 1, version: '1.2.3' },
            tmm: { running: 2, pods: 2 },
            controller: { running: 1, pods: 1 },
            analyzer: { running: 0, pods: 0 },
            crd_installer: { completed: true },
          },
        });
      }),
    );

    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);
    await user.click(screen.getByRole('button', { name: /Scan Cluster/ }));

    await waitFor(() => {
      expect(screen.getByText('F5 BNK Installation')).toBeInTheDocument();
    });

    expect(screen.getByText('FLO deploy flow')).toBeInTheDocument();
    expect(screen.getByText('FLO Operator')).toBeInTheDocument();
    expect(screen.getByText('FLO Version')).toBeInTheDocument();
    expect(screen.getByText('CRD Installer')).toBeInTheDocument();
  });

  it('shows ready summary and required-only count when only optional prereqs are missing', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/scan', () => {
        return HttpResponse.json(mockScanResultOptionalOnlyMissing);
      }),
    );

    const user = userEvent.setup();
    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);

    await user.click(screen.getByRole('button', { name: /Scan Cluster/ }));

    await waitFor(() => {
      expect(screen.getByText('Cluster Ready')).toBeInTheDocument();
    });

    expect(screen.queryByText('Prerequisites Missing')).not.toBeInTheDocument();
    expect(screen.getByText(/6\/6 prerequisites detected/)).toBeInTheDocument();

    // Optional add-ons (DPF, Kamaji) are intentionally hidden when missing —
    // the component only renders those cards once they're actually present.
    expect(screen.queryByText('NVIDIA DPF')).not.toBeInTheDocument();
    expect(screen.queryByText('Kamaji Control Plane')).not.toBeInTheDocument();
  });

  it('keeps missing summary when a required prerequisite is missing', async () => {
    const user = userEvent.setup();
    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);

    await user.click(screen.getByRole('button', { name: /Scan Cluster/ }));

    await waitFor(() => {
      expect(screen.getByText('Prerequisites Missing')).toBeInTheDocument();
    });

    expect(screen.getByText(/4\/6 prerequisites detected/)).toBeInTheDocument();
  });

  it('shows explicit generic_onprem baseline context when scan reports that profile', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/scan', () => {
        return HttpResponse.json({
          ...mockScanResult,
          platform_context: {
            detected_platform_profile: 'generic_onprem',
          },
        });
      }),
    );

    const user = userEvent.setup();
    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);

    await user.click(screen.getByRole('button', { name: /Scan Cluster/ }));

    await waitFor(() => {
      expect(screen.getByText(/Baseline profile: Generic On-Prem/i)).toBeInTheDocument();
    });
  });

  it('does not show Prerequisites Missing when only info-severity prereq (gateway-api) is missing', async () => {
    // Cluster where only gateway-api is missing — severity info, FLO auto-installs it.
    // The banner must NOT go red; only required-severity deploy recommendations count.
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/scan', () => {
        return HttpResponse.json({
          ...mockScanResult,
          prerequisites: {
            ...mockScanResult.prerequisites,
            sriov: {
              status: 'detected',
              device_plugin: { name: 'sriovdp', ready: 3, desired: 3 },
              nodes_with_vfs: 2,
              total_vfs: 32,
              node_details: [],
            },
            // gateway_api remains missing (from mockScanResult spread)
          },
          recommendations: [
            {
              id: 'gateway-api',
              category: 'prerequisite',
              title: 'Gateway API CRDs not installed',
              description: 'Gateway API CRDs will be installed automatically by FLO.',
              severity: 'info',
              status: 'skip',
              module: null,
            },
          ],
        });
      }),
    );

    const user = userEvent.setup();
    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);

    await user.click(screen.getByRole('button', { name: /Scan Cluster/ }));

    await waitFor(() => {
      expect(screen.getByText('Cluster Ready')).toBeInTheDocument();
    });

    expect(screen.queryByText('Prerequisites Missing')).not.toBeInTheDocument();
  });

  it('shows error state when scan fails', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/scan', () => {
        return HttpResponse.json(
          { error: { message: 'Connection refused' } },
          { status: 500 },
        );
      }),
    );

    const user = userEvent.setup();
    render(<ClusterScanResults clusterId={1} />);

    await user.click(screen.getByRole('button', { name: /Scan Cluster/ }));

    await waitFor(() => {
      expect(screen.getByText('Cluster scan failed')).toBeInTheDocument();
    });

    // Should show retry button
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Lab remediation removed — the dashboard is read-only/informational
  // (issue #387 follow-up: no "prepare cluster" action on this screen).
  // -------------------------------------------------------------------------

  it('does not render a "prepare cluster" remediation action for a local/kind cluster', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/scan', () => {
        return HttpResponse.json({
          ...mockScanResult,
          cluster_info: { ...mockScanResult.cluster_info, is_kind: true, is_local: true },
        });
      }),
    );

    const user = userEvent.setup();
    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);

    await user.click(screen.getByRole('button', { name: /Scan Cluster/ }));
    // Click-only contract — the probe hasn't run yet, so the button still
    // shows its initial label.
    await screen.findByRole('button', { name: /Check node readiness/ });

    expect(
      screen.queryByRole('button', { name: /Prepare cluster for BNK/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /override/i })).not.toBeInTheDocument();
  });

  it('does not render a "prepare cluster" remediation action for a non-local cluster', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/scan', () => {
        return HttpResponse.json({
          ...mockScanResult,
          cluster_info: { ...mockScanResult.cluster_info, is_kind: false, is_local: false },
        });
      }),
    );

    const user = userEvent.setup();
    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);

    await user.click(screen.getByRole('button', { name: /Scan Cluster/ }));
    // Click-only contract — the probe hasn't run yet, so the button still
    // shows its initial label.
    await screen.findByRole('button', { name: /Check node readiness/ });

    expect(
      screen.queryByRole('button', { name: /Prepare cluster for BNK/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /override/i })).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Node Readiness — click-only probe (no auto-fire on scan)
  // -------------------------------------------------------------------------

  it('runs the node-readiness probe only when the button is clicked (local/kind cluster)', async () => {
    let probeCallCount = 0;
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/scan', () => {
        return HttpResponse.json({
          ...mockScanResult,
          cluster_info: { ...mockScanResult.cluster_info, is_kind: true, is_local: true },
        });
      }),
      http.post('*/api/k8s/clusters/:clusterId/node-readiness/probe', () => {
        probeCallCount += 1;
        return HttpResponse.json({
          cluster_id: 1,
          job_name: 'bnk-node-readiness-auto',
          is_kind: true,
          is_local: true,
          nodes: [
            {
              node: 'bnkfull-control-plane',
              cni_ok: true,
              core_pattern_ok: true,
              core_pattern: '/tmp/core.%e.%p',
              cni_plugins: { macvlan: true, host_device: true, ipvlan: true },
            },
          ],
          all_ready: true,
          message: 'ok',
        });
      }),
    );

    const user = userEvent.setup();
    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);

    await user.click(screen.getByRole('button', { name: /Scan Cluster/ }));

    // The probe does not fire on its own — initial label, no node rows, no calls yet.
    const checkButton = await screen.findByRole('button', { name: /Check node readiness/ });
    expect(screen.queryByText('bnkfull-control-plane')).not.toBeInTheDocument();
    expect(probeCallCount).toBe(0);

    // Clicking fires the probe.
    await user.click(checkButton);
    await screen.findByText('bnkfull-control-plane');
    expect(await screen.findByRole('button', { name: /Re-check node readiness/ })).toBeInTheDocument();
    expect(probeCallCount).toBe(1);

    // Re-clicking re-fires the same mutation rather than looping on its own.
    await user.click(screen.getByRole('button', { name: /Re-check node readiness/ }));
    await waitFor(() => expect(probeCallCount).toBe(2));
  });

  it('runs the node-readiness probe only when the button is clicked for a non-local (e.g. AWS EKS) cluster too — the checks are a BNK requirement everywhere', async () => {
    let probeCallCount = 0;
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/scan', () => {
        return HttpResponse.json({
          ...mockScanResult,
          cluster_info: { ...mockScanResult.cluster_info, is_kind: false, is_local: false },
        });
      }),
      http.post('*/api/k8s/clusters/:clusterId/node-readiness/probe', () => {
        probeCallCount += 1;
        return HttpResponse.json({
          cluster_id: 1,
          job_name: 'bnk-node-readiness-eks',
          is_kind: false,
          is_local: false,
          nodes: [
            {
              node: 'ip-10-0-1-42.ec2.internal',
              cni_ok: true,
              core_pattern_ok: true,
              core_pattern: '/tmp/core.%e.%p',
              cni_plugins: { macvlan: true, host_device: true, ipvlan: true },
            },
          ],
          all_ready: true,
          message: 'ok',
        });
      }),
    );

    const user = userEvent.setup();
    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);

    await user.click(screen.getByRole('button', { name: /Scan Cluster/ }));

    // Does not fire on its own even though this is not a local/kind cluster.
    const checkButton = await screen.findByRole('button', { name: /Check node readiness/ });
    expect(screen.queryByText('ip-10-0-1-42.ec2.internal')).not.toBeInTheDocument();
    expect(probeCallCount).toBe(0);

    // The privileged-probe note is shown for all clusters, even before a click.
    expect(
      screen.getByText(/Dispatches a short-lived privileged probe pod to each node\./),
    ).toBeInTheDocument();

    await user.click(checkButton);
    await screen.findByText('ip-10-0-1-42.ec2.internal');
    expect(await screen.findByRole('button', { name: /Re-check node readiness/ })).toBeInTheDocument();
    expect(probeCallCount).toBe(1);

    // No remediation action on this (informational) dashboard.
    expect(
      screen.queryByRole('button', { name: /Prepare cluster for BNK/ }),
    ).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Adaptive Deployment Plan — template selector
  // -------------------------------------------------------------------------

  it('groups cluster prerequisites (node readiness, informational only) with the deployment plan under a single "Deploy BNK" flow', async () => {
    server.use(
      http.post('*/api/k8s/clusters/:clusterId/scan', () => {
        return HttpResponse.json({
          ...mockScanResult,
          cluster_info: { ...mockScanResult.cluster_info, is_kind: true, is_local: true },
        });
      }),
    );

    const user = userEvent.setup();
    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);

    await user.click(screen.getByRole('button', { name: /Scan Cluster/ }));

    // Step 1 (prerequisites) and Step 2 (deployment plan) both render under
    // the same "Deploy BNK" heading, with Step 1 first in document order.
    await screen.findByText('Deploy BNK');
    const step1 = await screen.findByText('Step 1 · Cluster Prerequisites');
    const step2 = await screen.findByText('Step 2 · Adaptive Deployment Plan');
    expect(
      step1.compareDocumentPosition(step2) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // Node readiness content (detection, click-only) still present, now inside Step 1.
    const checkButton = await screen.findByRole('button', { name: /Check node readiness/ });
    await user.click(checkButton);
    expect(await screen.findByRole('button', { name: /Re-check node readiness/ })).toBeInTheDocument();

    // No remediation action — this dashboard informs, it doesn't fix.
    expect(
      screen.queryByRole('button', { name: /Prepare cluster for BNK/ }),
    ).not.toBeInTheDocument();

    // The old standalone card no longer exists as a separate grid card.
    expect(screen.queryByText('Node Readiness')).not.toBeInTheDocument();
  });

  it('offers F5 BNK 2.3 alongside F5 BNK 2.2 in the deployment-plan template selector', async () => {
    const user = userEvent.setup();
    render(<ClusterScanResults clusterId={1} clusterName="dev-cluster" />);

    await user.click(screen.getByRole('button', { name: /Scan Cluster/ }));
    await screen.findByText('Step 2 · Adaptive Deployment Plan');

    await user.click(screen.getByRole('combobox'));

    expect(await screen.findByRole('option', { name: 'F5 BNK 2.2' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'F5 BNK 2.3' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'BNK Demo Apps' })).toBeInTheDocument();
  });
});
