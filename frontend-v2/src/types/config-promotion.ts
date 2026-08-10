/**
 * Cross-Cluster Config Promotion types (UX-013).
 *
 * Mirrors backend Pydantic models from routes/config_promotion.py.
 */

/** Action type for a promotion change */
export type PromotionAction = 'add' | 'modify' | 'remove';

/** Single resource change in a promotion diff */
export interface PromotionChange {
  action: PromotionAction;
  kind: string;
  name: string;
  namespace: string;
  diff: string | null;
}

/** Cluster identity summary */
export interface PromotionClusterInfo {
  id: number;
  name: string;
}

/** Request body for POST /api/config/promote */
export interface PromoteRequest {
  source_cluster_id: number;
  target_cluster_id: number;
  dry_run: boolean;
}

/** Apply results (only present when applied=true) */
export interface PromotionApplyResults {
  applied: Array<{ kind: string; name: string; namespace: string }>;
  failed: Array<{ kind: string; name: string; namespace: string; error: string }>;
  skipped: Array<{ kind: string; name: string; namespace: string; reason: string }>;
}

/** Response from POST /api/config/promote */
export interface PromoteResponse {
  source_cluster: PromotionClusterInfo;
  target_cluster: PromotionClusterInfo;
  changes: PromotionChange[];
  total_changes: number;
  applied: boolean;
  apply_results: PromotionApplyResults | null;
}

/** Promotion history entry */
export interface PromotionHistoryEntry {
  id: number;
  source_cluster_name: string;
  target_cluster_name: string;
  change_count: number;
  applied_by: string | null;
  applied_at: string;
  success: boolean;
  applied_count: number;
  failed_count: number;
  skipped_count: number;
  changes_summary: Array<{
    action: PromotionAction;
    kind: string;
    name: string;
    namespace: string;
  }> | null;
}

/** Response from GET /api/config/promotions */
export interface PromotionHistoryResponse {
  total: number;
  items: PromotionHistoryEntry[];
}
