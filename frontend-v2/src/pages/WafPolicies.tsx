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
import { Textarea } from '@/components/ui/textarea';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { EmptyState } from '@/components/ui/empty-state';
import { SkeletonTable } from '@/components/ui/skeleton-table';
import { DestructiveConfirmDialog } from '@/components/ui/destructive-confirm-dialog';
import { Plus, Shield, Trash2, FileText, Key, RefreshCw, PenLine, Pencil, AlertTriangle, Copy, Check, RotateCcw, FileCode2 } from 'lucide-react';
import { useAllClusters } from '@/hooks/useK8sClusters';
import { useClusterNamespaces } from '@/hooks/useK8sResources';
import {
  useWafPolicies, useDeleteWafPolicy, useUpdateWafPolicy,
  useWafLogConfs, useCreateWafLogConf, useUpdateWafLogConf, useDeleteWafLogConf,
  useWafSignatures, useUpsertWafSignatures,
  useWafUserSigs, useCreateWafUserSig, useUpdateWafUserSig, useDeleteWafUserSig,
} from '@/hooks/useWafPolicies';
import { WafPolicyWizard } from '@/components/k8s/WafPolicyWizard';
import { WafPolicyDetail } from '@/components/k8s/f5bnk-details/WafPolicyDetail';
import { formatAge } from '@/lib/time-utils';
import type {
  APPolicyResource, APLogConfResource, APUserSigResource, BundleState,
  APLogConfFormat, APLogConfRequestType,
} from '@/types';

type PageTab = 'log-profiles' | 'policies' | 'signatures' | 'user-sigs';

const REQUEST_TYPES: APLogConfRequestType[] = ['illegal', 'blocked', 'all'];

// RFC 1123 DNS label: lowercase alphanumeric or '-', no leading/trailing '-', max 63 chars
const RFC1123_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
function validateK8sName(name: string): string | null {
  if (!name) return 'Name is required.';
  if (name.length > 63) return 'Name must be ≤ 63 characters.';
  if (!RFC1123_RE.test(name)) return 'Must be lowercase alphanumeric with hyphens; cannot start or end with a hyphen.';
  return null;
}

// APLogConf max_message_size: \d+k where 1–64
const MSG_SIZE_RE = /^([1-9]|[1-5][0-9]|6[0-4])k$/;
function validateMaxMsgSize(v: string): string | null {
  if (!v || v === '10k') return null; // default
  if (!MSG_SIZE_RE.test(v)) return 'Must be 1k–64k (e.g. "16k")';
  return null;
}

// max_request_size: a positive integer, Nk, or "any"
const REQ_SIZE_RE = /^(any|[1-9][0-9]*(k)?)$/;
function validateMaxReqSize(v: string): string | null {
  if (!v || v === 'any') return null;
  if (!REQ_SIZE_RE.test(v)) return 'Must be a number, "Nk", or "any"';
  return null;
}

function yamlFromSpec(name: string, namespace: string, kind: string, spec: Record<string, unknown>): string {
  const lines = [
    `apiVersion: appprotect.f5.com/v1`,
    `kind: ${kind}`,
    `metadata:`,
    `  name: ${name}`,
    `  namespace: ${namespace}`,
    `spec:`,
    ...JSON.stringify(spec, null, 2).split('\n').slice(1, -1).map(l => '  ' + l),
  ];
  return lines.join('\n');
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
    <div className="flex items-start gap-1.5 rounded-md border border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-900/20 px-3 py-2 text-xs text-red-600 dark:text-red-400">
      <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
      <span>{message}</span>
    </div>
  );
}

function getBundleStateBadgeClass(state: BundleState | undefined) {
  switch (state) {
    case 'ready':       return 'bg-green-500/10 text-green-600 border-green-500/20';
    case 'invalid':     return 'bg-red-500/10 text-red-600 border-red-500/20';
    case 'processing':  return 'bg-blue-500/10 text-blue-600 border-blue-500/20';
    default:            return 'bg-slate-500/10 text-slate-600 border-slate-500/20';
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

interface LogProfileFormProps {
  name: string;
  onNameChange?: (v: string) => void;
  nameError?: string | null;
  format: APLogConfFormat;
  onFormatChange: (v: APLogConfFormat) => void;
  formatString: string;
  onFormatStringChange: (v: string) => void;
  requestType: APLogConfRequestType;
  onRequestTypeChange: (v: APLogConfRequestType) => void;
  maxMsg: string;
  onMaxMsgChange: (v: string) => void;
  maxReq: string;
  onMaxReqChange: (v: string) => void;
  /** When true the name field is hidden (for edit sheet where name is immutable). */
  hideNameField?: boolean;
}

const REQUEST_TYPE_HINTS: Record<string, string> = {
  illegal: 'Log only requests that violate the security policy',
  blocked: 'Log only requests that were blocked',
  all: 'Log all requests (high volume — use with care)',
};

function LogProfileFormFields({ name, onNameChange, nameError, format, onFormatChange, formatString, onFormatStringChange, requestType, onRequestTypeChange, maxMsg, onMaxMsgChange, maxReq, onMaxReqChange, hideNameField }: LogProfileFormProps) {
  const msgSizeError = validateMaxMsgSize(maxMsg);
  const reqSizeError = validateMaxReqSize(maxReq);
  const formatStringMissing = format === 'user-defined' && !formatString.trim();
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 max-w-xl">
      {!hideNameField && (
        <div className="space-y-1.5">
          <Label>Name <span className="text-red-500">*</span></Label>
          <Input
            value={name}
            onChange={(e) => onNameChange?.(e.target.value)}
            placeholder="my-log-profile"
            className={nameError ? 'border-red-500 focus-visible:ring-red-500' : ''}
          />
          {nameError
            ? <p className="text-xs text-red-500 flex items-center gap-1"><AlertTriangle className="h-3 w-3" />{nameError}</p>
            : <p className="text-xs text-muted-foreground">Lowercase alphanumeric with hyphens (RFC 1123).</p>
          }
        </div>
      )}
      <div className="space-y-1.5">
        <Label>Format</Label>
        <Select value={format} onValueChange={(v) => onFormatChange(v as APLogConfFormat)}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="default">default — NAP JSON</SelectItem>
            <SelectItem value="splunk">splunk — Splunk key=value</SelectItem>
            <SelectItem value="arcsight">arcsight — ArcSight CEF</SelectItem>
            <SelectItem value="user-defined">user-defined — custom format string</SelectItem>
            <SelectItem value="grpc">grpc — gRPC streaming</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {format === 'user-defined' && (
        <div className="space-y-1.5 sm:col-span-2">
          <Label>Format String <span className="text-red-500">*</span></Label>
          <Input
            value={formatString}
            onChange={(e) => onFormatStringChange(e.target.value)}
            placeholder="%date_time,%blocking_exception_reason,%dest_port,%ip_client"
            className={formatStringMissing ? 'border-red-500' : ''}
          />
          {formatStringMissing
            ? <p className="text-xs text-red-500 flex items-center gap-1"><AlertTriangle className="h-3 w-3" />Format string is required for user-defined format.</p>
            : <p className="text-xs text-muted-foreground">Comma-separated NAP log field names prefixed with %.</p>
          }
        </div>
      )}
      <div className="space-y-1.5">
        <Label>Request Type Filter</Label>
        <Select value={requestType} onValueChange={(v) => onRequestTypeChange(v as APLogConfRequestType)}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            {REQUEST_TYPES.map((t) => (
              <SelectItem key={t} value={t}>
                <div>
                  <div className="font-medium">{t}</div>
                  <div className="text-xs text-muted-foreground">{REQUEST_TYPE_HINTS[t]}</div>
                </div>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">{REQUEST_TYPE_HINTS[requestType]}</p>
      </div>
      <div className="space-y-1.5">
        <Label>Max Message Size</Label>
        <Input
          value={maxMsg}
          onChange={(e) => onMaxMsgChange(e.target.value)}
          placeholder="10k"
          className={msgSizeError ? 'border-red-500' : ''}
        />
        {msgSizeError
          ? <p className="text-xs text-red-500 flex items-center gap-1"><AlertTriangle className="h-3 w-3" />{msgSizeError}</p>
          : <p className="text-xs text-muted-foreground">Max size of a single log message: 1k–64k</p>
        }
      </div>
      <div className="space-y-1.5">
        <Label>Max Request Size</Label>
        <Input
          value={maxReq}
          onChange={(e) => onMaxReqChange(e.target.value)}
          placeholder="any"
          className={reqSizeError ? 'border-red-500' : ''}
        />
        {reqSizeError
          ? <p className="text-xs text-red-500 flex items-center gap-1"><AlertTriangle className="h-3 w-3" />{reqSizeError}</p>
          : <p className="text-xs text-muted-foreground">Max logged request body size: number, &quot;Nk&quot;, or &quot;any&quot;</p>
        }
      </div>
    </div>
  );
}

/** Returns true when the log profile form has blocking errors that should prevent submission */
function hasLogProfileFormErrors(format: APLogConfFormat, formatString: string, maxMsg: string, maxReq: string): boolean {
  if (format === 'user-defined' && !formatString.trim()) return true;
  if (validateMaxMsgSize(maxMsg)) return true;
  if (validateMaxReqSize(maxReq)) return true;
  return false;
}

// ── Edit sheet: APLogConf ──────────────────────────────────────────────────

function EditLogProfileSheet({
  clusterId, namespace, item, onClose,
}: { clusterId: number; namespace: string; item: APLogConfResource; onClose: () => void }) {
  const content = item.spec?.content as { format?: APLogConfFormat; format_string?: string; max_message_size?: string; max_request_size?: string } | undefined;
  const filter = item.spec?.filter;
  const [format, setFormat]           = useState<APLogConfFormat>(content?.format ?? 'default');
  const [formatString, setFormatString] = useState(content?.format_string ?? '');
  const [requestType, setRequestType] = useState<APLogConfRequestType>(filter?.request_type ?? 'illegal');
  const [maxMsg, setMaxMsg]           = useState(content?.max_message_size ?? '10k');
  const [maxReq, setMaxReq]           = useState(content?.max_request_size ?? 'any');
  const [submitError, setSubmitError] = useState<string | null>(null);

  const update = useUpdateWafLogConf(clusterId, item.metadata.name);

  const [yamlOpen, setYamlOpen] = useState(false);
  const yamlPreview = yamlFromSpec(item.metadata.name, item.metadata.namespace ?? namespace, 'APLogConf', item.spec as Record<string, unknown> ?? {});

  const handleSave = async () => {
    setSubmitError(null);
    try {
      await update.mutateAsync({
        namespace: item.metadata.namespace ?? namespace,
        spec: {
          content: {
            format,
            ...(format === 'user-defined' && formatString ? { format_string: formatString } : {}),
            max_message_size: maxMsg,
            max_request_size: maxReq,
          },
          filter: { request_type: requestType },
        },
      });
      onClose();
    } catch (e) {
      setSubmitError(extractApiError(e));
    }
  };

  return (
    <div className="space-y-4 mt-2">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">Editing <span className="font-mono font-medium">{item.metadata.name}</span>. Name is immutable.</p>
        <Button variant="ghost" size="sm" className="h-6 text-xs gap-1" onClick={() => setYamlOpen(!yamlOpen)}>
          <FileCode2 className="h-3 w-3" /> {yamlOpen ? 'Hide YAML' : 'View YAML'}
        </Button>
      </div>
      {yamlOpen && (
        <pre className="rounded border bg-zinc-950 text-zinc-300 p-3 text-[11px] font-mono overflow-x-auto max-h-48">{yamlPreview}</pre>
      )}
      <LogProfileFormFields
        name={item.metadata.name}
        hideNameField
        format={format} onFormatChange={setFormat}
        formatString={formatString} onFormatStringChange={setFormatString}
        requestType={requestType} onRequestTypeChange={setRequestType}
        maxMsg={maxMsg} onMaxMsgChange={setMaxMsg}
        maxReq={maxReq} onMaxReqChange={setMaxReq}
      />
      {submitError && <InlineError message={submitError} />}
      <div className="flex gap-2">
        <Button
          size="sm" className="bg-blue-600 hover:bg-blue-700"
          onClick={handleSave}
          disabled={hasLogProfileFormErrors(format, formatString, maxMsg, maxReq) || update.isPending}
        >Save</Button>
        <Button size="sm" variant="ghost" onClick={onClose}>Cancel</Button>
      </div>
    </div>
  );
}

function LogProfilesTab({ clusterId, namespace, isDark }: { clusterId: number; namespace: string; isDark: boolean }) {
  const [showCreate, setShowCreate] = useState(false);
  const [editingItem, setEditingItem]   = useState<APLogConfResource | null>(null);
  const [deletingItem, setDeletingItem] = useState<APLogConfResource | null>(null);

  // Create form state
  const [lcName, setLcName] = useState('');
  const [lcFormat, setLcFormat] = useState<APLogConfFormat>('default');
  const [lcFormatString, setLcFormatString] = useState('');
  const [lcRequestType, setLcRequestType] = useState<APLogConfRequestType>('illegal');
  const [lcMaxMsg, setLcMaxMsg] = useState('10k');
  const [lcMaxReq, setLcMaxReq] = useState('any');
  const [lcCreateError, setLcCreateError] = useState<string | null>(null);

  const lcNameError = lcName ? validateK8sName(lcName) : null;

  const { data, isLoading } = useWafLogConfs(clusterId, namespace, { enabled: !!clusterId });
  const logConfs = useMemo(() => data?.log_confs ?? [], [data]);
  const create = useCreateWafLogConf(clusterId);
  const deleteLc = useDeleteWafLogConf(clusterId);

  const handleCreate = async () => {
    const nameErr = validateK8sName(lcName);
    if (nameErr) { setLcCreateError(nameErr); return; }
    setLcCreateError(null);
    try {
      const spec: Record<string, unknown> = {
        content: {
          format: lcFormat,
          ...(lcFormat === 'user-defined' && lcFormatString ? { format_string: lcFormatString } : {}),
          max_message_size: lcMaxMsg,
          max_request_size: lcMaxReq,
        },
        filter: { request_type: lcRequestType },
      };
      await create.mutateAsync({ name: lcName.trim(), namespace, spec });
      setLcName(''); setLcFormat('default'); setLcFormatString(''); setLcMaxMsg('10k'); setLcMaxReq('any');
      setShowCreate(false);
    } catch (e) {
      setLcCreateError(extractApiError(e));
    }
  };

  const { refetch: refetchLogConfs } = useWafLogConfs(clusterId, namespace, { enabled: !!clusterId });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className={cn('text-xs', isDark ? 'text-zinc-400' : 'text-slate-500')}>
          APLogConf CRs define WAF security event log format. Create them first — they can be reused across multiple policies.
        </p>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" className="h-8 gap-1.5" onClick={() => refetchLogConfs()} title="Refresh from cluster">
            <RotateCcw className="h-3.5 w-3.5" />
          </Button>
          <Button size="sm" className="gap-1.5" onClick={() => setShowCreate(!showCreate)}>
            <Plus className="h-4 w-4" /> Create Log Profile
          </Button>
        </div>
      </div>

      {showCreate && (
        <div className={cn('rounded-lg border p-4 space-y-3', isDark ? 'border-zinc-800 bg-zinc-900/50' : 'border-slate-200 bg-white')}>
          <p className={cn('text-sm font-semibold', isDark ? 'text-white' : 'text-zinc-900')}>New Log Profile</p>
          <LogProfileFormFields
            name={lcName} onNameChange={(v) => { setLcName(v); setLcCreateError(null); }}
            nameError={lcNameError}
            format={lcFormat} onFormatChange={setLcFormat}
            formatString={lcFormatString} onFormatStringChange={setLcFormatString}
            requestType={lcRequestType} onRequestTypeChange={setLcRequestType}
            maxMsg={lcMaxMsg} onMaxMsgChange={setLcMaxMsg}
            maxReq={lcMaxReq} onMaxReqChange={setLcMaxReq}
          />
          {lcCreateError && <InlineError message={lcCreateError} />}
          <div className="flex gap-2">
            <Button
              size="sm" className="bg-blue-600 hover:bg-blue-700"
              onClick={handleCreate}
              disabled={!!lcNameError || !lcName.trim() || hasLogProfileFormErrors(lcFormat, lcFormatString, lcMaxMsg, lcMaxReq) || create.isPending}
            >Create</Button>
            <Button size="sm" variant="ghost" onClick={() => { setShowCreate(false); setLcCreateError(null); }}>Cancel</Button>
          </div>
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
              <TableRow key={`${lc.metadata.namespace}/${lc.metadata.name}`}>
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
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-slate-400 hover:text-blue-500" onClick={() => setEditingItem(lc)}>
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-slate-400 hover:text-red-500" onClick={() => setDeletingItem(lc)}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {/* Edit sheet — key forces remount so useState re-initialises from latest item */}
      <Sheet open={!!editingItem} onOpenChange={(open) => !open && setEditingItem(null)}>
        <SheetContent className="w-full sm:max-w-lg overflow-y-auto">
          <SheetHeader><SheetTitle>Edit Log Profile — {editingItem?.metadata.name}</SheetTitle></SheetHeader>
          {editingItem && (
            <EditLogProfileSheet
              key={`${editingItem.metadata.namespace}/${editingItem.metadata.name}/${editingItem.metadata.uid ?? editingItem.metadata.creationTimestamp}`}
              clusterId={clusterId}
              namespace={namespace}
              item={editingItem}
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

// ── Edit sheet: APPolicy JSON editor ──────────────────────────────────────

function EditPolicySheet({
  clusterId, namespace, item, onClose,
}: { clusterId: number; namespace: string; item: APPolicyResource; onClose: () => void }) {
  const [jsonText, setJsonText] = useState(() => {
    try { return JSON.stringify(item.spec ?? {}, null, 2); } catch { return '{}'; }
  });
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const update = useUpdateWafPolicy(clusterId, item.metadata.name);

  const handleSave = async () => {
    let spec: Record<string, unknown>;
    try {
      spec = JSON.parse(jsonText) as Record<string, unknown>;
      setJsonError(null);
    } catch (e) {
      setJsonError(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    try {
      await update.mutateAsync({ namespace: item.metadata.namespace ?? namespace, spec });
      onClose();
    } catch (e) {
      setJsonError(extractApiError(e));
    }
  };

  return (
    <div className="space-y-3 mt-2">
      <p className="text-xs text-muted-foreground">
        Editing <span className="font-mono font-medium">{item.metadata.name}</span>. Saving triggers recompile.
      </p>
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label>Policy Spec (JSON)</Label>
          <Button variant="ghost" size="sm" className="h-6 text-xs gap-1" onClick={() => { void navigator.clipboard.writeText(jsonText); setCopied(true); setTimeout(() => setCopied(false), 2000); }}>
            {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
            {copied ? 'Copied' : 'Copy'}
          </Button>
        </div>
        <Textarea
          value={jsonText}
          onChange={(e) => { setJsonText(e.target.value); setJsonError(null); }}
          rows={22}
          className="font-mono text-xs"
        />
        {jsonError && <InlineError message={jsonError} />}
      </div>
      <div className="flex gap-2">
        <Button size="sm" className="bg-blue-600 hover:bg-blue-700" onClick={handleSave} disabled={update.isPending}>Save</Button>
        <Button size="sm" variant="ghost" onClick={onClose}>Cancel</Button>
      </div>
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

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className={cn('text-xs', isDark ? 'text-zinc-400' : 'text-slate-500')}>
          APPolicy CRs define WAF rules. Click a row to inspect bundle status.
        </p>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" className="h-8 gap-1.5" onClick={() => refetch()} title="Refresh from cluster">
            <RotateCcw className="h-3.5 w-3.5" />
          </Button>
          <Button size="sm" className="gap-1.5" onClick={() => setShowWizard(!showWizard)}>
            <Plus className="h-4 w-4" /> {showWizard ? 'Cancel' : 'Create Policy'}
          </Button>
        </div>
      </div>

      {showWizard && (
        <div className={cn('rounded-lg border p-4', isDark ? 'border-zinc-800 bg-zinc-900/50' : 'border-slate-200 bg-white')}>
          <WafPolicyWizard clusterId={clusterId} onClose={() => setShowWizard(false)} />
        </div>
      )}

      {isLoading && <SkeletonTable rows={4} columns={5} />}
      {error && (
        <div className={cn('rounded-lg border p-4 text-center', isDark ? 'border-red-500/30 bg-red-500/5' : 'border-red-200 bg-red-50')}>
          <p className="text-sm text-red-600 dark:text-red-400">Failed to load policies</p>
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
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-slate-400 hover:text-blue-500"
                      onClick={(e) => { e.stopPropagation(); setEditingPolicy(p); }}>
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-slate-400 hover:text-red-500"
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
          {selectedPolicy && <div className="mt-4"><WafPolicyDetail resource={selectedPolicy} /></div>}
        </SheetContent>
      </Sheet>

      {/* Edit sheet — key forces remount on each open so JSON re-initialises */}
      <Sheet open={!!editingPolicy} onOpenChange={(open) => !open && setEditingPolicy(null)}>
        <SheetContent className="w-full sm:max-w-xl overflow-y-auto">
          <SheetHeader><SheetTitle>Edit Policy — {editingPolicy?.metadata.name}</SheetTitle></SheetHeader>
          {editingPolicy && (
            <EditPolicySheet
              key={`${editingPolicy.metadata.namespace}/${editingPolicy.metadata.name}/${editingPolicy.metadata.uid ?? editingPolicy.metadata.creationTimestamp}`}
              clusterId={clusterId}
              namespace={namespace}
              item={editingPolicy}
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
  const [saveError, setSaveError] = useState<string | null>(null);

  type SigData = { spec?: { 'attack-signatures'?: { revision?: string }; 'bot-signatures'?: { revision?: string }; 'threat-campaigns'?: { revision?: string } }; status?: { installationState?: string } } | null;
  const existing = sigData as SigData;

  const [attack, setAttack] = useState('latest');
  const [bot,    setBot]    = useState('latest');
  const [threat, setThreat] = useState('latest');

  // Populate fields once the CR data arrives from the cluster
  useEffect(() => {
    if (!existing) return;
    setAttack(existing.spec?.['attack-signatures']?.revision ?? 'latest');
    setBot(existing.spec?.['bot-signatures']?.revision    ?? 'latest');
    setThreat(existing.spec?.['threat-campaigns']?.revision  ?? 'latest');
  }, [existing]);

  const installState = existing?.status?.installationState;

  const handleSave = async () => {
    setSaveError(null);
    try {
      await upsert.mutateAsync({ namespace, spec: { 'attack-signatures': { revision: attack }, 'bot-signatures': { revision: bot }, 'threat-campaigns': { revision: threat } } });
    } catch (e) {
      setSaveError(extractApiError(e));
    }
  };

  return (
    <div className="space-y-4 max-w-md">
      <div className="flex items-start justify-between">
        <p className={cn('text-xs', isDark ? 'text-zinc-400' : 'text-slate-500')}>
          APSignatures is a singleton per namespace. Controls which attack/bot/threat-campaign signature package
          versions the Policy Controller embeds when compiling WAF policies.
          <strong className="block mt-1 text-amber-500">Warning: changes here trigger recompilation of ALL policies in this namespace.</strong>
        </p>
        <Button variant="ghost" size="sm" className="h-7 shrink-0" onClick={() => refetch()}>
          <RotateCcw className="h-3.5 w-3.5" />
        </Button>
      </div>

      {isLoading && <p className="text-xs text-muted-foreground">Loading…</p>}

      {!isLoading && (
        <>
          {installState && (
            <div className="flex items-center gap-2">
              <span className={cn('text-xs', isDark ? 'text-zinc-400' : 'text-slate-500')}>Installation state:</span>
              <Badge variant="outline" className={cn('text-[10px]',
                installState === 'success'   ? 'bg-green-500/10 text-green-600 border-green-500/20'
                : installState === 'failure' ? 'bg-red-500/10 text-red-600 border-red-500/20'
                                            : 'bg-slate-500/10 text-slate-600')}>
                {installState}
              </Badge>
              {installState === 'failure' && (
                <span className="text-xs text-amber-500">Requires a valid NGINX App Protect repository secret in the cluster.</span>
              )}
            </div>
          )}
          <div className="space-y-1.5">
            <Label>Attack Signatures Revision</Label>
            <Input value={attack} onChange={(e) => setAttack(e.target.value)} placeholder="latest" />
            <p className="text-xs text-muted-foreground">e.g. &quot;latest&quot; or &quot;2024.09.14&quot;</p>
          </div>
          <div className="space-y-1.5">
            <Label>Bot Signatures Revision</Label>
            <Input value={bot} onChange={(e) => setBot(e.target.value)} placeholder="latest" />
            <p className="text-xs text-muted-foreground">e.g. &quot;latest&quot; or &quot;2024.09.14&quot;</p>
          </div>
          <div className="space-y-1.5">
            <Label>Threat Campaigns Revision</Label>
            <Input value={threat} onChange={(e) => setThreat(e.target.value)} placeholder="latest" />
            <p className="text-xs text-muted-foreground">e.g. &quot;latest&quot; or &quot;2024.09.14&quot;</p>
          </div>
          {saveError && <InlineError message={saveError} />}
          <div className="flex gap-2">
            <Button size="sm" className="gap-1.5 bg-blue-600 hover:bg-blue-700" onClick={handleSave} disabled={upsert.isPending}>
              <RefreshCw className="h-3.5 w-3.5" /> Save Signature Settings
            </Button>
            <Button size="sm" variant="ghost" onClick={() => { setAttack(existing?.spec?.['attack-signatures']?.revision ?? 'latest'); setBot(existing?.spec?.['bot-signatures']?.revision ?? 'latest'); setThreat(existing?.spec?.['threat-campaigns']?.revision ?? 'latest'); setSaveError(null); }}>Reset</Button>
          </div>
        </>
      )}
    </div>
  );
}

// ── Edit sheet: APUserSig ─────────────────────────────────────────────────

function EditUserSigSheet({
  clusterId, namespace, item, onClose,
}: { clusterId: number; namespace: string; item: APUserSigResource; onClose: () => void }) {
  const [sigTag, setSigTag]         = useState(item.spec?.tag ?? '');
  const [sigVersion, setSigVersion] = useState(item.spec?.softwareVersion ?? '');
  const firstRule = item.spec?.signatures?.[0];
  const [sigRule, setSigRule] = useState(firstRule?.rule ?? '');
  const [submitError, setSubmitError] = useState<string | null>(null);

  const update = useUpdateWafUserSig(clusterId, item.metadata.name);

  const handleSave = async () => {
    setSubmitError(null);
    try {
      await update.mutateAsync({
        namespace: item.metadata.namespace ?? namespace,
        spec: {
          tag: sigTag.trim(),
          softwareVersion: sigVersion.trim() || undefined,
          signatures: sigRule.trim()
            ? [{ ...(firstRule ?? {}), name: item.metadata.name, rule: sigRule.trim(), signatureType: firstRule?.signatureType ?? 'request', risk: firstRule?.risk ?? 'medium', accuracy: firstRule?.accuracy ?? 'medium' }]
            : (item.spec?.signatures ?? []),
        },
      });
      onClose();
    } catch (e) {
      setSubmitError(extractApiError(e));
    }
  };

  return (
    <div className="space-y-4 mt-2">
      <p className="text-xs text-muted-foreground">Editing <span className="font-mono font-medium">{item.metadata.name}</span>. Name is immutable.</p>
      <div className="space-y-1.5">
        <Label>Tag <span className="text-red-500">*</span></Label>
        <Input value={sigTag} onChange={(e) => setSigTag(e.target.value)} placeholder="my-custom-tag" />
        <p className="text-xs text-muted-foreground">Referenced in policy JSON via signature-requirements[].tag</p>
      </div>
      <div className="space-y-1.5">
        <Label>Software Version</Label>
        <Input value={sigVersion} onChange={(e) => setSigVersion(e.target.value)} placeholder="1.0.0" />
      </div>
      <div className="space-y-1.5">
        <Label>Signature Rule (first rule)</Label>
        <Input value={sigRule} onChange={(e) => setSigRule(e.target.value)} placeholder='content:"attack-pattern"; nocase;' />
        <p className="text-xs text-muted-foreground">Editing the first rule only. Use kubectl for multi-rule configurations.</p>
      </div>
      {submitError && <InlineError message={submitError} />}
      <div className="flex gap-2">
        <Button size="sm" className="bg-blue-600 hover:bg-blue-700" onClick={handleSave} disabled={!sigTag.trim() || update.isPending}>Save</Button>
        <Button size="sm" variant="ghost" onClick={onClose}>Cancel</Button>
      </div>
    </div>
  );
}

// ============================================================================
// Tab 4: User Signatures (APUserSig) — custom attack signatures
// ============================================================================

function UserSigsTab({ clusterId, namespace, isDark }: { clusterId: number; namespace: string; isDark: boolean }) {
  const [showCreate, setShowCreate] = useState(false);
  const [editingItem, setEditingItem]   = useState<APUserSigResource | null>(null);
  const [deletingItem, setDeletingItem] = useState<APUserSigResource | null>(null);

  const [sigName, setSigName]   = useState('');
  const [sigTag, setSigTag]     = useState('');
  const [sigVersion, setSigVersion] = useState('');
  const [sigRule, setSigRule]   = useState('');
  const [sigCreateError, setSigCreateError] = useState<string | null>(null);

  const sigNameError = sigName ? validateK8sName(sigName) : null;

  const { data, isLoading } = useWafUserSigs(clusterId, namespace, { enabled: !!clusterId });
  const userSigs = useMemo(() => data?.user_sigs ?? [], [data]);
  const create   = useCreateWafUserSig(clusterId);
  const deleteSig = useDeleteWafUserSig(clusterId);

  const handleCreate = async () => {
    const nameErr = validateK8sName(sigName);
    if (nameErr) { setSigCreateError(nameErr); return; }
    if (!sigTag.trim()) { setSigCreateError('Tag is required.'); return; }
    setSigCreateError(null);
    try {
      await create.mutateAsync({
        name: sigName.trim(),
        namespace,
        spec: {
          tag: sigTag.trim(),
          softwareVersion: sigVersion.trim() || undefined,
          signatures: sigRule.trim() ? [{ name: sigName.trim(), rule: sigRule.trim(), signatureType: 'request', risk: 'medium', accuracy: 'medium' }] : [],
        },
      });
      setSigName(''); setSigTag(''); setSigVersion(''); setSigRule('');
      setShowCreate(false);
    } catch (e) {
      setSigCreateError(extractApiError(e));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className={cn('text-xs', isDark ? 'text-zinc-400' : 'text-slate-500')}>
          APUserSig CRs define custom attack signatures. They are embedded into compiled policies that reference them via <code>signature-requirements[].tag</code> in the policy JSON.
        </p>
        <Button size="sm" className="gap-1.5" onClick={() => setShowCreate(!showCreate)}>
          <Plus className="h-4 w-4" /> Create User Signature
        </Button>
      </div>

      {showCreate && (
        <div className={cn('rounded-lg border p-4 space-y-3', isDark ? 'border-zinc-800 bg-zinc-900/50' : 'border-slate-200 bg-white')}>
          <p className={cn('text-sm font-semibold', isDark ? 'text-white' : 'text-zinc-900')}>New User Signature</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 max-w-xl">
            <div className="space-y-1.5">
              <Label>Name <span className="text-red-500">*</span></Label>
              <Input
                value={sigName}
                onChange={(e) => { setSigName(e.target.value); setSigCreateError(null); }}
                placeholder="my-custom-sig"
                className={sigNameError ? 'border-red-500' : ''}
              />
              {sigNameError
                ? <p className="text-xs text-red-500 flex items-center gap-1"><AlertTriangle className="h-3 w-3" />{sigNameError}</p>
                : <p className="text-xs text-muted-foreground">Lowercase alphanumeric with hyphens (RFC 1123)</p>
              }
            </div>
            <div className="space-y-1.5">
              <Label>Tag <span className="text-red-500">*</span></Label>
              <Input value={sigTag} onChange={(e) => setSigTag(e.target.value)} placeholder="my-custom-tag" />
              <p className="text-xs text-muted-foreground">Referenced in policy JSON via signature-requirements[].tag</p>
            </div>
            <div className="space-y-1.5">
              <Label>Software Version</Label>
              <Input value={sigVersion} onChange={(e) => setSigVersion(e.target.value)} placeholder="1.0.0" />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label>Signature Rule (optional)</Label>
              <Input value={sigRule} onChange={(e) => setSigRule(e.target.value)} placeholder='content:"attack-pattern"; nocase;' />
              <p className="text-xs text-muted-foreground">NAP signature rule syntax. Leave empty to add rules later by editing the CR.</p>
            </div>
          </div>
          {sigCreateError && <InlineError message={sigCreateError} />}
          <div className="flex gap-2">
            <Button size="sm" className="bg-blue-600 hover:bg-blue-700" onClick={handleCreate} disabled={!!sigNameError || !sigName.trim() || !sigTag.trim() || create.isPending}>Create</Button>
            <Button size="sm" variant="ghost" onClick={() => { setShowCreate(false); setSigCreateError(null); }}>Cancel</Button>
          </div>
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
              <TableRow key={`${us.metadata.namespace}/${us.metadata.name}`}>
                <TableCell className="font-medium">{us.metadata.name}</TableCell>
                <TableCell className="text-xs font-mono">{us.spec?.tag ?? '—'}</TableCell>
                <TableCell className="text-xs">{(us.spec?.signatures ?? []).length}</TableCell>
                <TableCell>
                  <Badge variant="outline" className={cn('text-[10px]',
                    us.status?.installationState === 'success' ? 'bg-green-500/10 text-green-600 border-green-500/20'
                    : us.status?.installationState === 'failure' ? 'bg-red-500/10 text-red-600 border-red-500/20'
                    : 'bg-slate-500/10 text-slate-600 border-slate-500/20')}>
                    {us.status?.installationState ?? 'unknown'}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs">{formatAge(us.metadata.creationTimestamp)}</TableCell>
                <TableCell>
                  <div className="flex items-center gap-1">
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-slate-400 hover:text-blue-500" onClick={() => setEditingItem(us)}>
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-slate-400 hover:text-red-500" onClick={() => setDeletingItem(us)}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {/* Edit sheet — key forces remount so state re-initialises from latest item */}
      <Sheet open={!!editingItem} onOpenChange={(open) => !open && setEditingItem(null)}>
        <SheetContent className="w-full sm:max-w-lg overflow-y-auto">
          <SheetHeader><SheetTitle>Edit User Signature — {editingItem?.metadata.name}</SheetTitle></SheetHeader>
          {editingItem && (
            <EditUserSigSheet
              key={`${editingItem.metadata.namespace}/${editingItem.metadata.name}/${editingItem.metadata.uid ?? editingItem.metadata.creationTimestamp}`}
              clusterId={clusterId}
              namespace={namespace}
              item={editingItem}
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
          <p className={cn('text-sm', isDark ? 'text-zinc-400' : 'text-slate-500')}>
            Manage all 4 App Protect WAF CRDs. Requires the nap-policy-operator PLM chart installed on the target cluster.
          </p>
        </div>

        {/* Cluster + Namespace pickers */}
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <span className={cn('text-xs shrink-0', isDark ? 'text-zinc-400' : 'text-slate-500')}>Cluster:</span>
            <Select value={clusterId ? String(clusterId) : undefined} onValueChange={(v) => setSelectedCluster(Number(v))}>
              <SelectTrigger className="w-56 h-9"><SelectValue placeholder="Select a cluster" /></SelectTrigger>
              <SelectContent>
                {clusters.map((c) => <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <span className={cn('text-xs shrink-0', isDark ? 'text-zinc-400' : 'text-slate-500')}>Namespace:</span>
            {clusterId
              ? <NamespacePicker clusterId={clusterId} value={namespace} onChange={setNamespace} />
              : <Input value={namespace} onChange={(e) => setNamespace(e.target.value)} className="w-36 h-9 text-sm" placeholder="default" />
            }
          </div>
        </div>

        {/* Tabs */}
        <div className={cn('flex gap-1 p-1 rounded-lg border flex-wrap', isDark ? 'bg-zinc-900/50 border-zinc-800' : 'bg-zinc-100 border-zinc-200')}>
          {TABS.map((t) => {
            const Icon = t.icon;
            return (
              <button
                key={t.key}
                className={cn(
                  'flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium transition-colors',
                  activeTab === t.key
                    ? isDark ? 'bg-zinc-800 text-white shadow-sm' : 'bg-white text-zinc-900 shadow-sm'
                    : isDark ? 'text-zinc-400 hover:text-zinc-200' : 'text-zinc-500 hover:text-zinc-700'
                )}
                onClick={() => setActiveTab(t.key)}
              >
                <Icon className="h-4 w-4" /> {t.label}
              </button>
            );
          })}
        </div>

        {/* Active tab description */}
        <p className={cn('text-xs -mt-2', isDark ? 'text-zinc-500' : 'text-slate-400')}>
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
