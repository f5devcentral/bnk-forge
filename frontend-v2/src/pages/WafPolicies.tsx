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

import { useEffect, useMemo, useState } from 'react';
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
import { Plus, Shield, Trash2, FileText, Key, RefreshCw, PenLine, Pencil, AlertTriangle, RotateCcw, Info, Download, RefreshCcw } from 'lucide-react';

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

type PageTab = 'log-profiles' | 'policies' | 'signatures' | 'user-sigs';

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

function LogProfilesTab({ clusterId, namespace, isDark }: { clusterId: number; namespace: string; isDark: boolean }) {
  const [showCreate, setShowCreate] = useState(false);
  const [viewingItem, setViewingItem]   = useState<APLogConfResource | null>(null);
  const [editingItem, setEditingItem]   = useState<APLogConfResource | null>(null);
  const [deletingItem, setDeletingItem] = useState<APLogConfResource | null>(null);

  const { data, isLoading, refetch: refetchLogConfs } = useWafLogConfs(clusterId, namespace, { enabled: !!clusterId });
  const logConfs = useMemo(() => data?.log_confs ?? [], [data]);
  const deleteLc = useDeleteWafLogConf(clusterId);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className={cn('text-xs', isDark ? 'text-muted-foreground' : 'text-muted-foreground')}>
          APLogConf CRs define WAF security event log format. Create them first — they can be reused across multiple policies.
        </p>
        <div className="flex gap-2">
          <RefreshButton refetch={refetchLogConfs} isLoading={isLoading} />
          <Button size="sm" className="gap-1.5" onClick={() => setShowCreate(!showCreate)}>
            <Plus className="h-4 w-4" /> {showCreate ? 'Cancel' : 'Create Log Profile'}
          </Button>
        </div>
      </div>

      {showCreate && (
        <div className={cn('rounded-lg border overflow-hidden', isDark ? 'border-border' : 'border-border')}>
          <APLogConfForm clusterId={clusterId} namespace={namespace} onClose={() => setShowCreate(false)} />
        </div>
      )}

      {isLoading && <SkeletonTable rows={3} columns={4} />}
      {!isLoading && logConfs.length === 0 && (
        <EmptyState icon={FileText} title="No log profiles yet" description="Create an APLogConf to define WAF security event log format." />
      )}
      {logConfs.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Format</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Age</TableHead>
              <TableHead className="w-20" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {logConfs.map((lc: APLogConfResource) => (
              <TableRow key={`${lc.metadata.namespace}/${lc.metadata.name}`} className="cursor-pointer" onClick={() => setViewingItem(lc)}>
                <TableCell className="font-medium">{lc.metadata.name}</TableCell>
                <TableCell className="text-xs">{(lc.spec?.content as { format?: string } | undefined)?.format ?? '—'}</TableCell>
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
            ))}
          </TableBody>
        </Table>
      )}

      {/* Detail sheet */}
      <Sheet open={!!viewingItem} onOpenChange={(open) => !open && setViewingItem(null)}>
        <SheetContent className="w-full sm:max-w-xl overflow-y-auto p-0">
          <SheetHeader className="px-4 pt-4 pb-0"><SheetTitle>{viewingItem?.metadata.name}</SheetTitle></SheetHeader>
          {viewingItem && <APLogConfDetail resource={viewingItem} />}
        </SheetContent>
      </Sheet>

      {/* Edit sheet */}
      <Sheet open={!!editingItem} onOpenChange={(open) => !open && setEditingItem(null)}>
        <SheetContent className="w-full sm:max-w-2xl overflow-y-auto p-0">
          <SheetHeader className="px-4 pt-4 pb-0"><SheetTitle>Edit Log Profile — {editingItem?.metadata.name}</SheetTitle></SheetHeader>
          {editingItem && (
            <APLogConfForm
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

function PoliciesTab({ clusterId, namespace, isDark }: { clusterId: number; namespace: string; isDark: boolean }) {
  const [showWizard, setShowWizard] = useState(false);
  const [selectedPolicy, setSelectedPolicy] = useState<APPolicyResource | null>(null);
  const [editingPolicy, setEditingPolicy]   = useState<APPolicyResource | null>(null);
  const [deletingPolicy, setDeletingPolicy] = useState<APPolicyResource | null>(null);

  const { data, isLoading, error, refetch } = useWafPolicies(clusterId, namespace, { enabled: !!clusterId });
  const policies = useMemo(() => data?.policies ?? [], [data]);
  const deletePolicy = useDeleteWafPolicy(clusterId);
  const recompile = useRecompileWafPolicy(clusterId);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className={cn('text-xs', isDark ? 'text-muted-foreground' : 'text-muted-foreground')}>
          APPolicy CRs define WAF rules. Click a row to inspect bundle status.
        </p>
        <div className="flex gap-2">
          <RefreshButton refetch={refetch} isLoading={isLoading} />
          <Button size="sm" className="gap-1.5" onClick={() => setShowWizard(!showWizard)}>
            <Plus className="h-4 w-4" /> {showWizard ? 'Cancel' : 'Create Policy'}
          </Button>
        </div>
      </div>

      {showWizard && (
        <div className={cn('rounded-lg border overflow-hidden', isDark ? 'border-border' : 'border-border')}>
          <APPolicyForm clusterId={clusterId} namespace={namespace} onClose={() => setShowWizard(false)} />
        </div>
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
              <TableHead>Name</TableHead>
              <TableHead>Namespace</TableHead>
              <TableHead>Bundle State</TableHead>
              <TableHead>Compiler Version</TableHead>
              <TableHead>Age</TableHead>
              <TableHead className="w-20" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {policies.map((p) => (
              <TableRow key={`${p.metadata.namespace}/${p.metadata.name}`} className="cursor-pointer" onClick={() => setSelectedPolicy(p)}>
                <TableCell className="font-medium">{p.metadata.name}</TableCell>
                <TableCell>{p.metadata.namespace}</TableCell>
                <TableCell>
                  <Badge variant="outline" className={cn('text-[10px]', getBundleStateBadgeClass(p.status?.bundle?.state))}>
                    {p.status?.bundle?.state ?? 'unknown'}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs">{p.status?.bundle?.compilerVersion ?? '—'}</TableCell>
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
            ))}
          </TableBody>
        </Table>
      )}

      {/* Detail sheet (read-only) */}
      <Sheet open={!!selectedPolicy} onOpenChange={(open) => !open && setSelectedPolicy(null)}>
        <SheetContent className="w-full sm:max-w-xl overflow-y-auto">
          <SheetHeader><SheetTitle>{selectedPolicy?.metadata.name}</SheetTitle></SheetHeader>
          {selectedPolicy && <div className="mt-4"><WafPolicyDetail resource={selectedPolicy} isDark={isDark} clusterId={clusterId} /></div>}
        </SheetContent>
      </Sheet>

      {/* Edit sheet */}
      <Sheet open={!!editingPolicy} onOpenChange={(open) => !open && setEditingPolicy(null)}>
        <SheetContent className="w-full sm:max-w-4xl overflow-y-auto p-0">
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

  const { data, isLoading } = useWafUserSigs(clusterId, namespace, { enabled: !!clusterId });
  const userSigs = useMemo(() => data?.user_sigs ?? [], [data]);
  const deleteSig = useDeleteWafUserSig(clusterId);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className={cn('text-xs', isDark ? 'text-muted-foreground' : 'text-muted-foreground')}>
          APUserSig CRs define custom attack signatures embedded into compiled policies via <code>signature-requirements[].tag</code>. Click a row to inspect.
        </p>
        <Button size="sm" className="gap-1.5" onClick={() => setShowCreate(!showCreate)}>
          <Plus className="h-4 w-4" /> {showCreate ? 'Cancel' : 'Create User Signature'}
        </Button>
      </div>

      {showCreate && (
        <div className={cn('rounded-lg border', isDark ? 'border-border bg-card/50' : 'border-border bg-white')}>
          <APUserSigForm clusterId={clusterId} namespace={namespace} onClose={() => setShowCreate(false)} />
        </div>
      )}

      {isLoading && <SkeletonTable rows={3} columns={4} />}
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
              <TableHead>Status</TableHead>
              <TableHead>Age</TableHead>
              <TableHead className="w-20" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {userSigs.map((us: APUserSigResource) => (
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

const TABS: { key: PageTab; label: string; icon: typeof Shield; description: string }[] = [
  { key: 'log-profiles', label: 'Log Profiles',       icon: FileText, description: 'APLogConf — define WAF security event log format (create before policies)' },
  { key: 'policies',     label: 'Policies',            icon: Shield,   description: 'APPolicy — compile and enforce WAF rules against traffic' },
  { key: 'signatures',   label: 'Signature Settings',  icon: Key,      description: 'APSignatures — set attack/bot/threat signature revision (namespace-wide)' },
  { key: 'user-sigs',    label: 'User Signatures',     icon: PenLine,  description: 'APUserSig — custom attack signatures embedded by policies' },
];

export default function WafPolicies() {
  const { isDark } = useTheme();
  const { data: clustersData } = useAllClusters();
  const clusters = clustersData?.clusters ?? [];

  const [selectedCluster, setSelectedCluster] = useState<number | null>(null);
  const [activeTab, setActiveTab]             = useState<PageTab>('log-profiles');
  const [namespace, setNamespace]             = useState('default');

  const clusterId = selectedCluster ?? clusters[0]?.id ?? null;

  return (
    <div className="p-6 overflow-y-auto">
      <div className="max-w-6xl mx-auto space-y-6">
        {/* Header */}
        <div>
          <h2 className="text-xl font-semibold mb-1 flex items-center gap-2">
            <Shield className="h-5 w-5" /> WAF Policies
          </h2>
          <p className={cn('text-sm', isDark ? 'text-muted-foreground' : 'text-muted-foreground')}>
            Manage all 4 App Protect WAF CRDs. Requires the nap-policy-operator PLM chart installed on the target cluster.
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

        {/* Tabs */}
        <div className={cn('flex gap-1 p-1 rounded-lg border flex-wrap', isDark ? 'bg-card/50 border-border' : 'bg-muted border-border/50')}>
          {TABS.map((t) => {
            const Icon = t.icon;
            return (
              <button
                key={t.key}
                className={cn(
                  'flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium transition-colors',
                  activeTab === t.key
                    ? isDark ? 'bg-card text-white shadow-sm' : 'bg-white text-foreground shadow-sm'
                    : isDark ? 'text-muted-foreground hover:text-foreground/90' : 'text-muted-foreground hover:text-foreground/80'
                )}
                onClick={() => setActiveTab(t.key)}
              >
                <Icon className="h-4 w-4" /> {t.label}
              </button>
            );
          })}
        </div>

        {/* Active tab description */}
        <p className={cn('text-xs -mt-2', isDark ? 'text-muted-foreground' : 'text-muted-foreground')}>
          {TABS.find((t) => t.key === activeTab)?.description}
        </p>

        {/* Tab content */}
        {clusterId ? (
          <>
            {activeTab === 'log-profiles' && <LogProfilesTab clusterId={clusterId} namespace={namespace} isDark={isDark} />}
            {activeTab === 'policies'     && <PoliciesTab    clusterId={clusterId} namespace={namespace} isDark={isDark} />}
            {activeTab === 'signatures'   && <SignatureSettingsTab clusterId={clusterId} namespace={namespace} isDark={isDark} />}
            {activeTab === 'user-sigs'    && <UserSigsTab    clusterId={clusterId} namespace={namespace} isDark={isDark} />}
          </>
        ) : (
          <EmptyState icon={Shield} title="No cluster selected" description="Select a cluster above to manage WAF resources." />
        )}
      </div>
    </div>
  );
}
