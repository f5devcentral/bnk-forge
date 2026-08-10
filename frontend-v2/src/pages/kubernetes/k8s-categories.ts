/**
 * buildK8sCategories — merges the static K8s resource tree with live CRD discovery.
 *
 * Built-in K8s kinds (Pods, Deployments, etc.) are NOT CRDs and won't appear in
 * discovery, so `resourceTree` is always the baseline.  Discovered CRDs are merged
 * into matching static categories (by category name, via crdCategoryLabel — CRDInfo.category
 * is a slug, e.g. 'gateway-api', not a display name) or appended as new categories.
 * Duplicate keys within a category are deduplicated against the static item's registry
 * key (kind.lower()), since discovered/curated keys don't share a naming convention.
 */

import type { CRDInfo } from '@/hooks/useCrds';
import { crdCategoryLabel } from '@/lib/crd-category-labels';
import { resourceTree } from './k8s-constants';

export interface K8sStaticItem {
  key: string;
  label: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  icon: any;
}

export interface K8sCategory {
  category: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  icon: any;
  items: K8sStaticItem[];
}

export function buildK8sCategories(crds: CRDInfo[]): K8sCategory[] {
  // Deep-clone the static baseline so we never mutate the imported constant.
  const result: K8sCategory[] = resourceTree.map((cat) => ({
    category: cat.category,
    icon: cat.icon,
    items: cat.items.map((item) => ({ key: item.key, label: item.label, icon: item.icon })),
  }));

  // Build a lookup for fast category matching.
  const catIndex = new Map<string, K8sCategory>(result.map((c) => [c.category, c]));

  for (const crd of crds) {
    const categoryName = crdCategoryLabel(crd.category) ?? crd.group;
    const label = crd.display_name ?? crd.kind;
    // Stable, globally-unique identity (<plural>.<group>) — avoids resolve_crd's
    // ambiguous-bare-plural 400 when the same plural exists in multiple groups.
    const key = crd.name;
    // Static registry keys are conventionally kind.lower() (see
    // core/k8s_resource_registry.py) — used to detect a registry-enriched CRD
    // that's already represented by a static item, so it isn't double-listed.
    const registryKey = crd.kind.toLowerCase();

    let bucket = catIndex.get(categoryName);
    if (!bucket) {
      // New category not in static baseline — append it.
      bucket = { category: categoryName, icon: null, items: [] };
      result.push(bucket);
      catIndex.set(categoryName, bucket);
    }

    const alreadyPresent = bucket.items.some((i) => i.key === registryKey || i.key === key);
    if (!alreadyPresent) {
      bucket.items.push({ key, label, icon: null });
    }
  }

  return result;
}
