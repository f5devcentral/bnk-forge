/**
 * Release Sources API client — BNK catalog management (ADR-494).
 */
import { apiClient } from './client';
import type { components } from '@/types/api-generated';

export type ReleaseSource = components['schemas']['ReleaseSourceResponse'];
export type ReleaseSourceCreate = components['schemas']['ReleaseSourceCreate'];
export type ReleaseSourceUpdate = components['schemas']['ReleaseSourceUpdate'];
export type ReleaseSourceKind = components['schemas']['ReleaseSourceKind'];
export type SyncSourceResponse = components['schemas']['SyncSourceResponse'];
export type ReleaseSourceTagList = components['schemas']['ReleaseSourceTagList'];
export type ReleaseSourceTag = components['schemas']['ReleaseSourceTag'];
export type PullTagsSummary = components['schemas']['PullTagsSummary'];
export type FailedTag = components['schemas']['FailedTag'];

export const releaseSourcesApi = {
  list: async (): Promise<ReleaseSource[]> => {
    const resp = await apiClient.get<ReleaseSource[]>('/api/bare-metal/release-sources');
    return resp.data;
  },

  get: async (id: number): Promise<ReleaseSource> => {
    const resp = await apiClient.get<ReleaseSource>(`/api/bare-metal/release-sources/${id}`);
    return resp.data;
  },

  create: async (payload: ReleaseSourceCreate): Promise<ReleaseSource> => {
    const resp = await apiClient.post<ReleaseSource>('/api/bare-metal/release-sources', payload);
    return resp.data;
  },

  update: async (id: number, payload: ReleaseSourceUpdate): Promise<ReleaseSource> => {
    const resp = await apiClient.patch<ReleaseSource>(
      `/api/bare-metal/release-sources/${id}`,
      payload,
    );
    return resp.data;
  },

  remove: async (id: number): Promise<void> => {
    await apiClient.delete(`/api/bare-metal/release-sources/${id}`);
  },

  sync: async (id: number, manifestYaml: string): Promise<SyncSourceResponse> => {
    const resp = await apiClient.post<SyncSourceResponse>(
      `/api/bare-metal/release-sources/${id}/sync`,
      { manifest_yaml: manifestYaml },
    );
    return resp.data;
  },

  listTags: async (id: number): Promise<ReleaseSourceTagList> => {
    const resp = await apiClient.get<ReleaseSourceTagList>(
      `/api/bare-metal/release-sources/${id}/tags`,
    );
    return resp.data;
  },

  pullTags: async (id: number, tags: string[]): Promise<PullTagsSummary> => {
    // Preserve the literal colon in the path — do NOT URL-encode it.
    const resp = await apiClient.post<PullTagsSummary>(
      `/api/bare-metal/release-sources/${id}/tags:pull`,
      { tags },
    );
    return resp.data;
  },
};
