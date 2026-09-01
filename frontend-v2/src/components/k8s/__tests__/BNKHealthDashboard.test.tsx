/**
 * Tests for BNKHealthDashboard component
 *
 * Tests loading/error/success states, severity-sorted component cards,
 * overall status banner, drift status banner, and refresh behavior.
 *
 * MSW handlers return realistic BNK health data shapes matching the
 * BnkDataResponse type from useK8sBnk.ts.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import _userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { BNKHealthDashboard } from '../BNKHealthDashboard';

// ---------------------------------------------------------------------------
// Mock heavy child components to isolate dashboard logic
// ---------------------------------------------------------------------------

vi.mock('@/components/k8s/PodLogsViewer', () => ({
  PodLogsViewer: () => <div data-testid="pod-logs-viewer" />,
}));
vi.mock('@/components/k8s/ResourceDescribeViewer', () => ({
  ResourceDescribeViewer: () => <div data-testid="resource-describe-viewer" />,
}));

// ---------------------------------------------------------------------------
// Fixtures — realistic BNK health response
// ---------------------------------------------------------------------------

const mockBnkData = {
  health: {
    overall: 'healthy' as const,
    // Default fixture represents a confirmed FLO deploy-flow install so the
    // FLO Operator card renders in the baseline tests below; the "install
    // shape" describe block covers 'helm' / 'unknown' / absent explicitly.
    installShape: 'flo' as const,
    platform: {
      severity: 'healthy' as const,
      flo: {
        total: 1,
        running: 1,
        severity: 'healthy' as const,
        explanation: 'FLO operator is running normally',
        podDetails: [],
        remediationActions: [],
        namespaces: ['f5-bnk'],
        zones: ['us-east-1a'],
        nodes: ['worker-1'],
      },
      controller: {
        total: 1,
        running: 1,
        severity: 'healthy' as const,
        explanation: 'CNE Controller is running',
        podDetails: [],
        remediationActions: [],
        namespaces: ['f5-bnk'],
        zones: [],
        nodes: [],
      },
      crdInstaller: {
        total: 1,
        completed: 1,
        severity: 'healthy' as const,
        explanation: 'CRD Installer completed',
        podDetails: [],
        remediationActions: [],
        namespaces: ['f5-utils'],
        zones: [],
        nodes: [],
      },
      analyzer: {
        total: 0,
        running: 0,
        severity: 'unknown' as const,
        explanation: '',
        podDetails: [],
        remediationActions: [],
        namespaces: [],
        zones: [],
        nodes: [],
      },
    },
    dataPlane: {
      severity: 'healthy' as const,
      tmm: {
        pods: 2,
        running: 2,
        containersTotal: 4,
        containersReady: 4,
        totalRestarts: 0,
        severity: 'healthy' as const,
        explanation: 'All TMM pods are running',
        podDetails: [
          {
            podName: 'f5-tmm-abc12',
            namespace: 'f5-bnk',
            nodeName: 'worker-1',
            nodeZone: 'us-east-1a',
            nodeInstanceType: 'm5.large',
            phase: 'Running',
            restartCount: 0,
            containersReady: '2/2',
            issue: '',
          },
          {
            podName: 'f5-tmm-def34',
            namespace: 'f5-bnk',
            nodeName: 'worker-2',
            nodeZone: 'us-east-1b',
            nodeInstanceType: 'm5.large',
            phase: 'Running',
            restartCount: 0,
            containersReady: '2/2',
            issue: '',
          },
        ],
        remediationActions: [],
        namespaces: ['f5-bnk'],
        zones: ['us-east-1a', 'us-east-1b'],
        nodes: ['worker-1', 'worker-2'],
      },
      cneInstance: {
        name: 'bnk-instance',
        firewallACL: true,
        intelligentLB: false,
        pseudoCNI: true,
        metricSubsystem: false,
        loggingSubsystem: false,
      },
    },
    networking: {
      severity: 'healthy' as const,
      gateways: {
        total: 1,
        programmed: 1,
        accepted: 1,
        severity: 'healthy' as const,
        explanation: 'All gateways programmed',
        addresses: ['10.0.20.100'],
      },
      vlans: {
        total: 2,
        programmed: 2,
        severity: 'healthy' as const,
        explanation: 'All VLANs programmed',
        details: [
          { name: 'external', programmed: true, interfaces: ['ens5'], selfIPs: ['10.1.1.10'], mtu: null },
          { name: 'internal', programmed: true, interfaces: ['ens6'], selfIPs: ['10.2.1.10'], mtu: null },
        ],
      },
      listeners: 2,
      httpRoutes: 3,
      staticRoutes: 1,
      snatPools: 1,
    },
    security: {
      severity: 'healthy' as const,
      firewallPolicies: 2,
      securityPolicies: 1,
      networkPolicies: 0,
      addressLists: 3,
      portLists: 2,
      irules: {
        total: 1,
        accepted: 1,
        programmed: 1,
        severity: 'healthy' as const,
        explanation: 'All iRules accepted',
        details: [{ name: 'my-irule', accepted: true, programmed: true, error: null }],
      },
    },
    ai: {
      severity: 'unknown' as const,
      analyzers: 0,
      analyzerDetails: [],
    },
    counts: {
      gateways: 1,
      listeners: 2,
      httpRoutes: 3,
      vlans: 2,
      firewallPolicies: 2,
      irules: 1,
      analyzers: 0,
      cneInstances: 1,
      tmm_pods: 2,
      tmm_running: 2,
      tmm_containers: '4/4',
    },
    connectivity: {
      status: 'connected',
      message: 'Kubernetes API is accessible',
      checkedAt: '2026-01-01T00:00:00Z',
    },
    integration: {
      status: 'healthy',
      operatorConnected: true,
      operatorMode: 'direct_ws',
      operatorVersion: '1.2.3',
      lastSeen: '2026-01-01T00:00:00Z',
      message: 'Operator op-1 is connected',
    },
  },
  // Minimal topology/policy data that the unified endpoint includes
  topology: [],
  dataPlane: {},
  topologyCounts: {},
  policyAssociations: [],
  policyCount: 0,
  // Backend also returns cluster_id + namespace at top level (routes/k8s/f5bnk.py L71-77)
  cluster_id: 1,
  namespace: null,
};

const mockDriftStatus = {
  cluster_id: 1,
  project_id: 1,
  drift_enabled: true,
  total_modules: 3,
  modules_with_drift: 0,
  modules_ok: 3,
  modules_unchecked: 0,
  overall_status: 'ok' as const,
  module_statuses: [],
};

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();

  server.use(
    http.get(/\/api\/k8s\/clusters\/\d+\/f5bnk\/health/, () => {
      return HttpResponse.json(mockBnkData.health);
    }),
    http.get(/\/api\/clusters\/\d+\/drift\/status/, () => {
      return HttpResponse.json(mockDriftStatus);
    }),
    http.get(/\/api\/licensing\/\d+\/cwc-status/, () => {
      return HttpResponse.json({ status: 'unknown', expiry: null });
    }),
  );
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('BNKHealthDashboard', () => {
  // ─── Loading State ──────────────────────────────────────────────────

  describe('loading state', () => {
    it('shows loading spinner while fetching health data', () => {
      server.use(
        http.get(/\/api\/k8s\/clusters\/\d+\/f5bnk\/health/, async () => {
          await new Promise((r) => setTimeout(r, 10000));
          return HttpResponse.json(mockBnkData.health);
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);
      expect(screen.getByText('Loading BNK health data...')).toBeInTheDocument();
    });
  });

  // ─── Error State ────────────────────────────────────────────────────

  describe('error state', () => {
    it('shows error message when API fails', async () => {
      server.use(
        http.get(/\/api\/k8s\/clusters\/\d+\/f5bnk\/health/, () => {
          return HttpResponse.json({ error: 'Connection refused' }, { status: 500 });
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Server Error')).toBeInTheDocument();
      });
    });

    it('shows Retry button on error', async () => {
      server.use(
        http.get(/\/api\/k8s\/clusters\/\d+\/f5bnk\/health/, () => {
          return HttpResponse.json({ error: 'Timeout' }, { status: 500 });
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument();
      });
    });
  });

  // ─── Overall Status Banner ─────────────────────────────────────────

  describe('overall status banner', () => {
    it('shows "BNK Platform Healthy" when overall status is healthy', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('BNK Platform Healthy')).toBeInTheDocument();
      });
    });

    it('displays TMM container count in banner', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/4\/4 TMM containers/)).toBeInTheDocument();
      });
    });

    it('displays gateway count in banner', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/1 gateway/)).toBeInTheDocument();
      });
    });

    it('displays route count in banner', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/3 routes/)).toBeInTheDocument();
      });
    });

    it('displays VLAN count in banner', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/2 VLANs/)).toBeInTheDocument();
      });
    });

    it('shows "BNK Platform Critical" for critical overall status', async () => {
      server.use(
        http.get(/\/api\/k8s\/clusters\/\d+\/f5bnk\/health/, () => {
          return HttpResponse.json({ ...mockBnkData.health, overall: 'critical' });
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('BNK Platform Critical')).toBeInTheDocument();
      });
    });
  });

  // ─── Component Cards ───────────────────────────────────────────────

  describe('component cards', () => {
    it('renders FLO Operator card', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('FLO Operator')).toBeInTheDocument();
      });
    });

    it('renders CNE Controller card', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('CNE Controller')).toBeInTheDocument();
      });
    });

    it('renders CRD Installer card', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('CRD Installer')).toBeInTheDocument();
      });
    });

    it('renders TMM (Data Plane) card', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('TMM (Data Plane)')).toBeInTheDocument();
      });
    });

    it('renders Gateways card', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Gateways')).toBeInTheDocument();
      });
    });

    it('renders VLANs card', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('VLANs')).toBeInTheDocument();
      });
    });

    it('renders iRules card', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('iRules')).toBeInTheDocument();
      });
    });

    it('renders Security Policies card', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Security Policies')).toBeInTheDocument();
      });
    });

    it('does not render Analyzer card when total is 0', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('FLO Operator')).toBeInTheDocument();
      });

      // Analyzer has total=0, so it should not appear
      expect(screen.queryByText('F5 Analyzer')).not.toBeInTheDocument();
    });

    it('renders Analyzer card when analyzer count > 0', async () => {
      const dataWithAnalyzer = {
        ...mockBnkData.health,
        platform: {
          ...mockBnkData.health.platform,
          analyzer: {
            total: 1,
            running: 1,
            severity: 'healthy' as const,
            explanation: 'Analyzer is running',
            podDetails: [],
            remediationActions: [],
            namespaces: ['f5-bnk'],
            zones: [],
            nodes: [],
          },
        },
      };

      server.use(
        http.get(/\/api\/k8s\/clusters\/\d+\/f5bnk\/health/, () => {
          return HttpResponse.json(dataWithAnalyzer);
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('F5 Analyzer')).toBeInTheDocument();
      });
    });
  });

  // ─── Install Shape (direct-helm vs FLO) ─────────────────────────────

  describe('install shape', () => {
    it('shows FLO Operator card when installShape is "flo" (confirmed FLO install)', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('FLO Operator')).toBeInTheDocument();
      });
    });

    it('hides FLO Operator card when installShape is absent', async () => {
      const noShapeData = { ...mockBnkData.health, installShape: undefined };

      server.use(
        http.get(/\/api\/k8s\/clusters\/\d+\/f5bnk\/health/, () => {
          return HttpResponse.json(noShapeData);
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('CNE Controller')).toBeInTheDocument();
      });
      expect(screen.queryByText('FLO Operator')).not.toBeInTheDocument();
    });

    it('hides FLO Operator card when installShape is "unknown" (e.g. transient cluster-connectivity issue)', async () => {
      const unknownShapeData = { ...mockBnkData.health, installShape: 'unknown' };

      server.use(
        http.get(/\/api\/k8s\/clusters\/\d+\/f5bnk\/health/, () => {
          return HttpResponse.json(unknownShapeData);
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('CNE Controller')).toBeInTheDocument();
      });
      expect(screen.queryByText('FLO Operator')).not.toBeInTheDocument();
    });

    it('hides FLO Operator card when installShape is "helm"', async () => {
      const helmData = {
        ...mockBnkData.health,
        installShape: 'helm',
        installMethod: 'Helm / manual',
      };

      server.use(
        http.get(/\/api\/k8s\/clusters\/\d+\/f5bnk\/health/, () => {
          return HttpResponse.json(helmData);
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('CNE Controller')).toBeInTheDocument();
      });
      expect(screen.queryByText('FLO Operator')).not.toBeInTheDocument();
    });

    it('shows the install method badge when installMethod is present', async () => {
      const helmData = {
        ...mockBnkData.health,
        installShape: 'helm',
        installMethod: 'Helm / manual',
      };

      server.use(
        http.get(/\/api\/k8s\/clusters\/\d+\/f5bnk\/health/, () => {
          return HttpResponse.json(helmData);
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Helm / manual')).toBeInTheDocument();
      });
    });

    it('does not show an install method badge when the field is absent', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('FLO Operator')).toBeInTheDocument();
      });
      expect(screen.queryByText('Helm / manual')).not.toBeInTheDocument();
      expect(screen.queryByText('FLO deploy flow')).not.toBeInTheDocument();
    });
  });

  // ─── CNEInstance Features ──────────────────────────────────────────
  // Note: CNEInstance name and feature badges are rendered as children
  // of HealthDetailCard, which is collapsed by default (Radix Collapsible).
  // We verify the card exists by its visible header text/aria label.

  describe('CNEInstance features', () => {
    it('renders TMM card with healthy severity (implies CNEInstance present)', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        // The TMM card renders with an aria-label that includes the severity
        expect(screen.getByLabelText(/TMM.*health.*Healthy/i)).toBeInTheDocument();
      });
    });

    it('renders TMM summary with pod and container counts', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        // TMM summary text includes pod/container counts
        expect(screen.getByText(/2\/2 pods running/)).toBeInTheDocument();
      });
    });
  });

  // ─── Severity Sorting ──────────────────────────────────────────────

  describe('severity sorting', () => {
    it('sorts critical cards before healthy cards', async () => {
      const dataWithCritical = {
        ...mockBnkData.health,
        overall: 'critical' as const,
        platform: {
          ...mockBnkData.health.platform,
          flo: {
            ...mockBnkData.health.platform.flo,
            severity: 'critical' as const,
            explanation: 'FLO pod is crash-looping',
          },
        },
      };

      server.use(
        http.get(/\/api\/k8s\/clusters\/\d+\/f5bnk\/health/, () => {
          return HttpResponse.json(dataWithCritical);
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('FLO Operator')).toBeInTheDocument();
        expect(screen.getByText('CNE Controller')).toBeInTheDocument();
      });

      // FLO is critical, Controller is healthy — FLO should appear first
      const allCardNames = screen.getAllByText(/(FLO Operator|CNE Controller)/);
      expect(allCardNames[0].textContent).toBe('FLO Operator');
    });
  });

  // ─── Drift Status Banner ──────────────────────────────────────────

  describe('drift status banner', () => {
    it('shows "No Drift" banner when all modules are OK', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Configuration Drift')).toBeInTheDocument();
        expect(screen.getByText('No Drift')).toBeInTheDocument();
      });
    });

    it('shows drift detected banner when modules have drift', async () => {
      server.use(
        http.get(/\/api\/clusters\/\d+\/drift\/status/, () => {
          return HttpResponse.json({
            ...mockDriftStatus,
            modules_with_drift: 1,
            modules_ok: 2,
            overall_status: 'drift_detected',
            module_statuses: [
              {
                module_id: 1,
                module_name: 'gateway-config',
                status: 'drift',
                engine_type: 'kubernetes',
                drift_summary: '2 resources changed',
              },
            ],
          });
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Drift Detected')).toBeInTheDocument();
        expect(screen.getByText(/1 of 3 module/)).toBeInTheDocument();
      });
    });

    it('shows drifted module name and summary', async () => {
      server.use(
        http.get(/\/api\/clusters\/\d+\/drift\/status/, () => {
          return HttpResponse.json({
            ...mockDriftStatus,
            modules_with_drift: 1,
            modules_ok: 2,
            overall_status: 'drift_detected',
            module_statuses: [
              {
                module_id: 1,
                module_name: 'gateway-config',
                status: 'drift',
                engine_type: 'kubernetes',
                drift_summary: '2 resources changed',
              },
            ],
          });
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('gateway-config')).toBeInTheDocument();
        expect(screen.getByText('2 resources changed')).toBeInTheDocument();
      });
    });

    it('shows "Not Checked" when overall status is unchecked', async () => {
      server.use(
        http.get(/\/api\/clusters\/\d+\/drift\/status/, () => {
          return HttpResponse.json({
            ...mockDriftStatus,
            modules_ok: 0,
            modules_unchecked: 3,
            overall_status: 'unchecked',
          });
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Not Checked')).toBeInTheDocument();
      });
    });

    it('hides drift banner when there are no modules', async () => {
      server.use(
        http.get(/\/api\/clusters\/\d+\/drift\/status/, () => {
          return HttpResponse.json({
            ...mockDriftStatus,
            total_modules: 0,
            modules_ok: 0,
          });
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('BNK Platform Healthy')).toBeInTheDocument();
      });

      expect(screen.queryByText('Configuration Drift')).not.toBeInTheDocument();
    });
  });

  // ─── Refresh ───────────────────────────────────────────────────────

  describe('refresh', () => {
    it('renders refresh button in status banner', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('BNK Platform Healthy')).toBeInTheDocument();
      });

      // There should be a button near the Updated timestamp
      const buttons = screen.getAllByRole('button');
      expect(buttons.length).toBeGreaterThan(0);
    });

    it('shows last-updated timestamp', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText(/Updated/)).toBeInTheDocument();
      });
    });
  });

  // ─── Placement Enrichment ──────────────────────────────────────────

  describe('placement enrichment', () => {
    it('shows namespace, zone, and node chips when expanded', async () => {
      const user = _userEvent.setup();
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('TMM (Data Plane)')).toBeInTheDocument();
      });

      // Expand the TMM card
      const tmmHeader = screen.getByLabelText(/TMM.*health.*Healthy/i);
      await user.click(tmmHeader);

      // Namespace chips
      expect(screen.getByText('Namespaces')).toBeInTheDocument();
      expect(screen.getByText('f5-bnk')).toBeInTheDocument();
      // Zone chips
      expect(screen.getByText('Availability Zones')).toBeInTheDocument();
      expect(screen.getAllByText('us-east-1a').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('us-east-1b').length).toBeGreaterThanOrEqual(1);
      // Node chips
      expect(screen.getByText('Nodes')).toBeInTheDocument();
      expect(screen.getAllByText('worker-1').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('worker-2').length).toBeGreaterThanOrEqual(1);
    });

    it('shows node zone and instance type in pod details table', async () => {
      const user = _userEvent.setup();
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('TMM (Data Plane)')).toBeInTheDocument();
      });

      const tmmHeader = screen.getByLabelText(/TMM.*health.*Healthy/i);
      await user.click(tmmHeader);

      await waitFor(() => {
        expect(screen.getByText('Pod Details')).toBeInTheDocument();
      });

      expect(screen.getAllByText('us-east-1a').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('m5.large').length).toBeGreaterThanOrEqual(1);
    });
  });

  // ─── Connectivity / Integration Indicators ─────────────────────────

  describe('connectivity and integration indicators', () => {
    it('shows Connected badge when connectivity status is connected', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Connected')).toBeInTheDocument();
      });
    });

    it('shows Operator Connected badge for healthy direct_ws integration', async () => {
      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Operator Connected')).toBeInTheDocument();
      });
    });

    it('shows Kubeconfig badge when integration mode is kubeconfig', async () => {
      const kubeconfigData = {
        ...mockBnkData.health,
        integration: {
          status: 'healthy',
          operatorConnected: false,
          operatorMode: 'kubeconfig',
          operatorVersion: null,
          lastSeen: null,
          message: 'Cluster managed via kubeconfig',
        },
      };

      server.use(
        http.get(/\/api\/k8s\/clusters\/\d+\/f5bnk\/health/, () => {
          return HttpResponse.json(kubeconfigData);
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Kubeconfig')).toBeInTheDocument();
      });
    });

    it('shows Unreachable badge when connectivity status is unreachable', async () => {
      const unreachableData = {
        ...mockBnkData.health,
        connectivity: {
          status: 'unreachable',
          message: 'Kubernetes API is unreachable',
          checkedAt: '2026-01-01T00:00:00Z',
        },
      };

      server.use(
        http.get(/\/api\/k8s\/clusters\/\d+\/f5bnk\/health/, () => {
          return HttpResponse.json(unreachableData);
        }),
      );

      render(<BNKHealthDashboard clusterId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Unreachable')).toBeInTheDocument();
      });
    });
  });

  // AI card is intentionally omitted from severity-bearing dashboard cards.
});
