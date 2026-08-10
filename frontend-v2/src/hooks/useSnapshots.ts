/**
 * React Query hooks for Config Snapshots (UX-011).
 *
 * Provides query + mutation hooks for snapshot CRUD, restore, and diff.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { snapshotsApi } from '@/lib/api/snapshots';
import { queryKeys } from '@/lib/queryKeys';
import { notify } from '@/lib/notify';
import type { CreateSnapshotRequest, DiffSnapshotsRequest } from '@/types/snapshots';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// ============================================================================
// Query Hooks
// ============================================================================

/** List snapshots for a project (newest first) */
export const useSnapshots = (projectId: number | undefined, limit = 50, offset = 0) => {
  return useQuery({
    queryKey: queryKeys.snapshots.list(projectId, limit, offset),
    queryFn: () => snapshotsApi.listSnapshots(projectId!, limit, offset),
    enabled: !!projectId,
  });
};

/** Get full snapshot detail (includes config_data) */
export const useSnapshot = (snapshotId: number | undefined) => {
  return useQuery({
    queryKey: queryKeys.snapshots.detail(snapshotId),
    queryFn: () => snapshotsApi.getSnapshot(snapshotId!),
    enabled: !!snapshotId,
  });
};

// ============================================================================
// Mutation Hooks
// ============================================================================

/** Create a manual snapshot */
export const useCreateSnapshot = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: ({ projectId, data }: { projectId: number; data: CreateSnapshotRequest }) =>
      snapshotsApi.createSnapshot(projectId, data),
    onSuccess: (result, variables) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.snapshots.list(variables.projectId) });
      notify.success('Config snapshot created',
        result.label
          ? `Snapshot "${result.label}" saved successfully.`
          : 'Current configuration has been saved.',
        { category: 'system' },
      );
    },
  });
};

/** Restore from a snapshot */
export const useRestoreSnapshot = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (snapshotId: number) => snapshotsApi.restoreSnapshot(snapshotId),
    onSuccess: (result) => {
      // Invalidate related caches
      queryClient.invalidateQueries({ queryKey: queryKeys.snapshots.all });
      queryClient.invalidateQueries({ queryKey: ['kubernetes'] });
      queryClient.invalidateQueries({ queryKey: ['f5bnk'] });

      const applied = result.results.applied.length;
      const failed = result.results.failed.length;

      if (failed > 0) {
        notify.warning('Snapshot partially restored',
          `${applied} resources applied, ${failed} failed. Check the results for details.`,
          { category: 'system' },
        );
      } else {
        notify.success('Snapshot restored successfully',
          `${applied} resources re-applied to cluster "${result.target_cluster}".`,
          { category: 'system' },
        );
      }
    },
  });
};

/** Diff two snapshots */
export const useDiffSnapshots = () => {
  return useAppMutation({
    mutationFn: (data: DiffSnapshotsRequest) => snapshotsApi.diffSnapshots(data),
  });
};

/** Delete a snapshot */
export const useDeleteSnapshot = () => {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (snapshotId: number) => snapshotsApi.deleteSnapshot(snapshotId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.snapshots.all });
      notify.success('Snapshot deleted', undefined, { category: 'system' });
    },
  });
};
