/**
 * F5 AI Analyzer Viewer Component
 * 
 * Custom visualization for F5BigAnalyzer CRDs that provide
 * intelligent AI-driven load balancing for LLM inference workloads.
 * 
 * Shows:
 * - Monitored applications (HTTPRoutes/L4Routes)
 * - Data sources (Prometheus endpoints)
 * - Script configuration (built-in vs custom)
 * - Schedule and status
 */

import { useMemo, useState } from 'react';
import { 
  Activity, 
  AlertCircle, 
  ChevronDown, 
  ChevronRight, 
  Clock,
  Code,
  Database,
  Route,
  Settings,
  CheckCircle2,
  XCircle,
  Loader2,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { formatAge } from '@/lib/time-utils';
import { AlertTriangle, Brain, Scale, TrendingUp } from 'lucide-react';
import { useAnalyzerRuntimeMetrics } from '@/hooks/useAnalyzerMetrics';
import type { AnalyzerRuntimeMetrics } from '@/hooks/useAnalyzerMetrics';
import { BackendHealthSection } from '@/components/k8s/BackendHealthSection';
import { useBackendHealth } from '@/hooks/useBackendHealth';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion';
import { useNavigate } from 'react-router-dom';
import { ErrorState } from '@/components/ui/error-state';
import { parseApiError } from '@/lib/error-handler';

// ---------------------------------------------------------------------------
// Demo-preview helpers
// ---------------------------------------------------------------------------
//
// These helpers surface richer AI-analyzer visualisations from whatever data
// exists on the CR, and fall back to deterministic sample data where the
// runtime pipeline (Prometheus bridge, analyzer-pod metrics, decision log)
// isn't wired yet. Every sample section carries a visible "sample" badge so
// viewers can distinguish configured values from mocked previews.
//
// Deterministic per-analyzer: seeded off the analyzer name so demos render
// consistently across reloads.
// ---------------------------------------------------------------------------

function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h);
}

interface ModelWeight {
  model: string;
  weight: number; // percent
  source: 'configured' | 'sample';
}

const SAMPLE_MODELS = ['llama3-70b-instruct', 'gpt-4o', 'claude-sonnet-4', 'mistral-large', 'gemini-2.5-pro'];

function parseModelsAndWeights(
  params: AnalyzerScriptParameter[] | undefined,
  applications: AnalyzerApplication[],
  httpRoutesByRef: Map<string, HTTPRouteLike>,
  seed: string,
): ModelWeight[] {
  // 1. Custom-script path — parameters like model_a=llama3 / weight_a=40
  const modelPairs: Record<string, { model?: string; weight?: number }> = {};
  for (const p of params ?? []) {
    const key = String(p.key);
    const val = String(p.value);
    const modelMatch = key.match(/^model[_-]?([a-zA-Z0-9]+)$/i);
    const weightMatch = key.match(/^weight[_-]?([a-zA-Z0-9]+)$/i);
    if (modelMatch) {
      const id = modelMatch[1].toLowerCase();
      modelPairs[id] = { ...(modelPairs[id] ?? {}), model: val };
    } else if (weightMatch) {
      const id = weightMatch[1].toLowerCase();
      const n = Number(val);
      if (!Number.isNaN(n)) modelPairs[id] = { ...(modelPairs[id] ?? {}), weight: n };
    }
  }
  const configured: ModelWeight[] = Object.values(modelPairs)
    .filter((v) => v.model)
    .map((v) => ({ model: v.model!, weight: v.weight ?? 0, source: 'configured' }));
  if (configured.length > 0) return configured;

  // 2. Builtin-script path — derive from HTTPRoute backends + live weights.
  // For a builtin analyzer like LLMLoadMonitor, "model" = backend service
  // name and "weight %" = whatever the controller / weight-shifter has
  // written to backendRefs[].weight. Same data source as Traffic
  // Distribution, so both sections stay coherent.
  const backendWeights: Record<string, number> = {};
  let total = 0;
  for (const app of applications) {
    if (!/httproute/i.test(app.kind)) continue;
    const route = httpRoutesByRef.get(`${app.namespace}/${app.name}`);
    if (!route) continue;
    for (const rule of route.spec?.rules ?? []) {
      for (const ref of rule.backendRefs ?? []) {
        const name = ref.name ?? '<unnamed>';
        const w = Number(ref.weight ?? 1);
        if (Number.isNaN(w)) continue;
        backendWeights[name] = (backendWeights[name] ?? 0) + w;
        total += w;
      }
    }
  }
  if (total > 0) {
    return Object.entries(backendWeights)
      .sort((a, b) => b[1] - a[1])
      .map(([name, w]) => ({
        model: name,
        weight: Math.round((w / total) * 100),
        source: 'configured' as const,
      }));
  }

  // 3. Last-resort sample (no parameters, no reachable HTTPRoute weights)
  const base = hashString(seed);
  const a = 40 + (base % 15);
  const b = 35 - ((base >> 4) % 15);
  const c = Math.max(0, 100 - a - b);
  return [
    { model: SAMPLE_MODELS[base % SAMPLE_MODELS.length], weight: a, source: 'sample' },
    { model: SAMPLE_MODELS[(base + 1) % SAMPLE_MODELS.length], weight: b, source: 'sample' },
    { model: SAMPLE_MODELS[(base + 2) % SAMPLE_MODELS.length], weight: c, source: 'sample' },
  ];
}

interface RuntimeMetricsAgg {
  latencyP50Ms: number | null;
  latencyP95Ms: number | null;
  tokensPerSecond: number | null;
  errorRatePct: number | null;
  requestsPerMin: number | null;
}

/**
 * Roll up per-backend metrics into a single aggregate view.
 * - latency p50/p95: max across backends (worst-case)
 * - TPS / req/min: sum across backends (total throughput)
 * - error rate: max across backends (worst-case)
 */
function aggregateRuntimeMetrics(
  perBackend: Record<string, Record<string, number | null>> | undefined,
): RuntimeMetricsAgg {
  const empty: RuntimeMetricsAgg = {
    latencyP50Ms: null,
    latencyP95Ms: null,
    tokensPerSecond: null,
    errorRatePct: null,
    requestsPerMin: null,
  };
  if (!perBackend) return empty;
  const vals = Object.values(perBackend);
  if (vals.length === 0) return empty;

  const pickMax = (k: string) => {
    const nums = vals.map((b) => b[k]).filter((v): v is number => typeof v === 'number' && !Number.isNaN(v));
    return nums.length ? Math.max(...nums) : null;
  };
  const pickSum = (k: string) => {
    const nums = vals.map((b) => b[k]).filter((v): v is number => typeof v === 'number' && !Number.isNaN(v));
    return nums.length ? nums.reduce((a, b) => a + b, 0) : null;
  };

  return {
    latencyP50Ms: pickMax('latency_p50_ms'),
    latencyP95Ms: pickMax('latency_p95_ms'),
    tokensPerSecond: pickSum('tokens_per_second'),
    requestsPerMin: pickSum('requests_per_minute'),
    errorRatePct: pickMax('error_rate_pct'),
  };
}

function fmtMs(v: number | null): string { return v == null ? '—' : `${Math.round(v)}ms`; }
function fmtInt(v: number | null): string { return v == null ? '—' : Math.round(v).toLocaleString(); }
function fmtPct(v: number | null, digits = 2): string {
  return v == null ? '—' : `${v.toFixed(digits)}%`;
}

// "Recent Routing Decisions" removed — to be replaced in a follow-up PR with
// "Weight Change History" sourced from our own HTTPRoute-annotation diff log
// (the analyzer does not publish a decision log, so the previous sample-only
// rendering couldn't become real without a new backend service).

interface HTTPRouteBackendRef {
  name?: string;
  namespace?: string;
  weight?: number;
  port?: number;
}
interface HTTPRouteLike {
  metadata?: {
    name?: string;
    namespace?: string;
    annotations?: Record<string, string>;
  };
  spec?: {
    rules?: Array<{
      backendRefs?: HTTPRouteBackendRef[];
    }>;
  };
}

interface TrafficSlice {
  application: string; // backend name (for live) or route name (for sample)
  percent: number;
  source: 'live' | 'configured' | 'sample';
}

/**
 * Live path: read where F5BigAnalyzer / weight-shifter actually writes.
 *
 * Primary source: `k8s.f5.com/service-settings` annotation on the monitored
 * HTTPRoute. Per F5 docs it contains JSON like:
 *     {"pool-3":{"10.244.114.53":33,"10.244.114.54":34,"10.244.99.91":33}}
 * Secondary source: sum of `spec.rules[].backendRefs[].weight` — Gateway-API
 * native path some deployments use.
 *
 * Polling cadence (5s) surfaces live weight shifts.
 */
const SERVICE_SETTINGS_ANNOTATION = 'k8s.f5.com/service-settings';

function deriveTrafficFromHTTPRoutes(
  applications: AnalyzerApplication[],
  httpRoutesByRef: Map<string, HTTPRouteLike>,
): TrafficSlice[] {
  // 1a. Annotation path — preferred (this is where the analyzer writes).
  for (const app of applications) {
    if (!/httproute/i.test(app.kind)) continue;
    const route = httpRoutesByRef.get(`${app.namespace}/${app.name}`);
    const ann = route?.metadata?.annotations?.[SERVICE_SETTINGS_ANNOTATION];
    if (!ann) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(ann);
    } catch {
      continue;
    }
    if (!parsed || typeof parsed !== 'object') continue;
    // Flatten: {poolName: {ip: weight, ...}, ...} → per-backend totals.
    const perBackend: Record<string, number> = {};
    let total = 0;
    for (const [pool, members] of Object.entries(parsed as Record<string, unknown>)) {
      if (members && typeof members === 'object') {
        for (const [ip, w] of Object.entries(members as Record<string, unknown>)) {
          const n = Number(w);
          if (Number.isNaN(n)) continue;
          const key = `${pool} · ${ip}`;
          perBackend[key] = (perBackend[key] ?? 0) + n;
          total += n;
        }
      } else {
        const n = Number(members);
        if (!Number.isNaN(n)) {
          perBackend[pool] = (perBackend[pool] ?? 0) + n;
          total += n;
        }
      }
    }
    if (total > 0) {
      return Object.entries(perBackend)
        .sort((a, b) => b[1] - a[1])
        .map(([name, w]) => ({
          application: name,
          percent: Math.round((w / total) * 100),
          source: 'live' as const,
        }));
    }
  }

  // 1b. backendRefs path — fallback for Gateway-API-native weight routing.
  const backendTotals: Record<string, number> = {};
  let overallTotal = 0;
  for (const app of applications) {
    if (!/httproute/i.test(app.kind)) continue;
    const route = httpRoutesByRef.get(`${app.namespace}/${app.name}`);
    if (!route) continue;
    for (const rule of route.spec?.rules ?? []) {
      for (const ref of rule.backendRefs ?? []) {
        const name = ref.name ?? '<unnamed>';
        const w = Number(ref.weight ?? 1);
        if (Number.isNaN(w)) continue;
        backendTotals[name] = (backendTotals[name] ?? 0) + w;
        overallTotal += w;
      }
    }
  }
  if (overallTotal <= 0) return [];
  return Object.entries(backendTotals)
    .sort((a, b) => b[1] - a[1])
    .map(([name, w]) => ({
      application: name,
      percent: Math.round((w / overallTotal) * 100),
      source: 'live' as const,
    }));
}

function deriveTrafficDistribution(
  applications: AnalyzerApplication[],
  params: AnalyzerScriptParameter[] | undefined,
  httpRoutesByRef: Map<string, HTTPRouteLike>,
  seed: string,
): TrafficSlice[] {
  if (applications.length === 0) return [];

  // 1. Live — HTTPRoute backendRefs[].weight (analyzer writes here)
  const live = deriveTrafficFromHTTPRoutes(applications, httpRoutesByRef);
  if (live.length > 0) return live;

  // 2. Configured — weight_<appname> in script.parameters (static config)
  const weights: Record<string, number> = {};
  for (const p of params ?? []) {
    const m = String(p.key).match(/^weight[_-]?(.+)$/i);
    const n = Number(p.value);
    if (m && !Number.isNaN(n)) weights[m[1].toLowerCase()] = n;
  }
  const hasConfigured = applications.some((a) => weights[a.name.toLowerCase()] != null);
  if (hasConfigured) {
    const total = applications.reduce((s, a) => s + (weights[a.name.toLowerCase()] ?? 0), 0) || 1;
    return applications.map((a) => ({
      application: a.name,
      percent: Math.round(((weights[a.name.toLowerCase()] ?? 0) / total) * 100),
      source: 'configured' as const,
    }));
  }

  // 3. Sample — deterministic skewed distribution
  const h = hashString(seed);
  const raw = applications.map((_, i) => 20 + ((h >> (i * 2)) % 80));
  const total = raw.reduce((s, v) => s + v, 0) || 1;
  return applications.map((a, i) => ({
    application: a.name,
    percent: Math.round((raw[i] / total) * 100),
    source: 'sample' as const,
  }));
}

function SampleBadge() {
  return (
    <Badge variant="outline" className="text-[10px] border-warning/40 text-warning bg-warning/5">
      sample
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Status helpers — hoisted to module scope so AnalyzerCard and the parent can
// both use them without each calling them from component scope (which would
// be fine but is unnecessary).
// ---------------------------------------------------------------------------

function getStatusCondition(analyzer: F5BigAnalyzer) {
  const conditions = analyzer.status?.conditions || [];
  // F5BigAnalyzer CRDs use Programmed/Accepted (Gateway API style), not Ready/Available
  return conditions.find(
    (c) => c.type === 'Programmed' || c.type === 'Accepted' || c.type === 'Ready' || c.type === 'Available',
  );
}

function getStatusIcon(analyzer: F5BigAnalyzer) {
  const condition = getStatusCondition(analyzer);
  if (!condition) return <Loader2 className="h-4 w-4 text-muted-foreground animate-spin" />;
  if (condition.status === 'True') return <CheckCircle2 className="h-4 w-4 text-success" />;
  return <XCircle className="h-4 w-4 text-destructive" />;
}

function getStatusText(analyzer: F5BigAnalyzer, backendHealthAvailable?: boolean): string {
  const condition = getStatusCondition(analyzer);
  if (!condition) {
    // No CR controller writes .status.conditions for F5BigAnalyzer, so derive
    // a live status from backend-health when it's available.
    if (backendHealthAvailable === true) return 'Active';
    return 'Pending';
  }
  if (condition.status === 'True') return 'Running';
  return condition.reason || 'Failed';
}

function parseSchedule(schedule?: string): string {
  if (!schedule) return 'Not configured';
  const match = schedule.match(/^(\d+)(m|h|d)$/);
  if (match) {
    const value = parseInt(match[1], 10);
    const unit = match[2];
    const unitMap: Record<string, string> = { m: 'minute', h: 'hour', d: 'day' };
    return `Every ${value} ${unitMap[unit]}${value > 1 ? 's' : ''}`;
  }
  return schedule;
}

interface F5AIAnalyzerViewerProps {
  clusterId: number;
  namespace?: string;
}

interface AnalyzerApplication {
  kind: string;
  group: string;
  name: string;
  namespace: string;
}

interface AnalyzerDataSource {
  name: string;
  type?: string;
  endpoint?: string;
  username?: string;
}

interface AnalyzerScriptParameter {
  key: string;
  value: string;
}

interface AnalyzerScript {
  type: 'builtin' | 'custom';
  builtin?: {
    name: string;
  };
  custom?: {
    configMapRef?: {
      name: string;
      key: string;
    };
    // CRD returns array of {key, value} objects, not a flat Record
    parameters?: AnalyzerScriptParameter[];
  };
}

interface F5BigAnalyzer {
  metadata: {
    name: string;
    namespace?: string;
    creationTimestamp?: string;
    uid?: string;
  };
  spec: {
    name?: string;
    applications?: AnalyzerApplication[];
    dataSources?: AnalyzerDataSource[];
    script?: AnalyzerScript;
    schedule?: string;
  };
  status?: {
    conditions?: Array<{
      type: string;
      status: string;
      reason?: string;
      message?: string;
      lastTransitionTime?: string;
    }>;
    lastRunTime?: string;
    nextRunTime?: string;
  };
}

export function F5AIAnalyzerViewer({ clusterId, namespace }: F5AIAnalyzerViewerProps) {
  const navigate = useNavigate();
  const [expandedAnalyzers, setExpandedAnalyzers] = useState<Set<string>>(new Set());

  const { data, isLoading, error } = useQuery({
    queryKey: ['bnk-resources', clusterId, 'f5biganalyzer', namespace],
    queryFn: () => api.getClusterResources(clusterId, 'f5biganalyzer', {
      namespace: namespace === 'all' ? undefined : namespace
    }),
    enabled: !!clusterId,
    staleTime: 30000,
  });

  // Parallel fetch: HTTPRoutes monitored by any analyzer. Polled every 5s so
  // weight shifts written by the analyzer into backendRefs[].weight surface
  // live in the Traffic Distribution section.
  const { data: httpRoutesData } = useQuery({
    queryKey: ['bnk-resources', clusterId, 'httproute', namespace],
    queryFn: () => api.getClusterResources(clusterId, 'httproute', {
      namespace: namespace === 'all' ? undefined : namespace,
    }),
    enabled: !!clusterId,
    refetchInterval: 5000,
    staleTime: 0,
  });
  const httpRoutesByRef = useMemo(() => {
    const map = new Map<string, HTTPRouteLike>();
    for (const r of (httpRoutesData?.resources ?? []) as HTTPRouteLike[]) {
      const ns = r?.metadata?.namespace ?? '';
      const name = r?.metadata?.name ?? '';
      if (name) map.set(`${ns}/${name}`, r);
    }
    return map;
  }, [httpRoutesData]);

  const toggleExpansion = (uid: string) => {
    const newExpanded = new Set(expandedAnalyzers);
    if (newExpanded.has(uid)) {
      newExpanded.delete(uid);
    } else {
      newExpanded.add(uid);
    }
    setExpandedAnalyzers(newExpanded);
  };

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (error) {
    const parsedAnalyzerError = parseApiError(error);
    const analyzerErrorRoute = parsedAnalyzerError.action?.route;
    return (
      <ErrorState
        error={error}
        size="sm"
        {...(analyzerErrorRoute ? {
          secondaryAction: {
            label: parsedAnalyzerError.action!.label,
            onClick: () => navigate(analyzerErrorRoute),
          },
        } : {})}
      />
    );
  }

  const analyzers = (data?.resources || []) as F5BigAnalyzer[];

  // Empty state — no F5BigAnalyzer CRs in this cluster yet. Render a
  // single informational panel rather than a fake card so operators don't
  // mistake a placeholder for a stale CR.
  if (analyzers.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5 text-primary" />
            AI Analyzers
            <Badge
              variant="outline"
              className="ml-2 gap-1 text-[11px] border-warning/40 text-warning bg-warning/5"
            >
              <AlertCircle className="h-3 w-3" />
              no analyzer CR found
            </Badge>
          </CardTitle>
          <CardDescription>
            No F5BigAnalyzer resources in this cluster yet
            {namespace && namespace !== 'all' && ` (namespace: ${namespace})`}
            . Once one is applied, its monitored applications, data sources,
            script configuration, models &amp; weights, runtime metrics, and
            traffic distribution will appear here.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {/* Summary Header */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5 text-primary" />
            AI Analyzers
          </CardTitle>
          <CardDescription>
            Viewing {analyzers.length} analyzer{analyzers.length !== 1 ? 's' : ''} for intelligent LLM traffic distribution
            {namespace && namespace !== 'all' && ` (namespace: ${namespace})`}
          </CardDescription>
        </CardHeader>
      </Card>

      {/* Analyzer Cards */}
      {analyzers.map((analyzer, index) => {
        const uid = analyzer.metadata.uid || `analyzer-${index}`;
        return (
          <AnalyzerCard
            key={uid}
            analyzer={analyzer}
            uid={uid}
            isExpanded={expandedAnalyzers.has(uid)}
            onToggle={toggleExpansion}
            clusterId={clusterId}
            httpRoutesByRef={httpRoutesByRef}
          />
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AnalyzerCard — one card per analyzer. Extracted from F5AIAnalyzerViewer so
// the hooks (useAnalyzerRuntimeMetrics) live at the top of a stable component
// instead of inside a .map() callback (which would break the Rules of Hooks
// whenever the analyzers array size or expansion state changes — React error
// #310).
// ---------------------------------------------------------------------------

interface AnalyzerCardProps {
  analyzer: F5BigAnalyzer;
  uid: string;
  isExpanded: boolean;
  onToggle: (uid: string) => void;
  clusterId: number;
  httpRoutesByRef: Map<string, HTTPRouteLike>;
}

function AnalyzerCard({
  analyzer,
  uid,
  isExpanded,
  onToggle,
  clusterId,
  httpRoutesByRef,
}: AnalyzerCardProps) {
  const spec = analyzer.spec || {};
  const applications = spec.applications || [];
  const dataSources = spec.dataSources || [];
  const script = spec.script;

  // All hooks at the top — stable count across every render of this card.
  // enabled=false still registers the hook call; it just skips the fetch,
  // keeping the hooks order invariant when the placeholder toggles.
  const backendHealthQuery = useBackendHealth(
    clusterId,
    analyzer.metadata.namespace,
    analyzer.metadata.name,
  );
  const backendHealthAvailable = backendHealthQuery.data?.available === true;

  const metricsQuery = useAnalyzerRuntimeMetrics(
    clusterId,
    analyzer.metadata.namespace,
    analyzer.metadata.name,
  );

  const modelWeights = parseModelsAndWeights(
    script?.custom?.parameters,
    spec.applications ?? [],
    httpRoutesByRef,
    analyzer.metadata.name,
  );
  const liveMetrics: AnalyzerRuntimeMetrics | undefined = metricsQuery.data;
  const runtimeAgg = aggregateRuntimeMetrics(liveMetrics?.per_backend);
  const metricsAvailable = liveMetrics?.available === true;
  const metricsLoading = metricsQuery.isLoading || metricsQuery.isFetching;
  const trafficSlices = deriveTrafficDistribution(
    spec.applications ?? [],
    script?.custom?.parameters,
    httpRoutesByRef,
    analyzer.metadata.name,
  );
  const anyModelIsSample = modelWeights.some((m) => m.source === 'sample');
  const anySliceIsSample = trafficSlices.some((s) => s.source === 'sample');
  const anySliceIsLive = trafficSlices.some((s) => s.source === 'live');

  return (
    <Card key={uid} className="border-2">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <div className="space-y-1 flex-1">
            <div className="flex items-center gap-3">
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger>{getStatusIcon(analyzer)}</TooltipTrigger>
                  <TooltipContent>
                    <p>{getStatusText(analyzer, backendHealthAvailable)}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <CardTitle className="text-lg">{analyzer.metadata.name}</CardTitle>
              <Badge variant="outline" className="text-xs">
                {analyzer.metadata.namespace}
              </Badge>
            </div>
            <CardDescription className="flex flex-wrap gap-2 mt-2">
              <Badge variant="secondary" className="gap-1">
                <Clock className="h-3 w-3" />
                {parseSchedule(spec.schedule)}
              </Badge>
              <Badge variant="secondary" className="gap-1">
                <Route className="h-3 w-3" />
                {applications.length} application{applications.length !== 1 ? 's' : ''}
              </Badge>
              <Badge variant="secondary" className="gap-1">
                <Database className="h-3 w-3" />
                {dataSources.length} data source{dataSources.length !== 1 ? 's' : ''}
              </Badge>
              {script && (
                <Badge
                  variant={script.type === 'builtin' ? 'default' : 'outline'}
                  className={cn('gap-1', script.type === 'builtin' && 'bg-primary')}
                >
                  <Code className="h-3 w-3" />
                  {script.type === 'builtin' ? script.builtin?.name || 'Built-in' : 'Custom Script'}
                </Badge>
              )}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <Badge
              className={cn(
                'text-xs',
                ['Running', 'Active'].includes(getStatusText(analyzer, backendHealthAvailable))
                  ? 'bg-success/10 text-success border-success/20'
                  : 'bg-muted text-muted-foreground',
              )}
            >
              {getStatusText(analyzer, backendHealthAvailable)}
            </Badge>
            <Button variant="ghost" size="sm" onClick={() => onToggle(uid)}>
              {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </Button>
          </div>
        </div>
      </CardHeader>

      {isExpanded && (
              <CardContent className="space-y-4 pt-0">
                {/* Backend Health — per-pod LLM inference metrics via direct
                    K8s API server proxy /metrics scrape (vLLM shape parser). */}
                <BackendHealthSection
                  clusterId={clusterId}
                  analyzerNamespace={analyzer.metadata.namespace}
                  analyzerName={analyzer.metadata.name}
                />

                {/* Runtime Metrics — real, from the analyzer's own Prometheus
                    dataSource, proxied through the K8s API server. Polls every
                    15s (matches Prometheus default scrape cadence). Degrades
                    cleanly when Prometheus is unreachable or has no series. */}
                <div>
                  <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
                    <TrendingUp className="h-4 w-4 text-info" />
                    Runtime Metrics (last window)
                    {metricsAvailable && (
                      <Badge
                        variant="outline"
                        className="text-[10px] border-info/40 text-info bg-info/5 gap-1"
                      >
                        <span className="relative flex h-1.5 w-1.5">
                          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-info opacity-75" />
                          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-info" />
                        </span>
                        live · Prometheus
                      </Badge>
                    )}
                    {metricsLoading && !liveMetrics && (
                      <Badge variant="outline" className="text-[10px]">
                        <Loader2 className="h-3 w-3 animate-spin mr-1" /> querying
                      </Badge>
                    )}
                    {liveMetrics && !metricsAvailable && (
                      <Badge
                        variant="outline"
                        className="text-[10px] border-destructive/40 text-destructive bg-destructive/5 gap-1"
                        title={liveMetrics.reason}
                      >
                        <AlertTriangle className="h-3 w-3" />
                        Prometheus unavailable
                      </Badge>
                    )}
                  </h4>
                  {liveMetrics && !metricsAvailable && liveMetrics.reason && (
                    <p className="mb-2 text-xs text-muted-foreground">{liveMetrics.reason}</p>
                  )}
                  <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                    <div className="rounded-lg border p-3 bg-muted/20">
                      <div className="text-[11px] text-muted-foreground">Latency p50</div>
                      <div className="text-lg font-semibold mt-0.5">{fmtMs(runtimeAgg.latencyP50Ms)}</div>
                    </div>
                    <div className="rounded-lg border p-3 bg-muted/20">
                      <div className="text-[11px] text-muted-foreground">Latency p95</div>
                      <div className="text-lg font-semibold mt-0.5">{fmtMs(runtimeAgg.latencyP95Ms)}</div>
                    </div>
                    <div className="rounded-lg border p-3 bg-muted/20">
                      <div className="text-[11px] text-muted-foreground">Tokens / sec</div>
                      <div className="text-lg font-semibold mt-0.5">{fmtInt(runtimeAgg.tokensPerSecond)}</div>
                    </div>
                    <div className="rounded-lg border p-3 bg-muted/20">
                      <div className="text-[11px] text-muted-foreground">Error rate</div>
                      <div className="text-lg font-semibold mt-0.5">{fmtPct(runtimeAgg.errorRatePct)}</div>
                    </div>
                    <div className="rounded-lg border p-3 bg-muted/20">
                      <div className="text-[11px] text-muted-foreground">Req / min</div>
                      <div className="text-lg font-semibold mt-0.5">{fmtInt(runtimeAgg.requestsPerMin)}</div>
                    </div>
                  </div>

                  {/* Per-backend breakdown (only when we have real data) */}
                  {metricsAvailable && liveMetrics?.per_backend && Object.keys(liveMetrics.per_backend).length > 0 && (
                    <div className="mt-3 border rounded-lg overflow-hidden">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Backend</TableHead>
                            <TableHead className="text-right">p50</TableHead>
                            <TableHead className="text-right">p95</TableHead>
                            <TableHead className="text-right">Tokens/s</TableHead>
                            <TableHead className="text-right">Req/min</TableHead>
                            <TableHead className="text-right">Errors</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {Object.entries(liveMetrics.per_backend).map(([name, m]) => (
                            <TableRow key={name}>
                              <TableCell className="font-mono text-sm">{name}</TableCell>
                              <TableCell className="text-right">{fmtMs(m.latency_p50_ms ?? null)}</TableCell>
                              <TableCell className="text-right">{fmtMs(m.latency_p95_ms ?? null)}</TableCell>
                              <TableCell className="text-right">{fmtInt(m.tokens_per_second ?? null)}</TableCell>
                              <TableCell className="text-right">{fmtInt(m.requests_per_minute ?? null)}</TableCell>
                              <TableCell className="text-right">{fmtPct(m.error_rate_pct ?? null)}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </div>

                {/* Traffic Distribution across monitored applications */}
                {trafficSlices.length > 0 && (
                  <div>
                    <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
                      <Scale className="h-4 w-4 text-success" />
                      Traffic Distribution
                      {anySliceIsLive && (
                        <Badge
                          variant="outline"
                          className="text-[10px] border-success/40 text-success bg-success/5 gap-1"
                        >
                          <span className="relative flex h-1.5 w-1.5">
                            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-75" />
                            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success" />
                          </span>
                          live · HTTPRoute weights
                        </Badge>
                      )}
                      {anySliceIsSample && <SampleBadge />}
                    </h4>
                    <div className="space-y-1.5">
                      {trafficSlices.map((s, idx) => (
                        <div key={idx} className="flex items-center gap-3">
                          <div className="text-sm font-mono w-[180px] truncate" title={s.application}>
                            {s.application}
                          </div>
                          <div className="flex-1 h-2.5 rounded bg-muted overflow-hidden">
                            <div
                              className="h-full bg-success"
                              style={{ width: `${s.percent}%` }}
                            />
                          </div>
                          <div className="text-sm font-medium w-[48px] text-right">{s.percent}%</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Models & Weights — analyzer's weight decisions; parsed from
                    script.parameters where possible, sample-fallback otherwise. */}
                <div>
                  <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
                    <Brain className="h-4 w-4 text-primary" />
                    Models &amp; Weights
                    {anyModelIsSample && <SampleBadge />}
                  </h4>
                  <div className="border rounded-lg overflow-hidden">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Model</TableHead>
                          <TableHead className="w-[120px]">Weight</TableHead>
                          <TableHead>Distribution</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {modelWeights.map((mw, idx) => (
                          <TableRow key={idx}>
                            <TableCell className="font-mono text-sm">{mw.model}</TableCell>
                            <TableCell className="font-medium">{mw.weight}%</TableCell>
                            <TableCell>
                              <div className="h-2 w-full rounded bg-muted overflow-hidden">
                                <div
                                  className="h-full bg-primary"
                                  style={{ width: `${Math.min(100, mw.weight)}%` }}
                                />
                              </div>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </div>

                {/* Configuration — collapsed by default. Holds the wiring detail
                    operators rarely need during an incident: which apps the
                    analyzer monitors, which Prometheus it queries, the script
                    setup, and CR status conditions. One click expands all. */}
                <Accordion type="single" collapsible>
                  <AccordionItem value="configuration" className="border rounded-lg border-b">
                    <AccordionTrigger className="px-3 py-2 text-sm font-medium hover:no-underline hover:bg-muted/40 transition-colors">
                      <span className="flex items-center gap-2">
                        <Settings className="h-4 w-4 text-muted-foreground" />
                        Configuration
                        <span className="text-xs text-muted-foreground font-normal ml-1">
                          monitored apps · data sources · script · status conditions
                        </span>
                      </span>
                    </AccordionTrigger>
                    <AccordionContent className="px-3 pb-3 pt-1 space-y-4 border-t">
                      {/* Monitored Applications */}
                      {applications.length > 0 && (
                        <div>
                          <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
                            <Route className="h-4 w-4 text-info" />
                            Monitored Applications
                          </h4>
                          <div className="border rounded-lg overflow-hidden">
                            <Table>
                              <TableHeader>
                                <TableRow>
                                  <TableHead>Kind</TableHead>
                                  <TableHead>Name</TableHead>
                                  <TableHead>Namespace</TableHead>
                                  <TableHead>API Group</TableHead>
                                </TableRow>
                              </TableHeader>
                              <TableBody>
                                {applications.map((app, idx) => (
                                  <TableRow key={idx}>
                                    <TableCell>
                                      <Badge variant="outline">{app.kind}</Badge>
                                    </TableCell>
                                    <TableCell className="font-mono text-sm">
                                      {app.name}
                                    </TableCell>
                                    <TableCell className="text-sm text-muted-foreground">
                                      {app.namespace}
                                    </TableCell>
                                    <TableCell className="text-xs text-muted-foreground font-mono">
                                      {app.group}
                                    </TableCell>
                                  </TableRow>
                                ))}
                              </TableBody>
                            </Table>
                          </div>
                        </div>
                      )}

                      {/* Data Sources */}
                      {dataSources.length > 0 && (
                        <div>
                          <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
                            <Database className="h-4 w-4 text-success" />
                            Data Sources
                          </h4>
                          <div className="border rounded-lg overflow-hidden">
                            <Table>
                              <TableHeader>
                                <TableRow>
                                  <TableHead>Name</TableHead>
                                  <TableHead>Type</TableHead>
                                  <TableHead>Endpoint</TableHead>
                                  <TableHead>Auth</TableHead>
                                </TableRow>
                              </TableHeader>
                              <TableBody>
                                {dataSources.map((ds, idx) => (
                                  <TableRow key={idx}>
                                    <TableCell className="font-medium">
                                      {ds.name}
                                    </TableCell>
                                    <TableCell>
                                      <Badge variant="outline">{ds.type || 'prometheus'}</Badge>
                                    </TableCell>
                                    <TableCell className="font-mono text-xs max-w-[300px] truncate">
                                      {ds.endpoint || 'N/A'}
                                    </TableCell>
                                    <TableCell>
                                      {ds.username ? (
                                        <Badge variant="secondary">Basic Auth</Badge>
                                      ) : (
                                        <span className="text-muted-foreground text-sm">None</span>
                                      )}
                                    </TableCell>
                                  </TableRow>
                                ))}
                              </TableBody>
                            </Table>
                          </div>
                        </div>
                      )}

                      {/* Script Configuration */}
                      {script && (
                        <div>
                          <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
                            <Code className="h-4 w-4 text-primary" />
                            Script Configuration
                          </h4>
                          <div className="border rounded-lg p-4 bg-muted/30 space-y-2">
                            <div className="flex items-center gap-2">
                              <span className="text-sm text-muted-foreground">Type:</span>
                              <Badge variant={script.type === 'builtin' ? 'default' : 'outline'}>
                                {script.type === 'builtin' ? 'Built-in' : 'Custom'}
                              </Badge>
                            </div>
                            {script.type === 'builtin' && script.builtin?.name && (
                              <div className="flex items-center gap-2">
                                <span className="text-sm text-muted-foreground">Script Name:</span>
                                <code className="text-sm bg-muted px-2 py-1 rounded">
                                  {script.builtin.name}
                                </code>
                              </div>
                            )}
                            {script.type === 'custom' && script.custom?.configMapRef && (
                              <>
                                <div className="flex items-center gap-2">
                                  <span className="text-sm text-muted-foreground">ConfigMap:</span>
                                  <code className="text-sm bg-muted px-2 py-1 rounded">
                                    {script.custom.configMapRef.name}
                                  </code>
                                </div>
                                <div className="flex items-center gap-2">
                                  <span className="text-sm text-muted-foreground">Key:</span>
                                  <code className="text-sm bg-muted px-2 py-1 rounded">
                                    {script.custom.configMapRef.key}
                                  </code>
                                </div>
                              </>
                            )}
                            {script.type === 'custom' && script.custom?.parameters &&
                              script.custom.parameters.length > 0 && (
                              <div className="mt-2">
                                <span className="text-sm text-muted-foreground">Parameters:</span>
                                <div className="mt-1 flex flex-wrap gap-1">
                                  {script.custom.parameters.map((param) => (
                                    <Badge key={param.key} variant="outline" className="text-xs">
                                      {param.key}={param.value}
                                    </Badge>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        </div>
                      )}

                      {/* Status Details */}
                      {analyzer.status && (
                        <div>
                          <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
                            <Settings className="h-4 w-4 text-muted-foreground" />
                            Status Details
                          </h4>
                          <div className="border rounded-lg p-4 bg-muted/30 space-y-2 text-sm">
                            <div className="grid grid-cols-2 gap-4">
                              <div>
                                <span className="text-muted-foreground">Created:</span>
                                <span className="ml-2">{formatAge(analyzer.metadata.creationTimestamp)}</span>
                              </div>
                              {analyzer.status.lastRunTime && (
                                <div>
                                  <span className="text-muted-foreground">Last Run:</span>
                                  <span className="ml-2">{formatAge(analyzer.status.lastRunTime)}</span>
                                </div>
                              )}
                              {analyzer.status.nextRunTime && (
                                <div>
                                  <span className="text-muted-foreground">Next Run:</span>
                                  <span className="ml-2">{new Date(analyzer.status.nextRunTime).toLocaleString()}</span>
                                </div>
                              )}
                            </div>
                            {analyzer.status.conditions && analyzer.status.conditions.length > 0 && (
                              <div className="mt-3 pt-3 border-t">
                                <span className="text-muted-foreground block mb-2">Conditions:</span>
                                <div className="space-y-1">
                                  {analyzer.status.conditions.map((condition, idx) => (
                                    <div key={idx} className="flex items-center gap-2">
                                      {condition.status === 'True' ? (
                                        <CheckCircle2 className="h-3 w-3 text-success" />
                                      ) : (
                                        <XCircle className="h-3 w-3 text-destructive" />
                                      )}
                                      <span className="font-medium">{condition.type}</span>
                                      {condition.reason && (
                                        <span className="text-muted-foreground">({condition.reason})</span>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </AccordionContent>
                  </AccordionItem>
                </Accordion>
        </CardContent>
      )}
    </Card>
  );
}
