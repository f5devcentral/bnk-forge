/**
 * WafPanelRenderer — renders a single user-defined panel from the panel builder.
 * Supports bar, horizontal_bar, area, line, pie, kpi, table chart types.
 */
import { useMemo } from 'react';
import {
  BarChart, Bar, AreaChart, Area, LineChart, Line,
  PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip,
  ResponsiveContainer, Legend,
} from 'recharts';
import { Pencil, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { CHART_GRID, CHART_TEXT, CHART_TOOLTIP, SERIES_COLORS, seriesColor } from '@/components/observability/chart-theme';
import { useWafPanelData } from '@/hooks/useWafPanels';
import type { WafPanel, TimeRange } from '@/lib/api/waf-panels';

const RANGE_HOURS: Record<string, number> = { '1h': 1, '24h': 24, '7d': 168, '30d': 720 };

function fmtTsBucket(ts: string, h: number): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  if (h > 24) return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
    + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function fmtNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

interface WafPanelRendererProps {
  panel: WafPanel;
  clusterId: number;
  globalTimeRange: TimeRange;
  refreshIntervalMs?: number;
  editMode?: boolean;
  onEdit?: (panel: WafPanel) => void;
  onDelete?: (panelId: number) => void;
}

export function WafPanelRenderer({
  panel, clusterId, globalTimeRange, refreshIntervalMs = 0, editMode, onEdit, onDelete,
}: WafPanelRendererProps) {
  const timeRange = panel.time_range ?? globalTimeRange;
  const { data, isLoading } = useWafPanelData(clusterId, panel.id, timeRange, refreshIntervalMs);

  const rows = data?.rows ?? [];
  const h = RANGE_HOURS[timeRange] ?? 168;

  // Detect trend templates (multi-series: ts_bucket + REJECTED/PASSED/ALERTED)
  // multi-series time data: {ts_bucket, REJECTED, PASSED, ALERTED}
  const isTrend = rows.length > 0 && 'ts_bucket' in rows[0] && 'REJECTED' in rows[0];
  // single-series time data: {ts_bucket, value} — e.g. blocked_rate_over_time
  const isTimeSeries = rows.length > 0 && 'ts_bucket' in rows[0] && 'value' in rows[0] && !isTrend;
  // label+value templates
  const isLabelValue = rows.length > 0 && 'label' in rows[0] && 'value' in rows[0];

  const chartData = useMemo(() => {
    if (isTrend)      return rows.map(r => ({ ...r, ts: String(r.ts_bucket ?? '') }));
    if (isTimeSeries) return rows.map(r => ({ ts: String(r.ts_bucket ?? ''), value: Number(r.value ?? 0) }));
    if (isLabelValue) return rows.map(r => ({ name: String(r.label ?? '—'), value: Number(r.value ?? 0) }));
    return rows;
  }, [rows, isTrend, isTimeSeries, isLabelValue]);

  const totalValue = useMemo(() =>
    isLabelValue ? rows.reduce((s, r) => s + Number(r.value ?? 0), 0) : 0
  , [rows, isLabelValue]);

  function renderChart() {
    if (isLoading) return <Skeleton className="h-48 w-full" />;
    if (!rows.length) return (
      <div className="flex items-center justify-center h-48 text-xs text-muted-foreground">No data</div>
    );

    const { chart_type } = panel;

    // KPI — show a single big number (first row's value)
    if (chart_type === 'kpi') {
      const val = isLabelValue ? totalValue : Number(Object.values(rows[0])[0] ?? 0);
      return (
        <div className="flex items-center justify-center h-48">
          <span className="text-5xl font-bold tabular-nums text-foreground">{fmtNum(val)}</span>
        </div>
      );
    }

    // Table — plain row display
    if (chart_type === 'table') {
      const keys = Object.keys(rows[0]);
      return (
        <div className="overflow-x-auto max-h-48">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                {keys.map(k => <th key={k} className="text-left pb-1 pr-3 font-medium capitalize">{k}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-b border-border/50 last:border-0">
                  {keys.map(k => <td key={k} className="py-1 pr-3 font-mono">{String(r[k] ?? '')}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    // Pie chart
    if (chart_type === 'pie' && isLabelValue) {
      return (
        <ResponsiveContainer width="100%" height={192}>
          <PieChart>
            <Pie data={chartData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={72} label={(e) => `${e.name} (${e.value})`} labelLine={false}>
              {chartData.map((_, i) => <Cell key={i} fill={seriesColor(i)} />)}
            </Pie>
            <RechartsTooltip contentStyle={CHART_TOOLTIP} />
          </PieChart>
        </ResponsiveContainer>
      );
    }

    // Single-series time chart: {ts_bucket, value} — e.g. block rate over time
    if (isTimeSeries && (chart_type === 'line' || chart_type === 'area' || chart_type === 'bar')) {
      const color = seriesColor(0);
      const Comp = chart_type === 'bar' ? BarChart : chart_type === 'area' ? AreaChart : LineChart;
      return (
        <ResponsiveContainer width="100%" height={192}>
          <Comp data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
            <XAxis dataKey="ts" tickFormatter={(v) => fmtTsBucket(String(v), h)} tick={{ fill: CHART_TEXT, fontSize: 10 }} minTickGap={40} />
            <YAxis tick={{ fill: CHART_TEXT, fontSize: 10 }} width={36} tickFormatter={(v) => `${v}%`} domain={[0, 100]} />
            <RechartsTooltip
              contentStyle={CHART_TOOLTIP}
              labelFormatter={(v) => fmtTsBucket(String(v), h)}
              formatter={(v) => [`${v}%`, 'Block Rate']}
            />
            {chart_type === 'bar'
              ? <Bar dataKey="value" fill={color} radius={[3, 3, 0, 0]} />
              : chart_type === 'area'
              ? <Area type="monotone" dataKey="value" stroke={color} fill={color} fillOpacity={0.3} dot={false} />
              : <Line type="monotone" dataKey="value" stroke={color} dot={false} strokeWidth={2} />
            }
          </Comp>
        </ResponsiveContainer>
      );
    }

    // Trend (multi-series area/line)
    if (isTrend && (chart_type === 'area' || chart_type === 'line')) {
      const seriesKeys = ['REJECTED', 'PASSED', 'ALERTED'].filter(k => k in rows[0]);
      const COLORS: Record<string, string> = { REJECTED: '#ef4444', ALERTED: '#f59e0b', PASSED: '#10b981' };
      const Comp = chart_type === 'area' ? AreaChart : LineChart;
      return (
        <ResponsiveContainer width="100%" height={192}>
          <Comp data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
            <XAxis dataKey="ts" tickFormatter={(v) => fmtTsBucket(String(v), h)} tick={{ fill: CHART_TEXT, fontSize: 10 }} minTickGap={40} />
            <YAxis tick={{ fill: CHART_TEXT, fontSize: 10 }} width={36} />
            <RechartsTooltip contentStyle={CHART_TOOLTIP} labelFormatter={(v) => fmtTsBucket(String(v), h)} />
            <Legend iconSize={8} wrapperStyle={{ fontSize: 10 }} />
            {seriesKeys.map(k => (
              chart_type === 'area'
                ? <Area key={k} type="monotone" dataKey={k} stackId="1" stroke={COLORS[k] ?? seriesColor(0)} fill={COLORS[k] ?? seriesColor(0)} fillOpacity={0.8} />
                : <Line key={k} type="monotone" dataKey={String(k)} stroke={COLORS[String(k)] ?? seriesColor(0)} dot={false} />
            ))}
          </Comp>
        </ResponsiveContainer>
      );
    }

    // Horizontal bar
    if (chart_type === 'horizontal_bar' && isLabelValue) {
      return (
        <ResponsiveContainer width="100%" height={Math.max(160, chartData.length * 32)}>
          <BarChart data={chartData} layout="vertical" margin={{ top: 0, right: 40, left: 8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} horizontal={false} />
            <XAxis type="number" tick={{ fill: CHART_TEXT, fontSize: 10 }} tickFormatter={fmtNum} />
            <YAxis type="category" dataKey="name" width={120} tick={{ fill: CHART_TEXT, fontSize: 10 }} />
            <RechartsTooltip contentStyle={CHART_TOOLTIP} formatter={(v) => [fmtNum(Number(v)), '']} />
            <Bar dataKey="value" radius={[0, 3, 3, 0]}>
              {chartData.map((_, i) => <Cell key={i} fill={SERIES_COLORS[i % SERIES_COLORS.length]} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      );
    }

    // Default: vertical bar
    return (
      <ResponsiveContainer width="100%" height={192}>
        <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
          <XAxis dataKey="name" tick={{ fill: CHART_TEXT, fontSize: 10 }} />
          <YAxis tick={{ fill: CHART_TEXT, fontSize: 10 }} width={36} tickFormatter={fmtNum} />
          <RechartsTooltip contentStyle={CHART_TOOLTIP} formatter={(v) => [fmtNum(Number(v)), '']} />
          <Bar dataKey="value" radius={[3, 3, 0, 0]}>
            {chartData.map((_, i) => <Cell key={i} fill={SERIES_COLORS[i % SERIES_COLORS.length]} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <h3 className="text-sm font-semibold text-foreground truncate">{panel.title}</h3>
          <Badge variant="outline" className="text-[9px] px-1.5 py-0 h-4 shrink-0 text-muted-foreground">
            {panel.time_range}
          </Badge>
        </div>
        {editMode && (
          <div className="flex items-center gap-1 shrink-0">
            <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => onEdit?.(panel)}>
              <Pencil className="h-3 w-3" />
            </Button>
            <Button variant="ghost" size="sm" className="h-6 w-6 p-0 text-destructive hover:text-destructive" onClick={() => onDelete?.(panel.id)}>
              <Trash2 className="h-3 w-3" />
            </Button>
          </div>
        )}
      </div>
      <div className="p-4">{renderChart()}</div>
    </div>
  );
}
