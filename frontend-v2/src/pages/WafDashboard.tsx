import { useState } from 'react';
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts';
import {
  ShieldOff,
  Shield,
  Wifi,
  WifiOff,
  RefreshCw,
  AlertTriangle,
  Activity,
  Globe,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useAllClusters } from '@/hooks/useK8sClusters';
import {
  useWafDashboardStatus,
  useWafDashboardSummary,
  useWafDashboardTrend,
  useWafDashboardTopAttacks,
  useWafDashboardTopIps,
  useWafDashboardTopUris,
} from '@/hooks/useWafDashboard';
import type {
  TimeRange,
  DashboardSummary,
  DashboardTrend,
  DashboardTopAttacks,
  DashboardTopIps,
  DashboardTopUris,
} from '@/lib/api/waf-dashboard';
import { CHART_GRID, CHART_TEXT, CHART_TOOLTIP } from '@/components/observability/chart-theme';

// ── Semantic colours for WAF outcomes ───────────────────────────────────────
const REJECTED_COLOR = '#ef4444';
const ALERTED_COLOR  = '#f59e0b';
const PASSED_COLOR   = '#10b981';

// ── Palette for attack type bars ─────────────────────────────────────────────
const ATTACK_PALETTE = [
  '#ef4444', '#f97316', '#f59e0b', '#eab308', '#84cc16',
  '#22c55e', '#14b8a6', '#3b82f6', '#8b5cf6', '#ec4899',
];

function fmtNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

const RANGE_HOURS: Record<string, number> = { '1h': 1, '24h': 24, '7d': 168, '30d': 720 };

function fmtTs(ts: string, bucketHours: number, totalHours: number): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  // 30d with 6h buckets: show date only
  if (bucketHours >= 24) return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  // 7d with 1h buckets: show date + time so ticks are unambiguous across days
  if (totalHours > 24) return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
    + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  // 1h / 24h: time only is enough
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// ── KPI card ─────────────────────────────────────────────────────────────────
interface KpiCardProps {
  label: string;
  value: string | number;
  sub?: string;
  icon: React.ReactNode;
  accent?: 'destructive' | 'warning' | 'success' | 'default';
  loading?: boolean;
}

function KpiCard({ label, value, sub, icon, accent = 'default', loading }: KpiCardProps) {
  const accentClass = {
    destructive: 'text-destructive',
    warning:     'text-warning',
    success:     'text-success',
    default:     'text-foreground',
  }[accent];

  return (
    <div className="rounded-lg border border-border bg-card p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</span>
        <span className="text-muted-foreground/60">{icon}</span>
      </div>
      {loading ? (
        <Skeleton className="h-8 w-24" />
      ) : (
        <span className={cn('text-2xl font-bold tabular-nums', accentClass)}>{value}</span>
      )}
      {sub && <span className="text-xs text-muted-foreground">{sub}</span>}
    </div>
  );
}

// ── Section card wrapper ──────────────────────────────────────────────────────
function Panel({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn('rounded-lg border border-border bg-card', className)}>
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

// ── Time range toggle ─────────────────────────────────────────────────────────
const TIME_RANGES: { value: TimeRange; label: string }[] = [
  { value: '1h',  label: '1 h' },
  { value: '24h', label: '24 h' },
  { value: '7d',  label: '7 d' },
  { value: '30d', label: '30 d' },
];

function TimeRangePicker({ value, onChange }: { value: TimeRange; onChange: (v: TimeRange) => void }) {
  return (
    <div className="flex items-center gap-1 rounded-md border border-border bg-muted/30 p-0.5">
      {TIME_RANGES.map((r) => (
        <button
          key={r.value}
          onClick={() => onChange(r.value)}
          className={cn(
            'px-3 py-1 rounded text-xs font-medium transition-colors',
            value === r.value
              ? 'bg-background text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground',
          )}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

// ── Unavailable / no-data states ──────────────────────────────────────────────
function UnavailableBanner({ reason }: { reason: string }) {
  return (
    <div className="rounded-lg border border-warning/30 bg-warning/10 px-4 py-3 flex items-start gap-3">
      <WifiOff className="h-4 w-4 text-warning mt-0.5 shrink-0" />
      <div>
        <p className="text-sm font-medium text-warning">ClickHouse not available</p>
        <p className="text-xs text-muted-foreground mt-0.5">{reason}</p>
      </div>
    </div>
  );
}

function EmptyState({ icon, title, description }: { icon: React.ReactNode; title: string; description: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3 text-center">
      <div className="text-muted-foreground/30">{icon}</div>
      <p className="text-sm font-medium text-foreground">{title}</p>
      <p className="text-xs text-muted-foreground max-w-xs">{description}</p>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function WafDashboard() {
  const { data: clustersData } = useAllClusters();
  const clusters = clustersData?.clusters ?? [];

  const [selectedCluster, setSelectedCluster] = useState<number | null>(null);
  const [timeRange, setTimeRange] = useState<TimeRange>('7d');

  const clusterId = selectedCluster ?? clusters[0]?.id ?? null;

  const { data: statusData,  isLoading: statusLoading,  refetch: refetchStatus  } = useWafDashboardStatus(clusterId);
  const { data: summaryData, isLoading: summaryLoading, isFetching: summaryFetching, refetch: refetchSummary } = useWafDashboardSummary(clusterId, timeRange);
  const { data: trendData,   isLoading: trendLoading,                                refetch: refetchTrend   } = useWafDashboardTrend(clusterId, timeRange);
  const { data: attacksData, isLoading: attacksLoading,                              refetch: refetchAttacks } = useWafDashboardTopAttacks(clusterId, timeRange);
  const { data: ipsData,     isLoading: ipsLoading,                                  refetch: refetchIps     } = useWafDashboardTopIps(clusterId, timeRange);
  const { data: urisData,    isLoading: urisLoading,                                 refetch: refetchUris    } = useWafDashboardTopUris(clusterId, timeRange);

  const refetchAll = () => {
    void refetchStatus();
    void refetchSummary();
    void refetchTrend();
    void refetchAttacks();
    void refetchIps();
    void refetchUris();
  };

  // isFetching on summary is the best proxy for "any panel is reloading" on refresh
  const isLoading = statusLoading || summaryLoading || trendLoading || attacksLoading || ipsLoading || urisLoading || summaryFetching;
  const available = statusData?.available === true;
  const unavailableReason = statusData?.available === false ? statusData.reason : null;

  const summary  = summaryData?.available  === true ? (summaryData  as DashboardSummary)    : null;
  const trend    = trendData?.available    === true ? (trendData    as DashboardTrend)      : null;
  const attacks  = attacksData?.available  === true ? (attacksData  as DashboardTopAttacks) : null;
  const ips      = ipsData?.available      === true ? (ipsData      as DashboardTopIps)     : null;
  const uris     = urisData?.available     === true ? (urisData     as DashboardTopUris)    : null;

  // Skeletons only on first mount — keepPreviousData keeps old values visible during refetch
  const summaryPending = summaryLoading;
  const trendPending   = trendLoading;
  const attacksPending = attacksLoading;
  const ipsPending     = ipsLoading;
  const urisPending    = urisLoading;

  return (
    <div className="space-y-6 p-6">
      {/* Page header */}
      <PageHeader
        title="WAF Dashboard"
        subtitle={
          available && statusData
            ? <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Wifi className="h-3 w-3 text-success" />
                ClickHouse connected · {fmtNumber(statusData.total_events_30d)} events (30 d)
              </span>
            : undefined
        }
        actions={
          <div className="flex items-center gap-3">
            {/* Cluster selector */}
            <Select
              value={clusterId ? String(clusterId) : undefined}
              onValueChange={(v) => setSelectedCluster(Number(v))}
            >
              <SelectTrigger className="w-52 h-9 text-sm">
                <SelectValue placeholder="Select a cluster" />
              </SelectTrigger>
              <SelectContent>
                {clusters.map((c) => (
                  <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* Time range */}
            <TimeRangePicker value={timeRange} onChange={setTimeRange} />

            {/* Refresh */}
            <Button
              variant="outline"
              size="sm"
              className="h-9 w-9 p-0"
              onClick={() => void refetchAll()}
              disabled={isLoading}
              title="Refresh"
            >
              <RefreshCw className={cn('h-4 w-4', isLoading && 'animate-spin')} />
            </Button>
          </div>
        }
      />

      {/* No cluster */}
      {!clusterId && (
        <EmptyState
          icon={<Shield className="h-16 w-16" />}
          title="No cluster selected"
          description="Select a cluster above to view WAF security analytics."
        />
      )}

      {/* ClickHouse unavailable */}
      {clusterId && !statusLoading && unavailableReason && (
        <UnavailableBanner reason={unavailableReason} />
      )}

      {/* Main dashboard content */}
      {clusterId && (available || statusLoading) && (
        <div className="space-y-6">

          {/* ── KPI row ─────────────────────────────────────────────────── */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <KpiCard
              label="Total Events"
              value={summaryPending ? '…' : fmtNumber(summary?.total ?? 0)}
              sub={`Last ${timeRange}`}
              icon={<Activity className="h-4 w-4" />}
              loading={summaryPending}
            />
            <KpiCard
              label="Block Rate"
              value={summaryPending ? '…' : `${summary?.rejected_pct ?? 0}%`}
              sub={summaryPending ? undefined : `${fmtNumber(summary?.rejected ?? 0)} blocked`}
              icon={<ShieldOff className="h-4 w-4" />}
              accent={
                (summary?.rejected_pct ?? 0) > 80 ? 'destructive' :
                (summary?.rejected_pct ?? 0) > 40 ? 'warning' : 'success'
              }
              loading={summaryPending}
            />
            <KpiCard
              label="Top Attack"
              value={summaryPending ? '…' : (summary?.top_attack_type ?? '—')}
              sub={summaryPending ? undefined : `${fmtNumber(summary?.top_attack_count ?? 0)} events`}
              icon={<AlertTriangle className="h-4 w-4" />}
              accent="warning"
              loading={summaryPending}
            />
            <KpiCard
              label="Unique Source IPs"
              value={summaryPending ? '…' : fmtNumber(summary?.unique_ips ?? 0)}
              icon={<Globe className="h-4 w-4" />}
              loading={summaryPending}
            />
          </div>

          {/* ── Traffic trend ────────────────────────────────────────────── */}
          <Panel title="Traffic Trend — Events by Outcome">
            {trendPending ? (
              <Skeleton className="h-56 w-full" />
            ) : !trend?.series.length ? (
              <div className="flex items-center justify-center h-56 text-xs text-muted-foreground">
                No data for this time range.
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={trend.series} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                  <XAxis
                    dataKey="ts"
                    tickFormatter={(ts) => fmtTs(String(ts), trend.bucket_hours, RANGE_HOURS[timeRange] ?? 24)}
                    tick={{ fill: CHART_TEXT, fontSize: 10 }}
                    minTickGap={48}
                  />
                  <YAxis tick={{ fill: CHART_TEXT, fontSize: 10 }} width={40} />
                  <RechartsTooltip
                    contentStyle={CHART_TOOLTIP}
                    labelFormatter={(ts) => fmtTs(String(ts), trend.bucket_hours, RANGE_HOURS[timeRange] ?? 24)}
                  />
                  <Area type="monotone" dataKey="REJECTED" stackId="1" stroke={REJECTED_COLOR} fill={REJECTED_COLOR} fillOpacity={0.8} />
                  <Area type="monotone" dataKey="ALERTED"  stackId="1" stroke={ALERTED_COLOR}  fill={ALERTED_COLOR}  fillOpacity={0.8} />
                  <Area type="monotone" dataKey="PASSED"   stackId="1" stroke={PASSED_COLOR}   fill={PASSED_COLOR}   fillOpacity={0.8} />
                </AreaChart>
              </ResponsiveContainer>
            )}
            {/* Legend */}
            {!trendPending && !!trend?.series.length && (
              <div className="flex items-center gap-4 mt-2 justify-end">
                {[['REJECTED', REJECTED_COLOR], ['ALERTED', ALERTED_COLOR], ['PASSED', PASSED_COLOR]].map(([label, color]) => (
                  <span key={label} className="flex items-center gap-1 text-xs text-muted-foreground">
                    <span className="inline-block h-2 w-3 rounded-sm" style={{ background: color }} />
                    {label}
                  </span>
                ))}
              </div>
            )}
          </Panel>

          {/* ── Bottom two-column row ────────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">

            {/* Top Attack Types */}
            <Panel title="Top Attack Types">
              {attacksPending ? (
                <Skeleton className="h-64 w-full" />
              ) : !attacks?.items.length ? (
                <div className="flex items-center justify-center h-64 text-xs text-muted-foreground">No attack data.</div>
              ) : (
                <ResponsiveContainer width="100%" height={Math.max(200, attacks.items.length * 36)}>
                  <BarChart
                    data={attacks.items}
                    layout="vertical"
                    margin={{ top: 0, right: 48, left: 8, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} horizontal={false} />
                    <XAxis type="number" tick={{ fill: CHART_TEXT, fontSize: 10 }} />
                    <YAxis
                      type="category"
                      dataKey="attack_type"
                      width={130}
                      tick={{ fill: CHART_TEXT, fontSize: 10 }}
                    />
                    <RechartsTooltip
                      contentStyle={CHART_TOOLTIP}
                      formatter={(v, _name, entry) => [
                        `${v} (${(entry.payload as { pct: number }).pct}%)`,
                        'Events',
                      ]}
                    />
                    <Bar dataKey="count" radius={[0, 3, 3, 0]}>
                      {attacks.items.map((_item, i: number) => (
                        <Cell key={i} fill={ATTACK_PALETTE[i % ATTACK_PALETTE.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </Panel>

            {/* Top Source IPs */}
            <Panel title="Top Source IPs (by blocked hits)">
              {ipsPending ? (
                <Skeleton className="h-64 w-full" />
              ) : !ips?.items.length ? (
                <div className="flex items-center justify-center h-64 text-xs text-muted-foreground">No IP data.</div>
              ) : (
                <ResponsiveContainer width="100%" height={Math.max(200, ips.items.length * 36)}>
                  <BarChart
                    data={ips.items}
                    layout="vertical"
                    margin={{ top: 0, right: 48, left: 8, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} horizontal={false} />
                    <XAxis type="number" tick={{ fill: CHART_TEXT, fontSize: 10 }} />
                    <YAxis
                      type="category"
                      dataKey="ip"
                      width={110}
                      tick={{ fill: CHART_TEXT, fontSize: 10 }}
                    />
                    <RechartsTooltip
                      contentStyle={CHART_TOOLTIP}
                      formatter={(v, name) => [v, name === 'blocked_hits' ? 'Blocked' : 'Total']}
                    />
                    <Bar dataKey="blocked_hits" fill={REJECTED_COLOR} radius={[0, 3, 3, 0]} name="Blocked" />
                    <Bar dataKey="total_hits"   fill="#94a3b8"        radius={[0, 3, 3, 0]} name="Total" />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </Panel>
          </div>

          {/* ── Top URIs table ───────────────────────────────────────────── */}
          <Panel title="Top Attacked URIs">
            {urisPending ? (
              <Skeleton className="h-40 w-full" />
            ) : !uris?.items.length ? (
              <div className="flex items-center justify-center h-24 text-xs text-muted-foreground">No URI data.</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground">
                      <th className="text-left pb-2 pr-4 font-medium w-1/2">URI</th>
                      <th className="text-right pb-2 px-4 font-medium">Hits</th>
                      <th className="text-right pb-2 px-4 font-medium">Blocked</th>
                      <th className="text-left pb-2 pl-4 font-medium">Attack Types</th>
                      <th className="text-right pb-2 font-medium">Last Seen</th>
                    </tr>
                  </thead>
                  <tbody>
                    {uris.items.map((u: DashboardTopUris['items'][number], i: number) => (
                      <tr key={i} className="border-b border-border/50 last:border-0 hover:bg-muted/30">
                        <td className="py-2 pr-4 font-mono text-foreground truncate max-w-xs" title={u.uri}>
                          {u.uri}
                        </td>
                        <td className="py-2 px-4 text-right tabular-nums">{fmtNumber(u.count)}</td>
                        <td className="py-2 px-4 text-right tabular-nums text-destructive font-medium">
                          {fmtNumber(u.rejected)}
                        </td>
                        <td className="py-2 pl-4">
                          <div className="flex flex-wrap gap-1">
                            {u.attack_types.filter(t => t !== 'N/A').slice(0, 3).map((t) => (
                              <Badge key={t} variant="outline" className="text-[9px] px-1.5 py-0 h-4 bg-warning/10 text-warning border-warning/20">
                                {t}
                              </Badge>
                            ))}
                          </div>
                        </td>
                        <td className="py-2 text-right text-muted-foreground whitespace-nowrap">
                          {u.last_seen ? new Date(u.last_seen).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

        </div>
      )}
    </div>
  );
}
