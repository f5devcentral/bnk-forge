// Bare-metal DPU deployment types

// --- Host ---

export interface BareMetalHost {
  id: number;
  project_id: number;
  name: string;
  description: string | null;
  hostname: string | null;
  host_ip: string;
  ssh_credential_id: number | null;
  ssh_port: number;
  has_jumphost_chain: boolean;
  uses_project_jumphost?: boolean;
  bmc_ip: string | null;
  bmc_access_tier: string; // "none" | "ipmi" | "redfish"
  bmc_vendor: string | null;
  ipmi_ip: string | null;
  has_bmc_credentials: boolean;
  has_ipmi_credentials: boolean;
  topology: string | null; // "regular" | "bf3" | "bf3_ipmi" | "bmc" | "dual_dpu_obmc"
  topology_auto_detected: boolean;
  network_mode: string | null;
  vlan_id: number | null;
  gateway_ip: string | null;
  host_mgmt_ip: string | null;
  dpu_mgmt_ip: string | null;
  dpu_credential_id: number | null;
  version_profile_id: number | null;
  os_info: Record<string, unknown> | null;
  dpu_info: Record<string, unknown>[] | null;
  k8s_info: Record<string, unknown> | null;
  last_discovery_at: string | null;
  last_discovery_status: string | null; // null | "pending" | "ssh_probe" | "bmc_probe" | "dpu_probe" | "assessment" | "completed" | "failed"
  last_discovery_error: string | null;
  nic_mode: string | null;
  mst_device: string | null;
  rshim_present: boolean | null;
  default_route_iface: string | null;
  phase_c_deposited: boolean | null;
  phase_c_completed: boolean | null;
  pf_interfaces: Record<string, unknown>[] | null;
  vf_count: number | null;
  hugepages_host_gb: number | null;
  // dual_dpu_obmc topology fields
  deploy_dpu_pci_address: string | null;
  deploy_dpu_index: number | null;
  rshim_source: string | null;  // "host" | "bmc"
  bond_mode: string | null;     // "independent" | "lag"
  has_discovery_result: boolean;
  kubernetes_cluster_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface BareMetalHostCreate {
  name: string;
  description?: string;
  host_ip: string;
  ssh_credential_id?: number;
  ssh_port?: number;
  jumphost_chain?: Record<string, unknown>[];
  bmc_ip?: string;
  bmc_username?: string;
  bmc_password?: string;
  ipmi_ip?: string;
  ipmi_username?: string;
  ipmi_password?: string;
  topology?: string;
  network_mode?: string;
  vlan_id?: number;
  gateway_ip?: string;
  host_mgmt_ip?: string;
  dpu_mgmt_ip?: string;
  dpu_credential_id?: number;
  version_profile_id?: number;
  deploy_dpu_pci_address?: string;
  rshim_source?: string;
  bond_mode?: string;
}

export interface BareMetalHostUpdate {
  name?: string;
  description?: string;
  host_ip?: string;
  ssh_credential_id?: number;
  ssh_port?: number;
  jumphost_chain?: Record<string, unknown>[];
  bmc_ip?: string;
  bmc_username?: string;
  bmc_password?: string;
  ipmi_ip?: string;
  ipmi_username?: string;
  ipmi_password?: string;
  topology?: string;
  network_mode?: string;
  vlan_id?: number;
  gateway_ip?: string;
  host_mgmt_ip?: string;
  dpu_mgmt_ip?: string;
  dpu_credential_id?: number;
  version_profile_id?: number;
  deploy_dpu_pci_address?: string;
  rshim_source?: string;
  bond_mode?: string;
}

// --- Deployment ---

export interface DeploymentStep {
  id: number;
  step_index: number;
  phase: string; // "phase_1_dpu" | "phase_2_k8s" | "phase_3_join" | "phase_4_platform"
  name: string;
  description: string | null;
  status: string; // "pending" | "running" | "completed" | "failed" | "skipped"
  already_done: boolean;
  connectivity_risk: boolean;
  connectivity_mechanism: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  error_message: string | null;
  exit_code: number | null;
  created_at: string;
}

export interface DeploymentStepPlanItem {
  step_index: number;
  phase: string;
  name: string;
  description: string;
  connectivity_risk: boolean;
  estimated_duration_seconds: number;
  prerequisites: string[];
  idempotent: boolean;
  selected: boolean;
}

export interface DeploymentPlanPreview {
  topology: string;
  total_steps: number;
  selected_steps: number;
  estimated_total_duration_seconds: number;
  steps: DeploymentStepPlanItem[];
  prerequisite_warnings: string[];
}

export interface BareMetalDeployment {
  id: number;
  host_id: number;
  project_id: number;
  topology: string;
  status: string; // "pending" | "discovering" | "planning" | "in_progress" | "paused" | "completed" | "failed" | "cancelled"
  current_phase: string | null;
  current_step_index: number;
  celery_task_id: string | null;
  resume_from_step: number | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  error_message: string | null;
  error_phase: string | null;
  error_step_index: number | null;
  triggered_by: string;
  created_at: string;
  steps: DeploymentStep[];
}

export interface BareMetalDeploymentCreate {
  host_id: number;
  resume_from_step?: number;
  skip_discovery?: boolean;
}

export interface BareMetalDeploymentResumeRequest {
  from_step?: number;
}

// --- Discovery ---

export interface BareMetalDiscoveryRequest {
  probe_bmc?: boolean;
  probe_ipmi?: boolean;
  probe_dpu?: boolean;
  probe_vlan?: boolean;
}

export interface DiscoveryAssessmentCheck {
  check: string;
  status: 'pass' | 'warn' | 'fail';
  message: string;
}

export interface VersionDrift {
  component: string;
  installed: string;
  expected: string;
}

export interface ActionableAssessmentSection {
  topic: string;
  severity: 'info' | 'warning' | 'action_required';
  title: string;
  body: string;
  commands: string[];
}

export interface BareMetalDiscoveryResponse {
  host_id: number;
  host_ip: string;
  topology_recommendation: string | null;
  probes: Record<string, Record<string, unknown>>;
  assessment: DiscoveryAssessmentCheck[];
  version_drift: VersionDrift[] | null;
  recommendations: string[];
  actionable_assessment?: ActionableAssessmentSection[] | null;
  discovered_at: string;
}

export interface DiscoveryTriggerResponse {
  host_id: number;
  status: string;
}

// --- Version Profile ---

export interface BnkVersionProfile {
  id: number;
  name: string;
  display_name: string;
  description: string | null;
  is_default: boolean;
  bnk_manifest_version: string;
  bnk_cr_kind: string;
  flo_version: string;
  k8s_version: string;
  doca_version: string;
  containerd_version: string;
  runc_version: string;
  calico_version: string;
  cert_manager_version: string;
  gateway_api_version: string;
  multus_version: string;
  sriov_version: string;
  storage_class_type: string;
  storage_provisioner: string;
  feature_flags: Record<string, unknown> | null;
  created_at: string;
}

export const DISCOVERY_STATUS_LABELS: Record<string, string> = {
  pending: 'Queued',
  ssh_probe: 'SSH Probe',
  bmc_probe: 'BMC Probe',
  dpu_probe: 'DPU Probe',
  assessment: 'Assessment',
  completed: 'Discovered',
  failed: 'Failed',
};

// --- Topology helpers ---

export const TOPOLOGY_LABELS: Record<string, string> = {
  regular: 'Regular (Independent NIC)',
  bf3: 'BF3 (Mode change via BFB)',
  bf3_ipmi: 'BF3 + IPMI (mlxconfig + power cycle)',
  bmc: 'BMC (Redfish BIOS + power cycle)',
  dual_dpu_obmc: 'Dual DPU (OpenBMC rshim)',
};

export const DEPLOYMENT_PHASE_LABELS: Record<string, string> = {
  phase_1_dpu: 'Phase 1: DPU Provisioning',
  phase_2_k8s: 'Phase 2: K8s Setup',
  phase_3_join: 'Phase 3: DPU Join',
  phase_4_platform: 'Phase 4: Platform',
};

export const DEPLOYMENT_STATUS_COLORS: Record<string, string> = {
  pending: 'bg-muted/10 text-muted-foreground border-border',
  discovering: 'bg-info/10 text-info border-info/20',
  planning: 'bg-info/10 text-info border-info/20',
  in_progress: 'bg-info/10 text-info border-info/20',
  paused: 'bg-warning/10 text-warning border-warning/20',
  completed: 'bg-success/10 text-success border-success/20',
  failed: 'bg-destructive/10 text-destructive border-destructive/20',
  cancelled: 'bg-muted/10 text-muted-foreground border-border',
};

export const STEP_STATUS_COLORS: Record<string, string> = {
  pending: 'text-muted-foreground',
  running: 'text-info animate-pulse',
  completed: 'text-success',
  failed: 'text-destructive',
  skipped: 'text-muted-foreground',
};
