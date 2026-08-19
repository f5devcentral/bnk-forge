/**
 * BenchmarkTrendsView — time-series + baseline/regression tracking for a
 * target(+proxy/scenario) context. Reachable as a drill-in from the Runs tab.
 *
 * Chrome (panel surfaces, headers, axis label tints) uses tokens. Recharts series
 * fills/strokes follow the same allowlisted-palette convention as
 * BenchmarkCompareTab.tsx / BenchmarkRunDetail.tsx.
 */
import { useEffect, useMemo, useState } from 'react';
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
import { Skeleton } from '@/components/ui/skeleton';
import { SectionCard } from '@/components/ui/section-card';
import { EmptyState } from '@/components/ui/empty-state';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { LineChart as LineChartIcon } from 'lucide-react';
import { useDebounce } from '@/hooks/useDebounce';
import { useBenchmarkTargets, useBenchmarkTrends } from '@/hooks/useBenchmarks';
import type { BenchmarkTrendPoint } from '@/types';

const CHART_GRID = 'hsl(var(--border))';
const CHART_TEXT = 'hsl(var(--muted-foreground))';
const CHART_TOOLTIP = {
  backgroundColor: 'hsl(var(--card))',
  border: '1px solid hsl(var(--border))',
  borderRadius: 8,
  fontSize: 12,
  color: 'hsl(var(--foreground))',
};
const BASELINE_COLOR = '#f59e0b';

function fmtPointTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function BenchmarkTrendsView() {
  const { data: targetsData } = useBenchmarkTargets();
  const targets = useMemo(() => targetsData?.targets ?? [], [targetsData?.targets]);

  const [targetId, setTargetId] = useState<number | undefined>(undefined);
  const [proxy, setProxy] = useState('');
  const [scenarioKeyInput, setScenarioKeyInput] = useState('');
  const scenarioKey = useDebounce(scenarioKeyInput, 300);

  useEffect(() => {
    if (targetId === undefined && targets.length > 0) setTargetId(targets[0].id);
  }, [targets, targetId]);

  const { data, isLoading } = useBenchmarkTrends(
    targetId != null
      ? { target_id: targetId, proxy: proxy || undefined, scenario_key: scenarioKey || undefined }
      : undefined,
    targetId != null,
  );

  const points = data?.points ?? [];
  const baseline = data?.baseline_run_id != null
    ? points.find((p) => p.id === data.baseline_run_id)
    : points.find((p) => p.is_baseline);

  const chartData = points.map((p) => ({
    time: fmtPointTime(p.created_at),
    'P50 (ms)': p.latency_p50 != null ? p.latency_p50 * 1000 : null,
    'P99 (ms)': p.latency_p99 != null ? p.latency_p99 * 1000 : null,
    'RPS': p.overall_rps,
    'Success %': p.success_rate_pct,
    isBaseline: p.is_baseline,
    runLabel: p.run_label,
  }));

  const baselineP99Ms = baseline?.latency_p99 != null ? baseline.latency_p99 * 1000 : undefined;
  const baselineRps = baseline?.overall_rps ?? undefined;

  return (
    <div className="space-y-6">
      <h3 className="text-lg font-semibold text-foreground">Trends</h3>

      <SectionCard compact>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Target</Label>
            <Select
              value={targetId != null ? String(targetId) : undefined}
              onValueChange={(v) => setTargetId(Number(v))}
            >
              <SelectTrigger className="w-56"><SelectValue placeholder="Select a target" /></SelectTrigger>
              <SelectContent>
                {targets.map((t) => (
                  <SelectItem key={t.id} value={String(t.id)}>{t.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Proxy</Label>
            <Select value={proxy || 'all'} onValueChange={(v) => setProxy(v === 'all' ? '' : v)}>
              <SelectTrigger className="w-36"><SelectValue placeholder="All proxies" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All proxies</SelectItem>
                <SelectItem value="envoy">Envoy</SelectItem>
                <SelectItem value="nginx">Nginx</SelectItem>
                <SelectItem value="haproxy">HAProxy</SelectItem>
                <SelectItem value="f5-bnk">F5 BNK</SelectItem>
                <SelectItem value="nodeport">NodePort</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Scenario key</Label>
            <Input
              placeholder="e.g. prefix-cache"
              value={scenarioKeyInput}
              onChange={(e) => setScenarioKeyInput(e.target.value)}
              className="w-48"
            />
          </div>
        </div>
      </SectionCard>

      {targetId == null ? (
        <SectionCard>
          <EmptyState
            icon={LineChartIcon}
            title="No targets yet"
            description="Add a benchmark target on the Targets tab, then come back here to track trends over time."
          />
        </SectionCard>
      ) : isLoading ? (
        <SectionCard compact><Skeleton className="h-64 w-full" /></SectionCard>
      ) : points.length < 2 ? (
        <SectionCard>
          <EmptyState
            icon={LineChartIcon}
            title="Not enough data yet"
            description="This context needs at least 2 completed runs to show a trend. Run more benchmarks, or broaden the proxy/scenario filter above."
          />
        </SectionCard>
      ) : (
        <div className="space-y-6">
          <SectionCard title="Latency over time (ms)" compact>
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                <XAxis dataKey="time" tick={{ fill: CHART_TEXT, fontSize: 10 }} />
                <YAxis tick={{ fill: CHART_TEXT, fontSize: 11 }} />
                <RechartsTooltip contentStyle={CHART_TOOLTIP} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {baselineP99Ms != null && (
                  <ReferenceLine
                    y={baselineP99Ms}
                    stroke={BASELINE_COLOR}
                    strokeDasharray="4 4"
                    label={{ value: 'Baseline P99', position: 'insideTopRight', fill: CHART_TEXT, fontSize: 10 }}
                  />
                )}
                <Line type="monotone" dataKey="P50 (ms)" stroke="#8b5cf6" strokeWidth={2} dot={{ r: 3 }} connectNulls />
                <Line type="monotone" dataKey="P99 (ms)" stroke="#ef4444" strokeWidth={2} dot={{ r: 3 }} connectNulls />
              </LineChart>
            </ResponsiveContainer>
          </SectionCard>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            <SectionCard title="Throughput — RPS over time" compact>
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                  <XAxis dataKey="time" tick={{ fill: CHART_TEXT, fontSize: 10 }} />
                  <YAxis tick={{ fill: CHART_TEXT, fontSize: 11 }} />
                  <RechartsTooltip contentStyle={CHART_TOOLTIP} />
                  {baselineRps != null && (
                    <ReferenceLine
                      y={baselineRps}
                      stroke={BASELINE_COLOR}
                      strokeDasharray="4 4"
                      label={{ value: 'Baseline RPS', position: 'insideTopRight', fill: CHART_TEXT, fontSize: 10 }}
                    />
                  )}
                  <Line type="monotone" dataKey="RPS" stroke="#3b82f6" strokeWidth={2} dot={{ r: 3 }} connectNulls />
                </LineChart>
              </ResponsiveContainer>
            </SectionCard>

            <SectionCard title="Success rate over time (%)" compact>
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                  <XAxis dataKey="time" tick={{ fill: CHART_TEXT, fontSize: 10 }} />
                  <YAxis tick={{ fill: CHART_TEXT, fontSize: 11 }} domain={[0, 100]} />
                  <RechartsTooltip contentStyle={CHART_TOOLTIP} />
                  <Line type="monotone" dataKey="Success %" stroke="#10b981" strokeWidth={2} dot={{ r: 3 }} connectNulls />
                </LineChart>
              </ResponsiveContainer>
            </SectionCard>
          </div>
        </div>
      )}
    </div>
  );
}

// Exported for the vitest suite — keeps the (currently trivial) point-count →
// empty-state decision as a pure, directly testable function.
export function hasEnoughTrendData(points: BenchmarkTrendPoint[]): boolean {
  return points.length >= 2;
}
