import { apiClient } from './client';
import type {
  BlueprintRelease,
  BlueprintSource,
  BlueprintSourceCreate,
  BlueprintSourceUpdate,
  BlueprintSourceSyncResult,
} from '@/types';

export const blueprintCatalogApi = {
  getBlueprintSources: (params?: { source_type?: string; is_active?: boolean }) =>
    apiClient.get<BlueprintSource[]>('/api/blueprint-catalog/sources', { params }).then((res) => res.data),

  createBlueprintSource: (data: BlueprintSourceCreate) =>
    apiClient.post<BlueprintSource>('/api/blueprint-catalog/sources', data).then((res) => res.data),

  updateBlueprintSource: (sourceId: number, data: BlueprintSourceUpdate) =>
    apiClient.put<BlueprintSource>(`/api/blueprint-catalog/sources/${sourceId}`, data).then((res) => res.data),

  deleteBlueprintSource: (sourceId: number) =>
    apiClient.delete<{ success: boolean; message: string }>(`/api/blueprint-catalog/sources/${sourceId}`).then((res) => res.data),

  syncBlueprintSource: (sourceId: number) =>
    apiClient.post<BlueprintSourceSyncResult>(`/api/blueprint-catalog/sources/${sourceId}/sync`).then((res) => res.data),

  importBlueprintRelease: (releaseId: number, data?: { state_reason?: string }) =>
    apiClient.post<BlueprintRelease>(`/api/blueprint-catalog/releases/${releaseId}/import`, data ?? {}).then((res) => res.data),

  deleteBlueprintRelease: (releaseId: number) =>
    apiClient.delete<{ success: boolean; message: string }>(`/api/blueprint-catalog/releases/${releaseId}`).then((res) => res.data),

  unimportBlueprintRelease: (releaseId: number) =>
    apiClient.post<BlueprintRelease>(`/api/blueprint-catalog/releases/${releaseId}/unimport`).then((res) => res.data),

  setBlueprintReleaseVisibility: (releaseId: number, isVisible: boolean) =>
    apiClient.patch<BlueprintRelease>(`/api/blueprint-catalog/releases/${releaseId}/visibility`, { is_visible: isVisible }).then((res) => res.data),

  getBlueprintReleases: (params?: {
    source_id?: number;
    blueprint_id?: string;
    release_state?: string;
    validation_state?: string;
    is_active?: boolean;
  }) => apiClient.get<BlueprintRelease[]>('/api/blueprint-catalog/releases', { params }).then((res) => res.data),
};
