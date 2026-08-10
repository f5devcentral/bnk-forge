// Kubernetes Cluster & Resource types

export interface K8sEvent {
  type: string;
  reason: string;
  message: string;
  count?: number;
  firstTimestamp?: string;
  lastTimestamp?: string;
  // Support both camelCase and snake_case from API
  involvedObject?: { kind: string; name: string; namespace?: string };
  involved_object?: { kind: string; name: string; namespace?: string };
  metadata?: { name: string; namespace?: string; uid?: string; creationTimestamp?: string };
  source?: string;
  last_timestamp?: string;
  first_timestamp?: string;
}

export interface K8sPodContainer {
  name: string;
  image?: string;
  ports?: Array<{ containerPort: number; protocol?: string }>;
  resources?: { limits?: Record<string, string>; requests?: Record<string, string> };
  // Runtime status fields (returned by getPodContainers API)
  ready?: boolean;
  state?: string;
  restartCount?: number;
}

export interface K8sPodMetric {
  name: string;
  namespace: string;
  containers: Array<{ name: string; cpu: string; memory: string }>;
  cpu_total?: string;
  memory_total?: string;
  // Numeric fields from metrics API
  cpu_millicores?: number;
  memory_bytes?: number;
}

export interface K8sNodeMetric {
  name: string;
  cpu: string;
  memory: string;
  cpu_percent?: number;
  memory_percent?: number;
  // Numeric fields from metrics API
  cpu_millicores?: number;
  memory_bytes?: number;
  allocatable_cpu_millicores?: number;
  allocatable_memory_bytes?: number;
}

export interface K8sRolloutHistoryEntry {
  revision: number;
  change_cause?: string;
  created_at?: string;
  is_current?: boolean;
  replicas?: number;
  ready_replicas?: number;
}

export interface K8sCondition {
  type: string;
  status: string;
  reason?: string;
  message?: string;
  lastTransitionTime?: string;
  lastUpdateTime?: string;
  last_transition_time?: string;
  last_update_time?: string;
}

export interface K8sRolloutStatus {
  deployment_name: string;
  namespace: string;
  replicas?: number; // Legacy/alternate field
  ready_replicas: number;
  available_replicas: number;
  updated_replicas: number;
  unavailable_replicas?: number;
  current_replicas?: number;
  desired_replicas?: number;
  current_revision?: number;
  observed_generation?: number;
  conditions: K8sCondition[];
  rollout_complete: boolean;
  message?: string;
}

export interface K8sCluster {
  id: number;
  name: string;
  context: string;
  api_server: string;
  cloud_provider?: string;
  detected_platform_profile?: import('./platform').PlatformProfile;
  detected_platform_provider?: import('./platform').PlatformProvider | null;
  platform_capabilities?: import('./platform').PlatformCapabilities;
  platform_constraints?: import('./platform').PlatformConstraints;
  region?: string;
  default_namespace: string;
  status: string;
  version?: string;
  project_id: number;
  last_synced_at?: string;
  created_at?: string;
  updated_at?: string;
  // SSH tunnel per-cluster config
  ssh_tunnel_enabled?: boolean;
  ssh_remote_k8s_host?: string;
  ssh_remote_k8s_port?: number;
  ssh_credential_id?: number | null;
  /** Override SSH endpoint host (credential provides the key; this provides the target host) */
  ssh_host_override?: string | null;
  /** Per-cluster prereq selection — null means use defaults; locked entries (multus) are always included on read */
  enabled_prerequisites?: string[] | null;
}

export interface K8sClusterCreateRequest {
  name: string;
  kubeconfig: string; // Base64 encoded kubeconfig YAML
  cloud_provider?: string;
  region?: string;
  context?: string;
  default_namespace?: string;
  // SSH tunnel (opt-in per cluster)
  ssh_tunnel_enabled?: boolean;
  ssh_remote_k8s_host?: string;
  ssh_remote_k8s_port?: number;
  ssh_credential_id?: number | null;
  ssh_host_override?: string | null;
}

export interface K8sClusterUpdateRequest {
  name?: string;
  kubeconfig?: string;
  cloud_provider?: string;
  region?: string;
  context?: string;
  default_namespace?: string;
  // SSH tunnel
  ssh_tunnel_enabled?: boolean;
  ssh_remote_k8s_host?: string;
  ssh_remote_k8s_port?: number;
  ssh_credential_id?: number | null;
  ssh_host_override?: string | null;
  // Per-cluster prereq selection
  enabled_prerequisites?: string[] | null;
}

export interface K8sResourceType {
  key: string;
  kind: string;
  api_group: string;
  api_version: string;
  plural: string;
  namespaced: boolean;
  display_name: string;
  description?: string;
}

// K8s Container Status
export interface K8sContainerStatus {
  name: string;
  ready: boolean;
  restartCount: number;
  state?: {
    running?: { startedAt?: string };
    waiting?: { reason?: string; message?: string };
    terminated?: { exitCode?: number; reason?: string };
  };
  image?: string;
  imageID?: string;
}

// K8s Address (for nodes/services)
export interface K8sAddress {
  type: string;
  address: string;
}

// K8s Ingress status
export interface K8sLoadBalancerIngress {
  ip?: string;
  hostname?: string;
}

// K8s Resource spec/status are highly dynamic – handle deeply nested K8s API structures
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type K8sResourceSpec = Record<string, any>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type K8sResourceStatus = Record<string, any>;

// ─── Gateway API & CRD Sub-Resource Types ──────────────────────────────
// Used to eliminate `any` in detail panel components that render CRD spec/status fields.

/** Gateway API parentRef / targetRef (shared across HTTPRoute, L4Route, NetworkPolicy, SecurityPolicy) */
export interface K8sGatewayRef {
  name: string;
  namespace?: string;
  kind?: string;
  group?: string;
  sectionName?: string;
}

/** Gateway API backendRef (HTTPRoute, L4Route, TCPRoute) */
export interface K8sBackendRef {
  name: string;
  namespace?: string;
  port?: number;
  weight?: number;
  kind?: string;
  group?: string;
}

/** CRD extension ref (NetworkPolicy extensionRefs — iRules, log profiles, etc.) */
export interface K8sExtensionRef {
  kind: string;
  name: string;
  group?: string;
}

/** K8s status ancestor entry (NetworkPolicy, HTTPRoute status.ancestors) */
export interface K8sAncestorStatus {
  ancestorRef: K8sGatewayRef;
  controllerName?: string;
  conditions: K8sCondition[];
}

/** K8s status descendant entry (NetworkPolicy status.descendants) */
export interface K8sDescendantStatus {
  descendantRef: { name: string; kind?: string };
  conditions: K8sCondition[];
}

/** Gateway spec listener */
export interface K8sGatewayListener {
  name: string;
  protocol: string;
  port: number;
  hostname?: string;
  tls?: Record<string, unknown>;
}

/** Gateway status address */
export interface K8sGatewayAddress {
  type?: string;
  value: string;
}

/** HTTPRoute rule */
export interface K8sHTTPRouteRule {
  matches?: K8sHTTPRouteMatch[];
  backendRefs?: K8sBackendRef[];
  filters?: unknown[];
}

/** HTTPRoute match */
export interface K8sHTTPRouteMatch {
  path?: { type: string; value: string };
  headers?: Array<{ type?: string; name: string; value: string }>;
  queryParams?: Array<{ type?: string; name: string; value: string }>;
  method?: string;
}

/** L4Route / TCPRoute rule */
export interface K8sL4RouteRule {
  backendRefs?: K8sBackendRef[];
}

/** F5 Firewall rule (from F5BigFwPolicy / F5BigFwRulelist CRDs) */
export interface K8sFirewallRule {
  name?: string;
  action?: string;
  ipProtocol?: string;
  protocol?: string;
  logging?: boolean;
  source?: {
    addressLists?: string[];
    addresses?: string[];
    ports?: string[];
  };
  destination?: {
    addressLists?: string[];
    addresses?: string[];
    portLists?: string[];
    ports?: string[];
  };
}

/** K8s Service port (for K8sResourceViewer) */
export interface K8sServicePort {
  port: number;
  targetPort?: number | string;
  protocol?: string;
  name?: string;
  nodePort?: number;
}

/** K8s owner reference (for ResourceDescribeViewer) */
export interface K8sOwnerReference {
  name: string;
  kind: string;
  uid: string;
  controller?: boolean;
  apiVersion?: string;
}

/** K8s resource relationship (for ResourceDescribeViewer) */
export interface K8sResourceRelationship {
  kind: string;
  name: string;
  namespace?: string;
}

/** Egress route entry (string or object) */
export type K8sEgressRoute = string | {
  destination?: string;
  network?: string;
  gateway?: string;
  gw?: string;
};

/** Port list entry (string, number, or object) */
export type K8sPortEntry = string | number | {
  port?: number | string;
  name?: string;
};

/** Address list entry (string or object) */
export type K8sAddressEntry = string | {
  address?: string;
  ip?: string;
  network?: string;
};

/** SNAT pool member (string or object) */
export type K8sSnatMember = string | {
  address?: string;
  ip?: string;
};

/** VLAN interface entry (string or object) */
export type K8sVlanInterface = string | {
  name: string;
  tagged?: boolean;
};

// K8s Resource - uses flexible types for spec/status to handle dynamic K8s resource structures
export interface K8sResource {
  name: string;
  namespace?: string;
  kind: string;
  apiVersion: string;
  metadata: {
    name: string;
    namespace?: string;
    labels?: Record<string, string>;
    annotations?: Record<string, string>;
    // Support both camelCase and snake_case from API
    creationTimestamp?: string;
    creation_timestamp?: string;
    uid?: string;
    ownerReferences?: Array<{ name: string; kind: string; uid: string }>;
    owner_references?: Array<{ name: string; kind: string; uid: string }>;
  };
  // Spec and status are dynamic based on resource kind - use flexible types
  spec?: K8sResourceSpec;
  status?: K8sResourceStatus;
}

export interface K8sNamespace {
  name: string;
  status: string;
  created_at?: string;
}

export interface K8sClusterTestResult {
  success: boolean;
  message: string;
  cluster_name?: string;
  version?: string;
  api_server?: string;
  cloud_provider?: string;
  region?: string;
  status_code?: number;
}

export interface K8sResourceListResponse {
  resources: K8sResource[];
  count: number;
  resource_type: string;
  namespace?: string;
  cluster_id: number;
  /** Set when the CRD is not installed or unavailable on the cluster */
  info?: string;
}

export interface K8sNamespaceListResponse {
  namespaces: K8sNamespace[];
  count: number;
  cluster_id: number;
}

export interface K8sResourceTypesResponse {
  resource_types: K8sResourceType[];
  count: number;
}

// ─── SSH Tunnel Types ──────────────────────────────────────────────────

export interface K8sTunnelInfo {
  cluster_id: number;
  local_port: number;
  ssh_host: string;
  ssh_port: number;
  remote_host: string;
  remote_port: number;
  is_healthy: boolean;
  last_used: number;
  last_health_check: number;
  reconnect_attempts: number;
}

export interface K8sTunnelStatusResponse {
  cluster_id: number;
  cluster_name: string;
  ssh_tunnel_enabled: boolean;
  ssh_remote_k8s_host: string;
  ssh_remote_k8s_port: number;
  tunnel_active: boolean;
  tunnel: K8sTunnelInfo | null;
}

export interface K8sTunnelSummary {
  cluster_id: number;
  local_port: number;
  ssh_host: string;
  is_healthy: boolean;
  idle_seconds: number;
}

export interface K8sListAllTunnelsResponse {
  tunnels: K8sTunnelSummary[];
}

export interface K8sOpenTunnelResponse {
  success: true;
  message: string;
  local_port: number;
  tunnel: K8sTunnelInfo | null;
}

export interface K8sCloseTunnelResponse {
  success: true;
  message: string;
}

export interface K8sCloseAllTunnelsResponse {
  success: true;
  message: string;
}

// ─── Cluster Scanner Types ─────────────────────────────────────────────

export interface ClusterScanHelmRelease {
  name: string;
  namespace: string;
  version: string;
  status: string;
}

export interface ClusterScanNodeDetail {
  name: string;
  instance_type: string | null;
  zone: string | null;
  ready: boolean;
}

export interface ClusterScanClusterInfo {
  version: string | null;
  major: string | null;
  minor: string | null;
  platform: string | null;
  distribution: 'EKS' | 'AKS' | 'GKE' | 'on-prem' | 'generic';
  cloud_provider: string;
  region: string | null;
  node_count: number;
  nodes_ready: number;
  hp_nodes: number;
  hp_node_details: ClusterScanNodeDetail[];
  namespaces: number;
  /** Cheap, Node-API-only local/lab-cluster detection (issue #387 part A). Optional for backward-compat with older cached scans. */
  is_kind?: boolean;
  is_local?: boolean;
}

export interface ClusterScanCertManager {
  status: 'detected' | 'missing' | 'partial' | 'unknown';
  version: string | null;
  crds_installed: boolean;
  crd_count: number;
  crd_kinds: string[];
  missing_crds: string[];
  pods: {
    controller: number;
    webhook: number;
    cainjector: number;
    total_running: number;
  };
  helm_release: ClusterScanHelmRelease | null;
}

export interface ClusterScanMultus {
  status: 'detected' | 'missing' | 'partial' | 'unknown';
  nad_crd_installed: boolean;
  daemonset: {
    name: string;
    namespace: string;
    desired: number;
    ready: number;
  } | null;
  running_pods: number;
}

export interface ClusterScanSriov {
  status: 'detected' | 'missing' | 'partial' | 'unknown';
  device_plugin: {
    name: string;
    namespace: string;
    desired: number;
    ready: number;
    images: string[];
  } | null;
  nodes_with_vfs: number;
  total_vfs: number;
  node_details: Array<{
    name: string;
    resources: Record<string, string>;
    vf_count: number;
    instance_type: string | null;
  }>;
}

export interface ClusterScanHugepages {
  status: 'detected' | 'missing' | 'partial' | 'unknown';
  nodes_with_hugepages: number;
  node_details: Array<{
    name: string;
    hugepages_2mi: string | null;
    hugepages_1gi: string | null;
    instance_type: string | null;
    is_hp_node: boolean;
  }>;
}

export interface ClusterScanStorage {
  status: 'detected' | 'missing' | 'partial' | 'unknown';
  count: number;
  default: string | null;
  has_gp3: boolean;
  has_gp2: boolean;
  classes: Array<{
    name: string;
    provisioner: string;
    is_default: boolean;
    reclaim_policy: string;
    volume_binding_mode: string;
  }>;
}

export interface ClusterScanGatewayApi {
  status: 'detected' | 'missing' | 'partial' | 'unknown';
  crds_installed: number;
  standard_crds_found: string[];
  standard_crds_missing: string[];
  api_versions: string[];
  gatewayclasses: number;
  gateways: number;
}

export interface ClusterScanDpf {
  status: 'detected' | 'missing' | 'partial' | 'unknown';
  version: string | null;
  crds_installed: number;
  core_crds_found: string[];
  core_crds_missing: string[];
  service_crds_found: string[];
  operator: {
    configured: boolean;
    ready: boolean;
    conditions: Array<{ type: string; status: string; reason: string }>;
  };
  devices: {
    total: number;
    ready: number;
  };
  dpusets: number;
  dpuclusters: number;
  dpuservices: number;
  bfbs: number;
  helm_release: ClusterScanHelmRelease | null;
}

/** Kamaji multi-tenant K8s control plane manager (optional DPF prerequisite). */
export interface ClusterScanKamaji {
  status: 'detected' | 'missing' | 'partial' | 'unknown';
  version: string | null;
  crds_installed: number;
  core_crds_found: string[];
  core_crds_missing: string[];
  tenant_control_planes: number;
  pods_running: number;
  helm_release: ClusterScanHelmRelease | null;
}

/** One proxy / ingress controller detected in inventory mode. */
export interface ClusterScanProxy {
  proxy_type: string;
  display_name: string;
  controller: string;
  kind: 'IngressClass' | 'GatewayClass';
  found: boolean;
  namespace: string | null;
  proxy_url: string | null;
  external_url: string | null;
  backends: Array<{ service: string; namespace: string; via: string }>;
  is_bnk: boolean;
  details: Record<string, unknown>;
  error: string | null;
}

/** Existing-proxy inventory section returned by the cluster scan. */
export interface ClusterScanExistingProxies {
  status: 'detected' | 'none';
  proxies: ClusterScanProxy[];
  discovered_count: number;
  total_scanned: number;
}

export interface ClusterScanCisController {
  found: boolean;
  image: string | null;
  namespace: string | null;
  replicas_ready: number;
  bigip_url: string | null;
  login_secret_ref: { name: string | null; namespace: string | null };
}

export interface ClusterScanCisEntry {
  name: string | null;
  namespace: string | null;
}

export interface ClusterScanCis {
  status: 'detected' | 'partial' | 'missing';
  controller: ClusterScanCisController;
  external_bigip: { host: string | null; port: number | null };
  inventory: {
    virtual_servers: ClusterScanCisEntry[];
    transport_servers: ClusterScanCisEntry[];
    ingresslinks: ClusterScanCisEntry[];
  };
  eol_note: string;
}

export interface ClusterScanPrerequisites {
  cert_manager: ClusterScanCertManager;
  multus: ClusterScanMultus;
  sriov: ClusterScanSriov;
  hugepages: ClusterScanHugepages;
  storage: ClusterScanStorage;
  gateway_api: ClusterScanGatewayApi;
  dpf?: ClusterScanDpf;
  kamaji?: ClusterScanKamaji;
  existing_proxies?: ClusterScanExistingProxies;
  cis?: ClusterScanCis;
}

export interface ClusterScanBnkInstall {
  status: 'installed' | 'partial' | 'not_installed';
  health: 'healthy' | 'degraded' | null;
  /** How BNK was installed: FLO-driven Forge deploy flow vs a direct helm/manual install. Optional for backward-compat with older cached scans. */
  install_shape?: 'flo' | 'helm' | 'unknown';
  namespaces: {
    f5_operator: boolean;
    f5_bnk: boolean;
    f5_utils: boolean;
    f5_cne_core?: boolean;
  };
  crds: {
    total: number;
    groups: string[];
    has_data_plane: boolean;
    has_flo: boolean;
    has_gateway_ext: boolean;
  };
  flo: {
    version: string | null;
    pods: number;
    running: number;
    helm_release: ClusterScanHelmRelease | null;
  };
  tmm: {
    pods: number;
    running: number;
    containers: {
      total_containers: number;
      ready_containers: number;
      containers: string;
    } | null;
  };
  controller: {
    pods: number;
    running: number;
  };
  analyzer: {
    pods: number;
    running: number;
  };
  crd_installer: {
    completed: boolean;
    pods: number;
  };
  cne_instance: {
    name: string;
    features: {
      firewallACL: boolean;
      intelligentLB: boolean;
      pseudoCNI: boolean;
      metricSubsystem: boolean;
      loggingSubsystem: boolean;
    };
  } | null;
  vlans: Array<{
    name: string;
    interfaces: unknown[];
    self_ips: string[];
    mtu: number | null;
    programmed: boolean;
  }>;
}

export interface ClusterScanRecommendation {
  id: string;
  category: 'prerequisite' | 'bnk_component' | 'optimization';
  severity: 'required' | 'recommended' | 'info';
  title: string;
  description: string;
  module: string | null;
  status: 'deploy' | 'skip' | 'upgrade' | 'investigate';
}

export interface ClusterScanResponse {
  cluster_id: number;
  cluster_name: string;
  cluster_info: ClusterScanClusterInfo;
  prerequisites: ClusterScanPrerequisites;
  bnk_install: ClusterScanBnkInstall;
  recommendations: ClusterScanRecommendation[];
  platform_context?: {
    detected_platform_profile?: import('./platform').PlatformProfile;
    detected_platform_provider?: import('./platform').PlatformProvider | null;
    platform_capabilities?: import('./platform').PlatformCapabilities;
    platform_constraints?: import('./platform').PlatformConstraints;
  } | null;
  scan_metadata: {
    scanned_at: string;
    duration_ms: number;
    api_calls: number;
  };
}

// ─── HugePages Deploy Action ────────────────────────────────────────────

export type BnkDeploymentSize = 'small' | 'medium' | 'large' | 'max';

export interface HugePagesDeployRequest {
  size: BnkDeploymentSize;
  namespace?: string;
  image?: string | null;
}

export interface HugePagesDeployResponse {
  success: boolean;
  job_name: string;
  namespace: string;
  size: string;
  page_count: number;
  memory_gib_per_node: number;
  target_node_count: number;
  target_nodes: string[];
  image: string;
  message: string;
}

// ─── Node Readiness Probe (issue #387 part A — detection only) ─────────

/** Request body for POST /k8s/clusters/{id}/node-readiness/probe. */
export interface NodeReadinessProbeRequest {
  namespace?: string;
  image?: string | null;
}

export interface NodeCniPlugins {
  macvlan: boolean;
  host_device: boolean;
  ipvlan: boolean;
}

/** Per-node CNI/core_pattern/hugepages readiness, from the privileged probe. */
export interface NodeReadinessResult {
  node: string;
  cni_plugins: NodeCniPlugins;
  cni_ok: boolean;
  core_pattern: string | null;
  core_pattern_ok: boolean;
  hugepages_2mi: string | null;
  hugepages_ok: boolean;
}

export interface NodeReadinessProbeResponse {
  cluster_id: number;
  job_name: string;
  is_kind: boolean;
  is_local: boolean;
  nodes: NodeReadinessResult[];
  all_ready: boolean;
  message: string;
}

// ─── Cluster Connectivity Probe Types ───────────────────────────────────

/** Canonical connectivity status (PLAT-REL-001). */
export type ClusterConnectivityStatus = 'connected' | 'reachable' | 'partial' | 'unreachable' | 'unknown';

export interface ClusterConnectivityIcmp {
  reachable: boolean;
  latency_ms: number | null;
}

export interface ClusterConnectivityTcp {
  open: boolean;
  connect_ms: number | null;
  port: number | null;
}

export interface ClusterConnectivityK8sApi {
  accessible: boolean;
  version: string | null;
  status_code: number | null;
}

export interface ClusterConnectivityResult {
  cluster_id: number;
  cluster_name: string;
  api_server: string | null;
  status: ClusterConnectivityStatus;
  message: string;
  suggestion: string | null;
  icmp: ClusterConnectivityIcmp;
  tcp: ClusterConnectivityTcp;
  k8s_api: ClusterConnectivityK8sApi;
  checked_at: string;
}

/** Canonical connectivity summary (PLAT-REL-001). */
export interface ClusterConnectivitySummary {
  total: number;
  connected: number;
  reachable: number;
  partial: number;
  unreachable: number;
  unknown: number;
}

export interface BatchConnectivityResponse {
  results: ClusterConnectivityResult[];
  summary: ClusterConnectivitySummary;
}

// ─── Adaptive Module Selection Types ────────────────────────────────────

export type AdaptiveModuleAction = 'deploy' | 'skip' | 'upgrade' | 'investigate' | 'blocked';
export type AdaptiveModuleConfidence = 'high' | 'medium' | 'low';

export interface AdaptiveModule {
  path: string;
  name: string;
  action: AdaptiveModuleAction;
  reason: string;
  confidence: AdaptiveModuleConfidence;
  required: boolean;
  order: number;
  variable_overrides: Record<string, unknown>;
  warnings: string[];
  blockers: string[];
  detected_info: Record<string, unknown>;
}

export interface AdaptiveModulePlanResponse {
  cluster_id: number;
  cluster_name: string;
  template_slug: string | null;
  template_name: string | null;
  modules: AdaptiveModule[];
  summary: {
    deploy: number;
    skip: number;
    investigate: number;
    blocked: number;
    total: number;
  };
  global_blockers: string[];
  global_warnings: string[];
  suggested_variables: Record<string, unknown>;
  is_ready: boolean;
}
