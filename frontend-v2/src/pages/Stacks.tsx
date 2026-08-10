/**
 * Blueprints page — D-020 redesign.
 *
 * One coherent surface: bold heading, chip-filter row, single SectionCard
 * holding a flat table of every blueprint sorted by deployment order.
 * No in-page sidebar, no per-category color tints, no decorative gradients —
 * status conveyed by small Badge variants only.
 */

import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { SectionCard } from '@/components/ui/section-card';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { StackDetailDialog } from '@/components/stacks/StackDetailDialog';
import { ImportedBlueprintDeployDialog } from '@/components/stacks/ImportedBlueprintDeployDialog';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { useStackTemplates } from '@/hooks/useStacks';
import { PageHeader } from '@/components/layout/PageHeader';
import { usePageRefresh } from '@/hooks/usePageRefresh';
import { cn } from '@/lib/utils';
import { Search, Rocket, MoreVertical, Eye } from 'lucide-react';
import type { StackDeployKind, StackTemplateList } from '@/types';

const INITIAL_STACKS_LIMIT = 20;

// Category labels — step number prefix gives the deployment order without
// needing a separate colorful badge.
const categoryConfig: Record<string, { step: number | null; label: string; helper: string }> = {
  infrastructure: {
    step: 1,
    label: 'Infrastructure',
    helper: 'Provisions the base infrastructure — VPC, K8s cluster. No BNK version required.',
  },
  'bare-metal': {
    step: 1,
    label: 'Bare Metal',
    helper: 'Provisions DPU hardware and bootstraps K8s on bare-metal hosts.',
  },
  bnk: {
    step: 2,
    label: 'Platform (BNK)',
    helper: 'Installs the BNK platform onto an existing cluster. Check the BNK version column for compatibility.',
  },
  solution: {
    step: 3,
    label: 'Solutions',
    helper: 'Applications and demos that run on top of a deployed BNK platform.',
  },
  custom: {
    step: null,
    label: 'Custom',
    helper: 'User-created blueprint combinations.',
  },
};

// Provider label only — no per-cloud color, all rendered as `Badge variant="muted"`.
const providerLabels: Record<string, string> = {
  aws: 'AWS',
  azure: 'Azure',
  gcp: 'GCP',
  openshift: 'OpenShift',
  ibm: 'IBM',
  any: 'Multi-platform',
  'bare-metal': 'Bare metal',
};

// Maturity → Badge variant (status semantic, not raw palette).
const maturityConfig: Record<
  string,
  { label: string; variant: 'success' | 'info' | 'warning' | 'destructive' | 'muted' }
> = {
  'production-ready': { label: 'Production', variant: 'success' },
  reference: { label: 'Reference', variant: 'info' },
  beta: { label: 'Beta', variant: 'warning' },
  alpha: { label: 'Alpha', variant: 'destructive' },
  experimental: { label: 'Experimental', variant: 'warning' },
};

const categorySortOrder: Record<string, number> = {
  infrastructure: 0,
  'bare-metal': 1,
  bnk: 2,
  solution: 3,
  custom: 4,
};

export default function Stacks() {
  const navigate = useNavigate();

  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [selectedStackSlug, setSelectedStackSlug] = useState<string>('');
  const [selectedDeployKind, setSelectedDeployKind] = useState<StackDeployKind>('template');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [showAllStacks, setShowAllStacks] = useState(false);

  const { data: stacks, isLoading, isError, error, refetch } = useStackTemplates({
    category: selectedCategory || undefined,
  });

  const filteredStacks = useMemo(() => {
    return (stacks || [])
      .filter((stack) => {
        if (!searchQuery) return true;
        const q = searchQuery.toLowerCase();
        return (
          stack.name.toLowerCase().includes(q) ||
          stack.description?.toLowerCase().includes(q) ||
          stack.tags?.some((tag) => tag.toLowerCase().includes(q))
        );
      })
      .sort((a, b) => {
        const orderA = categorySortOrder[a.category?.toLowerCase() || 'custom'] ?? 99;
        const orderB = categorySortOrder[b.category?.toLowerCase() || 'custom'] ?? 99;
        if (orderA !== orderB) return orderA - orderB;
        if (a.is_featured !== b.is_featured) return a.is_featured ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
  }, [stacks, searchQuery]);

  const categoryCounts = useMemo(() => {
    const counts: Record<string, number> = { all: stacks?.length || 0 };
    stacks?.forEach((stack) => {
      const cat = stack.category?.toLowerCase() || 'custom';
      counts[cat] = (counts[cat] || 0) + 1;
    });
    return counts;
  }, [stacks]);

  const limitedStacks = showAllStacks ? filteredStacks : filteredStacks.slice(0, INITIAL_STACKS_LIMIT);
  const hasMoreStacks = filteredStacks.length > INITIAL_STACKS_LIMIT;

  const handleDeploy = (stack: StackTemplateList) => {
    const kind = stack.deploy_kind ?? 'template';
    // builtin-release: open StackDetailDialog by the template slug (blueprint_id)
    // git-release: open ImportedBlueprintDeployDialog by `release-{id}` slug
    // template: open StackDetailDialog by template slug
    const slug = kind === 'builtin-release' ? (stack.deploy_slug ?? stack.slug) : stack.slug;
    setSelectedDeployKind(kind);
    setSelectedStackSlug(slug);
    setDialogOpen(true);
  };

  const { refresh, isRefreshing } = usePageRefresh();

  const activeHelper =
    selectedCategory && categoryConfig[selectedCategory]?.helper;

  // Chips: All, then categories in deployment order (skip 0-count to keep the row calm).
  const chipKeys = ['all', ...Object.keys(categoryConfig).filter((k) => (categoryCounts[k] || 0) > 0)];

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto" data-onboarding="stacks-page">
      {/* Header */}
      <PageHeader
        title="Blueprints"
        subtitle="Reusable deployment patterns. Deploy in order: infrastructure → platform → solutions."
        onRefresh={refresh}
        isRefreshing={isRefreshing}
      />

      {/* Filter chips + search */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          {chipKeys.map((key) => {
            const isAll = key === 'all';
            const cfg = isAll ? null : categoryConfig[key];
            const selected = isAll ? selectedCategory === null : selectedCategory === key;
            const count = categoryCounts[key] || 0;
            const label = isAll ? 'All' : cfg?.label || key;
            return (
              <button
                key={key}
                type="button"
                onClick={() => {
                  setSelectedCategory(isAll ? null : key);
                  setShowAllStacks(false);
                }}
                className={cn(
                  'inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm transition-colors',
                  selected
                    ? 'bg-foreground text-background border-foreground'
                    : 'bg-card text-muted-foreground border-border hover:text-foreground',
                )}
                aria-pressed={selected}
              >
                {!isAll && cfg?.step && (
                  <span
                    className={cn(
                      'inline-flex items-center justify-center h-4 w-4 rounded-full text-[10px] font-semibold tabular-nums',
                      selected ? 'bg-background/20 text-background' : 'bg-muted text-muted-foreground',
                    )}
                  >
                    {cfg.step}
                  </span>
                )}
                <span>{label}</span>
                <span
                  className={cn(
                    'text-xs tabular-nums',
                    selected ? 'text-background/70' : 'text-muted-foreground/70',
                  )}
                >
                  {count}
                </span>
              </button>
            );
          })}
        </div>

        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search blueprints…"
            aria-label="Search blueprints"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9 h-9"
          />
        </div>

        {activeHelper && (
          <p className="text-xs text-muted-foreground">{activeHelper}</p>
        )}
      </div>

      {/* Content */}
      {isError ? (
        <ErrorState error={error} onRetry={refetch} />
      ) : isLoading ? (
        <SectionCard>
          <div className="space-y-2">
            {[...Array(6)].map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-md" />
            ))}
          </div>
        </SectionCard>
      ) : filteredStacks.length === 0 ? (
        <EmptyState
          icon={Rocket}
          title="No blueprints found"
          description={
            searchQuery
              ? `No blueprints match "${searchQuery}"`
              : 'No blueprints available'
          }
        />
      ) : (
        <SectionCard
          title={`${filteredStacks.length} ${filteredStacks.length === 1 ? 'blueprint' : 'blueprints'}`}
          compact
        >
          <div className="overflow-x-auto -mx-4">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs uppercase tracking-wider text-muted-foreground">
                  <th scope="col" className="text-left font-medium px-4 py-2">
                    Blueprint
                  </th>
                  <th scope="col" className="text-left font-medium px-4 py-2">
                    Category
                  </th>
                  <th scope="col" className="text-left font-medium px-4 py-2">
                    BNK Version
                  </th>
                  <th scope="col" className="text-left font-medium px-4 py-2">
                    Provider
                  </th>
                  <th scope="col" className="text-left font-medium px-4 py-2">
                    Maturity
                  </th>
                  <th scope="col" className="text-right font-medium px-4 py-2 w-28">
                    {/* actions */}
                  </th>
                </tr>
              </thead>
              <tbody>
                {limitedStacks.map((stack) => {
                  const categoryKey = (stack.category || 'custom').toLowerCase();
                  const category = categoryConfig[categoryKey];
                  const providerKey = stack.cloud_provider?.toLowerCase() || 'any';
                  const providerLabel = providerLabels[providerKey] || providerKey;
                  const maturity = stack.maturity ? maturityConfig[stack.maturity] : null;

                  return (
                    <tr
                      key={stack.slug}
                      className="border-t border-border cursor-pointer hover:bg-muted/40 transition-colors"
                      onClick={() => handleDeploy(stack)}
                    >
                      <td className="px-4 py-3">
                        <div className="flex flex-col">
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-foreground">{stack.name}</span>
                            {stack.version && (
                              <span className="text-xs text-muted-foreground tabular-nums">
                                v{stack.version}
                              </span>
                            )}
                            {stack.source_kind === 'blueprint_release' && (
                              <Badge variant="muted" className="text-[10px]">
                                Catalog
                              </Badge>
                            )}
                          </div>
                          {stack.description && (
                            <span className="text-xs text-muted-foreground line-clamp-1 mt-0.5">
                              {stack.description}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        {category ? (
                          <span className="inline-flex items-center gap-1.5 text-sm text-foreground/80">
                            {category.step && (
                              <span className="inline-flex items-center justify-center h-4 w-4 rounded-full bg-muted text-[10px] font-semibold tabular-nums text-muted-foreground">
                                {category.step}
                              </span>
                            )}
                            {category.label}
                          </span>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {stack.bnk_version ? (
                          <span className="text-sm font-mono text-foreground/80">
                            BNK {stack.bnk_version}
                          </span>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant="outline" className="text-xs">
                          {providerLabel}
                        </Badge>
                      </td>
                      <td className="px-4 py-3">
                        {maturity ? (
                          <Badge variant={maturity.variant} className="text-xs">
                            {maturity.label}
                          </Badge>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            size="sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeploy(stack);
                            }}
                          >
                            Deploy
                          </Button>
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild onClick={(e) => e.stopPropagation()}>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-8 w-8 p-0"
                                aria-label="Blueprint actions"
                              >
                                <MoreVertical className="h-4 w-4" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleDeploy(stack);
                                }}
                              >
                                <Eye className="h-4 w-4 mr-2" />
                                View Details
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {!showAllStacks && hasMoreStacks && (
            <div className="mt-4 flex justify-center">
              <Button variant="outline" size="sm" onClick={() => setShowAllStacks(true)}>
                Show {filteredStacks.length - INITIAL_STACKS_LIMIT} more
              </Button>
            </div>
          )}
          {showAllStacks && hasMoreStacks && (
            <div className="mt-4 flex justify-center">
              <Button variant="ghost" size="sm" onClick={() => setShowAllStacks(false)}>
                Show less
              </Button>
            </div>
          )}
        </SectionCard>
      )}

      {/* Deploy routing:
          - builtin-release: opens StackDetailDialog by the template slug (blueprint_id),
            giving full region/bare-metal/prereq/optional-module support.
          - git-release: opens ImportedBlueprintDeployDialog by `release-{id}` slug.
          - template (no matching release): opens StackDetailDialog by template slug. */}
      {selectedDeployKind === 'git-release' ? (
        <ImportedBlueprintDeployDialog
          slug={selectedStackSlug}
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          onSuccess={(projectId) => navigate(`/projects/${projectId}`)}
        />
      ) : (
        <StackDetailDialog
          slug={selectedStackSlug}
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          onSuccess={(projectId) => navigate(`/projects/${projectId}`)}
        />
      )}
    </div>
  );
}
