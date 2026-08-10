/**
 * TimeSeriesChart — multi-series bar/area/line renderer over the histogram
 * `series` contract shape. Merges per-series point arrays into one recharts
 * dataset keyed by timestamp, then renders stacked bars, stacked areas, or
 * (for latency percentiles) overlaid lines.
 */
import {
  BarChart,
  Bar,
  AreaChart,
  Area,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
} from 'recharts';
import { CHART_GRID, CHART_TEXT, CHART_TOOLTIP } from './chart-theme';
import type { LlmSeries } from '@/types/llm-observability';

export interface TimeSeriesChartProps {
  series: LlmSeries[];
  colors: string[];
  type: 'bar' | 'area' | 'line';
  stacked?: boolean;
  height?: number;
  /** Formats the y-axis + tooltip values. */
  valueFormatter?: (v: number) => string;
}

interface MergedRow {
  ts: string;
  [seriesName: string]: string | number;
}

function mergeSeries(series: LlmSeries[]): MergedRow[] {
  const byTs = new Map<string, MergedRow>();
  for (const s of series) {
    for (const p of s.points) {
      const row = byTs.get(p.ts) ?? { ts: p.ts };
      row[s.name] = p.value;
      byTs.set(p.ts, row);
    }
  }
  return Array.from(byTs.values()).sort((a, b) => (a.ts < b.ts ? -1 : 1));
}

function fmtTick(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function TimeSeriesChart({
  series,
  colors,
  type,
  stacked = false,
  height = 220,
  valueFormatter,
}: TimeSeriesChartProps) {
  const data = mergeSeries(series);
  const names = series.map((s) => s.name);
  const fmt = valueFormatter ?? ((v: number) => String(v));

  const axes = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
      <XAxis dataKey="ts" tickFormatter={fmtTick} tick={{ fill: CHART_TEXT, fontSize: 10 }} minTickGap={24} />
      <YAxis tickFormatter={(v) => fmt(Number(v))} tick={{ fill: CHART_TEXT, fontSize: 10 }} width={48} />
      <RechartsTooltip
        contentStyle={CHART_TOOLTIP}
        labelFormatter={(l) => fmtTick(String(l))}
        formatter={(v, name) => [fmt(Number(v)), name]}
      />
    </>
  );

  return (
    <div style={{ width: '100%', height: `${height}px` }}>
    <ResponsiveContainer width="100%" height="100%" debounce={50}>
      {type === 'bar' ? (
        <BarChart data={data}>
          {axes}
          {names.map((name, i) => (
            <Bar
              key={name}
              dataKey={name}
              stackId={stacked ? 'stack' : undefined}
              fill={colors[i % colors.length]}
              radius={stacked ? undefined : [3, 3, 0, 0]}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      ) : type === 'area' ? (
        <AreaChart data={data}>
          {axes}
          {names.map((name, i) => (
            <Area
              key={name}
              type="monotone"
              dataKey={name}
              stackId={stacked ? 'stack' : undefined}
              stroke={colors[i % colors.length]}
              fill={colors[i % colors.length]}
              fillOpacity={0.25}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      ) : (
        <LineChart data={data}>
          {axes}
          {names.map((name, i) => (
            <Line
              key={name}
              type="monotone"
              dataKey={name}
              stroke={colors[i % colors.length]}
              dot={false}
              strokeWidth={1.5}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      )}
    </ResponsiveContainer>
    </div>
  );
}
