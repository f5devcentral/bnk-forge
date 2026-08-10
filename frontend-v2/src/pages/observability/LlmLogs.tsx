/**
 * LlmLogs — per-request log explorer for the AI gateway (Tier 1).
 *
 * Header (refresh · live toggle · debounced search · column config) + a stat
 * strip + a zoomable request-volume histogram (drag-select to zoom, reset) +
 * a sortable request table with time-cursor "load older" + a detail drawer
 * that reconstructs the chat transcript from req_body/resp_body.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  ReferenceArea,
} from 'recharts';
import {
  Activity,
  CheckCircle2,
  Clock,
  Hash,
  DollarSign,
  Boxes,
  RefreshCw,
  Search,
  Settings2,
  ArrowUp,
  ArrowDown,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { SectionCard } from '@/components/ui/section-card';
import { Skeleton } from '@/components/ui/skeleton';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuCheckboxItem,
} from '@/components/ui/dropdown-menu';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn } from '@/lib/utils';
import { useDebounce } from '@/hooks/useDebounce';
import {
  StatTile,
  TimeRangePicker,
  ClusterPicker,
  FilterSelect,
  CHART_GRID,
  CHART_TEXT,
  CHART_TOOLTIP,
  SUCCESS_COLOR,
  fmtInt,
  fmtCompact,
  fmtCost,
  fmtLatencyMs,
  fmtPct,
} from '@/components/observability';
import { useLlmStats, useLlmHistogram, useLlmLogs, useLlmFilterData } from '@/hooks/useLlmObservability';
import { llmObservabilityApi } from '@/lib/api/llmObservability';
import { useObservabilityFilters } from './use-observability-filters';
import { parseTranscript } from './transcript';
import type { LlmLogRow } from '@/types/llm-observability';

const PAGE_LIMIT = 50;

type SortKey = 'ts' | 'latency_ms' | 'total_tk' | 'cost';
type SortDir = 'asc' | 'desc';

const OPTIONAL_COLUMNS = ['model', 'tokens', 'cost', 'status'] as const;
type OptionalColumn = (typeof OPTIONAL_COLUMNS)[number];

export default function LlmLogs() {
  const f = useObservabilityFilters('logs');
  const { data: filterData } = useLlmFilterData(f.clusterId, f.range);

  const [live, setLive] = useState(false);
  const [searchInput, setSearchInput] = useState('');
  const search = useDebounce(searchInput, 300);
  const [visibleCols, setVisibleCols] = useState<Record<OptionalColumn, boolean>>({
    model: true,
    tokens: true,
    cost: true,
    status: true,
  });

  const [sortKey, setSortKey] = useState<SortKey>('ts');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [selected, setSelected] = useState<number | null>(null);

  const logParams = useMemo(
    () => ({ ...f.queryParams, limit: PAGE_LIMIT, ...(search ? { content_search: search } : {}) }),
    [f.queryParams, search],
  );

  const stats = useLlmStats(f.clusterId, f.queryParams);
  const histogram = useLlmHistogram(f.clusterId, { ...f.queryParams, metric: 'requests' }, true);
  const logs = useLlmLogs(f.clusterId, logParams, { live });

  // Accumulated "load older" pages, reset whenever the base query changes.
  const [olderRows, setOlderRows] = useState<LlmLogRow[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const paramKey = JSON.stringify(logParams) + String(f.clusterId);
  const prevKey = useRef(paramKey);
  useEffect(() => {
    if (prevKey.current !== paramKey) {
      prevKey.current = paramKey;
      setOlderRows([]);
      setCursor(null);
    }
  }, [paramKey]);

  const allRows = useMemo(() => {
    const seen = new Set<string>();
    const merged: LlmLogRow[] = [];
    for (const r of [...(logs.data?.rows ?? []), ...olderRows]) {
      const id = `${r.ts}-${r.model}-${r.total_tk}`;
      if (seen.has(id)) continue;
      seen.add(id);
      merged.push(r);
    }
    return merged;
  }, [logs.data, olderRows]);

  const sortedRows = useMemo(() => {
    const rows = [...allRows];
    rows.sort((a, b) => {
      const av = sortKey === 'ts' ? Date.parse(a.ts) : a[sortKey];
      const bv = sortKey === 'ts' ? Date.parse(b.ts) : b[sortKey];
      return sortDir === 'asc' ? av - bv : bv - av;
    });
    return rows;
  }, [allRows, sortKey, sortDir]);

  const nextEnd = cursor ?? logs.data?.next_end ?? null;

  const loadOlder = useCallback(async () => {
    if (!f.clusterId || !nextEnd) return;
    setLoadingOlder(true);
    try {
      const res = await llmObservabilityApi.getLogs(f.clusterId, { ...logParams, end: nextEnd });
      setOlderRows((prev) => [...prev, ...res.rows]);
      setCursor(res.next_end);
    } finally {
      setLoadingOlder(false);
    }
  }, [f.clusterId, nextEnd, logParams]);

  const toggleSort = (k: SortKey) => {
    if (sortKey === k) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSortKey(k);
      setSortDir('desc');
    }
  };

  const refresh = () => {
    setOlderRows([]);
    setCursor(null);
    void logs.refetch();
    void stats.refetch();
    void histogram.refetch();
  };

  return (
    <div className="space-y-5 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-foreground">LLM Logs</h1>
          <p className="text-sm text-muted-foreground">Per-request logs from the LLM gateway.</p>
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

      {/* Controls row */}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" onClick={refresh} className="h-8 gap-1.5">
          <RefreshCw className={cn('h-3.5 w-3.5', logs.isFetching && 'animate-spin')} />
          Refresh
        </Button>
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <Switch checked={live} onCheckedChange={setLive} aria-label="Live" />
          Live
          {live && <span className="h-2 w-2 rounded-full bg-success animate-pulse" />}
        </label>
        <div className="relative flex-1 min-w-[180px] max-w-sm">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search request text…"
            className="h-8 pl-8 text-xs"
          />
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 gap-1.5">
              <Settings2 className="h-3.5 w-3.5" />
              Columns
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>Columns</DropdownMenuLabel>
            {OPTIONAL_COLUMNS.map((col) => (
              <DropdownMenuCheckboxItem
                key={col}
                checked={visibleCols[col]}
                onCheckedChange={(v) => setVisibleCols((prev) => ({ ...prev, [col]: !!v }))}
                className="capitalize"
              >
                {col}
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Stat strip */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <StatTile icon={Activity} label="Total Requests" tone="primary" loading={stats.isLoading} value={fmtInt(stats.data?.total_requests)} />
        <StatTile icon={CheckCircle2} label="Success Rate" tone="success" loading={stats.isLoading} value={fmtPct(stats.data?.success_rate)} />
        <StatTile icon={Clock} label="Avg Latency" tone="info" loading={stats.isLoading} value={fmtLatencyMs(stats.data?.avg_latency_ms)} />
        <StatTile icon={Hash} label="Total Tokens" tone="warning" loading={stats.isLoading} value={fmtCompact(stats.data?.total_tokens)} />
        <StatTile icon={DollarSign} label="Total Cost" tone="success" loading={stats.isLoading} value={fmtCost(stats.data?.total_cost)} />
        <StatTile icon={Boxes} label="Models" tone="muted" loading={stats.isLoading} value={fmtInt(stats.data?.models)} />
      </div>

      <ZoomableHistogram
        series={histogram.data?.series ?? []}
        loading={histogram.isLoading}
        available={histogram.data?.available ?? true}
      />

      {/* Request table */}
      <SectionCard compact>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <SortHead label="Time" k="ts" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                <TableHead>Request</TableHead>
                {visibleCols.model && <TableHead>Model</TableHead>}
                <SortHead label="Latency" k="latency_ms" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} align="right" />
                {visibleCols.tokens && (
                  <SortHead label="Tokens" k="total_tk" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} align="right" />
                )}
                {visibleCols.cost && (
                  <SortHead label="Cost" k="cost" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} align="right" />
                )}
                {visibleCols.status && <TableHead className="text-right">Status</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {logs.isLoading ? (
                <TableRow>
                  <TableCell colSpan={7}>
                    <Skeleton className="h-40 w-full" />
                  </TableCell>
                </TableRow>
              ) : sortedRows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-sm text-muted-foreground py-8">
                    {logs.data?.available === false
                      ? logs.data?.errors?.logs || 'Log source unavailable.'
                      : 'No requests in this window.'}
                  </TableCell>
                </TableRow>
              ) : (
                sortedRows.map((row, i) => (
                  <TableRow
                    key={`${row.ts}-${i}`}
                    className="cursor-pointer"
                    onClick={() => setSelected(i)}
                  >
                    <TableCell className="whitespace-nowrap text-xs tabular-nums text-muted-foreground">
                      {fmtTime(row.ts)}
                    </TableCell>
                    <TableCell className="max-w-[280px] truncate text-foreground/90">{row.message || '—'}</TableCell>
                    {visibleCols.model && <TableCell className="text-xs text-foreground/80">{row.model}</TableCell>}
                    <TableCell className="text-right tabular-nums text-foreground/80">{fmtLatencyMs(row.latency_ms)}</TableCell>
                    {visibleCols.tokens && (
                      <TableCell className="text-right tabular-nums text-foreground/80">{fmtInt(row.total_tk)}</TableCell>
                    )}
                    {visibleCols.cost && (
                      <TableCell className="text-right tabular-nums text-foreground/80">{fmtCost(row.cost)}</TableCell>
                    )}
                    {visibleCols.status && (
                      <TableCell className="text-right">
                        <StatusBadge status={row.status} />
                      </TableCell>
                    )}
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>

        {nextEnd && sortedRows.length > 0 && (
          <div className="mt-3 flex justify-center">
            <Button variant="outline" size="sm" onClick={loadOlder} disabled={loadingOlder} className="h-8">
              {loadingOlder ? 'Loading…' : 'Load older'}
            </Button>
          </div>
        )}
      </SectionCard>

      <LogDetailDrawer
        rows={sortedRows}
        index={selected}
        onIndexChange={setSelected}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}

// ============================================================================
// Zoomable histogram
// ============================================================================

interface HistoRow {
  ts: string;
  value: number;
}

function ZoomableHistogram({
  series,
  loading,
  available,
}: {
  series: { name: string; points: { ts: string; value: number }[] }[];
  loading: boolean;
  available: boolean;
}) {
  const data: HistoRow[] = useMemo(() => {
    const byTs = new Map<string, number>();
    for (const s of series) {
      for (const p of s.points) byTs.set(p.ts, (byTs.get(p.ts) ?? 0) + p.value);
    }
    return Array.from(byTs.entries())
      .map(([ts, value]) => ({ ts, value }))
      .sort((a, b) => (a.ts < b.ts ? -1 : 1));
  }, [series]);

  const [refLeft, setRefLeft] = useState<string | null>(null);
  const [refRight, setRefRight] = useState<string | null>(null);
  const [zoom, setZoom] = useState<[string, string] | null>(null);

  const shown = useMemo(() => {
    if (!zoom) return data;
    const [a, b] = zoom;
    const lo = a < b ? a : b;
    const hi = a < b ? b : a;
    return data.filter((d) => d.ts >= lo && d.ts <= hi);
  }, [data, zoom]);

  const applyZoom = () => {
    if (refLeft && refRight && refLeft !== refRight) setZoom([refLeft, refRight]);
    setRefLeft(null);
    setRefRight(null);
  };

  if (loading) return <Skeleton className="h-[200px] w-full" />;
  if (!available) {
    return (
      <SectionCard compact>
        <p className="text-sm text-muted-foreground">Histogram unavailable.</p>
      </SectionCard>
    );
  }

  return (
    <SectionCard compact>
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-sm font-semibold text-foreground">Request Volume</h4>
        {zoom && (
          <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={() => setZoom(null)}>
            Reset zoom
          </Button>
        )}
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart
          data={shown}
          onMouseDown={(e) => e?.activeLabel && setRefLeft(String(e.activeLabel))}
          onMouseMove={(e) => refLeft && e?.activeLabel && setRefRight(String(e.activeLabel))}
          onMouseUp={applyZoom}
        >
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} />
          <XAxis dataKey="ts" tickFormatter={fmtTime} tick={{ fill: CHART_TEXT, fontSize: 10 }} minTickGap={24} />
          <YAxis tickFormatter={(v) => fmtCompact(Number(v))} tick={{ fill: CHART_TEXT, fontSize: 10 }} width={44} />
          <RechartsTooltip
            contentStyle={CHART_TOOLTIP}
            labelFormatter={(l) => fmtTime(String(l))}
            formatter={(v) => [fmtInt(Number(v)), 'requests']}
          />
          <Bar dataKey="value" fill={SUCCESS_COLOR} radius={[2, 2, 0, 0]} isAnimationActive={false} />
          {refLeft && refRight && <ReferenceArea x1={refLeft} x2={refRight} strokeOpacity={0.3} />}
        </BarChart>
      </ResponsiveContainer>
    </SectionCard>
  );
}

// ============================================================================
// Detail drawer
// ============================================================================

function LogDetailDrawer({
  rows,
  index,
  onIndexChange,
  onClose,
}: {
  rows: LlmLogRow[];
  index: number | null;
  onIndexChange: (i: number) => void;
  onClose: () => void;
}) {
  const row = index != null ? rows[index] : undefined;
  const turns = useMemo(() => (row ? parseTranscript(row.req_body, row.resp_body) : []), [row]);

  return (
    <Sheet open={index != null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full sm:max-w-xl overflow-y-auto">
        {row && (
          <>
            <SheetHeader>
              <SheetTitle className="flex items-center gap-2">
                <span className="truncate">{row.model}</span>
                <StatusBadge status={row.status} />
              </SheetTitle>
            </SheetHeader>

            <div className="mt-3 flex items-center justify-between">
              <span className="text-xs text-muted-foreground tabular-nums">{fmtTime(row.ts)}</span>
              <div className="flex items-center gap-1">
                <Button
                  variant="outline"
                  size="icon"
                  className="h-7 w-7"
                  disabled={index === 0}
                  onClick={() => index != null && onIndexChange(index - 1)}
                  aria-label="Previous request"
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  className="h-7 w-7"
                  disabled={index === rows.length - 1}
                  onClick={() => index != null && onIndexChange(index + 1)}
                  aria-label="Next request"
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>

            {/* Metrics */}
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Metric label="Latency" value={fmtLatencyMs(row.latency_ms)} />
              <Metric label="Prompt tk" value={fmtInt(row.prompt_tk)} />
              <Metric label="Completion tk" value={fmtInt(row.comp_tk)} />
              <Metric label="Cost" value={fmtCost(row.cost)} />
            </div>

            {/* Transcript */}
            <div className="mt-5">
              <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground/70">Transcript</h5>
              <div className="space-y-2">
                {turns.map((t, i) => (
                  <div key={i} className="rounded-lg border border-border p-2.5">
                    <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{t.role}</div>
                    <div className="whitespace-pre-wrap break-words text-sm text-foreground/90">{t.content || '—'}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* Raw JSON */}
            <details className="mt-5">
              <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-muted-foreground/70">
                Raw JSON
              </summary>
              <pre className="mt-2 max-h-72 overflow-auto rounded-lg border border-border bg-muted/40 p-2 text-[11px] leading-relaxed">
                {JSON.stringify({ request: row.req_body, response: row.resp_body }, null, 2)}
              </pre>
            </details>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="font-mono text-sm font-medium text-foreground tabular-nums">{value}</div>
    </div>
  );
}

// ============================================================================
// Shared bits
// ============================================================================

function SortHead({
  label,
  k,
  sortKey,
  sortDir,
  onSort,
  align = 'left',
}: {
  label: string;
  k: SortKey;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (k: SortKey) => void;
  align?: 'left' | 'right';
}) {
  const active = sortKey === k;
  const Icon = sortDir === 'asc' ? ArrowUp : ArrowDown;
  return (
    <TableHead className={align === 'right' ? 'text-right' : undefined}>
      <button
        type="button"
        onClick={() => onSort(k)}
        className={cn(
          'inline-flex items-center gap-1 transition-colors hover:text-foreground',
          active ? 'text-foreground' : 'text-muted-foreground',
        )}
      >
        {label}
        {active && <Icon className="h-3 w-3" />}
      </button>
    </TableHead>
  );
}

function StatusBadge({ status }: { status: string }) {
  const ok = status.startsWith('2');
  return (
    <Badge variant={ok ? 'success' : 'destructive'} className="font-mono text-[10px]">
      {status}
    </Badge>
  );
}

function fmtTime(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
