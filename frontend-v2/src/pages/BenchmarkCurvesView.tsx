/**
 * BenchmarkCurvesView — load curves: how each proxy behaves as load rises.
 *
 * Industry practice (SemiAnalysis InferenceMAX, MLPerf Server, DistServe goodput)
 * compares curves across a load sweep, judged on tail latency, not single-point
 * averages. Each selected run-group (a scenario sweep) is one line; points line
 * up by variant label, so sweeps of the same scenario compare point for point.
 * Recharts strokes follow the PROXY_COLORS palette used across Benchmarks.
 */
import { useMemo, useState } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  Legend,
  ReferenceLine,
} from 'recharts';
import { AlertTriangle, LineChart as LineChartIcon } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Checkbox } from '@/components/ui/checkbox';
import { EmptyState } from '@/components/ui/empty-state';
import { Label } from '@/components/ui/label';
import { SectionCard } from '@/components/ui/section-card';
import { Skeleton } from '@/components/ui/skeleton';
import { TimeAgo } from '@/components/ui/TimeAgo';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useRunGroupCurves, useRunGroups } from '@/hooks/useBenchmarks';
import { PROXY_COLORS, PROXY_LABELS, ProxyBadge, fmtNum } from './benchmark-utils';
import type { CurveGroup, CurvePoint } from '@/types';

const CHART_GRID = 'hsl(var(--border))';
const CHART_TEXT = 'hsl(var(--muted-foreground))';
const CHART_TOOLTIP = {
  backgroundColor: 'hsl(var(--card))',
  border: '1px solid hsl(var(--border))',
  borderRadius: 8,
  fontSize: 12,
  color: 'hsl(var(--foreground))',
};
// Fallback strokes when two sweeps share a proxy (or the proxy has no colour).
const EXTRA_COLORS = ['#8b5cf6', '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#64748b'];

// MLPerf Server-style default target: a load point "holds" when p99 TTFT and
// p99 inter-token latency meet the sweep's goodput targets (2 s / 200 ms unless
// the sweep set others) and fewer than 1% of requests fail.
const SLO_TTFT_P99_MS = 2000;
const SLO_ITL_P99_MS = 200;
const SLO_ERROR_PCT = 1;

interface Slo { ttft: number; itl: number }

function sloOf(g: CurveGroup): Slo {
  return {
    ttft: g.goodput_targets?.time_to_first_token ?? SLO_TTFT_P99_MS,
    itl: g.goodput_targets?.inter_token_latency ?? SLO_ITL_P99_MS,
  };
}

function isOpenLoop(g: CurveGroup): boolean {
  return g.load_axis === 'request_rate';
}

function groupName(g: CurveGroup): string {
  return `${PROXY_LABELS[g.proxy ?? ''] ?? g.proxy ?? 'sweep'} #${g.id}`;
}

function holds(p: CurvePoint, slo: Slo): boolean {
  const ttft = p.ttft_p99 ?? (p.latency_p99 != null ? p.latency_p99 * 1000 : null);
  return ttft != null && ttft <= slo.ttft
    && (p.itl_p99 == null || p.itl_p99 <= slo.itl)
    && (p.error_rate_pct ?? 0) < SLO_ERROR_PCT;
}

/** The load point meeting the target at the most load: the highest offered
 * rate for open-loop sweeps (MLPerf "max QPS"), else the highest throughput. */
function bestHeldPoint(g: CurveGroup): CurvePoint | undefined {
  const load = (p: CurvePoint) => (isOpenLoop(g) ? p.request_rate : p.overall_rps) ?? 0;
  return g.points.filter((p) => holds(p, sloOf(g))).sort((a, b) => load(b) - load(a))[0];
}

type Metric = (p: CurvePoint) => number | null;

function CurveChart({ title, unit, groups, labels, metric, colors, reference }: {
  title: string;
  unit: string;
  groups: CurveGroup[];
  labels: string[];
  metric: Metric;
  colors: Map<number, string>;
  reference?: number;
}) {
  const data = labels.map((label) => {
    const row: Record<string, string | number | null> = { label };
    for (const g of groups) {
      const p = g.points.find((pt) => (pt.variant_label ?? `#${pt.run_id}`) === label);
      row[groupName(g)] = p ? metric(p) : null;
    }
    return row;
  });
  if (!data.some((row) => groups.some((g) => row[groupName(g)] != null))) return null;
  return (
    <SectionCard title={title} compact>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
          <XAxis dataKey="label" tick={{ fill: CHART_TEXT, fontSize: 10 }} />
          <YAxis tick={{ fill: CHART_TEXT, fontSize: 11 }} unit={unit} width={64} />
          <RechartsTooltip contentStyle={CHART_TOOLTIP} formatter={(v) => (typeof v === 'number' ? `${fmtNum(v, 2)}${unit}` : String(v ?? '—'))} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {reference != null && <ReferenceLine y={reference} stroke="#f59e0b" strokeDasharray="4 4" />}
          {groups.map((g) => (
            <Line
              key={g.id}
              type="monotone"
              dataKey={groupName(g)}
              stroke={colors.get(g.id)}
              strokeWidth={2}
              dot={{ r: 3 }}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </SectionCard>
  );
}

export function BenchmarkCurvesView({ groupIds, onGroupIdsChange }: {
  groupIds: number[];
  onGroupIdsChange: (ids: number[]) => void;
}) {
  const { data: list, isLoading: listLoading } = useRunGroups();
  const { data: curves, isLoading: curvesLoading } = useRunGroupCurves(groupIds);
  const [referenceId, setReferenceId] = useState<number | null>(null);

  const groups = useMemo(() => curves?.groups ?? [], [curves]);
  const labels = useMemo(() => {
    const seen: string[] = [];
    for (const g of groups) for (const p of g.points) {
      const label = p.variant_label ?? `#${p.run_id}`;
      if (!seen.includes(label)) seen.push(label);
    }
    return seen;
  }, [groups]);
  const colors = useMemo(() => {
    const used = new Set<string>();
    const map = new Map<number, string>();
    let extra = 0;
    for (const g of groups) {
      let c = PROXY_COLORS[g.proxy ?? ''];
      if (!c || used.has(c)) c = EXTRA_COLORS[extra++ % EXTRA_COLORS.length];
      used.add(c);
      map.set(g.id, c);
    }
    return map;
  }, [groups]);

  const reference = groups.find((g) => g.id === referenceId);
  const slo = groups.length ? sloOf(groups[0]) : { ttft: SLO_TTFT_P99_MS, itl: SLO_ITL_P99_MS };
  const openLoop = groups.some(isOpenLoop);
  // Output-length integrity: if the model returned far fewer tokens than asked,
  // latency reflects proxy overhead only, not real generation.
  const shortOutput = groups.flatMap((g) => g.points).find(
    (p) => p.requested_osl != null && p.osl_avg != null && p.osl_avg < p.requested_osl * 0.5,
  );

  const toggle = (id: number) =>
    onGroupIdsChange(groupIds.includes(id) ? groupIds.filter((g) => g !== id) : [...groupIds, id]);

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold text-foreground">Load curves</h3>
        <p className="text-sm text-muted-foreground">
          How each proxy behaves as load rises. Pick sweeps of the same scenario: one line per proxy, points matched by load step.
        </p>
      </div>

      <SectionCard title="Sweeps" compact>
        {listLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : !list?.groups.length ? (
          <EmptyState icon={LineChartIcon} title="No sweeps yet" description="Run a scenario against a proxy to get a load sweep." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>Scenario</TableHead>
                <TableHead>Proxy</TableHead>
                <TableHead>Label</TableHead>
                <TableHead className="text-right">Points</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.groups.map((g) => (
                <TableRow key={g.id} className="cursor-pointer" onClick={() => toggle(g.id)}>
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Checkbox checked={groupIds.includes(g.id)} onCheckedChange={() => toggle(g.id)} />
                  </TableCell>
                  <TableCell className="text-sm">{g.scenario_name ?? g.scenario_key}</TableCell>
                  <TableCell>{g.proxy ? <ProxyBadge proxy={g.proxy} /> : '—'}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{g.run_label ?? `#${g.id}`}</TableCell>
                  <TableCell className="text-right text-sm font-mono">{g.completed_runs}/{g.total_runs}</TableCell>
                  <TableCell className="text-sm text-muted-foreground"><TimeAgo dateStr={g.created_at} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </SectionCard>

      {groupIds.length > 0 && curvesLoading && <Skeleton className="h-64 w-full" />}

      {groups.length > 0 && (
        <>
          {curves?.mismatch_reasons.length ? (
            <Alert>
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                These sweeps are not like-for-like ({curves.mismatch_reasons.join(', ')}), so differences are not only the proxy.
              </AlertDescription>
            </Alert>
          ) : null}
          {shortOutput && (
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                The model returned about {fmtNum(shortOutput.osl_avg, 0)} output tokens per request against {fmtNum(shortOutput.requested_osl, 0)} requested.
                Latency and throughput here measure proxy overhead, not real generation. Use a model that honours max_tokens with ignore_eos.
              </AlertDescription>
            </Alert>
          )}

          <SectionCard title="Headline" compact>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Sweep</TableHead>
                  <TableHead className="text-right">{openLoop ? 'Max load within target' : 'Best throughput within target'}</TableHead>
                  <TableHead className="text-right">TTFT p99 there</TableHead>
                  <TableHead className="text-right">Meeting targets there</TableHead>
                  <TableHead className="text-right">Peak throughput</TableHead>
                  <TableHead className="text-right">Worst TTFT p99</TableHead>
                  <TableHead className="text-right">Worst errors</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {groups.map((g) => {
                  const best = bestHeldPoint(g);
                  const peak = Math.max(...g.points.map((p) => p.overall_rps ?? 0));
                  const worstTtft = Math.max(...g.points.map((p) => p.ttft_p99 ?? 0));
                  const worstErr = Math.max(...g.points.map((p) => p.error_rate_pct ?? 0));
                  return (
                    <TableRow key={g.id}>
                      <TableCell className="text-sm font-medium" style={{ color: colors.get(g.id) }}>{groupName(g)}</TableCell>
                      <TableCell className="text-right font-mono text-sm">
                        {!best ? 'none'
                          : isOpenLoop(g) && best.request_rate != null
                            ? `${fmtNum(best.request_rate)} req/s offered (${fmtNum(best.overall_rps)} achieved)`
                            : `${fmtNum(best.overall_rps)} req/s (${best.variant_label ?? `#${best.run_id}`})`}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm">{best?.ttft_p99 != null ? `${fmtNum(best.ttft_p99, 0)} ms` : '—'}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{best?.good_request_pct != null ? `${fmtNum(best.good_request_pct, 1)}%` : '—'}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{g.points.length ? `${fmtNum(peak)} req/s` : '—'}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{worstTtft ? `${fmtNum(worstTtft, 0)} ms` : '—'}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{g.points.length ? `${fmtNum(worstErr, 1)}%` : '—'}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
            <p className="text-xs text-muted-foreground pt-2">
              Target: TTFT p99 ≤ {fmtNum(slo.ttft, 0)} ms, inter-token p99 ≤ {fmtNum(slo.itl, 0)} ms and errors &lt; {SLO_ERROR_PCT}% (MLPerf Server-style), from each sweep&apos;s goodput settings. Dashed lines on the charts.
              {openLoop && ' Open-loop sweeps: requests arrive at the offered rate however fast the proxy answers, so overload shows as rising TTFT and requests in flight.'}
            </p>
          </SectionCard>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            <CurveChart title="Time to first token p99" unit=" ms" groups={groups} labels={labels} colors={colors}
              metric={(p) => p.ttft_p99} reference={slo.ttft} />
            <CurveChart title="Inter-token latency p99" unit=" ms" groups={groups} labels={labels} colors={colors}
              metric={(p) => p.itl_p99} reference={slo.itl} />
            <CurveChart title="Request latency p99" unit=" ms" groups={groups} labels={labels} colors={colors}
              metric={(p) => (p.latency_p99 != null ? p.latency_p99 * 1000 : null)} />
            <CurveChart title="Throughput" unit=" req/s" groups={groups} labels={labels} colors={colors}
              metric={(p) => p.overall_rps} />
            <CurveChart title="Per-user speed p50" unit=" tok/s" groups={groups} labels={labels} colors={colors}
              metric={(p) => p.per_user_throughput_p50} />
            <CurveChart title="Errors" unit="%" groups={groups} labels={labels} colors={colors}
              metric={(p) => p.error_rate_pct} />
            <CurveChart title="Requests meeting targets (goodput)" unit="%" groups={groups} labels={labels} colors={colors}
              metric={(p) => p.good_request_pct} />
            {openLoop && (
              <>
                <CurveChart title="Achieved vs offered rate" unit="%" groups={groups} labels={labels} colors={colors} reference={100}
                  metric={(p) => (p.request_rate && p.overall_rps != null ? (p.overall_rps / p.request_rate) * 100 : null)} />
                <CurveChart title="Requests in flight" unit="" groups={groups} labels={labels} colors={colors}
                  metric={(p) => p.effective_concurrency ?? null} />
              </>
            )}
          </div>

          {groups.length > 1 && (
            <SectionCard title="Overhead against a reference" compact>
              <div className="flex items-center gap-2 pb-3">
                <Label className="text-xs text-muted-foreground">Reference</Label>
                <Select value={referenceId != null ? String(referenceId) : undefined} onValueChange={(v) => setReferenceId(Number(v))}>
                  <SelectTrigger className="w-56 h-8"><SelectValue placeholder="Pick the baseline sweep (e.g. direct)" /></SelectTrigger>
                  <SelectContent>
                    {groups.map((g) => <SelectItem key={g.id} value={String(g.id)}>{groupName(g)}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              {reference ? (
                <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                  {([
                    ['Added TTFT p99', (p: CurvePoint) => p.ttft_p99],
                    ['Added request latency p99', (p: CurvePoint) => (p.latency_p99 != null ? p.latency_p99 * 1000 : null)],
                  ] as const).map(([title, metric]) => (
                    <CurveChart
                      key={title}
                      title={`${title} vs ${groupName(reference)}`}
                      unit=" ms"
                      groups={groups.filter((g) => g.id !== reference.id)}
                      labels={labels}
                      colors={colors}
                      reference={0}
                      metric={(p) => {
                        const ref = reference.points.find((r) => r.variant_label === p.variant_label);
                        const a = metric(p);
                        const b = ref ? metric(ref) : null;
                        return a != null && b != null ? a - b : null;
                      }}
                    />
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Pick the sweep to measure against; each other line shows the extra latency at the same load step.</p>
              )}
            </SectionCard>
          )}
        </>
      )}
    </div>
  );
}
