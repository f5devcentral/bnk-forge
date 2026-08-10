/**
 * ResourceViewTabs — the ONE tab idiom across the app (D-020).
 *
 * A full-width underline tab bar. Tabs distribute across the row (flex-1) with a
 * max-width cap — so a 2-tab page reads substantial-but-not-huge while an 8-tab
 * page spreads evenly with breathing room instead of squashing. The active
 * indicator is an underline on the bar's bottom border.
 *
 * Two variants for the two page shells:
 *   - 'strip'  (default) — the full-bleed header strip on the resource-explorer
 *     pages (Kubernetes / F5 BNK / CNF). Carries its own gutter + card surface,
 *     sits between <ResourcePageHeader> and <ResourceExplorerLayout.Body>.
 *   - 'inline' — for centered content pages (p-6 space-y-6 max-w-7xl) such as
 *     Benchmarks. No gutter/surface of its own; just the underline bar within
 *     the page column, replacing a shadcn <TabsList>.
 */

import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';

export interface ResourceViewTab {
  key: string;
  label: string;
  icon?: React.ElementType;
  title?: string;
  disabled?: boolean;
  /** Optional trailing element (e.g. a count Badge) rendered after the label. */
  badge?: ReactNode;
}

interface ResourceViewTabsProps {
  tabs: ResourceViewTab[];
  active: string;
  onChange: (key: string) => void;
  variant?: 'strip' | 'inline';
  className?: string;
  'aria-label'?: string;
}

export function ResourceViewTabs({
  tabs,
  active,
  onChange,
  variant = 'strip',
  className,
  'aria-label': ariaLabel = 'View mode',
}: ResourceViewTabsProps) {
  return (
    <div
      className={cn(
        'border-b border-border',
        variant === 'strip' && 'flex-shrink-0 bg-card px-6',
        className,
      )}
    >
      <div role="tablist" aria-label={ariaLabel} className="flex w-full overflow-x-auto">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isActive = active === tab.key;
          return (
            <button
              key={tab.key}
              role="tab"
              aria-selected={isActive}
              aria-disabled={tab.disabled}
              disabled={tab.disabled}
              title={tab.title}
              onClick={() => !tab.disabled && onChange(tab.key)}
              className={cn(
                'flex min-w-0 max-w-[16rem] flex-1 items-center justify-center gap-2 whitespace-nowrap border-b-2 px-4 py-3 text-sm font-medium transition-colors',
                tab.disabled
                  ? 'cursor-not-allowed border-transparent text-muted-foreground/40'
                  : isActive
                    ? 'border-primary text-foreground'
                    : 'border-transparent text-muted-foreground hover:border-border hover:text-foreground',
              )}
            >
              {Icon && <Icon className="h-4 w-4 flex-shrink-0" aria-hidden="true" />}
              <span className="truncate">{tab.label}</span>
              {tab.badge && <span className="flex-shrink-0">{tab.badge}</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
