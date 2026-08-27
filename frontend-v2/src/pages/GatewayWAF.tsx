import { useState } from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { EmptyState } from '@/components/ui/empty-state';
import { Label } from '@/components/ui/label';
import { Globe, ArrowRight, Network, Route, Lock, Shield, Info, AlertTriangle, Plus, Trash2, Pencil } from 'lucide-react';
import { useAllClusters } from '@/hooks/useK8sClusters';
import { useClusterNamespaces } from '@/hooks/useK8sResources';
import {
  useGateways, useCreateGateway, useUpdateGateway, useDeleteGateway,
  useSecurityProfiles, useCreateSecurityProfile, useUpdateSecurityProfile, useDeleteSecurityProfile,
  useHTTPRoutes, useCreateHTTPRoute, useUpdateHTTPRoute, useDeleteHTTPRoute,
  useReferenceGrants, useCreateReferenceGrant, useDeleteReferenceGrant,
  useGatewayClasses, useCreateGatewayClass, useDeleteGatewayClass,
} from '@/hooks/useWafGateway';
import { useWafPolicies } from '@/hooks/useWafPolicies';
import type { Gateway, HTTPRoute, SecurityProfile } from '@/lib/api/waf-gateway';

import { useMemo } from 'react';

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

export default function GatewayWAF() {
  const { data: clustersData } = useAllClusters();
  const clusters = clustersData?.clusters ?? [];
  const [selectedCluster, setSelectedCluster] = useState<number | null>(null);
  const [namespace, setNamespace] = useState('default');

  const clusterId = selectedCluster ?? clusters[0]?.id ?? null;

  return (
    <div className="p-6 overflow-y-auto">
      <div className="max-w-6xl mx-auto space-y-5">
        <div>
          <p className="text-xs text-muted-foreground mb-1">Gateway API</p>
          <h2 className="text-xl font-semibold flex items-center gap-2">
            <Globe className="h-5 w-5" /> Gateway
          </h2>
          <p className="text-sm text-muted-foreground mt-0.5">
            Configure Gateway API resources: GatewayClass, Gateway, HTTPRoute, WAF Profiles, and Reference Grants.
          </p>
        </div>

        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <span className="text-xs shrink-0 text-muted-foreground">Cluster:</span>
            <Select value={clusterId ? String(clusterId) : undefined} onValueChange={(v) => setSelectedCluster(Number(v))}>
              <SelectTrigger className="w-56 h-9"><SelectValue placeholder="Select a cluster" /></SelectTrigger>
              <SelectContent>
                {clusters.map((c) => <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs shrink-0 text-muted-foreground">Namespace:</span>
            {clusterId
              ? <NamespacePicker clusterId={clusterId} value={namespace} onChange={setNamespace} />
              : <Input value={namespace} onChange={(e) => setNamespace(e.target.value)} className="w-36 h-9 text-sm" placeholder="default" />
            }
          </div>
        </div>

        {clusterId
          ? <GatewayWafTab clusterId={clusterId} namespace={namespace} />
          : <EmptyState icon={Globe} title="No cluster selected" description="Select a cluster above to manage Gateway API resources." />
        }
      </div>
    </div>
  );
}
