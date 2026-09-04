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

  const domainSlugMap: Record<string, string> = {
    'gateway-api': 'Gateways & Traffic',
    'traffic-management': 'Gateways & Traffic',
    'gateways': 'Gateways & Traffic',
    'security': 'Policies & Security',
    'firewall': 'Policies & Security',
    'policies': 'Policies & Security',
    'networking': 'System & Configuration',
    'f5-bnk': 'System & Configuration',
    'logging': 'System & Configuration',
    'system': 'System & Configuration',
    'ai-gateway': 'AI Gateway & A2A',
    'a2a': 'AI Gateway & A2A',
    'health': 'Health & Diagnostics',
    'diagnostics': 'Health & Diagnostics',
  };

  const curatedKinds = new Set<string>();
  for (const cat of result) {
    for (const item of cat.items) {
      curatedKinds.add(item.key.toLowerCase());
    }
  }

  for (const crd of crds) {
    const rawCategory = crd.category?.toLowerCase() || '';
    const mappedDomain = domainSlugMap[rawCategory] || (curatedNames.has(crd.category ?? '') ? crd.category : undefined);
    const categoryName = mappedDomain || (curatedNames.has(crd.group) ? crd.group : 'Other');
    const label = crd.display_name ?? crd.kind;
    const key = crd.name;
    const registryKey = crd.kind.toLowerCase();

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
