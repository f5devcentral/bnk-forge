/**
 * BenchmarkRunsTab — D-020: runs table + filters in a SectionCard.
 * Status conveyed via Badge variants only; action buttons are ghost icons.
 */
import { useMemo } from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { Skeleton } from '@/components/ui/skeleton';
import { TimeAgo } from '@/components/ui/TimeAgo';
import { SectionCard } from '@/components/ui/section-card';
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
import {
  BarChart3,
  XCircle,
  Trash2,
  Search,
  ArrowRight,
  GitCompare,
  Star,
  LineChart,
} from 'lucide-react';
import {
  useBenchmarkRuns,
  useCancelBenchmarkRun,
  useDeleteBenchmarkRun,
  useSetBenchmarkRunBaseline,
  useUnsetBenchmarkRunBaseline,
} from '@/hooks/useBenchmarks';
import { StatusBadge, ProxyBadge, RegressionBadge, fmtLatency, fmtNum, fmtPct } from './benchmark-utils';

interface RunsTabProps {
  proxyFilter: string;
  onProxyFilterChange: (s: string) => void;
  statusFilter: string;
  onStatusFilterChange: (s: string) => void;
  searchQuery: string;
  onSearchChange: (s: string) => void;
  onSelectRun: (id: number) => void;
  compareRunIds: number[];
  onToggleCompare: (id: number) => void;
  onCompare: () => void;
  onViewTrends?: () => void;
}

export function BenchmarkRunsTab({
  proxyFilter, onProxyFilterChange,
  statusFilter, onStatusFilterChange,
  searchQuery, onSearchChange,
  onSelectRun, compareRunIds, onToggleCompare, onCompare, onViewTrends,
}: RunsTabProps) {
  const { data, isLoading } = useBenchmarkRuns({
    proxy: proxyFilter || undefined,
    status: statusFilter || undefined,
    pollingEnabled: true,
  });
  const cancelRun = useCancelBenchmarkRun();
  const deleteRun = useDeleteBenchmarkRun();
  const setBaseline = useSetBenchmarkRunBaseline();
  const unsetBaseline = useUnsetBenchmarkRunBaseline();

  const runs = useMemo(() => data?.runs ?? [], [data?.runs]);
  const filtered = useMemo(() => {
    if (!searchQuery) return runs;
    const q = searchQuery.toLowerCase();
    return runs.filter(r =>
      (r.run_label || '').toLowerCase().includes(q) ||
      r.model.toLowerCase().includes(q) ||
      r.proxy.toLowerCase().includes(q)
    );
  }, [runs, searchQuery]);

  return (
    <div className="space-y-6">
      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input placeholder="Search runs..." value={searchQuery} onChange={e => onSearchChange(e.target.value)} className="pl-9" />
        </div>
        <Select value={proxyFilter || 'all'} onValueChange={v => onProxyFilterChange(v === 'all' ? '' : v)}>
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
        <Select value={statusFilter || 'all'} onValueChange={v => onStatusFilterChange(v === 'all' ? '' : v)}>
          <SelectTrigger className="w-36"><SelectValue placeholder="All statuses" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="completed">Completed</SelectItem>
            <SelectItem value="running">Running</SelectItem>
            <SelectItem value="failed">Failed</SelectItem>
            <SelectItem value="cancelled">Cancelled</SelectItem>
          </SelectContent>
        </Select>
        {compareRunIds.length >= 2 && (
          <Button size="sm" variant="outline" onClick={onCompare}>
            <GitCompare className="h-4 w-4 mr-1" />
            Compare {compareRunIds.length} runs
          </Button>
        )}
        {onViewTrends && (
          <Button size="sm" variant="outline" onClick={onViewTrends}>
            <LineChart className="h-4 w-4 mr-1" />
            Trends
          </Button>
        )}
      </div>

      {/* Table */}
      {isLoading ? (
        <SectionCard compact>
          <div className="space-y-2">{[1, 2, 3].map(i => <Skeleton key={i} className="h-12 w-full" />)}</div>
        </SectionCard>
      ) : filtered.length === 0 ? (
        <SectionCard>
          <div className="text-center py-8">
            <BarChart3 className="h-12 w-12 mx-auto text-muted-foreground mb-3" />
            <p className="text-foreground">No benchmark runs found</p>
            <p className="text-xs text-muted-foreground mt-1">Run aiperf profile, then push the JSON result — see the Agents tab for instructions.</p>
          </div>
        </SectionCard>
      ) : (
        <SectionCard
          title={`${filtered.length} ${filtered.length === 1 ? 'run' : 'runs'}`}
          compact
        >
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8"></TableHead>
                  <TableHead>Proxy</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>Tool</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Baseline</TableHead>
                  <TableHead className="text-right">Latency P50</TableHead>
                  <TableHead className="text-right">Latency P99</TableHead>
                  <TableHead className="text-right">RPS</TableHead>
                  <TableHead className="text-right">Tok/s</TableHead>
                  <TableHead className="text-right">Success</TableHead>
                  <TableHead className="text-right">Errors</TableHead>
                  <TableHead className="text-right">Requests</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map(run => (
                  <TableRow key={run.id} className="cursor-pointer" onClick={() => onSelectRun(run.id)}>
                    <TableCell onClick={e => e.stopPropagation()}>
                      <Checkbox
                        checked={compareRunIds.includes(run.id)}
                        onCheckedChange={() => onToggleCompare(run.id)}
                      />
                    </TableCell>
                    <TableCell><ProxyBadge proxy={run.proxy} /></TableCell>
                    <TableCell className="text-sm font-medium max-w-[200px] truncate">{run.model}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{run.tool}</TableCell>
                    <TableCell><StatusBadge status={run.status} /></TableCell>
                    <TableCell onClick={e => e.stopPropagation()}>
                      <div className="flex items-center gap-1.5">
                        {run.status === 'completed' && (
                          <Button
                            size="icon"
                            variant="ghost"
                            className={cn('h-6 w-6', run.is_baseline ? 'text-warning' : 'text-muted-foreground')}
                            onClick={() =>
                              run.is_baseline ? unsetBaseline.mutate(run.id) : setBaseline.mutate(run.id)
                            }
                            title={run.is_baseline ? 'Clear baseline' : 'Mark as baseline'}
                          >
                            <Star className={cn('h-3.5 w-3.5', run.is_baseline && 'fill-current')} />
                          </Button>
                        )}
                        <RegressionBadge run={run} />
                      </div>
                    </TableCell>
                    <TableCell className="text-right text-sm font-mono">{fmtLatency(run.latency_p50)}</TableCell>
                    <TableCell className="text-right text-sm font-mono">{fmtLatency(run.latency_p99)}</TableCell>
                    <TableCell className="text-right text-sm font-mono">{fmtNum(run.overall_rps)}</TableCell>
                    <TableCell className="text-right text-sm font-mono">{fmtNum(run.tokens_per_sec)}</TableCell>
                    <TableCell className="text-right text-sm font-mono">{fmtPct(run.success_rate_pct)}</TableCell>
                    <TableCell className="text-right text-sm font-mono">
                      {run.failed_requests == null ? (
                        '—'
                      ) : run.failed_requests > 0 ? (
                        <span className="text-destructive">{run.failed_requests.toLocaleString()}</span>
                      ) : (
                        <span className="text-muted-foreground">0</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right text-sm">{run.total_requests?.toLocaleString() ?? '—'}</TableCell>
                    <TableCell className="text-sm text-muted-foreground"><TimeAgo dateStr={run.created_at} /></TableCell>
                    <TableCell className="text-right" onClick={e => e.stopPropagation()}>
                      <div className="flex items-center justify-end gap-1">
                        {run.status === 'running' && (
                          <Button size="icon" variant="ghost" className="h-7 w-7 text-warning" onClick={() => cancelRun.mutate(run.id)} title="Cancel">
                            <XCircle className="h-3.5 w-3.5" />
                          </Button>
                        )}
                        {run.status !== 'running' && (
                          <Button size="icon" variant="ghost" className="h-7 w-7 text-destructive" onClick={() => deleteRun.mutate(run.id)} title="Delete">
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        )}
                        <Button size="icon" variant="ghost" className="h-7 w-7" onClick={() => onSelectRun(run.id)} title="View details">
                          <ArrowRight className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </SectionCard>
      )}
    </div>
  );
}
