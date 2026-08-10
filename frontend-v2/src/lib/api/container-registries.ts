/**
 * Container Registries API methods.
 *
 * First-class OCI registry access management — orthogonal to cloud provider.
 * Standalone types (ghcr/quay/far) carry their own secret; derived types
 * (ecr/acr/icr/gar) reference a cloud credential template.
 */
import { apiClient } from './client';
import type { components } from '@/types/api-generated';

export type ContainerRegistry = components['schemas']['ContainerRegistryResponse'];
export type ContainerRegistryCreate = components['schemas']['ContainerRegistryCreate'];
export type ContainerRegistryUpdate = components['schemas']['ContainerRegistryUpdate'];

export interface ContainerRegistryTestResult {
  success: boolean;
  message?: string;
  error?: string;
  not_implemented?: boolean;
  type?: string;
  last_test_status?: string | null;
  last_test_at?: string | null;
  last_test_message?: string | null;
}

export const containerRegistriesApi = {
  listContainerRegistries: () =>
    apiClient.get<ContainerRegistry[]>('/api/container-registries').then((res) => res.data),

  getContainerRegistry: (id: number) =>
    apiClient.get<ContainerRegistry>(`/api/container-registries/${id}`).then((res) => res.data),

  createContainerRegistry: (data: ContainerRegistryCreate) =>
    apiClient.post<ContainerRegistry>('/api/container-registries', data).then((res) => res.data),

  updateContainerRegistry: (id: number, data: ContainerRegistryUpdate) =>
    apiClient.put<ContainerRegistry>(`/api/container-registries/${id}`, data).then((res) => res.data),

  deleteContainerRegistry: (id: number) =>
    apiClient.delete(`/api/container-registries/${id}`).then((res) => res.data),

  testContainerRegistry: (id: number) =>
    apiClient
      .post<ContainerRegistryTestResult>(`/api/container-registries/${id}/test`)
      .then((res) => res.data),
};
