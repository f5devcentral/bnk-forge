/**
 * Kubernetes Sidebar — resource-category navigation.
 *
 * D-020: a thin adapter over the shared <ResourceCategorySidebar>. The view-mode
 * switcher, Cluster Scan / Export Config buttons, and the unhealthy/namespace
 * toggles used to live in here; they now live in the page toolbar
 * (ResourcePageHeader actions), leaving this sidebar to do one quiet job —
 * navigate the resource tree — so the content + detail panel are the focus.
 *
 * #140 / #36: category tree is now driven by CRD discovery via buildK8sCategories,
 * with the static resourceTree as the always-present baseline for built-in K8s
 * kinds. Discovered CRDs (including l4route when present on the cluster) surface
 * automatically without code changes.
 */

import { useMemo } from 'react';
import {
  ResourceCategorySidebar,
  type ResourceCategoryGroup,
} from '@/components/layout/ResourceCategorySidebar';
import { useCrds } from '@/hooks/useCrds';
import { buildK8sCategories } from './k8s-categories';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ResourceSummaryEntry {
  count: number;
  unhealthy: number | null;
  available: boolean;
}

interface K8sSidebarProps {
  clusterId: number | null;
  selectedResourceType: string;
  onSelectResourceType: (type: string) => void;
  expandedCategories: string[];
  onToggleCategory: (category: string) => void;
  filteredResourceCount: number;
  /** When the cluster-scan view is active, no tree item is selected. */
  showClusterScan: boolean;
  resourceSummary?: Record<string, ResourceSummaryEntry>;
  isResourceSummaryLoading?: boolean;
  showOnlyUnhealthy?: boolean;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function K8sSidebar({
  clusterId,
  selectedResourceType,
  onSelectResourceType,
  expandedCategories,
  onToggleCategory,
  filteredResourceCount,
  showClusterScan,
  resourceSummary,
  isResourceSummaryLoading,
  showOnlyUnhealthy = false,
}: K8sSidebarProps) {
  const { data: crdsData } = useCrds(clusterId ?? 0, {
    enabled: !!clusterId,
    group: ['gateway.networking.k8s.io', 'cert-manager.io'],
  });

  const categoryTree = useMemo(
    () => buildK8sCategories(crdsData?.crds ?? []),
    [crdsData?.crds],
  );

  const groups = useMemo<ResourceCategoryGroup[]>(() => {
    return categoryTree
      .map((category) => {
        // When "only unhealthy" is on, hide items whose summary has no unhealthy
        // entries. Always keep the currently-selected item visible so the user
        // can see where they are.
        const visibleItems = showOnlyUnhealthy
          ? category.items.filter((item) => {
              if (item.key === selectedResourceType) return true;
              const s = resourceSummary?.[item.key];
              return s != null && s.unhealthy != null && s.unhealthy > 0;
            })
          : category.items;

        return {
          category: category.category,
          icon: category.icon,
          items: visibleItems.map((item) => {
            const isSelected = selectedResourceType === item.key && !showClusterScan;
            const summary = resourceSummary?.[item.key];
            // Selected view shows the live filtered count (reflects search
            // filter etc.); other rows show the backend summary count.
            const displayCount = isSelected ? filteredResourceCount : summary?.count;
            const hasCount = displayCount !== undefined && displayCount > 0;
            const unhealthy = summary?.unhealthy ?? null;

            // Dot colors:
            //   destructive — unhealthy > 0 (needs attention)
            //   success     — healthy, something exists
            //   muted       — count=0, or health isn't meaningful for this kind
            let dotColor: string;
            if (unhealthy !== null && unhealthy > 0) {
              dotColor = 'bg-destructive';
            } else if (unhealthy !== null && hasCount) {
              dotColor = 'bg-success';
            } else {
              dotColor = hasCount ? 'bg-muted-foreground/60' : 'bg-muted-foreground/20';
            }

            return {
              key: item.key,
              label: item.label,
              icon: item.icon,
              dotColor,
              count: displayCount,
              countLoading: displayCount === undefined && isResourceSummaryLoading,
              title:
                summary && unhealthy !== null && unhealthy > 0
                  ? `${unhealthy} unhealthy / ${summary.count} total`
                  : summary
                    ? `${summary.count} total`
                    : undefined,
            };
          }),
        };
      })
      // Collapse a category entirely if the filter removed all its items.
      .filter((group) => group.items.length > 0);
  }, [
    categoryTree,
    selectedResourceType,
    showClusterScan,
    filteredResourceCount,
    resourceSummary,
    isResourceSummaryLoading,
    showOnlyUnhealthy,
  ]);

  return (
    <ResourceCategorySidebar
      aria-label="Kubernetes resource categories"
      groups={groups}
      selectedKey={showClusterScan ? null : selectedResourceType}
      onSelect={onSelectResourceType}
      expandedCategories={expandedCategories}
      onToggleCategory={onToggleCategory}
    />
  );
}
