/**
 * Discovery API methods (read-focused for project detail surfacing).
 */
import { apiClient } from './client';

export interface DiscoveredNode {
  id: number;
  discovery_job_id: number;
  ip_address: string;
  hostname: string | null;
  ssh_credential_id: number | null;
  has_ssh_credentials: boolean;
  status: 'pending' | 'connecting' | 'discovering' | 'completed' | 'failed';
  error_message: string | null;
  os_type: string | null;
  os_version: string | null;
  os_pretty_name: string | null;
  kernel_version: string | null;
  architecture: string | null;
  is_dpu_host: boolean;
  is_dpu_node: boolean;
  dpu_count: number;
  dpu_details: Array<Record<string, unknown>> | null;
  is_dpu_bmc?: boolean;
  bmc_product?: string | null;
  bmc_serial_number?: string | null;
  // Phase 2 richer Redfish capture — populated when the probe could
  // authenticate. Keys are Redfish-aligned (Manufacturer, Model,
  // BiosVersion, BmcFirmwareVersion, NicMode, HostPrivilegeLevel,
  // PowerState, MemoryGiB, …) plus a synthetic `UsesDefaultPassword`
  // boolean when 0penBmc is what authenticated.
  bmc_redfish_payload?: Record<string, unknown> | null;
  k8s_installed: boolean;
  k8s_running: boolean;
  k8s_version: string | null;
  k8s_distribution: string | null;
  k8s_role: string | null;
  container_runtime: string | null;
  network_interfaces: NetworkInterface[] | null;
  discovered_at: string | null;
  created_at: string;
}

export interface InterfaceAddress {
  family: 'inet' | 'inet6';
  address: string;
  prefix: number | null;
  scope?: string | null;
}

export interface NetworkInterface {
  name: string;
  state: string;
  mac?: string | null;
  mtu?: number | null;
  link_type?: string;
  pci?: string | null;
  kind: 'builtin' | 'bluefield' | 'virtual';
  addresses: InterfaceAddress[];
}

export interface ConnectivityEndpoint {
  node_id: number;
  node_ip: string;
  hostname: string | null;
  interface: string;
  addresses: InterfaceAddress[];
}

export interface ConnectivityResult {
  src_node_id: number;
  src_iface: string;
  dst_node_id: number;
  dst_iface: string;
  family: 'inet' | 'inet6';
  dst_address: string;
  reachable: boolean;
  rtt_ms: number | null;
  error: string | null;
}

export interface ConnectivityGroup {
  endpoints: ConnectivityEndpoint[];
  results: ConnectivityResult[];
}

export interface ConnectivityMatrix {
  groups: Partial<Record<'builtin' | 'bluefield', ConnectivityGroup>>;
  error?: string;
}

export interface DiscoveryJob {
  id: number;
  project_id: number;
  ssh_credential_id: number | null;
  has_shared_credentials: boolean;
  status: 'pending' | 'in_progress' | 'completed' | 'failed' | 'cancelled';
  total_nodes: number;
  completed_nodes: number;
  failed_nodes: number;
  celery_task_id: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  created_by: string | null;
  connectivity_status: 'skipped' | 'pending' | 'in_progress' | 'completed' | 'failed' | null;
  connectivity_matrix: ConnectivityMatrix | null;
  nodes: DiscoveredNode[];
}

export interface DiscoveryJobListResponse {
  jobs: DiscoveryJob[];
}

export interface KubeconfigContext {
  name: string;
  cluster: string;
  api_server: string;
  is_current: boolean;
  kubeconfig_b64: string;
}

export interface NodeKubeconfigProbeResult {
  contexts: KubeconfigContext[];
  host: string;
  node_ip: string;
}

export interface DiscoveryNodeInput {
  ip_address: string;
}

export interface DiscoveryTriggerRequest {
  nodes: DiscoveryNodeInput[];
  ssh_credential_id?: number;
  ssh_username?: string;
  ssh_port?: number;
  ssh_auth_type?: 'password' | 'key';
  ssh_password?: string;
  ssh_private_key?: string;
}

export interface DiscoveryTriggerResponse {
  job: DiscoveryJob;
}

export const discoveryApi = {
  listDiscoveryJobs: (projectId: number, limit = 20) =>
    apiClient
      .get<DiscoveryJobListResponse>(`/api/projects/${projectId}/discovery`, { params: { limit } })
      .then((res) => res.data.jobs),

  triggerDiscovery: (projectId: number, payload: DiscoveryTriggerRequest) =>
    apiClient
      .post<DiscoveryTriggerResponse>(`/api/projects/${projectId}/discovery`, payload)
      .then((res) => res.data.job),

  getDiscoveryJob: (projectId: number, jobId: number) =>
    apiClient
      .get<DiscoveryJob>(`/api/projects/${projectId}/discovery/${jobId}`)
      .then((res) => res.data),

  rerunDiscoveryJob: (projectId: number, jobId: number) =>
    apiClient
      .post<DiscoveryTriggerResponse>(`/api/projects/${projectId}/discovery/${jobId}/rerun`)
      .then((res) => res.data.job),

  deleteDiscoveryJob: (projectId: number, jobId: number) =>
    apiClient
      .delete(`/api/projects/${projectId}/discovery/${jobId}`)
      .then((res) => res.data),

  probeNodeKubeconfig: (projectId: number, nodeId: number) =>
    apiClient
      .post<NodeKubeconfigProbeResult>(
        `/api/projects/${projectId}/discovery/nodes/${nodeId}/probe-kubeconfig`
      )
      .then((res) => res.data),

  registerNodeAsDpu: (
    projectId: number,
    nodeId: number,
    body: RegisterAsDpuRequest,
  ) =>
    apiClient
      .post<RegisterAsDpuResponse>(
        `/api/projects/${projectId}/discovery/nodes/${nodeId}/register-as-dpu`,
        body,
      )
      .then((res) => res.data),

  registerNodeAsHost: (projectId: number, nodeId: number) =>
    apiClient
      .post<RegisterAsHostResponse>(
        `/api/projects/${projectId}/discovery/nodes/${nodeId}/register-as-host`,
        {},
      )
      .then((res) => res.data),

  registerAllHostsInJob: (projectId: number, jobId: number) =>
    apiClient
      .post<RegisterAllHostsResponse>(
        `/api/projects/${projectId}/discovery/${jobId}/register-all-hosts`,
      )
      .then((res) => res.data),
};

export interface RegisterAsDpuRequest {
  bmc_ip?: string;
  bmc_username?: string;
  bmc_password?: string;
  name?: string;
}

export interface RegisteredDpuSummary {
  dpu_id: number;
  project_id: number;
  access_mode: 'bmc' | 'in-band';
  bmc_ip: string | null;
  dpu_os_ip: string | null;
  host_node_ip: string | null;
  pci_address: string | null;
}

export interface RegisterAsDpuResponse {
  dpus: RegisteredDpuSummary[];
}

export interface RegisterAsHostResponse {
  host_id: number;
  status: 'created' | 'exists';
  name: string;
  host_ip: string;
}

export interface RegisterAllHostsResponse {
  created: number;
  existing: number;
  hosts: RegisterAsHostResponse[];
}
