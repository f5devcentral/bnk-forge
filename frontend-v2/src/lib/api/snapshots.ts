/**
 * Snapshots API client (UX-011).
 *
 * CRUD operations for config snapshots, restore, and diff.
 */

import { apiClient } from './client';
import type {
  SnapshotListResponse,
  ConfigSnapshotDetail,
  CreateSnapshotRequest,
  CreateSnapshotResponse,
  RestoreSnapshotResponse,
  DiffSnapshotsRequest,
  SnapshotDiffResponse,
} from '@/types/snapshots';

export const snapshotsApi = {
  /** List snapshots for a project (newest first) */
  listSnapshots: (projectId: number, limit = 50, offset = 0) =>
    apiClient
      .get<SnapshotListResponse>(`/api/projects/${projectId}/snapshots`, {
        params: { limit, offset },
      })
      .then((res) => res.data),

  /** Create a manual snapshot */
  createSnapshot: (projectId: number, data: CreateSnapshotRequest) =>
    apiClient
      .post<CreateSnapshotResponse>(`/api/projects/${projectId}/snapshots`, data)
      .then((res) => res.data),

  /** Get full snapshot detail (includes config_data) */
  getSnapshot: (snapshotId: number) =>
    apiClient
      .get<ConfigSnapshotDetail>(`/api/snapshots/${snapshotId}`)
      .then((res) => res.data),

  /** Restore cluster config from a snapshot */
  restoreSnapshot: (snapshotId: number) =>
    apiClient
      .post<RestoreSnapshotResponse>(`/api/snapshots/${snapshotId}/restore`)
      .then((res) => res.data),

  /** Diff two snapshots */
  diffSnapshots: (data: DiffSnapshotsRequest) =>
    apiClient
      .post<SnapshotDiffResponse>('/api/snapshots/diff', data)
      .then((res) => res.data),

  /** Delete a snapshot */
  deleteSnapshot: (snapshotId: number) =>
    apiClient
      .delete<{ message: string; id: number }>(`/api/snapshots/${snapshotId}`)
      .then((res) => res.data),
};
