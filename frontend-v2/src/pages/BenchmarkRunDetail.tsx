/**
 * BenchmarkRunDetail — D-020: single run deep-dive. Leads with what happened (model server,
 * router, workload, cache hits, per-pod routing, TTFT split); the full aiperf output sits in a
 * collapsed Details section.
 * Includes MetricCard, AiperfMetricBarChart, LatencyPercentilesChart,
 * PhaseBreakdownTable, and TimelineChart sub-components.
 *
 * Chrome (panels, headers, axis labels) uses tokens. Recharts series colors
 * (Bar fill / Scatter fill / per-percentile palette / per-model routing
 * palette) are preserved per ADR D-020 §3 / resolved decisions §6.
 */
import { cn } from '@/lib/utils';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  Cell,
} from 'recharts';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { SectionCard } from '@/components/ui/section-card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { EmptyState } from '@/components/ui/empty-state';
import { Zap, Clock, Activity, Trophy, Star, SearchX, AlertTriangle, ChevronRight, Database } from 'lucide-react';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  useBenchmarkRun,
  useBenchmarkWebSocket,
  useSetBenchmarkRunBaseline,
  useUnsetBenchmarkRunBaseline,
} from '@/hooks/useBenchmarks';
import {
  PROXY_COLORS,
  StatusBadge,
  ProxyBadge,
  RegressionBadge,
  fmtDuration,
  fmtLatency,
  fmtNum,
} from './benchmark-utils';
import type { LatencyStats, ModelServerStats, PhaseResult, TimelineEvent } from '@/types';
import { RoutingTable } from './benchmark-routing';
import { cacheSentence, modelServerLabel, routerLabel, workloadSummary } from './benchmark-labels';

// Chart chrome tokens — shared across sub-components.
const CHART_GRID = 'hsl(var(--border))';
const CHART_TEXT = 'hsl(var(--muted-foreground))';
const CHART_TOOLTIP = {
  backgroundColor: 'hsl(var(--card))',
  border: '1px solid hsl(var(--border))',
  borderRadius: 8,
  fontSize: 12,
  color: 'hsl(var(--foreground))',
};

// ============================================================================
// Main Component
// ============================================================================

export function BenchmarkRunDetail({ runId, onBack }: { runId: number; onBack?: () => void }) {
  const { data: run, isLoading, isError } = useBenchmarkRun(runId, true);
  const { isConnected } = useBenchmarkWebSocket(run?.status === 'running' ? runId : undefined);
  const setBaseline = useSetBenchmarkRunBaseline();
  const unsetBaseline = useUnsetBenchmarkRunBaseline();

  if (isError) {
    return (
      <SectionCard>
        <EmptyState
          icon={SearchX}
          title="Run not found"
          description={`Benchmark run #${runId} no longer exists — it may have been deleted.`}
          action={onBack ? { label: 'Back to runs', onClick: onBack, variant: 'outline' } : undefined}
        />
      </SectionCard>
    );
  }

  if (isLoading || !run) {
    return <div className="space-y-4"><Skeleton className="h-24 w-full" /><Skeleton className="h-48 w-full" /></div>;
  }

  const result = run.result_json as Record<string, unknown> | null;
  const latency = result?.latency as LatencyStats | undefined;
  const throughput = result?.throughput as Record<string, number> | undefined;
  const phases = result?.phases as Record<string, PhaseResult> | undefined;
  const timeline = result?.timeline as TimelineEvent[] | undefined;
  const aiperfMetrics = result?.aiperf_metrics as Record<string, Record<string, number>> | undefined;

  // Extract aiperf sub-metrics (all have {unit, avg, p1..p99, min, max, std})
  const ttft = aiperfMetrics?.ttft;
  const itl = aiperfMetrics?.itl;
  const tst = aiperfMetrics?.time_to_second_token as Record<string, number> | undefined;
  const osl = aiperfMetrics?.osl;
  const isl = aiperfMetrics?.isl;
  const perUserThroughput = aiperfMetrics?.output_token_throughput_per_user;
  // Service level (aiperf 0.13+ with --goodput): share of requests meeting every target.
  const goodFraction = aiperfMetrics?.good_request_fraction?.avg;
  const goodput = aiperfMetrics?.goodput?.avg;
  const oslMismatch = aiperfMetrics?.osl_mismatch_count?.avg;
  const snapshot = (run.config_snapshot ?? {}) as Record<string, unknown>;
  const requestedOsl = Number(snapshot.output_tokens_mean ?? snapshot.osl) || null;
  const actualOsl = osl?.avg ?? null;
  // Far fewer tokens than asked: latency measures the proxy path, not generation.
  const shortOutput = requestedOsl != null && actualOsl != null && actualOsl < requestedOsl * 0.5;
  const errorRate = run.total_requests ? ((run.failed_requests ?? 0) / run.total_requests) * 100 : null;
  const served = result?.model_server as ModelServerStats | undefined;
  const serverLabel = modelServerLabel(run.tags);
  const workload = workloadSummary(run.config_snapshot as Record<string, unknown> | null);

  return (
    <div className="space-y-6">
      {/* Run Header */}
      <SectionCard>
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3">
              <ProxyBadge proxy={run.proxy} />
              <h2 className="text-xl font-semibold text-foreground">
                {run.run_label || `${run.proxy} → ${run.model}`}
              </h2>
            </div>
            <div className="mt-2 space-y-1 text-sm text-muted-foreground">
              <p>
                Model server: <span className="font-medium text-foreground/80">{serverLabel ?? run.model}</span>
                <span className="mx-2">·</span>
                Router: <span className="font-medium text-foreground/80">{routerLabel(run.proxy, run.tags)}</span>
              </p>
              {workload && <p>Workload: <span className="font-medium text-foreground/80">{workload}</span></p>}
              <p className="text-xs">
                Model {run.model} · {run.tool} · <span className="font-mono">{run.base_url}</span>
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <StatusBadge status={run.status} />
            <RegressionBadge run={run} />
            {run.status === 'running' && isConnected && (
              <Badge variant="success" className="gap-1">
                <div className="h-2 w-2 rounded-full bg-success animate-pulse" />Live
              </Badge>
            )}
            {run.status === 'completed' && (
              <Button
                size="sm"
                variant="outline"
                className={cn('gap-1.5', run.is_baseline && 'text-warning border-warning')}
                onClick={() =>
                  run.is_baseline ? unsetBaseline.mutate(run.id) : setBaseline.mutate(run.id)
                }
              >
                <Star className={cn('h-3.5 w-3.5', run.is_baseline && 'fill-current')} />
                {run.is_baseline ? 'Baseline' : 'Mark as baseline'}
              </Button>
            )}
          </div>
        </div>

        {/* Result — the numbers that tell how the proxy did */}
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4 mt-5 pt-5 border-t border-border">
          <MetricCard icon={Database} label="Prefix-cache hit" value={served?.totals.hit_pct != null ? `${served.totals.hit_pct.toFixed(0)}%` : '—'} tone="primary" />
          <MetricCard icon={Clock} label="TTFT avg" value={ttft?.avg != null ? `${ttft.avg.toFixed(0)} ms` : '—'} tone="info" />
          <MetricCard icon={Clock} label="TTFT p50 / p90 / p99" value={ttft?.p50 != null ? `${ttft.p50.toFixed(0)} / ${(ttft.p90 ?? ttft.p50).toFixed(0)} / ${(ttft.p99 ?? ttft.p50).toFixed(0)} ms` : '—'} tone="info" />
          <MetricCard icon={Clock} label="Inter-token p50" value={itl?.p50 != null ? `${itl.p50.toFixed(1)} ms` : itl?.avg != null ? `${itl.avg.toFixed(1)} ms` : '—'} tone="info" />
          <MetricCard icon={Trophy} label="Errors" value={errorRate != null ? `${errorRate.toFixed(1)}%` : '—'} tone={errorRate ? 'destructive' : 'success'} />
          <MetricCard icon={Activity} label="Output tok/s" value={fmtNum(run.tokens_per_sec)} tone="warning" />
          <MetricCard icon={Activity} label="Requests" value={run.total_requests != null ? `${run.total_requests.toLocaleString()} in ${fmtDuration(run.duration_seconds)}` : '—'} tone="muted" />
        </div>
        {cacheSentence(served) && <p className="mt-3 text-sm text-muted-foreground">{cacheSentence(served)}</p>}
      </SectionCard>

      {/* Routing — what the router did, from the model-server pods */}
      {run.status === 'completed' && (
        <SectionCard title="Routing — what the router did" compact>
          {served ? (
            <RoutingTable stats={served} />
          ) : (
            <p className="text-sm text-muted-foreground">
              No per-pod data for this run. Forge reads it from the model-server pods’ /metrics when a run starts and
              ends; runs pushed from outside Forge, or started before this was recorded, don’t have it.
            </p>
          )}
        </SectionCard>
      )}

      {/* Latency — TTFT by percentile: cache hits are the fast band, full prefill the slow one */}
      {ttft && <TtftPercentileChart ttft={ttft} proxy={run.proxy} withCache={served?.totals.hit_pct != null} />}

      {shortOutput && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            The model returned about {actualOsl!.toFixed(0)} output tokens per request against {requestedOsl} requested.
            Latency and token throughput here measure proxy overhead, not real generation.
          </AlertDescription>
        </Alert>
      )}

      {/* Details — the full aiperf output, collapsed */}
      {result && (
      <Collapsible className="space-y-4">
        <CollapsibleTrigger className="group flex w-full items-center gap-2 rounded-lg border border-border px-4 py-2.5 text-sm font-medium text-muted-foreground hover:bg-muted/40">
          <ChevronRight className="h-4 w-4 transition-transform group-data-[state=open]:rotate-90" />
          Details — service level, latency, throughput and the full aiperf metrics
        </CollapsibleTrigger>
        <CollapsibleContent className="space-y-6">
      {aiperfMetrics && (
        <SectionCard title="Service level" compact>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 text-sm">
            <ServiceStat label="Requests meeting targets" value={goodFraction != null ? `${(goodFraction * 100).toFixed(1)}%` : 'no targets set'} />
            <ServiceStat label="Goodput" value={goodput != null ? `${fmtNum(goodput)} req/s` : '—'} />
            <ServiceStat label="Errors" value={errorRate != null ? `${errorRate.toFixed(1)}%` : '—'} />
            <ServiceStat label="Output tokens (asked / got)" value={`${requestedOsl ?? '—'} / ${actualOsl != null ? actualOsl.toFixed(0) : '—'}`} />
            <ServiceStat label="Short outputs" value={oslMismatch != null ? `${oslMismatch.toFixed(0)} requests` : '—'} />
            <ServiceStat label="Per-user speed p50" value={perUserThroughput?.p50 != null ? `${perUserThroughput.p50.toFixed(1)} tok/s` : '—'} />
          </div>
          {goodFraction == null && (
            <p className="text-xs text-muted-foreground pt-3">Set TTFT / inter-token targets under aiperf settings (Service-level targets) to measure goodput.</p>
          )}
        </SectionCard>
      )}

      {/* Smart Routing Breakdown — shows how each requested model was routed by the proxy.
          F5 BNK with the LLM iRule will distribute weighted across multiple backends;
          HAProxy/Envoy return 100% of what the client requested. */}
      <SmartRoutingPanel run={run} />

      {/* Latency Percentiles Chart */}
      {latency && <LatencyPercentilesChart latency={latency} proxy={run.proxy} />}

      {/* aiperf Detailed Metrics — TTFT, ITL, TST, OSL, ISL, Per-User Throughput */}
      {aiperfMetrics && (
        <SectionCard title="Detailed metrics (aiperf)" compact>
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="text-left text-muted-foreground border-b border-border">
                  <th className="pb-2 pr-4 font-medium">Metric</th>
                  <th className="pb-2 pr-3 text-right font-medium">avg</th>
                  <th className="pb-2 pr-3 text-right font-medium">min</th>
                  <th className="pb-2 pr-3 text-right font-medium">p25</th>
                  <th className="pb-2 pr-3 text-right font-medium">p50</th>
                  <th className="pb-2 pr-3 text-right font-medium">p75</th>
                  <th className="pb-2 pr-3 text-right font-medium">p90</th>
                  <th className="pb-2 pr-3 text-right font-medium">p95</th>
                  <th className="pb-2 pr-3 text-right font-medium">p99</th>
                  <th className="pb-2 pr-3 text-right font-medium">max</th>
                  <th className="pb-2 text-right font-medium">std</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { name: 'Time to First Token (ms)', data: ttft },
                  { name: 'Time to Second Token (ms)', data: tst },
                  { name: 'Inter Token Latency (ms)', data: itl },
                  { name: 'Request Latency (ms)', data: latency ? { avg: (latency.avg ?? 0) * 1000, min: (latency.min ?? 0) * 1000, p25: latency.p25 != null ? latency.p25 * 1000 : undefined, p50: (latency.p50 ?? 0) * 1000, p75: latency.p75 != null ? latency.p75 * 1000 : undefined, p90: latency.p90 != null ? latency.p90 * 1000 : undefined, p95: latency.p95 != null ? latency.p95 * 1000 : undefined, p99: (latency.p99 ?? 0) * 1000, max: (latency.max ?? 0) * 1000 } : undefined },
                  { name: 'Per-User Throughput (tok/s/user)', data: perUserThroughput },
                  { name: 'Output Sequence Length (tokens)', data: osl },
                  { name: 'Input Sequence Length (tokens)', data: isl },
                ].filter(row => row.data).map(row => (
                  <tr key={row.name} className="border-b border-border hover:bg-muted/40">
                    <td className="py-2 pr-4 font-medium text-foreground">{row.name}</td>
                    {['avg', 'min', 'p25', 'p50', 'p75', 'p90', 'p95', 'p99', 'max', 'std'].map(key => (
                      <td key={key} className="py-2 pr-3 text-right tabular-nums text-foreground/80">
                        {(row.data as Record<string, number | undefined>)?.[key] != null
                          ? Number((row.data as Record<string, number>)[key]).toLocaleString(undefined, { maximumFractionDigits: 2 })
                          : '—'}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      )}

      {/* aiperf Metric Charts — visual percentile distributions */}
      {aiperfMetrics && (
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
          {ttft && <AiperfMetricBarChart data={ttft} title="Time to First Token (TTFT)" unit="ms" color="#06b6d4" />}
          {itl && <AiperfMetricBarChart data={itl} title="Inter Token Latency (ITL)" unit="ms" color="#14b8a6" />}
          {tst && <AiperfMetricBarChart data={tst} title="Time to Second Token (TST)" unit="ms" color="#8b5cf6" />}
          {osl && <AiperfMetricBarChart data={osl} title="Output Sequence Length (OSL)" unit=" tok" color="#f59e0b" />}
          {isl && <AiperfMetricBarChart data={isl} title="Input Sequence Length (ISL)" unit=" tok" color="#3b82f6" />}
          {perUserThroughput && <AiperfMetricBarChart data={perUserThroughput} title="Per-User Throughput" unit=" tok/s" color="#10b981" />}
        </div>
      )}

      {/* Per-Phase Breakdown — only when a tool reports more than one phase */}
      {phases && Object.keys(phases).length > 1 && <PhaseBreakdownTable phases={phases} />}

      {/* Timeline Scatter (if available and not too large) */}
      {timeline && timeline.length > 0 && timeline.length <= 50000 && (
        <TimelineChart timeline={timeline} proxy={run.proxy} />
      )}

      {/* Throughput + Token stats */}
      {(throughput || result) && (
        <SectionCard title="Throughput & tokens" compact>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4 text-sm">
            {throughput && <>
              <div><span className="text-muted-foreground">Overall RPS</span><p className="font-mono font-medium text-foreground">{fmtNum(throughput.overall_rps)}</p></div>
              {/* aiperf exports an average only; older rows stored that average as the peak. */}
              {throughput.peak_rps != null && throughput.peak_rps !== throughput.overall_rps && <div><span className="text-muted-foreground">Peak RPS</span><p className="font-mono font-medium text-foreground">{fmtNum(throughput.peak_rps)}</p></div>}
              {throughput.p50_rps != null && <div><span className="text-muted-foreground">P50 RPS</span><p className="font-mono font-medium text-foreground">{fmtNum(throughput.p50_rps)}</p></div>}
              <div><span className="text-muted-foreground">Output Tokens/sec</span><p className="font-mono font-medium text-foreground">{fmtNum(throughput.gen_tokens_per_sec)}</p></div>
            </>}
            {result && <>
              <div><span className="text-muted-foreground">Total Requests</span><p className="font-mono font-medium text-foreground">{(result.total_requests as number)?.toLocaleString() ?? '—'}</p></div>
              <div><span className="text-muted-foreground">Total Input Tokens</span><p className="font-mono font-medium text-foreground">{(result.total_input_tokens as number)?.toLocaleString() ?? '—'}</p></div>
              <div><span className="text-muted-foreground">Total Output Tokens</span><p className="font-mono font-medium text-foreground">{(result.total_output_tokens as number)?.toLocaleString() ?? '—'}</p></div>
              <div><span className="text-muted-foreground">Avg Input Tokens</span><p className="font-mono font-medium text-foreground">{fmtNum(result.avg_input_tokens as number)}</p></div>
              <div><span className="text-muted-foreground">Avg Output Tokens</span><p className="font-mono font-medium text-foreground">{fmtNum(result.avg_output_tokens as number)}</p></div>
            </>}
          </div>
        </SectionCard>
      )}
        </CollapsibleContent>
      </Collapsible>
      )}
    </div>
  );
}

// ============================================================================
// Sub-Components
// ============================================================================

type MetricTone = 'info' | 'destructive' | 'primary' | 'warning' | 'success' | 'muted';

function ServiceStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-muted-foreground">{label}</span>
      <p className="font-mono font-medium text-foreground">{value}</p>
    </div>
  );
}

function MetricCard({ icon: Icon, label, value, tone }: { icon: typeof Zap; label: string; value: string; tone: MetricTone }) {
  const toneClass: Record<MetricTone, string> = {
    info: 'text-info',
    destructive: 'text-destructive',
    primary: 'text-primary',
    warning: 'text-warning',
    success: 'text-success',
    muted: 'text-muted-foreground',
  };
  return (
    <div>
      <div className="flex items-center gap-1.5 mb-1">
        <Icon className={cn('h-3.5 w-3.5', toneClass[tone])} />
        <span className="text-xs text-muted-foreground">{label}</span>
      </div>
      <span className="text-lg font-semibold font-mono text-foreground">{value}</span>
    </div>
  );
}

/**
 * TTFT at each percentile. With prefix caching the distribution splits in two: requests
 * whose prompt prefix was cached return fast, the rest pay a full prefill. Bars below the
 * midpoint of the range are drawn as the fast band.
 */
function TtftPercentileChart({ ttft, proxy, withCache }: { ttft: Record<string, number>; proxy: string; withCache: boolean }) {
  const keys = ['p1', 'p5', 'p10', 'p25', 'p50', 'p75', 'p90', 'p95', 'p99'];
  const data = keys.filter(k => ttft[k] != null).map(k => ({ name: k.toUpperCase(), value: ttft[k] }));
  if (data.length === 0) return null;
  const values = data.map(d => d.value);
  const mid = (Math.min(...values) + Math.max(...values)) / 2;
  const slow = PROXY_COLORS[proxy] || '#3b82f6';
  const fast = '#10b981';
  return (
    <SectionCard title="Time to first token by percentile (ms)" compact>
      {withCache && (
        <p className="mb-3 text-xs text-muted-foreground">
          <span className="font-medium" style={{ color: fast }}>■ fast</span> — the prompt prefix was already in a pod’s KV cache ·{' '}
          <span className="font-medium" style={{ color: slow }}>■ slow</span> — full prefill of the prompt
        </p>
      )}
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
          <XAxis dataKey="name" tick={{ fill: CHART_TEXT, fontSize: 11 }} />
          <YAxis tick={{ fill: CHART_TEXT, fontSize: 11 }} />
          <RechartsTooltip contentStyle={CHART_TOOLTIP} formatter={(v) => [`${Number(v).toFixed(0)} ms`, 'TTFT']} />
          <Bar dataKey="value" radius={[4, 4, 0, 0]}>
            {data.map(d => <Cell key={d.name} fill={withCache && d.value < mid ? fast : slow} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </SectionCard>
  );
}

/** Reusable percentile bar chart for any aiperf metric (TTFT, ITL, TST, OSL, ISL, etc.) */
function AiperfMetricBarChart({ data, title, unit, color }: {
  data: Record<string, number>;
  title: string;
  unit: string;
  color: string;
}) {
  const PERCENTILE_KEYS = ['min', 'p25', 'p50', 'p75', 'p90', 'p95', 'p99', 'max'];
  const chartData = PERCENTILE_KEYS
    .filter(k => data[k] != null)
    .map(k => ({ name: k.toUpperCase(), value: data[k] }));

  if (chartData.length === 0) return null;

  return (
    <SectionCard compact>
      <div className="flex items-center justify-between mb-4">
        <h4 className="text-sm font-semibold text-foreground">{title}</h4>
        {data.avg != null && (
          <span className="text-xs text-muted-foreground">avg: <span className="font-mono font-medium" style={{ color }}>{data.avg.toFixed(1)}{unit}</span></span>
        )}
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
          <XAxis dataKey="name" tick={{ fill: CHART_TEXT, fontSize: 10 }} />
          <YAxis tick={{ fill: CHART_TEXT, fontSize: 10 }} />
          <RechartsTooltip
            contentStyle={CHART_TOOLTIP}
            formatter={(v) => [`${Number(v).toFixed(2)}${unit}`, title.split(' ')[0]]}
          />
          <Bar dataKey="value" fill={color} radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </SectionCard>
  );
}

function LatencyPercentilesChart({ latency, proxy }: { latency: LatencyStats; proxy: string }) {
  const fill = PROXY_COLORS[proxy] || '#3b82f6';

  const data = [
    { name: 'Min', value: (latency.min ?? 0) * 1000 },
    ...(latency.p25 != null ? [{ name: 'P25', value: latency.p25 * 1000 }] : []),
    { name: 'P50', value: (latency.p50 ?? 0) * 1000 },
    ...(latency.p75 != null ? [{ name: 'P75', value: latency.p75 * 1000 }] : []),
    ...(latency.p90 != null ? [{ name: 'P90', value: latency.p90 * 1000 }] : []),
    ...(latency.p95 != null ? [{ name: 'P95', value: latency.p95 * 1000 }] : []),
    { name: 'P99', value: (latency.p99 ?? 0) * 1000 },
    { name: 'Max', value: (latency.max ?? 0) * 1000 },
  ];

  return (
    <SectionCard title="Latency distribution (ms)" compact>
      <ResponsiveContainer width="100%" height={250}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
          <XAxis dataKey="name" tick={{ fill: CHART_TEXT, fontSize: 11 }} />
          <YAxis tick={{ fill: CHART_TEXT, fontSize: 11 }} />
          <RechartsTooltip
            contentStyle={CHART_TOOLTIP}
            formatter={(v) => [`${Number(v).toFixed(1)}ms`, 'Latency']}
          />
          <Bar dataKey="value" fill={fill} radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </SectionCard>
  );
}

function PhaseBreakdownTable({ phases }: { phases: Record<string, PhaseResult> }) {
  const phaseList = Object.entries(phases).map(([key, phase]) => ({
    ...phase,
    name: phase.name ?? key,
    success: phase.success ?? phase.successful ?? 0,
  }));

  return (
    <SectionCard title="Per-phase breakdown" compact>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Phase</TableHead>
              <TableHead className="text-right">Requests</TableHead>
              <TableHead className="text-right">Success</TableHead>
              <TableHead className="text-right">Failed</TableHead>
              <TableHead className="text-right">Duration</TableHead>
              <TableHead className="text-right">P50</TableHead>
              <TableHead className="text-right">P99</TableHead>
              <TableHead className="text-right">RPS</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {phaseList.map(phase => (
              <TableRow key={phase.name}>
                <TableCell className="font-medium">{phase.name}</TableCell>
                <TableCell className="text-right font-mono">{(phase.requests ?? 0).toLocaleString()}</TableCell>
                <TableCell className="text-right font-mono text-success">{(phase.success ?? 0).toLocaleString()}</TableCell>
                <TableCell className="text-right font-mono text-destructive">{(phase.failed ?? 0) > 0 ? phase.failed.toLocaleString() : '—'}</TableCell>
                <TableCell className="text-right font-mono">{fmtDuration(phase.duration_seconds)}</TableCell>
                <TableCell className="text-right font-mono">{fmtLatency(phase.latency?.p50)}</TableCell>
                <TableCell className="text-right font-mono">{fmtLatency(phase.latency?.p99)}</TableCell>
                <TableCell className="text-right font-mono">{phase.rps != null ? fmtNum(phase.rps) : '—'}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </SectionCard>
  );
}

function TimelineChart({ timeline, proxy }: { timeline: TimelineEvent[]; proxy: string }) {
  // Sample if too large
  const sampled = timeline.length > 5000
    ? timeline.filter((_, i) => i % Math.ceil(timeline.length / 5000) === 0)
    : timeline;

  const data = sampled.map(e => ({
    t: e.t,
    latency: (e.latency ?? 0) * 1000,
  }));

  return (
    <SectionCard title={`Timeline — latency over time (${timeline.length.toLocaleString()} events)`} compact>
      <ResponsiveContainer width="100%" height={250}>
        <ScatterChart margin={{ left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
          <XAxis dataKey="t" name="Time" unit="s" tick={{ fill: CHART_TEXT, fontSize: 10 }} />
          <YAxis dataKey="latency" name="Latency" unit="ms" tick={{ fill: CHART_TEXT, fontSize: 10 }} />
          <RechartsTooltip
            contentStyle={CHART_TOOLTIP}
            formatter={(v, name) => [name === 'latency' ? `${Number(v).toFixed(1)}ms` : `${Number(v).toFixed(1)}s`, name === 'latency' ? 'Latency' : 'Time']}
          />
          <Scatter data={data} fill={PROXY_COLORS[proxy] || '#3b82f6'} opacity={0.4} />
        </ScatterChart>
      </ResponsiveContainer>
    </SectionCard>
  );
}

// ============================================================================
// SmartRoutingPanel — visualizes which backend models actually served the requests.
// Renders only when run.tags.routing_breakdown is present (post-processed from
// aiperf's profile.json after a run completes).
// ============================================================================
interface RoutingBreakdown {
  requested_model: string;
  total_responses: number;
  by_model: Record<string, number>;
}

function SmartRoutingPanel({ run }: { run: { tags: Record<string, unknown> | null; proxy: string } }) {
  const tags = run.tags as Record<string, unknown> | null;
  const breakdown = tags?.routing_breakdown as RoutingBreakdown | undefined;
  if (!breakdown || !breakdown.by_model || Object.keys(breakdown.by_model).length === 0) {
    return null;
  }

  const total = breakdown.total_responses || Object.values(breakdown.by_model).reduce((a, b) => a + b, 0);
  const entries = Object.entries(breakdown.by_model).sort(([, a], [, b]) => b - a);
  const distinct = entries.length;
  const isSmart = distinct > 1;
  // Per-model series palette — preserved per ADR D-020 §6 (chart series allowlisted).
  const palette = ['#3b82f6', '#a855f7', '#10b981', '#f59e0b', '#ec4899', '#06b6d4'];

  return (
    <SectionCard compact>
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-sm font-semibold text-foreground">
          Smart Routing Breakdown
        </h3>
        <Badge variant={isSmart ? 'info' : 'muted'} className="text-[10px]">
          {isSmart ? 'smart-routed' : 'passthrough'}
        </Badge>
      </div>
      <div className="text-xs text-muted-foreground mb-3">
        Client requested <span className="font-mono text-foreground/80">{breakdown.requested_model}</span>;
        {isSmart
          ? <> proxy distributed across {distinct} backend models ({total} responses total).</>
          : <> proxy forwarded all {total} responses to the requested backend.</>}
      </div>
      {/* Stacked bar */}
      <div className="flex h-7 w-full overflow-hidden rounded-md border border-border" role="img" aria-label="Routing distribution">
        {entries.map(([model, count], i) => {
          const pct = (count / total) * 100;
          const color = palette[i % palette.length];
          return (
            <div
              key={model}
              className="flex items-center justify-center text-[10px] font-mono text-white"
              style={{ width: `${pct}%`, backgroundColor: color, minWidth: pct >= 5 ? '40px' : '0' }}
              title={`${model}: ${count}/${total} (${pct.toFixed(1)}%)`}
            >
              {pct >= 8 ? `${pct.toFixed(0)}%` : ''}
            </div>
          );
        })}
      </div>
      {/* Legend */}
      <div className="mt-3 flex flex-wrap gap-3 text-xs">
        {entries.map(([model, count], i) => {
          const pct = (count / total) * 100;
          const color = palette[i % palette.length];
          return (
            <div key={model} className="flex items-center gap-2">
              <div className="h-3 w-3 rounded" style={{ backgroundColor: color }} />
              <span className="font-mono text-foreground/80">{model}</span>
              <span className="text-muted-foreground tabular-nums">{count} ({pct.toFixed(1)}%)</span>
            </div>
          );
        })}
      </div>
    </SectionCard>
  );
}
