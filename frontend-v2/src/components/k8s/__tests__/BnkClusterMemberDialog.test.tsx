/**
 * Tests for BnkClusterMemberDialog (ADR-424 F7)
 *
 * The dialog's API is declarative-destructive: absence from the payload == removal.
 * CT-012: MSW handlers return the real backend response shapes.
 *
 * Backend routes:
 *   GET  /api/projects/{id}/bare-metal/hosts  → { hosts: BareMetalHost[] }
 *   GET  /api/projects/{id}/dpus              → { dpus: DpuResponse[] }
 *   POST /api/k8s/clusters/{id}/bnk-members   → BnkClusterMemberAssignResponse
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { BnkClusterMemberDialog } from '../BnkClusterMemberDialog';
import type { BnkClusterConfigSummary } from '@/types';

vi.mock('@/lib/notify', () => ({
  notify: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

// ---------------------------------------------------------------------------
// Fixtures — minimal shapes covering fields the component reads
// ---------------------------------------------------------------------------

// GET /api/projects/1/bare-metal/hosts → { hosts: [...] }
// Real shape: backend/schemas/bare_metal.py BareMetalHostResponse
const mockHosts = [
  {
    id: 1,
    project_id: 1,
    name: 'host-1',
    description: null,
    hostname: 'host-1.lab',
    host_ip: '10.0.0.1',
    ssh_credential_id: null,
    ssh_port: 22,
    has_jumphost_chain: false,
    bmc_ip: null,
    bmc_access_tier: 'none',
    bmc_vendor: null,
    ipmi_ip: null,
    has_bmc_credentials: false,
    has_ipmi_credentials: false,
    topology: 'regular',
    topology_auto_detected: false,
    network_mode: null,
    vlan_id: null,
    gateway_ip: null,
    host_mgmt_ip: null,
    dpu_mgmt_ip: null,
    dpu_credential_id: null,
    version_profile_id: null,
    os_info: null,
    dpu_info: null,
    k8s_info: null,
    last_discovery_at: null,
    last_discovery_status: 'completed',
    last_discovery_error: null,
    nic_mode: null,
    mst_device: null,
    rshim_present: null,
    default_route_iface: null,
    phase_c_deposited: null,
    phase_c_completed: null,
    pf_interfaces: null,
    vf_count: null,
    hugepages_host_gb: null,
    deploy_dpu_pci_address: null,
    deploy_dpu_index: null,
    rshim_source: null,
    bond_mode: null,
    net_rshim_mac_base: null,
    has_discovery_result: true,
    kubernetes_cluster_id: 42, // already in this cluster (not foreign)
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 2,
    project_id: 1,
    name: 'host-2',
    description: null,
    hostname: 'host-2.lab',
    host_ip: '10.0.0.2',
    ssh_credential_id: null,
    ssh_port: 22,
    has_jumphost_chain: false,
    bmc_ip: null,
    bmc_access_tier: 'none',
    bmc_vendor: null,
    ipmi_ip: null,
    has_bmc_credentials: false,
    has_ipmi_credentials: false,
    topology: 'regular',
    topology_auto_detected: false,
    network_mode: null,
    vlan_id: null,
    gateway_ip: null,
    host_mgmt_ip: null,
    dpu_mgmt_ip: null,
    dpu_credential_id: null,
    version_profile_id: null,
    os_info: null,
    dpu_info: null,
    k8s_info: null,
    last_discovery_at: null,
    last_discovery_status: 'completed',
    last_discovery_error: null,
    nic_mode: null,
    mst_device: null,
    rshim_present: null,
    default_route_iface: null,
    phase_c_deposited: null,
    phase_c_completed: null,
    pf_interfaces: null,
    vf_count: null,
    hugepages_host_gb: null,
    deploy_dpu_pci_address: null,
    deploy_dpu_index: null,
    rshim_source: null,
    bond_mode: null,
    net_rshim_mac_base: null,
    has_discovery_result: true,
    kubernetes_cluster_id: 42,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
];

// GET /api/projects/1/dpus → { dpus: [...] }
// Real shape: backend/schemas/dpu.py DpuResponse (relevant fields only)
// dpu-1 is owned by host-1 (host_node_ip matches host_ip)
// dpu-2 is owned by host-2
const mockDpus = [
  {
    id: 10,
    project_id: 1,
    name: 'dpu-1',
    serial_number: 'SN-10',
    host_node_ip: '10.0.0.1', // owner host: host-1
    kubernetes_cluster_id: 42,
    last_discovery_status: 'completed',
    nic_mode: 'dpu',
    oob0_ipv4: '192.168.1.10',
    dpu_os_ip: null,
    host_hostname: 'host-1.lab',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 11,
    project_id: 1,
    name: 'dpu-2',
    serial_number: 'SN-11',
    host_node_ip: '10.0.0.2', // owner host: host-2
    kubernetes_cluster_id: 42,
    last_discovery_status: 'completed',
    nic_mode: 'dpu',
    oob0_ipv4: '192.168.1.11',
    dpu_os_ip: null,
    host_hostname: 'host-2.lab',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
];

// BnkClusterConfigSummary — the dialog's currentConfig prop
// host 1 is the control plane; both hosts + both DPUs are in the cluster
const currentConfig: BnkClusterConfigSummary = {
  id: 1,
  cluster_id: 42,
  control_plane_host_id: 1,
  tmfifo_pool_cidr: '192.168.100.0/22',
  join_transport: 'rshim',
  host_ids: [1, 2],
  dpu_ids: [10, 11],
};

// Real BnkClusterMemberAssignResponse shape (CT-012: backend/schemas/k8s.py)
// assigned_dpus: { dpu_id, dpu_name, host_tmfifo_ip, dpu_tmfifo_ip, subnet_cidr }
// bnk_config: BnkClusterConfigSummary (includes host_ids + dpu_ids)
function makeAssignResponse(hostIds: number[], dpuIds: number[]) {
  return {
    cluster_id: 42,
    control_plane_host_id: 1,
    host_ids: hostIds,
    assigned_dpus: dpuIds.map((id, i) => ({
      dpu_id: id,
      dpu_name: `dpu-${id}`,
      host_tmfifo_ip: `192.168.100.${i * 4 + 1}`,
      dpu_tmfifo_ip: `192.168.100.${i * 4 + 2}`,
      subnet_cidr: `192.168.100.${i * 4}/30`,
    })),
    bnk_config: {
      id: 1,
      cluster_id: 42,
      control_plane_host_id: 1,
      tmfifo_pool_cidr: '192.168.100.0/22',
      join_transport: 'rshim',
      host_ids: hostIds,
      dpu_ids: dpuIds,
    },
  };
}

const defaultProps = {
  open: true,
  onOpenChange: vi.fn(),
  clusterId: 42,
  projectId: 1,
  clusterName: 'test-cluster',
  currentConfig,
};

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  server.use(
    http.get('*/api/projects/1/bare-metal/hosts', () => {
      return HttpResponse.json({ hosts: mockHosts });
    }),
    http.get('*/api/projects/1/dpus', () => {
      return HttpResponse.json({ dpus: mockDpus });
    }),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('BnkClusterMemberDialog', () => {
  // (a) Seeds checkboxes from currentConfig.host_ids / dpu_ids

  it('seeds member selections from currentConfig host_ids and dpu_ids', async () => {
    render(<BnkClusterMemberDialog {...defaultProps} />);

    // Wait for hosts and DPUs to load
    await waitFor(() => {
      expect(screen.getByText('host-1.lab')).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText('dpu-1')).toBeInTheDocument();
    });

    // host-1 is the CP — its checkbox is disabled but still rendered
    // host-2 is a non-CP member — its checkbox should be checked (from host_ids=[1,2])
    const host2Checkbox = screen.getByRole('checkbox', { name: /host-2\.lab/i });
    expect(host2Checkbox).toBeChecked();

    // Both DPUs should be checked (seeded from dpu_ids=[10,11])
    const dpu1Checkbox = screen.getByRole('checkbox', { name: /dpu-1/i });
    const dpu2Checkbox = screen.getByRole('checkbox', { name: /dpu-2/i });
    expect(dpu1Checkbox).toBeChecked();
    expect(dpu2Checkbox).toBeChecked();
  });

  // (b) Unchecking a DPU and saving sends the reduced dpu_ids

  it('sends reduced dpu_ids when a DPU is unchecked before saving', async () => {
    const user = userEvent.setup();
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.post('*/api/k8s/clusters/42/bnk-members', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeAssignResponse([1, 2], [11]));
      }),
    );

    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(<BnkClusterMemberDialog {...defaultProps} />);

    // Wait for DPU list to be ready
    await waitFor(() => expect(screen.getByText('dpu-1')).toBeInTheDocument());

    // Uncheck dpu-1 (id=10)
    const dpu1Checkbox = screen.getByRole('checkbox', { name: /dpu-1/i });
    await user.click(dpu1Checkbox);

    const saveButton = screen.getByRole('button', { name: /Save & Orchestrate/i });
    await user.click(saveButton);

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // dpu-1 removed; dpu-2 remains
    expect(capturedBody!.dpu_ids).toEqual([11]);
    // Both hosts remain
    expect((capturedBody!.host_ids as number[]).sort()).toEqual([1, 2]);
  });

  // (c) Unchecking a host auto-unchecks its DPUs (F4.2)

  it('auto-unchecks DPUs owned by a host when that host is unchecked', async () => {
    const user = userEvent.setup();

    render(<BnkClusterMemberDialog {...defaultProps} />);

    await waitFor(() => expect(screen.getByText('host-2.lab')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('dpu-2')).toBeInTheDocument());

    // dpu-2 should initially be checked
    expect(screen.getByRole('checkbox', { name: /dpu-2/i })).toBeChecked();

    // Uncheck host-2 (owner of dpu-2 via host_node_ip=10.0.0.2)
    const host2Checkbox = screen.getByRole('checkbox', { name: /host-2\.lab/i });
    await user.click(host2Checkbox);

    // dpu-2 must be auto-unchecked because its owner host was removed
    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: /dpu-2/i })).not.toBeChecked();
    });

    // dpu-1 (owned by host-1) must be unaffected
    expect(screen.getByRole('checkbox', { name: /dpu-1/i })).toBeChecked();

    // host-2 itself must also be unchecked
    expect(screen.getByRole('checkbox', { name: /host-2\.lab/i })).not.toBeChecked();
  });

  // (d) confirm() guard fires when members are removed; no-op when user declines (F4.1)

  it('prompts confirm() when removing a DPU and aborts if user declines', async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

    render(<BnkClusterMemberDialog {...defaultProps} />);

    await waitFor(() => expect(screen.getByText('dpu-1')).toBeInTheDocument());

    // Uncheck dpu-1 to trigger a removal
    const dpu1Checkbox = screen.getByRole('checkbox', { name: /dpu-1/i });
    await user.click(dpu1Checkbox);

    const saveButton = screen.getByRole('button', { name: /Save & Orchestrate/i });
    await user.click(saveButton);

    // confirm() must have been called
    expect(confirmSpy).toHaveBeenCalledOnce();
    expect(confirmSpy.mock.calls[0][0]).toMatch(/remove/i);

    // User declined → dialog must not close
    expect(defaultProps.onOpenChange).not.toHaveBeenCalled();
  });

  // (e) MSW handler returns real response shape; assert request payload (CT-012)

  it('sends correctly shaped BnkClusterMemberAssignRequest and receives real response shape', async () => {
    const user = userEvent.setup();
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.post('*/api/k8s/clusters/42/bnk-members', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeAssignResponse([1, 2], [10, 11]));
      }),
    );

    render(<BnkClusterMemberDialog {...defaultProps} />);

    // Wait for the component to finish loading and seed selections
    await waitFor(() => expect(screen.getByText('host-1.lab')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('dpu-1')).toBeInTheDocument());

    // Save without any changes — both hosts and both DPUs are still selected,
    // so no removal occurs and no confirm is needed
    const saveButton = screen.getByRole('button', { name: /Save & Orchestrate/i });
    await user.click(saveButton);

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // BnkClusterMemberAssignRequest: { control_plane_host_id, host_ids, dpu_ids, tmfifo_pool_cidr }
    expect(capturedBody).toMatchObject({
      control_plane_host_id: 1,
      tmfifo_pool_cidr: '192.168.100.0/22',
    });
    expect((capturedBody!.host_ids as number[]).sort()).toEqual([1, 2]);
    expect((capturedBody!.dpu_ids as number[]).sort()).toEqual([10, 11]);

    // Dialog closes after successful save
    await waitFor(() => {
      expect(defaultProps.onOpenChange).toHaveBeenCalledWith(false);
    });
  });
});
