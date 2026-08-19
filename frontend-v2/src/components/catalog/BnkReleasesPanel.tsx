/**
 * BNK Releases Catalog tab — ADR-494 Phase A.
 *
 * Two sections:
 *   1. Release Sources — where the Catalog syncs BNK releases from.
 *   2. BNK Release Catalog — deployable releases available to bare-metal hosts.
 *      (Admin controls relocated from BareMetalPanel.)
 *
 * Admin mutations (create/edit/delete/sync, activate/set-default) are gated on isAdmin.
 */
import { useState, useRef, useEffect } from 'react';
import {
  useReleaseSources,
  useCreateReleaseSource,
  useUpdateReleaseSource,
  useDeleteReleaseSource,
  useSyncReleaseSource,
  useReleaseSourceTags,
  usePullReleaseSourceTags,
} from '@/hooks/useReleaseSources';
import {
  useDeployableReleases,
  useActivateDeployableRelease,
  useSetDefaultDeployableRelease,
} from '@/hooks/useBareMetal';
import { useRole } from '@/hooks/useRole';
import { notify, notifyError } from '@/lib/notify';
import type { ReleaseSource, ReleaseSourceCreate, ReleaseSourceUpdate, ReleaseSourceKind } from '@/lib/api/release-sources';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Box, Loader2, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react';

// ============================================================================
// Helpers
// ============================================================================

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
}

function KindBadge({ kind }: { kind: string }) {
  const variants: Record<string, string> = {
    oci: 'bg-info/10 text-info border-info/20',
    mirror: 'bg-warning/10 text-warning border-warning/20',
    manual: 'bg-muted text-muted-foreground',
  };
  return (
    <Badge variant="outline" className={`text-xs ${variants[kind] ?? 'bg-muted text-muted-foreground'}`}>
      {kind}
    </Badge>
  );
}

function SyncStatusBadge({ status, error }: { status: string; error: string | null }) {
  if (status === 'success') {
    return (
      <Badge variant="outline" className="text-xs bg-success/10 text-success border-success/20">
        synced
      </Badge>
    );
  }
  if (status === 'error') {
    return (
      <Badge
        variant="outline"
        className="text-xs bg-destructive/10 text-destructive border-destructive/20"
        title={error ?? undefined}
      >
        error
      </Badge>
    );
  }
  if (status === 'syncing') {
    return (
      <Badge variant="outline" className="text-xs bg-info/10 text-info border-info/20">
        <Loader2 className="h-3 w-3 mr-1 animate-spin" />
        syncing
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-xs bg-muted text-muted-foreground">
      {status}
    </Badge>
  );
}

// ============================================================================
// Source form
// ============================================================================

interface SourceFormState {
  name: string;
  kind: ReleaseSourceKind;
  url: string;
  credential: string;
  description: string;
  is_active: boolean;
}

const OCI_DEFAULT_URL = 'repo.f5.com';

const BLANK_FORM: SourceFormState = {
  name: '',
  kind: 'manual',
  url: '',
  credential: '',
  description: '',
  is_active: true,
};

function sourceToForm(s: ReleaseSource): SourceFormState {
  return {
    name: s.name,
    kind: s.kind as ReleaseSourceKind,
    url: s.url ?? '',
    credential: '',
    description: s.description ?? '',
    is_active: s.is_active,
  };
}

// ============================================================================
// Source dialog (create / edit)
// ============================================================================

interface SourceDialogProps {
  mode: 'create' | 'edit';
  source: ReleaseSource | null;
  onClose: () => void;
}

function SourceDialog({ mode, source, onClose }: SourceDialogProps) {
  const [form, setForm] = useState<SourceFormState>(
    mode === 'edit' && source ? sourceToForm(source) : BLANK_FORM,
  );
  const [credHint, setCredHint] = useState<string | null>(null);
  const credFileInputRef = useRef<HTMLInputElement>(null);
  const create = useCreateReleaseSource();
  const update = useUpdateReleaseSource();
  const isPending = create.isPending || update.isPending;

  const set = <K extends keyof SourceFormState>(k: K, v: SourceFormState[K]) =>
    setForm((prev) => ({ ...prev, [k]: v }));

  const handleCredentialFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const text = reader.result as string;
      let value: string;
      let detected: string;
      try {
        JSON.parse(text);
        // Valid JSON → treat as raw SA-key file; base64-encode (ASCII-safe)
        value = btoa(text);
        detected = 'detected: SA-key JSON → base64';
      } catch {
        // Not JSON → already a base64 blob or token; store verbatim
        value = text.trim();
        detected = 'stored as-is';
      }
      set('credential', value);
      setCredHint(`Loaded ${file.name} (${detected})`);
    };
    reader.onerror = () => {
      notify.error(`Failed to read file: ${file.name}`);
    };
    reader.readAsText(file);
    // Reset so the same file can be re-selected if needed
    e.target.value = '';
  };

  const handleSubmit = async () => {
    if (!form.name.trim()) return;
    try {
      if (mode === 'create') {
        const payload: ReleaseSourceCreate = {
          name: form.name.trim(),
          kind: form.kind,
          url: form.kind === 'manual' ? null : (form.url.trim() || null),
          credential: form.kind === 'manual' ? null : (form.credential || null),
          description: form.description.trim() || null,
          is_active: form.is_active,
          auto_sync: false,
        };
        await create.mutateAsync(payload);
        notify.success('Release source created');
      } else if (source) {
        const payload: ReleaseSourceUpdate = {
          name: form.name.trim(),
          kind: form.kind,
          url: form.kind === 'manual' ? null : (form.url.trim() || null),
          description: form.description.trim() || null,
          is_active: form.is_active,
        };
        // For manual kind, explicitly clear the credential on edit.
        // For oci/mirror, only include the credential key when the user typed a
        // new value — omitting it preserves the existing encrypted credential via
        // the backend's model_dump(exclude_unset=True) path.
        if (form.kind === 'manual') {
          payload.credential = null;
        } else if (form.credential) {
          payload.credential = form.credential;
        }
        await update.mutateAsync({ id: source.id, payload });
        notify.success('Release source updated');
      }
      onClose();
    } catch (err) {
      notifyError(err);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{mode === 'create' ? 'Add Release Source' : 'Edit Release Source'}</DialogTitle>
          <DialogDescription>
            A release source tells Forge where to sync BNK releases into the Catalog.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="grid grid-cols-2 gap-4">
            <div className="col-span-2 space-y-1">
              <Label htmlFor="src-name">Name <span className="text-destructive">*</span></Label>
              <Input
                id="src-name"
                value={form.name}
                onChange={(e) => set('name', e.target.value)}
                placeholder="e.g. repo.f5.com / air-gap-mirror"
              />
            </div>

            <div className="space-y-1">
              <Label htmlFor="src-kind">Kind</Label>
              <Select
                value={form.kind}
                onValueChange={(v) => {
                  const newKind = v as ReleaseSourceKind;
                  setForm((prev) => ({
                    ...prev,
                    kind: newKind,
                    url:
                      newKind === 'oci' && !prev.url.trim()
                        ? OCI_DEFAULT_URL
                        : newKind === 'manual'
                        ? ''
                        : prev.url,
                    credential: newKind === 'manual' ? '' : prev.credential,
                  }));
                  setCredHint(null);
                }}
              >
                <SelectTrigger id="src-kind">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="oci">OCI registry</SelectItem>
                  <SelectItem value="mirror">Mirror / proxy</SelectItem>
                  <SelectItem value="manual">Manual (paste YAML)</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {form.kind !== 'manual' && (
              <div className="space-y-1">
                <Label htmlFor="src-url">URL</Label>
                <Input
                  id="src-url"
                  value={form.url}
                  onChange={(e) => set('url', e.target.value)}
                  placeholder={form.kind === 'oci' ? 'repo.f5.com' : 'https://internal-mirror.example.com'}
                />
              </div>
            )}

            {form.kind !== 'manual' && (
              <div className="col-span-2 space-y-1">
                <div className="flex items-center gap-2">
                  <Label htmlFor="src-cred">
                    {form.kind === 'oci' ? 'Service-account key (base64)' : 'Pull-secret / token'}
                    {mode === 'edit' && source?.has_credential && (
                      <span className="ml-2 text-xs text-muted-foreground">(leave blank to keep existing)</span>
                    )}
                  </Label>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => credFileInputRef.current?.click()}
                  >
                    Browse…
                  </Button>
                  <input
                    ref={credFileInputRef}
                    type="file"
                    accept=".json,.b64,.txt,text/plain,application/json"
                    onChange={handleCredentialFileSelect}
                    className="hidden"
                  />
                </div>
                <Input
                  id="src-cred"
                  type="password"
                  value={form.credential}
                  onChange={(e) => { set('credential', e.target.value); setCredHint(null); }}
                  placeholder={form.kind === 'oci' ? 'base64 GCP SA key' : 'pull-secret or token'}
                />
                {credHint && (
                  <p className="text-xs text-muted-foreground">{credHint}</p>
                )}
              </div>
            )}

            <div className="col-span-2 space-y-1">
              <Label htmlFor="src-desc">Description</Label>
              <Input
                id="src-desc"
                value={form.description}
                onChange={(e) => set('description', e.target.value)}
                placeholder="Optional notes"
              />
            </div>

            <div className="flex items-center gap-2">
              <input
                id="src-active"
                type="checkbox"
                checked={form.is_active}
                onChange={(e) => set('is_active', e.target.checked)}
                className="h-4 w-4"
              />
              <Label htmlFor="src-active" className="cursor-pointer">Active</Label>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isPending}>Cancel</Button>
          <Button onClick={handleSubmit} disabled={isPending || !form.name.trim()}>
            {isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            {mode === 'create' ? 'Create' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ============================================================================
// Tag picker pane (used inside SyncDialog for oci/mirror sources)
// ============================================================================

interface TagPickerProps {
  sourceId: number;
  onAdd: (tags: string[]) => void;
  isPending: boolean;
}

function TagPicker({ sourceId, onAdd, isPending }: TagPickerProps) {
  // Fetch is demand-driven: the query only activates when the user clicks
  // "Fetch tags". This avoids a registry round-trip on dialog open.
  const [enabled, setEnabled] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [manualTag, setManualTag] = useState('');

  const { data, isFetching, refetch } = useReleaseSourceTags(enabled ? sourceId : null);

  const tagData = data?.tags ?? null;
  const listError = data?.list_error ?? null;
  // Only show the tag list after at least one successful fetch.
  const fetched = enabled && data !== undefined;

  // Pre-select non-catalog, non-prerelease tags whenever a new fetch result
  // arrives (data reference changes). Uses tagData as the dependency so the
  // effect fires once per new server response, not on every render.
  useEffect(() => {
    if (!tagData) return;
    const initial = new Set<string>();
    for (const t of tagData) {
      if (!t.in_catalog && !t.prerelease) {
        initial.add(t.tag);
      }
    }
    setSelected(initial);
  }, [tagData]);

  const handleFetch = () => {
    if (!enabled) {
      setEnabled(true);
    } else {
      void refetch();
    }
  };

  const toggle = (tag: string, disabled: boolean) => {
    if (disabled) return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) {
        next.delete(tag);
      } else {
        next.add(tag);
      }
      return next;
    });
  };

  const handleAdd = () => {
    const all = new Set(selected);
    if (manualTag.trim()) {
      all.add(manualTag.trim());
    }
    onAdd([...all]);
    setManualTag('');
  };

  const canAdd = selected.size > 0 || manualTag.trim().length > 0;

  return (
    <div className="space-y-3 border border-border rounded-lg p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">Available tags</span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleFetch}
          disabled={isFetching}
        >
          {isFetching ? (
            <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5 mr-1" />
          )}
          Fetch tags
        </Button>
      </div>

      {listError && (
        <p className="text-xs text-destructive bg-destructive/10 rounded px-2 py-1">
          Listing failed: {listError} — use manual entry below.
        </p>
      )}

      {fetched && tagData && tagData.length > 0 && (
        <div className="max-h-48 overflow-y-auto space-y-1 pr-1">
          {tagData.map((t) => (
            <label
              key={t.tag}
              className={`flex items-center gap-2 text-xs px-2 py-1 rounded cursor-pointer hover:bg-muted/50 ${
                t.in_catalog ? 'opacity-50 cursor-not-allowed' : ''
              }`}
            >
              <input
                type="checkbox"
                checked={selected.has(t.tag)}
                disabled={t.in_catalog}
                onChange={() => toggle(t.tag, t.in_catalog)}
                className="h-3.5 w-3.5"
              />
              <span className="font-mono">{t.tag}</span>
              {t.in_catalog && (
                <Badge variant="outline" className="text-[10px] h-4 px-1 bg-success/10 text-success border-success/20">
                  in Catalog
                </Badge>
              )}
              {t.prerelease && (
                <Badge variant="outline" className="text-[10px] h-4 px-1 bg-warning/10 text-warning border-warning/20">
                  pre-release
                </Badge>
              )}
            </label>
          ))}
        </div>
      )}

      {fetched && tagData && tagData.length === 0 && !listError && (
        <p className="text-xs text-muted-foreground">No tags found in the registry.</p>
      )}

      {/* Manual tag entry — always available as fallback */}
      <div className="space-y-1">
        <Label htmlFor="manual-tag" className="text-xs">Manual tag entry</Label>
        <div className="flex gap-2">
          <Input
            id="manual-tag"
            value={manualTag}
            onChange={(e) => setManualTag(e.target.value)}
            placeholder="e.g. 2.3.1-3.2598.3-0.0.304"
            className="text-xs font-mono h-8"
          />
        </div>
      </div>

      <Button
        size="sm"
        onClick={handleAdd}
        disabled={isPending || !canAdd}
        className="w-full"
      >
        {isPending && <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />}
        Add selected
      </Button>
    </div>
  );
}

// ============================================================================
// Sync dialog
// ============================================================================

interface SyncDialogProps {
  source: ReleaseSource;
  onClose: () => void;
}

function SyncDialog({ source, onClose }: SyncDialogProps) {
  const [yaml, setYaml] = useState('');
  const [pickedFileName, setPickedFileName] = useState<string | null>(null);
  const [pullSummary, setPullSummary] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sync = useSyncReleaseSource();
  const pullTags = usePullReleaseSourceTags();

  const canLiveFetch = source.kind === 'oci' || source.kind === 'mirror';

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setYaml(reader.result as string);
      setPickedFileName(file.name);
    };
    reader.onerror = () => {
      notify.error(`Failed to read file: ${file.name}`);
    };
    reader.readAsText(file);
    // Reset so the same file can be re-selected if needed
    e.target.value = '';
  };

  const handleSync = async () => {
    if (!yaml.trim()) return;
    try {
      const result = await sync.mutateAsync({ id: source.id, manifestYaml: yaml.trim() });
      const summary = Object.entries(result.sync_result)
        .map(([k, v]) => `${v} ${k}`)
        .join(', ');
      notify.success(`Sync complete: ${summary || 'no changes'}`);
      onClose();
    } catch (err) {
      notifyError(err);
    }
  };

  const handlePullTags = async (tags: string[]) => {
    if (tags.length === 0) return;
    try {
      const result = await pullTags.mutateAsync({ id: source.id, tags });
      const parts: string[] = [];
      if (result.added.length > 0) parts.push(`${result.added.length} added`);
      if (result.skipped.length > 0) parts.push(`${result.skipped.length} already in Catalog`);
      if (result.failed.length > 0) {
        parts.push(`${result.failed.length} failed`);
      }
      const msg = parts.join(', ') || 'no changes';
      if (result.failed.length > 0) {
        const reasons = result.failed.map((f) => `${f.tag}: ${f.reason}`).join('; ');
        setPullSummary(`Done (${msg}). Failures: ${reasons}`);
      } else {
        setPullSummary(`Done: ${msg}`);
      }
      notify.success(`Pull complete: ${msg}`);
    } catch (err) {
      notifyError(err);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Sync Release Source — {source.name}</DialogTitle>
          <DialogDescription>
            {canLiveFetch
              ? 'Fetch available tags from the registry and add them to the Catalog.'
              : 'Paste the BNK manifest YAML below. Forge will parse it and add any new releases to the Catalog.'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {canLiveFetch ? (
            <div className="space-y-2">
              <p className="text-sm font-medium">Pull from registry</p>
              <TagPicker
                sourceId={source.id}
                onAdd={handlePullTags}
                isPending={pullTags.isPending}
              />
              {pullSummary && (
                <p className="text-xs text-muted-foreground bg-muted rounded px-2 py-1">
                  {pullSummary}
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Label htmlFor="sync-yaml">BNK manifest YAML</Label>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => fileInputRef.current?.click()}
                >
                  Browse…
                </Button>
                {pickedFileName && (
                  <span className="text-xs text-muted-foreground truncate max-w-[200px]">
                    {pickedFileName}
                  </span>
                )}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".yaml,.yml,text/yaml,text/plain"
                  onChange={handleFileSelect}
                  className="hidden"
                />
              </div>
              <Textarea
                id="sync-yaml"
                value={yaml}
                onChange={(e) => setYaml(e.target.value)}
                placeholder="Paste manifest YAML here..."
                className="font-mono text-xs min-h-[160px]"
              />
            </div>
          )}
        </div>

        {source.sync_error && (
          <p className="text-xs text-destructive bg-destructive/10 rounded px-3 py-2">
            Last sync error: {source.sync_error}
          </p>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={sync.isPending || pullTags.isPending}>Cancel</Button>
          {!canLiveFetch && (
            <Button onClick={handleSync} disabled={sync.isPending || !yaml.trim()}>
              {sync.isPending ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4 mr-2" />
              )}
              Sync YAML
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ============================================================================
// Release Sources section
// ============================================================================

function ReleaseSourcesSection({ isAdmin }: { isAdmin: boolean }) {
  const { data: sources, isLoading } = useReleaseSources();
  const deleteSource = useDeleteReleaseSource();
  const [dialog, setDialog] = useState<{ mode: 'create' | 'edit'; source: ReleaseSource | null } | null>(null);
  const [syncTarget, setSyncTarget] = useState<ReleaseSource | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ReleaseSource | null>(null);

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteSource.mutateAsync(deleteTarget.id);
      notify.success('Release source deleted');
    } catch (err) {
      notifyError(err);
    } finally {
      setDeleteTarget(null);
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-muted">
            <Box className="h-5 w-5 text-muted-foreground" />
          </div>
          <div>
            <CardTitle className="text-lg">Release Sources</CardTitle>
            <CardDescription>
              Where Forge syncs BNK releases from — OCI registries, mirrors, or manual manifests.
            </CardDescription>
          </div>
        </div>
        {isAdmin && (
          <Button size="sm" onClick={() => setDialog({ mode: 'create', source: null })}>
            <Plus className="h-4 w-4 mr-1" />Add Source
          </Button>
        )}
      </CardHeader>

      <CardContent className="pt-2">
        {isLoading ? (
          <div className="space-y-2 p-2">
            {[0, 1, 2].map((i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        ) : !sources || sources.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">
            No release sources configured. {isAdmin && 'Click "Add Source" to create one.'}
          </p>
        ) : (
          <div className="border border-border rounded-lg overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>URL</TableHead>
                  <TableHead>Active</TableHead>
                  <TableHead>Sync Status</TableHead>
                  <TableHead>Last Synced</TableHead>
                  <TableHead className="text-right">Releases</TableHead>
                  {isAdmin && <TableHead />}
                </TableRow>
              </TableHeader>
              <TableBody>
                {sources.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell className="font-medium">{s.name}</TableCell>
                    <TableCell><KindBadge kind={s.kind} /></TableCell>
                    <TableCell className="max-w-[200px] truncate text-xs text-muted-foreground font-mono">
                      {s.url ?? '—'}
                    </TableCell>
                    <TableCell>
                      {s.is_active ? (
                        <Badge variant="outline" className="text-xs bg-success/10 text-success border-success/20">active</Badge>
                      ) : (
                        <Badge variant="outline" className="text-xs bg-muted text-muted-foreground">inactive</Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <SyncStatusBadge status={s.sync_status} error={s.sync_error} />
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDate(s.last_synced_at)}
                    </TableCell>
                    <TableCell className="text-right text-sm">{s.release_count}</TableCell>
                    {isAdmin && (
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 w-7 p-0"
                            title="Sync"
                            onClick={() => setSyncTarget(s)}
                          >
                            <RefreshCw className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 w-7 p-0"
                            title="Edit"
                            onClick={() => setDialog({ mode: 'edit', source: s })}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 w-7 p-0 text-destructive hover:text-destructive"
                            title="Delete"
                            onClick={() => setDeleteTarget(s)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>

      {dialog && (
        <SourceDialog
          mode={dialog.mode}
          source={dialog.source}
          onClose={() => setDialog(null)}
        />
      )}

      {syncTarget && (
        <SyncDialog source={syncTarget} onClose={() => setSyncTarget(null)} />
      )}

      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete release source?</AlertDialogTitle>
            <AlertDialogDescription>
              This will remove <strong>{deleteTarget?.name}</strong> from Forge. Releases already
              synced into the Catalog are not deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={handleDelete}
              disabled={deleteSource.isPending}
            >
              {deleteSource.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}

// ============================================================================
// BNK Release Catalog section (relocated from BareMetalPanel)
// ============================================================================

function ReleaseCatalogSection({ isAdmin }: { isAdmin: boolean }) {
  const { data: releases, isLoading } = useDeployableReleases();
  const activate = useActivateDeployableRelease();
  const setDefault = useSetDefaultDeployableRelease();

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-muted">
            <Box className="h-5 w-5 text-muted-foreground" />
          </div>
          <div>
            <CardTitle className="text-lg">BNK Release Catalog</CardTitle>
            <CardDescription>
              Deployable BNK releases available to bare-metal hosts.
            </CardDescription>
          </div>
        </div>
      </CardHeader>

      <CardContent className="pt-2">
        {isLoading ? (
          <div className="space-y-2 p-2">
            {[0, 1, 2].map((i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        ) : !releases || releases.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">
            No releases in the catalog. Sync a release source to populate it.
          </p>
        ) : (
          <div className="border border-border rounded-lg overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Release</TableHead>
                  <TableHead>Manifest</TableHead>
                  <TableHead>Flo</TableHead>
                  <TableHead>Status</TableHead>
                  {isAdmin && <TableHead />}
                </TableRow>
              </TableHeader>
              <TableBody>
                {releases.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-medium">{r.display_name}</TableCell>
                    <TableCell className="text-xs text-muted-foreground font-mono">
                      {r.bnk_manifest_version}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground font-mono">
                      {r.flo_version}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1 flex-wrap">
                        {r.is_default && (
                          <Badge variant="outline" className="text-xs bg-info/10 text-info border-info/20">
                            default
                          </Badge>
                        )}
                        {r.is_active ? (
                          <Badge variant="outline" className="text-xs bg-success/10 text-success border-success/20">
                            active
                          </Badge>
                        ) : (
                          <Badge variant="outline" className="text-xs bg-muted text-muted-foreground">
                            inactive
                          </Badge>
                        )}
                      </div>
                    </TableCell>
                    {isAdmin && (
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-2">
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 px-2 text-xs"
                            disabled={activate.isPending}
                            onClick={() => activate.mutate({ releaseId: r.id, isActive: !r.is_active })}
                          >
                            {r.is_active ? 'Deactivate' : 'Activate'}
                          </Button>
                          {!r.is_default && r.is_active && (
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-7 px-2 text-xs"
                              disabled={setDefault.isPending}
                              onClick={() => setDefault.mutate(r.id)}
                            >
                              Set Default
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ============================================================================
// Main panel export
// ============================================================================

export default function BnkReleasesPanel() {
  const { isAdmin } = useRole();

  return (
    <div className="space-y-6">
      <ReleaseSourcesSection isAdmin={isAdmin} />
      <ReleaseCatalogSection isAdmin={isAdmin} />
    </div>
  );
}
