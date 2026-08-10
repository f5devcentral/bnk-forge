/**
 * LlmDashboard — AI Gateway observability (Tier 1: request analytics).
 *
 * Header filters (cluster · model · time range · status) + three tabs:
 * Overview (chart grid incl. gauges), Model Rankings (stacked bar + table),
 * Provider Usage (cost/tokens/latency, provider inferred from model).
 * Dashboard hooks are gated on the active tab so inactive tabs don't hit Loki.
 */
import { useMemo, useState } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { SectionCard } from '@/components/ui/section-card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Info, ArrowUpDown } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  ChartCard,
  ChartTypeToggle,
  Gauge,
  TimeSeriesChart,
  TimeRangePicker,
  TrendBadge,
  ClusterPicker,
  FilterSelect,
  seriesColor,
  providerColor,
  SUCCESS_COLOR,
  ERROR_COLOR,
  fmtInt,
  fmtCompact,
  fmtCost,
  fmtLatencyMs,
  fmtPct,
  inferProvider,
  type ChartType,
} from '@/components/observability';
import {
  useLlmHistogram,
  useLlmFilterData,
  useLlmProviderUsage,
  useLlmRankings,
} from '@/hooks/useLlmObservability';
import { useObservabilityFilters } from './use-observability-filters';
import type {
  LlmHistogramMetric,
  LlmObservabilityParams,
  LlmProviderMetric,
  LlmRankingRow,
  LlmSeries,
} from '@/types/llm-observability';

const sumSeries = (series: LlmSeries[]): number =>
  series.reduce((acc, s) => acc + s.points.reduce((a, p) => a + p.value, 0), 0);

const seriesTotal = (s: LlmSeries): number => s.points.reduce((a, p) => a + p.value, 0);

const requestSeriesColor = (name: string): string =>
  name.toLowerCase().includes('error') ? ERROR_COLOR : SUCCESS_COLOR;

export default function LlmDashboard() {
  const f = useObservabilityFilters('overview');
  const { data: filterData } = useLlmFilterData(f.clusterId, f.range);

  return (
    <div className="space-y-6 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-foreground">Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            Request analytics from the LLM gateway (Loki-backed).
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ClusterPicker value={f.clusterId} onChange={f.setClusterId} />
          <FilterSelect
            value={f.model}
            onChange={f.setModel}
            options={filterData?.models ?? []}
            allLabel="All models"
            placeholder="Model"
            ariaLabel="Model filter"
          />
          <TimeRangePicker value={f.range} onChange={f.setRange} />
          <FilterSelect
            value={f.status}
            onChange={f.setStatus}
            options={filterData?.statuses ?? []}
            allLabel="All statuses"
            placeholder="Status"
            width="w-[130px]"
            ariaLabel="Status filter"
          />
        </div>
      </div>

      <Tabs value={f.tab} onValueChange={f.setTab}>
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="rankings">Model Rankings</TabsTrigger>
          <TabsTrigger value="providers">Provider Usage</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-4">
          <OverviewTab clusterId={f.clusterId} params={f.queryParams} active={f.tab === 'overview'} />
        </TabsContent>
        <TabsContent value="rankings" className="mt-4">
          <RankingsTab clusterId={f.clusterId} params={f.queryParams} active={f.tab === 'rankings'} />
        </TabsContent>
        <TabsContent value="providers" className="mt-4">
          <ProviderUsageTab clusterId={f.clusterId} params={f.queryParams} active={f.tab === 'providers'} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ============================================================================
// Overview
// ============================================================================

interface TabProps {
  clusterId: number | undefined;
  params: LlmObservabilityParams;
  active: boolean;
}

function OverviewTab({ clusterId, params, active }: TabProps) {
  const tokens = useLlmHistogram(clusterId, { ...params, metric: 'tokens' }, active);

  // Derive external cache hit rate from the token histogram's "cached" series.
  const cacheRate = useMemo(() => {
    const series = tokens.data?.series ?? [];
    const cached = series.find((s) => s.name.toLowerCase().includes('cache'));
    const input = series.find((s) => /input|prompt/.test(s.name.toLowerCase()));
    if (!cached || !input) return null;
    const c = seriesTotal(cached);
    const i = seriesTotal(input);
    if (c + i === 0) return 0;
    return c / (c + i);
  }, [tokens.data]);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 2xl:grid-cols-3 gap-4">
      <HistogramCard
        clusterId={clusterId}
        params={params}
        metric="requests"
        title="Request Volume"
        active={active}
        stacked
        colorFor={requestSeriesColor}
        valueFormatter={fmtCompact}
        totalFormatter={fmtInt}
      />
      <HistogramCard
        clusterId={clusterId}
        params={params}
        metric="tokens"
        title="Token Usage"
        active={active}
        stacked
        valueFormatter={fmtCompact}
        totalFormatter={fmtCompact}
      />
      <ChartCard
        title="External Cache Hit Rate"
        available={tokens.data?.available ?? true}
        unavailableReason={tokens.data?.errors?.tokens || 'No token data'}
        loading={tokens.isLoading}
      >
        <Gauge value={cacheRate ?? 0} label="cached ÷ input tokens" />
      </ChartCard>
      <ChartCard
        title="Local Cache Hit Rate"
        available={false}
        unavailableReason="Local cache hit rate is not in the Tier-1 stream (deferred)."
      />
      <HistogramCard
        clusterId={clusterId}
        params={params}
        metric="cost"
        title="Cost"
        active={active}
        valueFormatter={fmtCost}
        totalFormatter={fmtCost}
      />
      <HistogramCard
        clusterId={clusterId}
        params={params}
        metric="models"
        title="Model Usage"
        active={active}
        stacked
        valueFormatter={fmtCompact}
        totalFormatter={fmtInt}
      />
      <HistogramCard
        clusterId={clusterId}
        params={params}
        metric="latency"
        title="Latency"
        active={active}
        forceType="line"
        valueFormatter={fmtLatencyMs}
      />
    </div>
  );
}

interface HistogramCardProps {
  clusterId: number | undefined;
  params: LlmObservabilityParams;
  metric: LlmHistogramMetric;
  title: string;
  active: boolean;
  stacked?: boolean;
  forceType?: 'line';
  colorFor?: (name: string) => string;
  valueFormatter: (v: number) => string;
  totalFormatter?: (v: number) => string;
}

function HistogramCard({
  clusterId,
  params,
  metric,
  title,
  active,
  stacked,
  forceType,
  colorFor,
  valueFormatter,
  totalFormatter,
}: HistogramCardProps) {
  const [chartType, setChartType] = useState<ChartType>('bar');
  const { data, isLoading } = useLlmHistogram(clusterId, { ...params, metric }, active);

  const series = data?.series ?? [];
  const colors = series.map((s, i) => (colorFor ? colorFor(s.name) : seriesColor(i)));
  const legend = series.map((s, i) => ({ name: s.name, color: colors[i] }));
  const total = totalFormatter ? totalFormatter(sumSeries(series)) : undefined;
  const type = forceType ?? chartType;

  return (
    <ChartCard
      title={title}
      total={total}
      legend={legend}
      loading={isLoading}
      available={data?.available ?? true}
      unavailableReason={data?.errors?.[metric] || 'Data source unavailable'}
      controls={forceType ? undefined : <ChartTypeToggle value={chartType} onChange={setChartType} />}
    >
      <TimeSeriesChart series={series} colors={colors} type={type} stacked={stacked} valueFormatter={valueFormatter} />
    </ChartCard>
  );
}

// ============================================================================
// Model Rankings
// ============================================================================

type SortKey = 'requests' | 'tokens' | 'cost' | 'avg_latency_ms' | 'success_rate';

function RankingsTab({ clusterId, params, active }: TabProps) {
  const { data, isLoading } = useLlmRankings(clusterId, params, active);
  const [sortKey, setSortKey] = useState<SortKey>('requests');

  const rows = useMemo(() => {
    const r = [...(data?.rows ?? [])];
    return r.sort((a, b) => b[sortKey] - a[sortKey]);
  }, [data, sortKey]);

  const topSeries = useMemo(() => buildTopModelsSeries(rows), [rows]);

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (!data?.available) {
    return (
      <SectionCard>
        <p className="text-sm text-muted-foreground">{data?.errors?.rankings || 'Rankings unavailable.'}</p>
      </SectionCard>
    );
  }

  const colors = topSeries.map((_, i) => seriesColor(i));

  return (
    <div className="space-y-4">
      <ChartCard
        title="Top Models by Requests"
        legend={topSeries.map((s, i) => ({ name: s.name, color: colors[i] }))}
        maxLegend={6}
      >
        <TimeSeriesChart series={topSeries} colors={colors} type="bar" stacked valueFormatter={fmtCompact} />
      </ChartCard>

      <SectionCard compact>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">#</TableHead>
                <TableHead>Model</TableHead>
                <SortHead label="Requests" k="requests" sortKey={sortKey} onSort={setSortKey} />
                <SortHead label="Success" k="success_rate" sortKey={sortKey} onSort={setSortKey} />
                <SortHead label="Tokens" k="tokens" sortKey={sortKey} onSort={setSortKey} />
                <SortHead label="Cost" k="cost" sortKey={sortKey} onSort={setSortKey} />
                <SortHead label="Avg Latency" k="avg_latency_ms" sortKey={sortKey} onSort={setSortKey} />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row, i) => (
                <TableRow key={row.model}>
                  <TableCell className="text-muted-foreground tabular-nums">{i + 1}</TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <span
                        className="h-2 w-2 rounded-full flex-shrink-0"
                        style={{ backgroundColor: providerColor(row.provider) }}
                        title={row.provider}
                      />
                      <span className="font-medium text-foreground">{row.model}</span>
                      <span className="text-[10px] text-muted-foreground uppercase">{row.provider}</span>
                    </div>
                  </TableCell>
                  <NumCell value={fmtInt(row.requests)} delta={row.trend.requests} />
                  <TableCell className="text-right tabular-nums text-foreground/80">{fmtPct(row.success_rate)}</TableCell>
                  <NumCell value={fmtCompact(row.tokens)} delta={row.trend.tokens} />
                  <NumCell value={fmtCost(row.cost)} delta={row.trend.cost} />
                  <TableCell className="text-right tabular-nums text-foreground/80">{fmtLatencyMs(row.avg_latency_ms)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </SectionCard>
    </div>
  );
}

function SortHead({
  label,
  k,
  sortKey,
  onSort,
}: {
  label: string;
  k: SortKey;
  sortKey: SortKey;
  onSort: (k: SortKey) => void;
}) {
  return (
    <TableHead className="text-right">
      <button
        type="button"
        onClick={() => onSort(k)}
        className={cn(
          'inline-flex items-center gap-1 hover:text-foreground transition-colors',
          sortKey === k ? 'text-foreground' : 'text-muted-foreground',
        )}
      >
        {label}
        <ArrowUpDown className="h-3 w-3" />
      </button>
    </TableHead>
  );
}

function NumCell({ value, delta }: { value: string; delta: number | null }) {
  return (
    <TableCell className="text-right">
      <div className="flex items-center justify-end gap-1.5">
        <span className="tabular-nums text-foreground/80">{value}</span>
        <TrendBadge delta={delta} />
      </div>
    </TableCell>
  );
}

/** Build a single-bucket stacked series (top-N models + "Other") for the bar. */
function buildTopModelsSeries(rows: LlmRankingRow[], topN = 6): LlmSeries[] {
  const sorted = [...rows].sort((a, b) => b.requests - a.requests);
  const top = sorted.slice(0, topN);
  const rest = sorted.slice(topN);
  const bucket = 'total';
  const series: LlmSeries[] = top.map((r) => ({
    name: r.model,
    points: [{ ts: bucket, value: r.requests }],
  }));
  if (rest.length > 0) {
    series.push({
      name: 'Other',
      points: [{ ts: bucket, value: rest.reduce((a, r) => a + r.requests, 0) }],
    });
  }
  return series;
}

// ============================================================================
// Provider Usage
// ============================================================================

function ProviderUsageTab({ clusterId, params, active }: TabProps) {
  const [provider, setProvider] = useState('');

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Info className="h-3.5 w-3.5" />
        <span>Provider is inferred from the model name.</span>
        <div className="ml-2">
          <FilterSelect
            value={provider}
            onChange={setProvider}
            options={['openai', 'anthropic', 'google', 'unknown']}
            allLabel="All providers"
            placeholder="Provider"
            width="w-[150px]"
            ariaLabel="Provider filter"
          />
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 2xl:grid-cols-3 gap-4">
        <ProviderCard clusterId={clusterId} params={params} metric="cost" title="Cost by Provider" active={active} provider={provider} valueFormatter={fmtCost} />
        <ProviderCard clusterId={clusterId} params={params} metric="tokens" title="Tokens by Provider" active={active} provider={provider} valueFormatter={fmtCompact} />
        <ProviderCard clusterId={clusterId} params={params} metric="latency" title="Latency by Provider" active={active} provider={provider} valueFormatter={fmtLatencyMs} />
      </div>
    </div>
  );
}

function ProviderCard({
  clusterId,
  params,
  metric,
  title,
  active,
  provider,
  valueFormatter,
}: {
  clusterId: number | undefined;
  params: LlmObservabilityParams;
  metric: LlmProviderMetric;
  title: string;
  active: boolean;
  provider: string;
  valueFormatter: (v: number) => string;
}) {
  const { data, isLoading } = useLlmProviderUsage(clusterId, { ...params, metric }, active);

  // Series are keyed by provider; a model name is defensively folded too.
  const series = (data?.series ?? []).filter(
    (s) => !provider || inferProvider(s.name) === provider || s.name === provider,
  );
  const colors = series.map((s) => providerColor(inferProvider(s.name) === 'unknown' ? s.name : inferProvider(s.name)));

  return (
    <ChartCard
      title={title}
      legend={series.map((s, i) => ({ name: s.name, color: colors[i] }))}
      loading={isLoading}
      available={data?.available ?? true}
      unavailableReason={data?.errors?.[metric] || 'Data source unavailable'}
    >
      <TimeSeriesChart series={series} colors={colors} type="area" valueFormatter={valueFormatter} />
    </ChartCard>
  );
}
