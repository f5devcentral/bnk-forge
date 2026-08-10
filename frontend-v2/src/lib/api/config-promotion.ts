/**
 * Cross-Cluster Config Promotion API methods (UX-013).
 */
import { apiClient } from './client';
import type {
  PromoteRequest,
  PromoteResponse,
  PromotionHistoryResponse,
} from '@/types/config-promotion';

export const configPromotionApi = {
  /**
   * Promote config from source to target cluster.
   *
   * When dryRun=true, returns diff only (no mutations).
   * When dryRun=false, applies the delta and logs to history.
   */
  promoteConfig: async (
    sourceClusterId: number,
    targetClusterId: number,
    dryRun: boolean
  ): Promise<PromoteResponse> => {
    const body: PromoteRequest = {
      source_cluster_id: sourceClusterId,
      target_cluster_id: targetClusterId,
      dry_run: dryRun,
    };
    const { data } = await apiClient.post<PromoteResponse>(
      '/api/config/promote',
      body
    );
    return data;
  },

  /** List promotion history (newest first). */
  listPromotions: async (
    limit = 20,
    offset = 0
  ): Promise<PromotionHistoryResponse> => {
    const { data } = await apiClient.get<PromotionHistoryResponse>(
      '/api/config/promotions',
      { params: { limit, offset } }
    );
    return data;
  },
};
