// F5 BNK & Upgrade types

import type { JsonValue } from './common';

export interface F5FirewallRule {
  name: string;
  action: string;
  ipProtocol: string;
  source: {
    addresses?: string[];
    ports?: number[];
    addressLists?: string[];
    portLists?: string[];
  };
  destination: {
    addresses?: string[];
    ports?: number[];
    addressLists?: string[];
    portLists?: string[];
  };
  logging: boolean;
}

export interface F5GatewayPolicyAssociation {
  kind?: 'gateway';
  bnk_policy_name?: string;
  namespace: string;
  gateway_name?: string;
  listener_name?: string;
  firewall_policy_name: string;
  gateway_ip?: string;
  port?: number;
  protocol?: string;
  rules_count?: number;
  rules?: F5FirewallRule[];
  egress_name?: string;
  captured_namespaces?: string[];
  snat_type?: string;
}

export interface F5EgressPolicyAssociation {
  kind: 'egress';
  namespace: string;
  egress_name?: string;
  firewall_policy_name: string;
  captured_namespaces?: string[];
  snat_type?: string;
  rules_count?: number;
  rules?: F5FirewallRule[];
  bnk_policy_name?: string;
  gateway_name?: string;
  listener_name?: string;
}

export type F5PolicyGatewayAssociation = F5GatewayPolicyAssociation | F5EgressPolicyAssociation;

export interface F5PolicyGatewayAssociationsResponse {
  associations: F5PolicyGatewayAssociation[];
  count: number;
  cluster_id: number;
  namespace?: string;
  error?: string;
  info?: string;
}

// BNK Health Types

/**
 * Canonical health severity (PLAT-REL-001).
 * Includes legacy terms for backward compat with BNK health API
 * until fleet/BNK endpoints migrate to canonical vocabulary.
 */
export type HealthSeverity =
  | 'healthy' | 'degraded' | 'unhealthy' | 'unknown'  // Canonical (PLAT-REL-001)
  | 'warning' | 'critical';                            // Legacy (BNK health API)

export interface HealthRemediationAction {
  label: string;
  action: 'view_logs' | 'restart_pod' | 'describe' | 'diagnostics';
  target: string;
  namespace: string;
}

export interface HealthPodDetail {
  podName: string;
  namespace: string;
  nodeName?: string | null;
  hostIP?: string | null;
  phase: string;
  restartCount: number;
  containersReady: string;
  issue: string;
}

export interface HealthComponentEnrichment {
  explanation: string;
  podDetails: HealthPodDetail[];
  remediationActions: HealthRemediationAction[];
}

export interface HealthPlatformComponent extends HealthComponentEnrichment {
  total: number;
  running?: number;
  completed?: number;
  severity: HealthSeverity;
}

export interface HealthTmmComponent extends HealthComponentEnrichment {
  pods: number;
  running: number;
  containersTotal: number;
  containersReady: number;
  totalRestarts: number;
  severity: HealthSeverity;
}

export interface HealthGatewayComponent {
  total: number;
  programmed: number;
  accepted: number;
  severity: HealthSeverity;
  explanation: string;
  addresses: string[];
}

export interface HealthVlanComponent {
  total: number;
  programmed: number;
  severity: HealthSeverity;
  explanation: string;
  details: Array<{
    name: string;
    programmed: boolean;
    interfaces: string[];
    selfIPs: string[];
    mtu: number | null;
  }>;
}

export interface HealthIRulesComponent {
  total: number;
  accepted: number;
  programmed: number;
  severity: HealthSeverity;
  explanation: string;
  details: Array<{
    name: string;
    accepted: boolean;
    programmed: boolean;
    error: string | null;
  }>;
}

export interface HealthCneInstance {
  name: string;
  firewallACL: boolean;
  intelligentLB: boolean;
  pseudoCNI: boolean;
  metricSubsystem: boolean;
  loggingSubsystem: boolean;
}

export interface BnkHealthResponse {
  overall: HealthSeverity;
  installShape?: string;
  installMethod?: string;
  platform: {
    severity: HealthSeverity;
    flo: HealthPlatformComponent;
    controller: HealthPlatformComponent;
    crdInstaller: HealthPlatformComponent;
    analyzer: HealthPlatformComponent;
  };
  dataPlane: {
    severity: HealthSeverity;
    tmm: HealthTmmComponent;
    cneInstance: HealthCneInstance | Record<string, never>;
  };
  networking: {
    severity: HealthSeverity;
    gateways: HealthGatewayComponent;
    vlans: HealthVlanComponent;
    listeners: number;
    httpRoutes: number;
    staticRoutes: number;
    snatPools: number;
  };
  security: {
    severity: HealthSeverity;
    firewallPolicies: number;
    securityPolicies: number;
    networkPolicies: number;
    addressLists: number;
    portLists: number;
    irules: HealthIRulesComponent;
  };
  ai: {
    severity: HealthSeverity;
    analyzers: number;
    analyzerDetails: Array<{
      name: string;
      namespace: string;
      schedule: string;
    }>;
  };
  counts: {
    gateways: number;
    listeners: number;
    httpRoutes: number;
    vlans: number;
    firewallPolicies: number;
    irules: number;
    analyzers: number;
    cneInstances: number;
    tmm_pods: number;
    tmm_running: number;
    tmm_containers: string;
  };
}

// BNK Upgrade Types
export interface BnkUpgradePreCheck {
  name: string;
  label: string;
  status: 'pass' | 'fail' | 'warn';
  detail: string;
  critical?: boolean;
}

export interface BnkUpgradePlanStep {
  step: number;
  action: string;
  label: string;
  module?: string;
  phase?: string;
  timeout?: number;
}

export interface BnkUpgrade {
  id: number;
  cluster_id: number;
  project_id?: number;
  from_version?: string;
  to_version: string;
  status: 'planning' | 'ready' | 'in_progress' | 'health_check' | 'completed' | 'failed' | 'rolling_back' | 'rolled_back' | 'cancelled';
  pre_check_passed: boolean;
  pre_checks?: BnkUpgradePreCheck[];
  plan?: BnkUpgradePlanStep[];
  total_steps: number;
  current_step: number;
  step_results?: Array<{
    step: number;
    label: string;
    action: string;
    status: string;
    started_at?: string;
    completed_at?: string;
    output?: string;
    error?: string;
    health?: Record<string, JsonValue>;
  }>;
  pre_health?: Record<string, JsonValue>;
  post_health?: Record<string, JsonValue>;
  health_gate_passed?: boolean;
  rollback_available: boolean;
  error_message?: string;
  error_step?: number;
  triggered_by?: string;
  approved_by?: string;
  started_at?: string;
  completed_at?: string;
  duration_seconds?: number;
  celery_task_id?: string;
  created_at?: string;
}

export interface BnkVersionInfo {
  version: string;
  label: string;
  release_date?: string;
  notes?: string;
  min_k8s?: string;
  max_k8s?: string;
}

export interface BnkReleaseRegistryItem {
  id: number;
  ga_label: string;
  product_line: string;
  manifest_version: string | null;
  flo_version_prefix: string | null;
  flo_version_min: string | null;
  flo_version_max: string | null;
  min_k8s: string | null;
  max_k8s: string | null;
  source_type: 'clouddocs' | 'oci' | 'observed' | 'manual';
  source_url: string | null;
  notes: string | null;
  is_active: boolean;
}

export interface BnkReleaseRegistryResponse {
  releases: BnkReleaseRegistryItem[];
  total: number;
}

export interface BnkCurrentVersion {
  status: 'installed' | 'partial' | 'not_installed';
  health: 'healthy' | 'degraded' | null;
  flo_version: string | null;
  ga_label?: string | null;
  min_k8s?: string | null;
  max_k8s?: string | null;
  helm_release: {
    name: string;
    namespace: string;
    version: string;
    status: string;
  } | null;
  tmm_pods: {
    pods: number;
    running: number;
    containers: {
      total_containers: number;
      ready_containers: number;
      containers: string;
    } | null;
  };
  vlans: Array<{
    name: string;
    interfaces: unknown[];
    self_ips: string[];
    mtu: number | null;
    programmed: boolean;
  }>;
}

// BNK Upgrade Versions Response
export interface BnkUpgradeVersionsResponse {
  current_version: string | null;
  available_versions: Array<BnkVersionInfo & { source?: 'registry' | null }>;
  registry_available: boolean;
  registry_error: string | null;
}

// BNK Upgrade History Response
export interface BnkUpgradeHistoryResponse {
  upgrades: BnkUpgrade[];
  total: number;
}

// BNK Upgrade Execution Response
export interface BnkUpgradeExecuteResponse {
  message: string;
  upgrade_id: number;
  celery_task_id: string;
  status: 'in_progress';
}

// BNK Upgrade Rollback Response
export interface BnkUpgradeRollbackResponse {
  message: string;
  upgrade_id: number;
  celery_task_id: string;
  status: 'rolling_back';
}

// ─── BNK Gateway Topology Types ──────────────────────────────────────

export interface TopologyRouteBackend {
  name: string;
  namespace?: string | null;
  port: number | null;
  weight: number | null;          // declared spec weight (backendRefs[].weight)
  // Analyzer-computed weights from the k8s.f5.com/service-settings annotation.
  // effectiveWeight is the number the UI should show; it's null when the
  // analyzer didn't weight this service, in which case fall back to `weight` (#8).
  effectiveWeight?: number | null;
  analyzerWeights?: Record<string, number> | null;   // {pod_ip: weight}
  kind?: string;   // "Service" (default), "ServiceImport", etc.
  group?: string;   // "" = core API group
}

export interface TopologyAnalyzer {
  name: string;
  schedule: string;
  scriptType: string;
  dataSources: string[];
  parameters: Record<string, string>;
}

export interface TopologyRoute {
  name: string;
  namespace: string;
  kind: string;   // "HTTPRoute", "TCPRoute", "UDPRoute", "TLSRoute", "GRPCRoute", "L4Route"
  hostnames: string[];
  backends: TopologyRouteBackend[];
  analyzers: TopologyAnalyzer[];
}

export interface TopologyNetworkPolicyExtension {
  kind: string;
  name: string;
  group: string;
  lineCount?: number;
  eventHandlers?: string[];
}

export interface TopologyNetworkPolicy {
  name: string;
  namespace: string;
  extensions: TopologyNetworkPolicyExtension[];
  resolvedCount: number;
  totalExtensions: number;
}

export interface TopologyFirewallPolicy {
  name: string;
  rules: Array<{
    name: string;
    action: string;
    ipProtocol: string;
    logging: boolean;
  }>;
  addressLists: Array<{
    name: string;
    addresses: unknown[];
  }>;
  portLists: Array<{
    name: string;
    ports: unknown[];
  }>;
}

export interface TopologySecurityPolicy {
  name: string;
  namespace: string;
  targetListener: string;
  firewallPolicies: TopologyFirewallPolicy[];
}

export interface TopologyListener {
  name: string;
  protocol: string;
  port: number | null;
  routes: TopologyRoute[];
  networkPolicies: TopologyNetworkPolicy[];
}

export interface TopologyGateway {
  name: string;
  namespace: string;
  gatewayClassName: string;
  addresses: string[];
  listeners: TopologyListener[];
  securityPolicies: TopologySecurityPolicy[];
}

export interface TopologyVlan {
  name: string;
  namespace: string;
  interfaces: unknown[];
  selfipV4s: string[];
  prefixLen: number | null;
  mtu: number | null;
  internal: boolean;
  autoLasthop: string;
  ready: boolean;
}

export interface TopologyCneInstance {
  name: string;
  namespace: string;
  features: Record<string, boolean>;
  networkAttachments: unknown[];
  containerPlatform: string;
  phase: string;
}

export interface TopologyStaticRoute {
  name: string;
  namespace: string;
  destination: string;
  gateway: string;
}

export interface TopologySnatPool {
  name: string;
  namespace: string;
  addresses: string[];
}

export interface TopologyEgress {
  name: string;
  namespace: string;
  snatType: string;
  egressSnatpool: string | null;
  firewallEnforcedPolicy: string | null;
  logProfile: string | null;
  capturedNamespaces: string[];
  vxlan: {
    tmmInterfaceName: string;
    nodeInterfaceName: string;
  } | null;
  ready: boolean;
}

export interface TopologyDataPlane {
  vlans: TopologyVlan[];
  cneInstances: TopologyCneInstance[];
  staticRoutes: TopologyStaticRoute[];
  snatPools: TopologySnatPool[];
  egresses: TopologyEgress[];
  logging: {
    hslPublishers: Array<{
      name: string;
      namespace: string;
      pool: Record<string, unknown>;
      protocol: string;
    }>;
    logProfiles: Array<{
      name: string;
      namespace: string;
      publishers: unknown[];
    }>;
  };
}

export interface TopologyReferenceGrant {
  name: string;
  namespace: string;
  from: Array<{ group: string; kind: string; namespace: string }>;
  to: Array<{ group: string; kind: string }>;
}

export interface TopologyCounts {
  gateways: number;
  listeners: number;
  httpRoutes: number;
  grpcRoutes: number;
  tcpRoutes: number;
  udpRoutes: number;
  tlsRoutes: number;
  l4Routes: number;
  totalRoutes: number;
  referenceGrants: number;
  securityPolicies: number;
  networkPolicies: number;
  firewallPolicies: number;
  iRules: number;
  analyzers: number;
  vlans: number;
  cneInstances: number;
  staticRoutes: number;
  snatPools: number;
  egresses: number;
  hslPublishers: number;
  logProfiles: number;
}

export interface GatewayTopologyResponse {
  topology: TopologyGateway[];
  dataPlane: TopologyDataPlane;
  referenceGrants: TopologyReferenceGrant[];
  counts: TopologyCounts;
  cluster_id: number;
  namespace: string | null;
}

// Backend entry — a K8s Service cross-referenced with route backendRefs
export interface BnkBackendRouteRef {
  kind: string;            // "HTTPRoute", "TCPRoute", etc.
  name: string;
  namespace: string;
  gatewayName: string;
  listenerName: string;
  port?: number | null;
  weight?: number | null;             // declared spec weight
  // Carried through from the topology backend so the backends view prefers the
  // analyzer's number over the declared weight, same as the topology tree (#8).
  effectiveWeight?: number | null;
  analyzerWeights?: Record<string, number> | null;
}

export interface BnkBackendEntry {
  name: string;
  namespace: string;
  type: string;            // "ClusterIP" | "NodePort" | "LoadBalancer" | "ExternalName"
  clusterIP?: string | null;
  ports: Array<{
    port: number | null;
    targetPort: number | string | null;
    protocol: string;
    name?: string | null;
  }>;
  mapped: boolean;
  routeRefs: BnkBackendRouteRef[];
  selector?: Record<string, string> | null;
  createdAt?: string | null;
}

// BNK unified data response (getBnkData)
export interface BnkDataResponse {
  health: BnkHealthResponse;
  topology: TopologyGateway[];
  dataPlane: TopologyDataPlane;
  referenceGrants: TopologyReferenceGrant[];
  topologyCounts: TopologyCounts;
  policyAssociations: F5PolicyGatewayAssociation[];
  policyCount: number;
  backends?: BnkBackendEntry[];
  palette?: BnkPaletteData;
  cluster_id: number;
  namespace: string | null;
}

// BNK Health endpoint response (adds cluster_id to health)
export interface BnkHealthEndpointResponse extends BnkHealthResponse {
  cluster_id: number;
}

// ─── Policy Builder Types ────────────────────────────────────────────

/** A firewall rule being composed in the builder */
export interface PolicyBuilderRule {
  id: string;                     // client-side UUID for React keys
  name: string;
  action: 'accept' | 'drop' | 'reject';
  ipProtocol: 'tcp' | 'udp' | 'any';
  source: {
    addresses: string[];          // inline CIDRs
    addressLists: string[];       // refs to F5BigCneAddresslist names
    ports: string[];              // inline ports (strings per CRD spec)
    portLists: string[];          // refs to F5BigCnePortlist names
  };
  destination: {
    addresses: string[];
    addressLists: string[];
    ports: string[];
    portLists: string[];
  };
  logging: boolean;
}

// ─── Configuration Builder (Wizard) Types ────────────────────────────

export type ConfigBuilderStep = 'gateway' | 'routes' | 'security' | 'review';

export interface ConfigBuilderRoute {
  id: string;                     // client-side UUID
  kind: 'HTTPRoute' | 'TCPRoute' | 'UDPRoute' | 'TLSRoute' | 'GRPCRoute';
  name: string;
  listenerName: string;           // parentRef sectionName
  hostnames: string[];            // HTTPRoute/TLSRoute/GRPCRoute only
  // For HTTPRoute matches
  matches: Array<{
    pathType: 'PathPrefix' | 'Exact';
    pathValue: string;
  }>;
  // Backend refs
  backends: Array<{
    serviceName: string;
    servicePort: number;
    weight?: number;
  }>;
}

export interface ConfigBuilderState {
  // Step 1: Gateway
  gatewayName: string;
  gatewayClassName: string;
  namespace: string;
  listeners: Array<{
    name: string;
    protocol: 'HTTP' | 'HTTPS' | 'TCP' | 'UDP' | 'TLS';
    port: number;
    hostname?: string;
    tls_certificate_ref?: string;
  }>;
  // Step 2: Routes
  routes: ConfigBuilderRoute[];
  // Step 3: Security (optional)
  securityEnabled: boolean;
  securityPolicyName: string;
  firewallPolicyName: string;
  firewallRules: PolicyBuilderRule[];
  targetListener: string;         // which listener to attach security policy to
  // Step 3: Network policy (optional)
  networkEnabled: boolean;
  networkPolicyName: string;
  networkExtensionRefs: Array<{
    kind: string;
    name: string;
    group: string;
  }>;
  networkTargetListener: string;
}

/** The complete state of a policy being built */
export interface PolicyBuilderState {
  mode: 'create' | 'edit';
  policyType: 'security' | 'network';
  name: string;
  namespace: string;
  // Target gateway/listener
  targetGateway: string;
  targetListener: string;         // sectionName, empty = all listeners
  // For security policies (BNKSecPolicy → F5BigFwPolicy)
  firewallPolicyName: string;
  firewallRules: PolicyBuilderRule[];
  // For network policies (BNKNetPolicy → extensionRefs)
  extensionRefs: Array<{
    kind: string;                 // 'F5BigCneIrule', etc.
    name: string;
    group: string;                // 'k8s.f5net.com'
  }>;
}

/** Palette items — summaries of existing cluster resources */
export interface PaletteGatewayClass {
  name: string;
  controllerName: string;
}

export interface PaletteService {
  name: string;
  namespace: string;
  ports: Array<{ port: number | null; name?: string | null; protocol: string }>;
}

export interface PaletteAddressList {
  name: string;
  namespace: string;
  addresses: string[];
}

export interface PalettePortList {
  name: string;
  namespace: string;
  ports: string[];
}

export interface PaletteIRule {
  name: string;
  namespace: string;
  lineCount: number;
  eventHandlers: string[];
}

/** Palette data included in the unified BNK response */
export interface BnkPaletteData {
  gatewayClasses: PaletteGatewayClass[];
  services: PaletteService[];
  addressLists: PaletteAddressList[];
  portLists: PalettePortList[];
  iRules: PaletteIRule[];
}

// ─── A2A Protocol Types ──────────────────────────────────────────────

/** A2A agent card — per https://a2a-protocol.org/latest/specification/ */
export interface A2AAgentCard {
  name: string;
  description: string;
  version: string;
  url: string;
  capabilities: Record<string, boolean>;
  skills: Array<{ id?: string; name: string; description?: string }>;
  defaultInputModes: string[];
  defaultOutputModes: string[];
  provider: Record<string, string>;
  securitySchemes: Record<string, unknown>;
  iconUrl?: string | null;
}

/** A2A agent candidate — service behind an HTTPRoute */
export interface A2AAgentCandidate {
  name: string;
  namespace: string;
  ports: Array<{ port: number | null; name?: string | null; protocol: string }>;
  clusterIP?: string | null;
  routeRefs: BnkBackendRouteRef[];
  gateways: string[];
  agentCard: A2AAgentCard | null;
  probeStatus: 'pending' | 'success' | 'error' | 'skipped';
}

/** Response from GET /api/k8s/clusters/{id}/f5bnk/a2a/agents */
export interface A2AAgentsResponse {
  agents: A2AAgentCandidate[];
  count: number;
  probed: boolean;
  cluster_id: number;
  namespace: string | null;
}
