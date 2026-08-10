import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { auditApi } from '@/lib/api/audit';
import { Card } from '@/components/ui/card';
import { SectionCard } from '@/components/ui/section-card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
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
  ChevronLeft,
  ChevronRight,
  Search,
  Filter,
  RefreshCw,
  User,
  Clock,
  Activity,
  Shield,
  Loader2,
  AlertCircle,
} from 'lucide-react';

interface AuditEntry {
  id: number;
  timestamp: string;
  user: string | null;
  user_id: number | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  resource_name: string | null;
  status: string;
  details: Record<string, unknown>;
  ip_address: string | null;
  http_method: string | null;
  http_path: string | null;
  http_status_code: number | null;
  duration_ms: number | null;
}

interface AuditResponse {
  logs: AuditEntry[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

interface AuditStatsResponse {
  period_days: number;
  total_events: number;
  actions: Record<string, number>;
  resource_types: Record<string, number>;
  users: Record<string, number>;
  statuses: Record<string, number>;
  daily_activity: Array<{ date: string; count: number }>;
}

interface AuditFiltersResponse {
  actions: string[];
  resource_types: string[];
  users: string[];
  statuses: string[];
}

type BadgeVariant = 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' | 'info' | 'muted';

// D-020: action badges use semantic token variants — no raw palette tints.
const ACTION_VARIANTS: Record<string, BadgeVariant> = {
  create: 'success',
  update: 'info',
  delete: 'destructive',
  deploy: 'info',
  deploy_all: 'info',
  destroy: 'destructive',
  destroy_all: 'destructive',
  plan: 'warning',
  init: 'info',
  apply: 'info',
  sync: 'info',
  login: 'success',
};

const STATUS_VARIANTS: Record<string, BadgeVariant> = {
  success: 'success',
  failed: 'destructive',
  error: 'destructive',
};

function formatTimestamp(ts: string): string {
  const date = new Date(ts);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 1) return 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString('en-AU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export default function AuditLog() {
  const [page, setPage] = useState(1);
  const [pageSize] = useState(25);
  const [actionFilter, setActionFilter] = useState<string>('all');
  const [resourceTypeFilter, setResourceTypeFilter] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [userFilter, setUserFilter] = useState<string>('all');
  const [search, setSearch] = useState('');

  // Fetch filter options
  const { data: filters } = useQuery<AuditFiltersResponse>({
    queryKey: ['audit-filters'],
    queryFn: () => auditApi.getFilters(),
    staleTime: 60000,
  });

  // Fetch audit stats
  const { data: stats } = useQuery<AuditStatsResponse>({
    queryKey: ['audit-stats'],
    queryFn: () => auditApi.getStats(7),
    staleTime: 30000,
  });

  // Fetch audit logs
  const { data, isLoading, error, refetch } = useQuery<AuditResponse>({
    queryKey: ['audit-logs', page, pageSize, actionFilter, resourceTypeFilter, statusFilter, userFilter, search],
    queryFn: () => auditApi.getLogs({
      page,
      page_size: pageSize,
      action: actionFilter !== 'all' ? actionFilter : undefined,
      resource_type: resourceTypeFilter !== 'all' ? resourceTypeFilter : undefined,
      status: statusFilter !== 'all' ? statusFilter : undefined,
      user: userFilter !== 'all' ? userFilter : undefined,
      search: search || undefined,
    }),
    staleTime: 10000,
  });

  const logs = data?.logs ?? [];
  const total = data?.total ?? 0;
  const totalPages = data?.total_pages ?? 0;

  return (
    <div className="space-y-4">
      {/* Stats Row */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Card className="p-3">
            <div className="flex items-center gap-2">
              <Activity className="h-4 w-4 text-info" />
              <div>
                <div className="text-xs text-muted-foreground">Events (7d)</div>
                <div className="text-lg font-semibold text-foreground">{stats.total_events}</div>
              </div>
            </div>
          </Card>
          <Card className="p-3">
            <div className="flex items-center gap-2">
              <User className="h-4 w-4 text-success" />
              <div>
                <div className="text-xs text-muted-foreground">Active Users</div>
                <div className="text-lg font-semibold text-foreground">{Object.keys(stats.users).length}</div>
              </div>
            </div>
          </Card>
          <Card className="p-3">
            <div className="flex items-center gap-2">
              <Shield className="h-4 w-4 text-success" />
              <div>
                <div className="text-xs text-muted-foreground">Success Rate</div>
                <div className="text-lg font-semibold text-foreground">
                  {stats.total_events > 0
                    ? `${Math.round(((stats.statuses?.success ?? 0) / stats.total_events) * 100)}%`
                    : '—'}
                </div>
              </div>
            </div>
          </Card>
          <Card className="p-3">
            <div className="flex items-center gap-2">
              <Clock className="h-4 w-4 text-warning" />
              <div>
                <div className="text-xs text-muted-foreground">Top Action</div>
                <div className="text-lg font-semibold text-foreground">
                  {Object.entries(stats.actions).sort((a, b) => b[1] - a[1])[0]?.[0] ?? '—'}
                </div>
              </div>
            </div>
          </Card>
        </div>
      )}

      {/* Filters */}
      <SectionCard title="Filters" compact>
        <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1.5">
              <Filter className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm font-medium text-foreground/80">Filters:</span>
            </div>

            <Select value={actionFilter} onValueChange={(v) => { setActionFilter(v); setPage(1); }}>
              <SelectTrigger className="w-[130px] h-8 text-xs">
                <SelectValue placeholder="Action" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Actions</SelectItem>
                {filters?.actions.map(a => (
                  <SelectItem key={a} value={a}>{a}</SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={resourceTypeFilter} onValueChange={(v) => { setResourceTypeFilter(v); setPage(1); }}>
              <SelectTrigger className="w-[150px] h-8 text-xs">
                <SelectValue placeholder="Resource" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Resources</SelectItem>
                {filters?.resource_types.map(r => (
                  <SelectItem key={r} value={r}>{r.replace('_', ' ')}</SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={statusFilter} onValueChange={(v) => { setStatusFilter(v); setPage(1); }}>
              <SelectTrigger className="w-[120px] h-8 text-xs">
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Statuses</SelectItem>
                {filters?.statuses.map(s => (
                  <SelectItem key={s} value={s}>{s}</SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={userFilter} onValueChange={(v) => { setUserFilter(v); setPage(1); }}>
              <SelectTrigger className="w-[120px] h-8 text-xs">
                <SelectValue placeholder="User" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Users</SelectItem>
                {filters?.users.map(u => (
                  <SelectItem key={u} value={u}>{u}</SelectItem>
                ))}
              </SelectContent>
            </Select>

            <div className="relative flex-1 min-w-[150px]">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                placeholder="Search..."
                aria-label="Search audit logs"
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1); }}
                className="h-8 pl-7 text-xs"
              />
            </div>

            <Button variant="outline" size="sm" onClick={() => refetch()} className="h-8">
              <RefreshCw className="h-3.5 w-3.5" />
            </Button>
          </div>
      </SectionCard>

      {/* Table */}
      <SectionCard title="Audit events">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-6 w-6 animate-spin text-primary" />
          </div>
        ) : error ? (
          <div className="flex items-center justify-center gap-2 py-12 text-destructive">
            <AlertCircle className="h-5 w-5" />
            <span>Failed to load audit logs</span>
          </div>
        ) : logs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 gap-2 text-muted-foreground">
            <Shield className="h-8 w-8" />
            <span>No audit events found</span>
            <span className="text-xs">Audit events will appear here as you use the application</span>
          </div>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[140px]">Time</TableHead>
                  <TableHead className="w-[100px]">User</TableHead>
                  <TableHead className="w-[90px]">Action</TableHead>
                  <TableHead className="w-[110px]">Resource</TableHead>
                  <TableHead>Path / Details</TableHead>
                  <TableHead className="w-[70px]">Status</TableHead>
                  <TableHead className="w-[60px] text-right">Time</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {logs.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell className="text-xs text-muted-foreground">
                      {entry.timestamp ? formatTimestamp(entry.timestamp) : '—'}
                    </TableCell>
                    <TableCell>
                      <span className="text-xs font-medium text-foreground/80">
                        {entry.user || 'system'}
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={ACTION_VARIANTS[entry.action] ?? 'muted'}
                        className="text-[10px] px-1.5 py-0"
                      >
                        {entry.action}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <span className="text-xs text-foreground/80">
                        {entry.resource_type?.replace('_', ' ')}
                        {entry.resource_id && <span className="text-muted-foreground"> #{entry.resource_id}</span>}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="text-xs font-mono truncate block max-w-[300px] text-muted-foreground">
                        {entry.resource_name || entry.http_path || '—'}
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={STATUS_VARIANTS[entry.status] ?? 'muted'}
                        className="text-[10px] px-1.5 py-0"
                      >
                        {entry.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right text-xs tabular-nums text-muted-foreground">
                      {entry.duration_ms != null ? `${entry.duration_ms}ms` : '—'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            {/* Pagination */}
            <div className="flex items-center justify-between px-4 py-2 border-t border-border">
              <span className="text-xs text-muted-foreground">
                Showing {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total}
              </span>
              <div className="flex items-center gap-1">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  disabled={page <= 1}
                  className="h-7 w-7 p-0"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </Button>
                <span className="text-xs px-2 text-foreground/80">
                  {page} / {totalPages}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages}
                  className="h-7 w-7 p-0"
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          </>
        )}
      </SectionCard>
    </div>
  );
}
