/**
 * ConfigBuilder — Multi-step wizard for creating complete BNK Gateway configurations.
 *
 * Steps:
 * 1. Gateway — class, name, listeners (reuses ListenerBuilder)
 * 2. Routes — create routes with backends, attach to listeners
 * 3. Security — optional firewall rules (reuses PolicyBuilder pieces)
 * 4. Review — YAML preview, dry-run, apply all
 *
 * Part of TOPO-003b.
 */

import { useState, useMemo, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Globe, Shield, Network, Route, Check, ChevronRight, Plus, Trash2,
  Play, Copy, AlertTriangle, ArrowLeft, ArrowRight, Code, Wand2,
  ArrowUp, ArrowDown,
} from 'lucide-react';
import { useBnkData } from '@/hooks/k8s/useBnk';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { notify } from '@/lib/notify';
import { parseApiError } from '@/lib/error-handler';
import { SkeletonTable } from '@/components/ui/skeleton-table';
import { EmptyState } from '@/components/ui/empty-state';
import { ListenerBuilder } from '@/components/bnk/ListenerBuilder';
import type { Listener } from '@/components/bnk/ListenerBuilder';
import {
  generateConfigYaml,
  splitYamlDocuments,
  extractResourceType,
  RULE_TEMPLATES,
  POLICY_TEMPLATES,
} from '@/lib/policy-yaml';
import type {
  ConfigBuilderState,
  ConfigBuilderStep,
  ConfigBuilderRoute,
  PolicyBuilderRule,
  PaletteGatewayClass,
  PaletteService,
  PaletteAddressList,
  PalettePortList,
  PaletteIRule,
} from '@/types/f5bnk';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let _counter = 0;
function uid(): string { return `item-${Date.now()}-${++_counter}`; }

function createInitialState(namespace: string): ConfigBuilderState {
  return {
    gatewayName: '',
    gatewayClassName: '',
    namespace: namespace || 'default',
    listeners: [{ name: 'http', protocol: 'HTTP', port: 80 }],
    routes: [],
    securityEnabled: false,
    securityPolicyName: '',
    firewallPolicyName: '',
    firewallRules: [],
    targetListener: '',
    networkEnabled: false,
    networkPolicyName: '',
    networkExtensionRefs: [],
    networkTargetListener: '',
  };
}

function createRoute(kind: ConfigBuilderRoute['kind'] = 'HTTPRoute'): ConfigBuilderRoute {
  return {
    id: uid(),
    kind,
    name: '',
    listenerName: '',
    hostnames: [],
    matches: [{ pathType: 'PathPrefix', pathValue: '/' }],
    backends: [{ serviceName: '', servicePort: 80 }],
  };
}

const STEPS: { key: ConfigBuilderStep; label: string; icon: typeof Globe }[] = [
  { key: 'gateway', label: 'Gateway', icon: Globe },
  { key: 'routes', label: 'Routes', icon: Route },
  { key: 'security', label: 'Security', icon: Shield },
  { key: 'review', label: 'Review & Apply', icon: Check },
];

// Map route kinds to semantic tokens for the kind badge accent.
const ROUTE_KIND_COLORS: Record<string, string> = {
  HTTPRoute: 'text-info',
  TCPRoute: 'text-success',
  UDPRoute: 'text-primary',
  TLSRoute: 'text-warning',
  GRPCRoute: 'text-warning',
};

// ---------------------------------------------------------------------------
// Step Indicator
// ---------------------------------------------------------------------------

function StepIndicator({
  steps,
  currentStep,
  onStepClick,
}: {
  steps: typeof STEPS;
  currentStep: ConfigBuilderStep;
  onStepClick: (step: ConfigBuilderStep) => void;
}) {
  const currentIdx = steps.findIndex(s => s.key === currentStep);

  return (
    <div className="flex items-center gap-1">
      {steps.map((step, i) => {
        const isDone = i < currentIdx;
        const isActive = i === currentIdx;
        const Icon = step.icon;
        return (
          <div key={step.key} className="flex items-center">
            <button
              onClick={() => onStepClick(step.key)}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors',
                isActive
                  ? 'bg-primary text-primary-foreground'
                  : isDone
                    ? 'bg-success/10 text-success hover:bg-success/20'
                    : 'bg-muted text-muted-foreground hover:bg-muted/70',
              )}
            >
              {isDone ? <Check className="h-3 w-3" /> : <Icon className="h-3 w-3" />}
              {step.label}
            </button>
            {i < steps.length - 1 && (
              <ChevronRight className="h-3.5 w-3.5 mx-0.5 text-muted-foreground" />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 1: Gateway
// ---------------------------------------------------------------------------

function GatewayStep({
  state,
  onChange,
  gatewayClasses,
}: {
  state: ConfigBuilderState;
  onChange: (partial: Partial<ConfigBuilderState>) => void;
  gatewayClasses: PaletteGatewayClass[];
}) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3">
        <div>
          <label className="text-xs font-medium opacity-70">Gateway Name</label>
          <Input
            value={state.gatewayName}
            onChange={e => onChange({ gatewayName: e.target.value })}
            placeholder="my-gateway"
            className="h-8 text-sm"
          />
        </div>
        <div>
          <label className="text-xs font-medium opacity-70">Gateway Class</label>
          {gatewayClasses.length > 0 ? (
            <Select value={state.gatewayClassName} onValueChange={v => onChange({ gatewayClassName: v })}>
              <SelectTrigger className="h-8 text-sm"><SelectValue placeholder="Select class..." /></SelectTrigger>
              <SelectContent>
                {gatewayClasses.map(gc => (
                  <SelectItem key={gc.name} value={gc.name}>
                    {gc.name}
                    <span className="text-[10px] ml-2 opacity-50">{gc.controllerName}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Input
              value={state.gatewayClassName}
              onChange={e => onChange({ gatewayClassName: e.target.value })}
              placeholder="f5-gateway-class"
              className="h-8 text-sm"
            />
          )}
        </div>
        <div>
          <label className="text-xs font-medium opacity-70">Namespace</label>
          <Input
            value={state.namespace}
            onChange={e => onChange({ namespace: e.target.value })}
            placeholder="default"
            className="h-8 text-sm"
          />
        </div>
      </div>

      <div className="rounded-lg border border-border p-4">
        <h3 className="text-sm font-semibold mb-3">Listeners</h3>
        <ListenerBuilder
          value={state.listeners as Listener[]}
          onChange={listeners => onChange({ listeners })}
          showPresets
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 2: Routes
// ---------------------------------------------------------------------------

function RoutesStep({
  state,
  onChange,
  services,
}: {
  state: ConfigBuilderState;
  onChange: (partial: Partial<ConfigBuilderState>) => void;
  services: PaletteService[];
}) {
  const addRoute = (kind: ConfigBuilderRoute['kind'] = 'HTTPRoute') => {
    onChange({ routes: [...state.routes, createRoute(kind)] });
  };

  const updateRoute = (index: number, updated: ConfigBuilderRoute) => {
    onChange({ routes: state.routes.map((r, i) => i === index ? updated : r) });
  };

  const removeRoute = (index: number) => {
    onChange({ routes: state.routes.filter((_, i) => i !== index) });
  };

  return (
    <div className="space-y-3">
      {state.routes.length === 0 && (
        <div className="text-center py-10">
          <Route className="h-10 w-10 mx-auto mb-3 text-muted-foreground" />
          <p className="text-sm mb-1 text-muted-foreground">No routes yet</p>
          <p className="text-xs mb-4 text-muted-foreground">
            Add routes to direct traffic from listeners to backend services
          </p>
        </div>
      )}

      {state.routes.map((route, i) => (
        <RouteCard
          key={route.id}
          route={route}
          index={i}
          listeners={state.listeners}
          services={services}
          onChange={updated => updateRoute(i, updated)}
          onRemove={() => removeRoute(i)}
        />
      ))}

      <div className="flex gap-2">
        <Button variant="outline" size="sm" className="h-7 text-xs" onClick={() => addRoute('HTTPRoute')}>
          <Plus className="h-3 w-3 mr-1" /> HTTP Route
        </Button>
        <Button variant="outline" size="sm" className="h-7 text-xs" onClick={() => addRoute('TCPRoute')}>
          <Plus className="h-3 w-3 mr-1" /> TCP Route
        </Button>
        <Button variant="outline" size="sm" className="h-7 text-xs" onClick={() => addRoute('GRPCRoute')}>
          <Plus className="h-3 w-3 mr-1" /> gRPC Route
        </Button>
      </div>
    </div>
  );
}

function RouteCard({
  route,
  index,
  listeners,
  services,
  onChange,
  onRemove,
}: {
  route: ConfigBuilderRoute;
  index: number;
  listeners: ConfigBuilderState['listeners'];
  services: PaletteService[];
  onChange: (updated: ConfigBuilderRoute) => void;
  onRemove: () => void;
}) {
  const [hostnameInput, setHostnameInput] = useState('');

  const addHostname = () => {
    const trimmed = hostnameInput.trim();
    if (trimmed && !route.hostnames.includes(trimmed)) {
      onChange({ ...route, hostnames: [...route.hostnames, trimmed] });
      setHostnameInput('');
    }
  };

  const addBackend = () => {
    onChange({ ...route, backends: [...route.backends, { serviceName: '', servicePort: 80 }] });
  };

  const updateBackend = (bi: number, field: string, value: string | number) => {
    onChange({
      ...route,
      backends: route.backends.map((b, i) => i === bi ? { ...b, [field]: value } : b),
    });
  };

  const removeBackend = (bi: number) => {
    onChange({ ...route, backends: route.backends.filter((_, i) => i !== bi) });
  };

  return (
    <div className="border border-border rounded-lg bg-card">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
        <Badge variant="outline" className={cn('text-[10px]', ROUTE_KIND_COLORS[route.kind])}>{route.kind}</Badge>
        <span className="text-xs font-mono opacity-40">#{index + 1}</span>
        <div className="flex-1" />
        <button onClick={onRemove} className="p-0.5 rounded hover:bg-destructive/10 text-destructive">
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="p-3 space-y-3">
        {/* Row 1: Name + Listener */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[10px] font-medium opacity-60">Route Name</label>
            <Input
              value={route.name}
              onChange={e => onChange({ ...route, name: e.target.value })}
              placeholder={`my-${route.kind.toLowerCase()}`}
              className="h-7 text-xs"
            />
          </div>
          <div>
            <label className="text-[10px] font-medium opacity-60">Attach to Listener</label>
            <Select value={route.listenerName} onValueChange={v => onChange({ ...route, listenerName: v })}>
              <SelectTrigger className="h-7 text-xs"><SelectValue placeholder="Select listener..." /></SelectTrigger>
              <SelectContent>
                {listeners.map(l => (
                  <SelectItem key={l.name} value={l.name}>{l.name} ({l.protocol}/{l.port})</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* HTTP-specific: hostnames + matches */}
        {route.kind === 'HTTPRoute' && (
          <>
            {/* Hostnames */}
            <div>
              <label className="text-[10px] font-medium opacity-60">Hostnames (optional)</label>
              <div className="flex gap-1">
                <Input
                  value={hostnameInput}
                  onChange={e => setHostnameInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addHostname())}
                  placeholder="example.com"
                  className="h-6 text-[11px] flex-1"
                />
                <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={addHostname}>
                  <Plus className="h-3 w-3" />
                </Button>
              </div>
              {route.hostnames.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1">
                  {route.hostnames.map(h => (
                    <Badge key={h} variant="secondary" className="text-[10px] pr-1 gap-0.5">
                      {h}
                      <button onClick={() => onChange({ ...route, hostnames: route.hostnames.filter(x => x !== h) })} className="ml-0.5 hover:text-destructive">×</button>
                    </Badge>
                  ))}
                </div>
              )}
            </div>

            {/* Match rules */}
            <div>
              <label className="text-[10px] font-medium opacity-60">Match Rules</label>
              {route.matches.map((m, mi) => (
                <div key={mi} className="flex items-center gap-2 mt-1">
                  <Select value={m.pathType} onValueChange={v => {
                    const matches = [...route.matches];
                    matches[mi] = { ...m, pathType: v as 'PathPrefix' | 'Exact' };
                    onChange({ ...route, matches });
                  }}>
                    <SelectTrigger className="h-6 text-[11px] w-28"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="PathPrefix">PathPrefix</SelectItem>
                      <SelectItem value="Exact">Exact</SelectItem>
                    </SelectContent>
                  </Select>
                  <Input
                    value={m.pathValue}
                    onChange={e => {
                      const matches = [...route.matches];
                      matches[mi] = { ...m, pathValue: e.target.value };
                      onChange({ ...route, matches });
                    }}
                    placeholder="/"
                    className="h-6 text-[11px] flex-1"
                  />
                  {route.matches.length > 1 && (
                    <button
                      onClick={() => onChange({ ...route, matches: route.matches.filter((_, i) => i !== mi) })}
                      className="p-0.5 text-destructive hover:bg-destructive/10 rounded"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  )}
                </div>
              ))}
              <Button
                variant="ghost"
                size="sm"
                className="h-5 text-[10px] mt-1 px-1"
                onClick={() => onChange({ ...route, matches: [...route.matches, { pathType: 'PathPrefix', pathValue: '/' }] })}
              >
                <Plus className="h-2.5 w-2.5 mr-0.5" /> Match
              </Button>
            </div>
          </>
        )}

        {/* Backends */}
        <div>
          <label className="text-[10px] font-medium opacity-60">Backend Services</label>
          {route.backends.map((b, bi) => (
            <div key={bi} className="flex items-center gap-2 mt-1">
              {services.length > 0 ? (
                <Select value={b.serviceName} onValueChange={v => updateBackend(bi, 'serviceName', v)}>
                  <SelectTrigger className="h-6 text-[11px] flex-1"><SelectValue placeholder="Select service..." /></SelectTrigger>
                  <SelectContent>
                    {services.map(s => (
                      <SelectItem key={`${s.namespace}/${s.name}`} value={s.name}>
                        {s.name}
                        <span className="text-[10px] ml-1 opacity-50">{s.namespace}</span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <Input
                  value={b.serviceName}
                  onChange={e => updateBackend(bi, 'serviceName', e.target.value)}
                  placeholder="service-name"
                  className="h-6 text-[11px] flex-1"
                />
              )}
              <Input
                type="number"
                value={b.servicePort}
                onChange={e => updateBackend(bi, 'servicePort', parseInt(e.target.value) || 80)}
                placeholder="80"
                className="h-6 text-[11px] w-16"
              />
              {route.backends.length > 1 && (
                <button onClick={() => removeBackend(bi)} className="p-0.5 text-destructive hover:bg-destructive/10 rounded">
                  <Trash2 className="h-3 w-3" />
                </button>
              )}
            </div>
          ))}
          <Button variant="ghost" size="sm" className="h-5 text-[10px] mt-1 px-1" onClick={addBackend}>
            <Plus className="h-2.5 w-2.5 mr-0.5" /> Backend
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 3: Security
// ---------------------------------------------------------------------------

function SecurityStep({
  state,
  onChange,
  addressLists: _addressLists,
  portLists: _portLists,
  iRules,
}: {
  state: ConfigBuilderState;
  onChange: (partial: Partial<ConfigBuilderState>) => void;
  addressLists: PaletteAddressList[];
  portLists: PalettePortList[];
  iRules: PaletteIRule[];
}) {
  // _addressLists and _portLists reserved for future rule source/dest pickers
  void _addressLists; void _portLists;

  const addRule = (template: Omit<PolicyBuilderRule, 'id'>) => {
    onChange({ firewallRules: [...state.firewallRules, { ...template, id: uid() }] });
  };

  const applyTemplate = (rules: Array<Omit<PolicyBuilderRule, 'id'>>) => {
    onChange({ firewallRules: rules.map(r => ({ ...r, id: uid() })) });
  };

  const updateRule = (index: number, updated: PolicyBuilderRule) => {
    onChange({ firewallRules: state.firewallRules.map((r, i) => i === index ? updated : r) });
  };

  const removeRule = (index: number) => {
    onChange({ firewallRules: state.firewallRules.filter((_, i) => i !== index) });
  };

  const moveRule = (index: number, dir: -1 | 1) => {
    const rules = [...state.firewallRules];
    const target = index + dir;
    if (target < 0 || target >= rules.length) return;
    [rules[index], rules[target]] = [rules[target], rules[index]];
    onChange({ firewallRules: rules });
  };

  return (
    <div className="space-y-4">
      {/* Security policy toggle */}
      <div className="flex items-center gap-3 p-3 rounded-lg border border-border">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={state.securityEnabled}
            onChange={e => onChange({ securityEnabled: e.target.checked })}
            className="rounded"
          />
          <Shield className="h-4 w-4 text-primary" />
          <span className="text-sm font-medium">Security Policy</span>
        </label>
        {state.securityEnabled && (
          <div className="flex-1 grid grid-cols-3 gap-2 ml-4">
            <Input
              value={state.securityPolicyName}
              onChange={e => onChange({ securityPolicyName: e.target.value })}
              placeholder="BNKSecPolicy name"
              className="h-7 text-xs"
            />
            <Input
              value={state.firewallPolicyName}
              onChange={e => onChange({ firewallPolicyName: e.target.value })}
              placeholder="F5BigFwPolicy name"
              className="h-7 text-xs"
            />
            <Select value={state.targetListener} onValueChange={v => onChange({ targetListener: v })}>
              <SelectTrigger className="h-7 text-xs"><SelectValue placeholder="Target listener..." /></SelectTrigger>
              <SelectContent>
                <SelectItem value="">All Listeners</SelectItem>
                {state.listeners.map(l => (
                  <SelectItem key={l.name} value={l.name}>{l.name} ({l.protocol}/{l.port})</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </div>

      {/* Firewall rules */}
      {state.securityEnabled && (
        <div className="rounded-lg border border-border p-3">
          <div className="flex items-center justify-between mb-3">
            <h4 className="text-xs font-semibold">Firewall Rules ({state.firewallRules.length})</h4>
            <div className="flex gap-1">
              {POLICY_TEMPLATES.slice(0, 3).map(t => (
                <Button key={t.label} variant="ghost" size="sm" className="h-6 text-[10px] gap-1" onClick={() => applyTemplate(t.rules)}>
                  <Wand2 className="h-3 w-3" /> {t.label}
                </Button>
              ))}
            </div>
          </div>

          {state.firewallRules.length === 0 ? (
            <div className="text-center py-6">
              <p className="text-xs text-muted-foreground">
                Use a template above or add rules manually
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {state.firewallRules.map((rule, i) => (
                <CompactRuleCard
                  key={rule.id}
                  rule={rule}
                  index={i}
                  total={state.firewallRules.length}
                  onChange={updated => updateRule(i, updated)}
                  onRemove={() => removeRule(i)}
                  onMoveUp={() => moveRule(i, -1)}
                  onMoveDown={() => moveRule(i, 1)}
                />
              ))}
            </div>
          )}

          <div className="flex gap-1 mt-2">
            {RULE_TEMPLATES.map(t => (
              <Button key={t.label} variant="outline" size="sm" className="h-6 text-[10px]" onClick={() => addRule(t.rule)}>
                <Plus className="h-2.5 w-2.5 mr-0.5" /> {t.label}
              </Button>
            ))}
          </div>
        </div>
      )}

      {/* Network policy toggle */}
      <div className="flex items-center gap-3 p-3 rounded-lg border border-border">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={state.networkEnabled}
            onChange={e => onChange({ networkEnabled: e.target.checked })}
            className="rounded"
          />
          <Network className="h-4 w-4 text-primary" />
          <span className="text-sm font-medium">Network Policy</span>
        </label>
        {state.networkEnabled && (
          <div className="flex-1 grid grid-cols-2 gap-2 ml-4">
            <Input
              value={state.networkPolicyName}
              onChange={e => onChange({ networkPolicyName: e.target.value })}
              placeholder="BNKNetPolicy name"
              className="h-7 text-xs"
            />
            <Select value={state.networkTargetListener} onValueChange={v => onChange({ networkTargetListener: v })}>
              <SelectTrigger className="h-7 text-xs"><SelectValue placeholder="Target listener..." /></SelectTrigger>
              <SelectContent>
                <SelectItem value="">All Listeners</SelectItem>
                {state.listeners.map(l => (
                  <SelectItem key={l.name} value={l.name}>{l.name} ({l.protocol}/{l.port})</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </div>

      {state.networkEnabled && iRules.length > 0 && (
        <div className="rounded-lg border border-border p-3">
          <h4 className="text-xs font-semibold mb-2">iRules</h4>
          <div className="space-y-1">
            {iRules.map(ir => {
              const isAdded = state.networkExtensionRefs.some(e => e.name === ir.name);
              return (
                <button
                  key={ir.name}
                  onClick={() => {
                    if (isAdded) {
                      onChange({ networkExtensionRefs: state.networkExtensionRefs.filter(e => e.name !== ir.name) });
                    } else {
                      onChange({ networkExtensionRefs: [...state.networkExtensionRefs, { kind: 'F5BigCneIrule', name: ir.name, group: 'k8s.f5net.com' }] });
                    }
                  }}
                  className={cn(
                    'flex items-center gap-2 w-full px-2 py-1.5 rounded text-xs text-left transition-colors',
                    isAdded
                      ? 'bg-primary/10 text-primary'
                      : 'hover:bg-muted text-foreground/80',
                  )}
                >
                  <Code className="h-3 w-3 text-warning" />
                  <span className="flex-1">{ir.name}</span>
                  <span className="opacity-50 text-[10px]">{ir.lineCount} lines</span>
                  {isAdded && <Check className="h-3 w-3 text-success" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

/** Compact rule card for the security step */
function CompactRuleCard({
  rule,
  index,
  total,
  onChange,
  onRemove,
  onMoveUp,
  onMoveDown,
}: {
  rule: PolicyBuilderRule;
  index: number;
  total: number;
  onChange: (updated: PolicyBuilderRule) => void;
  onRemove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
}) {
  const ruleVariant: 'success' | 'destructive' | 'warning' =
    rule.action === 'accept' ? 'success' : rule.action === 'drop' ? 'destructive' : 'warning';

  return (
    <div className="flex items-center gap-2 px-2 py-1.5 rounded border border-border bg-card">
      <span className="text-[10px] font-mono opacity-40 w-4">#{index + 1}</span>
      <Input
        value={rule.name}
        onChange={e => onChange({ ...rule, name: e.target.value })}
        placeholder="rule-name"
        className="h-6 text-[11px] w-28"
      />
      <Select value={rule.action} onValueChange={v => onChange({ ...rule, action: v as PolicyBuilderRule['action'] })}>
        <SelectTrigger className="h-6 text-[11px] w-20"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="accept">Accept</SelectItem>
          <SelectItem value="drop">Drop</SelectItem>
          <SelectItem value="reject">Reject</SelectItem>
        </SelectContent>
      </Select>
      <Select value={rule.ipProtocol} onValueChange={v => onChange({ ...rule, ipProtocol: v as PolicyBuilderRule['ipProtocol'] })}>
        <SelectTrigger className="h-6 text-[11px] w-16"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="tcp">TCP</SelectItem>
          <SelectItem value="udp">UDP</SelectItem>
          <SelectItem value="any">Any</SelectItem>
        </SelectContent>
      </Select>
      <Badge variant={ruleVariant} className="text-[9px] px-1">{rule.action}</Badge>
      <div className="flex-1" />
      <div className="flex items-center gap-0.5">
        <button onClick={onMoveUp} disabled={index === 0} className={cn('p-0.5 rounded', index === 0 ? 'opacity-20' : 'hover:bg-muted')}>
          <ArrowUp className="h-3 w-3" />
        </button>
        <button onClick={onMoveDown} disabled={index === total - 1} className={cn('p-0.5 rounded', index === total - 1 ? 'opacity-20' : 'hover:bg-muted')}>
          <ArrowDown className="h-3 w-3" />
        </button>
        <button onClick={onRemove} className="p-0.5 rounded hover:bg-destructive/10 text-destructive ml-1">
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 4: Review
// ---------------------------------------------------------------------------

function ReviewStep({
  yaml,
  resourceCount,
}: {
  yaml: string;
  resourceCount: number;
}) {
  const copyToClipboard = () => {
    navigator.clipboard.writeText(yaml);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="text-xs">{resourceCount} resources</Badge>
          <span className="text-xs text-muted-foreground">will be created</span>
        </div>
        <Button size="sm" variant="ghost" onClick={copyToClipboard} className="h-7 text-xs gap-1">
          <Copy className="h-3 w-3" /> Copy
        </Button>
      </div>
      <pre className="rounded-lg border border-border p-4 text-xs font-mono whitespace-pre overflow-auto max-h-[50vh] text-foreground/80 bg-muted/50">
        {yaml || '(nothing to generate — complete previous steps)'}
      </pre>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

interface ConfigBuilderProps {
  clusterId: number;
  namespace?: string;
}

export function ConfigBuilder({ clusterId, namespace }: ConfigBuilderProps) {
  const queryClient = useQueryClient();
  const { data: bnkData, isLoading, error } = useBnkData(clusterId, { namespace });

  const [step, setStep] = useState<ConfigBuilderStep>('gateway');
  const [state, setState] = useState<ConfigBuilderState>(() =>
    createInitialState(namespace || 'default')
  );
  const [isApplying, setIsApplying] = useState(false);

  // Palette data
  const palette = bnkData?.palette;
  const gatewayClasses = palette?.gatewayClasses ?? [];
  const services = palette?.services ?? [];
  const addressLists = palette?.addressLists ?? [];
  const portLists = palette?.portLists ?? [];
  const iRules = palette?.iRules ?? [];

  // State update
  const onChange = useCallback((partial: Partial<ConfigBuilderState>) => {
    setState(prev => ({ ...prev, ...partial }));
  }, []);

  // YAML generation
  const generatedYaml = useMemo(() => {
    try {
      return generateConfigYaml(state);
    } catch {
      return '';
    }
  }, [state]);

  const resourceCount = useMemo(() => {
    return splitYamlDocuments(generatedYaml).length;
  }, [generatedYaml]);

  // Validation per step
  const stepErrors = useMemo((): Record<ConfigBuilderStep, string[]> => {
    const errs: Record<ConfigBuilderStep, string[]> = { gateway: [], routes: [], security: [], review: [] };
    if (!state.gatewayName) errs.gateway.push('Gateway name is required');
    if (!state.gatewayClassName) errs.gateway.push('Gateway class is required');
    if (state.listeners.length === 0) errs.gateway.push('At least one listener is required');

    if (state.routes.length === 0) errs.routes.push('Add at least one route');
    state.routes.forEach((r, i) => {
      if (!r.name) errs.routes.push(`Route #${i + 1} needs a name`);
      if (!r.listenerName) errs.routes.push(`Route #${i + 1} needs a listener`);
      if (r.backends.some(b => !b.serviceName)) errs.routes.push(`Route #${i + 1} has empty backend`);
    });

    if (state.securityEnabled) {
      if (!state.securityPolicyName) errs.security.push('Security policy name required');
      if (!state.firewallPolicyName) errs.security.push('Firewall policy name required');
      if (state.firewallRules.length === 0) errs.security.push('Add at least one firewall rule');
    }
    if (state.networkEnabled) {
      if (!state.networkPolicyName) errs.security.push('Network policy name required');
      if (state.networkExtensionRefs.length === 0) errs.security.push('Add at least one extension');
    }

    return errs;
  }, [state]);

  const currentStepValid = stepErrors[step].length === 0;
  const allValid = Object.values(stepErrors).every(e => e.length === 0);

  // Navigation
  const stepIdx = STEPS.findIndex(s => s.key === step);
  const canGoBack = stepIdx > 0;
  const canGoForward = stepIdx < STEPS.length - 1;

  const goNext = () => {
    if (canGoForward) setStep(STEPS[stepIdx + 1].key);
  };
  const goBack = () => {
    if (canGoBack) setStep(STEPS[stepIdx - 1].key);
  };

  // Apply
  const handleApply = async (dryRun: boolean) => {
    if (!allValid) return;
    setIsApplying(true);
    try {
      const docs = splitYamlDocuments(generatedYaml);
      for (const doc of docs) {
        const resourceType = extractResourceType(doc);
        if (!resourceType) continue;
        await api.createK8sResource(clusterId, resourceType, {
          resource_yaml: doc,
          namespace: state.namespace,
          dry_run: dryRun,
        });
      }
      if (dryRun) {
        notify.success(`Dry run passed — all ${docs.length} resources are valid`, undefined, { category: 'cluster' });
      } else {
        notify.success(`Configuration applied — ${docs.length} resources created`, undefined, { category: 'cluster' });
        queryClient.invalidateQueries({ queryKey: ['bnk-resources'] });
        queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', clusterId] });
        setState(createInitialState(namespace || 'default'));
        setStep('gateway');
      }
    } catch (err: unknown) {
      const parsed = parseApiError(err);
      notify.error(dryRun ? 'Dry run failed' : 'Apply failed', parsed.message, { category: 'cluster' });
    } finally {
      setIsApplying(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (isLoading) return <SkeletonTable rows={6} columns={3} />;
  if (error) return <EmptyState icon={AlertTriangle} title="Failed to load BNK data" description={parseApiError(error).message} />;

  return (
    <div className="flex flex-col gap-4">
      {/* Step indicator */}
      <StepIndicator steps={STEPS} currentStep={step} onStepClick={setStep} />

      {/* Step content */}
      <div className="min-h-[400px]">
        {step === 'gateway' && (
          <GatewayStep state={state} onChange={onChange} gatewayClasses={gatewayClasses} />
        )}
        {step === 'routes' && (
          <RoutesStep state={state} onChange={onChange} services={services} />
        )}
        {step === 'security' && (
          <SecurityStep
            state={state}
            onChange={onChange}
            addressLists={addressLists}
            portLists={portLists}
            iRules={iRules}
          />
        )}
        {step === 'review' && (
          <ReviewStep yaml={generatedYaml} resourceCount={resourceCount} />
        )}
      </div>

      {/* Validation errors for current step */}
      {stepErrors[step].length > 0 && (
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          {stepErrors[step].map(err => (
            <span key={err} className="flex items-center gap-1">
              <AlertTriangle className="h-3 w-3 text-warning" />{err}
            </span>
          ))}
        </div>
      )}

      {/* Navigation + actions */}
      <div className="flex items-center justify-between">
        <Button
          variant="outline"
          size="sm"
          className="h-8 text-xs gap-1.5"
          onClick={goBack}
          disabled={!canGoBack}
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back
        </Button>

        <div className="flex items-center gap-2">
          {step === 'review' ? (
            <>
              <Button
                variant="outline"
                size="sm"
                className="h-8 text-xs gap-1.5"
                onClick={() => handleApply(true)}
                disabled={!allValid || isApplying}
              >
                <Play className="h-3.5 w-3.5" /> Dry Run
              </Button>
              <Button
                size="sm"
                className="h-8 text-xs gap-1.5"
                onClick={() => handleApply(false)}
                disabled={!allValid || isApplying}
              >
                <Check className="h-3.5 w-3.5" /> Apply All ({resourceCount})
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              className="h-8 text-xs gap-1.5"
              onClick={goNext}
              disabled={!canGoForward || !currentStepValid}
            >
              Next <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
