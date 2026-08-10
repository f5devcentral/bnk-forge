/**
 * Bluefield Software Images API (unified BFB + DOCA catalog).
 *
 * Each entry pairs a BFB bootstream image with its DOCA release metadata.
 * The standalone `/api/doca-images` endpoint has been removed — all DOCA
 * fields now live on the bluefield image resource.
 */
import { apiClient } from './client';

/**
 * Unified BFB + DOCA entry returned by the backend.
 *
 * Note: the generated types in api-generated.ts may be stale until the next
 * `make openapi-types` run. These manual types match the current backend
 * Pydantic schema (BluefieldSoftwareImageResponse).
 */
export interface BluefieldImage {
  id: number;
  image_filename: string | null;
  base_url: string | null;
  doca_version: string | null;
  doca_package: string | null;
  host_os: string | null;
  host_os_version: string | null;
  host_arch: string | null;
  doca_host_url: string | null;
  is_default: boolean;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateBluefieldImageData {
  image_filename?: string | null;
  base_url?: string | null;
  doca_version: string;
  doca_package?: string | null;
  host_os: string;
  host_os_version?: string | null;
  host_arch: string;
  doca_host_url?: string | null;
  is_default?: boolean;
  notes?: string | null;
}

export interface UpdateBluefieldImageData {
  image_filename?: string | null;
  base_url?: string | null;
  doca_version?: string | null;
  doca_package?: string | null;
  host_os?: string | null;
  host_os_version?: string | null;
  host_arch?: string | null;
  doca_host_url?: string | null;
  is_default?: boolean;
  notes?: string | null;
}

export const bluefieldImagesApi = {
  list: () =>
    apiClient
      .get<BluefieldImage[]>('/api/bluefield-images')
      .then((res) => res.data),

  get: (id: number) =>
    apiClient
      .get<BluefieldImage>(`/api/bluefield-images/${id}`)
      .then((res) => res.data),

  getDefault: () =>
    apiClient
      .get<BluefieldImage | null>('/api/bluefield-images/default')
      .then((res) => res.data),

  create: (data: CreateBluefieldImageData) =>
    apiClient
      .post<BluefieldImage>('/api/bluefield-images', data)
      .then((res) => res.data),

  update: (id: number, data: UpdateBluefieldImageData) =>
    apiClient
      .patch<BluefieldImage>(`/api/bluefield-images/${id}`, data)
      .then((res) => res.data),

  delete: (id: number) =>
    apiClient.delete(`/api/bluefield-images/${id}`).then((res) => res.data),
};
