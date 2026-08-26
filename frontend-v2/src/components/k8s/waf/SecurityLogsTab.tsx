import { useState, useMemo } from 'react';
import { AlertTriangle, RefreshCw, ChevronDown, ChevronRight, Download, Wifi, WifiOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { cn } from '@/lib/utils';
import { useWafLogs } from '@/hooks/useWafLogs';
import type { NapLogEntry, WafLogsParams } from '@/lib/api/waf-logs';

interface SecurityLogsTabProps {
  clusterId: number;
  namespace: string;
  crKind?: 'appolicy' | 'f5virtualserver';
  crName?: string;
  /** Pre-seed filters when navigating here from a dashboard "apply as filter" click. */
  initialOutcomeFilter?: string;
  initialAttackFilter?: string;
  initialIpFilter?: string;
  initialUriFilter?: string;
}

const OUTCOME_COLORS: Record<string, string> = {
  REJECTED: 'bg-destructive/10 text-destructive border-destructive/20',
  BLOCKED:  'bg-destructive/10 text-destructive border-destructive/20',
  PASSED:   'bg-success/10 text-success border-success/20',
  ALERTED:  'bg-warning/10 text-warning border-warning/20',
};

const RATING_COLORS: Record<string, string> = {
  '5': 'text-destructive font-semibold',
  '4': 'text-destructive',
  '3': 'text-warning',
  '2': 'text-muted-foreground',
  '1': 'text-muted-foreground',
};

function outcomeLabel(entry: NapLogEntry): string {
  return (entry.outcome ?? entry.request_status ?? '—').toUpperCase();
}

// Shared column template: chevron | outcome | request | attack-type | rating | time
const ROW_COLS = 'grid-cols-[16px_auto_1fr_1fr_40px_auto]';

function LogEntryRow({ entry }: { entry: NapLogEntry }) {
  const [expanded, setExpanded] = useState(false);
  const outcome = outcomeLabel(entry);
  const ratingColor = RATING_COLORS[entry.violation_rating ?? ''] ?? 'text-muted-foreground';
  const fields = Object.entries(entry).filter(([k]) => k !== 'raw');

  return (
    <div className="border-b border-border last:border-0">
      <button
        onClick={() => setExpanded(v => !v)}
        className={cn('w-full grid gap-x-3 px-3 py-2 text-left hover:bg-muted/40 transition-colors items-center text-xs', ROW_COLS)}
      >
        {/* Chevron */}
        <span className="text-muted-foreground flex items-center justify-center">
          {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        </span>
        {/* Outcome badge */}
        <Badge variant="outline" className={cn('text-[9px] px-1.5 py-0 h-4 shrink-0 justify-self-start', OUTCOME_COLORS[outcome] ?? 'bg-muted text-muted-foreground border-border')}>
          {outcome}
        </Badge>
        {/* URI / request */}
        <span className="truncate text-foreground font-mono" title={entry.request ?? entry.uri ?? entry.raw}>
          {entry.method ? `${entry.method} ` : ''}{entry.uri ?? entry.request ?? entry.raw.slice(0, 60)}
        </span>
        {/* Attack type */}
        <span className="truncate text-muted-foreground">{entry.attack_type ?? '—'}</span>
        {/* Rating */}
        <span className={cn('shrink-0 justify-self-end', ratingColor)}>
          {entry.violation_rating ? `⚠ ${entry.violation_rating}` : ''}
        </span>
        {/* Timestamp */}
        <span className="text-[10px] text-muted-foreground whitespace-nowrap justify-self-end">{entry.date_time ?? ''}</span>
      </button>

      {expanded && (
        <div className="px-8 pb-3 space-y-1">
          <div className="rounded-md border border-border bg-muted/30 divide-y divide-border">
            {fields.map(([k, v]) => v ? (
              <div key={k} className="flex gap-3 px-3 py-1 text-xs">
                <span className="text-muted-foreground w-32 shrink-0 font-medium">{k}</span>
                <span className="text-foreground font-mono break-all">{v}</span>
              </div>
            ) : null)}
            {/* Raw line */}
            <div className="flex gap-3 px-3 py-1 text-xs">
              <span className="text-muted-foreground w-32 shrink-0 font-medium">raw</span>
              <span className="text-muted-foreground font-mono break-all">{entry.raw}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function SecurityLogsTab({
  clusterId, namespace, crKind, crName,
  initialOutcomeFilter, initialAttackFilter, initialIpFilter, initialUriFilter,
}: SecurityLogsTabProps) {
  const [outcomeFilter, setOutcomeFilter] = useState<string>(initialOutcomeFilter ?? 'all');
  const [attackFilter, setAttackFilter]   = useState(initialAttackFilter ?? '');
  const [vsFilter, setVsFilter]           = useState('');
  const [ipFilter, setIpFilter]           = useState(initialIpFilter ?? '');
  const [uriFilter, setUriFilter]         = useState(initialUriFilter ?? '');
  const [limit, setLimit]                 = useState(200);

  const params: WafLogsParams = useMemo(() => ({
    namespace,
    ...(crKind ? { cr_kind: crKind } : {}),
    ...(crName ? { cr_name: crName } : {}),
    limit,
    outcome_filter:     outcomeFilter !== 'all' ? outcomeFilter : undefined,
    attack_type_filter: attackFilter  || undefined,
    vs_name_filter:     vsFilter      || undefined,
    ip_filter:          ipFilter      || undefined,
    uri_filter:         uriFilter     || undefined,
  }), [namespace, crKind, crName, limit, outcomeFilter, attackFilter, vsFilter, ipFilter, uriFilter]);

  const { data, isFetching, refetch, dataUpdatedAt } = useWafLogs(clusterId, params);

  const entries = data?.entries ?? [];
  const lastUpdated = dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : null;

  // Derive unique vs_names from current entries for the filter hint
  const vsNames = useMemo(() => [...new Set(entries.map(e => e.vs_name).filter(Boolean))], [entries]);

  function exportCsv() {
    if (!entries.length) return;
    const cols = ['date_time', 'outcome', 'violation_rating', 'attack_type', 'vs_name', 'method', 'uri', 'client_ip', 'policy_name', 'support_id'];
    const header = cols.join(',');
    const rows = entries.map(e => cols.map(c => JSON.stringify(e[c] ?? '')).join(','));
    const blob = new Blob([header + '\n' + rows.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `security-logs-${crName}-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-3 p-4">
      {/* Source info bar */}
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {data?.source_endpoint ? (
          <>
            <Wifi className="h-3 w-3 text-success shrink-0" />
            <span>syslog: <span className="font-mono text-foreground">{data.source_endpoint}</span></span>
          </>
        ) : data && !isFetching ? (
          <>
            <WifiOff className="h-3 w-3 text-destructive shrink-0" />
            <span className="text-destructive">{data.warning ?? 'No syslog endpoint resolved'}</span>
          </>
        ) : null}
        <span className="ml-auto">
          {lastUpdated && <span className="mr-2">Updated {lastUpdated}</span>}
          <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => void refetch()} disabled={isFetching}>
            <RefreshCw className={cn('h-3 w-3', isFetching && 'animate-spin')} />
          </Button>
        </span>
      </div>

      {/* Error banner */}
      {data?.error && (
        <div className="flex items-start gap-2 rounded-md border border-destructive/20 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>{data.error}</span>
        </div>
      )}

      {/* Filter bar */}
      <div className="flex flex-wrap gap-2 items-center">
        <Select value={outcomeFilter} onValueChange={setOutcomeFilter}>
          <SelectTrigger className="h-7 text-xs w-32">
            <SelectValue placeholder="Outcome" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All outcomes</SelectItem>
            <SelectItem value="REJECTED">REJECTED</SelectItem>
            <SelectItem value="PASSED">PASSED</SelectItem>
            <SelectItem value="ALERTED">ALERTED</SelectItem>
          </SelectContent>
        </Select>

        <Input
          className="h-7 text-xs w-36"
          placeholder="Attack type…"
          value={attackFilter}
          onChange={e => setAttackFilter(e.target.value)}
        />

        <Input
          className="h-7 text-xs w-32 font-mono"
          placeholder="IP address…"
          value={ipFilter}
          onChange={e => setIpFilter(e.target.value)}
        />

        <Input
          className="h-7 text-xs w-36 font-mono"
          placeholder="URI…"
          value={uriFilter}
          onChange={e => setUriFilter(e.target.value)}
        />

        {crKind === 'f5virtualserver' && (
          <Input
            className="h-7 text-xs w-36"
            placeholder="vs_name…"
            value={vsFilter}
            onChange={e => setVsFilter(e.target.value)}
            list="vs-names-datalist"
          />
        )}
        {vsNames.length > 0 && (
          <datalist id="vs-names-datalist">
            {vsNames.map(n => <option key={n} value={n} />)}
          </datalist>
        )}

        <Select value={String(limit)} onValueChange={v => setLimit(Number(v))}>
          <SelectTrigger className="h-7 text-xs w-24">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {[50, 100, 200, 500].map(n => (
              <SelectItem key={n} value={String(n)}>Last {n}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <div className="flex items-center gap-2 ml-auto">
          <span className="text-xs text-muted-foreground">
            {entries.length} {entries.length === 1 ? 'entry' : 'entries'}
          </span>
          {(attackFilter || ipFilter || uriFilter || vsFilter || outcomeFilter !== 'all') && (
            <Button variant="ghost" size="sm" className="h-7 text-xs"
              onClick={() => { setOutcomeFilter('all'); setAttackFilter(''); setVsFilter(''); setIpFilter(''); setUriFilter(''); }}>
              Clear filters
            </Button>
          )}
          <Button variant="outline" size="sm" className="h-7 text-xs gap-1" onClick={exportCsv} disabled={!entries.length}>
            <Download className="h-3 w-3" /> CSV
          </Button>
        </div>
      </div>

      {/* Table header */}
      {entries.length > 0 && (
        <div className="rounded-md border border-border overflow-hidden">
          <div className={cn('grid gap-x-3 px-3 py-1.5 bg-muted/50 text-[10px] font-medium text-muted-foreground border-b border-border items-center', ROW_COLS)}>
            <span />{/* chevron placeholder */}
            <span>Outcome</span>
            <span>Request</span>
            <span>Attack Type</span>
            <span className="text-right">Rating</span>
            <span className="text-right">Time</span>
          </div>
          <div className="divide-y divide-border max-h-[420px] overflow-y-auto">
            {entries.map((entry, i) => (
              <LogEntryRow key={entry.support_id ?? `${entry.date_time}-${i}`} entry={entry} />
            ))}
          </div>
        </div>
      )}

      {/* Empty states */}
      {!isFetching && entries.length === 0 && data?.source_endpoint && (
        <div className="text-center py-8 text-xs text-muted-foreground">
          No security events found. Traffic may not have triggered any WAF rules yet.
        </div>
      )}

      {!isFetching && !data?.source_endpoint && !data?.warning && (
        <div className="text-center py-8 text-xs text-muted-foreground">
          Loading…
        </div>
      )}
    </div>
  );
}
