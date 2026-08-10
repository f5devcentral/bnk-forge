/**
 * Drift Detection API methods
 */
import { apiClient } from './client';
import type {
  DriftSettings,
  DriftSettingsRequest,
  DriftCheck,
  DriftSummary,
  ClusterDriftStatus,
  TriggerDriftCheckRequest,
  RecentDriftedItem,
} from '@/types';

export const driftApi = {
  // Drift Detection
  getDriftSettings: (projectId: number) =>
    apiClient.get<DriftSettings>(`/api/projects/${projectId}/drift/settings`).then((res) => res.data),

  updateDriftSettings: (projectId: number, data: DriftSettingsRequest) =>
    apiClient.put<DriftSettings>(`/api/projects/${projectId}/drift/settings`, data).then((res) => res.data),

  enableDriftDetection: (projectId: number) =>
    apiClient.post<DriftSettings>(`/api/projects/${projectId}/drift/enable`).then((res) => res.data),

  disableDriftDetection: (projectId: number) =>
    apiClient.post<DriftSettings>(`/api/projects/${projectId}/drift/disable`).then((res) => res.data),

  getDriftChecks: (projectId: number, params?: { module_id?: number; limit?: number; offset?: number }) =>
    apiClient.get<DriftCheck[]>(`/api/projects/${projectId}/drift/checks`, { params }).then((res) => res.data),

  getDriftCheck: (checkId: number) =>
    apiClient.get<DriftCheck>(`/api/drift/checks/${checkId}`).then((res) => res.data),

  triggerDriftCheck: (projectId: number, data?: TriggerDriftCheckRequest) =>
    apiClient.post<{ message: string; task_ids: number[] }>(`/api/projects/${projectId}/drift/check-now`, data || {}).then((res) => res.data),

  triggerModuleDriftCheck: (moduleId: number) =>
    apiClient.post<{ message: string; task_id: number }>(`/api/project-modules/${moduleId}/drift/check-now`).then((res) => res.data),

  getProjectDriftSummary: (projectId: number) =>
    apiClient.get<DriftSummary>(`/api/projects/${projectId}/drift/summary`).then((res) => res.data),

  getGlobalDriftSummary: () =>
    apiClient.get<DriftSummary>('/api/drift/summary').then((res) => res.data),

  getDriftStats: (params?: { project_id?: number; days?: number }) =>
    apiClient.get<{ total_checks: number; drift_detected: number; no_drift: number; failed: number; average_check_duration: number; last_check_at?: string }>('/api/drift/stats', { params }).then((res) => res.data),

  getClusterDriftStatus: (clusterId: number) =>
    apiClient.get<ClusterDriftStatus>(`/api/clusters/${clusterId}/drift/status`).then((res) => res.data),

  getRecentDrifted: (params?: { limit?: number }) =>
    apiClient.get<RecentDriftedItem[]>('/api/drift/recent', { params }).then((res) => res.data),
};
