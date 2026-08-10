import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';

/** Statuses that indicate work is in flight — short polling here is appropriate. */
const TRANSITIONAL_STATUSES = new Set([
  'initializing',
  'planning',
  'applying',
  'destroying',
]);

export interface ModuleStateTransition {
  id: number;
  from_status: string;
  to_status: string;
  task_id: number | null;
  fence_token: number | null;
  reason: string | null;
  at: string;
}

export interface ModuleStateHistoryResponse {
  module_id: number;
  count: number;
  transitions: ModuleStateTransition[];
}

/**
 * React Query hook for the Phase 2 state-transition audit log.
 *
 * Polls every 5s while the module's current status is transitional
 * (initializing/planning/applying/destroying) so the UI surfaces new
 * audit rows in near-real-time during a deploy. Falls back to the
 * default cache TTL when the module is idle.
 */
export function useModuleStateHistory(
  moduleId: number,
  options?: { limit?: number; currentStatus?: string },
) {
  const limit = options?.limit ?? 50;
  const isInFlight = options?.currentStatus
    ? TRANSITIONAL_STATUSES.has(options.currentStatus)
    : false;

  return useQuery<ModuleStateHistoryResponse>({
    queryKey: queryKeys.modules.project.stateHistory(moduleId, limit),
    queryFn: () => api.getModuleStateHistory(moduleId, limit),
    enabled: !!moduleId,
    refetchInterval: isInFlight ? 5_000 : false,
    refetchIntervalInBackground: false,
  });
}
