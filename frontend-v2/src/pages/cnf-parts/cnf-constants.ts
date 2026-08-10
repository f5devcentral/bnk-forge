/**
 * CNF Dashboard — types and constants.
 *
 * No special views — the CNF page is purely discovery-driven.
 * All sidebar items derive from runtime CRD data, not static lists.
 */

import type { CRDInfo } from '@/hooks/useCrds';

export type { CRDInfo };

/** A grouped category entry built at runtime from discovered CRDs. */
export interface CnfCategory {
  /** Category label (from CRDInfo.category, or the raw API group as fallback). */
  category: string;
  /**
   * Whether this category came from the static registry (curated) or from
   * the bare API group (discovered-only). Curated categories sort first.
   */
  enriched: boolean;
  items: CnfCategoryItem[];
}

export interface CnfCategoryItem {
  /** Stable key = CRDInfo.name (`<plural>.<group>`). */
  key: string;
  /** Human-readable label: display_name if present, else kind. */
  label: string;
  /** Source badge: 'registry-enriched' | 'discovered'. */
  source: string;
  /** Original CRD info, kept for detail-panel / table use. */
  crdInfo: CRDInfo;
}
