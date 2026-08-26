/**
 * APPolicyForm — full-field tabbed form for creating / editing an APPolicy CR.
 * Every field is interactive — no raw JSON textareas.
 * Full schema: https://docs.nginx.com/nginx-app-protect-waf/declarative-policy/policy/
 */

import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/queryKeys';
import { WafFormToolbar } from './WafFormToolbar';
import { saveDraft, type WafDraft } from '@/lib/waf-drafts';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { AlertTriangle, ExternalLink, Info, Plus, RefreshCw, Trash2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { WafWizardFrame } from './WafWizardFrame';
import { validateK8sName, extractApiError } from './waf-utils';
import { useCreateWafPolicy, useUpdateWafPolicy, useWafPolicies } from '@/hooks/useWafPolicies';
import type { APPolicyResource } from '@/types';

// ── Known NAP values ───────────────────────────────────────────────────────

const SIGNATURE_SETS = [
  'Generic Detection Signatures',
  'SQL Injection Signatures',
  'XSS Signatures',
  'Command Execution Signatures',
  'Path Traversal Signatures',
  'Remote File Include Signatures',
  'Server Side Code Injection Signatures',
  'HTTP Response Splitting Signatures',
  'Predictable Resource Location Signatures',
  'Information Leakages Signatures',
  'Directory Indexing Signatures',
  'Authentication/Authorization Attack Signatures',
  'Cross-Site Request Forgery Signatures',
  'DNS Tunneling Request Signatures',
  'OWA Exchange Exploitation Signatures',
  'Apache Struts Exploitation Signatures',
  'Insecure Deserialization Signatures',
  'Evasion Technique Signatures',
  'CVE Signatures',
  'Bot Signatures',
];

const SERVER_TECHNOLOGIES = [
  'Apache Tomcat', 'Apache/NCSA HTTP Server', 'BEA Systems WebLogic Server',
  'Cisco Local Director', 'Cold Fusion', 'Microsoft ASP.NET', 'Microsoft IIS',
  'Node.js', 'Nginx', 'Oracle Application Server', 'Oracle Identity Manager',
  'PHP', 'Perl', 'Python', 'PostgreSQL', 'MySQL', 'Oracle Database',
  'MongoDB', 'Redis', 'WordPress', 'Drupal', 'Joomla', 'SharePoint',
  'Spring Boot', 'Ruby on Rails', 'Django', 'Flask', 'Express.js',
  'Java Servlets/JSP', 'Go', 'Rust',
];

const NAP_VIOLATIONS = [
  'VIOL_PARAMETER_VALUE_BASE64', 'VIOL_ATTACK_SIGNATURE', 'VIOL_FILETYPE',
  'VIOL_COOKIE_EXPIRED', 'VIOL_COOKIE_LENGTH', 'VIOL_COOKIE_MALFORMED',
  'VIOL_COOKIE_MODIFIED', 'VIOL_DATA_GUARD', 'VIOL_EVASION',
  'VIOL_HEADER_LENGTH', 'VIOL_HTTP_PROTOCOL', 'VIOL_HTTP_RESPONSE_STATUS',
  'VIOL_MALICIOUS_IP', 'VIOL_MANDATORY_HEADER', 'VIOL_MANDATORY_PARAMETER',
  'VIOL_PARAMETER', 'VIOL_PARAMETER_ARRAY_VALUE', 'VIOL_PARAMETER_EMPTY_VALUE',
  'VIOL_PARAMETER_LOCATION', 'VIOL_PARAMETER_MULTIPART_NULL_VALUE',
  'VIOL_PARAMETER_NAME_METACHAR', 'VIOL_PARAMETER_NUMERIC_VALUE',
  'VIOL_PARAMETER_STATIC_VALUE', 'VIOL_PARAMETER_VALUE_LENGTH',
  'VIOL_PARAMETER_VALUE_METACHAR', 'VIOL_PARAMETER_VALUE_REGEXP',
  'VIOL_POST_DATA_LENGTH', 'VIOL_QUERY_STRING_LENGTH',
  'VIOL_REQUEST_MAX_LENGTH', 'VIOL_THREAT_CAMPAIGN', 'VIOL_URL',
  'VIOL_URL_LENGTH', 'VIOL_URL_METACHAR', 'VIOL_USERNAME',
];

const HTTP_PROTOCOL_VIOLATIONS = [
  'Bad HTTP version', 'Body in GET or HEAD requests', 'Check maximum number of parameters',
  'Check maximum number of headers', 'Chunked request with Content-Length header',
  'Content length should be a positive number', 'Evasion technique detected',
  'Host header contains IP address', 'Maximum cookie header length exceeded',
  'Maximum header count exceeded', 'Maximum header length exceeded',
  'Maximum number of cookies exceeded', 'Null in request', 'POST request with Content-Length: 0',
  'Several Content-Length headers', 'Unparsable request content',
  'No Host header in HTTP/1.1 request', 'Multiple Host headers',
];

const EVASION_TECHNIQUES = [
  '%u decoding', 'Apache whitespace', 'Bare byte decoding', 'Base64 decoding',
  'Directory traversals', 'HTML entities decoding', 'IIS backslashes',
  'IIS Unicode codepoints', 'Multiple decoding', 'Apache whitespace',
];

// ── Shared helpers ─────────────────────────────────────────────────────────

function FieldRow({ label, hint, required, children, span2 }: {
  label: string; hint?: string; required?: boolean; children: React.ReactNode; span2?: boolean;
}) {
  return (
    <div className={cn('space-y-1.5', span2 && 'col-span-2')}>
      <Label className="text-xs">{label}{required && <span className="text-destructive ml-0.5">*</span>}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mb-3 mt-5 first:mt-0">{children}</p>;
}

// ── Multi-select chip widget for string list fields ────────────────────────
// Used for: server-technologies, signature-requirements (tags), sensitive-parameters

function TagInput({ values, onChange, placeholder, suggestions }: {
  values: string[]; onChange: (v: string[]) => void; placeholder?: string; suggestions?: string[];
}) {
  const [input, setInput] = useState('');
  const add = (v: string) => { const t = v.trim(); if (t && !values.includes(t)) { onChange([...values, t]); setInput(''); } };
  const remove = (v: string) => onChange(values.filter(x => x !== v));
  const filtered = suggestions?.filter(s => s.toLowerCase().includes(input.toLowerCase()) && !values.includes(s)) ?? [];
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap gap-1 min-h-8 p-1.5 rounded-md border border-input bg-background">
        {values.map(v => (
          <span key={v} className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-xs bg-primary/10 dark:bg-primary/20/50 text-primary dark:text-primary/70">
            {v}
            <button type="button" onClick={() => remove(v)} className="ml-0.5 opacity-60 hover:opacity-100"><X className="h-2.5 w-2.5" /></button>
          </span>
        ))}
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(input); } }}
          placeholder={values.length === 0 ? placeholder : ''}
          className="flex-1 min-w-24 bg-transparent text-xs outline-none placeholder:text-muted-foreground"
        />
      </div>
      {input && filtered.length > 0 && (
        <div className="rounded-md border border-input bg-background shadow-sm max-h-40 overflow-y-auto">
          {filtered.slice(0, 8).map(s => (
            <button key={s} type="button" onClick={() => add(s)}
              className="w-full text-left px-2.5 py-1 text-xs hover:bg-accent hover:text-accent-foreground">
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Toggle list for NAP boolean fields ─────────────────────────────────────
// Used for: violations (alarm/block), http-protocols (enabled), evasions (enabled)

interface ToggleEntry { name: string; alarm?: boolean; block?: boolean; enabled?: boolean; }

function ToggleList({ values, onChange, known, mode, hint }: {
  values: ToggleEntry[]; onChange: (v: ToggleEntry[]) => void;
  known: string[]; mode: 'alarm-block' | 'enabled'; hint?: string;
}) {
  const [adding, setAdding] = useState('');
  const update = (name: string, field: string, val: boolean) =>
    onChange(values.map(v => v.name === name ? { ...v, [field]: val } : v));
  const addEntry = (name: string) => {
    if (!name.trim() || values.find(v => v.name === name)) return;
    onChange([...values, mode === 'alarm-block' ? { name, alarm: true, block: false } : { name, enabled: true }]);
    setAdding('');
  };
  const remove = (name: string) => onChange(values.filter(v => v.name !== name));
  const notAdded = known.filter(k => !values.find(v => v.name === k));

  return (
    <div className="space-y-2">
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {values.length > 0 && (
        <div className="rounded-md border border-input divide-y divide-border">
          {values.map(entry => (
            <div key={entry.name} className="flex items-center gap-2 px-2.5 py-1.5 text-xs">
              <span className="flex-1 font-mono truncate" title={entry.name}>{entry.name}</span>
              {mode === 'alarm-block' ? (
                <>
                  <label className="flex items-center gap-1 cursor-pointer">
                    <input type="checkbox" checked={!!entry.alarm}
                      onChange={e => update(entry.name, 'alarm', e.target.checked)} className="h-3.5 w-3.5" />
                    <span className="text-warning">alarm</span>
                  </label>
                  <label className="flex items-center gap-1 cursor-pointer">
                    <input type="checkbox" checked={!!entry.block}
                      onChange={e => update(entry.name, 'block', e.target.checked)} className="h-3.5 w-3.5" />
                    <span className="text-destructive">block</span>
                  </label>
                </>
              ) : (
                <label className="flex items-center gap-1 cursor-pointer">
                  <input type="checkbox" checked={!!entry.enabled}
                    onChange={e => update(entry.name, 'enabled', e.target.checked)} className="h-3.5 w-3.5" />
                  <span>enabled</span>
                </label>
              )}
              <button type="button" onClick={() => remove(entry.name)} className="text-muted-foreground hover:text-destructive">
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="flex gap-1.5">
        <Select value={adding} onValueChange={v => { setAdding(v); addEntry(v); }}>
          <SelectTrigger className="flex-1 h-8 text-xs"><SelectValue placeholder="Add from known list…" /></SelectTrigger>
          <SelectContent position="popper" className="max-h-56 min-w-max">
            {notAdded.map(k => <SelectItem key={k} value={k} className="text-xs">{k}</SelectItem>)}
          </SelectContent>
        </Select>
        <div className="flex gap-1">
          <Input
            value={adding}
            onChange={e => setAdding(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addEntry(adding); } }}
            placeholder="or type name…"
            className="h-8 text-xs w-40"
          />
          <Button type="button" size="sm" variant="outline" className="h-8 w-8 p-0" onClick={() => addEntry(adding)}>
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Signature sets editor (name + action) ──────────────────────────────────

interface SigSetEntry { name: string; action: 'alarm' | 'block' | 'ignore' }

function SigSetEditor({ values, onChange }: { values: SigSetEntry[]; onChange: (v: SigSetEntry[]) => void }) {
  const [adding, setAdding] = useState('');
  const addEntry = (name: string) => {
    if (!name.trim() || values.find(v => v.name === name)) return;
    onChange([...values, { name, action: 'alarm' }]);
    setAdding('');
  };
  const update = (name: string, action: SigSetEntry['action']) =>
    onChange(values.map(v => v.name === name ? { ...v, action } : v));
  const remove = (name: string) => onChange(values.filter(v => v.name !== name));
  const notAdded = SIGNATURE_SETS.filter(s => !values.find(v => v.name === s));

  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">Select NAP signature sets and their enforcement action for this policy.</p>
      {values.length > 0 && (
        <div className="rounded-md border border-input divide-y divide-border">
          {values.map(entry => (
            <div key={entry.name} className="flex items-center gap-2 px-2.5 py-1.5 text-xs">
              <span className="flex-1 truncate font-medium" title={entry.name}>{entry.name}</span>
              <Select value={entry.action} onValueChange={(v) => update(entry.name, v as SigSetEntry['action'])}>
                <SelectTrigger className="h-6 w-24 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent position="popper" className="min-w-max">
                  <SelectItem value="alarm" className="text-xs">alarm</SelectItem>
                  <SelectItem value="block" className="text-xs">block</SelectItem>
                  <SelectItem value="ignore" className="text-xs">ignore</SelectItem>
                </SelectContent>
              </Select>
              <button type="button" onClick={() => remove(entry.name)} className="text-muted-foreground hover:text-destructive">
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="flex gap-1.5">
        <Select value="" onValueChange={v => addEntry(v)}>
          <SelectTrigger className="flex-1 h-8 text-xs"><SelectValue placeholder="Add signature set…" /></SelectTrigger>
          <SelectContent position="popper" className="max-h-56 min-w-max">
            {notAdded.map(k => <SelectItem key={k} value={k} className="text-xs">{k}</SelectItem>)}
          </SelectContent>
        </Select>
        <Input value={adding} onChange={e => setAdding(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addEntry(adding); } }}
          placeholder="Custom name…" className="h-8 text-xs w-40" />
        <Button type="button" size="sm" variant="outline" className="h-8 w-8 p-0" onClick={() => addEntry(adding)}>
          <Plus className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}

// ── Key-value pair editor for URL/cookie/header/parameter objects ──────────

interface KVEntry { [key: string]: unknown }

function KVListEditor({ values, onChange, schema, hint }: {
  values: KVEntry[]; onChange: (v: KVEntry[]) => void;
  schema: Array<{ key: string; label: string; type: 'text' | 'select' | 'checkbox'; options?: string[]; default?: unknown }>;
  hint?: string;
}) {
  const addRow = () => onChange([...values, Object.fromEntries(schema.map(f => [f.key, f.default ?? '']))]);
  const update = (i: number, key: string, val: unknown) => {
    const next = [...values]; next[i] = { ...next[i], [key]: val }; onChange(next);
  };
  const remove = (i: number) => onChange(values.filter((_, idx) => idx !== i));

  return (
    <div className="space-y-2">
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {values.map((row, i) => (
        <div key={i} className="rounded-md border border-input p-2 space-y-2 relative">
          <button type="button" onClick={() => remove(i)}
            className="absolute top-1.5 right-1.5 text-muted-foreground hover:text-destructive">
            <X className="h-3.5 w-3.5" />
          </button>
          <div className="grid grid-cols-2 gap-2 pr-5">
            {schema.map(field => (
              <div key={field.key} className="space-y-0.5">
                <label className="text-[10px] text-muted-foreground">{field.label}</label>
                {field.type === 'select' ? (
                  <Select value={String(row[field.key] ?? field.default ?? '')} onValueChange={v => update(i, field.key, v)}>
                    <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent position="popper" className="min-w-max">
                      {field.options?.map(o => <SelectItem key={o} value={o} className="text-xs">{o}</SelectItem>)}
                    </SelectContent>
                  </Select>
                ) : field.type === 'checkbox' ? (
                  <label className="flex items-center gap-1.5 cursor-pointer text-xs">
                    <input type="checkbox" checked={!!row[field.key]}
                      onChange={e => update(i, field.key, e.target.checked)} className="h-3.5 w-3.5" />
                    enabled
                  </label>
                ) : (
                  <Input value={String(row[field.key] ?? '')}
                    onChange={e => update(i, field.key, e.target.value)}
                    className="h-7 text-xs" placeholder={field.label} />
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
      <Button type="button" size="sm" variant="outline" className="h-7 text-xs gap-1" onClick={addRow}>
        <Plus className="h-3 w-3" /> Add entry
      </Button>
    </div>
  );
}

// ── IP entry list (for whitelist-ips and disallowed-geolocations) ──────────

function IpListEditor({ values, onChange, type }: {
  values: string[]; onChange: (v: string[]) => void; type: 'ip' | 'country';
}) {
  const [input, setInput] = useState('');
  const COUNTRIES = ['AF','AL','DZ','CN','CU','IR','IQ','KP','LY','NG','PK','RU','SD','SY','UA','VE','YE'];
  const add = (v: string) => {
    const t = v.trim();
    if (!t || values.includes(t)) return;
    onChange([...values, t]); setInput('');
  };
  return (
    <div className="space-y-1.5">
      {values.map(v => (
        <div key={v} className="flex items-center gap-2 px-2.5 py-1 rounded border border-input text-xs">
          <Badge variant="outline" className="text-[10px] font-mono">{v}</Badge>
          <span className="flex-1" />
          <button type="button" onClick={() => onChange(values.filter(x => x !== v))}
            className="text-muted-foreground hover:text-destructive"><Trash2 className="h-3 w-3" /></button>
        </div>
      ))}
      <div className="flex gap-1.5">
        {type === 'country' && (
          <Select value="" onValueChange={v => add(v)}>
            <SelectTrigger className="h-8 w-28 text-xs"><SelectValue placeholder="Country…" /></SelectTrigger>
            <SelectContent position="popper" className="min-w-max">{COUNTRIES.map(c => <SelectItem key={c} value={c} className="text-xs">{c}</SelectItem>)}</SelectContent>
          </Select>
        )}
        <Input value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add(input); } }}
          placeholder={type === 'ip' ? '10.0.0.0/8' : 'Country code (e.g. CN)'} className="h-8 text-xs" />
        <Button type="button" size="sm" variant="outline" className="h-8 w-8 p-0" onClick={() => add(input)}>
          <Plus className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}

// ── Default policy object ──────────────────────────────────────────────────

function defaultPolicy(crName: string): Record<string, unknown> {
  return {
    name: crName,
    'enforcement-mode': 'blocking',
    template: { name: 'POLICY_TEMPLATE_NGINX_BASE' },
    applicationLanguage: 'utf-8',
    caseInsensitive: false,
    'blocking-settings': { violations: [], 'http-protocols': [], evasions: [] },
    'signature-sets': [],
    'signature-settings': { minimumAccuracyForAutoAddedSignatures: 'high' },
    'signature-requirements': [],
    'threat-campaigns': [],
    'server-technologies': [],
    'bot-defense': { settings: { isEnabled: false } },
    'csrf-protection': { enabled: false },
    'data-guard': { enabled: false },
    'sensitive-parameters': [],
    urls: [], parameters: [], cookies: [], headers: [], filetypes: [], methods: [], 'host-names': [],
    'whitelist-ips': [], 'disallowed-geolocations': [],
    'graphql-profiles': [], 'json-profiles': [], 'grpc-profiles': [], 'xml-profiles': [], 'open-api-files': [],
  };
}

// ── Tab 1: Core ────────────────────────────────────────────────────────────

function CoreTab({ p, set, crName, setCrName, crNameError, nameConflict, apiVersion, setApiVersion, isEdit }: {
  p: Record<string, unknown>; set: (patch: Partial<Record<string, unknown>>) => void;
  crName: string; setCrName: (v: string) => void; crNameError: string | null; nameConflict: boolean;
  apiVersion: 'v1' | 'v1beta1'; setApiVersion: (v: 'v1' | 'v1beta1') => void; isEdit: boolean;
}) {
  const templateName = (p.template as { name?: string } | undefined)?.name ?? 'POLICY_TEMPLATE_NGINX_BASE';
  const policyNameDiverged = isEdit && String(p.name ?? '') !== crName && String(p.name ?? '') !== '';
  return (
    <div className="grid grid-cols-2 gap-4">
      {/* API version selector only shown on create — apiVersion is immutable on existing CRs */}
      {!isEdit && (
        <div className="col-span-2 flex items-start gap-3 rounded-md border border-border dark:border-border p-3 bg-muted/50 dark:bg-card/50">
          <Info className="h-3.5 w-3.5 mt-0.5 shrink-0 text-primary" />
          <div className="flex-1 space-y-2">
            <div className="flex items-center gap-3">
              <span className="text-xs font-medium">API Version:</span>
              <div className="flex gap-2">
                {(['v1', 'v1beta1'] as const).map(v => (
                  <button key={v} type="button" onClick={() => setApiVersion(v)}
                    className={cn('px-2.5 py-0.5 rounded text-xs font-mono border transition-colors',
                      apiVersion === v ? 'bg-primary text-white border-primary' : 'border-border dark:border-border text-muted-foreground dark:text-muted-foreground hover:border-primary/40')}>{v}</button>
                ))}
              </div>
              {apiVersion === 'v1' && <Badge variant="outline" className="text-[10px] bg-success/10 text-success border-success/50/20">recommended</Badge>}
              {apiVersion === 'v1beta1' && <Badge variant="outline" className="text-[10px] bg-warning/10 text-warning border-warning/50/20">not default storage</Badge>}
            </div>
            <p className="text-xs text-muted-foreground">
              <strong>v1</strong> is the storage version (recommended). v1beta1 is served but not stored by default.
              Only v1 supports <code className="font-mono">$ref</code> and <code className="font-mono">externalReferenceDetails</code>.
            </p>
          </div>
        </div>
      )}

      {/* CR name — read-only in edit mode (Kubernetes resource names are immutable) */}
      <FieldRow label="CR Name (metadata.name)" required={!isEdit}
        hint={isEdit ? 'Kubernetes resource names are immutable. Delete and recreate to rename.' : 'Lowercase alphanumeric with hyphens, max 63 chars.'}>
        {isEdit ? (
          <div className="flex items-center gap-2">
            <code className="flex-1 px-3 py-2 rounded-md border border-input bg-muted text-sm font-mono opacity-75 select-all">{crName}</code>
            <Badge variant="outline" className="text-[10px] shrink-0">immutable</Badge>
          </div>
        ) : (
          <>
            <Input value={crName} onChange={e => setCrName(e.target.value)} placeholder="my-waf-policy"
              className={crNameError || nameConflict ? 'border-destructive/50' : ''} />
            {crNameError && <p className="text-xs text-destructive flex items-center gap-1 mt-1"><AlertTriangle className="h-3 w-3" />{crNameError}</p>}
            {nameConflict && !crNameError && <p className="text-xs text-destructive mt-1">A policy with this name already exists.</p>}
          </>
        )}
      </FieldRow>

      <FieldRow label="Policy Name (spec.policy.name)" required hint="Internal name compiled into the bundle. Normally matches the CR name.">
        <Input value={String(p.name ?? crName)} onChange={e => set({ name: e.target.value })} placeholder={crName || 'my-waf-policy'} />
        {policyNameDiverged && (
          <p className="text-xs text-warning flex items-center gap-1 mt-1">
            <AlertTriangle className="h-3 w-3" />
            Policy name differs from CR name. The compiler uses this name internally; keeping them in sync is recommended.
          </p>
        )}
      </FieldRow>

      <FieldRow label="Enforcement Mode" hint="blocking: actively block. transparent: detect only. monitoring: log all.">
        <Select value={String(p['enforcement-mode'] ?? 'blocking')} onValueChange={v => set({ 'enforcement-mode': v })}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent position="popper" className="w-80 min-w-max">
            <SelectItem value="blocking">blocking — block violating requests</SelectItem>
            <SelectItem value="transparent">transparent — detect only, never block</SelectItem>
            <SelectItem value="monitoring">monitoring — log all requests (verbose)</SelectItem>
          </SelectContent>
        </Select>
        <p className={cn('text-xs mt-0.5', p['enforcement-mode'] !== 'blocking' ? 'text-warning' : 'text-success')}>
          {p['enforcement-mode'] !== 'blocking' ? '⚠️ Traffic will NOT be blocked — use for testing only.' : '✅ Policy will block violating requests.'}
        </p>
      </FieldRow>

      <FieldRow label="Base Template" hint="NAP policy template this policy inherits defaults from.">
        <Select value={templateName} onValueChange={v => set({ template: { name: v } })}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent position="popper" className="w-96 min-w-max">
            <SelectItem value="POLICY_TEMPLATE_NGINX_BASE">NGINX Base — balanced defaults, good starting point</SelectItem>
            <SelectItem value="POLICY_TEMPLATE_NGINX_STRICT">NGINX Strict — tight rules, may cause false positives</SelectItem>
            <SelectItem value="POLICY_TEMPLATE_NGINX_API_SECURITY">NGINX API Security — optimised for REST APIs</SelectItem>
          </SelectContent>
        </Select>
      </FieldRow>

      <FieldRow label="Application Language" hint="Character encoding of the protected application.">
        <Select value={String(p.applicationLanguage ?? 'utf-8')} onValueChange={v => set({ applicationLanguage: v })}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent position="popper" className="min-w-max">
            <SelectItem value="utf-8">utf-8 (recommended)</SelectItem>
            <SelectItem value="iso-8859-1">iso-8859-1</SelectItem>
            <SelectItem value="windows-1252">windows-1252</SelectItem>
            <SelectItem value="koi8-r">koi8-r</SelectItem>
            <SelectItem value="auto-detect">auto-detect</SelectItem>
          </SelectContent>
        </Select>
      </FieldRow>

      <FieldRow label="Case Insensitive" hint="When true, all string comparisons in the policy are case-insensitive.">
        <Select value={p.caseInsensitive ? 'true' : 'false'} onValueChange={v => set({ caseInsensitive: v === 'true' })}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent position="popper" className="min-w-max">
            <SelectItem value="false">false (default) — case-sensitive</SelectItem>
            <SelectItem value="true">true — case-insensitive</SelectItem>
          </SelectContent>
        </Select>
      </FieldRow>

      <FieldRow label="Enable Passive Mode" hint="When true, policy runs in transparent mode regardless of enforcement-mode.">
        <Select value={(p.enablePassiveMode as boolean) ? 'true' : 'false'} onValueChange={v => set({ enablePassiveMode: v === 'true' })}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent position="popper" className="min-w-max">
            <SelectItem value="false">false (default)</SelectItem>
            <SelectItem value="true">true</SelectItem>
          </SelectContent>
        </Select>
      </FieldRow>

      <FieldRow label="Description" hint="Human-readable description stored in the policy JSON.">
        <Input value={String(p.description ?? '')} onChange={e => set({ description: e.target.value || undefined })}
          placeholder="e.g. Production WAF policy for payment service API" />
      </FieldRow>
    </div>
  );
}

// ── Tab 2: Blocking Settings ───────────────────────────────────────────────

function BlockingTab({ p, set }: { p: Record<string, unknown>; set: (patch: Partial<Record<string, unknown>>) => void }) {
  const bs = (p['blocking-settings'] as { violations?: ToggleEntry[]; 'http-protocols'?: ToggleEntry[]; evasions?: ToggleEntry[] }) ?? {};
  const setBs = (patch: Partial<typeof bs>) => set({ 'blocking-settings': { ...bs, ...patch } });

  return (
    <div className="space-y-5">
      <div className={cn('rounded-md border p-3 text-xs flex gap-2 bg-primary/10 border-primary/20 dark:bg-primary/20/20 dark:border-primary/20')}>
        <Info className="h-3.5 w-3.5 mt-0.5 shrink-0 text-primary" />
        <span className="text-primary dark:text-primary/70">
          Blocking settings override the default alarm/block behaviour for individual violations and evasion techniques.
          Only add entries where you want non-default behaviour.{' '}
          <a href="https://docs.nginx.com/nginx-app-protect-waf/declarative-policy/policy/#blocking-settings" target="_blank" rel="noopener noreferrer" className="underline inline-flex items-center gap-0.5">NAP docs <ExternalLink className="h-3 w-3" /></a>.
        </span>
      </div>

      <SectionTitle>Violation Overrides</SectionTitle>
      <ToggleList values={bs.violations ?? []} onChange={v => setBs({ violations: v })}
        known={NAP_VIOLATIONS} mode="alarm-block"
        hint="Override alarm/block behaviour per violation type. Default: alarm=true, block=true for blocking mode." />

      <SectionTitle>HTTP Protocol Violation Overrides</SectionTitle>
      <ToggleList values={bs['http-protocols'] ?? []} onChange={v => setBs({ 'http-protocols': v })}
        known={HTTP_PROTOCOL_VIOLATIONS} mode="enabled"
        hint="Enable or disable detection of specific HTTP protocol violations." />

      <SectionTitle>Evasion Technique Overrides</SectionTitle>
      <ToggleList values={bs.evasions ?? []} onChange={v => setBs({ evasions: v })}
        known={EVASION_TECHNIQUES} mode="enabled"
        hint="Enable or disable detection of specific evasion techniques (e.g. URL encoding, Unicode escapes)." />
    </div>
  );
}

// ── Tab 3: Signatures ──────────────────────────────────────────────────────

function SignaturesTab({ p, set }: { p: Record<string, unknown>; set: (patch: Partial<Record<string, unknown>>) => void }) {
  const ss = (p['signature-settings'] as Record<string, unknown>) ?? {};
  const threatCampaigns = (p['threat-campaigns'] as Array<{ name: string; isEnabled: boolean }>) ?? [];
  const CAMPAIGNS = ['Campaign_A', 'Mirai', 'WannaCry', 'Log4Shell', 'Spring4Shell', 'ProxyLogon', 'PrintNightmare'];

  return (
    <div className="space-y-5">
      <SectionTitle>Signature Sets</SectionTitle>
      <SigSetEditor
        values={(p['signature-sets'] as SigSetEntry[]) ?? []}
        onChange={v => set({ 'signature-sets': v })}
      />

      <SectionTitle>Signature Settings</SectionTitle>
      <div className="grid grid-cols-2 gap-4">
        <FieldRow label="Minimum Accuracy for Auto-Added Signatures" hint="Only auto-add signatures at or above this accuracy.">
          <Select value={String(ss.minimumAccuracyForAutoAddedSignatures ?? 'high')}
            onValueChange={v => set({ 'signature-settings': { ...ss, minimumAccuracyForAutoAddedSignatures: v } })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent position="popper" className="min-w-max">
              <SelectItem value="low">low</SelectItem>
              <SelectItem value="medium">medium</SelectItem>
              <SelectItem value="high">high (default)</SelectItem>
            </SelectContent>
          </Select>
        </FieldRow>
      </div>

      <SectionTitle>Signature Requirements (User-Defined Signatures)</SectionTitle>
      <FieldRow label="APUserSig Tags" hint="Reference APUserSig CRs by tag name. The tag must match the tag field in the APUserSig CR.">
        <TagInput values={((p['signature-requirements'] as Array<{ tag: string }>) ?? []).map(r => r.tag)}
          onChange={tags => set({ 'signature-requirements': tags.map(tag => ({ tag })) })}
          placeholder="my-custom-tag, sql-bypass…" />
      </FieldRow>

      <SectionTitle>Threat Campaign Protections</SectionTitle>
      <FieldRow label="Active Threat Campaigns" hint="Enable specific threat campaign protections. Requires threat-campaign signatures installed.">
        <TagInput
          values={threatCampaigns.filter(c => c.isEnabled).map(c => c.name)}
          suggestions={CAMPAIGNS}
          onChange={names => set({ 'threat-campaigns': names.map(name => ({ name, isEnabled: true })) })}
          placeholder="Log4Shell, Spring4Shell…"
        />
      </FieldRow>

      <SectionTitle>Server Technologies</SectionTitle>
      <FieldRow label="Technologies behind this policy" hint="Activates technology-specific signature sets (e.g. PHP injection patterns for PHP apps).">
        <TagInput
          values={((p['server-technologies'] as Array<{ serverTechnologyName: string }>) ?? []).map(s => s.serverTechnologyName)}
          suggestions={SERVER_TECHNOLOGIES}
          onChange={names => set({ 'server-technologies': names.map(serverTechnologyName => ({ serverTechnologyName })) })}
          placeholder="PHP, Node.js, PostgreSQL…"
        />
      </FieldRow>
    </div>
  );
}

// ── Tab 4: Security Features ───────────────────────────────────────────────

function SecurityTab({ p, set }: { p: Record<string, unknown>; set: (patch: Partial<Record<string, unknown>>) => void }) {
  const bd = (p['bot-defense'] as { settings?: { isEnabled?: boolean; [k: string]: unknown }; mitigations?: unknown; [k: string]: unknown }) ?? {};
  const bdSettings = (bd.settings as { isEnabled?: boolean; [k: string]: unknown }) ?? {};
  const csrf = (p['csrf-protection'] as { enabled?: boolean; expirationTimeInSeconds?: number; [k: string]: unknown }) ?? {};
  const dg = (p['data-guard'] as { enabled?: boolean; creditCardNumbers?: boolean; usSocialSecurityNumbers?: boolean; maskData?: boolean; [k: string]: unknown }) ?? {};

  return (
    <div className="space-y-5">
      <SectionTitle>Bot Defense</SectionTitle>
      <div className="grid grid-cols-2 gap-4">
        <FieldRow label="Enable Bot Defense" hint="Inspect and classify bot traffic.">
          <Select value={bdSettings.isEnabled ? 'true' : 'false'}
            onValueChange={v => set({ 'bot-defense': { ...bd, settings: { ...bdSettings, isEnabled: v === 'true' } } })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent position="popper" className="min-w-max">
              <SelectItem value="false">false (default) — bot defense disabled</SelectItem>
              <SelectItem value="true">true — inspect and classify bots</SelectItem>
            </SelectContent>
          </Select>
        </FieldRow>
      </div>

      <SectionTitle>CSRF Protection</SectionTitle>
      <div className="grid grid-cols-2 gap-4">
        <FieldRow label="Enable CSRF Protection" hint="Validates CSRF tokens on state-changing requests.">
          <Select value={csrf.enabled ? 'true' : 'false'}
            onValueChange={v => set({ 'csrf-protection': { ...csrf, enabled: v === 'true' } })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent position="popper" className="min-w-max">
              <SelectItem value="false">false (default)</SelectItem>
              <SelectItem value="true">true — enforce CSRF token validation</SelectItem>
            </SelectContent>
          </Select>
        </FieldRow>
        {csrf.enabled && (
          <FieldRow label="Token Expiration (seconds)" hint="How long CSRF tokens remain valid. Default: 600.">
            <Input type="number" value={String(csrf.expirationTimeInSeconds ?? 600)}
              onChange={e => set({ 'csrf-protection': { ...csrf, expirationTimeInSeconds: Number(e.target.value) } })} />
          </FieldRow>
        )}
      </div>

      <SectionTitle>Data Guard (PII Masking)</SectionTitle>
      <div className="grid grid-cols-2 gap-4">
        <FieldRow label="Enable Data Guard" hint="Masks sensitive PII patterns in HTTP responses before they reach the client.">
          <Select value={dg.enabled ? 'true' : 'false'}
            onValueChange={v => set({ 'data-guard': { ...dg, enabled: v === 'true' } })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent position="popper" className="min-w-max">
              <SelectItem value="false">false (default)</SelectItem>
              <SelectItem value="true">true — mask PII in responses</SelectItem>
            </SelectContent>
          </Select>
        </FieldRow>
        {dg.enabled && (
          <>
            <FieldRow label="Mask Credit Card Numbers">
              <Select value={dg.creditCardNumbers ? 'true' : 'false'}
                onValueChange={v => set({ 'data-guard': { ...dg, creditCardNumbers: v === 'true' } })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent position="popper" className="min-w-max"><SelectItem value="false">false</SelectItem><SelectItem value="true">true</SelectItem></SelectContent>
              </Select>
            </FieldRow>
            <FieldRow label="Mask US Social Security Numbers">
              <Select value={dg.usSocialSecurityNumbers ? 'true' : 'false'}
                onValueChange={v => set({ 'data-guard': { ...dg, usSocialSecurityNumbers: v === 'true' } })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent position="popper" className="min-w-max"><SelectItem value="false">false</SelectItem><SelectItem value="true">true</SelectItem></SelectContent>
              </Select>
            </FieldRow>
            <FieldRow label="Mask Data (general masking)">
              <Select value={dg.maskData ? 'true' : 'false'}
                onValueChange={v => set({ 'data-guard': { ...dg, maskData: v === 'true' } })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent position="popper" className="min-w-max"><SelectItem value="false">false</SelectItem><SelectItem value="true">true</SelectItem></SelectContent>
              </Select>
            </FieldRow>
          </>
        )}
      </div>

      <SectionTitle>Sensitive Parameters</SectionTitle>
      <FieldRow label="Parameter names to never log" hint="Values of these parameters will be masked in security logs.">
        <TagInput
          values={((p['sensitive-parameters'] as Array<{ name: string }>) ?? []).map(sp => sp.name)}
          placeholder="password, credit_card, token…"
          onChange={names => set({ 'sensitive-parameters': names.map(name => ({ name })) })}
        />
      </FieldRow>

      <SectionTitle>CSRF URLs</SectionTitle>
      <FieldRow label="URLs requiring CSRF validation" hint="Specific URL paths where CSRF token is enforced. Only used when CSRF Protection is enabled above.">
        <TagInput
          values={((p['csrf-urls'] as Array<{ url?: { path?: string } }>) ?? []).map(c => c.url?.path ?? '')}
          placeholder="/api/login, /api/transfer…"
          onChange={paths => set({ 'csrf-urls': paths.map(path => ({ method: 'POST', url: { protocol: 'https', path } })) })}
        />
      </FieldRow>

      <SectionTitle>Cookie Settings</SectionTitle>
      <div className="grid grid-cols-2 gap-4">
        <FieldRow label="Maximum Cookie Header Length" hint="Maximum allowed length of the Cookie HTTP header. Requests with longer cookies are rejected.">
          <Input
            type="number"
            value={String((p['cookie-settings'] as { maximumCookieHeaderLength?: unknown } | undefined)?.maximumCookieHeaderLength ?? '')}
            onChange={e => set({ 'cookie-settings': { ...((p['cookie-settings'] as Record<string, unknown>) ?? {}), maximumCookieHeaderLength: e.target.value ? Number(e.target.value) : undefined } })}
            placeholder="e.g. 8192 (bytes)"
          />
        </FieldRow>
      </div>

      <SectionTitle>Enforcer Settings (Cookie Security)</SectionTitle>
      <div className="grid grid-cols-2 gap-4">
        <FieldRow label="State Cookie SameSite Attribute">
          <Select
            value={String((p['enforcer-settings'] as { enforcerStateCookies?: { sameSiteAttribute?: string } } | undefined)?.enforcerStateCookies?.sameSiteAttribute ?? 'none')}
            onValueChange={v => set({ 'enforcer-settings': { ...((p['enforcer-settings'] as Record<string, unknown>) ?? {}), enforcerStateCookies: { ...((p['enforcer-settings'] as { enforcerStateCookies?: Record<string, unknown> } | undefined)?.enforcerStateCookies ?? {}), sameSiteAttribute: v } } })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent position="popper" className="min-w-max">
              <SelectItem value="none">none (default)</SelectItem>
              <SelectItem value="strict">strict — same-site only</SelectItem>
              <SelectItem value="lax">lax — top-level navigation allowed</SelectItem>
            </SelectContent>
          </Select>
        </FieldRow>
        <FieldRow label="State Cookie Secure Attribute">
          <Select
            value={String((p['enforcer-settings'] as { enforcerStateCookies?: { secureAttribute?: string } } | undefined)?.enforcerStateCookies?.secureAttribute ?? 'always')}
            onValueChange={v => set({ 'enforcer-settings': { ...((p['enforcer-settings'] as Record<string, unknown>) ?? {}), enforcerStateCookies: { ...((p['enforcer-settings'] as { enforcerStateCookies?: Record<string, unknown> } | undefined)?.enforcerStateCookies ?? {}), secureAttribute: v } } })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent position="popper" className="min-w-max">
              <SelectItem value="always">always (default) — HTTPS only</SelectItem>
              <SelectItem value="never">never — allow HTTP</SelectItem>
            </SelectContent>
          </Select>
        </FieldRow>
      </div>
    </div>
  );
}

// ── Tab 5: Traffic ─────────────────────────────────────────────────────────

function TrafficTab({ p, set }: { p: Record<string, unknown>; set: (patch: Partial<Record<string, unknown>>) => void }) {
  const urlSchema = [
    { key: 'name', label: 'URL path', type: 'text' as const, default: '/api/endpoint' },
    { key: 'method', label: 'HTTP Method', type: 'select' as const, options: ['*', 'GET', 'POST', 'PUT', 'DELETE', 'PATCH'], default: '*' },
    { key: 'protocol', label: 'Protocol', type: 'select' as const, options: ['http', 'https'], default: 'https' },
    { key: 'attackSignaturesCheck', label: 'Signature Check', type: 'checkbox' as const, default: true },
    { key: 'isAllowed', label: 'Allow this URL', type: 'checkbox' as const, default: true },
  ];
  const paramSchema = [
    { key: 'name', label: 'Parameter name', type: 'text' as const },
    { key: 'type', label: 'Type', type: 'select' as const, options: ['explicit', 'wildcard'], default: 'explicit' },
    { key: 'dataType', label: 'Data type', type: 'select' as const, options: ['alpha-numeric', 'integer', 'email', 'boolean', 'phone'], default: 'alpha-numeric' },
    { key: 'allowEmptyValue', label: 'Allow empty', type: 'checkbox' as const, default: false },
  ];
  const methodSchema = [
    { key: 'name', label: 'HTTP method', type: 'select' as const, options: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS', 'CONNECT', 'TRACE'], default: 'GET' },
    { key: 'actAsMethod', label: 'Acts as', type: 'select' as const, options: ['GET', 'POST', ''], default: '' },
  ];
  const filetypeSchema = [
    { key: 'name', label: 'Extension (e.g. php)', type: 'text' as const },
    { key: 'type', label: 'Type', type: 'select' as const, options: ['explicit', 'wildcard'], default: 'explicit' },
    { key: 'allowed', label: 'Allowed', type: 'checkbox' as const, default: false },
  ];

  return (
    <div className="space-y-5">
      <SectionTitle>URL Overrides</SectionTitle>
      <KVListEditor values={(p.urls as KVEntry[]) ?? []} onChange={v => set({ urls: v })} schema={urlSchema}
        hint="Per-URL policy overrides. Leave empty to use policy defaults for all URLs." />

      <SectionTitle>Parameter Rules</SectionTitle>
      <KVListEditor values={(p.parameters as KVEntry[]) ?? []} onChange={v => set({ parameters: v })} schema={paramSchema}
        hint="Explicit parameter definitions for stricter validation." />

      <SectionTitle>Allowed HTTP Methods</SectionTitle>
      <KVListEditor values={(p.methods as KVEntry[]) ?? []} onChange={v => set({ methods: v })} schema={methodSchema}
        hint="Define the exact set of HTTP methods this policy permits. Leave empty to use template defaults." />

      <SectionTitle>File Type Rules</SectionTitle>
      <KVListEditor values={(p.filetypes as KVEntry[]) ?? []} onChange={v => set({ filetypes: v })} schema={filetypeSchema}
        hint="Block or allow specific file extensions." />

      <SectionTitle>Host Names</SectionTitle>
      <FieldRow label="Restrict to host names" hint="Policy applies only to requests with these Host headers. Leave empty for all hosts.">
        <TagInput
          values={((p['host-names'] as Array<{ name: string }>) ?? []).map(h => h.name)}
          onChange={names => set({ 'host-names': names.map(name => ({ name })) })}
          placeholder="api.example.com, *.internal.corp…"
        />
      </FieldRow>

      <SectionTitle>Header Settings</SectionTitle>
      <div className="grid grid-cols-2 gap-4">
        <FieldRow label="Maximum HTTP Header Length (bytes)" hint="Requests with any single header longer than this value are rejected.">
          <Input
            type="number"
            value={String((p['header-settings'] as { maximumHttpHeaderLength?: unknown } | undefined)?.maximumHttpHeaderLength ?? '')}
            onChange={e => set({ 'header-settings': { ...((p['header-settings'] as Record<string, unknown>) ?? {}), maximumHttpHeaderLength: e.target.value ? Number(e.target.value) : undefined } })}
            placeholder="e.g. 8192"
          />
        </FieldRow>
      </div>

      <SectionTitle>Response Pages (Blocked Request)</SectionTitle>
      <FieldRow label="AJAX action when request is blocked" hint="What to show clients when a request is blocked. Default: 'default' page.">
        <Select
          value={String(((p['response-pages'] as Array<{ responsePageType?: string; ajaxActionType?: string }>) ?? [])[0]?.ajaxActionType ?? 'default')}
          onValueChange={v => set({ 'response-pages': [{ responsePageType: 'ajax', ajaxEnabled: true, ajaxActionType: v, ...(v === 'redirect' ? { ajaxRedirectUrl: '/blocked' } : {}) }] })}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent position="popper" className="min-w-max">
            <SelectItem value="default">default — show built-in block page</SelectItem>
            <SelectItem value="redirect">redirect — redirect to a custom URL</SelectItem>
            <SelectItem value="custom">custom — return custom response body</SelectItem>
          </SelectContent>
        </Select>
      </FieldRow>
    </div>
  );
}

// ── Tab 6: Geographic / IP ─────────────────────────────────────────────────

function GeoTab({ p, set }: { p: Record<string, unknown>; set: (patch: Partial<Record<string, unknown>>) => void }) {
  const wlIps = ((p['whitelist-ips'] as Array<{ ipMask: string; trustedByPolicyBuilder?: boolean }>) ?? []);
  const geoBlocks = ((p['disallowed-geolocations'] as Array<{ countryCode: string }>) ?? []);

  return (
    <div className="space-y-5">
      <div className={cn('rounded-md border p-3 text-xs flex gap-2 bg-primary/10 border-primary/20 dark:bg-primary/20/20 dark:border-primary/20')}>
        <Info className="h-3.5 w-3.5 mt-0.5 shrink-0 text-primary" />
        <span className="text-primary dark:text-primary/70">
          IP Intelligence requires a valid IP Intelligence subscription. Geo-blocking requires threat campaign signatures.
        </span>
      </div>

      <SectionTitle>IP Whitelist (Bypass WAF)</SectionTitle>
      <FieldRow label="Trusted IP ranges" hint="Traffic from these CIDRs bypasses WAF inspection entirely.">
        <IpListEditor
          values={wlIps.map(e => e.ipMask)}
          onChange={ips => set({ 'whitelist-ips': ips.map(ipMask => ({ ipMask, trustedByPolicyBuilder: true })) })}
          type="ip"
        />
      </FieldRow>

      <SectionTitle>Country / Geolocation Blocks</SectionTitle>
      <FieldRow label="Block traffic from countries" hint="Block all traffic originating from these countries (ISO 3166-1 alpha-2 codes).">
        <IpListEditor
          values={geoBlocks.map(e => e.countryCode)}
          onChange={codes => set({ 'disallowed-geolocations': codes.map(countryCode => ({ countryCode })) })}
          type="country"
        />
      </FieldRow>
    </div>
  );
}

// ── Tab 7: API Security ────────────────────────────────────────────────────

function ApiSecurityTab({ p, set }: { p: Record<string, unknown>; set: (patch: Partial<Record<string, unknown>>) => void }) {
  const openApiFiles = ((p['open-api-files'] as Array<{ link: string }>) ?? []);

  return (
    <div className="space-y-5">
      <SectionTitle>OpenAPI Specification Files</SectionTitle>
      <FieldRow label="OpenAPI file URLs" hint="Links to OpenAPI spec files. NAP will enforce parameter types, required fields, and allowed endpoints from these specs.">
        <TagInput
          values={openApiFiles.map(f => f.link)}
          onChange={links => set({ 'open-api-files': links.map(link => ({ link })) })}
          placeholder="https://example.com/openapi.yaml…"
        />
      </FieldRow>

      <SectionTitle>JSON Profiles</SectionTitle>
      <div className="grid grid-cols-2 gap-4">
        <FieldRow label="Enable default JSON profile">
          <Select
            value={((p['json-profiles'] as Array<{ name: string }>) ?? []).length > 0 ? 'true' : 'false'}
            onValueChange={v => set({ 'json-profiles': v === 'true' ? [{ name: 'Default', defenseAttributes: { maximumValueLength: 100, maximumArrayLength: 100 } }] : [] })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent position="popper" className="min-w-max">
              <SelectItem value="false">false (default) — no JSON profile</SelectItem>
              <SelectItem value="true">true — apply default JSON content validation</SelectItem>
            </SelectContent>
          </Select>
        </FieldRow>
      </div>

      <SectionTitle>GraphQL Profiles</SectionTitle>
      <div className="grid grid-cols-2 gap-4">
        <FieldRow label="Enable default GraphQL profile">
          <Select
            value={((p['graphql-profiles'] as Array<unknown>) ?? []).length > 0 ? 'true' : 'false'}
            onValueChange={v => set({ 'graphql-profiles': v === 'true' ? [{ name: 'Default', attackSignaturesCheck: true, defenseAttributes: { maximumQueryDepth: 10, maximumQueryCost: 100 } }] : [] })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent position="popper" className="min-w-max">
              <SelectItem value="false">false (default)</SelectItem>
              <SelectItem value="true">true — apply default GraphQL validation</SelectItem>
            </SelectContent>
          </Select>
        </FieldRow>
      </div>

      <SectionTitle>XML Profiles</SectionTitle>
      <div className="grid grid-cols-2 gap-4">
        <FieldRow label="Enable default XML profile">
          <Select
            value={((p['xml-profiles'] as Array<unknown>) ?? []).length > 0 ? 'true' : 'false'}
            onValueChange={v => set({ 'xml-profiles': v === 'true' ? [{ name: 'Default', defenseAttributes: { maximumDocumentDepth: 10 } }] : [] })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent position="popper" className="min-w-max">
              <SelectItem value="false">false (default)</SelectItem>
              <SelectItem value="true">true — apply default XML content validation</SelectItem>
            </SelectContent>
          </Select>
        </FieldRow>
      </div>
    </div>
  );
}

// ── Tab 8: External References ─────────────────────────────────────────────

function ExternalRefsTab({ p, set, apiVersion }: { p: Record<string, unknown>; set: (patch: Partial<Record<string, unknown>>) => void; apiVersion: 'v1' | 'v1beta1' }) {
  const extRef = (p.externalReferenceDetails as Record<string, unknown>) ?? {};
  const refFields: Array<{ key: string; label: string }> = [
    { key: 'blockingSettingReference', label: 'Blocking Settings Reference' },
    { key: 'signatureReference', label: 'Signature Reference' },
    { key: 'signatureSetReference', label: 'Signature Set Reference' },
    { key: 'signatureSettingReference', label: 'Signature Setting Reference' },
    { key: 'serverTechnologyReference', label: 'Server Technology Reference' },
    { key: 'filetypeReference', label: 'Filetype Reference' },
    { key: 'headerReference', label: 'Header Reference' },
    { key: 'cookieReference', label: 'Cookie Reference' },
    { key: 'parameterReference', label: 'Parameter Reference' },
    { key: 'urlReference', label: 'URL Reference' },
    { key: 'jsonProfileReference', label: 'JSON Profile Reference' },
    { key: 'xmlProfileReference', label: 'XML Profile Reference' },
    { key: 'dataGuardReference', label: 'Data Guard Reference' },
    { key: 'sensitiveParameterReference', label: 'Sensitive Parameter Reference' },
    { key: 'responsePageReference', label: 'Response Page Reference' },
  ];

  return (
    <div className="space-y-4">
      <div className={cn('rounded-md border p-3 text-xs flex gap-2 bg-primary/10 border-primary/20 dark:bg-primary/20/20 dark:border-primary/20')}>
        <Info className="h-3.5 w-3.5 mt-0.5 shrink-0 text-primary" />
        <span className="text-primary dark:text-primary/70">
          External references load policy sub-sections from remote URLs, overriding any inline definitions.
          Use these for shared policy fragments across multiple policies.
        </span>
      </div>

      <div className="grid grid-cols-1 gap-3">
        {/* $ref and externalReferenceDetails are v1-only */}
        {apiVersion === 'v1' ? (
          <FieldRow label="$ref — Load full policy from URL" hint="URL to an external NAP policy JSON file. Overrides all inline fields when specified. (v1 only)">
            <Input value={String(p['$ref'] ?? '')} onChange={e => set({ '$ref': e.target.value || undefined })}
              placeholder="https://example.com/policies/base-policy.json" />
          </FieldRow>
        ) : (
          <div className="rounded-md border border-warning/20 dark:border-warning/20 bg-warning/10 dark:bg-warning/20/20 p-3 text-xs text-warning dark:text-warning/60">
            <strong>$ref</strong> and <strong>externalReferenceDetails</strong> are only available in <span className="font-mono">appprotect.f5.com/v1</span>.
            Switch to v1 in the Core tab to use these fields.
          </div>
        )}

        {apiVersion === 'v1' && (
          <div className="grid grid-cols-2 gap-3">
          <FieldRow label="External Reference Type">
            <Select value={String(extRef.type ?? '__none__')} onValueChange={v => set({ externalReferenceDetails: { ...extRef, type: v === '__none__' ? undefined : v } })}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent position="popper" className="min-w-max">
                <SelectItem value="__none__">— none —</SelectItem>
                <SelectItem value="url">url — load from HTTP URL</SelectItem>
                <SelectItem value="git">git — load from Git repository</SelectItem>
              </SelectContent>
            </Select>
          </FieldRow>

          {extRef.type === 'git' && (
            <>
              <FieldRow label="Git Repo URL">
                <Input value={String(extRef.url ?? '')} onChange={e => set({ externalReferenceDetails: { ...extRef, url: e.target.value } })} placeholder="https://github.com/org/repo.git" />
              </FieldRow>
              <FieldRow label="Branch / Ref">
                <Input value={String(extRef.ref ?? 'main')} onChange={e => set({ externalReferenceDetails: { ...extRef, ref: e.target.value } })} placeholder="main" />
              </FieldRow>
              <FieldRow label="Path in Repo">
                <Input value={String(extRef.path ?? '')} onChange={e => set({ externalReferenceDetails: { ...extRef, path: e.target.value } })} placeholder="policies/base.json" />
              </FieldRow>
            </>
          )}
        </div>
        )}{/* end apiVersion === 'v1' */}

        <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Sub-Section References</p>
        <div className="grid grid-cols-2 gap-3">
          {refFields.map(({ key, label }) => (
            <FieldRow key={key} label={label} hint="URL to external definition file">
              <Input value={String((p[key] as { link?: string } | undefined)?.link ?? '')}
                onChange={e => set({ [key]: e.target.value ? { link: e.target.value } : undefined })}
                placeholder="https://…" className="text-xs" />
            </FieldRow>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Main APPolicyForm ──────────────────────────────────────────────────────

interface APPolicyFormProps {
  clusterId: number;
  namespace: string;
  existingItem?: APPolicyResource | null;
  onClose: () => void;
  onAfterSave?: () => void;
  // Pre-seed values from the Create wizard step 1
  initialName?: string;
  initialEnforcementMode?: 'blocking' | 'transparent';
  initialTemplate?: string;
}

export function APPolicyForm({ clusterId, namespace, existingItem, onClose, initialName, initialEnforcementMode, initialTemplate }: APPolicyFormProps) {
  const isEdit = !!existingItem;
  const initialPolicy = existingItem?.spec?.policy as Record<string, unknown> | undefined;

  // All policies in namespace — used as clone sources in create mode
  const { data: allPoliciesData } = useWafPolicies(clusterId, namespace, { enabled: !isEdit, autoRefresh: false });

  const [apiVersion, setApiVersion] = useState<'v1' | 'v1beta1'>('v1');
  const [crName, setCrName] = useState(existingItem?.metadata.name ?? initialName ?? '');
  const [activeTab, setActiveTab] = useState('core');
  const _basePolicy = initialPolicy ? { ...initialPolicy } : defaultPolicy(crName);
  if (!isEdit && initialEnforcementMode) _basePolicy['enforcement-mode'] = initialEnforcementMode;
  if (!isEdit && initialTemplate !== undefined) _basePolicy['template'] = { name: initialTemplate };
  const [policy, setPolicy] = useState<Record<string, unknown>>(_basePolicy);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [jsonText, setJsonText] = useState(() => JSON.stringify(_basePolicy, null, 2));





  const [jsonError, setJsonError] = useState<string | null>(null);

  // Sync policy state → JSON textarea whenever fields change (unless JSON tab active)
  useEffect(() => {
    if (activeTab !== 'json') setJsonText(JSON.stringify(policy, null, 2));
  }, [policy, activeTab]);

  // When user edits JSON textarea, try to apply to policy state
  const handleJsonEdit = (text: string) => {
    setJsonText(text);
    try {
      const parsed = JSON.parse(text) as Record<string, unknown>;
      setJsonError(null);
      setPolicy(parsed);
    } catch (e) {
      setJsonError(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const crNameError = crName ? validateK8sName(crName) : (isEdit ? null : 'Name is required.');
  const { data: existingPolicies } = useWafPolicies(clusterId, namespace, { enabled: !isEdit && !!crName, autoRefresh: false });
  const nameConflict = !isEdit && (existingPolicies?.policies ?? []).some(p => p.metadata.name === crName);

  const setCrNameAndSync = (v: string) => {
    setCrName(v);
    if (!isEdit && policy.name === crName) setPolicy(prev => ({ ...prev, name: v }));
  };

  const set = (patch: Partial<Record<string, unknown>>) => setPolicy(prev => ({ ...prev, ...patch }));

  const createMutation = useCreateWafPolicy(clusterId);
  const updateMutation = useUpdateWafPolicy(clusterId, existingItem?.metadata.name ?? crName);
  const queryClient = useQueryClient();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [compileState, setCompileState] = useState<string | null>(null);
  const [clusterErrors, setClusterErrors] = useState<string[] | null>(null);
  const isPending = isSubmitting || (!!compileState && compileState !== 'ready' && compileState !== 'invalid');

  const coreErrors = [
    ...(!isEdit && crNameError ? [crNameError] : []),
    ...(!isEdit && nameConflict ? ['Name already exists.'] : []),
    ...(!policy.name ? ['Policy name (spec.policy.name) is required.'] : []),
  ];
  const tabs = [
    { key: 'core',       label: 'Core',          validate: () => coreErrors },
    { key: 'blocking',   label: 'Blocking',       validate: () => [] },
    { key: 'signatures', label: 'Signatures',     validate: () => [] },
    { key: 'security',   label: 'Security',       validate: () => [] },
    { key: 'traffic',    label: 'Traffic',        validate: () => [] },
    { key: 'geo',        label: 'Geographic',     validate: () => [] },
    { key: 'api',        label: 'API Security',   validate: () => [] },
    { key: 'refs',       label: 'External Refs',  validate: () => [] },
    { key: 'json',       label: '{ } JSON',       validate: () => jsonError ? [jsonError] : [] },
  ];

  const jsonTab = (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        Live view of the policy JSON being built. Edits here reflect immediately in all form tabs.
        Use this to paste an existing policy or make advanced changes not covered by the form.
      </p>
      <Textarea
        value={jsonText}
        onChange={e => handleJsonEdit(e.target.value)}
        rows={24}
        className="font-mono text-xs"
        spellCheck={false}
      />
      {jsonError && (
        <p className="text-xs text-destructive flex items-center gap-1">
          <AlertTriangle className="h-3 w-3" />{jsonError}
        </p>
      )}
    </div>
  );

  const tabContent: Record<string, React.ReactNode> = {
    core:       <CoreTab p={policy} set={set} crName={crName} setCrName={setCrNameAndSync} crNameError={crNameError} nameConflict={nameConflict} apiVersion={apiVersion} setApiVersion={setApiVersion} isEdit={isEdit} />,
    blocking:   <BlockingTab p={policy} set={set} />,
    signatures: <SignaturesTab p={policy} set={set} />,
    security:   <SecurityTab p={policy} set={set} />,
    traffic:    <TrafficTab p={policy} set={set} />,
    geo:        <GeoTab p={policy} set={set} />,
    api:        <ApiSecurityTab p={policy} set={set} />,
    refs:       <ExternalRefsTab p={policy} set={set} apiVersion={apiVersion} />,
    json:       jsonTab,
  };

  const handleSubmit = async () => {
    setSubmitError(null);
    setClusterErrors(null);
    setCompileState(null);
    setIsSubmitting(true);
    const spec = { policy };
    try {
      if (isEdit) {
        await updateMutation.mutateAsync({ namespace: existingItem?.metadata.namespace ?? namespace, spec });
        setIsSubmitting(false);
        // Poll bundle state after update to surface compiler errors
        setCompileState('pending');
        const policyName = existingItem?.metadata.name ?? crName;
        const ns = existingItem?.metadata.namespace ?? namespace;
        const poll = setInterval(async () => {
          try {
            await queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.wafPolicies(clusterId) });
            const fresh = queryClient.getQueryData<{ policies: APPolicyResource[] }>(['k8s', 'clusters', clusterId, 'waf', 'policies', ns]);
            const p = fresh?.policies?.find((x: APPolicyResource) => x.metadata.name === policyName);
            const state = p?.status?.bundle?.state;
            if (state) setCompileState(state);
            if (state === 'ready') { clearInterval(poll); setTimeout(onClose, 800); }
            if (state === 'invalid') {
              clearInterval(poll);
              // Surface processing errors from the cluster
              const errs = (p?.status as { processing?: { errors?: string[] } } | undefined)?.processing?.errors;
              setClusterErrors(errs && errs.length > 0 ? errs : ['Compilation failed — check the policy spec.']);
            }
          } catch { /* ignore polling errors */ }
        }, 2500);
        setTimeout(() => { clearInterval(poll); if (compileState !== 'ready' && compileState !== 'invalid') setCompileState('ready'); }, 90_000);
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

  // Auto-save draft on every field change (create mode only)
  useEffect(() => {
    if (isEdit || !crName) return;
    const t = setTimeout(() => {
      saveDraft('APPolicy', { name: crName, namespace, data: { crName, policy, apiVersion } });
    }, 800);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [crName, policy, apiVersion]);

  const handleClone = (src: { spec: Record<string, unknown> }) => {
    const p = (src.spec.policy ?? src.spec) as Record<string, unknown>;
    setPolicy({ ...defaultPolicy(crName), ...p, name: crName || (p.name as string) || crName });
    setJsonText(JSON.stringify({ ...defaultPolicy(crName), ...p, name: crName || (p.name as string) || crName }, null, 2));
  };

  const handleRestoreDraft = (draft: WafDraft) => {
    const d = draft.data;
    if (typeof d.crName === 'string') setCrName(d.crName);
    if (d.policy && typeof d.policy === 'object') {
      setPolicy(d.policy as Record<string, unknown>);
      setJsonText(JSON.stringify(d.policy, null, 2));
    }
    if (d.apiVersion === 'v1' || d.apiVersion === 'v1beta1') setApiVersion(d.apiVersion);
  };

  const handleImport = (raw: Record<string, unknown>) => {
    handleClone({ spec: raw });
    const metaName = typeof (raw.metadata as Record<string, unknown>)?.name === 'string'
      ? `copy-of-${(raw.metadata as Record<string, unknown>).name as string}` : '';
    if (metaName && !isEdit) { setCrName(metaName); }
  };

  const cloneSources = (allPoliciesData?.policies ?? []).map((p: APPolicyResource) => ({
    name: p.metadata.name, namespace: p.metadata.namespace ?? namespace,
    spec: { policy: p.spec?.policy as Record<string, unknown> ?? {} } as Record<string, unknown>,
  }));

  const toolbar = !isEdit ? (
    <WafFormToolbar
      kind="APPolicy"
      currentState={{ crName, policy, apiVersion }}
      currentLabel={crName}
      onClone={handleClone}
      onRestoreDraft={handleRestoreDraft}
      onImport={handleImport}
      cloneSources={cloneSources}
    />
  ) : undefined;

  return (
    <WafWizardFrame
      tabs={tabs.map(t => ({ ...t, content: tabContent[t.key] }))}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      allErrors={tabs.flatMap(t => t.validate())}
      toolbar={toolbar}
      isPending={isPending}
      submitLabel={
        compileState && compileState !== 'ready' && compileState !== 'invalid'
          ? `Compiling… (${compileState})`
          : isEdit ? 'Save & Recompile' : 'Create Policy'
      }
      onSubmit={handleSubmit}
      onCancel={onClose}
      submitError={
        clusterErrors
          ? `Cluster compiler error: ${clusterErrors.join(' | ')}`
          : submitError
      }
      statusNote={
        compileState && compileState !== 'ready' && compileState !== 'invalid' ? (
          <span className="text-xs text-primary dark:text-primary/80 flex items-center gap-1">
            <RefreshCw className="h-3 w-3 animate-spin" />
            Compiling… bundle state: <span className="font-mono">{compileState}</span>. Sheet will close automatically when ready.
          </span>
        ) : compileState === 'ready' ? (
          <span className="text-xs text-success">✅ Compiled successfully — closing…</span>
        ) : compileState === 'invalid' ? (
          <span className="text-xs text-destructive">❌ Compilation failed — see error above</span>
        ) : undefined
      }
    />
  );
}
