import { useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { blueprintSourceSchema, type BlueprintSourceFormData } from '@/schemas';
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  Download,
  Edit2,
  Eye,
  EyeOff,
  Filter,
  GitBranch,
  Layers,
  Loader2,
  Plus,
  RefreshCw,
  Rocket,
  Search,
  Settings,
  Tag,
  Trash2,
  XCircle,
} from 'lucide-react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { SectionCard } from '@/components/ui/section-card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { ImportedBlueprintDeployDialog } from '@/components/stacks/ImportedBlueprintDeployDialog';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import {
  useBlueprintReleases,
  useBlueprintSources,
  useCreateBlueprintSource,
  useDeleteBlueprintRelease,
  useDeleteBlueprintSource,
  useImportBlueprintRelease,
  useSetBlueprintReleaseVisibility,
  useSyncBlueprintSource,
  useUnimportBlueprintRelease,
  useUpdateBlueprintSource,
} from '@/hooks/useBlueprintCatalog';
import { notify } from '@/lib/notify';
import { queryKeys } from '@/lib/queryKeys';
import { formatAge } from '@/lib/time-utils';
import { cn } from '@/lib/utils';
import { sortVersionsDesc } from './blueprintVersionUtils';
import type { BlueprintRelease, BlueprintSource, BlueprintSourceCreate, BlueprintSourceUpdate } from '@/types';

const PLATFORM_OPTIONS = [
  { value: 'all', label: 'All Platforms' },
  { value: 'aws', label: 'AWS' },
  { value: 'azure', label: 'Azure' },
  { value: 'gcp', label: 'GCP' },
  { value: 'ibm', label: 'IBM' },
  { value: 'kubernetes', label: 'Kubernetes' },
  { value: 'docker', label: 'Docker / Local' },
  { value: 'other', label: 'Other' },
];

const VALIDATION_OPTIONS = [
  { value: 'all', label: 'Any Validation' },
  { value: 'valid', label: 'Valid' },
  { value: 'invalid', label: 'Invalid' },
  { value: 'unknown', label: 'Unknown' },
];

const RELEASE_STATE_OPTIONS = [
  { value: 'all', label: 'Any State' },
  { value: 'discovered', label: 'Discovered' },
  { value: 'imported', label: 'Imported' },
  { value: 'approved', label: 'Approved' },
  { value: 'deprecated', label: 'Deprecated' },
];

/** A release counts as "removed from deployable catalog" when discovered + removed reason. */
function isRemovedRelease(release: BlueprintRelease): boolean {
  return (
    release.release_state === 'discovered' &&
    release.state_reason === 'Removed from deployable catalog'
  );
}

function statusIcon(status: string) {
  switch (status) {
    case 'success':
      return <CheckCircle2 className="h-4 w-4 text-success" />;
    case 'failed':
      return <XCircle className="h-4 w-4 text-destructive" />;
    case 'syncing':
      return <Loader2 className="h-4 w-4 animate-spin text-info" />;
    default:
      return <Clock className="h-4 w-4 text-muted-foreground" />;
  }
}

function statusBadge(status: string) {
  const variants: Record<string, 'success' | 'destructive' | 'info' | 'muted' | 'outline'> = {
    success: 'success',
    failed: 'destructive',
    syncing: 'info',
    pending: 'muted',
  };
  return <Badge variant={variants[status] || 'outline'}>{status}</Badge>;
}

function sourceTypeIcon(sourceType: BlueprintSource['source_type']) {
  if (sourceType === 'git') {
    return <GitBranch className="h-4 w-4 text-info" />;
  }
  return <Layers className="h-4 w-4 text-muted-foreground" />;
}

function releaseStateBadge(state: string) {
  const variant: 'success' | 'muted' | 'destructive' | 'info' | 'outline' =
    state === 'approved'
      ? 'success'
      : state === 'deprecated'
        ? 'destructive'
        : state === 'imported'
          ? 'info'
          : 'muted';
  return <Badge variant={variant}>{state}</Badge>;
}

/** A group of releases sharing the same blueprint_id × blueprint_source_id. */
interface BlueprintGroup {
  key: string;
  blueprint_id: string;
  blueprint_source_id: number;
  /** All versions sorted newest-first (after "removed" filter applied upstream). */
  versions: BlueprintRelease[];
  /** The best candidate to show as the card's primary face. */
  latest: BlueprintRelease;
}

export default function BlueprintCatalogPanel() {
  const [search, setSearch] = useState('');
  const [platformFilter, setPlatformFilter] = useState('all');
  const [sourceFilter, setSourceFilter] = useState('all');
  const [validationFilter, setValidationFilter] = useState('all');
  const [releaseStateFilter, setReleaseStateFilter] = useState('all');
  const [showRemoved, setShowRemoved] = useState(false);
  const [latestOnly, setLatestOnly] = useState(false);
  const [isSourcesOpen, setIsSourcesOpen] = useState(false);
  const [sourceToDelete, setSourceToDelete] = useState<{ id: number; name: string } | null>(null);
  const [releaseToDelete, setReleaseToDelete] = useState<BlueprintRelease | null>(null);
  const [isSyncingAll, setIsSyncingAll] = useState(false);
  const [deployReleaseSlug, setDeployReleaseSlug] = useState<string>('');
  const [deployDialogOpen, setDeployDialogOpen] = useState(false);

  const { data: sources, isLoading: sourcesLoading } = useBlueprintSources();
  const { data: releases, isLoading: releasesLoading } = useBlueprintReleases();
  const createMutation = useCreateBlueprintSource();
  const updateMutation = useUpdateBlueprintSource();
  const syncMutation = useSyncBlueprintSource();
  const deleteSourceMutation = useDeleteBlueprintSource();
  const importMutation = useImportBlueprintRelease();
  const deleteReleaseMutation = useDeleteBlueprintRelease();
  const unimportMutation = useUnimportBlueprintRelease();
  const visibilityMutation = useSetBlueprintReleaseVisibility();
  const [releaseToUnimport, setReleaseToUnimport] = useState<BlueprintRelease | null>(null);
  const queryClient = useQueryClient();

  const sourceMap = useMemo(() => {
    return new Map((sources || []).map((source) => [source.id, source]));
  }, [sources]);

  // Step 1: Apply search + field filters (same logic as before, preserved exactly).
  const visibleReleases = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();

    return (releases || []).filter((release) => {
      const source = sourceMap.get(release.blueprint_source_id);
      const matchesSearch = !normalizedSearch || [
        release.blueprint_name,
        release.blueprint_id,
        release.blueprint_description,
        release.source_name,
        release.source_path,
        ...(release.tags || []),
      ].some((value) => String(value || '').toLowerCase().includes(normalizedSearch));

      const releaseProvider = (release.cloud_provider || 'other').toLowerCase();
      const matchesPlatform = platformFilter === 'all' || releaseProvider === platformFilter;
      const matchesSource = sourceFilter === 'all' || String(release.blueprint_source_id) === sourceFilter;
      const matchesValidation = validationFilter === 'all' || release.validation_state === validationFilter;
      const matchesReleaseState = releaseStateFilter === 'all' || release.release_state === releaseStateFilter;

      return matchesSearch && matchesPlatform && matchesSource && matchesValidation && matchesReleaseState && !!source;
    });
  }, [releases, search, platformFilter, sourceFilter, validationFilter, releaseStateFilter, sourceMap]);

  // Step 2: Group by blueprint_id × blueprint_source_id, sorted newest-first per group.
  //         D-033 (#438): removed (unimported) releases stay visible as version
  //         HISTORY inside the row's disclosure — superseding a version must not
  //         erase its history — but they never become the row's face. A blueprint
  //         whose EVERY release is removed stays hidden unless showRemoved is on.
  const blueprintGroups = useMemo((): BlueprintGroup[] => {
    const grouped = new Map<string, BlueprintRelease[]>();

    for (const release of visibleReleases) {
      const key = `${release.blueprint_source_id}:${release.blueprint_id}`;
      const bucket = grouped.get(key) ?? [];
      bucket.push(release);
      grouped.set(key, bucket);
    }

    const groups: BlueprintGroup[] = [];
    for (const [key, bucket] of grouped) {
      const nonRemoved = bucket.filter((r) => !isRemovedRelease(r));
      if (!showRemoved && nonRemoved.length === 0) continue;
      const sorted = sortVersionsDesc(bucket, (r) => r.blueprint_version);
      const latest =
        nonRemoved.length > 0
          ? sortVersionsDesc(nonRemoved, (r) => r.blueprint_version)[0]
          : sorted[0];
      groups.push({
        key,
        blueprint_id: sorted[0].blueprint_id,
        blueprint_source_id: sorted[0].blueprint_source_id,
        versions: sorted,
        latest,
      });
    }

    return groups;
  }, [visibleReleases, showRemoved]);

  const summary = useMemo(() => {
    const total = blueprintGroups.length;
    const valid = blueprintGroups.filter((g) => g.latest.validation_state === 'valid').length;
    const imported = blueprintGroups.filter((g) =>
      g.versions.some((r) => r.release_state === 'imported')
    ).length;
    const approved = blueprintGroups.filter((g) =>
      g.versions.some((r) => r.release_state === 'approved')
    ).length;
    return { total, valid, imported, approved };
  }, [blueprintGroups]);

  const handleDeleteSource = async () => {
    if (!sourceToDelete) {
      return;
    }

    await deleteSourceMutation.mutateAsync(sourceToDelete.id);
    setSourceToDelete(null);
  };

  const handleDeleteRelease = async () => {
    if (!releaseToDelete) {
      return;
    }

    await deleteReleaseMutation.mutateAsync(releaseToDelete.id);
    setReleaseToDelete(null);
  };

  const handleUnimportRelease = async () => {
    if (!releaseToUnimport) {
      return;
    }

    await unimportMutation.mutateAsync(releaseToUnimport.id);
    setReleaseToUnimport(null);
  };

  const handleSyncAll = async () => {
    const syncableSources = (sources || []).filter(
      (source) => source.source_type === 'git' && source.is_active
    );
    if (syncableSources.length === 0) {
      notify.info('No active git blueprint sources to sync', undefined, { category: 'system' });
      return;
    }

    setIsSyncingAll(true);
    try {
      for (const source of syncableSources) {
        await syncMutation.mutateAsync(source.id);
      }
      notify.success(`Synced ${syncableSources.length} blueprint source${syncableSources.length === 1 ? '' : 's'}`, undefined, { category: 'system' });
    } finally {
      setIsSyncingAll(false);
    }
  };

  const handleDeployRelease = (release: BlueprintRelease) => {
    setDeployReleaseSlug(`release-${release.id}`);
    setDeployDialogOpen(true);
  };

  return (
    <div className="space-y-6">
      <SectionCard>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between mb-5">
          <div>
            <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground/70">
              Blueprints
            </h3>
            <p className="text-sm text-muted-foreground mt-1">
              Browse discovered blueprints first, then manage sources from a dedicated dialog.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2 shrink-0">
            <Button variant="outline" size="sm" onClick={() => setIsSourcesOpen(true)}>
              <Settings className="mr-1.5 h-4 w-4" />
              Sources
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleSyncAll}
              disabled={isSyncingAll || syncMutation.isPending || !sources?.length}
            >
              {isSyncingAll ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="mr-1.5 h-4 w-4" />
              )}
              Sync all
            </Button>
          </div>
        </div>

        <div className="space-y-4">
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1.6fr)_repeat(4,minmax(0,1fr))]">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search blueprints, tags, source, or description"
                className="pl-9"
              />
            </div>

            <Select value={platformFilter} onValueChange={setPlatformFilter}>
              <SelectTrigger>
                <SelectValue placeholder="Platform" />
              </SelectTrigger>
              <SelectContent>
                {PLATFORM_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={sourceFilter} onValueChange={setSourceFilter}>
              <SelectTrigger>
                <SelectValue placeholder="Source" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Sources</SelectItem>
                {(sources || []).map((source) => (
                  <SelectItem key={source.id} value={String(source.id)}>
                    {source.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={validationFilter} onValueChange={setValidationFilter}>
              <SelectTrigger>
                <SelectValue placeholder="Validation" />
              </SelectTrigger>
              <SelectContent>
                {VALIDATION_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={releaseStateFilter} onValueChange={setReleaseStateFilter}>
              <SelectTrigger>
                <SelectValue placeholder="State" />
              </SelectTrigger>
              <SelectContent>
                {RELEASE_STATE_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Filter chips */}
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setShowRemoved((v) => !v)}
              className={cn(
                'inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm transition-colors',
                showRemoved
                  ? 'bg-foreground text-background border-foreground'
                  : 'bg-card text-muted-foreground border-border hover:text-foreground',
              )}
              aria-pressed={showRemoved}
            >
              {showRemoved ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
              {showRemoved ? 'Removed visible' : 'Show removed'}
            </button>

            <button
              type="button"
              onClick={() => setLatestOnly((v) => !v)}
              className={cn(
                'inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm transition-colors',
                latestOnly
                  ? 'bg-foreground text-background border-foreground'
                  : 'bg-card text-muted-foreground border-border hover:text-foreground',
              )}
              aria-pressed={latestOnly}
            >
              <Tag className="h-3.5 w-3.5" />
              Latest only
            </button>
          </div>
        </div>

        <div className="mt-6 space-y-6">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <SummaryTile label="Visible Blueprints" value={summary.total} icon={Layers} />
            <SummaryTile label="Valid" value={summary.valid} icon={CheckCircle2} />
            <SummaryTile label="Imported" value={summary.imported} icon={Download} />
            <SummaryTile label="Approved" value={summary.approved} icon={Tag} />
          </div>

          {releasesLoading || sourcesLoading ? (
            <div className="flex justify-center p-12">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : blueprintGroups.length === 0 ? (
            <Alert>
              <Filter className="h-4 w-4" />
              <AlertDescription>
                No blueprints match the current filters. Try clearing a filter or sync your sources again.
              </AlertDescription>
            </Alert>
          ) : (
            <BlueprintTable
              groups={blueprintGroups}
              sourceMap={sourceMap}
              latestOnly={latestOnly}
              onDeploy={handleDeployRelease}
              onImport={(release) =>
                importMutation.mutate({
                  releaseId: release.id,
                  stateReason: 'Imported from Blueprint Catalog UI',
                })
              }
              onDiscard={(release) => setReleaseToDelete(release)}
              onRemove={(release) => setReleaseToUnimport(release)}
              onSetVisibility={(release, isVisible) =>
                visibilityMutation.mutate({ releaseId: release.id, isVisible })
              }
              importPending={importMutation.isPending}
              discardPending={deleteReleaseMutation.isPending}
              removePending={unimportMutation.isPending}
              visibilityPending={visibilityMutation.isPending}
            />
          )}
        </div>
      </SectionCard>

      <BlueprintSourcesDialog
        open={isSourcesOpen}
        onOpenChange={setIsSourcesOpen}
        sources={sources || []}
        releasesBySource={new Map((sources || []).map((source) => [source.id, source.release_count]))}
        syncMutation={syncMutation}
        createMutation={createMutation}
        updateMutation={updateMutation}
        deleteSourceMutation={deleteSourceMutation}
        onDelete={(source) => setSourceToDelete(source)}
      />

      {deployReleaseSlug ? (
        <ImportedBlueprintDeployDialog
          slug={deployReleaseSlug}
          open={deployDialogOpen}
          onOpenChange={setDeployDialogOpen}
          onSuccess={() => {
            // Refresh the catalog so each card's deployed_project_count
            // reflects the new project, and refresh project lists so the
            // new project appears elsewhere in the app.
            queryClient.invalidateQueries({ queryKey: queryKeys.blueprintCatalog.releases() });
            queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
          }}
        />
      ) : null}

      <AlertDialog open={sourceToDelete !== null} onOpenChange={(open) => !open && setSourceToDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete blueprint source?</AlertDialogTitle>
            <AlertDialogDescription>
              {sourceToDelete
                ? `Remove blueprint source "${sourceToDelete.name}" from the catalog. Imported releases remain until removed separately.`
                : 'Remove this blueprint source from the catalog.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteSourceMutation.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteSource} disabled={deleteSourceMutation.isPending}>
              {deleteSourceMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Delete Source
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={releaseToDelete !== null} onOpenChange={(open) => !open && setReleaseToDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard discovered release?</AlertDialogTitle>
            <AlertDialogDescription>
              {releaseToDelete
                ? `${releaseToDelete.blueprint_name} ${releaseToDelete.blueprint_version} will be removed from the catalog. It will reappear on the next sync if it still exists in the source.`
                : 'This release will be removed from the catalog.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteReleaseMutation.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteRelease} disabled={deleteReleaseMutation.isPending}>
              {deleteReleaseMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Discard
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={releaseToUnimport !== null} onOpenChange={(open) => !open && setReleaseToUnimport(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove from deployable catalog?</AlertDialogTitle>
            <AlertDialogDescription>
              {releaseToUnimport
                ? `${releaseToUnimport.blueprint_name} ${releaseToUnimport.blueprint_version} will move back to the discovered state and will no longer be deployable. The catalog entry stays — re-import it later to make it deployable again. Blocked if any project was created from this release.`
                : 'This release will move back to the discovered state.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={unimportMutation.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleUnimportRelease} disabled={unimportMutation.isPending}>
              {unimportMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function SummaryTile({ label, value, icon: Icon }: { label: string; value: number; icon: typeof Layers }) {
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold">{value}</p>
        </div>
        <div className="rounded-md bg-muted p-2">
          <Icon className="h-4 w-4 text-muted-foreground" />
        </div>
      </div>
    </div>
  );
}

interface BlueprintTableProps {
  groups: BlueprintGroup[];
  sourceMap: Map<number, BlueprintSource>;
  latestOnly: boolean;
  onDeploy: (release: BlueprintRelease) => void;
  onImport: (release: BlueprintRelease) => void;
  onDiscard: (release: BlueprintRelease) => void;
  onRemove: (release: BlueprintRelease) => void;
  onSetVisibility: (release: BlueprintRelease, isVisible: boolean) => void;
  importPending: boolean;
  discardPending: boolean;
  removePending: boolean;
  visibilityPending: boolean;
}

function BlueprintTable({
  groups,
  sourceMap,
  latestOnly,
  onDeploy,
  onImport,
  onDiscard,
  onRemove,
  onSetVisibility,
  importPending,
  discardPending,
  removePending,
  visibilityPending,
}: BlueprintTableProps) {
  // Per-group expanded state for the "other versions" disclosure.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggle = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <div className="border border-border rounded-md overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
              Blueprint
            </TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
              Provider
            </TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
              Source
            </TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
              Version
            </TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
              State
            </TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
              Visible
            </TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-muted-foreground text-right w-48">
              {/* actions */}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {groups.map((group) => {
            const source = sourceMap.get(group.blueprint_source_id) || null;
            const latest = group.latest;
            const otherVersions = latestOnly
              ? []
              : group.versions.filter((r) => r.id !== latest.id);
            const isExpanded = expanded.has(group.key);
            const canDeploy =
              latest.release_state === 'imported' || latest.release_state === 'approved';
            const isImported =
              latest.release_state === 'imported' || latest.release_state === 'approved';
            const isDiscoveredOnly = latest.release_state === 'discovered';
            const deployedCount = latest.deployed_project_count ?? 0;
            const removeDisabled = removePending || deployedCount > 0;
            const validationErrors = latest.validation_errors?.length ?? 0;
            const isBuiltin = source?.source_type === 'builtin';

            return (
              <>
                <TableRow key={group.key}>
                  <TableCell>
                    <div className="flex flex-col">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-foreground">
                          {latest.blueprint_name}
                        </span>
                        {isBuiltin ? (
                          <Badge variant="outline" className="border-info/60 bg-info/10 text-info text-[10px]">
                            Built-in
                          </Badge>
                        ) : null}
                        {latest.is_featured ? (
                          <Badge variant="outline" className="border-warning/60 bg-warning/10 text-warning text-[10px]">
                            Featured
                          </Badge>
                        ) : null}
                        {validationErrors > 0 && (
                          <TooltipProvider>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span>
                                  <XCircle className="h-3.5 w-3.5 text-destructive" />
                                </span>
                              </TooltipTrigger>
                              <TooltipContent>
                                {validationErrors} validation issue
                                {validationErrors === 1 ? '' : 's'}
                              </TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        )}
                      </div>
                      {latest.blueprint_description && (
                        <span className="text-xs text-muted-foreground line-clamp-1 mt-0.5">
                          {latest.blueprint_description}
                        </span>
                      )}
                      {otherVersions.length > 0 && (
                        <button
                          type="button"
                          onClick={() => toggle(group.key)}
                          className="mt-1 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                          aria-expanded={isExpanded}
                        >
                          {isExpanded ? (
                            <ChevronUp className="h-3 w-3" />
                          ) : (
                            <ChevronDown className="h-3 w-3" />
                          )}
                          {group.versions.length - 1} other version
                          {group.versions.length - 1 === 1 ? '' : 's'}
                        </button>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className="text-xs">
                      {latest.cloud_provider || 'other'}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {source ? (
                      <span className="text-xs text-muted-foreground">{source.name}</span>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <span className="font-mono text-xs text-foreground/80">
                      v{latest.blueprint_version}
                    </span>
                  </TableCell>
                  <TableCell>{releaseStateBadge(latest.release_state)}</TableCell>
                  <TableCell>
                    {isImported ? (
                      <Switch
                        checked={latest.is_active}
                        onCheckedChange={(checked) => onSetVisibility(latest, checked)}
                        disabled={visibilityPending}
                        aria-label="Visible on Blueprints page"
                      />
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      <Button
                        size="sm"
                        onClick={() => onDeploy(latest)}
                        disabled={!canDeploy}
                        title={!canDeploy ? 'Import this release first to deploy it' : undefined}
                      >
                        <Rocket className="mr-1.5 h-3.5 w-3.5" />
                        Deploy
                      </Button>
                      {!isBuiltin && isDiscoveredOnly && latest.validation_state === 'valid' && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => onImport(latest)}
                          disabled={importPending}
                        >
                          {importPending ? (
                            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Download className="mr-1.5 h-3.5 w-3.5" />
                          )}
                          Import
                        </Button>
                      )}
                      {!isBuiltin && isImported ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => onRemove(latest)}
                          disabled={removeDisabled}
                          title={
                            deployedCount > 0
                              ? `Cannot remove: ${deployedCount} project${
                                  deployedCount === 1 ? '' : 's'
                                } deployed from this blueprint`
                              : undefined
                          }
                          className="h-8 w-8 p-0"
                          aria-label="Remove from deployable catalog"
                        >
                          {removePending ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Trash2 className="h-3.5 w-3.5 text-destructive" />
                          )}
                        </Button>
                      ) : !isBuiltin ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => onDiscard(latest)}
                          disabled={discardPending}
                          className="h-8 w-8 p-0"
                          aria-label="Discard discovered release"
                        >
                          <Trash2 className="h-3.5 w-3.5 text-destructive" />
                        </Button>
                      ) : null}
                    </div>
                  </TableCell>
                </TableRow>
                {isExpanded &&
                  otherVersions.map((release) => {
                    const rIsImported =
                      release.release_state === 'imported' ||
                      release.release_state === 'approved';
                    const rIsDiscoveredOnly = release.release_state === 'discovered';
                    return (
                      <TableRow key={release.id} className="bg-muted/30">
                        <TableCell>
                          <span className="ml-6 text-sm text-muted-foreground">
                            ↳ {release.blueprint_name}
                          </span>
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline" className="text-[10px]">
                            {release.cloud_provider || 'other'}
                          </Badge>
                        </TableCell>
                        <TableCell />
                        <TableCell>
                          <span className="font-mono text-xs text-muted-foreground">
                            v{release.blueprint_version}
                          </span>
                        </TableCell>
                        <TableCell>{releaseStateBadge(release.release_state)}</TableCell>
                        <TableCell>
                          {rIsImported ? (
                            <Switch
                              checked={release.is_active}
                              onCheckedChange={(checked) => onSetVisibility(release, checked)}
                              disabled={visibilityPending}
                              aria-label="Visible on Blueprints page"
                            />
                          ) : (
                            <span className="text-xs text-muted-foreground">—</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1">
                            {rIsDiscoveredOnly && release.validation_state === 'valid' && (
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => onImport(release)}
                                disabled={importPending}
                              >
                                {importPending ? (
                                  <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                                ) : (
                                  <Download className="mr-1.5 h-3 w-3" />
                                )}
                                Import
                              </Button>
                            )}
                            {rIsImported ? (
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => onRemove(release)}
                                disabled={removePending}
                                className="h-7 w-7 p-0"
                                aria-label="Remove"
                              >
                                <Trash2 className="h-3 w-3 text-destructive" />
                              </Button>
                            ) : (
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => onDiscard(release)}
                                disabled={discardPending}
                                className="h-7 w-7 p-0"
                                aria-label="Discard"
                              >
                                <Trash2 className="h-3 w-3 text-destructive" />
                              </Button>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
              </>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

interface BlueprintSourcesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sources: BlueprintSource[];
  releasesBySource: Map<number, number>;
  syncMutation: ReturnType<typeof useSyncBlueprintSource>;
  createMutation: ReturnType<typeof useCreateBlueprintSource>;
  updateMutation: ReturnType<typeof useUpdateBlueprintSource>;
  deleteSourceMutation: ReturnType<typeof useDeleteBlueprintSource>;
  onDelete: (source: { id: number; name: string }) => void;
}

function BlueprintSourcesDialog({
  open,
  onOpenChange,
  sources,
  releasesBySource,
  syncMutation,
  createMutation,
  updateMutation,
  deleteSourceMutation,
  onDelete,
}: BlueprintSourcesDialogProps) {
  const [editingSource, setEditingSource] = useState<BlueprintSource | null>(null);
  const [isEditorOpen, setIsEditorOpen] = useState(false);

  const handleAdd = () => {
    setEditingSource(null);
    setIsEditorOpen(true);
  };

  const handleEdit = (source: BlueprintSource) => {
    setEditingSource(source);
    setIsEditorOpen(true);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl">
        <DialogHeader>
          <DialogTitle>Blueprint Sources</DialogTitle>
          <DialogDescription>
            Manage the Git repositories that feed the Blueprint Catalog. Set one as default and sync them independently when needed.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {sources.length === 0 ? (
            <Alert>
              <AlertDescription>No blueprint sources configured yet.</AlertDescription>
            </Alert>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>URL</TableHead>
                  <TableHead>Releases</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Last Synced</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sources.map((source) => (
                  <TableRow key={source.id}>
                    <TableCell className="font-medium">
                      <div className="flex items-center gap-2">
                        {sourceTypeIcon(source.source_type)}
                        <span>{source.name}</span>
                        {source.is_default ? <Badge variant="muted">Default</Badge> : null}
                      </div>
                    </TableCell>
                    <TableCell className="max-w-xs truncate text-sm text-muted-foreground">{source.url}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{releasesBySource.get(source.id) || 0}</Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        {statusIcon(source.sync_status)}
                        {statusBadge(source.sync_status)}
                      </div>
                      {source.sync_status === 'success' && source.sync_error === 'No forge-blueprint.json manifests found' ? (
                        <p className="mt-1 text-xs text-warning">Synced successfully, but no forge-blueprint.json manifests were found.</p>
                      ) : null}
                      {source.sync_error ? (
                        <p className="mt-1 text-xs text-destructive line-clamp-2">{source.sync_error}</p>
                      ) : null}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {source.last_synced_at ? formatAge(source.last_synced_at) : 'Never'}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => syncMutation.mutate(source.id)}
                                disabled={syncMutation.isPending || source.sync_status === 'syncing'}
                                aria-label={`Sync ${source.name}`}
                              >
                                <RefreshCw className={cn('h-4 w-4', syncMutation.isPending && 'animate-spin')} />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent side="left">
                              <p className="text-xs">
                                Sync {source.name}
                                {source.last_synced_at ? <span className="text-muted-foreground ml-1">(last: {formatAge(source.last_synced_at)})</span> : null}
                              </p>
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                        {source.source_type !== 'builtin' ? (
                          <>
                            <Button variant="ghost" size="sm" onClick={() => handleEdit(source)}>
                              <Edit2 className="h-4 w-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => onDelete({ id: source.id, name: source.name })}
                              disabled={deleteSourceMutation.isPending}
                              aria-label="Delete Source"
                            >
                              <Trash2 className="h-4 w-4 text-destructive" />
                            </Button>
                          </>
                        ) : null}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          <div className="flex justify-end">
            <Button variant="outline" size="sm" onClick={handleAdd}>
              <Plus className="mr-1.5 h-4 w-4" />
              Add Blueprint Source
            </Button>
          </div>
        </div>

        {isEditorOpen ? (
          <AddEditBlueprintSourceDialog
            open={isEditorOpen}
            onOpenChange={setIsEditorOpen}
            source={editingSource}
            onSubmit={(data) => {
              if (editingSource) {
                updateMutation.mutate(
                  { sourceId: editingSource.id, data },
                  {
                    onSuccess: () => {
                      setIsEditorOpen(false);
                      setEditingSource(null);
                    },
                  }
                );
              } else {
                createMutation.mutate(data as BlueprintSourceCreate, {
                  onSuccess: () => {
                    setIsEditorOpen(false);
                    setEditingSource(null);
                  },
                });
              }
            }}
            isSubmitting={createMutation.isPending || updateMutation.isPending}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

interface AddEditBlueprintSourceDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source?: BlueprintSource | null;
  onSubmit: (data: BlueprintSourceCreate | BlueprintSourceUpdate) => void;
  isSubmitting: boolean;
}

function AddEditBlueprintSourceDialog({ open, onOpenChange, source, onSubmit, isSubmitting }: AddEditBlueprintSourceDialogProps) {
  const form = useForm<BlueprintSourceFormData>({
    resolver: zodResolver(blueprintSourceSchema),
    defaultValues: {
      name: '',
      source_type: 'git',
      url: '',
      branch: 'main',
      git_ref: '',
      is_active: true,
      description: '',
      make_default: false,
    },
  });

  useEffect(() => {
    form.reset({
      name: source?.name || '',
      source_type: source?.source_type || 'git',
      url: source?.url || '',
      branch: source?.branch || 'main',
      git_ref: source?.git_ref || '',
      is_active: source?.is_active ?? true,
      description: source?.description || '',
      make_default: source?.is_default || false,
    });
  }, [source, open, form]);

  const handleValid = (values: BlueprintSourceFormData) => {
    onSubmit(values);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleValid)} noValidate>
            <DialogHeader>
              <DialogTitle>{source ? 'Edit Blueprint Source' : 'Add Blueprint Source'}</DialogTitle>
              <DialogDescription>
                Register a Git repository that contains BNK `forge-blueprint.json` manifests.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4 py-4">
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="url"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Repository URL</FormLabel>
                    <FormControl>
                      <Input placeholder="https://github.com/org/repo" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <div className="grid grid-cols-2 gap-4">
                <FormField
                  control={form.control}
                  name="branch"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Branch</FormLabel>
                      <FormControl>
                        <Input {...field} value={field.value ?? ''} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="git_ref"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Git Ref</FormLabel>
                      <FormControl>
                        <Input {...field} value={field.value ?? ''} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
              <FormField
                control={form.control}
                name="description"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Description</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value ?? ''} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="make_default"
                render={({ field }) => (
                  <FormItem className="flex items-center space-x-2 space-y-0">
                    <FormControl>
                      <Checkbox
                        checked={!!field.value}
                        onCheckedChange={(checked) => field.onChange(checked === true)}
                      />
                    </FormControl>
                    <FormLabel className="cursor-pointer">Make Default</FormLabel>
                  </FormItem>
                )}
              />
            </div>

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                Save
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
