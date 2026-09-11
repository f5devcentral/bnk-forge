/**
 * Kubernetes & F5 BNK API methods
 */
import { apiClient } from './client';
import type {
  K8sCluster,
  K8sClusterCreateRequest,
  K8sClusterUpdateRequest,
  K8sResourceListResponse,
  K8sNamespaceListResponse,
  K8sResourceTypesResponse,
  K8sClusterTestResult,
  K8sResource,
  K8sResourceStatus,
  K8sEvent,
  K8sPodContainer,
  K8sPodMetric,
  K8sNodeMetric,
  K8sRolloutHistoryEntry,
  K8sRolloutStatus,
  K8sCondition,
  K8sTunnelStatusResponse,
  K8sListAllTunnelsResponse,
  K8sOpenTunnelResponse,
  K8sCloseTunnelResponse,
  K8sCloseAllTunnelsResponse,
  ClusterScanResponse,
  AdaptiveModulePlanResponse,
  ClusterConnectivityResult,
  BatchConnectivityResponse,
  F5PolicyGatewayAssociationsResponse,
  BnkDataResponse,
  GatewayTopologyResponse,
  BnkHealthEndpointResponse,
  BnkUpgradeVersionsResponse,
  BnkCurrentVersion,
  BnkReleaseRegistryResponse,
  BnkUpgrade,
  BnkUpgradeExecuteResponse,
  BnkUpgradeRollbackResponse,
  BnkUpgradeHistoryResponse,
  ConfigExport,
  ConfigImportResult,
  ConfigDiffResult,
  JsonValue,
  DpfDetectResponse,
  DpfDataResponse,
  DpfHealthEndpointResponse,
  A2AAgentsResponse,
  HugePagesDeployRequest,
  HugePagesDeployResponse,
  NodeReadinessProbeRequest,
  NodeReadinessProbeResponse,
  BnkClusterConfigSummary,
  BnkClusterConfigCreateRequest,
  BnkClusterMemberAssignRequest,
  BnkClusterMemberAssignResponse,
} from '@/types';
import type {
  ApiClusterCreateRequest,
  ApiClusterUpdateRequest,
  AssertKeysMatch,
} from '@/types/api-schemas';
import type { components } from '@/types/api-generated';

type CrdListEnvelope = components['schemas']['CrdListEnvelope'];
type TopologyGraphResponse = components['schemas']['TopologyGraphResponse'];
type ProxyTranslateRequest = components['schemas']['ProxyTranslateRequest'];
type ProxyTranslateResponse = components['schemas']['ProxyTranslateResponse'];
type CisTranslateRequest = components['schemas']['CisTranslateRequest'];

// ── Compile-time contract checks ────────────────────────────────────────────
const _checkClusterCreate: AssertKeysMatch<K8sClusterCreateRequest, ApiClusterCreateRequest> = true;
const _checkClusterUpdate: AssertKeysMatch<K8sClusterUpdateRequest, ApiClusterUpdateRequest> = true;
// eslint-disable-next-line @typescript-eslint/no-unused-expressions
void _checkClusterCreate, _checkClusterUpdate;

export const kubernetesApi = {
  // Kubernetes Monitoring
  getAllClusters: () =>
    apiClient.get<{ clusters: K8sCluster[]; count: number }>('/api/k8s/clusters').then((res) => res.data),

  getProjectClusters: (projectId: number) =>
    apiClient.get<{ clusters: K8sCluster[]; count: number }>(`/api/projects/${projectId}/k8s/clusters`).then((res) => res.data.clusters),

  getCluster: (clusterId: number) =>
    apiClient.get<K8sCluster>(`/api/k8s/clusters/${clusterId}`).then((res) => res.data),

  createCluster: (projectId: number, data: K8sClusterCreateRequest) =>
    apiClient.post<K8sCluster>(`/api/projects/${projectId}/k8s/clusters`, data).then((res) => res.data),

  updateCluster: (clusterId: number, data: K8sClusterUpdateRequest) =>
    apiClient.put<K8sCluster>(`/api/k8s/clusters/${clusterId}`, data).then((res) => res.data),

  deleteCluster: (clusterId: number) =>
    apiClient.delete<{ message: string }>(`/api/k8s/clusters/${clusterId}`).then((res) => res.data),

  testClusterConnection: (clusterId: number) =>
    apiClient.post<K8sClusterTestResult>(`/api/k8s/clusters/${clusterId}/test`).then((res) => res.data),

  detectEKSClusters: (projectId: number) =>
    apiClient.post<{
      success: boolean;
      message: string;
      registered: Array<{ id: number; name: string; module_id: number; status: string }>;
      skipped: Array<{ module_id: number; reason: string }>;
      errors: Array<{ module_id: number; error: string }>;
    }>(`/api/projects/${projectId}/k8s/clusters/detect-eks`).then((res) => res.data),

  detectManagedClusters: (projectId: number) =>
    apiClient.post<{
      success: boolean;
      message: string;
      registered: Array<{ id: number; name: string; module_id: number; status: string }>;
      skipped: Array<{ module_id: number; reason: string }>;
      errors: Array<{ module_id: number; error: string }>;
    }>(`/api/projects/${projectId}/k8s/clusters/detect-eks`).then((res) => res.data),

  detectClustersFromCredentials: (projectId: number) =>
    apiClient.post<{
      success: boolean;
      message: string;
      registered: Array<{ id: number; name: string; provider: string; status: string }>;
      skipped: Array<{ name: string; provider: string; reason: string }>;
      errors: Array<{ provider: string; name: string | null; error: string }>;
    }>(`/api/projects/${projectId}/k8s/clusters/detect-credentials`).then((res) => res.data),

  getClusterResources: (clusterId: number, resourceType: string, params?: { namespace?: string; label_selector?: string }) =>
    apiClient.get<K8sResourceListResponse>(`/api/k8s/clusters/${clusterId}/resources/${resourceType}`, { params }).then((res) => res.data),

  getClusterResourceSummary: (clusterId: number, namespace?: string) =>
    apiClient
      .get<{
        cluster_id: number;
        namespace: string | null;
        summary: Record<string, { count: number; unhealthy: number | null; available: boolean }>;
      }>(`/api/k8s/clusters/${clusterId}/resource-summary`, {
        params: namespace && namespace !== 'all' ? { namespace } : {},
      })
      .then((res) => res.data),

  getClusterNamespaces: (clusterId: number) =>
    apiClient.get<K8sNamespaceListResponse>(`/api/k8s/clusters/${clusterId}/namespaces`).then((res) => res.data),

  // CNF: CRD discovery (D-018 P1)
  // FastAPI's `group: list[str] = Query(...)` needs repeated `?group=a&group=b`, not
  // axios's default array serialization (`group[]=a&group[]=b`), so build the query
  // string manually — same pattern as api.getFleetMembers.
  getCrds: (clusterId: number, params?: { group?: string[] }) => {
    const searchParams = new URLSearchParams();
    if (params?.group) {
      params.group.forEach((g) => searchParams.append('group', g));
    }
    const qs = searchParams.toString();
    return apiClient
      .get<CrdListEnvelope>(`/api/k8s/clusters/${clusterId}/crds${qs ? `?${qs}` : ''}`)
      .then((res) => res.data);
  },

  // CNF: Namespace topology graph (D-018 P4)
  getTopology: (clusterId: number, namespace: string) =>
    apiClient
      .get<TopologyGraphResponse>(`/api/k8s/clusters/${clusterId}/topology`, { params: { namespace } })
      .then((res) => res.data),

  getClusterNodeCount: (clusterId: number) =>
    apiClient.get<{ cluster_id: number; node_count: number }>(`/api/k8s/clusters/${clusterId}/nodes/count`).then((res) => res.data.node_count),

  getK8sResourceTypes: () =>
    apiClient.get<K8sResourceTypesResponse>('/api/k8s/resource-types').then((res) => res.data.resource_types),

  // F5 BNK Monitoring — unified data endpoint (single fetch for all insight views)
   
  getBnkData: (clusterId: number, params?: { namespace?: string }) =>
    apiClient.get<BnkDataResponse>(`/api/k8s/clusters/${clusterId}/f5bnk/data`, { params }).then((res) => res.data),

  // Legacy individual endpoints (kept for backward compat, all delegate to shared fetch on backend)
  getF5PolicyGatewayAssociations: (clusterId: number, params?: { namespace?: string }) =>
    apiClient.get<F5PolicyGatewayAssociationsResponse>(`/api/k8s/clusters/${clusterId}/f5bnk/policy-gateway-associations`, { params }).then((res) => res.data),

  getF5GatewayTopology: (clusterId: number, params?: { namespace?: string }) =>
    apiClient.get<GatewayTopologyResponse>(`/api/k8s/clusters/${clusterId}/f5bnk/gateway-topology`, { params }).then((res) => res.data),

  getF5BNKHealth: (clusterId: number, params?: { namespace?: string }) =>
    apiClient.get<BnkHealthEndpointResponse>(`/api/k8s/clusters/${clusterId}/f5bnk/health`, { params }).then((res) => res.data),

  getA2AAgents: (clusterId: number, params?: { namespace?: string; probe?: boolean }) =>
    apiClient.get<A2AAgentsResponse>(`/api/k8s/clusters/${clusterId}/f5bnk/a2a/agents`, { params }).then((res) => res.data),

  // Cluster Scanner
  scanCluster: (clusterId: number, force = false) =>
    apiClient
      .post<ClusterScanResponse>(
        `/api/k8s/clusters/${clusterId}/scan${force ? '?force=true' : ''}`
      )
      .then((res) => res.data),

  // SSH Tunnel Management
  getTunnelStatus: (clusterId: number) =>
    apiClient.get<K8sTunnelStatusResponse>(`/api/k8s/clusters/${clusterId}/tunnel`).then((res) => res.data),

  listAllTunnels: () =>
    apiClient.get<K8sListAllTunnelsResponse>(`/api/k8s/tunnels`).then((res) => res.data),

  openTunnel: (clusterId: number) =>
    apiClient.post<K8sOpenTunnelResponse>(`/api/k8s/clusters/${clusterId}/tunnel/open`).then((res) => res.data),

  closeTunnel: (clusterId: number) =>
    apiClient.post<K8sCloseTunnelResponse>(`/api/k8s/clusters/${clusterId}/tunnel/close`).then((res) => res.data),

  closeAllTunnels: () =>
    apiClient.post<K8sCloseAllTunnelsResponse>(`/api/k8s/tunnels/close-all`).then((res) => res.data),

  // Cluster Connectivity Probes
  getClusterConnectivity: (clusterId: number) =>
    apiClient.get<ClusterConnectivityResult>(`/api/k8s/clusters/${clusterId}/connectivity`).then((res) => res.data),

  getBatchConnectivity: () =>
    apiClient.get<BatchConnectivityResponse>('/api/k8s/clusters/connectivity').then((res) => res.data),

  // Adaptive Module Selection
  getAdaptiveModulePlan: (clusterId: number, templateSlug?: string, modulePaths?: string[], sizingProfile?: string) =>
    apiClient.post<AdaptiveModulePlanResponse>(`/api/k8s/clusters/${clusterId}/adaptive-modules`, {
      template_slug: templateSlug || null,
      module_paths: modulePaths || null,
      sizing_profile: sizingProfile || null,
    }).then((res) => res.data),

  // Proxy Translation (D-021 P2)
  translateProxy: (clusterId: number, payload: ProxyTranslateRequest) =>
    apiClient
      .post<ProxyTranslateResponse>(
        `/api/k8s/clusters/${clusterId}/proxies/translate`,
        payload,
      )
      .then((res) => res.data),

  // CIS Translation (D-023 P3)
  translateCis: (clusterId: number, payload: CisTranslateRequest) =>
    apiClient
      .post<ProxyTranslateResponse>(
        `/api/k8s/clusters/${clusterId}/proxies/cis/translate`,
        payload,
      )
      .then((res) => res.data),

  // Recommendation Actions -- HugePages Deploy
  deployHugePages: (clusterId: number, payload: HugePagesDeployRequest) =>
    apiClient
      .post<HugePagesDeployResponse>(
        `/api/k8s/clusters/${clusterId}/recommendations/hugepages/deploy`,
        payload,
      )
      .then((res) => res.data),

  // Node Readiness Probe (issue #387 part A — detection only)
  probeNodeReadiness: (clusterId: number, payload: NodeReadinessProbeRequest = {}) =>
    apiClient
      .post<NodeReadinessProbeResponse>(
        `/api/k8s/clusters/${clusterId}/node-readiness/probe`,
        payload,
      )
      .then((res) => res.data),

  // Kubernetes Resource Management (Phase 5)
  createK8sResource: (clusterId: number, resourceType: string, data: { resource_yaml: string; namespace?: string; dry_run?: boolean }) =>
    apiClient.post<{ success: boolean; message: string; resource: K8sResource }>(`/api/k8s/clusters/${clusterId}/resources/${resourceType}`, data).then((res) => res.data),

  updateK8sResource: (clusterId: number, resourceType: string, resourceName: string, data: { resource_yaml: string; namespace?: string; dry_run?: boolean }) =>
    apiClient.put<{ success: boolean; message: string; resource: K8sResource }>(`/api/k8s/clusters/${clusterId}/resources/${resourceType}/${resourceName}`, data).then((res) => res.data),

  deleteK8sResource: (clusterId: number, resourceType: string, resourceName: string, params?: { namespace?: string; dry_run?: boolean }) =>
    apiClient.delete<{ success: boolean; message: string }>(`/api/k8s/clusters/${clusterId}/resources/${resourceType}/${resourceName}`, { params }).then((res) => res.data),

  scaleDeployment: (clusterId: number, deploymentName: string, data: { replicas: number; namespace: string }) =>
    apiClient.post<{ success: boolean; message: string; replicas: number }>(`/api/k8s/clusters/${clusterId}/deployments/${deploymentName}/scale`, data).then((res) => res.data),

  getPodLogs: (clusterId: number, podName: string, params: { namespace: string; container?: string; tail_lines?: number }) =>
    apiClient.get<{ logs: string; pod_name: string; namespace: string }>(`/api/k8s/clusters/${clusterId}/pods/${podName}/logs`, { params }).then((res) => res.data),

  restartPod: (clusterId: number, podName: string, params: { namespace: string }) =>
    apiClient.post<{ success: boolean; message: string }>(`/api/k8s/clusters/${clusterId}/pods/${podName}/restart`, null, { params }).then((res) => res.data),

  // Kubernetes Advanced Operations
  describeResource: (clusterId: number, resourceType: string, resourceName: string, namespace?: string) =>
    apiClient.get<{ 
      metadata: K8sResource['metadata']; 
      conditions?: K8sCondition[]; 
      events?: K8sEvent[]; 
      status?: K8sResourceStatus;
      relationships?: { owned?: Array<{ kind: string; name: string }>; related?: Array<{ kind: string; name: string }> } 
    }>(`/api/k8s/clusters/${clusterId}/resources/${resourceType}/${resourceName}/describe`, { params: { namespace } }).then((res) => res.data),

  getClusterEvents: (clusterId: number, params?: { namespace?: string; resource_type?: string; resource_name?: string; event_type?: string }) =>
    apiClient.get<{ events: K8sEvent[]; count: number }>(`/api/k8s/clusters/${clusterId}/events`, { params }).then((res) => res.data),

  getPodContainers: (clusterId: number, podName: string, namespace: string) =>
    apiClient.get<{ pod_name: string; namespace: string; containers: K8sPodContainer[]; init_containers: K8sPodContainer[] }>(`/api/k8s/clusters/${clusterId}/pods/${podName}/containers`, { params: { namespace } }).then((res) => res.data),

  getPodMetrics: (clusterId: number, params?: { namespace?: string; sort_by?: string }) =>
    apiClient.get<{ available: boolean; metrics?: K8sPodMetric[]; error?: string }>(`/api/k8s/clusters/${clusterId}/top/pods`, { params }).then((res) => res.data),

  getNodeMetrics: (clusterId: number, params?: { sort_by?: string }) =>
    apiClient.get<{ available: boolean; metrics?: K8sNodeMetric[]; error?: string }>(`/api/k8s/clusters/${clusterId}/top/nodes`, { params }).then((res) => res.data),

  // Kubernetes Rollout Management (Phase 3)
  getRolloutHistory: (clusterId: number, deploymentName: string, namespace: string) =>
    apiClient.get<{ history: K8sRolloutHistoryEntry[]; deployment_name: string; namespace: string }>(`/api/k8s/clusters/${clusterId}/deployments/${deploymentName}/rollout/history`, { params: { namespace } }).then((res) => res.data),

  getRolloutStatus: (clusterId: number, deploymentName: string, namespace: string) =>
    apiClient.get<K8sRolloutStatus>(`/api/k8s/clusters/${clusterId}/deployments/${deploymentName}/rollout/status`, { params: { namespace } }).then((res) => res.data),

  rolloutUndo: (clusterId: number, deploymentName: string, namespace: string, revision?: number) =>
    apiClient.post<{ success: boolean; message: string }>(`/api/k8s/clusters/${clusterId}/deployments/${deploymentName}/rollout/undo`, null, { params: { namespace, revision } }).then((res) => res.data),

  rolloutRestart: (clusterId: number, deploymentName: string, namespace: string) =>
    apiClient.post<{ success: boolean; message: string }>(`/api/k8s/clusters/${clusterId}/deployments/${deploymentName}/rollout/restart`, null, { params: { namespace } }).then((res) => res.data),

  rolloutPause: (clusterId: number, deploymentName: string, namespace: string) =>
    apiClient.post<{ success: boolean; message: string; paused: boolean }>(`/api/k8s/clusters/${clusterId}/deployments/${deploymentName}/rollout/pause`, null, { params: { namespace } }).then((res) => res.data),

  rolloutResume: (clusterId: number, deploymentName: string, namespace: string) =>
    apiClient.post<{ success: boolean; message: string; paused: boolean }>(`/api/k8s/clusters/${clusterId}/deployments/${deploymentName}/rollout/resume`, null, { params: { namespace } }).then((res) => res.data),

  // Kubernetes Node Operations (Phase 3)
  cordonNode: (clusterId: number, nodeName: string) =>
    apiClient.post<{ success: boolean; message: string }>(`/api/k8s/clusters/${clusterId}/nodes/${nodeName}/cordon`).then((res) => res.data),

  uncordonNode: (clusterId: number, nodeName: string) =>
    apiClient.post<{ success: boolean; message: string }>(`/api/k8s/clusters/${clusterId}/nodes/${nodeName}/uncordon`).then((res) => res.data),

  drainNode: (clusterId: number, nodeName: string, options?: { ignore_daemonsets?: boolean; delete_emptydir_data?: boolean; force?: boolean; grace_period?: number }) =>
    apiClient.post<{ success: boolean; message: string; evicted_pods: Array<{ name: string; namespace: string }>; failed_pods: Array<{ name: string; namespace: string; error: string }> }>(`/api/k8s/clusters/${clusterId}/nodes/${nodeName}/drain`, null, { params: options }).then((res) => res.data),

  // Kubernetes Patch, Label, Annotate Operations (Phase 6)
  patchK8sResource: (clusterId: number, resourceType: string, resourceName: string, data: { patch_data: Record<string, JsonValue>; namespace?: string; patch_type?: 'strategic' | 'merge' | 'json' }) =>
    apiClient.patch<{ success: boolean; message: string; resource: K8sResource }>(`/api/k8s/clusters/${clusterId}/resources/${resourceType}/${resourceName}`, data).then((res) => res.data),

  labelK8sResource: (clusterId: number, resourceType: string, resourceName: string, data: { labels: Record<string, string>; namespace?: string; overwrite?: boolean }) =>
    apiClient.post<{ success: boolean; message: string; resource: K8sResource }>(`/api/k8s/clusters/${clusterId}/resources/${resourceType}/${resourceName}/label`, data).then((res) => res.data),

  annotateK8sResource: (clusterId: number, resourceType: string, resourceName: string, data: { annotations: Record<string, string>; namespace?: string; overwrite?: boolean }) =>
    apiClient.post<{ success: boolean; message: string; resource: K8sResource }>(`/api/k8s/clusters/${clusterId}/resources/${resourceType}/${resourceName}/annotate`, data).then((res) => res.data),

  // Kubernetes Cluster Management
  refreshClusterKubeconfig: (clusterId: number) =>
    apiClient.post<{ success: boolean; message: string; refresh_method?: string }>(`/api/k8s/clusters/${clusterId}/refresh-kubeconfig`).then((res) => res.data),

  // Config Export/Import/Diff
  exportClusterConfig: (clusterId: number) =>
    apiClient.get(`/api/clusters/${clusterId}/bnk/export`, { responseType: 'blob' }).then((res) => res.data),

  exportClusterConfigJson: (clusterId: number) =>
    apiClient.get<ConfigExport>(`/api/clusters/${clusterId}/bnk/export/json`).then((res) => res.data),

  importClusterConfig: (clusterId: number, config: Record<string, unknown>) =>
    apiClient.post<ConfigImportResult>(`/api/clusters/${clusterId}/bnk/import`, { config }).then((res) => res.data),

  diffClusterConfigs: (clusterAId: number, clusterBId: number) =>
    apiClient.post<ConfigDiffResult>('/api/clusters/bnk/diff', { cluster_a_id: clusterAId, cluster_b_id: clusterBId }).then((res) => res.data),

  // BNK Upgrade Workflow
  getBnkUpgradeVersions: (clusterId: number) =>
    apiClient.get<BnkUpgradeVersionsResponse>(`/api/k8s/clusters/${clusterId}/bnk/upgrade/versions`).then((res) => res.data),

  getBnkCurrentVersion: (clusterId: number) =>
    apiClient.get<BnkCurrentVersion>(`/api/k8s/clusters/${clusterId}/bnk/upgrade/current`).then((res) => res.data),

  createBnkUpgradePlan: (clusterId: number, targetVersion: string) =>
    apiClient.post<BnkUpgrade>(`/api/k8s/clusters/${clusterId}/bnk/upgrade/plan`, { target_version: targetVersion }).then((res) => res.data),

  executeBnkUpgrade: (clusterId: number, upgradeId: number) =>
    apiClient.post<BnkUpgradeExecuteResponse>(`/api/k8s/clusters/${clusterId}/bnk/upgrade/${upgradeId}/execute`).then((res) => res.data),

  rollbackBnkUpgrade: (clusterId: number, upgradeId: number) =>
    apiClient.post<BnkUpgradeRollbackResponse>(`/api/k8s/clusters/${clusterId}/bnk/upgrade/${upgradeId}/rollback`).then((res) => res.data),

  cancelBnkUpgrade: (clusterId: number, upgradeId: number) =>
    apiClient.post<BnkUpgrade>(`/api/k8s/clusters/${clusterId}/bnk/upgrade/${upgradeId}/cancel`).then((res) => res.data),

  getBnkUpgradeHistory: (clusterId: number, limit: number = 20) =>
    apiClient.get<BnkUpgradeHistoryResponse>(`/api/k8s/clusters/${clusterId}/bnk/upgrade/history`, { params: { limit } }).then((res) => res.data),

  getBnkUpgradeDetail: (clusterId: number, upgradeId: number) =>
    apiClient.get<BnkUpgrade>(`/api/k8s/clusters/${clusterId}/bnk/upgrade/${upgradeId}`).then((res) => res.data),

  // BNK Release Registry (issue #217)
  getBnkReleases: (activeOnly = true) =>
    apiClient.get<BnkReleaseRegistryResponse>('/api/bnk/releases', { params: { active_only: activeOnly } }).then((res) => res.data),

  syncBnkReleasesFromOci: (clusterId: number) =>
    apiClient.post<{ tags_fetched: number; matched: number; unmatched: number; upserted: number }>(
      `/api/k8s/clusters/${clusterId}/bnk/releases/sync`,
    ).then((res) => res.data),

  // DPF (NVIDIA DPU Infrastructure)
  getDpfDetect: (clusterId: number) =>
    apiClient.get<DpfDetectResponse>(`/api/k8s/clusters/${clusterId}/dpf/detect`).then((res) => res.data),

  getDpfData: (clusterId: number) =>
    apiClient.get<DpfDataResponse>(`/api/k8s/clusters/${clusterId}/dpf/data`).then((res) => res.data),

  getDpfHealth: (clusterId: number) =>
    apiClient.get<DpfHealthEndpointResponse>(`/api/k8s/clusters/${clusterId}/dpf/health`).then((res) => res.data),

  // ADR-424: BNK Multi-host / Multi-DPU cluster configuration & member assignment
  configureBnkCluster: (clusterId: number, data: BnkClusterConfigCreateRequest) =>
    apiClient.post<BnkClusterConfigSummary>(`/api/k8s/clusters/${clusterId}/bnk-config`, data).then((res) => res.data),

  assignBnkClusterMembers: (clusterId: number, data: BnkClusterMemberAssignRequest) =>
    apiClient.post<BnkClusterMemberAssignResponse>(`/api/k8s/clusters/${clusterId}/bnk-members`, data).then((res) => res.data),
};
