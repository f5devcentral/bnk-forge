/**
 * Benchmark API methods (Phase 2 + Phase 4b: LLM Inference Load Testing Dashboard)
 */
import { apiClient } from './client';
import type {
  AgentHostCandidatesResponse,
  AgentHostProvisionResponse,
  AgentHostScanRequest,
  AgentHostScanResponse,
  BenchmarkAgentHost,
  BenchmarkAgentHostCreate,
  BenchmarkConfig,
  BenchmarkConfigCreate,
  BenchmarkConfigUpdate,
  BenchmarkRun,
  BenchmarkRunCreate,
  BenchmarkRunDetail,
  BenchmarkRunListResponse,
  BenchmarkAgent,
  ImportAwsJumphostRequest,
  ImportAwsJumphostResponse,
  BenchmarkCompareRequest,
  BenchmarkCompareResponse,
  BenchmarkSummary,
  BenchmarkTarget,
  BenchmarkTargetCreate,
  BenchmarkTargetDetail,
  BenchmarkTargetListResponse,
  BenchmarkTargetUpdate,
  DiscoverTargetsRequest,
  DiscoverTargetsResponse,
  ProxyDeployment,
  ProxyDeployRequest,
  ProxyDeploymentUpdate,
  ProxyDiscoveryResponse,
  ProxyTaskStatus,
  RunGroupResponse,
  ScenarioCatalogResponse,
  ScenarioRunRequest,
  ScenarioRunResponse,
  TriggerRunRequest,
  TriggerRunResponse,
} from '@/types';

export const benchmarksApi = {
  // ── Configs (saved RunConfig presets) ─────────────────────────────────

  listConfigs: (params?: { tool?: string }) =>
    apiClient.get<BenchmarkConfig[]>('/api/benchmarks/configs', { params }).then((res) => res.data),

  getConfig: (configId: number) =>
    apiClient.get<BenchmarkConfig>(`/api/benchmarks/configs/${configId}`).then((res) => res.data),

  createConfig: (data: BenchmarkConfigCreate) =>
    apiClient.post<BenchmarkConfig>('/api/benchmarks/configs', data).then((res) => res.data),

  updateConfig: (configId: number, data: BenchmarkConfigUpdate) =>
    apiClient.put<BenchmarkConfig>(`/api/benchmarks/configs/${configId}`, data).then((res) => res.data),

  deleteConfig: (configId: number) =>
    apiClient.delete(`/api/benchmarks/configs/${configId}`).then(() => undefined),

  // ── Runs (load test results) ─────────────────────────────────────────

  listRuns: (params?: { proxy?: string; tool?: string; model?: string; status?: string; limit?: number; offset?: number }) =>
    apiClient.get<BenchmarkRunListResponse>('/api/benchmarks/runs', { params }).then((res) => res.data),

  getRun: (runId: number) =>
    apiClient.get<BenchmarkRunDetail>(`/api/benchmarks/runs/${runId}`).then((res) => res.data),

  createRun: (data: BenchmarkRunCreate) =>
    apiClient.post<BenchmarkRun>('/api/benchmarks/runs', data).then((res) => res.data),

  cancelRun: (runId: number) =>
    apiClient.post<BenchmarkRun>(`/api/benchmarks/runs/${runId}/cancel`).then((res) => res.data),

  deleteRun: (runId: number) =>
    apiClient.delete(`/api/benchmarks/runs/${runId}`).then(() => undefined),

  // ── Agents (test client machines) ────────────────────────────────────

  listAgents: () =>
    apiClient.get<BenchmarkAgent[]>('/api/benchmarks/agents').then((res) => res.data),

  getAgent: (agentId: number) =>
    apiClient.get<BenchmarkAgent>(`/api/benchmarks/agents/${agentId}`).then((res) => res.data),

  deleteAgent: (agentId: number) =>
    apiClient.delete(`/api/benchmarks/agents/${agentId}`).then(() => undefined),

  // ── Comparison & Summary ─────────────────────────────────────────────

  compareRuns: (data: BenchmarkCompareRequest) =>
    apiClient.post<BenchmarkCompareResponse>('/api/benchmarks/compare', data).then((res) => res.data),

  getSummary: () =>
    apiClient.get<BenchmarkSummary>('/api/benchmarks/summary').then((res) => res.data),

  // ── Targets (K8s cluster + LLM endpoint) — Phase 4b ──────────────────

  listTargets: (params?: { status?: string; cluster_id?: number }) =>
    apiClient.get<BenchmarkTargetListResponse>('/api/benchmarks/targets', { params }).then((res) => res.data),

  getTarget: (targetId: number) =>
    apiClient.get<BenchmarkTargetDetail>(`/api/benchmarks/targets/${targetId}`).then((res) => res.data),

  createTarget: (data: BenchmarkTargetCreate) =>
    apiClient.post<BenchmarkTarget>('/api/benchmarks/targets', data).then((res) => res.data),

  updateTarget: (targetId: number, data: BenchmarkTargetUpdate) =>
    apiClient.put<BenchmarkTarget>(`/api/benchmarks/targets/${targetId}`, data).then((res) => res.data),

  deleteTarget: (targetId: number) =>
    apiClient.delete(`/api/benchmarks/targets/${targetId}`).then(() => undefined),

  validateTarget: (targetId: number) =>
    apiClient.post<BenchmarkTarget>(`/api/benchmarks/targets/${targetId}/validate`).then((res) => res.data),

  // ── Proxy Deployments — Phase 4b ─────────────────────────────────────

  listProxyDeployments: (targetId: number) =>
    apiClient.get<ProxyDeployment[]>(`/api/benchmarks/targets/${targetId}/proxies`).then((res) => res.data),

  getProxyDeployment: (targetId: number, proxyId: number) =>
    apiClient.get<ProxyDeployment>(`/api/benchmarks/targets/${targetId}/proxies/${proxyId}`).then((res) => res.data),

  deployProxy: (targetId: number, data: ProxyDeployRequest) =>
    apiClient.post<ProxyDeployment>(`/api/benchmarks/targets/${targetId}/proxies`, data).then((res) => res.data),

  updateProxyDeployment: (targetId: number, proxyId: number, data: ProxyDeploymentUpdate) =>
    apiClient.put<ProxyDeployment>(`/api/benchmarks/targets/${targetId}/proxies/${proxyId}`, data).then((res) => res.data),

  deleteProxyDeployment: (targetId: number, proxyId: number) =>
    apiClient.delete(`/api/benchmarks/targets/${targetId}/proxies/${proxyId}`).then(() => undefined),

  redeployProxy: (targetId: number, proxyId: number) =>
    apiClient.post<ProxyDeployment>(`/api/benchmarks/targets/${targetId}/proxies/${proxyId}/redeploy`).then((res) => res.data),

  /** Phase 4c: Poll Celery task status for a proxy deploy/undeploy operation. */
  getProxyTaskStatus: (targetId: number, proxyId: number) =>
    apiClient.get<ProxyTaskStatus>(`/api/benchmarks/targets/${targetId}/proxies/${proxyId}/task-status`).then((res) => res.data),

  /** Phase 4d: Trigger a benchmark run against a deployed proxy. */
  triggerRun: (targetId: number, proxyId: number, data: TriggerRunRequest) =>
    apiClient.post<TriggerRunResponse>(`/api/benchmarks/targets/${targetId}/proxies/${proxyId}/run`, data).then((res) => res.data),

  /** Phase 5: Discover existing proxies on the target's cluster. */
  discoverProxies: (targetId: number) =>
    apiClient.post<ProxyDiscoveryResponse>(`/api/benchmarks/targets/${targetId}/discover-proxies`).then((res) => res.data),

  /** Phase 5b: Scan cluster for LLM services and auto-create targets + discover proxies. */
  discoverTargets: (data: DiscoverTargetsRequest) =>
    apiClient.post<DiscoverTargetsResponse>('/api/benchmarks/discover-targets', data).then((res) => res.data),

  // ── Scenarios + Run-Groups ───────────────────────────────────────────

  /** List benchmark scenario presets and how many child runs each expands into. */
  getScenarios: () =>
    apiClient.get<ScenarioCatalogResponse>('/api/benchmarks/scenarios').then((res) => res.data),

  /** Run a SCENARIO against a deployed proxy; expands into a parent run-group + N child runs. */
  runScenario: (targetId: number, proxyId: number, data: ScenarioRunRequest) =>
    apiClient
      .post<ScenarioRunResponse>(`/api/benchmarks/targets/${targetId}/proxies/${proxyId}/run-scenario`, data)
      .then((res) => res.data),

  /** Fetch a run-group: parent aggregate metrics + child run summaries. */
  getRunGroup: (groupId: number) =>
    apiClient.get<RunGroupResponse>(`/api/benchmarks/run-groups/${groupId}`).then((res) => res.data),

  // ── Agent Hosts (Slice 1) — project-scoped managed remote hosts ──────────

  listAgentHosts: (params?: { project_id?: number }) =>
    apiClient.get<BenchmarkAgentHost[]>('/api/benchmarks/agent-hosts', { params }).then((res) => res.data),

  getAgentHost: (hostId: number) =>
    apiClient.get<BenchmarkAgentHost>(`/api/benchmarks/agent-hosts/${hostId}`).then((res) => res.data),

  createAgentHost: (data: BenchmarkAgentHostCreate) =>
    apiClient.post<BenchmarkAgentHost>('/api/benchmarks/agent-hosts', data).then((res) => res.data),

  deleteAgentHost: (hostId: number) =>
    apiClient.delete(`/api/benchmarks/agent-hosts/${hostId}`).then(() => undefined),

  /** Slice 2: Dispatch an SSH suitability scan for a managed agent host. */
  scanAgentHost: (hostId: number, data?: AgentHostScanRequest) =>
    apiClient
      .post<AgentHostScanResponse>(`/api/benchmarks/agent-hosts/${hostId}/scan`, data ?? {})
      .then((res) => res.data),

  /** Slice 3: Dispatch SSH provisioning (install aiperf + forge_agent + systemd) for a managed agent host. */
  provisionAgentHost: (hostId: number) =>
    apiClient
      .post<AgentHostProvisionResponse>(`/api/benchmarks/agent-hosts/${hostId}/provision`)
      .then((res) => res.data),

  // ── Agent Host Candidates (Slice 5) ──────────────────────────────────

  /** Slice 5: Aggregate bare-metal / cluster bastion / SSH cred / AWS jumphost candidates for a project. */
  listAgentHostCandidates: (projectId: number) =>
    apiClient
      .get<AgentHostCandidatesResponse>('/api/benchmarks/agent-host-candidates', { params: { project_id: projectId } })
      .then((res) => res.data),

  /** Slice 5: Import an AWS deployment jumphost as an SSHCredential. */
  importAwsJumphost: (data: ImportAwsJumphostRequest) =>
    apiClient
      .post<ImportAwsJumphostResponse>('/api/benchmarks/agent-host-candidates/import-aws-jumphost', data)
      .then((res) => res.data),
};
