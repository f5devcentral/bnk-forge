/**
 * BenchmarkCompareTab — D-020: side-by-side proxy comparison with winner indicators.
 *
 * Chrome (panel surfaces, headers, legend chrome, axis label tints) uses tokens.
 * Recharts series fills are preserved per ADR D-020 §3 / resolved decisions §6
 * (recharts series allowlisted): P50/P99/TTFT/ITL/TST/RPS/Peak/per-user.
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
  Legend,
} from 'recharts';
import { Skeleton } from '@/components/ui/skeleton';
import { SectionCard } from '@/components/ui/section-card';
import { EmptyState } from '@/components/ui/empty-state';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Trophy, AlertTriangle, SearchX } from 'lucide-react';
import { useBenchmarkCompare } from '@/hooks/useBenchmarks';
import {
  PROXY_LABELS,
  ProxyBadge,
  fmtDuration,
  fmtLatency,
  fmtNum,
  fmtPct,
} from './benchmark-utils';
import type { BenchmarkCompareRunMetrics } from '@/types';

// ============================================================================
// Main Component
// ============================================================================

export function BenchmarkCompareTab({ runIds }: { runIds: number[] }) {
  const { data, isLoading, isError } = useBenchmarkCompare(runIds);

  if (isError) {
    return (
      <SectionCard>
        <EmptyState
          icon={SearchX}
          title="Comparison unavailable"
          description="One or more of the selected runs no longer exist — they may have been deleted."
        />
      </SectionCard>
    );
  }

  if (isLoading || !data) return <Skeleton className="h-64 w-full" />;

  const metrics = data.runs;
  const winners = data.winners;

  return (
    <div className="space-y-6">
      <h3 className="text-lg font-semibold text-foreground">Proxy Comparison</h3>

      {data.context_mismatch && (
        <div className="flex items-start gap-2 rounded-lg border border-warning/20 bg-warning/10 px-4 py-3 text-sm text-warning">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
          <p>
            These runs don&apos;t share the same config or scenario — differences below may
            reflect a different workload, not proxy performance.
          </p>
        </div>
      )}

      {/* Comparison Table */}
      <SectionCard compact>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Metric</TableHead>
                {metrics.map(m => (
                  <TableHead key={m.run_id} className="text-center">
                    <div className="flex flex-col items-center gap-1">
                      <ProxyBadge proxy={m.proxy} />
                      {m.run_label && <span className="text-[10px] text-muted-foreground truncate max-w-[120px]">{m.run_label}</span>}
                    </div>
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              <CompareRow label="Latency P50" values={metrics.map(m => fmtLatency(m.latency_p50))} winnerIdx={findWinnerIdx(metrics, 'latency_p50', winners)} />
              <CompareRow label="Latency P99" values={metrics.map(m => fmtLatency(m.latency_p99))} winnerIdx={findWinnerIdx(metrics, 'latency_p99', winners)} />
              <CompareRow label="TTFT (avg)" values={metrics.map(m => m.ttft_avg != null ? `${m.ttft_avg.toFixed(1)}ms` : '—')} winnerIdx={findWinnerIdx(metrics, 'ttft_avg', winners)} />
              <CompareRow label="ITL (avg)" values={metrics.map(m => m.itl_avg != null ? `${m.itl_avg.toFixed(1)}ms` : '—')} winnerIdx={findWinnerIdx(metrics, 'itl_avg', winners)} />
              <CompareRow label="TST (avg)" values={metrics.map(m => m.tst_avg != null ? `${m.tst_avg.toFixed(1)}ms` : '—')} winnerIdx={findWinnerIdx(metrics, 'tst_avg', winners)} />
              <CompareRow label="Per-User Throughput" values={metrics.map(m => m.per_user_throughput_avg != null ? `${m.per_user_throughput_avg.toFixed(1)} tok/s` : '—')} winnerIdx={findWinnerIdx(metrics, 'per_user_throughput_avg', winners)} />
              <CompareRow label="Overall RPS" values={metrics.map(m => fmtNum(m.overall_rps))} winnerIdx={findWinnerIdx(metrics, 'overall_rps', winners)} />
              <CompareRow label="Peak RPS" values={metrics.map(m => fmtNum(m.peak_rps))} winnerIdx={findWinnerIdx(metrics, 'peak_rps', winners)} />
              <CompareRow label="Tokens/sec" values={metrics.map(m => fmtNum(m.tokens_per_sec))} winnerIdx={findWinnerIdx(metrics, 'tokens_per_sec', winners)} />
              <CompareRow label="Success Rate" values={metrics.map(m => fmtPct(m.success_rate_pct))} winnerIdx={findWinnerIdx(metrics, 'success_rate_pct', winners)} />
              <CompareRow label="Total Requests" values={metrics.map(m => m.total_requests?.toLocaleString() ?? '—')} />
              <CompareRow label="Duration" values={metrics.map(m => fmtDuration(m.duration_seconds))} />
            </TableBody>
          </Table>
        </div>
      </SectionCard>

      {/* Comparison Charts */}
      <CompareCharts metrics={metrics} />
    </div>
  );
}

// ============================================================================
// Sub-Components
// ============================================================================

function CompareRow({ label, values, winnerIdx }: { label: string; values: string[]; winnerIdx?: number }) {
  return (
    <TableRow>
      <TableCell className="font-medium">{label}</TableCell>
      {values.map((v, i) => (
        <TableCell key={i} className={cn('text-center font-mono', i === winnerIdx && 'text-success font-bold')}>
          {v} {i === winnerIdx && <Trophy className="inline h-3 w-3" />}
        </TableCell>
      ))}
    </TableRow>
  );
}

function findWinnerIdx(metrics: BenchmarkCompareRunMetrics[], metric: string, winners: Record<string, number>): number | undefined {
  const winnerId = winners[metric];
  if (winnerId == null) return undefined;
  return metrics.findIndex(m => m.run_id === winnerId);
}

function CompareCharts({ metrics }: { metrics: BenchmarkCompareRunMetrics[] }) {
  // Chart chrome — use CSS variables so axis labels/grid follow active theme.
  // Recharts can't read tokens directly so we resolve at render time via
  // currentColor on the chart parent; falling back to muted-foreground hex.
  const gridColor = 'hsl(var(--border))';
  const textColor = 'hsl(var(--muted-foreground))';
  const tooltipStyle = {
    backgroundColor: 'hsl(var(--card))',
    border: '1px solid hsl(var(--border))',
    borderRadius: 8,
    fontSize: 12,
    color: 'hsl(var(--foreground))',
  };

  const runLabel = (m: BenchmarkCompareRunMetrics) => m.run_label || (PROXY_LABELS[m.proxy] || m.proxy);

  const latencyData = metrics.map(m => ({
    name: runLabel(m),
    'P50 (ms)': (m.latency_p50 ?? 0) * 1000,
    'P99 (ms)': (m.latency_p99 ?? 0) * 1000,
  }));

  const rpsData = metrics.map(m => ({
    name: runLabel(m),
    'Overall RPS': m.overall_rps ?? 0,
    'Peak RPS': m.peak_rps ?? 0,
  }));

  const hasTtft = metrics.some(m => m.ttft_avg != null);
  const hasItl = metrics.some(m => m.itl_avg != null);
  const hasPerUser = metrics.some(m => m.per_user_throughput_avg != null);

  const ttftData = hasTtft ? metrics.map(m => ({
    name: runLabel(m),
    'TTFT (ms)': m.ttft_avg ?? 0,
    'ITL (ms)': m.itl_avg ?? 0,
    'TST (ms)': m.tst_avg ?? 0,
  })) : [];

  const perUserData = hasPerUser ? metrics.map(m => ({
    name: runLabel(m),
    'tok/s/user': m.per_user_throughput_avg ?? 0,
  })) : [];

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
      {/* Latency Comparison */}
      <SectionCard title="Request latency (ms)" compact>
        <ResponsiveContainer width="100%" height={250}>
          <BarChart data={latencyData} barCategoryGap="20%">
            <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
            <XAxis dataKey="name" tick={{ fill: textColor, fontSize: 10 }} />
            <YAxis tick={{ fill: textColor, fontSize: 11 }} />
            <RechartsTooltip contentStyle={tooltipStyle} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar dataKey="P50 (ms)" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
            <Bar dataKey="P99 (ms)" fill="#ef4444" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </SectionCard>

      {/* TTFT / ITL / TST Comparison */}
      {(hasTtft || hasItl) && (
        <SectionCard title="Token latency — TTFT / ITL / TST (ms)" compact>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={ttftData} barCategoryGap="20%">
              <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
              <XAxis dataKey="name" tick={{ fill: textColor, fontSize: 10 }} />
              <YAxis tick={{ fill: textColor, fontSize: 11 }} />
              <RechartsTooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="TTFT (ms)" fill="#06b6d4" radius={[4, 4, 0, 0]} />
              <Bar dataKey="ITL (ms)" fill="#14b8a6" radius={[4, 4, 0, 0]} />
              <Bar dataKey="TST (ms)" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </SectionCard>
      )}

      {/* RPS Comparison */}
      <SectionCard title="Throughput — RPS" compact>
        <ResponsiveContainer width="100%" height={250}>
          <BarChart data={rpsData} barCategoryGap="20%">
            <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
            <XAxis dataKey="name" tick={{ fill: textColor, fontSize: 10 }} />
            <YAxis tick={{ fill: textColor, fontSize: 11 }} />
            <RechartsTooltip contentStyle={tooltipStyle} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar dataKey="Overall RPS" fill="#3b82f6" radius={[4, 4, 0, 0]} />
            <Bar dataKey="Peak RPS" fill="#f59e0b" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </SectionCard>

      {/* Per-User Throughput Comparison */}
      {hasPerUser && (
        <SectionCard title="Per-user throughput (tok/s/user)" compact>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={perUserData} barCategoryGap="20%">
              <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
              <XAxis dataKey="name" tick={{ fill: textColor, fontSize: 10 }} />
              <YAxis tick={{ fill: textColor, fontSize: 11 }} />
              <RechartsTooltip contentStyle={tooltipStyle} />
              <Bar dataKey="tok/s/user" fill="#10b981" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </SectionCard>
      )}
    </div>
  );
}
