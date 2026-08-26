/**
 * WAF Policies page — 4 tabs matching the 4 appprotect.f5.com CRD kinds.
 *
 * Tab order reflects the workflow dependency chain:
 *   1. Log Profiles   (APLogConf)   — create reusable log profiles first
 *   2. Policies       (APPolicy)    — create policies that may reference log profiles
 *   3. Signatures     (APSignatures) — namespace-wide singleton: sig revision settings
 *   4. User Signatures (APUserSig)  — custom attack signatures embedded into policies
 *
 * All 4 CRDs have full Create / List / Edit / Delete UI.
 *
 * See docs/WAF_POLICY_MANAGER_DESIGN.md.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { EmptyState } from '@/components/ui/empty-state';
import { SkeletonTable } from '@/components/ui/skeleton-table';
import { DestructiveConfirmDialog } from '@/components/ui/destructive-confirm-dialog';
import { Plus, Shield, Trash2, FileText, Key, RefreshCw, PenLine, Pencil, AlertTriangle, RotateCcw, Info, Download, RefreshCcw, ExternalLink, CheckSquare, Square, Filter, X, ChevronDown, Globe, ArrowRight, Network, Route, Lock } from 'lucide-react';

// Refresh button — spins while loading, then pulses green briefly on success
function RefreshButton({ refetch, isLoading }: { refetch: () => void; isLoading?: boolean }) {
  const [flash, setFlash] = useState(false);
  const handleClick = () => {
    refetch();
    setFlash(true);
    setTimeout(() => setFlash(false), 800);
  };
  return (
    <Button
      variant="ghost" size="sm"
      className={cn('h-8 w-8 p-0 transition-colors', flash && !isLoading ? 'text-success' : '')}
      onClick={handleClick}
      title="Refresh from cluster"
    >
      <RotateCcw className={cn('h-3.5 w-3.5 transition-transform', isLoading ? 'animate-spin text-primary' : '')} />
    </Button>
  );
}
import { useAllClusters } from '@/hooks/useK8sClusters';
import { useClusterNamespaces } from '@/hooks/useK8sResources';
import {
  useWafPolicies, useDeleteWafPolicy, useRecompileWafPolicy,
  useWafLogConfs, useDeleteWafLogConf,
  useWafSignatures, useUpsertWafSignatures, useDeleteWafSignatures,
  useWafUserSigs, useDeleteWafUserSig,
} from '@/hooks/useWafPolicies';
import { WafPolicyDetail } from '@/components/k8s/f5bnk-details/WafPolicyDetail';
import { APLogConfDetail } from '@/components/k8s/f5bnk-details/APLogConfDetail';
import { APUserSigDetail } from '@/components/k8s/f5bnk-details/APUserSigDetail';
import { APPolicyForm } from '@/components/k8s/waf/APPolicyForm';
import { APLogConfForm } from '@/components/k8s/waf/APLogConfForm';
import { APUserSigForm } from '@/components/k8s/waf/APUserSigForm';
import { formatAge } from '@/lib/time-utils';
import type {
  APPolicyResource, APLogConfResource, APUserSigResource, BundleState,
} from '@/types';
import {
  useGateways, useCreateGateway, useUpdateGateway, useDeleteGateway,
  useSecurityProfiles, useCreateSecurityProfile, useUpdateSecurityProfile, useDeleteSecurityProfile,
  useHTTPRoutes, useCreateHTTPRoute, useUpdateHTTPRoute, useDeleteHTTPRoute,
  useReferenceGrants, useCreateReferenceGrant, useDeleteReferenceGrant,
  useGatewayClasses, useCreateGatewayClass, useDeleteGatewayClass,
} from '@/hooks/useWafGateway';
import type { Gateway, HTTPRoute, SecurityProfile } from '@/lib/api/waf-gateway';

type PageTab = 'policies' | 'log-profiles' | 'signatures' | 'user-sigs' | 'gateway-waf';

// NIM-style pill filter — "+ Add Filter" button opens a field+value picker,
// creates dismissible pill chips above the table
interface FilterPill { field: string; value: string; }

interface TabToolbarProps {
  filterDef: { field: string; label: string; options?: string[] }[];
  pills: FilterPill[];
  onAddPill: (p: FilterPill) => void;
  onRemovePill: (i: number) => void;
  onRefresh: () => void;
  isLoading?: boolean;
  onDelete?: () => void;
  deleteCount?: number;
  onCreate: () => void;
  createLabel: string;
  docsUrl?: string;
}

function TabToolbar({
  filterDef, pills, onAddPill, onRemovePill, onRefresh, isLoading,
  onDelete, deleteCount, onCreate, createLabel, docsUrl,
}: TabToolbarProps) {
  const [open, setOpen] = useState(false);
  const [field, setField] = useState(filterDef[0]?.field ?? '');
  const [value, setValue] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const handleAdd = () => {
    const v = value.trim();
    if (!v) return;
    onAddPill({ field, value: v });
    setValue('');
    setOpen(false);
  };

  const currentDef = filterDef.find(f => f.field === field);

  return (
    <div className="space-y-2">
      {/* Action bar */}
      <div className="flex items-center gap-2 flex-wrap">
        {/* Add Filter button + dropdown */}
        <div className="relative" ref={ref}>
          <button
            onClick={() => setOpen(v => !v)}
            className={cn(
              'inline-flex items-center gap-1.5 h-9 px-3 rounded-md border text-xs font-medium transition-colors',
              open ? 'border-primary text-primary bg-primary/5' : 'border-border text-muted-foreground hover:text-foreground hover:bg-accent',
            )}
          >
            <Filter className="h-3.5 w-3.5" />
            Add Filter
            <ChevronDown className={cn('h-3 w-3 transition-transform', open && 'rotate-180')} />
          </button>

          {open && (
            <div className="absolute left-0 top-full mt-1 z-50 w-64 rounded-lg border border-border bg-popover shadow-lg p-3 space-y-2">
              <p className="text-xs font-medium text-foreground">Add Filter</p>
              {/* Field selector */}
              <select
                value={field}
                onChange={(e) => { setField(e.target.value); setValue(''); }}
                className="w-full h-8 rounded-md border border-input bg-background text-xs px-2 focus:outline-none focus:ring-1 focus:ring-ring"
              >
                {filterDef.map(f => <option key={f.field} value={f.field}>{f.label}</option>)}
              </select>
              {/* Value input or select */}
              {currentDef?.options ? (
                <select
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  className="w-full h-8 rounded-md border border-input bg-background text-xs px-2 focus:outline-none focus:ring-1 focus:ring-ring"
                >
                  <option value="">Select value…</option>
                  {currentDef.options.map(o => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : (
                <input
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
                  placeholder="Enter value…"
                  className="w-full h-8 rounded-md border border-input bg-background text-xs px-2 focus:outline-none focus:ring-1 focus:ring-ring"
                />
              )}
              <div className="flex gap-2 pt-1">
                <Button size="sm" className="flex-1 h-7 text-xs" onClick={handleAdd} disabled={!value}>Apply</Button>
                <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setOpen(false)}>Cancel</Button>
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center gap-1.5 ml-auto">
          {docsUrl && (
            <a href={docsUrl} target="_blank" rel="noopener noreferrer"
              className="inline-flex items-center gap-1 h-9 px-3 rounded-md border border-border text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-accent transition-colors">
              <ExternalLink className="h-3.5 w-3.5" /> Docs
            </a>
          )}
          <Button variant="ghost" size="sm" className="h-9 px-3 gap-1.5" onClick={onRefresh}>
            <RotateCcw className={cn('h-3.5 w-3.5', isLoading && 'animate-spin text-primary')} />
            <span className="text-xs">Refresh</span>
          </Button>
          {onDelete && (
            <Button variant="ghost" size="sm" className="h-9 px-3 gap-1.5 text-muted-foreground" onClick={onDelete} disabled={!deleteCount}>
              <Trash2 className="h-3.5 w-3.5" />
              <span className="text-xs">Delete{deleteCount ? ` (${deleteCount})` : ''}</span>
            </Button>
          )}
          <Button size="sm" className="h-9 px-3 gap-1.5 min-w-[196px] justify-center" onClick={onCreate}>
            <Plus className="h-3.5 w-3.5" /> {createLabel}
          </Button>
        </div>
      </div>

      {/* Active filter pills */}
      {pills.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {pills.map((p, i) => (
            <span key={i} className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/5 text-primary text-[11px] px-2.5 py-0.5">
              <span className="text-muted-foreground">{filterDef.find(f => f.field === p.field)?.label ?? p.field}:</span>
              <span className="font-medium">{p.value}</span>
              <button onClick={() => onRemovePill(i)} className="ml-0.5 hover:text-destructive transition-colors">
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
          <button onClick={() => pills.forEach((_, i) => onRemovePill(pills.length - 1 - i))}
            className="text-[11px] text-muted-foreground hover:text-foreground underline">
            Clear all
          </button>
        </div>
      )}
    </div>
  );
}

// Table footer row count like NIM
// Table footer row count like NIM — handles irregular plurals (policy -> policies)
function pluralize(label: string, count: number): string {
  if (count === 1) return label;
  if (label.endsWith('y') && !/[aeiou]y$/i.test(label)) return `${label.slice(0, -1)}ies`;
  return `${label}s`;
}

function TableCount({ count, label }: { count: number; label: string }) {
  return (
    <p className="text-xs text-muted-foreground pt-1">
      <strong>{count}</strong> {pluralize(label, count)}
    </p>
  );
}

// Download a CR as formatted JSON file for backup / GitOps use
function exportCR(resource: unknown, name: string, kind: string) {
  const blob = new Blob([JSON.stringify(resource, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `${kind.toLowerCase()}-${name}.json`; a.click();
  URL.revokeObjectURL(url);
}

// Extract the most useful message from an Axios/API error
function extractApiError(e: unknown): string {
  const resp = (e as { response?: { data?: { detail?: string; message?: string; error?: string | { message?: string } } } })?.response?.data;
  if (resp?.detail) return String(resp.detail);
  if (resp?.message) return String(resp.message);
  if (typeof resp?.error === 'string') return resp.error;
  if (typeof resp?.error === 'object' && resp.error?.message) return resp.error.message;
  return e instanceof Error ? e.message : String(e);
}

function InlineError({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-1.5 rounded-md border border-destructive/30 bg-destructive/10 dark:border-destructive/20 dark:bg-destructive/20/20 px-3 py-2 text-xs text-destructive dark:text-destructive/80">
      <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
      <span>{message}</span>
    </div>
  );
}

function getBundleStateBadgeClass(state: BundleState | undefined) {
  switch (state) {
    case 'ready':       return 'bg-success/10 text-success border-success/50/20';
    case 'invalid':     return 'bg-destructive/10 text-destructive border-destructive/50/20';
    case 'processing':  return 'bg-primary/10 text-primary border-primary/50/20';
    default:            return 'bg-muted-foreground/20/10 text-muted-foreground border-muted-foreground/30/20';
  }
}

// ============================================================================
// Shared: namespace picker (dropdown from cluster + free-text fallback)
// ============================================================================

function NamespacePicker({
  clusterId,
  value,
  onChange,
}: { clusterId: number; value: string; onChange: (ns: string) => void }) {
  const { data: nsData } = useClusterNamespaces(clusterId, { enabled: !!clusterId });
  const namespaces = useMemo(
    () => (nsData?.namespaces ?? []).map((n: { name?: string } | string) => typeof n === 'string' ? n : n.name ?? '').filter(Boolean),
    [nsData]
  );

  return namespaces.length > 0 ? (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-44 h-9 text-sm"><SelectValue placeholder="namespace" /></SelectTrigger>
      <SelectContent>
        {namespaces.map((ns) => <SelectItem key={ns} value={ns}>{ns}</SelectItem>)}
      </SelectContent>
    </Select>
  ) : (
    <Input value={value} onChange={(e) => onChange(e.target.value)} className="w-36 h-9 text-sm" placeholder="default" />
  );
}

// ============================================================================
// Tab 1: Log Profiles (APLogConf) — create before policies
// ============================================================================

// ── Shared: Log Profile form fields (reused by create panel and edit sheet) ──


// ── Edit sheet: APLogConf ──────────────────────────────────────────────────

function LogProfilesTab({ clusterId, namespace }: { clusterId: number; namespace: string; isDark: boolean }) {
  const [showCreate, setShowCreate] = useState(false);
  const [viewingItem, setViewingItem]   = useState<APLogConfResource | null>(null);
  const [editingItem, setEditingItem]   = useState<APLogConfResource | null>(null);
  const [deletingItem, setDeletingItem] = useState<APLogConfResource | null>(null);
  const [pills, setPills] = useState<{ field: string; value: string }[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data, isLoading, refetch: refetchLogConfs } = useWafLogConfs(clusterId, namespace, { enabled: !!clusterId });
  const logConfs = useMemo(() => data?.log_confs ?? [], [data]);
  const deleteLc = useDeleteWafLogConf(clusterId);

  const filtered = useMemo(() => logConfs.filter((lc: APLogConfResource) =>
    pills.every(p => {
      if (p.field === 'name') return lc.metadata.name.toLowerCase().includes(p.value.toLowerCase());
      if (p.field === 'format') return ((lc.spec?.content as { format?: string } | undefined)?.format ?? '') === p.value;
      if (p.field === 'state') return (lc.status?.bundle?.state ?? '') === p.value;
      return true;
    })
  ), [logConfs, pills]);

  return (
    <div className="space-y-3">
      <TabToolbar
        filterDef={[
          { field: 'name',   label: 'Name' },
          { field: 'format', label: 'Format', options: ['default', 'splunk', 'arcsight', 'grpc', 'user-defined'] },
          { field: 'state',  label: 'Bundle State', options: ['ready', 'processing', 'invalid'] },
        ]}
        pills={pills}
        onAddPill={(p) => setPills(prev => [...prev, p])}
        onRemovePill={(i) => setPills(prev => prev.filter((_, idx) => idx !== i))}
        onRefresh={refetchLogConfs} isLoading={isLoading}
        docsUrl="https://docs.nginx.com/nginx-app-protect-waf/configuration-guide/configuration/#security-log"
        onDelete={() => { /* bulk delete not yet wired */ }}
        deleteCount={selected.size}
        onCreate={() => setShowCreate(!showCreate)}
        createLabel={showCreate ? 'Cancel' : 'Create Log Profile'}
      />

      {/* Create Log Profile — opens APLogConfForm in a wide side sheet */}
      {showCreate && (
        <Sheet open={showCreate} onOpenChange={(open) => !open && setShowCreate(false)}>
          <SheetContent className="w-full sm:max-w-[75vw] p-0 flex flex-col h-full overflow-hidden">
            <SheetHeader className="px-6 pt-5 pb-3 border-b border-border shrink-0">
              <SheetTitle className="flex items-center gap-2">
                <FileText className="h-4 w-4" /> Create Log Profile
              </SheetTitle>
            </SheetHeader>
            <div className="flex-1 min-h-0">
              <APLogConfForm clusterId={clusterId} namespace={namespace} onClose={() => setShowCreate(false)} />
            </div>
          </SheetContent>
        </Sheet>
      )}

      {isLoading && <SkeletonTable rows={3} columns={5} />}
      {!isLoading && logConfs.length === 0 && (
        <EmptyState icon={FileText} title="No log profiles yet" description="Create an APLogConf to define WAF security event log format." />
      )}
      {logConfs.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>Name</TableHead>
              <TableHead>Format</TableHead>
              <TableHead>Request Filter</TableHead>
              <TableHead>Bundle State</TableHead>
              <TableHead>Age</TableHead>
              <TableHead className="w-24" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((lc: APLogConfResource) => {
              const key = `${lc.metadata.namespace}/${lc.metadata.name}`;
              return (
              <TableRow key={key} className="cursor-pointer" onClick={() => setViewingItem(lc)}>
                <TableCell onClick={(e) => e.stopPropagation()}>
                  <button onClick={() => setSelected(s => { const n = new Set(s); n.has(key) ? n.delete(key) : n.add(key); return n; })} className="flex items-center text-muted-foreground hover:text-foreground">
                    {selected.has(key) ? <CheckSquare className="h-4 w-4 text-primary" /> : <Square className="h-4 w-4" />}
                  </button>
                </TableCell>
                <TableCell className="font-medium">{lc.metadata.name}</TableCell>
                <TableCell className="text-xs">{(lc.spec?.content as { format?: string } | undefined)?.format ?? '—'}</TableCell>
                <TableCell className="text-xs">{lc.spec?.filter?.request_type ?? '—'}</TableCell>
                <TableCell>
                  <Badge variant="outline" className={cn('text-[10px]', getBundleStateBadgeClass(lc.status?.bundle?.state as BundleState | undefined))}>
                    {(lc.status?.bundle?.state as string | undefined) ?? 'unknown'}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs">{formatAge(lc.metadata.creationTimestamp)}</TableCell>
                <TableCell>
                  <div className="flex items-center gap-1">
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-muted-foreground hover:text-primary" title="Edit" onClick={(e) => { e.stopPropagation(); setEditingItem(lc); }}>
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-muted-foreground hover:text-success" title="Export JSON" onClick={(e) => { e.stopPropagation(); exportCR(lc, lc.metadata.name, 'APLogConf'); }}>
                      <Download className="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive" title="Delete" onClick={(e) => { e.stopPropagation(); setDeletingItem(lc); }}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ); })}
          </TableBody>
        </Table>
      )}
      {!isLoading && logConfs.length > 0 && <TableCount count={filtered.length} label="log profile" />}

      {/* Detail sheet */}
      <Sheet open={!!viewingItem} onOpenChange={(open) => !open && setViewingItem(null)}>
        <SheetContent className="w-full sm:max-w-[75vw] overflow-y-auto p-0">
          <SheetHeader className="px-4 pt-4 pb-0"><SheetTitle>{viewingItem?.metadata.name}</SheetTitle></SheetHeader>
          {viewingItem && <APLogConfDetail resource={viewingItem} />}
        </SheetContent>
      </Sheet>

      {/* Edit sheet */}
      <Sheet open={!!editingItem} onOpenChange={(open) => !open && setEditingItem(null)}>
        <SheetContent className="w-full sm:max-w-[75vw] p-0 flex flex-col h-full overflow-hidden">
          <SheetHeader className="px-6 pt-5 pb-3 border-b border-border shrink-0"><SheetTitle>Edit Log Profile — {editingItem?.metadata.name}</SheetTitle></SheetHeader>
          {editingItem && (
            <div className="flex-1 min-h-0">
              <APLogConfForm
                key={`${editingItem.metadata.namespace}/${editingItem.metadata.name}/${editingItem.metadata.uid}`}
                clusterId={clusterId}
                namespace={namespace}
                existingItem={editingItem}
                onClose={() => setEditingItem(null)}
              />
            </div>
          )}
        </SheetContent>
      </Sheet>

      <DestructiveConfirmDialog
        open={!!deletingItem}
        onOpenChange={(open) => !open && setDeletingItem(null)}
        title={`Delete Log Profile "${deletingItem?.metadata.name}"?`}
        description="Policies referencing this log profile will no longer be associated with it after recompile."
        confirmText={deletingItem?.metadata.name ?? ''}
        isPending={deleteLc.isPending}
        onConfirm={() => {
          if (!deletingItem) return;
          deleteLc.mutate({ name: deletingItem.metadata.name, namespace: deletingItem.metadata.namespace ?? namespace }, { onSuccess: () => setDeletingItem(null) });
        }}
      />
    </div>
  );
}


// ============================================================================
// Tab 2: Policies (APPolicy) — references log profiles
// ============================================================================

function PoliciesTab({ clusterId, namespace, isDark, initialOpenPolicy, onConsumeInitialOpen }: { clusterId: number; namespace: string; isDark: boolean; initialOpenPolicy?: string | null; onConsumeInitialOpen?: () => void }) {
  const [showWizard, setShowWizard] = useState(false);
  const [selectedPolicy, setSelectedPolicy] = useState<APPolicyResource | null>(null);
  const [editingPolicy, setEditingPolicy]   = useState<APPolicyResource | null>(null);
  const [deletingPolicy, setDeletingPolicy] = useState<APPolicyResource | null>(null);
  const [pills, setPills] = useState<{ field: string; value: string }[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data, isLoading, error, refetch } = useWafPolicies(clusterId, namespace, { enabled: !!clusterId });
  const policies = useMemo(() => data?.policies ?? [], [data]);
  const deletePolicy = useDeleteWafPolicy(clusterId);
  const recompile = useRecompileWafPolicy(clusterId);

  // Deep-link support: /waf-policies?policy=name auto-opens that policy's detail sheet
  useEffect(() => {
    if (!initialOpenPolicy || policies.length === 0) return;
    const match = policies.find((p: APPolicyResource) => p.metadata.name === initialOpenPolicy);
    if (match) {
      setSelectedPolicy(match);
      onConsumeInitialOpen?.();
    }
  }, [initialOpenPolicy, policies, onConsumeInitialOpen]);

  const filtered = useMemo(() => policies.filter((p: APPolicyResource) =>
    pills.every(pill => {
      if (pill.field === 'name') return p.metadata.name.toLowerCase().includes(pill.value.toLowerCase());
      if (pill.field === 'enforcement') {
        const mode = (p.spec?.policy as { 'enforcement-mode'?: string; enforcementMode?: string } | undefined)?.['enforcement-mode']
          ?? (p.spec?.policy as { enforcementMode?: string } | undefined)?.enforcementMode
          ?? '';
        return mode === pill.value;
      }
      if (pill.field === 'state') return (p.status?.bundle?.state ?? '') === pill.value;
      return true;
    })
  ), [policies, pills]);

  return (
    <div className="space-y-3">
      <TabToolbar
        filterDef={[
          { field: 'name',        label: 'Name' },
          { field: 'enforcement', label: 'Enforcement Mode', options: ['blocking', 'transparent'] },
          { field: 'state',       label: 'Bundle State', options: ['ready', 'processing', 'invalid'] },
        ]}
        pills={pills}
        onAddPill={(p) => setPills(prev => [...prev, p])}
        onRemovePill={(i) => setPills(prev => prev.filter((_, idx) => idx !== i))}
        onRefresh={refetch} isLoading={isLoading}
        docsUrl="https://docs.nginx.com/nginx-app-protect-waf/configuration-guide/configuration/"
        onDelete={() => { /* TODO: bulk delete */ }}
        deleteCount={selected.size}
        onCreate={() => setShowWizard(!showWizard)}
        createLabel={showWizard ? 'Cancel' : 'Create Policy'}
      />

      {/* Create Policy — opens APPolicyForm directly in a wide side sheet */}
      {showWizard && (
        <Sheet open={showWizard} onOpenChange={(open) => !open && setShowWizard(false)}>
          <SheetContent className="w-full sm:max-w-[75vw] p-0 flex flex-col h-full overflow-hidden">
            <SheetHeader className="px-6 pt-5 pb-3 border-b border-border shrink-0">
              <SheetTitle className="flex items-center gap-2">
                <Shield className="h-4 w-4" /> Create WAF Policy
              </SheetTitle>
            </SheetHeader>
            <div className="flex-1 min-h-0">
              <APPolicyForm
                clusterId={clusterId}
                namespace={namespace}
                onClose={() => setShowWizard(false)}
              />
            </div>
          </SheetContent>
        </Sheet>
      )}

      {isLoading && <SkeletonTable rows={4} columns={5} />}
      {error && (
        <div className={cn('rounded-lg border p-4 text-center', isDark ? 'border-destructive/50/30 bg-destructive/5' : 'border-destructive/20 bg-destructive/10')}>
          <p className="text-sm text-destructive dark:text-destructive/80">Failed to load policies</p>
        </div>
      )}
      {!isLoading && !error && policies.length === 0 && !showWizard && (
        <EmptyState icon={Shield} title="No WAF policies yet" description="Create your first APPolicy. Create any required Log Profiles first in the Log Profiles tab." />
      )}
      {!isLoading && !error && policies.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>Name</TableHead>
              <TableHead>Enforcement Mode</TableHead>
              <TableHead>Bundle State</TableHead>
              <TableHead>Namespace</TableHead>
              <TableHead>Age</TableHead>
              <TableHead className="w-28" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((p: APPolicyResource) => {
              const key = `${p.metadata.namespace}/${p.metadata.name}`;
              const enfMode = (p.spec?.policy as { 'enforcement-mode'?: string; enforcementMode?: string } | undefined)?.['enforcement-mode']
                ?? (p.spec?.policy as { enforcementMode?: string } | undefined)?.enforcementMode
                ?? '—';
              return (
              <TableRow key={key} className="cursor-pointer" onClick={() => setSelectedPolicy(p)}>
                <TableCell onClick={(e) => e.stopPropagation()}>
                  <button onClick={() => setSelected(s => { const n = new Set(s); n.has(key) ? n.delete(key) : n.add(key); return n; })} className="flex items-center text-muted-foreground hover:text-foreground">
                    {selected.has(key) ? <CheckSquare className="h-4 w-4 text-primary" /> : <Square className="h-4 w-4" />}
                  </button>
                </TableCell>
                <TableCell className="font-medium">{p.metadata.name}</TableCell>
                <TableCell>
                  {enfMode !== '—' ? (
                    <Badge variant="outline" className={cn('text-[10px]',
                      enfMode === 'blocking' ? 'bg-destructive/10 text-destructive border-destructive/30'
                      : 'bg-warning/10 text-warning border-warning/30'
                    )}>{enfMode}</Badge>
                  ) : <span className="text-xs text-muted-foreground">—</span>}
                </TableCell>
                <TableCell>
                  <Badge variant="outline" className={cn('text-[10px]', getBundleStateBadgeClass(p.status?.bundle?.state))}>
                    {p.status?.bundle?.state ?? 'unknown'}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">{p.metadata.namespace}</TableCell>
                <TableCell className="text-xs">{formatAge(p.metadata.creationTimestamp)}</TableCell>
                <TableCell>
                  <div className="flex items-center gap-1">
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-muted-foreground hover:text-primary" title="Edit"
                      onClick={(e) => { e.stopPropagation(); setEditingPolicy(p); }}>
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-muted-foreground hover:text-warning" title="Force Recompile — trigger a new compilation cycle"
                      disabled={recompile.isPending}
                      onClick={(e) => { e.stopPropagation(); recompile.mutate({ name: p.metadata.name, namespace: p.metadata.namespace ?? namespace }); }}>
                      <RefreshCcw className="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-muted-foreground hover:text-success" title="Export JSON"
                      onClick={(e) => { e.stopPropagation(); exportCR(p, p.metadata.name, 'APPolicy'); }}>
                      <Download className="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive" title="Delete"
                      onClick={(e) => { e.stopPropagation(); setDeletingPolicy(p); }}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ); })}
          </TableBody>
        </Table>
      )}
      {!isLoading && policies.length > 0 && <TableCount count={filtered.length} label="policy" />}

      {/* Detail sheet (read-only) */}
      <Sheet open={!!selectedPolicy} onOpenChange={(open) => !open && setSelectedPolicy(null)}>
        <SheetContent className="w-full sm:max-w-[75vw] overflow-y-auto p-0">
          <SheetHeader className="px-4 pt-4 pb-0"><SheetTitle>{selectedPolicy?.metadata.name}</SheetTitle></SheetHeader>
          {selectedPolicy && <WafPolicyDetail resource={selectedPolicy} isDark={isDark} clusterId={clusterId} />}
        </SheetContent>
      </Sheet>

      {/* Edit sheet */}
      <Sheet open={!!editingPolicy} onOpenChange={(open) => !open && setEditingPolicy(null)}>
        <SheetContent className="w-full sm:max-w-[75vw] overflow-y-auto p-0">
          <SheetHeader className="px-4 pt-4 pb-0"><SheetTitle>Edit Policy — {editingPolicy?.metadata.name}</SheetTitle></SheetHeader>
          {editingPolicy && (
            <APPolicyForm
              key={`${editingPolicy.metadata.namespace}/${editingPolicy.metadata.name}/${editingPolicy.metadata.uid}`}
              clusterId={clusterId}
              namespace={namespace}
              existingItem={editingPolicy}
              onClose={() => setEditingPolicy(null)}
            />
          )}
        </SheetContent>
      </Sheet>

      <DestructiveConfirmDialog
        open={!!deletingPolicy}
        onOpenChange={(open) => !open && setDeletingPolicy(null)}
        title={`Delete WAF Policy "${deletingPolicy?.metadata.name}"?`}
        description="Deletes the APPolicy CR. The compiled bundle will be cleaned up by the Policy Controller."
        confirmText={deletingPolicy?.metadata.name ?? ''}
        isPending={deletePolicy.isPending}
        onConfirm={() => {
          if (!deletingPolicy) return;
          deletePolicy.mutate({ name: deletingPolicy.metadata.name, namespace: deletingPolicy.metadata.namespace ?? namespace }, { onSuccess: () => setDeletingPolicy(null) });
        }}
      />
    </div>
  );
}

// ============================================================================
// Tab 3: Signature Settings (APSignatures singleton)
// ============================================================================

function SignatureSettingsTab({ clusterId, namespace, isDark }: { clusterId: number; namespace: string; isDark: boolean }) {
  const { data: sigData, isLoading, refetch } = useWafSignatures(clusterId, namespace, { enabled: !!clusterId });
  const upsert = useUpsertWafSignatures(clusterId);
  const deleteSig = useDeleteWafSignatures(clusterId);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  type SigStatus = { errors?: string; 'attack-signatures'?: { installedRevision?: string }; 'bot-signatures'?: { installedRevision?: string }; 'threat-campaigns'?: { installedRevision?: string }; installationState?: string };
  type SigData = { spec?: { 'attack-signatures'?: { revision?: string }; 'bot-signatures'?: { revision?: string }; 'threat-campaigns'?: { revision?: string } }; status?: SigStatus } | null;
  const existing = sigData as SigData;

  const [attack, setAttack] = useState('latest');
  const [bot,    setBot]    = useState('latest');
  const [threat, setThreat] = useState('latest');

  useEffect(() => {
    if (!existing) return;
    setAttack(existing.spec?.['attack-signatures']?.revision ?? 'latest');
    setBot(existing.spec?.['bot-signatures']?.revision    ?? 'latest');
    setThreat(existing.spec?.['threat-campaigns']?.revision  ?? 'latest');
  }, [existing]);

  const installState = existing?.status?.installationState;
  const sigErrors    = existing?.status?.errors;

  const handleSave = async () => {
    setSaveError(null);
    try {
      await upsert.mutateAsync({ namespace, spec: { 'attack-signatures': { revision: attack }, 'bot-signatures': { revision: bot }, 'threat-campaigns': { revision: threat } } });
    } catch (e) {
      setSaveError(extractApiError(e));
    }
  };

  const installedAttack = existing?.status?.['attack-signatures']?.installedRevision;
  const installedBot    = existing?.status?.['bot-signatures']?.installedRevision;
  const installedThreat = existing?.status?.['threat-campaigns']?.installedRevision;

  return (
    <div className="space-y-4 max-w-xl">
      {/* What is this? */}
      <div className={cn('rounded-md border p-3 text-xs flex gap-2', isDark ? 'border-border bg-card/50' : 'border-primary/10 bg-primary/10')}>
        <Info className="h-3.5 w-3.5 mt-0.5 shrink-0 text-primary" />
        <div className={isDark ? 'text-foreground/80' : 'text-primary'}>
          <strong>APSignatures</strong> is a <strong>single resource per namespace</strong> (named{' '}
          <span className="font-mono">apsignatures</span>). It controls which version of the NGINX App Protect
          signature packages the Policy Controller downloads and embeds when compiling any policy in this namespace.
          Changing a revision here triggers recompilation of <em>all</em> policies in <em>{namespace}</em>.
          <br /><br />
          <strong>Revision format:</strong> use <code className="px-1 rounded bg-primary/10 dark:bg-muted">latest</code> for the newest
          available package, or a specific date-based tag like{' '}
          <code className="px-1 rounded bg-primary/10 dark:bg-muted">2026.07.31</code> to pin to a known-good version.
        </div>
      </div>

      <div className="flex items-center justify-between">
        <h4 className={cn('text-sm font-semibold', isDark ? 'text-white' : 'text-foreground')}>Signature Package Versions</h4>
        <RefreshButton refetch={refetch} isLoading={isLoading} />
      </div>

      {isLoading && <p className="text-xs text-muted-foreground">Loading…</p>}

      {!isLoading && (
        <>
          {/* Signature download failure + fix instructions */}
          {installState === 'failure' && (
            <div className={cn('rounded-md border p-3 space-y-2', isDark ? 'border-destructive/20 bg-destructive/20/20' : 'border-destructive/20 bg-destructive/10')}>
              <div className="flex items-start gap-1.5">
                <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0 text-destructive" />
                <div className="text-xs text-destructive dark:text-destructive/80">
                  <strong>Signature download failed.</strong> The Policy Controller cannot pull packages from the NGINX repository.
                  {sigErrors && <p className="mt-1 font-mono text-[11px] break-all">{sigErrors}</p>}
                </div>
              </div>
              <div className={cn('text-xs rounded p-2 space-y-1', isDark ? 'bg-card text-foreground/80' : 'bg-white text-foreground/80')}>
                <p className="font-semibold">How to fix: create the NGINX repo secret</p>
                <p>You need your F5/NGINX entitlement certificate and key. Run on the cluster:</p>
                <pre className={cn('mt-1 p-2 rounded text-[11px] overflow-x-auto', isDark ? 'bg-card' : 'bg-muted')}>
{`kubectl create secret generic nginx-repo-secret \\
  --from-file=nginx-repo.crt=/path/to/nginx-repo.crt \\
  --from-file=nginx-repo.key=/path/to/nginx-repo.key \\
  -n ${namespace}`}
                </pre>
                <p className="text-xs text-muted-foreground">Then ensure the waf-policy-controller Helm chart references this secret via <code>nginxRepoSecret</code>.</p>
              </div>
            </div>
          )}

          {installState === 'success' && (
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-[10px] bg-success/10 text-success border-success/50/20">Installed</Badge>
              <span className={cn('text-xs', isDark ? 'text-muted-foreground' : 'text-muted-foreground')}>Signature packages are downloaded and available for compilation.</span>
            </div>
          )}

          {/* Revision inputs with installed-version hint */}
          {[
            { label: 'Attack Signatures', key: 'attack' as const, value: attack, setter: setAttack, installed: installedAttack, hint: 'NAP attack pattern signatures (SQLi, XSS, RCE, etc.)' },
            { label: 'Bot Signatures',    key: 'bot'    as const, value: bot,    setter: setBot,    installed: installedBot,    hint: 'Bot and crawler fingerprint signatures' },
            { label: 'Threat Campaigns',  key: 'threat' as const, value: threat, setter: setThreat, installed: installedThreat, hint: 'Active threat campaign IOCs (IP/URI blocklists)' },
          ].map(({ label, value, setter, installed, hint }) => (
            <div key={label} className="space-y-1">
              <Label>{label}</Label>
              <Input value={value} onChange={(e) => setter(e.target.value)} placeholder="latest" />
              <div className="flex items-center justify-between">
                <p className="text-xs text-muted-foreground">{hint}</p>
                {installed && (
                  <p className="text-xs text-muted-foreground">Installed: <span className="font-mono">{installed}</span></p>
                )}
              </div>
            </div>
          ))}

          {saveError && <InlineError message={saveError} />}
          <div className="flex flex-wrap gap-2 pt-1">
            <Button size="sm" className="gap-1.5 bg-primary hover:bg-primary/90" onClick={handleSave} disabled={upsert.isPending}>
              <RefreshCw className="h-3.5 w-3.5" /> Apply &amp; Recompile All Policies
            </Button>
            <Button size="sm" variant="ghost" onClick={() => { setAttack(existing?.spec?.['attack-signatures']?.revision ?? 'latest'); setBot(existing?.spec?.['bot-signatures']?.revision ?? 'latest'); setThreat(existing?.spec?.['threat-campaigns']?.revision ?? 'latest'); setSaveError(null); }}>Reset</Button>
            {existing && (
              <Button size="sm" variant="ghost" className="gap-1.5 text-success hover:text-success"
                onClick={() => exportCR(sigData, 'apsignatures', 'APSignatures')} title="Export as JSON">
                <Download className="h-3.5 w-3.5" /> Export JSON
              </Button>
            )}
            {existing && (
              <Button size="sm" variant="ghost" className="gap-1.5 text-destructive hover:text-destructive"
                onClick={() => setConfirmDelete(true)} title="Delete the APSignatures CR from this namespace">
                <Trash2 className="h-3.5 w-3.5" /> Delete CR
              </Button>
            )}
          </div>
          <DestructiveConfirmDialog
            open={confirmDelete}
            onOpenChange={(open) => !open && setConfirmDelete(false)}
            title='Delete APSignatures "apsignatures"?'
            description={`Removes the APSignatures CR from namespace "${namespace}". All policies in this namespace will lose their signature package reference and may need to be recompiled after a new APSignatures CR is created.`}
            confirmText="apsignatures"
            isPending={deleteSig.isPending}
            onConfirm={() => deleteSig.mutate({ namespace }, { onSuccess: () => setConfirmDelete(false) })}
          />
        </>
      )}
    </div>
  );
// ============================================================================
// Tab 4: User Signatures (APUserSig) — custom attack signatures
// ============================================================================

} // end SignatureSettingsTab

function UserSigsTab({ clusterId, namespace, isDark }: { clusterId: number; namespace: string; isDark: boolean }) {
  const [showCreate, setShowCreate]     = useState(false);
  const [viewingItem, setViewingItem]   = useState<APUserSigResource | null>(null);
  const [editingItem, setEditingItem]   = useState<APUserSigResource | null>(null);
  const [deletingItem, setDeletingItem] = useState<APUserSigResource | null>(null);
  const [pills, setPills] = useState<{ field: string; value: string }[]>([]);

  const { data, isLoading, refetch } = useWafUserSigs(clusterId, namespace, { enabled: !!clusterId });
  const userSigs = useMemo(() => data?.user_sigs ?? [], [data]);
  const deleteSig = useDeleteWafUserSig(clusterId);
  const filtered = useMemo(() => userSigs.filter((us: APUserSigResource) =>
    pills.every(p => {
      if (p.field === 'name') return us.metadata.name.toLowerCase().includes(p.value.toLowerCase());
      if (p.field === 'tag') return (us.spec?.tag ?? '').toLowerCase().includes(p.value.toLowerCase());
      return true;
    })
  ), [userSigs, pills]);

  return (
    <div className="space-y-3">
      <TabToolbar
        filterDef={[
          { field: 'name', label: 'Name' },
          { field: 'tag',  label: 'Tag' },
        ]}
        pills={pills}
        onAddPill={(p) => setPills(prev => [...prev, p])}
        onRemovePill={(i) => setPills(prev => prev.filter((_, idx) => idx !== i))}
        onRefresh={refetch} isLoading={isLoading}
        onCreate={() => setShowCreate(!showCreate)}
        createLabel={showCreate ? 'Cancel' : 'Create User Signature'}
      />

      {showCreate && (
        <div className={cn('rounded-lg border', isDark ? 'border-border bg-card/50' : 'border-border bg-white')}>
          <APUserSigForm clusterId={clusterId} namespace={namespace} onClose={() => setShowCreate(false)} />
        </div>
      )}

      {isLoading && <SkeletonTable rows={3} columns={5} />}
      {!isLoading && userSigs.length === 0 && (
        <EmptyState icon={PenLine} title="No user signatures yet" description="Create a custom attack signature that your WAF policies can reference." />
      )}
      {userSigs.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Tag</TableHead>
              <TableHead>Signatures</TableHead>
              <TableHead>Install State</TableHead>
              <TableHead>Age</TableHead>
              <TableHead className="w-20" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((us: APUserSigResource) => (
              <TableRow key={`${us.metadata.namespace}/${us.metadata.name}`} className="cursor-pointer" onClick={() => setViewingItem(us)}>
                <TableCell className="font-medium">{us.metadata.name}</TableCell>
                <TableCell className="text-xs font-mono">{us.spec?.tag ?? '—'}</TableCell>
                <TableCell className="text-xs">{(us.spec?.signatures ?? []).length}</TableCell>
                <TableCell>
                  <Badge variant="outline" className={cn('text-[10px]',
                    us.status?.installationState === 'success' ? 'bg-success/10 text-success border-success/50/20'
                    : us.status?.installationState === 'failure' ? 'bg-destructive/10 text-destructive border-destructive/50/20'
                    : 'bg-muted-foreground/20/10 text-muted-foreground border-muted-foreground/30/20')}>
                    {us.status?.installationState ?? 'unknown'}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs">{formatAge(us.metadata.creationTimestamp)}</TableCell>
                <TableCell>
                  <div className="flex items-center gap-1">
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-muted-foreground hover:text-primary" title="Edit"
                      onClick={(e) => { e.stopPropagation(); setEditingItem(us); }}>
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-muted-foreground hover:text-success" title="Export JSON"
                      onClick={(e) => { e.stopPropagation(); exportCR(us, us.metadata.name, 'APUserSig'); }}>
                      <Download className="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive" title="Delete"
                      onClick={(e) => { e.stopPropagation(); setDeletingItem(us); }}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {!isLoading && userSigs.length > 0 && <TableCount count={filtered.length} label="user signature" />}

      {/* Detail (view) sheet */}
      <Sheet open={!!viewingItem} onOpenChange={(open) => !open && setViewingItem(null)}>
        <SheetContent className="w-full sm:max-w-xl overflow-y-auto">
          <SheetHeader><SheetTitle>{viewingItem?.metadata.name}</SheetTitle></SheetHeader>
          {viewingItem && <div className="mt-4"><APUserSigDetail resource={viewingItem} /></div>}
        </SheetContent>
      </Sheet>

      {/* Edit sheet */}
      <Sheet open={!!editingItem} onOpenChange={(open) => !open && setEditingItem(null)}>
        <SheetContent className="w-full sm:max-w-xl overflow-y-auto p-0">
          <SheetHeader className="px-4 pt-4 pb-0"><SheetTitle>Edit User Signature — {editingItem?.metadata.name}</SheetTitle></SheetHeader>
          {editingItem && (
            <APUserSigForm
              key={`${editingItem.metadata.namespace}/${editingItem.metadata.name}/${editingItem.metadata.uid}`}
              clusterId={clusterId}
              namespace={namespace}
              existingItem={editingItem}
              onClose={() => setEditingItem(null)}
            />
          )}
        </SheetContent>
      </Sheet>

      <DestructiveConfirmDialog
        open={!!deletingItem}
        onOpenChange={(open) => !open && setDeletingItem(null)}
        title={`Delete User Signature "${deletingItem?.metadata.name}"?`}
        description="Policies that reference this signature's tag will lose it on next recompile."
        confirmText={deletingItem?.metadata.name ?? ''}
        isPending={deleteSig.isPending}
        onConfirm={() => {
          if (!deletingItem) return;
          deleteSig.mutate({ name: deletingItem.metadata.name, namespace: deletingItem.metadata.namespace ?? namespace }, { onSuccess: () => setDeletingItem(null) });
        }}
      />
    </div>
  );
}

// ============================================================================
// Main page
// ============================================================================
// Gateway WAF Tab
// ============================================================================

type GwSubTab = 'gateways' | 'waf-profiles' | 'httproutes' | 'ref-grants' | 'overview';

function StatusBadge({ conditions }: { conditions?: { type: string; status: string; message?: string }[] }) {
  const prog = conditions?.find(c => c.type === 'Programmed' || c.type === 'Ready' || c.type === 'Accepted');
  if (!prog) return <span className="text-[10px] text-muted-foreground px-1.5 py-0.5 rounded border">—</span>;
  const ok = prog.status === 'True';
  return (
    <span className={cn('text-[10px] px-1.5 py-0.5 rounded border', ok ? 'text-success border-success/30 bg-success/10' : 'text-destructive border-destructive/30 bg-destructive/10')}>
      {ok ? prog.type : `Not ${prog.type}`}
    </span>
  );
}

function GatewayWafTab({ clusterId, namespace }: { clusterId: number; namespace: string }) {
  const [sub, setSub] = useState<GwSubTab>('overview');

  // Data
  const { data: gateways    = [], isLoading: gwLoading   } = useGateways(clusterId, undefined);
  const { data: profiles    = [], isLoading: profLoading } = useSecurityProfiles(clusterId, undefined);
  const { data: routes      = [], isLoading: rtLoading   } = useHTTPRoutes(clusterId, undefined);
  const { data: grants      = [], isLoading: grLoading   } = useReferenceGrants(clusterId, undefined);
  const { data: gwClasses   = [] }                         = useGatewayClasses(clusterId);
  const { data: policiesData }  = useWafPolicies(clusterId, undefined);
  const policies = policiesData?.policies ?? [];

  // Mutations
  const createGw    = useCreateGateway(clusterId);
  const updateGw    = useUpdateGateway(clusterId);
  const deleteGw    = useDeleteGateway(clusterId);
  const createProf  = useCreateSecurityProfile(clusterId);
  const updateProf  = useUpdateSecurityProfile(clusterId);
  const deleteProf  = useDeleteSecurityProfile(clusterId);
  const createRt    = useCreateHTTPRoute(clusterId);
  const updateRt    = useUpdateHTTPRoute(clusterId);
  const deleteRt    = useDeleteHTTPRoute(clusterId);
  const createGr    = useCreateReferenceGrant(clusterId);
  const deleteGr    = useDeleteReferenceGrant(clusterId);
  const createGc    = useCreateGatewayClass(clusterId);
  const deleteGc    = useDeleteGatewayClass(clusterId);

  // Form state — Gateway
  const [showGwForm, setShowGwForm]         = useState(false);
  const [editingGw, setEditingGw]           = useState<Gateway | null>(null);
  const [gwName, setGwName]                 = useState('');
  const [gwNs, setGwNs]                     = useState(namespace);
  const [gwClass, setGwClass]               = useState('f5-gatewayclass');
  const [gwListenerPort, setGwListenerPort] = useState('80');
  const [gwListenerProto, setGwListenerProto] = useState('HTTP');
  const [gwListenerName, setGwListenerName] = useState('http');
  const [gwAllowed, setGwAllowed]           = useState<'Same'|'All'>('Same');
  const [gwAddrs, setGwAddrs]               = useState('');
  const [gwWafProfile, setGwWafProfile]     = useState('');

  // Form state — WAF Profile
  const [showProfForm, setShowProfForm]   = useState(false);
  const [editingProf, setEditingProf]     = useState<SecurityProfile | null>(null);
  const [profName, setProfName]           = useState('');
  const [profNs, setProfNs]               = useState(namespace);
  const [profPolicy, setProfPolicy]       = useState('');

  // Form state — HTTPRoute
  const [showRtForm, setShowRtForm]     = useState(false);
  const [editingRt, setEditingRt]       = useState<HTTPRoute | null>(null);
  const [rtName, setRtName]             = useState('');
  const [rtNs, setRtNs]                 = useState(namespace);
  const [rtGateway, setRtGateway]       = useState('');
  const [rtGwNs, setRtGwNs]             = useState('');
  const [rtSection, setRtSection]       = useState('');
  const [rtHostnames, setRtHostnames]   = useState('');
  const [rtBackend, setRtBackend]       = useState('');
  const [rtBackendPort, setRtBackendPort] = useState('80');
  const [rtBackendNs, setRtBackendNs]   = useState('');
  const [rtPath, setRtPath]             = useState('/');

  // Form state — ReferenceGrant
  const [showGrForm, setShowGrForm]   = useState(false);
  const [grName, setGrName]           = useState('');
  const [grNs, setGrNs]               = useState(namespace);
  const [grFromNs, setGrFromNs]       = useState('');
  const [grFromKind, setGrFromKind]   = useState('HTTPRoute');
  const [grToKind, setGrToKind]       = useState('Service');
  const [grToName, setGrToName]       = useState('');

  // Form state — GatewayClass
  const [showGcForm, setShowGcForm]   = useState(false);
  const [gcName, setGcName]           = useState('f5-gatewayclass');
  const [gcController, setGcController] = useState('f5.com/default-f5-cne-controller');

  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  function flash(msg: string, isErr = false) {
    if (isErr) { setError(msg); setTimeout(() => setError(null), 5000); }
    else { setSuccess(msg); setTimeout(() => setSuccess(null), 3000); }
  }

  function resetGwForm() { setGwName(''); setGwNs(namespace); setGwClass('f5-gatewayclass'); setGwListenerPort('80'); setGwListenerProto('HTTP'); setGwListenerName('http'); setGwAllowed('Same'); setGwAddrs(''); setGwWafProfile(''); setEditingGw(null); setShowGwForm(false); }
  function openEditGw(gw: Gateway) {
    const l = gw.spec.listeners[0];
    setGwName(gw.metadata.name); setGwNs(gw.metadata.namespace);
    setGwClass(gw.spec.gatewayClassName);
    setGwListenerName(l?.name ?? 'http'); setGwListenerPort(String(l?.port ?? 80)); setGwListenerProto(l?.protocol ?? 'HTTP');
    setGwAllowed((l?.allowedRoutes?.namespaces?.from ?? 'Same') as 'Same'|'All');
    setGwAddrs((gw.spec.addresses ?? []).map(a => a.value).join(','));
    setGwWafProfile(gw.metadata.annotations?.[WSP_ANNOTATION_KEY] ?? '');
    setEditingGw(gw); setShowGwForm(true);
  }

  const WSP_ANNOTATION_KEY = 'k8s.f5net.com/web-security-profile';

  async function submitGw() {
    if (!gwName.trim()) { flash('Name is required', true); return; }
    const payload = {
      name: gwName.trim(), namespace: gwNs,
      gateway_class_name: gwClass,
      listeners: [{ name: gwListenerName, protocol: gwListenerProto, port: parseInt(gwListenerPort), allowed_routes_from: gwAllowed }],
      addresses: gwAddrs.split(',').map(s => s.trim()).filter(Boolean),
      waf_profile_name: gwWafProfile.trim() || undefined,
      annotations: {},
    };
    try {
      if (editingGw) { await updateGw.mutateAsync({ name: gwName.trim(), payload: { ...payload } }); flash('Gateway updated'); }
      else           { await createGw.mutateAsync(payload); flash('Gateway created'); }
      resetGwForm();
    } catch (e: unknown) { flash((e as Error).message ?? 'Failed', true); }
  }

  function resetProfForm() { setProfName(''); setProfNs(namespace); setProfPolicy(''); setEditingProf(null); setShowProfForm(false); }
  function openEditProf(p: SecurityProfile) { setProfName(p.metadata.name); setProfNs(p.metadata.namespace); setProfPolicy(p.spec.policyName); setEditingProf(p); setShowProfForm(true); }
  async function submitProf() {
    if (!profName.trim() || !profPolicy.trim()) { flash('Name and APPolicy name are required', true); return; }
    try {
      if (editingProf) { await updateProf.mutateAsync({ name: profName.trim(), payload: { namespace: profNs, policy_name: profPolicy.trim() } }); flash('WAF Profile updated'); }
      else             { await createProf.mutateAsync({ name: profName.trim(), namespace: profNs, policy_name: profPolicy.trim() }); flash('WAF Profile created'); }
      resetProfForm();
    } catch (e: unknown) { flash((e as Error).message ?? 'Failed', true); }
  }

  function resetRtForm() { setRtName(''); setRtNs(namespace); setRtGateway(''); setRtGwNs(''); setRtSection(''); setRtHostnames(''); setRtBackend(''); setRtBackendPort('80'); setRtBackendNs(''); setRtPath('/'); setEditingRt(null); setShowRtForm(false); }
  function openEditRt(r: HTTPRoute) {
    setRtName(r.metadata.name); setRtNs(r.metadata.namespace);
    const pr = r.spec.parentRefs[0];
    setRtGateway(pr?.name ?? ''); setRtGwNs(pr?.namespace ?? ''); setRtSection(pr?.sectionName ?? '');
    setRtHostnames((r.spec.hostnames ?? []).join(','));
    const br = r.spec.rules[0]?.backendRefs[0];
    setRtBackend(br?.name ?? ''); setRtBackendPort(String(br?.port ?? 80)); setRtBackendNs(br?.namespace ?? '');
    setRtPath(r.spec.rules[0]?.matches?.[0]?.path?.value ?? '/');
    setEditingRt(r); setShowRtForm(true);
  }
  async function submitRt() {
    if (!rtName.trim() || !rtGateway.trim() || !rtBackend.trim()) { flash('Name, Gateway, and Backend service are required', true); return; }
    const rule = {
      matches: [{ path_type: 'PathPrefix', path_value: rtPath || '/' }],
      backend_refs: [{ name: rtBackend.trim(), port: parseInt(rtBackendPort), ...(rtBackendNs.trim() ? { namespace: rtBackendNs.trim() } : {}) }],
    };
    const payload = {
      name: rtName.trim(), namespace: rtNs,
      parent_gateway_name: rtGateway.trim(),
      ...(rtGwNs.trim() ? { parent_gateway_namespace: rtGwNs.trim() } : {}),
      ...(rtSection.trim() ? { parent_gateway_section_name: rtSection.trim() } : {}),
      hostnames: rtHostnames.split(',').map(s => s.trim()).filter(Boolean),
      rules: [rule],
    };
    try {
      if (editingRt) { await updateRt.mutateAsync({ name: rtName.trim(), payload: { ...payload } }); flash('HTTPRoute updated'); }
      else           { await createRt.mutateAsync(payload); flash('HTTPRoute created'); }
      resetRtForm();
    } catch (e: unknown) { flash((e as Error).message ?? 'Failed', true); }
  }

  function resetGrForm() { setGrName(''); setGrNs(namespace); setGrFromNs(''); setGrFromKind('HTTPRoute'); setGrToKind('Service'); setGrToName(''); setShowGrForm(false); }
  async function submitGr() {
    if (!grName.trim() || !grNs.trim() || !grFromNs.trim()) { flash('Name, target namespace, and source namespace are required', true); return; }
    try {
      await createGr.mutateAsync({ name: grName.trim(), namespace: grNs.trim(), from_namespace: grFromNs.trim(), from_kind: grFromKind, to_kind: grToKind, ...(grToName.trim() ? { to_name: grToName.trim() } : {}) });
      flash('ReferenceGrant created'); resetGrForm();
    } catch (e: unknown) { flash((e as Error).message ?? 'Failed', true); }
  }

  function resetGcForm() { setGcName('f5-gatewayclass'); setGcController('f5.com/default-f5-cne-controller'); setShowGcForm(false); }
  async function submitGc() {
    if (!gcName.trim()) { flash('Name is required', true); return; }
    try { await createGc.mutateAsync({ name: gcName.trim(), controller_name: gcController.trim() }); flash('GatewayClass created'); resetGcForm(); }
    catch (e: unknown) { flash((e as Error).message ?? 'Failed', true); }
  }

  // ─── sub-tabs ───────────────────────────────────────────────────────────────
  const SUB_TABS: { key: GwSubTab; label: string; icon: typeof Globe; count?: number }[] = [
    { key: 'overview',    label: 'Overview',         icon: Network,  count: gateways.length },
    { key: 'gateways',    label: 'Gateways',         icon: Globe,    count: gateways.length },
    { key: 'waf-profiles',label: 'WAF Profiles',     icon: Shield,   count: profiles.length },
    { key: 'httproutes',  label: 'HTTP Routes',      icon: Route,    count: routes.length },
    { key: 'ref-grants',  label: 'Reference Grants', icon: Lock,     count: grants.length },
  ];

  return (
    <div className="space-y-4">
      {/* Notifications */}
      {error   && <div className="rounded-md bg-destructive/10 border border-destructive/30 text-destructive text-xs px-3 py-2">{error}</div>}
      {success && <div className="rounded-md bg-success/10 border border-success/30 text-success text-xs px-3 py-2">{success}</div>}

      {/* Architecture note */}
      <div className="rounded-md border border-border bg-muted/20 px-4 py-3 text-xs text-muted-foreground flex gap-2">
        <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
        <span>
          WAF Gateway API binding chain: <strong>APPolicy</strong> → <strong>WAF Profile</strong> (F5BigWebSecurityProfile annotated on) → <strong>Gateway</strong> ← <strong>HTTPRoute</strong>.
          All Gateway + HTTPRoute traffic inherits WAF enforcement automatically.
        </span>
      </div>

      {/* Sub-tab bar */}
      <div className="flex gap-1 border-b border-border">
        {SUB_TABS.map(({ key, label, icon: Icon, count }) => (
          <button
            key={key}
            onClick={() => setSub(key)}
            className={cn('flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-t border-b-2 -mb-px transition-colors whitespace-nowrap',
              sub === key ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground hover:border-border')}
          >
            <Icon className="h-3.5 w-3.5" /> {label}
            {count !== undefined && <span className="ml-0.5 rounded-full text-[9px] bg-muted px-1">{count}</span>}
          </button>
        ))}
      </div>

      {/* ── Overview ─────────────────────────────────────────────────────── */}
      {sub === 'overview' && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {[
              { label: 'GatewayClasses', value: gwClasses.length, icon: Globe },
              { label: 'Gateways',       value: gateways.length,  icon: Globe },
              { label: 'WAF Profiles',   value: profiles.length,  icon: Shield },
              { label: 'HTTP Routes',    value: routes.length,    icon: Route },
            ].map(({ label, value, icon: Icon }) => (
              <div key={label} className="rounded-lg border border-border bg-card p-4 flex items-center gap-3">
                <Icon className="h-5 w-5 text-muted-foreground" />
                <div><p className="text-2xl font-bold tabular-nums">{value}</p><p className="text-xs text-muted-foreground">{label}</p></div>
              </div>
            ))}
          </div>

          {/* Binding chain view */}
          <div className="rounded-lg border border-border bg-card p-4 space-y-3">
            <h3 className="text-sm font-semibold">Binding Chain</h3>
            {gateways.length === 0 ? (
              <p className="text-xs text-muted-foreground">No Gateways yet. Create a Gateway and attach a WAF Profile to start.</p>
            ) : (
              <div className="space-y-3">
                {gateways.map(gw => {
                  const gwAnno  = gw.metadata.annotations?.[WSP_ANNOTATION_KEY];
                  const prof    = gwAnno ? profiles.find(p => p.metadata.name === gwAnno && p.metadata.namespace === gw.metadata.namespace) : undefined;
                  const gwRoutes = routes.filter(r => r.spec.parentRefs.some(pr => pr.name === gw.metadata.name && (!pr.namespace || pr.namespace === gw.metadata.namespace)));
                  return (
                    <div key={`${gw.metadata.namespace}/${gw.metadata.name}`} className="rounded-md border border-border p-3 space-y-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Globe className="h-3.5 w-3.5 text-primary shrink-0" />
                        <span className="text-xs font-semibold">{gw.metadata.namespace}/{gw.metadata.name}</span>
                        <StatusBadge conditions={gw.status?.conditions} />
                        <span className="text-[10px] text-muted-foreground">{gw.spec.gatewayClassName}</span>
                        {gw.spec.listeners.map(l => (
                          <span key={l.name} className="text-[10px] px-1.5 py-0.5 rounded bg-muted">{l.protocol}:{l.port}</span>
                        ))}
                      </div>
                      {/* WAF Profile chain */}
                      {gwAnno ? (
                        <div className="flex items-start gap-1.5 ml-5">
                          <ArrowRight className="h-3 w-3 text-muted-foreground shrink-0 mt-0.5" />
                          <div className="text-[10px] space-y-0.5">
                            <div className="flex items-center gap-1.5">
                              <Shield className="h-3 w-3 text-warning" />
                              <span className="font-medium text-warning">WAF Profile:</span>
                              <span className="font-mono">{gwAnno}</span>
                              {prof ? (
                                <span className="text-muted-foreground">→ APPolicy: <span className="font-mono">{prof.spec.policyName}</span></span>
                              ) : (
                                <span className="text-destructive">(profile not found in {gw.metadata.namespace})</span>
                              )}
                            </div>
                          </div>
                        </div>
                      ) : (
                        <div className="ml-5 text-[10px] text-muted-foreground/60 flex items-center gap-1">
                          <AlertTriangle className="h-3 w-3" /> No WAF Profile attached
                        </div>
                      )}
                      {/* HTTPRoutes */}
                      {gwRoutes.length > 0 && (
                        <div className="ml-5 space-y-1">
                          {gwRoutes.map(r => (
                            <div key={`${r.metadata.namespace}/${r.metadata.name}`} className="flex items-center gap-1.5">
                              <ArrowRight className="h-3 w-3 text-muted-foreground shrink-0" />
                              <Route className="h-3 w-3 text-blue-500 shrink-0" />
                              <span className="text-[10px] font-mono">{r.metadata.namespace}/{r.metadata.name}</span>
                              {(r.spec.hostnames ?? []).map(h => <span key={h} className="text-[10px] px-1 py-0.5 rounded bg-muted">{h}</span>)}
                              <StatusBadge conditions={r.status?.parents?.[0]?.conditions} />
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Gateways ─────────────────────────────────────────────────────── */}
      {sub === 'gateways' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">Gateways</h3>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" className="h-8 gap-1.5" onClick={() => { resetGcForm(); setShowGcForm(true); }}><Plus className="h-3.5 w-3.5" /> GatewayClass</Button>
              <Button size="sm" className="h-8 gap-1.5" onClick={() => { resetGwForm(); setShowGwForm(true); }}><Plus className="h-3.5 w-3.5" /> Gateway</Button>
            </div>
          </div>

          {/* GatewayClass list */}
          {gwClasses.length > 0 && (
            <div className="rounded-md border border-border overflow-hidden">
              <div className="px-3 py-2 bg-muted/30 text-xs font-medium text-muted-foreground flex items-center gap-1.5"><Globe className="h-3 w-3" /> Gateway Classes</div>
              <table className="w-full text-xs">
                <thead><tr className="border-b border-border text-muted-foreground"><th className="text-left px-3 py-1.5">Name</th><th className="text-left px-3 py-1.5">Controller</th><th className="text-left px-3 py-1.5">Status</th><th className="px-3 py-1.5"></th></tr></thead>
                <tbody>
                  {gwClasses.map(gc => (
                    <tr key={gc.metadata.name} className="border-b border-border/40 last:border-0 hover:bg-muted/20">
                      <td className="px-3 py-2 font-mono font-medium">{gc.metadata.name}</td>
                      <td className="px-3 py-2 text-muted-foreground">{gc.spec.controllerName}</td>
                      <td className="px-3 py-2"><StatusBadge conditions={gc.status?.conditions} /></td>
                      <td className="px-3 py-2 text-right">
                        <Button size="sm" variant="ghost" className="h-6 w-6 p-0 text-destructive hover:text-destructive" onClick={() => { if (window.confirm(`Delete GatewayClass "${gc.metadata.name}"?`)) deleteGc.mutate(gc.metadata.name); }}><Trash2 className="h-3 w-3" /></Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Gateway list */}
          <div className="rounded-md border border-border overflow-hidden">
            <table className="w-full text-xs">
              <thead><tr className="border-b border-border text-muted-foreground bg-muted/30"><th className="text-left px-3 py-1.5">Name</th><th className="text-left px-3 py-1.5">Namespace</th><th className="text-left px-3 py-1.5">Class</th><th className="text-left px-3 py-1.5">Listeners</th><th className="text-left px-3 py-1.5">WAF Profile</th><th className="text-left px-3 py-1.5">Status</th><th className="px-3 py-1.5"></th></tr></thead>
              <tbody>
                {gwLoading && <tr><td colSpan={7} className="px-3 py-4 text-center text-muted-foreground">Loading…</td></tr>}
                {!gwLoading && gateways.length === 0 && <tr><td colSpan={7} className="px-3 py-4 text-center text-muted-foreground">No Gateways. Click "+ Gateway" to create one.</td></tr>}
                {gateways.map(gw => (
                  <tr key={`${gw.metadata.namespace}/${gw.metadata.name}`} className="border-b border-border/40 last:border-0 hover:bg-muted/20">
                    <td className="px-3 py-2 font-mono font-medium">{gw.metadata.name}</td>
                    <td className="px-3 py-2 text-muted-foreground">{gw.metadata.namespace}</td>
                    <td className="px-3 py-2 text-muted-foreground">{gw.spec.gatewayClassName}</td>
                    <td className="px-3 py-2">{gw.spec.listeners.map(l => <span key={l.name} className="mr-1 text-[10px] px-1.5 py-0.5 rounded bg-muted">{l.protocol}:{l.port}</span>)}</td>
                    <td className="px-3 py-2 font-mono text-[10px]">{gw.metadata.annotations?.[WSP_ANNOTATION_KEY] ?? <span className="text-muted-foreground/50">—</span>}</td>
                    <td className="px-3 py-2"><StatusBadge conditions={gw.status?.conditions} /></td>
                    <td className="px-3 py-2 text-right flex gap-1 justify-end">
                      <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => openEditGw(gw)}><Pencil className="h-3 w-3" /></Button>
                      <Button size="sm" variant="ghost" className="h-6 w-6 p-0 text-destructive hover:text-destructive" onClick={() => { if (window.confirm(`Delete Gateway "${gw.metadata.name}"?`)) deleteGw.mutate({ name: gw.metadata.name, namespace: gw.metadata.namespace }); }}><Trash2 className="h-3 w-3" /></Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* GatewayClass form */}
          <Sheet open={showGcForm} onOpenChange={v => { if (!v) resetGcForm(); }}>
            <SheetContent className="sm:max-w-md">
              <SheetHeader><SheetTitle>Create GatewayClass</SheetTitle></SheetHeader>
              <div className="p-4 space-y-4">
                <div><Label>Name</Label><Input value={gcName} onChange={e => setGcName(e.target.value)} placeholder="f5-gatewayclass" /></div>
                <div><Label>Controller Name</Label><Input value={gcController} onChange={e => setGcController(e.target.value)} placeholder="f5.com/default-f5-cne-controller" /></div>
                <div className="flex gap-2 pt-2"><Button onClick={submitGc} disabled={createGc.isPending}>{createGc.isPending ? 'Creating…' : 'Create'}</Button><Button variant="outline" onClick={resetGcForm}>Cancel</Button></div>
              </div>
            </SheetContent>
          </Sheet>

          {/* Gateway create/edit form */}
          <Sheet open={showGwForm} onOpenChange={v => { if (!v) resetGwForm(); }}>
            <SheetContent className="sm:max-w-lg overflow-y-auto">
              <SheetHeader><SheetTitle>{editingGw ? 'Edit Gateway' : 'Create Gateway'}</SheetTitle></SheetHeader>
              <div className="p-4 space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  <div><Label>Name *</Label><Input value={gwName} onChange={e => setGwName(e.target.value)} disabled={!!editingGw} placeholder="my-gateway" /></div>
                  <div><Label>Namespace</Label><Input value={gwNs} onChange={e => setGwNs(e.target.value)} placeholder="default" /></div>
                </div>
                <div><Label>GatewayClass</Label>
                  <Select value={gwClass} onValueChange={setGwClass}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {gwClasses.map(gc => <SelectItem key={gc.metadata.name} value={gc.metadata.name}>{gc.metadata.name}</SelectItem>)}
                      <SelectItem value="f5-gatewayclass">f5-gatewayclass (default)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Listener</Label>
                  <div className="grid grid-cols-3 gap-2">
                    <div><Label className="text-[10px]">Name</Label><Input value={gwListenerName} onChange={e => setGwListenerName(e.target.value)} placeholder="http" /></div>
                    <div><Label className="text-[10px]">Protocol</Label>
                      <Select value={gwListenerProto} onValueChange={setGwListenerProto}>
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {['HTTP','HTTPS','TCP','TLS'].map(p => <SelectItem key={p} value={p}>{p}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </div>
                    <div><Label className="text-[10px]">Port</Label><Input type="number" value={gwListenerPort} onChange={e => setGwListenerPort(e.target.value)} placeholder="80" /></div>
                  </div>
                  <div>
                    <Label className="text-[10px]">Allowed Routes From</Label>
                    <Select value={gwAllowed} onValueChange={v => setGwAllowed(v as 'Same'|'All')}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent><SelectItem value="Same">Same namespace</SelectItem><SelectItem value="All">All namespaces</SelectItem></SelectContent>
                    </Select>
                  </div>
                </div>
                <div><Label>Addresses (comma-separated IPs, optional)</Label><Input value={gwAddrs} onChange={e => setGwAddrs(e.target.value)} placeholder="10.1.1.100" /></div>
                <div>
                  <Label>WAF Profile (F5BigWebSecurityProfile name, optional)</Label>
                  <Select value={gwWafProfile} onValueChange={setGwWafProfile}>
                    <SelectTrigger><SelectValue placeholder="None — attach WAF profile" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="">None</SelectItem>
                      {profiles.filter(p => p.metadata.namespace === gwNs).map(p => <SelectItem key={p.metadata.name} value={p.metadata.name}>{p.metadata.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                  <p className="text-[10px] text-muted-foreground mt-1">Only profiles in the same namespace as the Gateway are shown.</p>
                </div>
                <div className="flex gap-2 pt-2">
                  <Button onClick={submitGw} disabled={createGw.isPending || updateGw.isPending}>{createGw.isPending || updateGw.isPending ? 'Saving…' : editingGw ? 'Save' : 'Create'}</Button>
                  <Button variant="outline" onClick={resetGwForm}>Cancel</Button>
                </div>
              </div>
            </SheetContent>
          </Sheet>
        </div>
      )}

      {/* ── WAF Profiles (F5BigWebSecurityProfile) ───────────────────────── */}
      {sub === 'waf-profiles' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">WAF Security Profiles (F5BigWebSecurityProfile)</h3>
            <Button size="sm" className="h-8 gap-1.5" onClick={() => { resetProfForm(); setShowProfForm(true); }}><Plus className="h-3.5 w-3.5" /> WAF Profile</Button>
          </div>
          <div className="rounded-md border border-border bg-muted/20 px-3 py-2 text-xs text-muted-foreground flex gap-1.5">
            <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            A WAF Profile bridges an APPolicy to a Gateway via annotation. After creating a profile, attach it to a Gateway in the Gateways tab.
          </div>
          <div className="rounded-md border border-border overflow-hidden">
            <table className="w-full text-xs">
              <thead><tr className="border-b border-border text-muted-foreground bg-muted/30"><th className="text-left px-3 py-1.5">Name</th><th className="text-left px-3 py-1.5">Namespace</th><th className="text-left px-3 py-1.5">APPolicy (policyName)</th><th className="text-left px-3 py-1.5">Policy Status</th><th className="px-3 py-1.5"></th></tr></thead>
              <tbody>
                {profLoading && <tr><td colSpan={5} className="px-3 py-4 text-center text-muted-foreground">Loading…</td></tr>}
                {!profLoading && profiles.length === 0 && <tr><td colSpan={5} className="px-3 py-4 text-center text-muted-foreground">No WAF Profiles. Create one to start attaching WAF to a Gateway.</td></tr>}
                {profiles.map(p => {
                  const pol = (policies as { metadata: { name: string }; status?: { bundle?: { state?: string } } }[]).find(pol => pol.metadata.name === p.spec.policyName);
                  return (
                    <tr key={`${p.metadata.namespace}/${p.metadata.name}`} className="border-b border-border/40 last:border-0 hover:bg-muted/20">
                      <td className="px-3 py-2 font-mono font-medium">{p.metadata.name}</td>
                      <td className="px-3 py-2 text-muted-foreground">{p.metadata.namespace}</td>
                      <td className="px-3 py-2 font-mono">{p.spec.policyName}</td>
                      <td className="px-3 py-2">
                        {pol ? <span className="text-[10px] px-1.5 py-0.5 rounded bg-success/10 text-success border border-success/20">found</span>
                          : <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground border">not in this ns</span>}
                      </td>
                      <td className="px-3 py-2 text-right flex gap-1 justify-end">
                        <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => openEditProf(p)}><Pencil className="h-3 w-3" /></Button>
                        <Button size="sm" variant="ghost" className="h-6 w-6 p-0 text-destructive hover:text-destructive" onClick={() => { if (window.confirm(`Delete WAF Profile "${p.metadata.name}"?`)) deleteProf.mutate({ name: p.metadata.name, namespace: p.metadata.namespace }); }}><Trash2 className="h-3 w-3" /></Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <Sheet open={showProfForm} onOpenChange={v => { if (!v) resetProfForm(); }}>
            <SheetContent className="sm:max-w-md">
              <SheetHeader><SheetTitle>{editingProf ? 'Edit WAF Profile' : 'Create WAF Profile'}</SheetTitle></SheetHeader>
              <div className="p-4 space-y-4">
                <div><Label>Profile Name *</Label><Input value={profName} onChange={e => setProfName(e.target.value)} disabled={!!editingProf} placeholder="my-waf-profile" /></div>
                <div><Label>Namespace</Label><Input value={profNs} onChange={e => setProfNs(e.target.value)} placeholder="default" /></div>
                <div>
                  <Label>APPolicy Name *</Label>
                  <Select value={profPolicy} onValueChange={setProfPolicy}>
                    <SelectTrigger><SelectValue placeholder="Select APPolicy" /></SelectTrigger>
                    <SelectContent>
                      {(policies as { metadata: { name: string; namespace: string } }[]).map(p => (
                        <SelectItem key={`${p.metadata.namespace}/${p.metadata.name}`} value={p.metadata.name}>
                          {p.metadata.namespace}/{p.metadata.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-[10px] text-muted-foreground mt-1">policyName is a plain string — the APPolicy can live in any namespace.</p>
                </div>
                <div className="flex gap-2 pt-2">
                  <Button onClick={submitProf} disabled={createProf.isPending || updateProf.isPending}>{createProf.isPending || updateProf.isPending ? 'Saving…' : editingProf ? 'Save' : 'Create'}</Button>
                  <Button variant="outline" onClick={resetProfForm}>Cancel</Button>
                </div>
              </div>
            </SheetContent>
          </Sheet>
        </div>
      )}

      {/* ── HTTPRoutes ───────────────────────────────────────────────────── */}
      {sub === 'httproutes' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">HTTP Routes</h3>
            <Button size="sm" className="h-8 gap-1.5" onClick={() => { resetRtForm(); setShowRtForm(true); }}><Plus className="h-3.5 w-3.5" /> HTTP Route</Button>
          </div>
          <div className="rounded-md border border-border overflow-hidden">
            <table className="w-full text-xs">
              <thead><tr className="border-b border-border text-muted-foreground bg-muted/30"><th className="text-left px-3 py-1.5">Name</th><th className="text-left px-3 py-1.5">Namespace</th><th className="text-left px-3 py-1.5">Parent Gateway</th><th className="text-left px-3 py-1.5">Hostnames</th><th className="text-left px-3 py-1.5">Status</th><th className="px-3 py-1.5"></th></tr></thead>
              <tbody>
                {rtLoading && <tr><td colSpan={6} className="px-3 py-4 text-center text-muted-foreground">Loading…</td></tr>}
                {!rtLoading && routes.length === 0 && <tr><td colSpan={6} className="px-3 py-4 text-center text-muted-foreground">No HTTPRoutes. Create one to route traffic through a WAF Gateway.</td></tr>}
                {routes.map(r => (
                  <tr key={`${r.metadata.namespace}/${r.metadata.name}`} className="border-b border-border/40 last:border-0 hover:bg-muted/20">
                    <td className="px-3 py-2 font-mono font-medium">{r.metadata.name}</td>
                    <td className="px-3 py-2 text-muted-foreground">{r.metadata.namespace}</td>
                    <td className="px-3 py-2 font-mono text-[10px]">{r.spec.parentRefs.map(pr => `${pr.namespace ?? r.metadata.namespace}/${pr.name}`).join(', ')}</td>
                    <td className="px-3 py-2">{(r.spec.hostnames ?? []).join(', ') || <span className="text-muted-foreground/50">—</span>}</td>
                    <td className="px-3 py-2"><StatusBadge conditions={r.status?.parents?.[0]?.conditions} /></td>
                    <td className="px-3 py-2 text-right flex gap-1 justify-end">
                      <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => openEditRt(r)}><Pencil className="h-3 w-3" /></Button>
                      <Button size="sm" variant="ghost" className="h-6 w-6 p-0 text-destructive hover:text-destructive" onClick={() => { if (window.confirm(`Delete HTTPRoute "${r.metadata.name}"?`)) deleteRt.mutate({ name: r.metadata.name, namespace: r.metadata.namespace }); }}><Trash2 className="h-3 w-3" /></Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <Sheet open={showRtForm} onOpenChange={v => { if (!v) resetRtForm(); }}>
            <SheetContent className="sm:max-w-lg overflow-y-auto">
              <SheetHeader><SheetTitle>{editingRt ? 'Edit HTTPRoute' : 'Create HTTPRoute'}</SheetTitle></SheetHeader>
              <div className="p-4 space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  <div><Label>Name *</Label><Input value={rtName} onChange={e => setRtName(e.target.value)} disabled={!!editingRt} placeholder="my-route" /></div>
                  <div><Label>Namespace</Label><Input value={rtNs} onChange={e => setRtNs(e.target.value)} placeholder="default" /></div>
                </div>
                <div className="space-y-2">
                  <Label>Parent Gateway *</Label>
                  <Select value={rtGateway} onValueChange={v => { const [gns, gn] = v.includes('/') ? v.split('/') : ['', v]; setRtGateway(gn); setRtGwNs(gns); }}>
                    <SelectTrigger><SelectValue placeholder="Select Gateway" /></SelectTrigger>
                    <SelectContent>
                      {gateways.map(gw => <SelectItem key={`${gw.metadata.namespace}/${gw.metadata.name}`} value={`${gw.metadata.namespace}/${gw.metadata.name}`}>{gw.metadata.namespace}/{gw.metadata.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                  <div className="grid grid-cols-2 gap-2">
                    <div><Label className="text-[10px]">Gw Namespace (if cross-ns)</Label><Input value={rtGwNs} onChange={e => setRtGwNs(e.target.value)} placeholder="default" /></div>
                    <div><Label className="text-[10px]">Section Name (listener)</Label><Input value={rtSection} onChange={e => setRtSection(e.target.value)} placeholder="http" /></div>
                  </div>
                </div>
                <div><Label>Hostnames (comma-separated, optional)</Label><Input value={rtHostnames} onChange={e => setRtHostnames(e.target.value)} placeholder="example.com,api.example.com" /></div>
                <div className="space-y-2">
                  <Label>Backend</Label>
                  <div className="grid grid-cols-3 gap-2">
                    <div><Label className="text-[10px]">Service Name *</Label><Input value={rtBackend} onChange={e => setRtBackend(e.target.value)} placeholder="my-svc" /></div>
                    <div><Label className="text-[10px]">Port</Label><Input type="number" value={rtBackendPort} onChange={e => setRtBackendPort(e.target.value)} placeholder="80" /></div>
                    <div><Label className="text-[10px]">Namespace (if cross-ns)</Label><Input value={rtBackendNs} onChange={e => setRtBackendNs(e.target.value)} placeholder="default" /></div>
                  </div>
                </div>
                <div><Label>Path Prefix</Label><Input value={rtPath} onChange={e => setRtPath(e.target.value)} placeholder="/" /></div>
                <div className="flex gap-2 pt-2">
                  <Button onClick={submitRt} disabled={createRt.isPending || updateRt.isPending}>{createRt.isPending || updateRt.isPending ? 'Saving…' : editingRt ? 'Save' : 'Create'}</Button>
                  <Button variant="outline" onClick={resetRtForm}>Cancel</Button>
                </div>
              </div>
            </SheetContent>
          </Sheet>
        </div>
      )}

      {/* ── Reference Grants ─────────────────────────────────────────────── */}
      {sub === 'ref-grants' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">Reference Grants</h3>
            <Button size="sm" className="h-8 gap-1.5" onClick={() => { resetGrForm(); setShowGrForm(true); }}><Plus className="h-3.5 w-3.5" /> Reference Grant</Button>
          </div>
          <div className="rounded-md border border-border bg-muted/20 px-3 py-2 text-xs text-muted-foreground flex gap-1.5">
            <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            Required when an HTTPRoute in namespace A references a backend Service in namespace B. Created in the <strong>target</strong> namespace (where the Service lives).
          </div>
          <div className="rounded-md border border-border overflow-hidden">
            <table className="w-full text-xs">
              <thead><tr className="border-b border-border text-muted-foreground bg-muted/30"><th className="text-left px-3 py-1.5">Name</th><th className="text-left px-3 py-1.5">Namespace (target)</th><th className="text-left px-3 py-1.5">Allows From</th><th className="text-left px-3 py-1.5">To</th><th className="px-3 py-1.5"></th></tr></thead>
              <tbody>
                {grLoading && <tr><td colSpan={5} className="px-3 py-4 text-center text-muted-foreground">Loading…</td></tr>}
                {!grLoading && grants.length === 0 && <tr><td colSpan={5} className="px-3 py-4 text-center text-muted-foreground">No Reference Grants. Only needed for cross-namespace backend references.</td></tr>}
                {grants.map(g => (
                  <tr key={`${g.metadata.namespace}/${g.metadata.name}`} className="border-b border-border/40 last:border-0 hover:bg-muted/20">
                    <td className="px-3 py-2 font-mono font-medium">{g.metadata.name}</td>
                    <td className="px-3 py-2 text-muted-foreground">{g.metadata.namespace}</td>
                    <td className="px-3 py-2 text-[10px]">{g.spec.from.map(f => `${f.namespace}/${f.kind}`).join(', ')}</td>
                    <td className="px-3 py-2 text-[10px]">{g.spec.to.map(t => t.name ? `${t.kind}/${t.name}` : t.kind).join(', ')}</td>
                    <td className="px-3 py-2 text-right">
                      <Button size="sm" variant="ghost" className="h-6 w-6 p-0 text-destructive hover:text-destructive" onClick={() => { if (window.confirm(`Delete ReferenceGrant "${g.metadata.name}"?`)) deleteGr.mutate({ name: g.metadata.name, namespace: g.metadata.namespace }); }}><Trash2 className="h-3 w-3" /></Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <Sheet open={showGrForm} onOpenChange={v => { if (!v) resetGrForm(); }}>
            <SheetContent className="sm:max-w-md">
              <SheetHeader><SheetTitle>Create Reference Grant</SheetTitle></SheetHeader>
              <div className="p-4 space-y-4">
                <div><Label>Name *</Label><Input value={grName} onChange={e => setGrName(e.target.value)} placeholder="allow-route-to-svc" /></div>
                <div><Label>Target Namespace (where the Service lives) *</Label><Input value={grNs} onChange={e => setGrNs(e.target.value)} placeholder="default" /></div>
                <div><Label>Source Namespace (where the HTTPRoute lives) *</Label><Input value={grFromNs} onChange={e => setGrFromNs(e.target.value)} placeholder="app-ns" /></div>
                <div className="grid grid-cols-2 gap-3">
                  <div><Label>From Kind</Label>
                    <Select value={grFromKind} onValueChange={setGrFromKind}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent><SelectItem value="HTTPRoute">HTTPRoute</SelectItem><SelectItem value="GRPCRoute">GRPCRoute</SelectItem></SelectContent>
                    </Select>
                  </div>
                  <div><Label>To Kind</Label>
                    <Select value={grToKind} onValueChange={setGrToKind}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent><SelectItem value="Service">Service</SelectItem><SelectItem value="Secret">Secret</SelectItem></SelectContent>
                    </Select>
                  </div>
                </div>
                <div><Label>Specific Resource Name (leave blank = any)</Label><Input value={grToName} onChange={e => setGrToName(e.target.value)} placeholder="my-service (optional)" /></div>
                <div className="flex gap-2 pt-2">
                  <Button onClick={submitGr} disabled={createGr.isPending}>{createGr.isPending ? 'Creating…' : 'Create'}</Button>
                  <Button variant="outline" onClick={resetGrForm}>Cancel</Button>
                </div>
              </div>
            </SheetContent>
          </Sheet>
        </div>
      )}
    </div>
  );
}

// ============================================================================

const TABS: { key: PageTab; label: string; icon: typeof Shield; description: string }[] = [
  { key: 'policies',     label: 'Policies',            icon: Shield,    description: 'APPolicy — compile and enforce WAF rules against traffic' },
  { key: 'log-profiles', label: 'Log Profiles',        icon: FileText,  description: 'APLogConf — define WAF security event log format and filter' },
  { key: 'signatures',   label: 'Attack Signatures',   icon: Key,       description: 'APSignatures — set attack/bot/threat campaign signature revision (namespace-wide)' },
  { key: 'user-sigs',    label: 'User Signatures',     icon: PenLine,   description: 'APUserSig — custom attack signatures embedded by policies via tag reference' },
  { key: 'gateway-waf', label: 'Gateway WAF',         icon: Globe,     description: 'Attach WAF policies to Gateway API resources (Gateway, HTTPRoute, F5BigWebSecurityProfile)' },
];

export default function WafPolicies() {
  const { isDark } = useTheme();
  const { data: clustersData } = useAllClusters();
  const clusters = clustersData?.clusters ?? [];

  const [selectedCluster, setSelectedCluster] = useState<number | null>(null);
  const [activeTab, setActiveTab]             = useState<PageTab>('policies');
  const [namespace, setNamespace]             = useState('default');
  const [searchParams, setSearchParams]       = useSearchParams();
  const deepLinkPolicy = searchParams.get('policy');

  // Deep link from WAF Dashboard — ?policy=name jumps to the Policies tab and opens it
  useEffect(() => {
    if (deepLinkPolicy) setActiveTab('policies');
  }, [deepLinkPolicy]);

  const clusterId = selectedCluster ?? clusters[0]?.id ?? null;

  return (
    <div className="p-6 overflow-y-auto">
      <div className="max-w-6xl mx-auto space-y-5">
        {/* Breadcrumb + header — matches NIM pattern */}
        <div>
          <p className="text-xs text-muted-foreground mb-1">WAF</p>
          <h2 className="text-xl font-semibold flex items-center gap-2">
            <Shield className="h-5 w-5" /> Security Policies
          </h2>
          <p className={cn('text-sm mt-0.5', isDark ? 'text-muted-foreground' : 'text-muted-foreground')}>
            Manage App Protect WAF CRDs: Policies, Log Profiles, Attack Signatures, and User Signatures.
          </p>
        </div>

        {/* Cluster + Namespace pickers */}
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <span className={cn('text-xs shrink-0', isDark ? 'text-muted-foreground' : 'text-muted-foreground')}>Cluster:</span>
            <Select value={clusterId ? String(clusterId) : undefined} onValueChange={(v) => setSelectedCluster(Number(v))}>
              <SelectTrigger className="w-56 h-9"><SelectValue placeholder="Select a cluster" /></SelectTrigger>
              <SelectContent>
                {clusters.map((c) => <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <span className={cn('text-xs shrink-0', isDark ? 'text-muted-foreground' : 'text-muted-foreground')}>Namespace:</span>
            {clusterId
              ? <NamespacePicker clusterId={clusterId} value={namespace} onChange={setNamespace} />
              : <Input value={namespace} onChange={(e) => setNamespace(e.target.value)} className="w-36 h-9 text-sm" placeholder="default" />
            }
          </div>
        </div>

        {/* Tabs — border-bottom style matching NIM */}
        <div className="border-b border-border">
          <div className="flex gap-0">
            {TABS.map((t) => {
              const Icon = t.icon;
              return (
                <button
                  key={t.key}
                  className={cn(
                    'flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px',
                    activeTab === t.key
                      ? 'border-primary text-foreground'
                      : 'border-transparent text-muted-foreground hover:text-foreground hover:border-border'
                  )}
                  onClick={() => setActiveTab(t.key)}
                >
                  <Icon className="h-3.5 w-3.5" /> {t.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Tab description line */}
        <p className="text-xs text-muted-foreground -mt-1">
          {TABS.find((t) => t.key === activeTab)?.description}
        </p>

        {/* Tab content */}
        {clusterId ? (
          <>
            {activeTab === 'log-profiles' && <LogProfilesTab clusterId={clusterId} namespace={namespace} isDark={isDark} />}
            {activeTab === 'policies'     && (
              <PoliciesTab
                clusterId={clusterId} namespace={namespace} isDark={isDark}
                initialOpenPolicy={deepLinkPolicy}
                onConsumeInitialOpen={() => setSearchParams(prev => { const n = new URLSearchParams(prev); n.delete('policy'); return n; }, { replace: true })}
              />
            )}
            {activeTab === 'signatures'   && <SignatureSettingsTab clusterId={clusterId} namespace={namespace} isDark={isDark} />}
            {activeTab === 'user-sigs'    && <UserSigsTab    clusterId={clusterId} namespace={namespace} isDark={isDark} />}
            {activeTab === 'gateway-waf'  && <GatewayWafTab  clusterId={clusterId} namespace={namespace} />}
          </>
        ) : (
          <EmptyState icon={Shield} title="No cluster selected" description="Select a cluster above to manage WAF resources." />
        )}
      </div>
    </div>
  );
}
