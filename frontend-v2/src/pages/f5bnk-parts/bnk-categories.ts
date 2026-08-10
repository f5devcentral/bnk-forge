/**
 * buildBnkCategories — merges the curated BNK category list with live CRD discovery.
 *
 * `bnkResourceCategories` is always the baseline (preserves curated ordering, special
 * VIEW_* entries, and icons).  Discovered CRDs are merged into matching static
 * categories by name, or appended as new categories.  Duplicate item keys are
 * deduplicated so discovery never double-renders a curated entry.
 *
 * Falls back to the static list unchanged when `crds` is empty (loading / unreachable).
 */

import type { CRDInfo } from '@/hooks/useCrds';
import { crdCategoryLabel } from '@/lib/crd-category-labels';
import { bnkResourceCategories } from './bnk-constants';

/**
 * API groups this page's curated categories actually populate from
 * (mirrors core/k8s_types.py ApiGroups). Passed to `useCrds`'s `group` filter
 * at the call site so the CRD fetch itself is scoped to F5/Gateway CRDs —
 * a generic cluster CRD (e.g. monitoring.coreos.com/ServiceMonitor) never
 * reaches `buildBnkCategories` and can't land in "Other" in the first place.
 */
export const BNK_CRD_GROUPS = [
  'gateway.networking.k8s.io', // Gateway API — Gateway, HTTPRoute, ReferenceGrant, ...
  'k8s.f5net.com',             // F5 BNK data-plane CRDs (default _f5_resource group)
  'k8s.f5.com',                // FLO-managed CRDs (CNEInstance, ...)
  'gateway.k8s.f5net.com',     // Gateway extensions (BNKSecPolicy, BNKNetPolicy, L4Route)
  'fic.f5.com',                // F5 IPAM Controller CRDs (IPAMRange, ...)
];

export interface BnkCategoryItem {
  key: string;
  label: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  icon: any;
}

export interface BnkCategory {
  category: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  icon: any;
  items: BnkCategoryItem[];
}

export function buildBnkCategories(crds: CRDInfo[]): BnkCategory[] {
  // Deep-clone the static baseline so we never mutate the imported constant.
  const result: BnkCategory[] = bnkResourceCategories.map((cat) => ({
    category: cat.category,
    icon: cat.icon,
    items: cat.items.map((item) => ({ key: item.key, label: item.label, icon: item.icon })),
  }));

  if (crds.length === 0) return result;

  const curatedNames = new Set(result.map((c) => c.category));
  const catIndex = new Map<string, BnkCategory>(result.map((c) => [c.category, c]));

  // Global kind-based dedup: a curated item can live in ANY curated bucket, not just
  // whichever tab the discovered CRD's backend category slug happens to map to. Curated
  // display names ('Traffic Management', 'Security', 'System', 'Networking') don't line
  // up 1:1 with backend ResourceCategory slugs — an installed Gateway CRD carries slug
  // 'gateway-api' (which isn't a curated tab name -> would land in 'Other') while curated
  // 'gateway' lives in 'Traffic Management'; BNKSecPolicy carries 'f5-bnk' (-> 'Other')
  // while curated 'bnksecpolicy' lives in 'Security'. Checking only the target bucket
  // missed this cross-tab case; a kind lookup across ALL curated buckets catches it.
  const curatedKinds = new Set<string>();
  for (const cat of result) {
    for (const item of cat.items) {
      curatedKinds.add(item.key.toLowerCase());
    }
  }

  for (const crd of crds) {
    // Only a CURATED category gets its own top-level tab. crdCategoryLabel maps a
    // backend category slug ('gateway-api', 'cert-manager', 'networking') to its
    // curated display name so a registry-enriched CRD lands in its real tab; raw CRD
    // groups ('monitoring.coreos.com', 'k8s.f5.com'), unmapped slugs ('f5-bnk'), and
    // uncategorized CRDs still collapse into ONE "Other" bucket. Otherwise a CRD-heavy
    // cluster (e.g. aws-syd-test: 110 CRDs) explodes the F5 BNK strip into ~27 tabs
    // that overflow and truncate to single letters.
    const discovered = crdCategoryLabel(crd.category) ?? crd.group;
    const categoryName = discovered && curatedNames.has(discovered) ? discovered : 'Other';
    const label = crd.display_name ?? crd.kind;
    // Stable, globally-unique identity (<plural>.<group>) — avoids resolve_crd's
    // ambiguous-bare-plural 400 when the same plural exists in multiple groups.
    const key = crd.name;
    // Static registry keys are conventionally kind.lower() (see
    // core/k8s_resource_registry.py) — used to detect a registry-enriched CRD
    // that's already represented by a static item, so it isn't double-listed.
    const registryKey = crd.kind.toLowerCase();

    // Already represented by a curated item somewhere — skip regardless of which
    // bucket this discovered CRD would otherwise target (see curatedKinds above).
    if (curatedKinds.has(registryKey)) continue;

    let bucket = catIndex.get(categoryName);
    if (!bucket) {
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
