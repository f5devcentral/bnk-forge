/**
 * CRDInfo.category → curated category display-name map.
 *
 * `CRDInfo.category` is always a slug (backend `ResourceCategory` constants
 * in `backend/core/k8s_types.py`, e.g. 'gateway-api', 'cert-manager',
 * 'networking'), never a Title-Case display name. This is the single source
 * of truth both `buildK8sCategories` and `buildBnkCategories` use to route a
 * registry-enriched CRD into its existing curated tab instead of spawning a
 * duplicate slug-named tab next to it.
 *
 * Slugs with no entry here (e.g. 'f5-bnk', 'dpf') have no matching curated
 * tab in either static tree — callers fall back to the raw slug/group, which
 * is the existing (correct) behavior for those.
 */
const CRD_CATEGORY_LABELS: Record<string, string> = {
  'gateway-api': 'Gateway API',
  'cert-manager': 'cert-manager',
  networking: 'Networking',
};

export function crdCategoryLabel(slug: string | null | undefined): string | undefined {
  if (!slug) return undefined;
  return CRD_CATEGORY_LABELS[slug] ?? slug;
}
