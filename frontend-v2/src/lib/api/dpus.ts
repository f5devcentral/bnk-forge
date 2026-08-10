/**
 * DPU Provisioning API — per-project DPUs and project-level defaults.
 */
import { apiClient } from './client';
import type { components } from '@/types/api-generated';

export type Dpu = components['schemas']['DpuResponse'] & {
  /** DOCA host package readiness status, populated by rshim probe. */
  doca_status?: Record<string, unknown> | null;
};
export type DpuListResponse = components['schemas']['DpuListResponse'];
export type DpuCreate = components['schemas']['DpuCreate'];
export type DpuUpdate = components['schemas']['DpuUpdate'];
export type ProjectDpuSettings = components['schemas']['ProjectDpuSettingsResponse'];
export type ProjectDpuSettingsUpdate = components['schemas']['ProjectDpuSettingsUpdate'];

export const dpusApi = {
  list: (projectId: number) =>
    apiClient
      .get<DpuListResponse>(`/api/projects/${projectId}/dpus`)
      .then((res) => res.data),

  get: (projectId: number, dpuId: number) =>
    apiClient
      .get<Dpu>(`/api/projects/${projectId}/dpus/${dpuId}`)
      .then((res) => res.data),

  create: (projectId: number, data: DpuCreate) =>
    apiClient
      .post<Dpu>(`/api/projects/${projectId}/dpus`, data)
      .then((res) => res.data),

  update: (projectId: number, dpuId: number, data: DpuUpdate) =>
    apiClient
      .patch<Dpu>(`/api/projects/${projectId}/dpus/${dpuId}`, data)
      .then((res) => res.data),

  delete: (projectId: number, dpuId: number) =>
    apiClient.delete(`/api/projects/${projectId}/dpus/${dpuId}`).then((res) => res.data),

  getSettings: (projectId: number) =>
    apiClient
      .get<ProjectDpuSettings>(`/api/projects/${projectId}/dpu-settings`)
      .then((res) => res.data),

  updateSettings: (projectId: number, data: ProjectDpuSettingsUpdate) =>
    apiClient
      .patch<ProjectDpuSettings>(`/api/projects/${projectId}/dpu-settings`, data)
      .then((res) => res.data),

  discover: (projectId: number, dpuId: number) =>
    apiClient
      .post<{ dpu_id: number; task_id: string; status: string }>(
        `/api/projects/${projectId}/dpus/${dpuId}/discover`,
      )
      .then((res) => res.data),

  getInventory: (projectId: number, dpuId: number) =>
    apiClient
      .get<DpuInventoryResponse>(`/api/projects/${projectId}/dpus/${dpuId}/inventory`)
      .then((res) => res.data),

  discoverAll: (projectId: number) =>
    apiClient
      .post<{ enqueued: number; max_parallel: number; tasks: Array<{ dpu_id: number; task_id: string }> }>(
        `/api/projects/${projectId}/dpus/discover-all`,
      )
      .then((res) => res.data),

  probeDpuOs: (projectId: number, dpuId: number) =>
    apiClient
      .post<{ dpu_id: number; task_id: string; status: string }>(
        `/api/projects/${projectId}/dpus/${dpuId}/probe-dpu-os`,
      )
      .then((res) => res.data),

  probeDpuOsAll: (projectId: number) =>
    apiClient
      .post<{ enqueued: number; max_parallel: number; tasks: Array<{ dpu_id: number; task_id: string }> }>(
        `/api/projects/${projectId}/dpus/probe-dpu-os-all`,
      )
      .then((res) => res.data),

  runDpuConnectivityMatrix: (projectId: number) =>
    apiClient
      .post<{ project_id: number; task_id: string; status: string }>(
        `/api/projects/${projectId}/dpus/connectivity-matrix/run`,
      )
      .then((res) => res.data),

  getDpuConnectivityMatrix: (projectId: number) =>
    apiClient
      .get<{
        status: 'pending' | 'in_progress' | 'completed' | 'failed' | 'skipped' | null;
        run_at: string | null;
        matrix: {
          groups?: Record<string, {
            endpoints: Array<{
              node_id: number;
              node_ip: string;
              hostname: string | null;
              interface: string;
              addresses: Array<{ family: string; address: string }>;
            }>;
            results: Array<{
              src_node_id: number;
              src_iface: string;
              dst_node_id: number;
              dst_iface: string;
              family: string;
              dst_address: string;
              reachable: boolean;
              rtt_ms: number | null;
              error: string | null;
            }>;
          }>;
          error?: string;
          note?: string;
        } | null;
      }>(`/api/projects/${projectId}/dpus/connectivity-matrix`)
      .then((res) => res.data),

  autoAssignVlanIps: (projectId: number) =>
    apiClient
      .post<{ dpus: Dpu[]; assignments: number }>(
        `/api/projects/${projectId}/dpus/auto-assign-vlan-ips`,
      )
      .then((res) => res.data),

  revealDefaultOobPassword: (projectId: number) =>
    apiClient
      .get<{ password: string }>(`/api/projects/${projectId}/dpu-settings/reveal-oob-password`)
      .then((res) => res.data.password),

  revealDefaultOsPassword: (projectId: number) =>
    apiClient
      .get<{ password: string }>(`/api/projects/${projectId}/dpu-settings/reveal-os-password`)
      .then((res) => res.data.password),

  revealDpuBmcPassword: (projectId: number, dpuId: number) =>
    apiClient
      .get<{ password: string }>(`/api/projects/${projectId}/dpus/${dpuId}/reveal-bmc-password`)
      .then((res) => res.data.password),

  resetDpu: (projectId: number, dpuId: number, resetType: DpuResetType) =>
    apiClient
      .post<{
        dpu_id: number;
        bmc_ip: string;
        reset_type: string;
        status: string;
        http_status: number;
      }>(
        `/api/projects/${projectId}/dpus/${dpuId}/reset`,
        { reset_type: resetType },
      )
      .then((res) => res.data),

  flashDpu: (projectId: number, dpuId: number) =>
    apiClient
      .post<{ dpu_id: number; task_id: string; status: string }>(
        `/api/projects/${projectId}/dpus/${dpuId}/flash`,
      )
      .then((res) => res.data),

  flashAllDpus: (projectId: number) =>
    apiClient
      .post<{
        enqueued: number;
        max_parallel: number;
        tasks: Array<{ dpu_id: number; task_id: string }>;
        skipped_render_failures?: Array<{ dpu_id: number; error: string }>;
      }>(`/api/projects/${projectId}/dpus/flash-all`)
      .then((res) => res.data),

  cancelFlash: (projectId: number, dpuId: number) =>
    apiClient
      .delete<{ dpu_id: number; status: string }>(
        `/api/projects/${projectId}/dpus/${dpuId}/flash`,
      )
      .then((res) => res.data),

  renderBfConf: (projectId: number, dpuId: number) =>
    apiClient
      .post<{ dpu_id: number; content: string; rendered_at: string | null }>(
        `/api/projects/${projectId}/dpus/${dpuId}/render-bf-conf`,
      )
      .then((res) => res.data),

  getRenderedBfConf: (projectId: number, dpuId: number) =>
    apiClient
      .get<{ dpu_id: number; content: string; rendered_at: string | null }>(
        `/api/projects/${projectId}/dpus/${dpuId}/rendered-bf-conf`,
      )
      .then((res) => res.data),

  resetDpuNic: (projectId: number, dpuId: number) =>
    apiClient
      .post<{
        dpu_id: number;
        ssh_target: string;
        status: string;
        action: string;
        output: string;
      }>(`/api/projects/${projectId}/dpus/${dpuId}/reset-nic`)
      .then((res) => res.data),

  resetToDefaults: (
    projectId: number,
    dpuId: number,
    opts: { bios: boolean; bmc: boolean; nic: boolean; bmc_deep?: boolean },
  ) =>
    apiClient
      .post<{
        dpu_id: number;
        bmc_ip: string;
        ok: boolean;
        steps: Array<{ name: string; ran: boolean; ok: boolean; message: string }>;
      }>(`/api/projects/${projectId}/dpus/${dpuId}/reset-to-defaults`, opts)
      .then((res) => res.data),

  rshimStatus: (projectId: number, dpuId: number) =>
    apiClient
      .get<RshimStatus>(`/api/projects/${projectId}/dpus/${dpuId}/rshim-status`)
      .then((res) => res.data),

  installRshim: (projectId: number, dpuId: number) =>
    apiClient
      .post<RshimInstallQueued>(`/api/projects/${projectId}/dpus/${dpuId}/install-rshim`)
      .then((res) => res.data),

  enableRshimOnBmc: (projectId: number, dpuId: number) =>
    apiClient
      .post<RshimInstallQueued>(
        `/api/projects/${projectId}/dpus/${dpuId}/enable-rshim-on-bmc`,
      )
      .then((res) => res.data),

  disableRshim: (projectId: number, dpuId: number) =>
    apiClient
      .post<RshimInstallQueued>(
        `/api/projects/${projectId}/dpus/${dpuId}/disable-rshim`,
      )
      .then((res) => res.data),

  convertToEthernet: (projectId: number, dpuId: number) =>
    apiClient
      .post<RshimInstallQueued>(
        `/api/projects/${projectId}/dpus/${dpuId}/convert-to-ethernet`,
      )
      .then((res) => res.data),

  registerHost: (projectId: number, dpuId: number) =>
    apiClient
      .post<RegisterDpuHostResponse>(
        `/api/projects/${projectId}/dpus/${dpuId}/register-host`,
      )
      .then((res) => res.data),
};

export interface RegisterDpuHostResponse {
  host_id: number;
  status: 'created' | 'exists';
  name: string;
  host_ip: string;
}

export interface RshimInstallQueued {
  dpu_id: number;
  task_id: string;
  status: string;
}

export interface RshimStatus {
  installed: boolean;
  active: boolean;
  device_present: boolean;
  mft_installed: boolean;
  mst_running: boolean;
  message: string;
}

export interface RshimInstallLogEntry {
  command: string;
  exit_code: number;
  stdout: string;
  stderr: string;
}

export interface RshimInstallResult {
  ok: boolean;
  log: RshimInstallLogEntry[];
  status: RshimStatus | null;
  error: string | null;
}

export type DpuResetType = 'GracefulRestart' | 'ForceRestart' | 'PowerCycle';

/**
 * Full discovered inventory (mirrors DpuInventory dataclass server-side).
 * Loosely typed — the detail view renders every non-null nested field.
 */
export interface DpuInventoryResponse {
  dpu_id: number;
  last_discovery_at: string | null;
  last_discovery_status: string | null;
  last_discovery_error: string | null;
  inventory: DpuInventoryPayload | null;
}

export interface DpuInventoryPayload {
  manufacturer: string | null;
  model: string | null;
  serial_number: string | null;
  part_number: string | null;
  uuid: string | null;
  power_state: string | null;
  system_health: string | null;
  redfish_version: string | null;
  redfish_product: string | null;
  interfaces: Array<{
    name: string;
    mac: string | null;
    link_status: string | null;
    speed_mbps: number | null;
    mtu: number | null;
    interface_enabled: boolean | null;
    state: string | null;
    dhcp_v4_enabled: boolean | null;
    dhcp_v6_mode: string | null;
    ipv4_addresses: Array<Record<string, unknown>>;
    ipv4_static_addresses: Array<Record<string, unknown>>;
    ipv6_addresses: Array<Record<string, unknown>>;
    ipv6_default_gateway: string | null;
  }>;
  firmware: {
    bmc: string | null;
    bsp: string | null;
    os_image: string | null;
    uefi: string | null;
    atf: string | null;
    nic: string | null;
    ofed: string | null;
    erot: string | null;
    board: string | null;
    redfish_spec: string | null;
  };
  bios: {
    host_privilege_level: string | null;
    nic_mode: string | null;
    internal_cpu_model: string | null;
    field_mode: boolean | null;
    enable_smmu: boolean | null;
    enable_optee: boolean | null;
    disable_pcie: boolean | null;
    boot_partition_protection: boolean | null;
    spcr_uart: string | null;
    uefi_args: string | null;
    os_args: string | null;
    all_attributes: Record<string, unknown>;
  };
  manager: {
    firmware_version: string | null;
    manager_type: string | null;
    model: string | null;
    health: string | null;
    datetime: string | null;
    otp_provisioned: boolean | null;
  };
  raw: Record<string, unknown>;
}
