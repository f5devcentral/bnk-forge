/**
 * ResourceCategorySidebar — the single in-page category-navigation idiom shared
 * by the three resource-explorer pages (Kubernetes, F5 BNK, CNF).
 *
 * D-020 Option A: a *quiet* sidebar. It recedes so the content + detail panel
 * are the visual focus. No card background, no tinted/bordered selection — the
 * selected item is just `text-primary font-medium`. Category headers are small
 * uppercase eyebrow labels. Per-item decorations (status dot, count, tag) are
 * optional so each page maps its own data onto one consistent shape.
 */

import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { ChevronDown, ChevronRight } from 'lucide-react';

export interface ResourceCategoryItem {
  key: string;
  label: string;
  icon?: React.ElementType;
  /** Small status dot color class, e.g. 'bg-success' / 'bg-destructive'. */
  dotColor?: string;
  /** Numeric count rendered quietly on the right. */
  count?: number;
  /** Render a loading shimmer instead of a count. */
  countLoading?: boolean;
  /** Small text tag, e.g. 'curated'. */
  tag?: string;
  /** Native title/tooltip. */
  title?: string;
}

export interface ResourceCategoryGroup {
  category: string;
  icon?: React.ElementType;
  items: ResourceCategoryItem[];
  /** Optional count shown faintly in the category header. */
  count?: number;
}

interface ResourceCategorySidebarProps {
  groups: ResourceCategoryGroup[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
  expandedCategories: string[];
  onToggleCategory: (category: string) => void;
  /** Optional content rendered above the category list (e.g. a hint card). */
  header?: ReactNode;
  /**
   * Render items as one flat list without the collapsible eyebrow group
   * headers. Used when a parent tab already names the active category
   * (F5 BNK), so the group header would be redundant. expandedCategories /
   * onToggleCategory are ignored in this mode.
   */
  hideGroupHeaders?: boolean;
  /** Tailwind width class. Default: 'w-52' (~208px — deliberately narrow). */
  width?: string;
  className?: string;
  'aria-label'?: string;
}

export function ResourceCategorySidebar({
  groups,
  selectedKey,
  onSelect,
  expandedCategories,
  onToggleCategory,
  header,
  hideGroupHeaders = false,
  width = 'w-52',
  className,
  'aria-label': ariaLabel = 'Resource categories',
}: ResourceCategorySidebarProps) {
  const renderItem = (item: ResourceCategoryItem) => {
    const ItemIcon = item.icon;
    const isSelected = selectedKey === item.key;

    return (
      <button
        key={item.key}
        onClick={() => onSelect(item.key)}
        title={item.title}
        className={cn(
          'flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm transition-colors',
          isSelected
            ? 'font-medium text-primary'
            : 'text-muted-foreground hover:bg-muted/40 hover:text-foreground',
        )}
      >
        {ItemIcon && <ItemIcon className="h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />}
        <span className="flex-1 truncate text-left">{item.label}</span>
        {item.dotColor && (
          <span
            className={cn('h-2 w-2 flex-shrink-0 rounded-full', item.dotColor)}
            aria-hidden="true"
          />
        )}
        {item.tag && (
          <Badge variant="outline" className="h-4 px-1 text-[9px]">
            {item.tag}
          </Badge>
        )}
        {item.count !== undefined ? (
          <span className="min-w-[1.5rem] text-right text-[10px] tabular-nums text-muted-foreground/70">
            {item.count}
          </span>
        ) : item.countLoading ? (
          <span className="h-1 w-6 animate-pulse rounded bg-muted-foreground/20" />
        ) : null}
      </button>
    );
  };

  if (hideGroupHeaders) {
    return (
      <aside
        aria-label={ariaLabel}
        className={cn(
          width,
          'flex-shrink-0 overflow-y-auto border-r border-border bg-background',
          className,
        )}
      >
        {header && <div className="p-3 pb-0">{header}</div>}
        <nav className="space-y-0.5 p-3">
          {groups.flatMap((group) => group.items).map(renderItem)}
        </nav>
      </aside>
    );
  }

  return (
    <aside
      aria-label={ariaLabel}
      className={cn(
        width,
        'flex-shrink-0 overflow-y-auto border-r border-border bg-background',
        className,
      )}
    >
      {header && <div className="p-3 pb-0">{header}</div>}

      <nav className="space-y-3 p-3">
        {groups.map((group) => {
          const expanded = expandedCategories.includes(group.category);
          const GroupIcon = group.icon;
          const slug = group.category.toLowerCase().replace(/\s+/g, '-');

          return (
            <div key={group.category}>
              <button
                onClick={() => onToggleCategory(group.category)}
                aria-expanded={expanded}
                aria-controls={`rcs-group-${slug}`}
                className="flex w-full items-center gap-1.5 px-1 py-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground/70 transition-colors hover:text-muted-foreground"
              >
                {expanded ? (
                  <ChevronDown className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
                ) : (
                  <ChevronRight className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
                )}
                {GroupIcon && <GroupIcon className="h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />}
                <span className="flex-1 truncate text-left">{group.category}</span>
                {group.count !== undefined && (
                  <span className="text-[10px] font-normal tabular-nums">{group.count}</span>
                )}
              </button>

              {expanded && (
                <div id={`rcs-group-${slug}`} className="mt-1 space-y-0.5">
                  {group.items.map(renderItem)}
                </div>
              )}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
