/**
 * Benchmark types for LLM Inference Load Testing Dashboard (Phase 2)
 *
 * ⚠️  TERMINOLOGY:
 *   BenchmarkRun   = one load test execution through one proxy
 *   BenchmarkConfig = a saved RunConfig (base_url, model, proxy, phases)
 *   BenchmarkAgent  = a test client MACHINE (registered via curl)
 */
import type { components } from './api-generated';

// =============================================================================
// Enums
// =============================================================================

/** Proxy types for comparison */
export type ProxyType =
  | 'envoy'
  | 'nginx'
  | 'haproxy'
  | 'f5-bnk'
  | 'nodeport'
  | 'envoy-ai-gateway'
  | 'llm-d-router';

/** Benchmark tool that produced results */
export type BenchmarkTool = 'aiperf' | 'llm-bench';

export type BenchmarkRunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
export type BenchmarkAgentStatus = 'connected' | 'disconnected' | 'running';
export type BenchmarkTargetStatus = 'active' | 'inactive' | 'validating' | 'error';
export type ProxyDeploymentStatus = 'discovered' | 'pending' | 'deploying' | 'ready' | 'failed' | 'uninstalling' | 'uninstalled';

// =============================================================================
// Config — saved RunConfig presets
// =============================================================================

export interface BenchmarkConfig {
  id: number;
  name: string;
  description: string | null;
  tool: string;
  config_json: Record<string, unknown>;  // Full RunConfig
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface BenchmarkConfigCreate {
  name: string;
  description?: string;
  tool?: string;
  config_json: Record<string, unknown>;
}

export interface BenchmarkConfigUpdate {
  name?: string;
  description?: string;
  tool?: string;
  config_json?: Record<string, unknown>;
}

// =============================================================================
// Run — load test result
// =============================================================================

export interface BenchmarkRun {
  id: number;
  tool: string;
  proxy: string;
  model: string;
  base_url: string;
  run_label: string | null;
  tags: Record<string, string> | null;
  config_id: number | null;
  agent_id: number | null;
  status: BenchmarkRunStatus;
  error_message: string | null;

  // Denormalized metrics
  duration_seconds: number | null;
  total_requests: number | null;
  successful_requests: number | null;
  failed_requests: number | null;
  success_rate_pct: number | null;
  latency_p50: number | null;  // seconds
  latency_p99: number | null;  // seconds
  overall_rps: number | null;
  peak_rps: number | null;
  tokens_per_sec: number | null;
  total_output_tokens: number | null;

  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface BenchmarkRunDetail extends BenchmarkRun {
  result_json: Record<string, unknown> | null;  // Full BenchmarkResult
  config_snapshot: Record<string, unknown> | null;  // Full RunConfig
  config: BenchmarkConfig | null;
  agent: BenchmarkAgent | null;
}

export interface BenchmarkRunCreate {
  config_id?: number;
  agent_id?: number;
  tool?: string;
  proxy?: string;
  model: string;
  base_url: string;
  run_label?: string;
  tags?: Record<string, string>;
  config_snapshot?: Record<string, unknown>;
}

export interface BenchmarkRunListResponse {
  runs: BenchmarkRun[];
  total: number;
  limit: number;
  offset: number;
}

// =============================================================================
// Agent — test client machine
// =============================================================================

export interface BenchmarkAgent {
  id: number;
  name: string;
  hostname: string | null;
  ip_address: string | null;
  tags: Record<string, unknown> | null;
  capabilities: Record<string, unknown> | null;
  status: BenchmarkAgentStatus;
  last_heartbeat: string | null;
  created_at: string;
  updated_at: string;
}

// =============================================================================
// Comparison — proxy vs proxy
// =============================================================================

export interface BenchmarkCompareRequest {
  run_ids: number[];
}

export interface BenchmarkCompareRunMetrics {
  run_id: number;
  proxy: string;
  model: string;
  tool: string;
  run_label: string | null;
  status: BenchmarkRunStatus;
  total_requests: number | null;
  success_rate_pct: number | null;
  latency_p50: number | null;
  latency_p99: number | null;
  overall_rps: number | null;
  peak_rps: number | null;
  tokens_per_sec: number | null;
  duration_seconds: number | null;
  // aiperf-specific metrics (avg values for comparison)
  ttft_avg: number | null;
  itl_avg: number | null;
  tst_avg: number | null;
  osl_avg: number | null;
  isl_avg: number | null;
  per_user_throughput_avg: number | null;
}

export interface BenchmarkCompareResponse {
  runs: BenchmarkCompareRunMetrics[];
  winners: Record<string, number>;  // metric → winning run_id
}

// =============================================================================
// Summary — dashboard overview
// =============================================================================

export interface BenchmarkSummary {
  total_runs: number;
  completed_runs: number;
  failed_runs: number;
  running_count: number;
  avg_latency_p50: number | null;
  avg_rps: number | null;
  avg_success_rate: number | null;
  last_run_at: string | null;
  runs_last_7d: number;
  runs_by_proxy: BenchmarkProxyStat[];
  runs_by_tool: BenchmarkToolStat[];
}

export interface BenchmarkProxyStat {
  proxy: string;
  count: number;
  avg_p50: number | null;
  avg_rps: number | null;
}

export interface BenchmarkToolStat {
  tool: string;
  count: number;
}

// =============================================================================
// WebSocket
// =============================================================================

export interface BenchmarkWSMessage {
  type: 'progress' | 'run_completed' | 'run_failed' | 'run_started';
  run_id?: number | string;
  event?: Record<string, unknown>;
  status?: string;
  summary?: Record<string, unknown>;
  error?: string;
  timestamp?: string;
}

// =============================================================================
// Result JSON sub-types (extracted from result_json for display)
// =============================================================================

export interface LatencyStats {
  min?: number;
  p25?: number;
  p50?: number;
  p75?: number;
  p90?: number;
  p95?: number;
  p99?: number;
  max?: number;
  avg?: number;
  mean?: number;  // alias for avg — some formats use one or the other
}

export interface ThroughputStats {
  overall_rps?: number;
  p50_rps?: number;
  p90_rps?: number;
  p99_rps?: number;
  peak_rps?: number;
  min_rps?: number;
  gen_tokens_per_sec?: number;
}

export interface PhaseResult {
  name?: string;
  requests: number;
  success?: number;       // alias: "successful" in some formats
  successful?: number;
  failed: number;
  duration_seconds?: number | null;
  input_tokens?: number;
  output_tokens?: number;
  latency?: LatencyStats;
  rps?: number | null;
}

export interface TimelineEvent {
  t: number;
  phase?: string;
  event?: string;
  latency?: number;
  input_tokens?: number;
  output_tokens?: number;
}

// =============================================================================
// Benchmark Target — K8s cluster + LLM endpoint (Phase 4b)
// =============================================================================

export interface BenchmarkTarget {
  id: number;
  name: string;
  description: string | null;
  cluster_id: number;
  llm_base_url: string;
  llm_model: string;
  llm_namespace: string;
  llm_endpoint: string;
  proxy_namespace: string;
  status: BenchmarkTargetStatus;
  last_validated: string | null;
  validation_msg: string | null;
  tags: Record<string, unknown> | null;
  proxy_count: number;
  created_at: string;
  updated_at: string;
}

export interface BenchmarkTargetDetail extends BenchmarkTarget {
  proxy_deployments: ProxyDeployment[];
}

export interface BenchmarkTargetCreate {
  name: string;
  description?: string;
  cluster_id: number;
  llm_base_url: string;
  llm_model: string;
  llm_namespace?: string;
  llm_endpoint?: string;
  proxy_namespace?: string;
  tags?: Record<string, unknown>;
}

export interface BenchmarkTargetUpdate {
  name?: string;
  description?: string;
  cluster_id?: number;
  llm_base_url?: string;
  llm_model?: string;
  llm_namespace?: string;
  llm_endpoint?: string;
  proxy_namespace?: string;
  tags?: Record<string, unknown>;
}

export interface BenchmarkTargetListResponse {
  targets: BenchmarkTarget[];
  total: number;
}

// =============================================================================
// Proxy Deployment — proxy on a target cluster (Phase 4b)
// =============================================================================

export interface ProxyDeployment {
  id: number;
  target_id: number;
  proxy_type: string;
  helm_release: string | null;
  helm_chart: string | null;
  helm_version: string | null;
  helm_values: Record<string, unknown> | null;
  proxy_url: string | null;
  external_url: string | null;
  status: ProxyDeploymentStatus;
  status_message: string | null;
  celery_task_id: string | null;
  deployed_at: string | null;
  created_at: string;
  updated_at: string;
}

/** Response from GET .../proxies/:id/task-status (Phase 4c polling) */
export interface ProxyTaskStatus {
  proxy_id: number;
  status: ProxyDeploymentStatus;
  status_message: string | null;
  proxy_url: string | null;
  celery_task_id: string | null;
  celery_state: string | null;
}

export interface ProxyDeployRequest {
  proxy_type: string;
  helm_values?: Record<string, unknown>;
  helm_chart?: string;
  helm_version?: string;
}

export interface ProxyDeploymentUpdate {
  helm_values?: Record<string, unknown>;
  proxy_url?: string;
  external_url?: string;
  status?: string;
  status_message?: string;
}

// =============================================================================
// Run Orchestration (Phase 4d) — trigger benchmark against a deployed proxy
// =============================================================================

export interface TriggerRunRequest {
  config_id?: number;
  agent_id?: number;
  run_label?: string;
  tags?: Record<string, string>;
  total_requests?: number;
  max_tokens?: number;
  timeout?: number;
}

export interface TriggerRunResponse {
  run_id: number;
  agent_id: number;
  proxy_id: number;
  target_id: number;
  status: string;
  message: string;
}

// =============================================================================
// Proxy Discovery (Phase 5) — scan cluster for existing proxies
// =============================================================================

export interface ProxyDiscoveryResultItem {
  proxy_type: string;
  found: boolean;
  proxy_url: string | null;
  external_url: string | null;
  namespace: string | null;
  details: Record<string, unknown>;
  error: string | null;
}

export interface ProxyDiscoveryResponse {
  target_id: number;
  target_name: string;
  cluster_id: number;
  results: ProxyDiscoveryResultItem[];
  discovered_count: number;
  total_scanned: number;
}

// =============================================================================
// Target Discovery (Phase 5b) — auto-discover LLM services on cluster
// =============================================================================

export interface DiscoverTargetsRequest {
  cluster_id: number;
  auto_create?: boolean;
  selected_services?: string[];
}

export interface DiscoveredLLMServiceItem {
  service_name: string;
  namespace: string;
  base_url: string;
  port: number;
  confidence: string;
  reason: string;
  model_hint: string | null;
  gpu_count: number;
  image: string | null;
}

export interface CreatedTargetItem {
  id: number;
  name: string;
  llm_base_url: string;
  status: string;
}

export interface TargetProxyDiscoveryResult {
  target_id: number;
  target_name: string;
  discovered_proxies: number;
  total_scanned: number;
  error: string | null;
}

export interface CreatedConfigItem {
  name: string;
  target_name: string;
  proxy_type: string;
}

export interface DiscoverTargetsResponse {
  cluster_id: number;
  cluster_name: string;
  discovered_services: DiscoveredLLMServiceItem[];
  discovered_count: number;
  created_targets: CreatedTargetItem[];
  proxy_results: TargetProxyDiscoveryResult[];
  created_configs: CreatedConfigItem[];
}

// =============================================================================
// Scenarios + Run-Groups — aliases over generated OpenAPI schema types.
// Source of truth is backend Pydantic → openapi.json → api-generated.ts.
// Do NOT hand-duplicate these shapes; re-alias the generated ones.
// =============================================================================

export type ScenarioCatalogItem = components['schemas']['ScenarioCatalogItem'];
export type ScenarioCatalogResponse = components['schemas']['ScenarioCatalogResponse'];
export type ScenarioRunRequest = components['schemas']['ScenarioRunRequest'];
export type ScenarioRunResponse = components['schemas']['ScenarioRunResponse'];
export type RunGroupChildSummary = components['schemas']['RunGroupChildSummary'];
export type RunGroupResponse = components['schemas']['RunGroupResponse'];

// =============================================================================
// Agent Host — Forge-managed remote benchmark host (Slice 1)
// =============================================================================

export type AgentHostProvisionStatus = 'unprovisioned' | 'provisioning' | 'provisioned' | 'failed' | 'scanning';

/** A Forge-managed remote server registered as a benchmark agent host. */
export interface BenchmarkAgentHost {
  id: number;
  name: string;
  hostname: string | null;
  ip_address: string | null;
  tags: Record<string, unknown> | null;
  capabilities: Record<string, unknown> | null;
  status: BenchmarkAgentStatus;
  last_heartbeat: string | null;

  // SSH-host registration fields
  project_id: number | null;
  host_ip: string | null;
  ssh_credential_id: number | null;
  ssh_port: number | null;
  jumphost_chain: Array<{ ssh_credential_id: number }> | null;
  provision_status: AgentHostProvisionStatus | null;
  provision_message: string | null;
  readiness: Record<string, unknown> | null;
  managed: boolean;

  created_at: string;
  updated_at: string;
}

export interface BenchmarkAgentHostCreate {
  name: string;
  project_id: number;
  host_ip: string;
  ssh_credential_id: number;
  ssh_port?: number;
  jumphost_chain?: Array<{ ssh_credential_id: number }>;
}

// Slice 2 — SSH scan

export interface AgentHostScanRequest {
  /** Optional: BenchmarkTarget IDs to probe reachability for. Omit = all active project targets. */
  target_ids?: number[];
}

export interface AgentHostScanResponse {
  host_id: number;
  message: string;
  celery_task_id: string;
}

// Slice 3 — SSH provision

export interface AgentHostProvisionResponse {
  host_id: number;
  message: string;
  celery_task_id: string;
}

export type AgentHostScanVerdict =
  | 'ready'
  | 'needs_provision'
  | 'unreachable_to_targets'
  | 'ssh_unreachable';

export interface AgentHostReadiness {
  ssh_reachable: boolean;
  os: {
    os_type?: string;
    os_version?: string;
    os_pretty_name?: string;
    architecture?: string;
  };
  cpu: number | null;
  mem_gb: number | null;
  tools: {
    python3: boolean;
    pip: boolean;
    aiperf: boolean;
    systemctl: boolean;
    python3_version?: string;
  };
  reachable_targets: Array<{
    target_id: number;
    name: string;
    llm_base_url: string;
    ok: boolean;
    http_code: number | null;
    error: string | null;
  }>;
  verdict: AgentHostScanVerdict;
  error?: string;
}

// =============================================================================
// Agent Host Candidates (Slice 5) — project host/jumphost picker
// =============================================================================

export type AgentHostCandidateSource =
  | 'bare_metal'
  | 'cluster_bastion'
  | 'ssh_credential'
  | 'aws_jumphost';

export interface AgentHostCandidate {
  label: string;
  host_ip: string;
  ssh_credential_id: number | null;
  ssh_port: number;
  jumphost_chain: Array<{ ssh_credential_id: number }> | null;
  source: AgentHostCandidateSource;
  source_ref: string | null;
  last_test_status: string | null;
  needs_credential_import: boolean;
  infra_key_path: string | null;
  module_id: number | null;
}

export interface AgentHostCandidatesResponse {
  candidates: AgentHostCandidate[];
  project_id: number;
}

export interface ImportAwsJumphostRequest {
  project_id: number;
  module_id: number;
}

export interface ImportAwsJumphostResponse {
  ssh_credential_id: number;
}
