import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, LayoutDashboard, List, Hash, Bot, SlidersHorizontal, Pencil, Trash2, Check, X } from 'lucide-react';
import { WafPanelRenderer } from '@/components/k8s/waf/WafPanelRenderer';
import { WafPanelBuilderModal } from '@/components/k8s/waf/WafPanelBuilderModal';
import { SecurityLogsTab } from '@/components/k8s/waf/SecurityLogsTab';
import { InfoTooltip, InfoTooltipQueryWindowProvider } from '@/components/k8s/waf/InfoTooltip';
import { useWafPanels, useDeleteWafPanel } from '@/hooks/useWafPanels';
import {
  useWafDashboardTabs,
  useCreateWafDashboardTab,
  useRenameWafDashboardTab,
  useDeleteWafDashboardTab,
} from '@/hooks/useWafDashboardTabs';
import type { WafPanel } from '@/lib/api/waf-panels';
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  PieChart,
  Pie,
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
  Search,
  ArrowRight,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Separator } from '@/components/ui/separator';
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
  useWafDashboardTopPolicies,
  useWafDashboardRequestMethods,
  useWafDashboardSeverity,
  useWafDashboardTopSignatures,
  useWafDashboardTopInstances,
  REFRESH_OPTIONS,
  type RefreshIntervalMs,
} from '@/hooks/useWafDashboard';
import type {
  TimeRange,
  DashboardSummary,
  DashboardTrend,
  DashboardTopAttacks,
  DashboardTopIps,
  DashboardTopUris,
  DashboardTopPolicies,
  DashboardRequestMethods,
  DashboardSeverity,
  DashboardTopSignatures,
  DashboardTopInstances,
} from '@/lib/api/waf-dashboard';
import { CHART_GRID, CHART_TEXT, CHART_TOOLTIP } from '@/components/observability/chart-theme';

// ── Panel tooltip copy — sourced from NIM's Security Dashboard help text so our
// panels carry the same explanatory context, adapted for BNK-Forge/ClickHouse data.
const HELP = {
  allWebAttacks: 'The total number of requests triggering WAF violations. Requests may be excluded by applying filter criteria.',
  botAttacks: 'The number of bot attacks (also shown as a percentage of total attacks). A bot attack is the use of automated web requests to manipulate, defraud, or disrupt a website, application, API, or end-users. Requires Bot Signatures to be installed.',
  threatIntelligence: 'Data collection about possible known threats as identified by threat campaign and signature packages.',
  attackRequestsOverTime: 'The total number of requests over a period of time, classified into WAF request status categories.',
  topAttackGeolocations: 'Map-view showing threat origin. Requires IP geolocation lookup (not yet wired up).',
  topWafPolicies: 'Top WAF policies with the most requests triggering WAF violations. Select a policy to view more details or use the row actions to apply it as a filter.',
  topAttackIps: 'Top IP addresses with the most requests triggering WAF violations. Select an IP address to view more details or use the row actions to apply it as a filter.',
  topViolations: 'Top violations (attack types) with the most requests triggering WAF violations. Select a violation to view more details or use the row actions to apply it as a filter.',
  topSignatures: 'Top signatures based on the number of requests triggering WAF attack signatures. Select a Signature Name to view more details or use the row actions to apply it as a filter.',
  topSubviolations: 'Top sub-violations with the most requests triggering WAF sub-violations. Requires sub-violation-level ingestion (not yet captured).',
  topAttackUris: 'Top URIs targeted by the most requests triggering WAF violations. Select a URI to view more details or use the row actions to apply it as a filter.',
  requestMethods: 'Proportional representation of request methods used as a part of requests triggering WAF violations.',
  responseCodes: 'Proportional representation of HTTP response codes used as part of requests triggering WAF violations. Requires response_code ingestion (not yet captured).',
  severity: 'Proportional representation of severity classifications WAF placed on requests triggering WAF violations. Severity represents the maximum severity calculated from all triggered violations found in a request.',
  botAttackRequestStatus: 'Proportional representation of all requests which WAF identifies as a bot attack, classified into WAF request status categories.',
  botHitsOverTime: 'Requests seen over a period of time that WAF has classified as possible bot attacks.',
  topBotCategories: 'Top bot categories based on the number of requests triggering WAF violations. Categorization is based on the identification of bots matching specific patterns.',
  topBotClasses: 'The classification of client traffic based on verification from WAF.',
  topBotSignatures: 'Bot category based on identification of bots matching specific signatures.',
  botApplications: 'Proportional representation of requests from applications using a bot.',
  topAttackedInstances: 'Top virtual servers based on the number of requests triggering WAF violations. Select an instance to view more details or use the row actions to apply it as a filter.',
  topSignatureCves: 'Top Signature Common Vulnerabilities and Exposures (CVEs) matched by requests triggering WAF attack signatures. Requires CVE metadata in signature packages (not yet captured).',
  topThreatCampaigns: 'Top threat campaigns based on the total number of requests processed by WAF. Requires threat campaign package matching (not yet captured).',
  violationContext: 'Context of signature location in the attack request. Requires per-parameter violation context (not yet captured).',
  outcomes: 'Breakdown of WAF request outcomes: Blocked (rejected), Alerted (logged only), Passed (no violation).',
} as const;

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

// ── Support ID lookup ────────────────────────────────────────────────────────
function SupportIdTab({ clusterId }: { clusterId: number }) {
  const [query, setQuery] = useState('');
  const [submitted, setSubmitted] = useState('');
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSearch = async () => {
    const id = query.trim();
    if (!id) return;
    setSubmitted(id);
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const token = localStorage.getItem('token') ?? sessionStorage.getItem('token') ?? '';
      const resp = await fetch(
        `/api/k8s/clusters/${clusterId}/waf/dashboard/support-id?support_id=${encodeURIComponent(id)}`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      const json = await resp.json() as Record<string, unknown>;
      if (!resp.ok) throw new Error((json.detail as string) ?? 'Not found');
      setResult(json);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No event found for this Support ID.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <p className="text-xs text-muted-foreground mb-3">
          Enter a WAF Support ID (from the "Request Rejected" page) to look up the full event details in ClickHouse.
        </p>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Hash className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void handleSearch()}
              placeholder="Enter a Support ID..."
              className="pl-9 font-mono text-sm"
            />
          </div>
          <button
            onClick={() => void handleSearch()}
            disabled={loading || !query.trim()}
            className={cn(
              'inline-flex items-center gap-1.5 px-4 rounded-md text-sm font-medium h-9 border transition-colors',
              'bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50',
            )}
          >
            {loading ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            Lookup
          </button>
        </div>
      </div>

      {error && submitted && (
        <div className="rounded-lg border border-border bg-card p-4">
          <p className="text-xs font-mono text-muted-foreground mb-1">{submitted}</p>
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {result && (
        <div className="rounded-lg border border-border bg-card">
          <div className="px-4 py-3 border-b border-border flex items-center gap-2">
            <Hash className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-xs font-mono text-muted-foreground">{submitted}</span>
            <Badge variant="outline" className={cn('text-[10px] ml-auto',
              (result.outcome as string) === 'REJECTED' ? 'bg-destructive/10 text-destructive border-destructive/30'
              : (result.outcome as string) === 'ALERTED' ? 'bg-warning/10 text-warning border-warning/30'
              : 'bg-success/10 text-success border-success/30'
            )}>
              {(result.outcome as string) ?? '—'}
            </Badge>
          </div>
          <div className="p-4 grid grid-cols-2 gap-x-8 gap-y-2 text-xs">
            {([
              ['Timestamp',    result.ts],
              ['Attack Type',  result.attack_type],
              ['Source IP',    result.ip_client],
              ['Method',       result.method],
              ['URI',          result.uri],
              ['Policy',       result.policy_name],
              ['VS',           result.vs_name],
              ['Violation Rating', result.violation_rating],
              ['Sig IDs',      result.sig_ids],
              ['Sig Names',    result.sig_names],
            ] as [string, unknown][]).map(([k, v]) => (
              <div key={k}>
                <span className="text-muted-foreground">{k}: </span>
                <span className="font-medium break-all">{String(v ?? '—')}</span>
              </div>
            ))}
          </div>
          {result.raw_message != null && (
            <>
              <Separator />
              <div className="p-4">
                <p className="text-xs text-muted-foreground mb-1.5">Raw Message</p>
                <pre className="text-[10px] font-mono text-foreground/80 whitespace-pre-wrap break-all bg-muted/30 rounded p-3">{String(result.raw_message)}</pre>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

const RANGE_HOURS: Record<string, number> = { '1h': 1, '24h': 24, '7d': 168, '30d': 720 };
type DashTab = 'main' | 'bots' | 'advanced' | 'event-logs' | 'custom' | 'support-id';

// NIM-style mini stat table (used for Request Methods, Response Codes, Severity)
function MiniStatTable({ rows, valueLabel = 'Count' }: {
  rows: { label: string; value: number | string; accent?: string }[];
  valueLabel?: string;
}) {
  const total = rows.reduce((s, r) => s + (typeof r.value === 'number' ? r.value : 0), 0);
  return rows.length === 0 ? (
    <p className="text-xs text-muted-foreground py-2">No data</p>
  ) : (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-border text-muted-foreground">
          <th className="text-left pb-1.5 font-medium">Label</th>
          <th className="text-right pb-1.5 font-medium">{valueLabel}</th>
          <th className="text-right pb-1.5 font-medium pl-4">%</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.label} className="border-b border-border/40 last:border-0">
            <td className={cn('py-1.5 font-medium', r.accent)}>{r.label}</td>
            <td className="py-1.5 text-right tabular-nums">{fmtNumber(typeof r.value === 'number' ? r.value : 0)}</td>
            <td className="py-1.5 text-right text-muted-foreground tabular-nums pl-4">
              {total > 0 ? `${Math.round((typeof r.value === 'number' ? r.value : 0) / total * 100)}%` : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// NIM-style donut chart + legend (used for Request Methods, Severity)
function MiniPieChart({ rows, valueLabel = 'Count' }: {
  rows: { label: string; value: number; color: string }[];
  valueLabel?: string;
}) {
  const total = rows.reduce((s, r) => s + r.value, 0);
  return rows.length === 0 ? (
    <p className="text-xs text-muted-foreground py-2">No data</p>
  ) : (
    <div className="flex items-center gap-4">
      <div className="h-28 w-28 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={rows} dataKey="value" nameKey="label" innerRadius={26} outerRadius={46} paddingAngle={2} strokeWidth={0}>
              {rows.map((r) => <Cell key={r.label} fill={r.color} />)}
            </Pie>
            <RechartsTooltip formatter={(v) => [fmtNumber(v as number), valueLabel]} />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ul className="flex-1 min-w-0 space-y-1.5 text-xs">
        {rows.map((r) => (
          <li key={r.label} className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full shrink-0" style={{ background: r.color }} />
            <span className="font-medium truncate">{r.label}</span>
            <span className="ml-auto tabular-nums text-muted-foreground">{fmtNumber(r.value)}</span>
            <span className="tabular-nums text-muted-foreground/70 w-9 text-right">{total > 0 ? `${Math.round(r.value / total * 100)}%` : '—'}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

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
  help?: string;
}

function KpiCard({ label, value, sub, icon, accent = 'default', loading, help }: KpiCardProps) {
  const accentClass = {
    destructive: 'text-destructive',
    warning:     'text-warning',
    success:     'text-success',
    default:     'text-foreground',
  }[accent];

  return (
    <div className="rounded-lg border border-border bg-card p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
          {label}
          {help && <InfoTooltip title={label} text={help} />}
        </span>
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
function Panel({ title, help, children, className, actions, footer }: { title: string; help?: string; children: React.ReactNode; className?: string; actions?: React.ReactNode; footer?: { label: string; onClick: () => void } }) {
  return (
    <div className={cn('rounded-lg border border-border bg-card flex flex-col', className)}>
      <div className="px-4 py-3 border-b border-border flex items-center gap-1.5">
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        {help && <InfoTooltip title={title} text={help} />}
        {actions && <div className="ml-auto flex items-center gap-1">{actions}</div>}
      </div>
      <div className="p-4 flex-1">{children}</div>
      {footer && (
        <button
          onClick={footer.onClick}
          className="flex items-center gap-1 px-4 py-2 border-t border-border text-xs font-medium text-primary hover:underline"
        >
          {footer.label}
          <ArrowRight className="h-3 w-3" />
        </button>
      )}
    </div>
  );
}

// ── Placeholder card for NIM panels we don't yet have real data for ─────────
function PlaceholderCard({ title, help, reason }: { title: string; help: string; reason: string }) {
  return (
    <Panel title={title} help={help}>
      <div className="flex flex-col items-center justify-center py-8 gap-2 text-center">
        <LayoutDashboard className="h-7 w-7 text-muted-foreground/25" />
        <p className="text-xs text-muted-foreground max-w-xs">{reason}</p>
      </div>
    </Panel>
  );
}

// ── "Custom panels available" notice — shown at the bottom of every analytics tab ───
function CustomTabNotice({ onNavigate }: { onNavigate: () => void }) {
  return (
    <div className="rounded-md border border-dashed border-border bg-muted/20 flex items-center gap-3 px-4 py-3">
      <LayoutDashboard className="h-4 w-4 text-muted-foreground/50 shrink-0" />
      <p className="text-xs text-muted-foreground">
        Custom analytics panels are available in the{' '}
        <button onClick={onNavigate} className="underline hover:text-foreground transition-colors">Custom tab</button>.
      </p>
      <Button size="sm" variant="ghost" className="ml-auto gap-1.5 h-7 text-xs" onClick={onNavigate}>
        <Plus className="h-3 w-3" /> Add Panel
      </Button>
    </div>
  );
}

// ── Clickable "apply as filter" cell — underline-on-hover link style ────────
function LinkCell({ children, onClick, title }: { children: React.ReactNode; onClick: () => void; title?: string }) {
  return (
    <button
      onClick={onClick}
      title={title ?? 'Click to filter Event Logs'}
      className="text-left hover:underline hover:text-primary transition-colors truncate max-w-full"
    >
      {children}
    </button>
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
  const navigate = useNavigate();
  const { data: clustersData } = useAllClusters();
  const clusters = clustersData?.clusters ?? [];

  const [selectedCluster, setSelectedCluster] = useState<number | null>(null);
  const [timeRange, setTimeRange]       = useState<TimeRange>('7d');
  const [intervalMs, setIntervalMs]     = useState<RefreshIntervalMs>(60_000);
  const [countdown, setCountdown]       = useState(0);
  const [editMode, setEditMode]         = useState(false);
  const [builderOpen, setBuilderOpen]   = useState(false);
  const [editingPanel, setEditingPanel] = useState<WafPanel | null>(null);
  const [dashTab, setDashTab]           = useState<DashTab>('main');
  const [trendView, setTrendView]        = useState<'Hits' | 'Blocked'>('Hits');
  // Which user-defined custom tab is active (id, or 0 for the default/legacy "Custom" tab)
  const [activeCustomTabId, setActiveCustomTabId] = useState<number>(0);
  const [renamingTabId, setRenamingTabId]         = useState<number | null>(null);
  const [renameValue, setRenameValue]             = useState('');
  const [addingTab, setAddingTab]                 = useState(false);
  const [newTabName, setNewTabName]               = useState('');
  // Click-through filters — set when a dashboard row is clicked, consumed by the Event Logs tab
  const [eventLogsFilter, setEventLogsFilter] = useState<{ attack?: string; ip?: string; uri?: string } | null>(null);

  function goToEventLogs(filter: { attack?: string; ip?: string; uri?: string }) {
    setEventLogsFilter(filter);
    setDashTab('event-logs');
  }

  // Countdown timer — ticks every second when auto-refresh is on
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (countdownRef.current) clearInterval(countdownRef.current);
    if (intervalMs === 0) { setCountdown(0); return; }
    setCountdown(Math.round(intervalMs / 1000));
    countdownRef.current = setInterval(() => {
      setCountdown(prev => {
        if (prev <= 1) return Math.round(intervalMs / 1000);
        return prev - 1;
      });
    }, 1000);
    return () => { if (countdownRef.current) clearInterval(countdownRef.current); };
  }, [intervalMs]);

  const clusterId = selectedCluster ?? clusters[0]?.id ?? null;

  const { data: statusData,  isLoading: statusLoading,  refetch: refetchStatus  } = useWafDashboardStatus(clusterId, intervalMs);
  const { data: summaryData, isLoading: summaryLoading, isFetching: summaryFetching, refetch: refetchSummary } = useWafDashboardSummary(clusterId, timeRange, intervalMs);
  const { data: trendData,   isLoading: trendLoading,                                refetch: refetchTrend   } = useWafDashboardTrend(clusterId, timeRange, intervalMs);
  const { data: attacksData, isLoading: attacksLoading,                              refetch: refetchAttacks } = useWafDashboardTopAttacks(clusterId, timeRange, intervalMs);
  const { data: ipsData,     isLoading: ipsLoading,                                  refetch: refetchIps     } = useWafDashboardTopIps(clusterId, timeRange, intervalMs);
  const { data: urisData,    isLoading: urisLoading,                                 refetch: refetchUris    } = useWafDashboardTopUris(clusterId, timeRange, intervalMs);

  const { data: customPanels = [] } = useWafPanels(clusterId, activeCustomTabId);
  const deletePanel = useDeleteWafPanel(clusterId ?? 0);

  const { data: customTabs = [] } = useWafDashboardTabs(clusterId);
  const createTab = useCreateWafDashboardTab(clusterId ?? 0);
  const renameTab = useRenameWafDashboardTab(clusterId ?? 0);
  const deleteTab = useDeleteWafDashboardTab(clusterId ?? 0);

  // Default to the first custom tab once tabs have loaded; fall back to "no tab" (0) if none remain
  useEffect(() => {
    if (customTabs.length === 0) {
      if (activeCustomTabId !== 0) setActiveCustomTabId(0);
      return;
    }
    if (!customTabs.some(t => t.id === activeCustomTabId)) {
      setActiveCustomTabId(customTabs[0].id);
    }
  }, [customTabs, activeCustomTabId]);

  async function handleAddTab() {
    if (!newTabName.trim()) return;
    const tab = await createTab.mutateAsync(newTabName.trim());
    setActiveCustomTabId(tab.id);
    setNewTabName('');
    setAddingTab(false);
    setDashTab('custom');
  }

  function startRename(id: number, currentName: string) {
    setRenamingTabId(id);
    setRenameValue(currentName);
  }

  async function commitRename() {
    if (renamingTabId !== null && renameValue.trim()) {
      await renameTab.mutateAsync({ id: renamingTabId, name: renameValue.trim() });
    }
    setRenamingTabId(null);
  }

  async function handleDeleteTab(id: number) {
    if (!window.confirm('Delete this tab and all of its panels?')) return;
    await deleteTab.mutateAsync(id);
    if (activeCustomTabId === id) {
      setActiveCustomTabId(customTabs.find(t => t.id !== id)?.id ?? 0);
    }
  }

  // New NIM-equivalent panels
  const { data: topPoliciesData, isLoading: topPoliciesLoading } = useWafDashboardTopPolicies(clusterId, timeRange, intervalMs);
  const { data: methodsData,     isLoading: methodsLoading     } = useWafDashboardRequestMethods(clusterId, timeRange, intervalMs);
  const { data: severityData,    isLoading: severityLoading     } = useWafDashboardSeverity(clusterId, timeRange, intervalMs);
  const { data: signaturesData,  isLoading: signaturesLoading   } = useWafDashboardTopSignatures(clusterId, timeRange, intervalMs);
  const { data: instancesData,   isLoading: instancesLoading    } = useWafDashboardTopInstances(clusterId, timeRange, intervalMs);

  function openCreate() { setEditingPanel(null); setBuilderOpen(true); }
  function openEdit(p: WafPanel) { setEditingPanel(p); setBuilderOpen(true); }

  const refetchAll = () => {
    void refetchStatus();
    void refetchSummary();
    void refetchTrend();
    void refetchAttacks();
    void refetchIps();
    void refetchUris();
    // Reset countdown so it reflects a fresh cycle
    if (intervalMs > 0) setCountdown(Math.round(intervalMs / 1000));
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

  // Query window shown in every panel's info tooltip (Start/End/Filters, NIM-style)
  const RANGE_MS: Record<TimeRange, number> = { '1h': 36e5, '24h': 864e5, '7d': 6048e5, '30d': 2592e6 };
  const queryWindow = {
    start: new Date(Date.now() - RANGE_MS[timeRange]),
    end: new Date(),
    filters: eventLogsFilter
      ? Object.entries(eventLogsFilter).filter(([, v]) => v).map(([k, v]) => `${k}=${v}`).join(', ')
      : 'None',
  };

  return (
    <InfoTooltipQueryWindowProvider value={queryWindow}>
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

            {/* Auto-refresh selector with live countdown */}
            <div className="flex items-center gap-1.5 rounded-md border border-border bg-muted/30 px-2 h-9 text-xs">
              <span className="text-muted-foreground shrink-0">Refresh:</span>
              <select
                value={intervalMs}
                onChange={(e) => setIntervalMs(Number(e.target.value) as RefreshIntervalMs)}
                className="bg-transparent text-foreground text-xs focus:outline-none cursor-pointer pr-1"
              >
                {REFRESH_OPTIONS.map((o) => (
                  <option key={o.ms} value={o.ms}>{o.label}</option>
                ))}
              </select>
              {intervalMs > 0 && (
                <span className="text-muted-foreground shrink-0 tabular-nums w-7 text-right">{countdown}s</span>
              )}
            </div>

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

      {/* NIM-style tab bar — shown once cluster is selected */}
      {clusterId && (
        <div className="border-b border-border -mt-2">
          <div className="flex gap-0 items-center overflow-x-auto">
            {([
              { key: 'main',       label: 'Main',       icon: Activity },
              { key: 'bots',       label: 'Bots',       icon: Bot },
              { key: 'advanced',   label: 'Advanced',   icon: SlidersHorizontal },
              { key: 'event-logs', label: 'Event Logs', icon: List },
              { key: 'support-id', label: 'Support ID', icon: Hash },
            ] as { key: DashTab; label: string; icon: typeof Activity }[]).map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                onClick={() => setDashTab(key)}
                className={cn(
                  'flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px whitespace-nowrap',
                  dashTab === key
                    ? 'border-primary text-foreground'
                    : 'border-transparent text-muted-foreground hover:text-foreground hover:border-border',
                )}
              >
                <Icon className="h-3.5 w-3.5" /> {label}
              </button>
            ))}

            {/* User-defined custom tabs — each holds its own set of custom panels */}
            {customTabs.map((tab) => {
              const isActive = dashTab === 'custom' && activeCustomTabId === tab.id;
              const isRenaming = renamingTabId === tab.id;
              return (
                <div
                  key={tab.id}
                  className={cn(
                    'flex items-center gap-1 pl-3 pr-1.5 py-1.5 border-b-2 -mb-px whitespace-nowrap',
                    isActive ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground hover:border-border',
                  )}
                >
                  {isRenaming ? (
                    <Input
                      autoFocus
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') void commitRename(); if (e.key === 'Escape') setRenamingTabId(null); }}
                      onBlur={() => void commitRename()}
                      className="h-6 w-28 text-xs px-2"
                    />
                  ) : (
                    <button
                      onClick={() => { setDashTab('custom'); setActiveCustomTabId(tab.id); }}
                      className="flex items-center gap-1.5 text-sm font-medium"
                    >
                      <LayoutDashboard className="h-3.5 w-3.5" /> {tab.name}
                    </button>
                  )}
                  {isActive && !isRenaming && (
                    <>
                      <button onClick={() => startRename(tab.id, tab.name)} className="p-1 text-muted-foreground/60 hover:text-foreground" title="Rename tab">
                        <Pencil className="h-3 w-3" />
                      </button>
                      <button onClick={() => void handleDeleteTab(tab.id)} className="p-1 text-muted-foreground/60 hover:text-destructive" title="Delete tab">
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </>
                  )}
                </div>
              );
            })}

            {/* Add-tab affordance */}
            {addingTab ? (
              <div className="flex items-center gap-1 px-2 py-1">
                <Input
                  autoFocus
                  value={newTabName}
                  onChange={(e) => setNewTabName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') void handleAddTab(); if (e.key === 'Escape') { setAddingTab(false); setNewTabName(''); } }}
                  placeholder="Tab name…"
                  className="h-7 w-32 text-xs px-2"
                />
                <button onClick={() => void handleAddTab()} className="p-1 text-success hover:text-success/80" title="Create tab">
                  <Check className="h-3.5 w-3.5" />
                </button>
                <button onClick={() => { setAddingTab(false); setNewTabName(''); }} className="p-1 text-muted-foreground hover:text-foreground" title="Cancel">
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ) : (
              <button
                onClick={() => setAddingTab(true)}
                className="flex items-center gap-1 px-3 py-2.5 text-xs font-medium text-muted-foreground hover:text-foreground shrink-0"
                title="Add a new custom tab"
              >
                <Plus className="h-3.5 w-3.5" /> Add Tab
              </button>
            )}
          </div>
        </div>
      )}

      {/* ClickHouse unavailable */}
      {clusterId && !statusLoading && unavailableReason && (
        <UnavailableBanner reason={unavailableReason} />
      )}

      {/* Main dashboard content — Main tab */}
      {clusterId && dashTab === 'main' && (available || statusLoading) && (
        <div className="space-y-6">

          {/* ── KPI row ─────────────────────────────────────────────────── */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <KpiCard
              label="Total Events"
              value={summaryPending ? '…' : fmtNumber(summary?.total ?? 0)}
              sub={`Last ${timeRange}`}
              icon={<Activity className="h-4 w-4" />}
              loading={summaryPending}
              help="The total number of requests processed by the WAF over the selected time range, including both attack and non-attack traffic."
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
              help="The percentage of total requests that were blocked (rejected) by the WAF policy's enforcement mode."
            />
            <KpiCard
              label="Top Attack"
              value={summaryPending ? '…' : (summary?.top_attack_type ?? '—')}
              sub={summaryPending ? undefined : `${fmtNumber(summary?.top_attack_count ?? 0)} events`}
              icon={<AlertTriangle className="h-4 w-4" />}
              accent="warning"
              loading={summaryPending}
              help="The attack (violation) type with the highest number of triggered events in the selected time range."
            />
            <KpiCard
              label="Unique Source IPs"
              value={summaryPending ? '…' : fmtNumber(summary?.unique_ips ?? 0)}
              icon={<Globe className="h-4 w-4" />}
              loading={summaryPending}
              help="The number of distinct client IP addresses that sent requests in the selected time range."
            />
          </div>

          {/* ── NIM parity row: All Web Attacks / Bot Attacks / Threat Intelligence ── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Panel title="All Web Attacks" help={HELP.allWebAttacks} footer={{ label: 'View Event Logs', onClick: () => goToEventLogs({}) }}>
              {summaryPending ? <Skeleton className="h-20 w-full" /> : (
                <div className="flex items-end gap-3">
                  <span className="text-3xl font-bold tabular-nums text-destructive">{fmtNumber(summary?.rejected ?? 0)}</span>
                  <span className="text-xs text-muted-foreground pb-1">Attack Requests</span>
                  <span className="text-xs text-muted-foreground pb-1 ml-auto">{fmtNumber((summary?.total ?? 0) - (summary?.rejected ?? 0))} Non-Attack Requests</span>
                </div>
              )}
            </Panel>
            <Panel title="Bot Attacks" help={HELP.botAttacks} footer={{ label: 'View Bot Details', onClick: () => setDashTab('bots') }}>
              {/* Donut right-side layout matching NIM — fills when Bot Signatures are installed */}
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-2xl font-bold tabular-nums text-muted-foreground">0</p>
                  <p className="text-xs text-muted-foreground">Bot Attack Requests</p>
                  <p className="text-xs text-muted-foreground/60 mt-0.5">of 0 Total Attacks</p>
                  <p className="text-xs text-muted-foreground/50 mt-2">Requires Bot Signatures</p>
                </div>
                <div className="relative h-28 w-28 shrink-0">
                  <svg viewBox="0 0 36 36" className="h-28 w-28 -rotate-90">
                    <circle cx="18" cy="18" r="14" fill="none" stroke="currentColor" strokeWidth="4" className="text-muted/20" />
                  </svg>
                  <span className="absolute inset-0 flex flex-col items-center justify-center">
                    <span className="text-sm font-semibold text-muted-foreground">0%</span>
                    <span className="text-[9px] text-muted-foreground/60">of total</span>
                  </span>
                </div>
              </div>
            </Panel>
            <Panel title="Threat Intelligence" help={HELP.threatIntelligence} footer={{ label: 'View Advanced', onClick: () => setDashTab('advanced') }}>
              <div className="flex items-center gap-6">
                <div>
                  <span className="text-2xl font-bold tabular-nums text-muted-foreground">0</span>
                  <p className="text-xs text-muted-foreground">Unique Threat Campaigns</p>
                </div>
                <div>
                  <span className="text-2xl font-bold tabular-nums">
                    {signaturesLoading ? '…' : (signaturesData?.available ? (signaturesData as DashboardTopSignatures).items.length : 0)}
                  </span>
                  <p className="text-xs text-muted-foreground">Unique Signatures</p>
                </div>
              </div>
            </Panel>
          </div>

          {/* ── Traffic trend ────────────────────────────────────────────── */}
          <Panel
            title="Attack Requests Over Time"
            help={HELP.attackRequestsOverTime}
            actions={
              <div className="flex gap-1 rounded-md border border-border bg-muted/30 p-0.5 ml-auto">
                {(['Hits', 'Blocked'] as const).map((v) => (
                  <button
                    key={v}
                    onClick={() => setTrendView(v)}
                    className={cn('px-2 py-0.5 rounded text-[10px] font-medium transition-colors',
                      trendView === v ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground')}
                  >{v}</button>
                ))}
              </div>
            }
          >
            {trendPending ? (
              <Skeleton className="h-56 w-full" />
            ) : !trend?.series.length ? (
              <div className="flex items-center justify-center h-56 text-xs text-muted-foreground">
                No data for this time range.
              </div>
            ) : trendView === 'Blocked' ? (
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={trend.series} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                  <XAxis dataKey="ts" tickFormatter={(ts) => fmtTs(String(ts), trend.bucket_hours, RANGE_HOURS[timeRange] ?? 24)} tick={{ fill: CHART_TEXT, fontSize: 10 }} minTickGap={48} />
                  <YAxis tick={{ fill: CHART_TEXT, fontSize: 10 }} width={40} />
                  <RechartsTooltip contentStyle={CHART_TOOLTIP} labelFormatter={(ts) => fmtTs(String(ts), trend.bucket_hours, RANGE_HOURS[timeRange] ?? 24)} />
                  <Area type="monotone" dataKey="REJECTED" stroke={REJECTED_COLOR} fill={REJECTED_COLOR} fillOpacity={0.8} name="Blocked" />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={trend.series} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
                  <XAxis dataKey="ts" tickFormatter={(ts) => fmtTs(String(ts), trend.bucket_hours, RANGE_HOURS[timeRange] ?? 24)} tick={{ fill: CHART_TEXT, fontSize: 10 }} minTickGap={48} />
                  <YAxis tick={{ fill: CHART_TEXT, fontSize: 10 }} width={40} />
                  <RechartsTooltip contentStyle={CHART_TOOLTIP} labelFormatter={(ts) => fmtTs(String(ts), trend.bucket_hours, RANGE_HOURS[timeRange] ?? 24)} />
                  <Area type="monotone" dataKey="REJECTED" stackId="1" stroke={REJECTED_COLOR} fill={REJECTED_COLOR} fillOpacity={0.8} />
                  <Area type="monotone" dataKey="ALERTED"  stackId="1" stroke={ALERTED_COLOR}  fill={ALERTED_COLOR}  fillOpacity={0.8} />
                  <Area type="monotone" dataKey="PASSED"   stackId="1" stroke={PASSED_COLOR}   fill={PASSED_COLOR}   fillOpacity={0.8} />
                </AreaChart>
              </ResponsiveContainer>
            )}
            {/* Legend */}
            {!trendPending && !!trend?.series.length && (
              <div className="flex items-center gap-4 mt-2 justify-end">
                {trendView === 'Blocked'
                  ? [['Blocked', REJECTED_COLOR]].map(([label, color]) => (
                    <span key={label} className="flex items-center gap-1 text-xs text-muted-foreground">
                      <span className="inline-block h-2 w-3 rounded-sm" style={{ background: color }} />{label}
                    </span>
                  ))
                  : [['REJECTED', REJECTED_COLOR], ['ALERTED', ALERTED_COLOR], ['PASSED', PASSED_COLOR]].map(([label, color]) => (
                    <span key={label} className="flex items-center gap-1 text-xs text-muted-foreground">
                      <span className="inline-block h-2 w-3 rounded-sm" style={{ background: color }} />{label}
                    </span>
                  ))
                }
              </div>
            )}
          </Panel>

          {/* ── Bottom two-column row ────────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">

            {/* Top Attack Geolocations — placeholder (no IP geolocation lookup wired up) */}
            <PlaceholderCard
              title="Top Attack Geolocations"
              help={HELP.topAttackGeolocations}
              reason="Map view requires an IP-to-geolocation lookup service. See Top Attack IP Addresses below for raw source IPs."
            />

            {/* Top Violations (renamed from Top Attack Types to match NIM terminology) */}
            <Panel title="Top Violations" help={HELP.topViolations}>
              {attacksPending ? (
                <Skeleton className="h-64 w-full" />
              ) : !attacks?.items.length ? (
                <div className="flex items-center justify-center h-64 text-xs text-muted-foreground">No violation data.</div>
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
                    <Bar
                      dataKey="count"
                      radius={[0, 3, 3, 0]}
                      cursor="pointer"
                      onClick={(data: unknown) => {
                        const attackType = (data as { attack_type?: string })?.attack_type;
                        if (attackType) goToEventLogs({ attack: attackType });
                      }}
                    >
                      {attacks.items.map((_item, i: number) => (
                        <Cell key={i} fill={ATTACK_PALETTE[i % ATTACK_PALETTE.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </Panel>
          </div>

          {/* ── Top WAF Policies + Top Attack IP Addresses ──────────────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">

            {/* Top WAF Policies */}
            <Panel title="Top WAF Policies" help={HELP.topWafPolicies}>
              {topPoliciesLoading ? <Skeleton className="h-32 w-full" /> : (
                (topPoliciesData?.available && (topPoliciesData as DashboardTopPolicies).items.length > 0) ? (
                  <>
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-border text-muted-foreground">
                          <th className="text-left pb-1.5 font-medium">Policy</th>
                          <th className="text-right pb-1.5 font-medium">Hits</th>
                          <th className="text-right pb-1.5 font-medium">Blocked</th>
                          <th className="text-right pb-1.5 font-medium">URIs</th>
                          <th className="text-right pb-1.5 font-medium">IPs</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(topPoliciesData as DashboardTopPolicies).items.map((p) => (
                          <tr key={p.policy_name} className="border-b border-border/40 last:border-0">
                            <td className="py-1.5 font-mono text-foreground max-w-[180px]">
                              <LinkCell onClick={() => navigate(`/waf-policies?policy=${encodeURIComponent(p.policy_name)}`)} title="View this policy in WAF Policies">
                                {p.policy_name}
                              </LinkCell>
                            </td>
                            <td className="py-1.5 text-right tabular-nums">{fmtNumber(p.hits)}</td>
                            <td className="py-1.5 text-right tabular-nums text-destructive">{fmtNumber(p.blocked)}</td>
                            <td className="py-1.5 text-right tabular-nums text-muted-foreground">{p.unique_uris}</td>
                            <td className="py-1.5 text-right tabular-nums text-muted-foreground">{p.unique_ips}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="mt-2 text-right text-[10px] text-muted-foreground/60">
                      Showing {(topPoliciesData as DashboardTopPolicies).items.length} of {(topPoliciesData as DashboardTopPolicies).items.length} {(topPoliciesData as DashboardTopPolicies).items.length === 1 ? 'policy' : 'policies'}
                    </p>
                  </>
                ) : <p className="text-xs text-muted-foreground py-2">No policy data.</p>
              )}
            </Panel>

            <Panel title="Top Attack IP Addresses" help={HELP.topAttackIps}>
              {ipsPending ? <Skeleton className="h-32 w-full" /> : !ips?.items.length ? (
                <p className="text-xs text-muted-foreground py-2">No IP data.</p>
              ) : (
                <>
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border text-muted-foreground">
                        <th className="text-left pb-1.5 font-medium">IP Address</th>
                        <th className="text-right pb-1.5 font-medium">Total Hits</th>
                        <th className="text-right pb-1.5 font-medium">Blocked</th>
                        <th className="text-right pb-1.5 font-medium">Last Seen</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ips.items.map((ip) => (
                        <tr key={ip.ip} className="border-b border-border/40 last:border-0">
                          <td className="py-1.5 font-mono">
                            <LinkCell onClick={() => goToEventLogs({ ip: ip.ip })} title="View Event Logs filtered by this IP">
                              {ip.ip}
                            </LinkCell>
                          </td>
                          <td className="py-1.5 text-right tabular-nums">{fmtNumber(ip.total_hits)}</td>
                          <td className="py-1.5 text-right tabular-nums text-destructive">{fmtNumber(ip.blocked_hits)}</td>
                          <td className="py-1.5 text-right text-muted-foreground whitespace-nowrap">
                            {ip.last_seen ? new Date(ip.last_seen).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="mt-2 text-right text-[10px] text-muted-foreground/60">
                    Showing {ips.items.length} of {ips.items.length} IP{ips.items.length !== 1 ? 's' : ''}
                  </p>
                </>
              )}
            </Panel>
          </div>

          {/* ── Top URIs table ───────────────────────────────────────────── */}
          <Panel title="Top Attack URIs" help={HELP.topAttackUris}>
            {urisPending ? (
              <Skeleton className="h-40 w-full" />
            ) : !uris?.items.length ? (
              <div className="flex items-center justify-center h-24 text-xs text-muted-foreground">No URI data.</div>
            ) : (
              <>
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
                          <td className="py-2 pr-4 font-mono text-foreground max-w-xs">
                            <LinkCell onClick={() => goToEventLogs({ uri: u.uri })} title="View Event Logs filtered by this URI">
                              {u.uri}
                            </LinkCell>
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
                <p className="mt-2 text-right text-[10px] text-muted-foreground/60">
                  Showing {uris.items.length} of {uris.items.length} URI{uris.items.length !== 1 ? 's' : ''}
                </p>
              </>
            )}
          </Panel>

          {/* ── NIM-equivalent lower panels ─────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">

            {/* Top Subviolations — placeholder (no sub-violation-level ingestion) */}
            <PlaceholderCard
              title="Top Subviolations"
              help={HELP.topSubviolations}
              reason="Sub-violation-level detail isn't captured in the current OTEL → ClickHouse pipeline."
            />

            {/* Request Methods + Severity stacked */}
            <div className="space-y-4">
              <Panel title="Request Methods" help={HELP.requestMethods}>
                {methodsLoading ? <Skeleton className="h-20 w-full" /> : (
                  <MiniPieChart
                    rows={methodsData?.available ? (methodsData as DashboardRequestMethods).items.map((m, i) => ({
                      label: m.method, value: m.count, color: ATTACK_PALETTE[i % ATTACK_PALETTE.length],
                    })) : []}
                    valueLabel="Requests"
                  />
                )}
              </Panel>
              <Panel title="Severity" help={HELP.severity}>
                {severityLoading ? <Skeleton className="h-20 w-full" /> : (
                  <MiniPieChart
                    rows={severityData?.available ? (severityData as DashboardSeverity).items.map(s => ({
                      label: s.label,
                      value: s.count,
                      color: s.rating >= 5 ? REJECTED_COLOR : s.rating >= 4 ? ALERTED_COLOR : s.rating >= 2 ? '#eab308' : '#94a3b8',
                    })) : []}
                    valueLabel="Events"
                  />
                )}
              </Panel>
            </div>

            {/* Response Codes — placeholder (response_code not ingested) */}
            <PlaceholderCard
              title="Response Codes"
              help={HELP.responseCodes}
              reason="HTTP response codes aren't currently captured by the NAP → OTEL pipeline. See Outcomes in the Advanced tab for the closest available breakdown."
            />
          </div>

          {/* ── Top Signatures ──────────────────────────────────────────── */}
          <Panel title="Top Signatures" help={HELP.topSignatures}>
            {signaturesLoading ? <Skeleton className="h-32 w-full" /> : (
              !(signaturesData?.available) || !(signaturesData as DashboardTopSignatures).items.length ? (
                <p className="text-xs text-muted-foreground py-2">No signature data available. Ensure signature packages are installed.</p>
              ) : (
                <>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-border text-muted-foreground">
                          <th className="text-left pb-1.5 font-medium w-1/2">Signature Name</th>
                          <th className="text-right pb-1.5 font-medium">Hits</th>
                          <th className="text-right pb-1.5 font-medium">Blocked</th>
                          <th className="text-right pb-1.5 font-medium">IPs</th>
                          <th className="text-right pb-1.5 font-medium">URIs</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(signaturesData as DashboardTopSignatures).items.map((s, i) => (
                          <tr key={i} className="border-b border-border/40 last:border-0">
                            <td className="py-1.5 max-w-xs font-medium">
                              <LinkCell onClick={() => goToEventLogs({ attack: s.sig_name })} title="View Event Logs filtered by this signature">
                                {s.sig_name}
                              </LinkCell>
                            </td>
                            <td className="py-1.5 text-right tabular-nums">{fmtNumber(s.hits)}</td>
                            <td className="py-1.5 text-right tabular-nums text-destructive">{fmtNumber(s.blocked)}</td>
                            <td className="py-1.5 text-right tabular-nums text-muted-foreground">{s.unique_ips}</td>
                            <td className="py-1.5 text-right tabular-nums text-muted-foreground">{s.unique_uris}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="mt-2 text-right text-[10px] text-muted-foreground/60">
                    Showing {(signaturesData as DashboardTopSignatures).items.length} of {(signaturesData as DashboardTopSignatures).items.length} {(signaturesData as DashboardTopSignatures).items.length === 1 ? 'signature' : 'signatures'}
                  </p>
                </>
              )
            )}
          </Panel>

          {/* ── Custom panel builder section (moved to Custom tab) ──────── */}
          <div className="rounded-md border border-dashed border-border bg-muted/20 flex items-center gap-3 px-4 py-3">
            <LayoutDashboard className="h-4 w-4 text-muted-foreground/50 shrink-0" />
            <p className="text-xs text-muted-foreground">
              Custom analytics panels are available in the{' '}
              <button onClick={() => setDashTab('custom')} className="underline hover:text-foreground transition-colors">Custom tab</button>.
            </p>
            <Button size="sm" variant="ghost" className="ml-auto gap-1.5 h-7 text-xs" onClick={() => setDashTab('custom')}>
              <Plus className="h-3 w-3" /> Add Panel
            </Button>
          </div>

        </div>
      )}

      <WafPanelBuilderModal
        clusterId={clusterId ?? 0}
        panel={editingPanel}
        open={builderOpen}
        onClose={() => { setBuilderOpen(false); setEditingPanel(null); }}
        nextOrder={customPanels.length}
      />

      {/* Bots tab — NIM box titles/tooltips shown honestly as placeholders (no bot-signature data yet) */}
      {clusterId && dashTab === 'bots' && (
        <div className="space-y-4">
          <div className="rounded-md border border-border bg-muted/20 px-4 py-3 flex items-start gap-2">
            <Bot className="h-4 w-4 text-muted-foreground shrink-0 mt-0.5" />
            <p className="text-xs text-muted-foreground">
              Bot-specific telemetry requires Bot Signatures, installed via{' '}
              <button onClick={() => navigate('/waf-policies')} className="underline hover:text-foreground">WAF Policies → Attack Signatures</button>.
              The boxes below mirror the Bots dashboard layout and will populate once bot signature data starts flowing through the pipeline.
            </p>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <PlaceholderCard title="Bot Attack Requests" help={HELP.botAttacks} reason="No bot signature data available yet." />
            <PlaceholderCard title="Bot Attack Request Status" help={HELP.botAttackRequestStatus} reason="No bot signature data available yet." />
          </div>
          <PlaceholderCard title="Bot Hits Over Time" help={HELP.botHitsOverTime} reason="No bot signature data available yet." />
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <PlaceholderCard title="Top Bot Categories" help={HELP.topBotCategories} reason="No bot signature data available yet." />
            <PlaceholderCard title="Top Bot Classes" help={HELP.topBotClasses} reason="No bot signature data available yet." />
            <PlaceholderCard title="Top Bot Signatures" help={HELP.topBotSignatures} reason="No bot signature data available yet." />
          </div>
          <PlaceholderCard title="Bot Applications" help={HELP.botApplications} reason="No bot signature data available yet." />

          {/* Custom tab notice — consistent across all tabs */}
          <CustomTabNotice onNavigate={() => setDashTab('custom')} />
        </div>
      )}

      {/* Advanced tab — deeper analytics matching NIM's Advanced panel */}
      {clusterId && dashTab === 'advanced' && (available || statusLoading) && (
        <div className="space-y-4">
          {/* Top Attacked Instances — real data grouped by vs_name */}
          <Panel title="Top Attacked Instances" help={HELP.topAttackedInstances}>
            {instancesLoading ? <Skeleton className="h-32 w-full" /> : (
              (instancesData?.available && (instancesData as DashboardTopInstances).items.length > 0) ? (
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground">
                      <th className="text-left pb-1.5 font-medium">Virtual Server / Instance</th>
                      <th className="text-right pb-1.5 font-medium">Hits</th>
                      <th className="text-right pb-1.5 font-medium">Blocked</th>
                      <th className="text-right pb-1.5 font-medium">URIs</th>
                      <th className="text-right pb-1.5 font-medium">IPs</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(instancesData as DashboardTopInstances).items.map((inst) => (
                      <tr key={inst.vs_name} className="border-b border-border/40 last:border-0">
                        <td className="py-1.5 font-mono">
                          <LinkCell onClick={() => goToEventLogs({})} title="View Event Logs">{inst.vs_name}</LinkCell>
                        </td>
                        <td className="py-1.5 text-right tabular-nums">{fmtNumber(inst.hits)}</td>
                        <td className="py-1.5 text-right tabular-nums text-destructive">{fmtNumber(inst.blocked)}</td>
                        <td className="py-1.5 text-right tabular-nums text-muted-foreground">{inst.unique_uris}</td>
                        <td className="py-1.5 text-right tabular-nums text-muted-foreground">{inst.unique_ips}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <p className="text-xs text-muted-foreground py-2">No instance data.</p>
            )}
          </Panel>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <PlaceholderCard title="Top Signature CVEs" help={HELP.topSignatureCves} reason="CVE metadata isn't captured by current signature packages." />
            <PlaceholderCard title="Top Threat Campaigns" help={HELP.topThreatCampaigns} reason="Threat campaign matching isn't captured in the current pipeline." />
            <PlaceholderCard title="Violation Context" help={HELP.violationContext} reason="Per-parameter violation context isn't captured in the current pipeline." />
          </div>

          {/* Outcomes — Request Methods/Severity already shown on the Main tab, not duplicated here */}
          <Panel title="Outcomes" help={HELP.outcomes} className="lg:max-w-sm">
            {summaryPending ? <Skeleton className="h-24 w-full" /> : (
              <MiniStatTable rows={summary ? [
                { label: 'Blocked',   value: summary.rejected, accent: 'text-destructive' },
                { label: 'Alerted',   value: summary.alerted,  accent: 'text-warning' },
                { label: 'Passed',    value: summary.passed,   accent: 'text-success' },
              ] : []} valueLabel="Events" />
            )}
          </Panel>

          {/* Custom tab notice — consistent across all tabs */}
          <CustomTabNotice onNavigate={() => setDashTab('custom')} />
        </div>
      )}

      {/* Event Logs tab */}
      {clusterId && dashTab === 'event-logs' && (
        <div className="space-y-4">
          <div className="rounded-lg border border-border bg-card">
            <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-2">
              <div>
                <h3 className="text-sm font-semibold">Security Event Logs</h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Recent WAF security events from ClickHouse. Use time range and cluster selectors above to filter.
                </p>
              </div>
              {eventLogsFilter && (
                <Button variant="ghost" size="sm" className="h-7 text-xs gap-1.5 shrink-0" onClick={() => setEventLogsFilter(null)}>
                  Clear applied filter
                </Button>
              )}
            </div>
            <div className="p-4">
              <SecurityLogsTab
                key={JSON.stringify(eventLogsFilter)}
                clusterId={clusterId}
                namespace="default"
                initialAttackFilter={eventLogsFilter?.attack}
                initialIpFilter={eventLogsFilter?.ip}
                initialUriFilter={eventLogsFilter?.uri}
              />
            </div>
          </div>

          {/* Custom tab notice — consistent across all tabs */}
          <CustomTabNotice onNavigate={() => setDashTab('custom')} />
        </div>
      )}

      {/* Support ID tab */}
      {clusterId && dashTab === 'support-id' && (
        <div className="rounded-lg border border-border bg-card">
          <div className="px-4 py-3 border-b border-border">
            <h3 className="text-sm font-semibold">Support ID Lookup</h3>
            <p className="text-xs text-muted-foreground mt-0.5">Find a specific WAF event by its Support ID.</p>
          </div>
          <div className="p-4">
            <SupportIdTab clusterId={clusterId} />
          </div>
        </div>
      )}

      {/* Custom tab — user-defined panels */}
      {clusterId && dashTab === 'custom' && customTabs.length === 0 && (
        <div className="rounded-lg border border-dashed border-border bg-muted/20 flex flex-col items-center justify-center py-12 gap-2">
          <LayoutDashboard className="h-8 w-8 text-muted-foreground/40" />
          <p className="text-sm text-muted-foreground">No custom tabs yet.</p>
          <p className="text-xs text-muted-foreground/70 max-w-xs text-center">Add a tab to start building your own analytics panels powered by ClickHouse.</p>
          <Button size="sm" variant="outline" className="mt-2 gap-1.5" onClick={() => setAddingTab(true)}>
            <Plus className="h-3.5 w-3.5" /> Add Tab
          </Button>
        </div>
      )}

      {clusterId && dashTab === 'custom' && customTabs.length > 0 && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-foreground">
                {customTabs.find(t => t.id === activeCustomTabId)?.name ?? 'Custom Panels'}
              </h2>
              <p className="text-xs text-muted-foreground mt-0.5">Add your own analytics panels powered by ClickHouse.</p>
            </div>
            <div className="flex items-center gap-1.5">
              <Button
                variant={editMode ? 'default' : 'outline'}
                size="sm"
                className="h-8 gap-1.5"
                onClick={() => setEditMode(v => !v)}
                title="Toggle panel edit mode"
              >
                <Pencil className="h-3.5 w-3.5" />
                {editMode ? 'Done' : 'Edit Panels'}
              </Button>
              <Button size="sm" className="gap-1.5 h-8" onClick={openCreate}>
                <Plus className="h-3.5 w-3.5" /> Add Panel
              </Button>
            </div>
          </div>
          {customPanels.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border bg-muted/20 flex flex-col items-center justify-center py-12 gap-2">
              <LayoutDashboard className="h-8 w-8 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">No custom panels yet.</p>
              <Button size="sm" variant="outline" className="mt-2 gap-1.5" onClick={openCreate}>
                <Plus className="h-3.5 w-3.5" /> Add Panel
              </Button>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {customPanels.map((panel) => (
                <div key={panel.id} className={panel.width === 'full' ? 'lg:col-span-2' : ''}>
                  <WafPanelRenderer
                    panel={panel} clusterId={clusterId}
                    globalTimeRange={timeRange} refreshIntervalMs={intervalMs}
                    editMode={editMode} onEdit={openEdit}
                    onDelete={(id) => void deletePanel.mutateAsync(id)}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <WafPanelBuilderModal
        clusterId={clusterId ?? 0}
        panel={editingPanel}
        open={builderOpen}
        onClose={() => { setBuilderOpen(false); setEditingPanel(null); }}
        nextOrder={customPanels.length}
        tabId={activeCustomTabId}
      />
    </div>
    </InfoTooltipQueryWindowProvider>
  );
}
