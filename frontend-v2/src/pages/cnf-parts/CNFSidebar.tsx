/**
 * CNF Sidebar — runtime-built category navigation.
 *
 * Unlike F5BNKSidebar (static hardcoded list), this sidebar derives its items
 * from the CRD discovery response at runtime via buildCnfCategories. It is a
 * thin adapter over the shared <ResourceCategorySidebar> (D-020) so all three
 * resource explorers share one visual idiom.
 */

import { useMemo } from 'react';
import { Eye, Layers } from 'lucide-react';
import {
  ResourceCategorySidebar,
  type ResourceCategoryGroup,
} from '@/components/layout/ResourceCategorySidebar';
import type { CnfCategory } from './cnf-constants';

interface CNFSidebarProps {
  categories: CnfCategory[];
  selectedCrdKey: string | null;
  onSelectCrd: (key: string) => void;
  expandedCategories: string[];
  onToggleCategory: (category: string) => void;
}

export function CNFSidebar({
  categories,
  selectedCrdKey,
  onSelectCrd,
  expandedCategories,
  onToggleCategory,
}: CNFSidebarProps) {
  const groups = useMemo<ResourceCategoryGroup[]>(
    () =>
      categories.map((cat) => ({
        category: cat.category,
        // Enriched (curated) categories get the Layers glyph; raw-discovered
        // ones get the Eye glyph — preserves the prior visual distinction.
        icon: cat.enriched ? Layers : Eye,
        count: cat.items.length,
        items: cat.items.map((item) => ({
          key: item.key,
          label: item.label,
          tag: item.source === 'registry-enriched' ? 'curated' : undefined,
        })),
      })),
    [categories],
  );

  return (
    <ResourceCategorySidebar
      aria-label="CNF resource categories"
      groups={groups}
      selectedKey={selectedCrdKey}
      onSelect={onSelectCrd}
      expandedCategories={expandedCategories}
      onToggleCategory={onToggleCategory}
    />
  );
}
