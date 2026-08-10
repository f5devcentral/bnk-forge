/**
 * F5 BNK Sidebar — items of the active category.
 *
 * D-020: the top-level categories are now header tabs (ResourceViewTabs), so
 * this sidebar shows just the active category's items as one flat list over the
 * shared <ResourceCategorySidebar> — same quiet idiom as Kubernetes and CNF.
 *
 * #140: accepts an optional `categories` prop (discovery-built list) so the
 * parent can pass a live-merged category tree; falls back to the static
 * bnkResourceCategories when not provided.
 */

import { useMemo } from 'react';
import {
  ResourceCategorySidebar,
  type ResourceCategoryGroup,
} from '@/components/layout/ResourceCategorySidebar';
import { bnkResourceCategories } from './bnk-constants';
import type { BnkCategory } from './bnk-categories';

interface F5BNKSidebarProps {
  /** The active top-level category (selected via the header tabs). */
  activeCategory: string;
  selectedResourceType: string;
  onSelectResourceType: (type: string) => void;
  /** Discovery-merged category list; falls back to static bnkResourceCategories. */
  categories?: BnkCategory[];
}

export function F5BNKSidebar({
  activeCategory,
  selectedResourceType,
  onSelectResourceType,
  categories,
}: F5BNKSidebarProps) {
  const resolvedCategories = categories ?? bnkResourceCategories;

  const groups = useMemo<ResourceCategoryGroup[]>(() => {
    const cat = resolvedCategories.find((c) => c.category === activeCategory);
    if (!cat) return [];
    return [
      {
        category: cat.category,
        items: cat.items.map((item) => ({
          key: item.key,
          label: item.label,
          icon: item.icon,
        })),
      },
    ];
  }, [resolvedCategories, activeCategory]);

  return (
    <ResourceCategorySidebar
      aria-label="F5 BNK resources"
      hideGroupHeaders
      groups={groups}
      selectedKey={selectedResourceType}
      onSelect={onSelectResourceType}
      expandedCategories={[]}
      onToggleCategory={() => {}}
    />
  );
}
