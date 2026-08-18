/**
 * APLogConfForm — full-field tabbed form for APLogConf CRs.
 *
 * Tabs: Content (all fields + request_type filter), { } JSON (live preview)
 * All fields on one tab so users see everything without navigating.
 *
 * CRD versions: v1 (storage=true, recommended) and v1beta1 (served but not default storage).
 * The schemas are identical — version affects only the apiVersion header in the CR.
 */

import { useEffect, useState } from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { AlertTriangle, Info, Plus, Trash2, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { WafWizardFrame } from './WafWizardFrame';
import { validateK8sName, extractApiError } from './waf-utils';
import { useQueryClient } from '@tanstack/react-query';
import { useCreateWafLogConf, useUpdateWafLogConf, useWafLogConfs } from '@/hooks/useWafPolicies';
import { queryKeys } from '@/lib/queryKeys';
import { WafFormToolbar } from './WafFormToolbar';
import { saveDraft, type WafDraft } from '@/lib/waf-drafts';
import type { APLogConfResource } from '@/types';

function FieldRow({ label, hint, required, children, span2 }: {
  label: string; hint?: string; required?: boolean; children: React.ReactNode; span2?: boolean;
}) {
  return (
    <div className={cn('space-y-1.5', span2 && 'col-span-2')}>
      <Label className="text-xs">{label}{required && <span className="text-red-500 ml-0.5">*</span>}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

interface APLogConfFormProps {
  clusterId: number;
  namespace: string;
  existingItem?: APLogConfResource | null;
  onClose: () => void;
  /** Pre-fill from a cloned source (create mode only) */
  cloneSource?: APLogConfResource | null;
}

export function APLogConfForm({ clusterId, namespace, existingItem, onClose, cloneSource }: APLogConfFormProps) {
  const isEdit = !!existingItem;
  // In create mode, initialise from cloneSource if provided
  const seedItem = isEdit ? existingItem : (cloneSource ?? null);
  const content = seedItem?.spec?.content;
  const filter = seedItem?.spec?.filter;

  // Fetch all log confs in current namespace for clone picker
  const { data: allLogConfs } = useWafLogConfs(clusterId, namespace, { enabled: !isEdit });

  // API version selector
  const [apiVersion, setApiVersion] = useState<'v1' | 'v1beta1'>('v1');

  const [crName, setCrName] = useState(existingItem?.metadata.name ?? '');
  const [format, setFormat] = useState<'default' | 'splunk' | 'arcsight' | 'user-defined' | 'grpc'>(content?.format ?? 'default');
  const [formatString, setFormatString] = useState(content?.format_string ?? '');
  const [listDelimiter, setListDelimiter] = useState(content?.list_delimiter ?? ',');
  const [listPrefix, setListPrefix] = useState(content?.list_prefix ?? '');
  const [listSuffix, setListSuffix] = useState(content?.list_suffix ?? '');
  const [maxMsgSize, setMaxMsgSize] = useState(content?.max_message_size ?? '10k');
  const [maxReqSize, setMaxReqSize] = useState(content?.max_request_size ?? 'any');
  const [escapingPairs, setEscapingPairs] = useState<Array<{ from: string; to: string }>>(content?.escaping_characters ?? []);
  const [requestType, setRequestType] = useState<'all' | 'illegal' | 'blocked'>(filter?.request_type ?? 'illegal');
  const [activeTab, setActiveTab] = useState('content');
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Live JSON state — syncs from form (one-way unless user edits JSON tab)
  const [jsonText, setJsonText] = useState('');
  const [jsonError, setJsonError] = useState<string | null>(null);

  // Build the current spec object from form state
  const buildSpec = () => ({
    content: {
      format,
      ...(formatString.trim() ? { format_string: formatString } : {}),
      ...(listDelimiter !== ',' ? { list_delimiter: listDelimiter } : {}),
      ...(listPrefix ? { list_prefix: listPrefix } : {}),
      ...(listSuffix ? { list_suffix: listSuffix } : {}),
      max_message_size: maxMsgSize,
      max_request_size: maxReqSize,
      ...(escapingPairs.length > 0 ? { escaping_characters: escapingPairs } : {}),
    },
    filter: { request_type: requestType },
  });

  // Sync JSON textarea from form state whenever form changes (except when JSON tab active)
  useEffect(() => {
    if (activeTab !== 'json') {
      const cr = {
        apiVersion: `appprotect.f5.com/${apiVersion}`,
        kind: 'APLogConf',
        metadata: { name: crName || '<name>', namespace },
        spec: buildSpec(),
      };
      setJsonText(JSON.stringify(cr, null, 2));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [format, formatString, listDelimiter, listPrefix, listSuffix, maxMsgSize, maxReqSize, escapingPairs, requestType, crName, apiVersion, activeTab]);

  // When user edits JSON tab, parse and reflect back to form state
  const handleJsonEdit = (text: string) => {
    setJsonText(text);
    try {
      const parsed = JSON.parse(text) as { spec?: { content?: Record<string, unknown>; filter?: { request_type?: string } } };
      setJsonError(null);
      const c = parsed.spec?.content ?? {};
      if (c.format) setFormat(c.format as typeof format);
      if (typeof c.format_string === 'string') setFormatString(c.format_string);
      if (typeof c.list_delimiter === 'string') setListDelimiter(c.list_delimiter);
      if (typeof c.list_prefix === 'string') setListPrefix(c.list_prefix);
      if (typeof c.list_suffix === 'string') setListSuffix(c.list_suffix);
      if (typeof c.max_message_size === 'string') setMaxMsgSize(c.max_message_size);
      if (typeof c.max_request_size === 'string') setMaxReqSize(c.max_request_size);
      const rt = parsed.spec?.filter?.request_type;
      if (rt === 'all' || rt === 'illegal' || rt === 'blocked') setRequestType(rt);
    } catch (e) {
      setJsonError(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  // Validation
  const crNameError = crName ? validateK8sName(crName) : (isEdit ? null : 'Name is required.');
  // Exact CRD patterns from kubectl get crd aplogconfs.appprotect.f5.com
  const MSG_SIZE_RE = /^([1-9]|[1-5][0-9]|6[0-4])k$/;
  const REQ_SIZE_RE = /^(any|[1-9]|[1-9][0-9]|[1-9][0-9]{2}|[1-9][0-9]{3}|10[0-1][0-9][0-9]|102[0-3][0-9]|1024[0]|[1-9]k|10k)$/;
  const msgSizeError = maxMsgSize && maxMsgSize !== '10k' && !MSG_SIZE_RE.test(maxMsgSize)
    ? 'Must be 1k–64k (e.g. "16k"). Only values like 1k,2k,...,64k are valid.' : null;
  const reqSizeError = maxReqSize && maxReqSize !== 'any' && !REQ_SIZE_RE.test(maxReqSize)
    ? 'Must be 1–10240, or 1k–10k, or "any". Note: only 1k–10k are valid with "k" suffix.' : null;
  // Cross-validation: message size must not exceed request size when both are numeric k-values
  const crossSizeError = (() => {
    const msgK = MSG_SIZE_RE.test(maxMsgSize) ? parseInt(maxMsgSize) * 1024 : null;
    const reqN = /^[0-9]+$/.test(maxReqSize) ? parseInt(maxReqSize) : /^[0-9]+k$/.test(maxReqSize) ? parseInt(maxReqSize) * 1024 : null;
    if (msgK && reqN && msgK > reqN) return `Max Message Size (${maxMsgSize} = ${msgK} bytes) must not exceed Max Request Size (${maxReqSize} = ${reqN} bytes).`;
    return null;
  })();
  const formatStringError = format === 'user-defined' && !formatString.trim() ? 'Format string is required for user-defined format.' : null;

  const createMutation = useCreateWafLogConf(clusterId);
  const updateMutation = useUpdateWafLogConf(clusterId, existingItem?.metadata.name ?? '');
  const queryClient = useQueryClient();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [compileState, setCompileState] = useState<string | null>(null);
  const [clusterErrors, setClusterErrors] = useState<string[] | null>(null);
  const isPending = isSubmitting || (!!compileState && compileState !== 'ready' && compileState !== 'invalid');

  const contentErrors = [
    ...(!isEdit && crNameError ? [crNameError] : []),
    ...(formatStringError ? [formatStringError] : []),
    ...(msgSizeError ? [msgSizeError] : []),
    ...(reqSizeError ? [reqSizeError] : []),
    ...(crossSizeError ? [crossSizeError] : []),
  ];

  const handleSubmit = async () => {
    setSubmitError(null);
    setClusterErrors(null);
    setCompileState(null);
    setIsSubmitting(true);
    try {
      const spec = buildSpec();
      if (isEdit) {
        await updateMutation.mutateAsync({ namespace: existingItem?.metadata.namespace ?? namespace, spec });
        setIsSubmitting(false);
        // Poll bundle state after update to surface compiler errors
        setCompileState('pending');
        const lcName = existingItem?.metadata.name ?? '';
        const ns = existingItem?.metadata.namespace ?? namespace;
        const poll = setInterval(async () => {
          try {
            await queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.wafLogConfs(clusterId, ns) });
            const fresh = queryClient.getQueryData<{ log_confs: APLogConfResource[] }>(queryKeys.k8s.clusters.wafLogConfs(clusterId, ns));
            const lc = fresh?.log_confs?.find(x => x.metadata.name === lcName);
            const state = (lc?.status as { bundle?: { state?: string } } | undefined)?.bundle?.state;
            if (state) setCompileState(state);
            if (state === 'ready') { clearInterval(poll); setTimeout(onClose, 800); }
            if (state === 'invalid') {
              clearInterval(poll);
              const errs = (lc?.status as { processing?: { errors?: string[] } } | undefined)?.processing?.errors;
              setClusterErrors(errs && errs.length > 0 ? errs : ['Log profile compilation failed — check the configuration.']);
            }
          } catch { /* ignore polling errors */ }
        }, 2500);
        setTimeout(() => { clearInterval(poll); }, 90_000);
      } else {
        await createMutation.mutateAsync({ name: crName, namespace, spec });
        setIsSubmitting(false);
        onClose();
      }
    } catch (e) {
      setSubmitError(extractApiError(e));
      setIsSubmitting(false);
    }
  };

  const contentTab = (
    <div className="grid grid-cols-2 gap-4">
      {/* API version selector only shown on create — immutable on existing CRs */}
      {!isEdit && (
        <div className="col-span-2 flex items-start gap-3 rounded-md border border-slate-200 dark:border-zinc-700 p-3 bg-slate-50 dark:bg-zinc-900/50">
          <Info className="h-3.5 w-3.5 mt-0.5 shrink-0 text-blue-500" />
          <div className="flex-1 space-y-2">
            <div className="flex items-center gap-3">
              <span className="text-xs font-medium">API Version:</span>
              <div className="flex gap-2">
                {(['v1', 'v1beta1'] as const).map(v => (
                  <button key={v} type="button" onClick={() => setApiVersion(v)}
                    className={cn('px-2.5 py-0.5 rounded text-xs font-mono border transition-colors',
                      apiVersion === v ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 dark:border-zinc-600 text-slate-600 dark:text-zinc-400 hover:border-blue-400')}>{v}</button>
                ))}
              </div>
              {apiVersion === 'v1' && <Badge variant="outline" className="text-[10px] bg-green-500/10 text-green-600 border-green-500/20">recommended</Badge>}
              {apiVersion === 'v1beta1' && <Badge variant="outline" className="text-[10px] bg-amber-500/10 text-amber-600 border-amber-500/20">not default storage</Badge>}
            </div>
            <p className="text-xs text-muted-foreground">
              Both versions have identical field schemas. <strong>v1</strong> is the storage version (recommended).
              v1beta1 is served but not stored by default.
            </p>
          </div>
        </div>
      )}

      {/* CR Name — read-only in edit (immutable), required input on create */}
      {isEdit ? (
        <FieldRow label="Name (metadata.name)" hint="Kubernetes resource names are immutable. Delete and recreate to rename.">
          <div className="flex items-center gap-2">
            <code className="flex-1 px-3 py-2 rounded-md border border-input bg-muted text-sm font-mono opacity-75 select-all">{crName}</code>
            <Badge variant="outline" className="text-[10px] shrink-0">immutable</Badge>
          </div>
        </FieldRow>
      ) : (
        <FieldRow label="Name (metadata.name)" required hint="Lowercase alphanumeric with hyphens, max 63 chars.">
          <Input value={crName} onChange={e => setCrName(e.target.value)} placeholder="my-log-profile"
            className={crNameError ? 'border-red-500' : ''} />
          {crNameError && <p className="text-xs text-red-500 flex items-center gap-1 mt-1"><AlertTriangle className="h-3 w-3" />{crNameError}</p>}
        </FieldRow>
      )}

      {/* ── Content fields ── */}
      <FieldRow label="Format" hint="Output format for security event log entries.">
        <Select value={format} onValueChange={(v) => setFormat(v as typeof format)}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent position="popper" className="min-w-max">
            <SelectItem value="default">default — structured NAP JSON (recommended)</SelectItem>
            <SelectItem value="splunk">splunk — Splunk HEC key=value pairs</SelectItem>
            <SelectItem value="arcsight">arcsight — ArcSight CEF syslog format</SelectItem>
            <SelectItem value="user-defined">user-defined — custom % field format string</SelectItem>
            <SelectItem value="grpc">grpc — stream to remote gRPC collector</SelectItem>
          </SelectContent>
        </Select>
      </FieldRow>

      {/* format_string — always visible, required only for user-defined */}
      <FieldRow
        label="Format String"
        required={format === 'user-defined'}
        hint={
          format === 'user-defined'
            ? 'Required. Comma-separated NAP log field names prefixed with %. E.g. %date_time,%src_ip,%request,%blocking_exception_reason'
            : 'Only used when format is "user-defined". Ignored for other formats.'
        }
      >
        <Input
          value={formatString}
          onChange={e => setFormatString(e.target.value)}
          placeholder="%date_time,%src_ip,%request,%response_code,%blocking_exception_reason"
          className={cn(formatStringError ? 'border-red-500' : '', format !== 'user-defined' ? 'opacity-50' : '')}
          disabled={format !== 'user-defined'}
        />
        {formatStringError && <p className="text-xs text-red-500 mt-1">{formatStringError}</p>}
      </FieldRow>

      {/* ── Filter ── */}
      <FieldRow
        label="Request Type Filter (filter.request_type)"
        hint="Which HTTP transactions generate a security event log entry."
        span2
      >
        <div className="grid grid-cols-3 gap-2">
          {([
            { value: 'illegal', label: 'illegal', desc: 'Requests that violated a policy rule', color: 'text-amber-600' },
            { value: 'blocked', label: 'blocked', desc: 'Requests that were actually blocked', color: 'text-red-600' },
            { value: 'all',     label: 'all',     desc: 'Every request (very high volume)', color: 'text-slate-500' },
          ] as const).map(opt => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setRequestType(opt.value)}
              className={cn(
                'flex flex-col items-start gap-0.5 rounded-md border p-2.5 text-left transition-colors',
                requestType === opt.value
                  ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20'
                  : 'border-slate-200 dark:border-zinc-700 hover:border-blue-300'
              )}
            >
              <span className={cn('text-xs font-semibold font-mono', opt.color)}>{opt.label}</span>
              <span className="text-[11px] text-muted-foreground leading-tight">{opt.desc}</span>
            </button>
          ))}
        </div>
        {requestType === 'all' && (
          <p className="text-xs text-amber-500 flex items-center gap-1 mt-1">
            <AlertTriangle className="h-3 w-3" /> "all" logs every request — this fills storage quickly in production.
          </p>
        )}
      </FieldRow>

      {/* ── Size limits ── */}
      <FieldRow label="Max Message Size" hint="Maximum size of a single log event message. Range: 1k–64k. Default: 10k.">
        <Input value={maxMsgSize} onChange={e => setMaxMsgSize(e.target.value)} placeholder="10k" className={msgSizeError ? 'border-red-500' : ''} />
        {msgSizeError
          ? <p className="text-xs text-red-500 mt-1">{msgSizeError}</p>
          : <p className="text-xs text-muted-foreground">Valid values: 1k, 2k, … 64k</p>}
      </FieldRow>

      <FieldRow label="Max Request Size" hint="Maximum logged request body size. Valid: 1–10240, 1k–10k, or 'any'.">
        <Input value={maxReqSize} onChange={e => setMaxReqSize(e.target.value)} placeholder="any" className={reqSizeError ? 'border-red-500' : ''} />
        {reqSizeError
          ? <p className="text-xs text-red-500 mt-1">{reqSizeError}</p>
          : <p className="text-xs text-muted-foreground">Valid: 1–10240 (bytes), 1k–10k, or &quot;any&quot;</p>}
        {crossSizeError && <p className="text-xs text-red-500 mt-1">{crossSizeError}</p>}
      </FieldRow>

      {/* ── List formatting ── */}
      <FieldRow label="List Delimiter" hint="Character separating list field values in log output. Default: ','">
        <Input value={listDelimiter} onChange={e => setListDelimiter(e.target.value)} placeholder="," />
      </FieldRow>

      <div className="col-span-2 grid grid-cols-2 gap-4">
        <FieldRow label="List Prefix" hint='String prepended to every list-type field value. E.g. "[" to open a bracket.'>
          <Input value={listPrefix} onChange={e => setListPrefix(e.target.value)} placeholder='e.g. [' />
        </FieldRow>
        <FieldRow label="List Suffix" hint='String appended after every list-type field value. E.g. "]" to close a bracket.'>
          <Input value={listSuffix} onChange={e => setListSuffix(e.target.value)} placeholder='e.g. ]' />
        </FieldRow>
      </div>

      {/* ── Escaping ── */}
      <div className="col-span-2 space-y-2">
        <Label className="text-xs">Escaping Characters (escaping_characters)</Label>
        <p className="text-xs text-muted-foreground">
          Character substitution pairs applied to log field values before output.
          Common use: escape double-quotes in Splunk/CEF formats (<span className="font-mono">"</span> → <span className="font-mono">\"</span>).
        </p>
        {escapingPairs.map((pair, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="text-[10px] text-muted-foreground w-8 shrink-0">from</span>
            <Input value={pair.from} onChange={e => setEscapingPairs(ps => ps.map((p, idx) => idx === i ? { ...p, from: e.target.value } : p))}
              placeholder='"' className="h-8 text-xs font-mono flex-1" />
            <span className="text-xs text-muted-foreground shrink-0">→</span>
            <span className="text-[10px] text-muted-foreground w-4 shrink-0">to</span>
            <Input value={pair.to} onChange={e => setEscapingPairs(ps => ps.map((p, idx) => idx === i ? { ...p, to: e.target.value } : p))}
              placeholder='\"' className="h-8 text-xs font-mono flex-1" />
            <Button type="button" variant="ghost" size="sm" className="h-8 w-8 p-0 shrink-0"
              onClick={() => setEscapingPairs(ps => ps.filter((_, idx) => idx !== i))}>
              <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-red-500" />
            </Button>
          </div>
        ))}
        <Button type="button" variant="outline" size="sm" className="h-7 text-xs gap-1"
          onClick={() => setEscapingPairs(ps => [...ps, { from: '', to: '' }])}>
          <Plus className="h-3 w-3" /> Add escape mapping
        </Button>
      </div>
    </div>
  );

  const jsonTab = (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        Live CR preview based on current form values. Edits here reflect immediately in the form fields above.
      </p>
      <Textarea
        value={jsonText}
        onChange={e => handleJsonEdit(e.target.value)}
        rows={22}
        className="font-mono text-xs"
        spellCheck={false}
      />
      {jsonError && (
        <p className="text-xs text-red-500 flex items-center gap-1">
          <AlertTriangle className="h-3 w-3" />{jsonError}
        </p>
      )}
    </div>
  );

  const tabs = [
    { key: 'content', label: 'Content', validate: () => contentErrors },
    { key: 'json',    label: '{ } JSON', validate: () => jsonError ? [jsonError] : [] },
  ];

  // Auto-save draft on every field change (create mode only)
  useEffect(() => {
    if (isEdit || !crName) return;
    const t = setTimeout(() => {
      saveDraft('APLogConf', { name: crName, namespace, data: { crName, format, formatString, listDelimiter, listPrefix, listSuffix, maxMsgSize, maxReqSize, escapingPairs, requestType, apiVersion } });
    }, 800);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [crName, format, formatString, listDelimiter, listPrefix, listSuffix, maxMsgSize, maxReqSize, escapingPairs, requestType, apiVersion]);

  // Handlers for toolbar actions
  const handleClone = (src: { spec: Record<string, unknown> }) => {
    const c = (src.spec.content ?? {}) as Record<string, unknown>;
    const f = (src.spec.filter ?? {}) as Record<string, unknown>;
    if (c.format) setFormat(c.format as typeof format);
    if (typeof c.format_string === 'string') setFormatString(c.format_string);
    if (typeof c.list_delimiter === 'string') setListDelimiter(c.list_delimiter);
    if (typeof c.list_prefix === 'string') setListPrefix(c.list_prefix);
    if (typeof c.list_suffix === 'string') setListSuffix(c.list_suffix);
    if (typeof c.max_message_size === 'string') setMaxMsgSize(c.max_message_size);
    if (typeof c.max_request_size === 'string') setMaxReqSize(c.max_request_size);
    if (Array.isArray(c.escaping_characters)) setEscapingPairs(c.escaping_characters as Array<{from:string;to:string}>);
    const rt = (f.request_type as string) ?? 'illegal';
    if (rt === 'all' || rt === 'illegal' || rt === 'blocked') setRequestType(rt);
  };

  const handleRestoreDraft = (draft: WafDraft) => {
    const d = draft.data;
    if (typeof d.crName === 'string') setCrName(d.crName);
    handleClone({ spec: { content: { format: d.format, format_string: d.formatString, list_delimiter: d.listDelimiter, list_prefix: d.listPrefix, list_suffix: d.listSuffix, max_message_size: d.maxMsgSize, max_request_size: d.maxReqSize, escaping_characters: d.escapingPairs }, filter: { request_type: d.requestType } } });
  };

  const handleImport = (raw: Record<string, unknown>) => {
    const spec = (raw.spec ?? raw) as Record<string, unknown>;
    handleClone({ spec });
    if (raw.metadata && typeof (raw.metadata as Record<string, unknown>).name === 'string') {
      setCrName('copy-of-' + ((raw.metadata as Record<string, unknown>).name as string));
    }
  };

  const cloneSources = (allLogConfs?.log_confs ?? []).map((lc: APLogConfResource) => ({
    name: lc.metadata.name, namespace: lc.metadata.namespace ?? namespace, spec: lc.spec as Record<string, unknown> ?? {},
  }));

  const toolbar = !isEdit ? (
    <WafFormToolbar
      kind="APLogConf"
      currentState={{ crName, format, formatString, listDelimiter, listPrefix, listSuffix, maxMsgSize, maxReqSize, escapingPairs, requestType, apiVersion }}
      currentLabel={crName}
      onClone={handleClone}
      onRestoreDraft={handleRestoreDraft}
      onImport={handleImport}
      cloneSources={cloneSources}
    />
  ) : undefined;

  return (
    <WafWizardFrame
      tabs={tabs.map(t => ({ ...t, content: t.key === 'content' ? contentTab : jsonTab }))}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      allErrors={contentErrors}
      toolbar={toolbar}
      isPending={isPending}
      submitLabel={
        compileState && compileState !== 'ready' && compileState !== 'invalid'
          ? `Compiling… (${compileState})`
          : isEdit ? 'Save Log Profile' : 'Create Log Profile'
      }
      onSubmit={handleSubmit}
      onCancel={onClose}
      submitError={clusterErrors ? `Cluster error: ${clusterErrors.join(' | ')}` : submitError}
      statusNote={
        compileState && compileState !== 'ready' && compileState !== 'invalid' ? (
          <span className="text-xs text-blue-600 dark:text-blue-400 flex items-center gap-1">
            <RefreshCw className="h-3 w-3 animate-spin" />
            Compiling… <span className="font-mono">{compileState}</span>
          </span>
        ) : compileState === 'invalid' ? (
          <span className="text-xs text-red-500">❌ Compilation failed — see error above</span>
        ) : undefined
      }
    />
  );
}
