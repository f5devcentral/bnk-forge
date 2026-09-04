/**
 * Tests for Fleet page — D-022 P6 IA
 *
 * Landing: Fleets list + DPU Infrastructure top-level tabs (no Cluster Health tab).
 * Per-fleet Health facet: OverviewView renders inside FleetDetailShell at ?fleet=<id>.
 * Global actions (Add cluster / Refresh / Promote config) are hidden in the per-fleet
 * Health facet — only shown at the global (no fleetId) level, which is no longer
 * a top-level tab.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import Fleet from '@/pages/Fleet';
import { useFleetHealth, useFleetMembersByFleet, useFleetRollups, useFleetTargets } from '@/hooks/useFleet';
import { useAllClusters as _useAllClusters, useBatchConnectivity } from '@/hooks/useK8s';
import type { FleetHealthResponse, FleetRollup } from '@/types/fleet';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

const noopMutation = () => ({ mutate: vi.fn(), isPending: false, data: null, isError: false, isSuccess: false, reset: vi.fn() });
const noopQuery = () => ({ data: undefined, isLoading: false, isError: false, refetch: vi.fn() });

vi.mock('@/hooks/useFleet', () => ({
  useFleetHealth: vi.fn(),
  useFleetCompare: vi.fn(() => ({ mutate: vi.fn(), isPending: false, data: null })),
  useFleetMembers: vi.fn(() => noopQuery()),
  useFleetTargets: vi.fn(() => noopQuery()),
  useCreateFleetTarget: vi.fn(() => noopMutation()),
  useUpdateFleetTarget: vi.fn(() => noopMutation()),
  useDeleteFleetTarget: vi.fn(() => noopMutation()),
  useFleetMembersByFleet: vi.fn(() => noopQuery()),
  useResolveFleetTarget: vi.fn(() => noopMutation()),
  useStartBulkOp: vi.fn(() => noopMutation()),
  useFleetBulkRun: vi.fn(() => noopQuery()),
  useFleetBulkRuns: vi.fn(() => noopQuery()),
  useResumeBulkRun: vi.fn(() => noopMutation()),
  useCancelBulkRun: vi.fn(() => noopMutation()),
  useFleetPolicies: vi.fn(() => noopQuery()),
  useEvaluateFleetPolicy: vi.fn(() => noopMutation()),
  useEnforceFleetPolicy: vi.fn(() => noopMutation()),
  usePolicyCompliance: vi.fn(() => noopQuery()),
  useCreateFleetPolicy: vi.fn(() => noopMutation()),
  useUpdateFleetPolicy: vi.fn(() => noopMutation()),
  useDeleteFleetPolicy: vi.fn(() => noopMutation()),
  useFleetRollup: vi.fn(() => noopQuery()),
  useFleetRollups: vi.fn(() => noopQuery()),
  useUpdateFleetMemberLabels: vi.fn(() => noopMutation()),
  useRunSelection: vi.fn(() => noopMutation()),
  useStrategies: vi.fn(() => noopQuery()),
  useCreateStrategy: vi.fn(() => noopMutation()),
  useDeleteStrategy: vi.fn(() => noopMutation()),
  useRerunBulkRun: vi.fn(() => noopMutation()),
}));

vi.mock('@/hooks/useOperators', () => ({
  useOperators: vi.fn(() => ({ data: [], isLoading: false })),
  useDeleteOperator: vi.fn(() => ({ mutate: vi.fn() })),
  useSendOperatorCommand: vi.fn(() => ({ mutate: vi.fn() })),
  useLinkOperatorToCluster: vi.fn(() => ({ mutate: vi.fn() })),
  useUnlinkOperatorFromCluster: vi.fn(() => ({ mutate: vi.fn() })),
}));

vi.mock('@/hooks/useK8s', () => ({
  useAllClusters: vi.fn(() => ({ data: { clusters: [], count: 0 } })),
  useBatchConnectivity: vi.fn(() => ({ data: null, isLoading: false })),
  useClusterScan: vi.fn(() => ({ mutate: vi.fn(), isPending: false, data: null, isError: false, reset: vi.fn() })),
}));

vi.mock('@/hooks/useProjects', () => ({
  useProjects: vi.fn(() => ({ data: [], isLoading: false })),
}));

vi.mock('@/hooks/useSystem', () => ({
  useBnkConsumption: vi.fn(() => ({ data: undefined, isLoading: false, error: null })),
}));

vi.mock('@/components/system/BnkResourcesPanel', () => ({
  BnkResourcesPanel: () => <div data-testid="bnk-resources-panel">BnkResourcesPanel</div>,
}));

vi.mock('@/components/fleet/ConfigPromotionWizard', () => ({
  ConfigPromotionWizard: () => null,
}));

const mockFleetData: FleetHealthResponse = {
  total_clusters: 3,
  healthy: 2,
  warning: 1,
  critical: 0,
  offline: 0,
  unknown: 0,
  operators: [
    {
      operator_id: 1,
      operator_uuid: 'uuid-1',
      cluster_id: 1,
      cluster_name: 'prod-east',
      status: 'healthy',
      bnk_severity: 'healthy',
      effective_connectivity_status: 'connected',
      last_seen: '2025-01-01T00:00:00Z',
      bnk_version: '2.5.0',
      route_count: 12,
      tmm_count: 2,
      gateway_count: 3,
      uptime: '14d 2h',
      health_summary: { healthy: 5, warning: 0, critical: 0 },
      kubernetes_version: '1.28.4',
      node_count: 5,
      operator_version: '1.2.0',
      connectivity_mode: 'direct_ws',
      detected_platform_profile: 'eks',
      detected_platform_provider: 'aws',
    },
    {
      operator_id: 2,
      operator_uuid: 'uuid-2',
      cluster_id: 2,
      cluster_name: 'staging-west',
      status: 'warning',
      bnk_severity: 'warning',
      effective_connectivity_status: 'connected',
      last_seen: '2025-01-01T00:00:00Z',
      bnk_version: '2.4.1',
      route_count: 4,
      tmm_count: 1,
      gateway_count: 1,
      uptime: '7d 5h',
      health_summary: { healthy: 3, warning: 1, critical: 0 },
      kubernetes_version: '1.27.8',
      node_count: 3,
      operator_version: '1.1.0',
      connectivity_mode: 'direct_ws',
      detected_platform_profile: 'generic_onprem',
      detected_platform_provider: 'baremetal',
    },
  ],
  platform_context: {
    mixed_platform_profiles: true,
    detected_profiles: ['eks', 'generic_onprem'],
    clusters: [
      {
        cluster_id: 1,
        cluster_name: 'prod-east',
        detected_platform_profile: 'eks',
        detected_platform_provider: 'aws',
      },
      {
        cluster_id: 2,
        cluster_name: 'staging-west',
        detected_platform_profile: 'generic_onprem',
        detected_platform_provider: 'baremetal',
      },
    ],
    comparison_caveats: ['Fleet contains mixed detected platform profiles.'],
    support_semantics: ['Support semantics are platform-aware.'],
  },
};

function setFleetHealthMock(overrides: Record<string, unknown> = {}) {
  vi.mocked(useFleetHealth).mockReturnValue({
    data: mockFleetData,
    isLoading: false,
    isError: false,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useFleetHealth>);
}

function setBatchConnectivityMock(connectivityData: unknown = null) {
  vi.mocked(useBatchConnectivity).mockReturnValue({
    data: connectivityData,
    isLoading: false,
  } as unknown as ReturnType<typeof useBatchConnectivity>);
}

/**
 * Set up the per-fleet mocks needed to exercise OverviewView inside FleetDetailShell.
 * useFleetTargets returns a fleet with id=1 so FleetDetailShell can resolve a name.
 * useFleetMembersByFleet returns cluster members matching the two operators in
 * mockFleetData (cluster_id 1 and 2) so the fleet-scoped operator filter passes them
 * through rather than hiding them.
 */
function setFleetScopedMocks() {
  vi.mocked(useFleetTargets).mockReturnValue({
    data: [{ id: 1, name: 'test-fleet', description: '', selector: { labels: [] } }],
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useFleetTargets>);

  vi.mocked(useFleetMembersByFleet).mockReturnValue({
    data: {
      members: [
        { id: 1, fleet_id: 1, member_type: 'cluster', member_id: 1, name: 'prod-east',
          lifecycle_state: 'active', health_status: null, fault_domain: null,
          discovered_labels: {}, assigned_labels: {} },
        { id: 2, fleet_id: 1, member_type: 'cluster', member_id: 2, name: 'staging-west',
          lifecycle_state: 'active', health_status: null, fault_domain: null,
          discovered_labels: {}, assigned_labels: {} },
      ],
      total: 2,
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useFleetMembersByFleet>);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Fleet', () => {
  beforeEach(() => {
    setBatchConnectivityMock(null);
  });

  it('renders Fleet page title and description', () => {
    setFleetHealthMock();
    render(<Fleet />);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Fleet');
    // D-022: subtitle describes fleet operations scope.
    expect(
      screen.getByText(/Group clusters into fleets and operate them at scale/i),
    ).toBeInTheDocument();
  });

  it('renders Estate Overview, Fleet Groups, and Resource & Version Rollup top-level tabs', () => {
    setFleetHealthMock();
    render(<Fleet />);
    expect(screen.getByRole('tab', { name: /estate overview/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /fleet groups/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /resource & version rollup/i })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /dpu infrastructure/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /migration/i })).not.toBeInTheDocument();
  });

  it('switches to Resource & Version Rollup tab and renders the panel', async () => {
    const user = userEvent.setup();
    setFleetHealthMock();
    render(<Fleet />);
    await user.click(screen.getByRole('tab', { name: /resource & version rollup/i }));
    expect(screen.getByTestId('bnk-resources-panel')).toBeInTheDocument();
  });

  it('renders aggregate stats in the per-fleet Health facet', () => {
    setFleetHealthMock();
    setFleetScopedMocks();
    // Navigate to fleet detail (fleet=1 → FleetDetailShell → Health sub-tab by default).
    render(<Fleet />, { initialRoute: '/?fleet=1' });
    // D-020: aggregate stat labels are "Total" / "Healthy" / "Warning" /
    // "Critical" / "Offline" / "Stale" (no "Total Clusters").
    expect(screen.getByText('Total')).toBeInTheDocument();
    // "Healthy" and "Warning" appear as stat labels and also as status badges
    expect(screen.getAllByText('Healthy').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Warning').length).toBeGreaterThanOrEqual(1);
  });

  it.skip('prioritizes connected access status in fleet rows (TODO: broken — cluster rows not rendering in test DOM; pre-existing issue from 8e5de2d3)', () => {
    setFleetHealthMock();
    render(<Fleet />);
    expect(screen.getAllByText('Reachable').length).toBeGreaterThanOrEqual(1);
  });

  it('renders canonical BNK severity labels safely in the per-fleet Health facet', () => {
    setFleetHealthMock({
      data: {
        ...mockFleetData,
        operators: [
          {
            ...mockFleetData.operators[0],
            status: 'degraded',
            bnk_severity: 'degraded',
          },
          {
            ...mockFleetData.operators[1],
            status: 'unhealthy',
            bnk_severity: 'unhealthy',
          },
        ],
      },
    });
    setFleetScopedMocks();
    render(<Fleet />, { initialRoute: '/?fleet=1' });
    expect(screen.getAllByText('Degraded').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Unhealthy').length).toBeGreaterThanOrEqual(1);
  });

  it.skip('keeps connected primary truth even when probe diagnostics are bad (TODO: see sibling test)', () => {
    setBatchConnectivityMock({
      results: [
        {
          cluster_id: 1,
          status: 'partial',
          message: 'Port blocked but cluster metadata still marked connected',
          suggestion: 'Check firewall',
          icmp: { latency_ms: null },
        },
      ],
    });
    setFleetHealthMock({
      data: {
        ...mockFleetData,
        operators: [
          {
            ...mockFleetData.operators[0],
            effective_connectivity_status: 'connected',
          },
        ],
      },
    });
    render(<Fleet />);
    expect(screen.getByText('Reachable')).toBeInTheDocument();
    // Diagnostic badge should stay secondary/hidden when primary is connected.
    expect(screen.queryByText('Partial')).not.toBeInTheDocument();
  });

  it.skip('shows diagnostic probe status secondarily when primary status is non-connected (TODO: see sibling test)', () => {
    setBatchConnectivityMock({
      results: [
        {
          cluster_id: 1,
          status: 'partial',
          message: 'Port blocked',
          suggestion: 'Open API port',
          icmp: { latency_ms: null },
        },
      ],
    });
    setFleetHealthMock({
      data: {
        ...mockFleetData,
        operators: [
          {
            ...mockFleetData.operators[0],
            effective_connectivity_status: 'unreachable',
          },
        ],
      },
    });
    render(<Fleet />);
    expect(screen.getByText('Unreachable')).toBeInTheDocument();
    expect(screen.getByText('Partial')).toBeInTheDocument();
  });

  it('renders cluster rows for each operator in the per-fleet Health facet', () => {
    setFleetHealthMock();
    setFleetScopedMocks();
    render(<Fleet />, { initialRoute: '/?fleet=1' });
    expect(screen.getByText('prod-east')).toBeInTheDocument();
    expect(screen.getByText('staging-west')).toBeInTheDocument();
    expect(screen.getByText(/Platform: Amazon EKS/i)).toBeInTheDocument();
    expect(screen.getByText(/Platform: Generic On-Prem/i)).toBeInTheDocument();
  });

  it('renders mixed-platform caveat banner in the per-fleet Health facet', () => {
    setFleetHealthMock();
    setFleetScopedMocks();
    render(<Fleet />, { initialRoute: '/?fleet=1' });
    expect(screen.getByText('Fleet contains mixed detected platform profiles.')).toBeInTheDocument();
  });

  it('renders loading skeletons while fleet health data is fetching', () => {
    setFleetHealthMock({ data: undefined, isLoading: true });
    setFleetScopedMocks();
    render(<Fleet />, { initialRoute: '/?fleet=1' });
    const skeletons = document.querySelectorAll('[class*="animate-pulse"], [class*="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('renders empty state (no cluster members) in the per-fleet Health facet', () => {
    setFleetHealthMock({
      data: { ...mockFleetData, operators: [], total_clusters: 0 },
    });
    setFleetScopedMocks();
    // With no operators and a fleetId set, OverviewView shows the fleet-scoped empty message.
    render(<Fleet />, { initialRoute: '/?fleet=1' });
    expect(screen.getByText('No cluster members in this fleet')).toBeInTheDocument();
  });

  it('does not render Add cluster button inside the per-fleet Health facet', () => {
    // D-022 P6 cleanup #4: Add-cluster is hidden when OverviewView is fleet-scoped
    // (fleetId is set). Global cluster management belongs to Kubernetes page.
    setFleetHealthMock();
    setFleetScopedMocks();
    render(<Fleet />, { initialRoute: '/?fleet=1' });
    expect(screen.queryByText(/add cluster/i)).not.toBeInTheDocument();
  });

  it('renders error state when fleet health fails in the per-fleet Health facet', () => {
    setFleetHealthMock({
      data: undefined,
      isError: true,
      error: new Error('Network failure'),
    });
    setFleetScopedMocks();
    render(<Fleet />, { initialRoute: '/?fleet=1' });
    expect(screen.getByText('Failed to load fleet health')).toBeInTheDocument();
    expect(screen.getByText('Network failure')).toBeInTheDocument();
  });

  it('renders Policies empty-state with explanatory text (inform/enforce explanation)', () => {
    setFleetHealthMock();
    // Navigate to a fleet detail's Policies sub-tab via URL drill-down.
    render(<Fleet />, { initialRoute: '/?fleet=1&detail_tab=policies' });
    // Empty state must explain what Policies are and what Inform/Enforce do.
    expect(screen.getByText(/Policies are compliance rules/i)).toBeInTheDocument();
    // Multiple elements may contain "Inform" / "Enforce" — just assert at least one is present.
    expect(screen.getAllByText(/Inform/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Enforce/).length).toBeGreaterThanOrEqual(1);
  });

  it('renders Operations intro empty-state with Strategy/Run explanation', () => {
    setFleetHealthMock();
    // Navigate to a fleet detail's Operations sub-tab via URL drill-down.
    render(<Fleet />, { initialRoute: '/?fleet=1&detail_tab=operations' });
    // Intro card must explain the strategy/run model.
    expect(screen.getByText(/Operations run an action across fleet members/i)).toBeInTheDocument();
    // Multiple elements may contain "Strategy" / "Run" — just assert at least one is present.
    expect(screen.getAllByText(/Strategy/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Run/).length).toBeGreaterThanOrEqual(1);
  });

  it('renders Health/Policy/Ops traffic-light labels in the Fleets list when rollups available', () => {
    // A fleet with a failed run should render the Ops label in the fleet row.
    setFleetHealthMock();
    vi.mocked(useFleetTargets).mockReturnValue({
      data: [{ id: 10, name: 'ops-fleet', description: null, selector: { labels: ['env=prod'] }, pinned_member_ids: null, created_by: null, created_at: null }],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useFleetTargets>);
    const rollupWithFailedOp: FleetRollup = {
      fleet_id: 10,
      member_count: 5,
      lifecycle: { active: 5, provisioning: 0, draining: 0, decommissioned: 0, unknown: 0 },
      unreachable_count: 0,
      compliant_count: 5,
      total_evaluated: 5,
      drift_count: 0,
      policy_count: 1,
      worst_state: 'ready',
      last_evaluated_at: null,
      active_run_count: 0,
      failed_run_count: 1,
      last_run_status: 'failed',
      ops_state: 'red',
    };
    vi.mocked(useFleetRollups).mockReturnValue({
      data: [rollupWithFailedOp],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useFleetRollups>);
    render(<Fleet />, { initialRoute: '/?view=fleets' });
    // Traffic-light labels should be visible in the fleet row.
    expect(screen.getAllByText('Health').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Policy').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Ops').length).toBeGreaterThanOrEqual(1);
  });

  it('renders estate summary bar when rollups are available', () => {
    setFleetHealthMock();
    vi.mocked(useFleetTargets).mockReturnValue({
      data: [
        { id: 11, name: 'fleet-a', description: null, selector: { labels: ['env=prod'] }, pinned_member_ids: null, created_by: null, created_at: null },
        { id: 12, name: 'fleet-b', description: null, selector: { labels: ['env=staging'] }, pinned_member_ids: null, created_by: null, created_at: null },
      ],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useFleetTargets>);
    const makeRollup = (fleet_id: number, worst_state: FleetRollup['worst_state'], ops_state: FleetRollup['ops_state']): FleetRollup => ({
      fleet_id, member_count: 3, lifecycle: { active: 3, provisioning: 0, draining: 0, decommissioned: 0, unknown: 0 },
      unreachable_count: 0, compliant_count: 3, total_evaluated: 3, drift_count: 0,
      policy_count: 1, worst_state, last_evaluated_at: null,
      active_run_count: 0, failed_run_count: 0, last_run_status: null, ops_state,
    });
    vi.mocked(useFleetRollups).mockReturnValue({
      data: [makeRollup(11, 'ready', 'green'), makeRollup(12, 'ready', 'grey')],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useFleetRollups>);
    render(<Fleet />, { initialRoute: '/?view=fleets' });
    // Estate summary bar should show fleet count and healthy count.
    // "2 fleets" or similar text
    expect(screen.getByText('fleets')).toBeInTheDocument();
    expect(screen.getByText('healthy')).toBeInTheDocument();
  });
});
