/**
 * React Query hooks for Proxy Migration (D-021 P3).
 *
 * Guided, gated, reversible proxy-to-BNK cutover:
 *   translate → deploy_bnk → verify → cutover (operator-confirmed) → teardown_old
 *
 * POLLING NOTE (AGENTS.md mutation+polling contract):
 *   Mutations that trigger async backend work use `await queryClient.refetchQueries()`
 *   (not invalidateQueries) so the polling hook sees the fresh transitional status
 *   before the refetchInterval evaluator fires.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { proxyMigrationApi } from '@/lib/api/proxy-migration';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';
import type {
  CreateMigrationRequest,
  ConfirmCutoverRequest,
  ConfirmTeardownRequest,
  AbortMigrationRequest,
  MigrationActionResponse,
  ProxyMigration,
} from '@/types/proxy-migration';
import { POLLING_MIGRATION_STATUSES, TERMINAL_MIGRATION_STATUSES } from '@/types/proxy-migration';

// ---------------------------------------------------------------------------
// List hook
// ---------------------------------------------------------------------------

export function useProxyMigrations(clusterId: number, projectId?: number) {
  return useQuery({
    queryKey: queryKeys.proxyMigrations.byCluster(clusterId),
    queryFn: () => proxyMigrationApi.list(clusterId, projectId),
    enabled: !!clusterId,
    staleTime: 10_000,
  });
}

// ---------------------------------------------------------------------------
// Single migration hook with polling
// ---------------------------------------------------------------------------

/**
 * Fetches a single migration run.
 * Polls every 3s while the run is in an active (non-terminal, non-waiting)
 * status so the UI reflects Celery task progress.
 */
export function useProxyMigration(clusterId: number, migrationId: number | null) {
  return useQuery({
    queryKey: queryKeys.proxyMigrations.detail(clusterId, migrationId ?? 0),
    queryFn: () => proxyMigrationApi.get(clusterId, migrationId as number),
    enabled: !!clusterId && !!migrationId,
    refetchInterval: (query) => {
      const migration = query.state.data as ProxyMigration | undefined;
      if (!migration) return false;
      const isPolling = POLLING_MIGRATION_STATUSES.includes(migration.status);
      return isPolling ? 3000 : false;
    },
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/** Create (and start) a new migration run. */
export function useCreateMigration(clusterId: number) {
  const queryClient = useQueryClient();
  return useAppMutation<ProxyMigration, Error, CreateMigrationRequest>({
    mutationFn: (data: CreateMigrationRequest) => proxyMigrationApi.create(clusterId, data),
    onSuccess: async () => {
      await queryClient.refetchQueries({
        queryKey: queryKeys.proxyMigrations.byCluster(clusterId),
      });
    },
  });
}

/** Confirm DNS/VIP cutover (operator-gated). Only valid in AWAITING_CUTOVER. */
export function useConfirmCutover(clusterId: number, migrationId: number) {
  const queryClient = useQueryClient();
  return useAppMutation<MigrationActionResponse, Error, ConfirmCutoverRequest>({
    mutationFn: (data: ConfirmCutoverRequest) =>
      proxyMigrationApi.confirmCutover(clusterId, migrationId, data),
    onSuccess: async () => {
      await queryClient.refetchQueries({
        queryKey: queryKeys.proxyMigrations.detail(clusterId, migrationId),
      });
      await queryClient.refetchQueries({
        queryKey: queryKeys.proxyMigrations.byCluster(clusterId),
      });
    },
  });
}

/** Confirm old-proxy teardown. Only valid in AWAITING_TEARDOWN. */
export function useConfirmTeardown(clusterId: number, migrationId: number) {
  const queryClient = useQueryClient();
  return useAppMutation<MigrationActionResponse, Error, ConfirmTeardownRequest>({
    mutationFn: (data: ConfirmTeardownRequest) =>
      proxyMigrationApi.confirmTeardown(clusterId, migrationId, data),
    onSuccess: async () => {
      await queryClient.refetchQueries({
        queryKey: queryKeys.proxyMigrations.detail(clusterId, migrationId),
      });
      await queryClient.refetchQueries({
        queryKey: queryKeys.proxyMigrations.byCluster(clusterId),
      });
    },
  });
}

/** Abort a migration run. */
export function useAbortMigration(clusterId: number, migrationId: number) {
  const queryClient = useQueryClient();
  return useAppMutation<MigrationActionResponse, Error, AbortMigrationRequest>({
    mutationFn: (data: AbortMigrationRequest) =>
      proxyMigrationApi.abort(clusterId, migrationId, data),
    onSuccess: async () => {
      await queryClient.refetchQueries({
        queryKey: queryKeys.proxyMigrations.detail(clusterId, migrationId),
      });
      await queryClient.refetchQueries({
        queryKey: queryKeys.proxyMigrations.byCluster(clusterId),
      });
    },
  });
}

// ---------------------------------------------------------------------------
// Derived helpers (used by the wizard component)
// ---------------------------------------------------------------------------

/** Returns true if the migration is in a terminal state. */
export function isMigrationTerminal(status: string): boolean {
  return TERMINAL_MIGRATION_STATUSES.includes(status as ProxyMigration['status']);
}

/** Returns a user-friendly label for a migration status. */
export function migrationStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: 'Pending',
    in_progress: 'Running',
    awaiting_verify: 'Awaiting Verify',
    verify_failed: 'Verify Failed',
    awaiting_cutover: 'Awaiting Cutover Confirmation',
    cutover_confirmed: 'Cutover Confirmed',
    awaiting_teardown: 'Awaiting Teardown Confirmation',
    completed: 'Completed',
    failed: 'Failed',
    aborted: 'Aborted',
    rolled_back: 'Rolled Back',
  };
  return labels[status] ?? status;
}
