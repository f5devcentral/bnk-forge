/**
 * Proxy Migration API client (D-021 P3).
 * Guided, gated, reversible proxy-to-BNK cutover.
 */
import { apiClient } from './client';
import type {
  ProxyMigration,
  ProxyMigrationListResponse,
  CreateMigrationRequest,
  ConfirmCutoverRequest,
  ConfirmTeardownRequest,
  AbortMigrationRequest,
  MigrationActionResponse,
} from '@/types/proxy-migration';

export const proxyMigrationApi = {
  /** List all migration runs for a cluster. */
  list: async (clusterId: number, projectId?: number): Promise<ProxyMigrationListResponse> => {
    const params: Record<string, string> = {};
    if (projectId !== undefined) params.project_id = String(projectId);
    const resp = await apiClient.get<ProxyMigrationListResponse>(
      `/api/k8s/clusters/${clusterId}/proxies/migrations`,
      { params }
    );
    return resp.data;
  },

  /** Get a single migration run. */
  get: async (clusterId: number, migrationId: number): Promise<ProxyMigration> => {
    const resp = await apiClient.get<ProxyMigration>(
      `/api/k8s/clusters/${clusterId}/proxies/migrations/${migrationId}`
    );
    return resp.data;
  },

  /** Create and start a migration run. */
  create: async (clusterId: number, data: CreateMigrationRequest): Promise<ProxyMigration> => {
    const resp = await apiClient.post<ProxyMigration>(
      `/api/k8s/clusters/${clusterId}/proxies/migrations`,
      data
    );
    return resp.data;
  },

  /**
   * Confirm that DNS/VIP has been pointed to the BNK gateway.
   * Only allowed when migration status == 'awaiting_cutover'.
   * Forge does NOT flip DNS — this records the operator's external action.
   */
  confirmCutover: async (
    clusterId: number,
    migrationId: number,
    data: ConfirmCutoverRequest
  ): Promise<MigrationActionResponse> => {
    const resp = await apiClient.post<MigrationActionResponse>(
      `/api/k8s/clusters/${clusterId}/proxies/migrations/${migrationId}/confirm-cutover`,
      data
    );
    return resp.data;
  },

  /** Confirm teardown of the old proxy. Only allowed when status == 'awaiting_teardown'. */
  confirmTeardown: async (
    clusterId: number,
    migrationId: number,
    data: ConfirmTeardownRequest
  ): Promise<MigrationActionResponse> => {
    const resp = await apiClient.post<MigrationActionResponse>(
      `/api/k8s/clusters/${clusterId}/proxies/migrations/${migrationId}/confirm-teardown`,
      data
    );
    return resp.data;
  },

  /** Abort a migration run. Pre-cutover: deletes new BNK resources, old proxy untouched. */
  abort: async (
    clusterId: number,
    migrationId: number,
    data: AbortMigrationRequest
  ): Promise<MigrationActionResponse> => {
    const resp = await apiClient.post<MigrationActionResponse>(
      `/api/k8s/clusters/${clusterId}/proxies/migrations/${migrationId}/abort`,
      data
    );
    return resp.data;
  },
};
