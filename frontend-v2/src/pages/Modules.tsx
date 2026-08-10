/**
 * Module Catalog page — D-020 redesign.
 *
 * Single-surface bnkhealth shape: bold heading + actions, KPI strip, filter
 * dropdowns (source, category, platform) + search, single sortable table in
 * a SectionCard. Source/category in-page sidebar dropped; grid view dropped.
 */

import { useState, useMemo, memo, useCallback } from 'react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { SectionCard } from '@/components/ui/section-card';
import { ModuleDetailSheet } from '@/components/modules/ModuleDetailSheet';
import { AddModuleToProjectDialog } from '@/components/modules/AddModuleToProjectDialog';
import { ModuleSourcesDialog } from '@/components/modules/ModuleSourcesDialog';
import { RegistryBrowseDialog } from '@/components/modules/RegistryBrowseDialog';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { useModuleLibrary, useSyncModuleLibrary } from '@/hooks/useModules';
import { PageHeader } from '@/components/layout/PageHeader';
import { usePageRefresh } from '@/hooks/usePageRefresh';
import { useModuleSources } from '@/hooks/useModuleSources';
import { cn } from '@/lib/utils';
import {
  Search,
  Package,
  RefreshCw,
  Server,
  Shield,
  GitBranch,
  Plus,
  Settings,
  Globe,
  ArrowUpCircle,
  Eye,
  MoreVertical,
  Database,
  Cloud,
  Network,
  CheckCircle2,
  XCircle,
  Clock,
  Loader2,
  Zap,
} from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { ModuleLibrary, ModuleSource } from '@/types';
import { compareBlueprintVersions } from '@/components/catalog/blueprintVersionUtils';
import { getEnginePresentation, resolveModuleEngineType } from '@/lib/module-engine';

// Show 25 modules at a time, click "Show more" to load more.
const INITIAL_MODULES_LIMIT = 25;

const CATEGORY_LABELS: Record<string, { label: string; icon: typeof Package }> = {
  infrastructure: { label: 'Infrastructure', icon: Server },
  infra: { label: 'Infrastructure', icon: Server },
  bnk: { label: 'BNK / F5', icon: Shield },
  security: { label: 'Security', icon: Shield },
  networking: { label: 'Networking', icon: Network },
  storage: { label: 'Storage', icon: Database },
  compute: { label: 'Compute', icon: Server },
  kubernetes: { label: 'Kubernetes', icon: Cloud },
  other: { label: 'Other', icon: Package },
};

const PLATFORM_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: 'eks', label: 'EKS' },
  { value: 'aks', label: 'AKS' },
  { value: 'gke', label: 'GKE' },
  { value: 'roks', label: 'ROKS' },
  { value: 'ocp', label: 'OCP' },
  { value: 'generic_onprem', label: 'On-Prem' },
];

// Sync status helper — small icon, token color.
function getSyncStatusIcon(status: string) {
  switch (status) {
    case 'success':
      return <CheckCircle2 className="h-3.5 w-3.5 text-success" />;
    case 'failed':
      return <XCircle className="h-3.5 w-3.5 text-destructive" />;
    case 'syncing':
      return <Loader2 className="h-3.5 w-3.5 text-info animate-spin" />;
    default:
      return <Clock className="h-3.5 w-3.5 text-muted-foreground" />;
  }
}

interface ModuleRowProps {
  module: ModuleLibrary;
  source: ModuleSource | null;
  onViewDetails: (module: ModuleLibrary) => void;
  onAddToProject: (module: ModuleLibrary) => void;
}

const ModuleRow = memo(function ModuleRow({
  module,
  source,
  onViewDetails,
  onAddToProject,
}: ModuleRowProps) {
  const categoryKey = (module.category || 'other').toLowerCase();
  const cat = CATEGORY_LABELS[categoryKey] || CATEGORY_LABELS.other;
  const engine = getEnginePresentation(
    resolveModuleEngineType(module),
    module.module_type,
  );
  const CategoryIcon = cat.icon;
  const deps = module.dependencies || [];
  const tags = module.tags || [];

  return (
    <tr
      className="border-t border-border cursor-pointer hover:bg-muted/40 transition-colors"
      onClick={() => onViewDetails(module)}
    >
      <td className="px-4 py-3">
        <div className="flex items-center gap-3">
          <CategoryIcon className="h-4 w-4 text-muted-foreground shrink-0" />
          <div>
            <div className="font-medium text-foreground text-sm">{module.name}</div>
            {(module.description || module.category) && (
              <div className="text-xs text-muted-foreground line-clamp-1 mt-0.5">
                {module.description || `${module.category}/${module.name}`}
              </div>
            )}
          </div>
        </div>
      </td>
      <td className="px-4 py-3">
        <Badge variant="outline" className="text-xs">
          {cat.label}
        </Badge>
      </td>
      <td className="px-4 py-3">
        {source ? (
          <div className="flex items-center gap-1.5 min-w-0">
            <GitBranch className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
            <span className="text-xs text-foreground/80 truncate max-w-[160px]">
              {source.name}
            </span>
            {getSyncStatusIcon(source.sync_status)}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </td>
      <td className="px-4 py-3">
        <Badge variant="muted" className="text-xs gap-1">
          <Zap className="h-3 w-3" />
          {engine.label}
        </Badge>
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-mono text-foreground/80">
            v{module.version || '1.0.0'}
          </span>
          {module.is_latest === false && (
            <Badge variant="outline" className="text-xs">
              older
            </Badge>
          )}
          {module.update_available && module.latest_version && (
            <Badge variant="info" className="text-xs gap-1">
              <ArrowUpCircle className="h-3 w-3" />
              {module.latest_version}
            </Badge>
          )}
        </div>
      </td>
      <td className="px-4 py-3">
        {tags.length > 0 ? (
          <div className="flex flex-wrap gap-1 max-w-[180px]">
            {tags.slice(0, 3).map((tag) => (
              <Badge key={tag} variant="outline" className="text-xs">
                {tag}
              </Badge>
            ))}
            {tags.length > 3 && (
              <span className="text-xs text-muted-foreground">+{tags.length - 3}</span>
            )}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </td>
      <td className="px-4 py-3">
        {deps.length > 0 ? (
          <span className="text-xs text-muted-foreground tabular-nums">
            {deps.length} dep{deps.length === 1 ? '' : 's'}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </td>
      <td className="px-4 py-3 text-right">
        <DropdownMenu>
          <DropdownMenuTrigger asChild onClick={(e) => e.stopPropagation()}>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 w-8 p-0"
              aria-label="Module actions"
            >
              <MoreVertical className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              onClick={(e) => {
                e.stopPropagation();
                onViewDetails(module);
              }}
            >
              <Eye className="h-4 w-4 mr-2" />
              View details
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={(e) => {
                e.stopPropagation();
                onAddToProject(module);
              }}
            >
              <Plus className="h-4 w-4 mr-2" />
              Add to project
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </td>
    </tr>
  );
});

export default function Modules() {
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string | undefined>(undefined);
  const [selectedSourceId, setSelectedSourceId] = useState<number | null>(null);
  const [selectedPlatform, setSelectedPlatform] = useState<string | undefined>(undefined);
  const [showAllModules, setShowAllModules] = useState(false);
  // D-033: the catalog holds one row per module version; default to the
  // latest version of each path with an opt-in to see full version history.
  const [showAllVersions, setShowAllVersions] = useState(false);

  // Detail / dialog state
  const [selectedModule, setSelectedModule] = useState<ModuleLibrary | null>(null);
  const [detailSheetOpen, setDetailSheetOpen] = useState(false);
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [sourcesDialogOpen, setSourcesDialogOpen] = useState(false);
  const [registryBrowseDialogOpen, setRegistryBrowseDialogOpen] = useState(false);

  const {
    data: modules,
    isLoading: isLoadingModules,
    isError,
    error,
    refetch,
  } = useModuleLibrary({
    search: searchTerm || undefined,
    category: selectedCategory,
  });
  const syncLibraryMutation = useSyncModuleLibrary();
  const { data: sources, isLoading: isLoadingSources } = useModuleSources();

  // Lookup: source_id → source
  const sourcesMap = useMemo(() => {
    const map: Record<number, ModuleSource> = {};
    (sources || []).forEach((s) => {
      map[s.id] = s;
    });
    return map;
  }, [sources]);

  // Available categories
  const categories = useMemo(() => {
    const set = new Set<string>();
    (modules || []).forEach((m) => {
      if (m.category) set.add(m.category);
    });
    return Array.from(set).sort();
  }, [modules]);

  const olderVersionCount = useMemo(
    () => (modules || []).filter((m) => m.is_latest === false).length,
    [modules],
  );

  const displayModules = useMemo(() => {
    let filtered = modules || [];
    if (!showAllVersions) {
      // is_latest === false marks a superseded version row; undefined (older
      // backends) is treated as latest so nothing disappears.
      filtered = filtered.filter((m) => m.is_latest !== false);
    }
    if (selectedSourceId !== null) {
      filtered = filtered.filter((m) => m.module_source_id === selectedSourceId);
    }
    if (selectedPlatform) {
      filtered = filtered.filter((m) => {
        const compat = m.platform_compatibility;
        if (!compat || compat.declared_any) return true;
        return compat.supported_profiles?.includes(selectedPlatform);
      });
    }
    return filtered.slice().sort((a, b) => {
      // Group by category, then alpha by name.
      const ca = (a.category || 'other').toLowerCase();
      const cb = (b.category || 'other').toLowerCase();
      if (ca !== cb) return ca.localeCompare(cb);
      if (a.name !== b.name) return a.name.localeCompare(b.name);
      // Same module across versions: newest version first (shared semver-tolerant
      // comparator — is_latest alone leaves older versions in arbitrary order).
      return compareBlueprintVersions(b.version || '', a.version || '');
    });
  }, [modules, selectedSourceId, selectedPlatform, showAllVersions]);

  const limited = showAllModules
    ? displayModules
    : displayModules.slice(0, INITIAL_MODULES_LIMIT);
  const hasMore = displayModules.length > INITIAL_MODULES_LIMIT;

  // Sync status roll-up for the KPI strip.
  const syncStats = useMemo(() => {
    const counts = { ok: 0, syncing: 0, failed: 0, unknown: 0 };
    (sources || []).forEach((s) => {
      switch (s.sync_status) {
        case 'success':
          counts.ok++;
          break;
        case 'syncing':
          counts.syncing++;
          break;
        case 'failed':
          counts.failed++;
          break;
        default:
          counts.unknown++;
      }
    });
    return counts;
  }, [sources]);

  const handleViewDetails = useCallback((module: ModuleLibrary) => {
    setSelectedModule(module);
    setDetailSheetOpen(true);
  }, []);

  const handleAddToProject = useCallback((module: ModuleLibrary) => {
    setSelectedModule(module);
    setAddDialogOpen(true);
  }, []);

  const { refresh, isRefreshing } = usePageRefresh();

  const isLoading = isLoadingModules || isLoadingSources;

  const subtitle = isLoading
    ? 'Loading modules…'
    : `${modules?.length || 0} modules across ${sources?.length || 0} source${sources?.length === 1 ? '' : 's'}`;

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <PageHeader
        title="Module Catalog"
        subtitle={subtitle}
        onRefresh={refresh}
        isRefreshing={isRefreshing}
        actions={
          <>
            <Button onClick={() => setSourcesDialogOpen(true)} variant="outline" size="sm">
              <Settings className="h-4 w-4 mr-1.5" />
              Sources
            </Button>
            <Button onClick={() => setRegistryBrowseDialogOpen(true)} variant="outline" size="sm">
              <Globe className="h-4 w-4 mr-1.5" />
              Browse registry
            </Button>
            <Button
              onClick={() => syncLibraryMutation.mutate(false)}
              disabled={syncLibraryMutation.isPending}
              variant="outline"
              size="sm"
            >
              <RefreshCw
                className={cn('h-4 w-4 mr-1.5', syncLibraryMutation.isPending && 'animate-spin')}
              />
              Sync all
            </Button>
          </>
        }
      />

      {/* KPI strip */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: 'Modules', value: modules?.length || 0, tone: 'foreground' as const },
          { label: 'Sources OK', value: syncStats.ok, tone: 'success' as const },
          { label: 'Syncing', value: syncStats.syncing, tone: 'info' as const },
          { label: 'Sync failed', value: syncStats.failed, tone: 'destructive' as const },
        ].map((tile) => {
          const toneText =
            tile.tone === 'success'
              ? 'text-success'
              : tile.tone === 'info'
              ? 'text-info'
              : tile.tone === 'destructive'
              ? 'text-destructive'
              : 'text-foreground';
          return (
            <div key={tile.label} className="rounded-lg border border-border bg-card px-4 py-3">
              <div className="text-xs text-muted-foreground">{tile.label}</div>
              <div className={cn('text-2xl font-bold mt-1 tabular-nums', toneText)}>
                {tile.value}
              </div>
            </div>
          );
        })}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-80 max-w-full">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search by name, category, or provider…"
            aria-label="Search modules"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-9 h-9"
          />
        </div>

        {(sources?.length || 0) > 0 && (
          <Select
            value={selectedSourceId !== null ? String(selectedSourceId) : 'all'}
            onValueChange={(val) =>
              setSelectedSourceId(val === 'all' ? null : Number(val))
            }
          >
            <SelectTrigger className="h-9 w-[180px]">
              <SelectValue placeholder="All sources" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All sources</SelectItem>
              {(sources || []).map((s) => (
                <SelectItem key={s.id} value={String(s.id)}>
                  {s.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        {categories.length > 0 && (
          <Select
            value={selectedCategory || 'all'}
            onValueChange={(val) =>
              setSelectedCategory(val === 'all' ? undefined : val)
            }
          >
            <SelectTrigger className="h-9 w-[180px]">
              <SelectValue placeholder="All categories" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All categories</SelectItem>
              {categories.map((c) => (
                <SelectItem key={c} value={c}>
                  {CATEGORY_LABELS[c.toLowerCase()]?.label || c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        <Select
          value={selectedPlatform || 'all'}
          onValueChange={(val) => setSelectedPlatform(val === 'all' ? undefined : val)}
        >
          <SelectTrigger className="h-9 w-[160px]">
            <SelectValue placeholder="All platforms" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All platforms</SelectItem>
            {PLATFORM_FILTER_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {olderVersionCount > 0 && (
          <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer select-none">
            <input
              type="checkbox"
              className="h-4 w-4 accent-primary"
              checked={showAllVersions}
              onChange={(e) => setShowAllVersions(e.target.checked)}
            />
            All versions ({olderVersionCount} older)
          </label>
        )}
      </div>

      {/* Table */}
      {isError ? (
        <ErrorState error={error} onRetry={refetch} />
      ) : isLoading ? (
        <SectionCard compact>
          <div className="space-y-2">
            {[...Array(8)].map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-md" />
            ))}
          </div>
        </SectionCard>
      ) : displayModules.length === 0 ? (
        <EmptyState
          icon={Package}
          title="No modules found"
          description={
            searchTerm
              ? `No modules match "${searchTerm}"`
              : 'Add a module source or browse the registry to populate the catalog.'
          }
          action={{
            label: 'Browse registry',
            onClick: () => setRegistryBrowseDialogOpen(true),
            icon: <Globe className="h-4 w-4 mr-1.5" />,
          }}
        />
      ) : (
        <SectionCard
          title={`${displayModules.length} ${displayModules.length === 1 ? 'module' : 'modules'}`}
          compact
        >
          <div className="overflow-x-auto -mx-4">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs uppercase tracking-wider text-muted-foreground">
                  <th scope="col" className="text-left font-medium px-4 py-2">Module</th>
                  <th scope="col" className="text-left font-medium px-4 py-2">Category</th>
                  <th scope="col" className="text-left font-medium px-4 py-2">Source</th>
                  <th scope="col" className="text-left font-medium px-4 py-2">Engine</th>
                  <th scope="col" className="text-left font-medium px-4 py-2">Version</th>
                  <th scope="col" className="text-left font-medium px-4 py-2">Tags</th>
                  <th scope="col" className="text-left font-medium px-4 py-2">Deps</th>
                  <th scope="col" className="text-right font-medium px-4 py-2 w-16">{/* actions */}</th>
                </tr>
              </thead>
              <tbody>
                {limited.map((module) => (
                  <ModuleRow
                    key={`${module.module_source_id ?? 0}-${module.name}-${module.version ?? ''}`}
                    module={module}
                    source={module.module_source_id ? sourcesMap[module.module_source_id] || null : null}
                    onViewDetails={handleViewDetails}
                    onAddToProject={handleAddToProject}
                  />
                ))}
              </tbody>
            </table>
          </div>

          {!showAllModules && hasMore && (
            <div className="mt-4 flex justify-center">
              <Button variant="outline" size="sm" onClick={() => setShowAllModules(true)}>
                Show {displayModules.length - INITIAL_MODULES_LIMIT} more
              </Button>
            </div>
          )}
          {showAllModules && hasMore && (
            <div className="mt-4 flex justify-center">
              <Button variant="ghost" size="sm" onClick={() => setShowAllModules(false)}>
                Show less
              </Button>
            </div>
          )}
        </SectionCard>
      )}

      {/* Dialogs */}
      {selectedModule && (
        <ModuleDetailSheet
          module={selectedModule}
          open={detailSheetOpen}
          onOpenChange={setDetailSheetOpen}
          onAddToProject={() => {
            setDetailSheetOpen(false);
            setAddDialogOpen(true);
          }}
        />
      )}
      {selectedModule && (
        <AddModuleToProjectDialog
          module={selectedModule}
          open={addDialogOpen}
          onOpenChange={setAddDialogOpen}
        />
      )}
      <ModuleSourcesDialog
        open={sourcesDialogOpen}
        onOpenChange={setSourcesDialogOpen}
      />
      <RegistryBrowseDialog
        open={registryBrowseDialogOpen}
        onOpenChange={setRegistryBrowseDialogOpen}
      />
    </div>
  );
}
