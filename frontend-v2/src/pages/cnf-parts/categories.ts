/**
 * buildCnfCategories — pure function that converts a flat CRD list into a
 * sorted category tree for the CNF sidebar.
 *
 * Grouping: CRDInfo.category (if present, registry-enriched) else CRDInfo.group.
 * Sort: enriched categories alphabetically first, then bare-group categories alphabetically.
 * Items within each category: alphabetical by label.
 */

import type { CRDInfo } from '@/hooks/useCrds';
import type { CnfCategory, CnfCategoryItem } from './cnf-constants';

export function buildCnfCategories(crds: CRDInfo[]): CnfCategory[] {
  const map = new Map<string, CnfCategory>();

  for (const crd of crds) {
    const enriched = !!crd.category;
    const category = crd.category ?? crd.group;
    const item: CnfCategoryItem = {
      key: crd.name,
      label: crd.display_name ?? crd.kind,
      source: crd.source,
      crdInfo: crd,
    };

    const existing = map.get(category);
    if (existing) {
      existing.items.push(item);
      // If any item in the bucket is enriched, mark the bucket as enriched.
      if (enriched) existing.enriched = true;
    } else {
      map.set(category, { category, enriched, items: [item] });
    }
  }

  const categories = [...map.values()];

  // Sort items within each category alphabetically by label.
  for (const cat of categories) {
    cat.items.sort((a, b) => a.label.localeCompare(b.label));
  }

  // Sort categories: enriched first (alphabetical within group), then bare-group (alphabetical).
  categories.sort((a, b) => {
    if (a.enriched !== b.enriched) return a.enriched ? -1 : 1;
    return a.category.localeCompare(b.category);
  });

  return categories;
}
