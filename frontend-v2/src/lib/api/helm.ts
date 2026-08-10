/**
 * Helm API methods
 */
import { apiClient } from './client';
import type {
  HelmReleasesResponse,
  HelmReleaseResponse,
  HelmHistoryResponse,
  HelmValuesResponse,
  HelmChartsResponse,
  HelmRepositoriesResponse,
  UploadedHelmChart,
  UploadedChartsResponse,
  HelmRevisionComparison,
  InstallChartRequest,
  UpgradeReleaseRequest,
  RollbackReleaseRequest,
} from '@/types';
import type {
  ApiInstallChartRequest,
  ApiUpgradeReleaseRequest,
  ApiRollbackReleaseRequest,
  AssertKeysMatch,
} from '@/types/api-schemas';

// ── Compile-time contract checks ────────────────────────────────────────────
const _checkInstall: AssertKeysMatch<InstallChartRequest, ApiInstallChartRequest> = true;
const _checkUpgrade: AssertKeysMatch<UpgradeReleaseRequest, ApiUpgradeReleaseRequest> = true;
const _checkRollback: AssertKeysMatch<RollbackReleaseRequest, ApiRollbackReleaseRequest> = true;
// eslint-disable-next-line @typescript-eslint/no-unused-expressions
void _checkInstall, _checkUpgrade, _checkRollback;

export const helmApi = {
  // Helm Release Management
  listHelmReleases: (clusterId: number, namespace?: string, allNamespaces?: boolean) =>
    apiClient.get<HelmReleasesResponse>(`/api/k8s/${clusterId}/helm/releases`, {
      params: { namespace, all_namespaces: allNamespaces }
    }).then((res) => res.data),

  getHelmRelease: (clusterId: number, releaseName: string, namespace: string = 'default') =>
    apiClient.get<HelmReleaseResponse>(`/api/k8s/${clusterId}/helm/releases/${releaseName}`, {
      params: { namespace }
    }).then((res) => res.data),

  installHelmChart: (clusterId: number, request: InstallChartRequest) =>
    apiClient.post<{ success: boolean; result: { name: string; namespace: string; revision: number; status: string }; message: string }>(
      `/api/k8s/${clusterId}/helm/install`,
      request
    ).then((res) => res.data),

  upgradeHelmRelease: (clusterId: number, releaseName: string, request: UpgradeReleaseRequest, namespace: string = 'default') =>
    apiClient.put<{ success: boolean; result: { name: string; namespace: string; revision: number; status: string }; message: string }>(
      `/api/k8s/${clusterId}/helm/releases/${releaseName}/upgrade`,
      request,
      { params: { namespace } }
    ).then((res) => res.data),

  rollbackHelmRelease: (clusterId: number, releaseName: string, request: RollbackReleaseRequest, namespace: string = 'default') =>
    apiClient.post<{ success: boolean; result: { name: string; namespace: string; revision: number; status: string }; message: string }>(
      `/api/k8s/${clusterId}/helm/releases/${releaseName}/rollback`,
      request,
      { params: { namespace } }
    ).then((res) => res.data),

  uninstallHelmRelease: (clusterId: number, releaseName: string, namespace: string = 'default', keepHistory: boolean = false) =>
    apiClient.delete<{ success: boolean; result: { name: string; info: string }; message: string }>(
      `/api/k8s/${clusterId}/helm/releases/${releaseName}`,
      { params: { namespace, keep_history: keepHistory, wait: true, timeout: '5m' } }
    ).then((res) => res.data),

  getHelmHistory: (clusterId: number, releaseName: string, namespace: string = 'default', maxRevisions: number = 256) =>
    apiClient.get<HelmHistoryResponse>(
      `/api/k8s/${clusterId}/helm/releases/${releaseName}/history`,
      { params: { namespace, max_revisions: maxRevisions } }
    ).then((res) => res.data),

  getHelmValues: (clusterId: number, releaseName: string, namespace: string = 'default', allValues: boolean = false) =>
    apiClient.get<HelmValuesResponse>(
      `/api/k8s/${clusterId}/helm/releases/${releaseName}/values`,
      { params: { namespace, all_values: allValues } }
    ).then((res) => res.data),

  getHelmManifest: (clusterId: number, releaseName: string, namespace: string = 'default', revision?: number) =>
    apiClient.get<{ success: boolean; manifest: string }>(
      `/api/k8s/${clusterId}/helm/releases/${releaseName}/manifest`,
      { params: { namespace, revision } }
    ).then((res) => res.data),

  testHelmRelease: (clusterId: number, releaseName: string, namespace: string = 'default', timeout: string = '5m') =>
    apiClient.post<{ success: boolean; exit_code: number; output: string; error: string }>(
      `/api/k8s/${clusterId}/helm/releases/${releaseName}/test`,
      null,
      { params: { namespace, timeout } }
    ).then((res) => res.data),

  // Helm Repository Management
  listHelmRepositories: () =>
    apiClient.get<HelmRepositoriesResponse>('/api/helm/repositories').then((res) => res.data),

  addHelmRepository: (name: string, url: string, username?: string, password?: string) =>
    apiClient.post<{ success: boolean; message: string; name: string; url: string }>(
      '/api/helm/repositories',
      { name, url, username, password }
    ).then((res) => res.data),

  removeHelmRepository: (name: string) =>
    apiClient.delete<{ success: boolean; message: string }>(
      `/api/helm/repositories/${name}`
    ).then((res) => res.data),

  updateHelmRepositories: () =>
    apiClient.post<{ success: boolean; exit_code: number; output: string; error: string }>(
      '/api/helm/repositories/update'
    ).then((res) => res.data),

  searchHelmCharts: (keyword: string, repository?: string, maxResults: number = 100) =>
    apiClient.get<HelmChartsResponse>('/api/helm/charts/search', {
      params: { keyword, repository, max_results: maxResults }
    }).then((res) => res.data),

  browseHelmCharts: (repository?: string, maxResults: number = 100) =>
    apiClient.get<HelmChartsResponse>('/api/helm/charts/browse', {
      params: { repository, max_results: maxResults }
    }).then((res) => res.data),

  // Helm Chart Upload
  uploadHelmChart: (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return apiClient.post<{ success: boolean; chart: UploadedHelmChart }>(
      '/api/helm/charts/upload',
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } }
    ).then((res) => res.data);
  },

  listUploadedCharts: (sourceType?: 'uploaded' | 'cloned') =>
    apiClient.get<UploadedChartsResponse>('/api/helm/charts/uploaded', {
      params: sourceType ? { source_type: sourceType } : {}
    }).then((res) => res.data),

  deleteUploadedChart: (chartId: number) =>
    apiClient.delete<{ success: boolean; message: string }>(
      `/api/helm/charts/uploaded/${chartId}`
    ).then((res) => res.data),

  getChartValues: (chartId: number) =>
    apiClient.get<{ success: boolean; values: string; chart_name?: string }>(`/api/helm/charts/uploaded/${chartId}/values`).then((res) => res.data),

  updateChartValues: (chartId: number, values: string) =>
    apiClient.put<{ success: boolean; message: string }>(`/api/helm/charts/uploaded/${chartId}/values`, values, {
      headers: { 'Content-Type': 'text/plain' }
    }).then((res) => res.data),

  cloneHelmChart: (chart: string, version?: string, clonedBy?: string) =>
    apiClient.post<{ success: boolean; chart: UploadedHelmChart }>(
      '/api/helm/charts/clone',
      null,
      { params: { chart, version, cloned_by: clonedBy } }
    ).then((res) => res.data),

  // Helm Release Comparison
  compareHelmRevisions: (
    clusterId: number,
    releaseName: string,
    revision1: number,
    revision2: number,
    namespace?: string
  ) =>
    apiClient.get<HelmRevisionComparison>(
      `/api/k8s/${clusterId}/helm/releases/${releaseName}/compare`,
      { params: { revision1, revision2, namespace: namespace || 'default' } }
    ).then((res) => res.data),
};
