/**
 * React Query hooks for Cross-Cluster Config Promotion (UX-013).
 *
 * Provides:
 *   - usePromoteConfig: mutation for dry-run or actual promotion
 *   - usePromotionHistory: query for promotion audit log
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { configPromotionApi } from '@/lib/api/config-promotion';
import { queryKeys } from '@/lib/queryKeys';
import { notify } from '@/lib/notify';
import type { PromoteResponse, PromotionHistoryResponse } from '@/types/config-promotion';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// ---------------------------------------------------------------------------
// Query Keys (re-export from centralized factory for backward compatibility)
// ---------------------------------------------------------------------------

export const promotionKeys = {
  all: queryKeys.configPromotion.all,
  history: (limit?: number, offset?: number) => queryKeys.configPromotion.history(limit, offset),
};

// ---------------------------------------------------------------------------
// Promote Config Mutation
// ---------------------------------------------------------------------------

export function usePromoteConfig() {
  const queryClient = useQueryClient();

  return useAppMutation<
    PromoteResponse,
    Error,
    { sourceClusterId: number; targetClusterId: number; dryRun: boolean }
  >({
    mutationFn: ({ sourceClusterId, targetClusterId, dryRun }) =>
      configPromotionApi.promoteConfig(sourceClusterId, targetClusterId, dryRun),
    onSuccess: (data) => {
      if (data.applied) {
        notify({
          title: 'Promotion Complete',
          message: `Applied ${data.total_changes} changes from ${data.source_cluster.name} → ${data.target_cluster.name}`,
          severity: 'success',
        });
        // Invalidate promotion history
        queryClient.invalidateQueries({ queryKey: promotionKeys.all });
      }
    },
  });
}

// ---------------------------------------------------------------------------
// Promotion History Query
// ---------------------------------------------------------------------------

export function usePromotionHistory(limit = 20, offset = 0) {
  return useQuery<PromotionHistoryResponse>({
    queryKey: promotionKeys.history(limit, offset),
    queryFn: () => configPromotionApi.listPromotions(limit, offset),
  });
}
