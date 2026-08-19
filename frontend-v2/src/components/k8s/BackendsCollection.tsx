/**
 * BackendsCollection — Shows all K8s Services cross-referenced with route backends.
 *
 * Displays which Services are mapped (referenced by routes) vs unmapped (available
 * but not yet used as a backend). Part of TOPO-002.
 */

import { useState, useMemo } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';

import { Input } from '@/components/ui/input';
import {
  Server, Search, CheckCircle2, Circle, ChevronDown, ChevronRight,
  Route, Globe, Network,
} from 'lucide-react';
import { useBnkData } from '@/hooks/k8s/useBnk';
import { SkeletonTable } from '@/components/ui/skeleton-table';
import { EmptyState } from '@/components/ui/empty-state';
import type { BnkBackendEntry } from '@/types/f5bnk';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type FilterMode = 'all' | 'mapped' | 'unmapped';

interface BackendsCollectionProps {
  clusterId: number;
  namespace?: string;
}

// ---------------------------------------------------------------------------
// Summary Card
// ---------------------------------------------------------------------------

function SummaryCard({
  label,
  value,
  icon: Icon,
  active,
  onClick,
  color,
}: {
  label: string;
  value: number;
  icon: React.ComponentType<{ className?: string }>;
  active?: boolean;
  onClick?: () => void;
  color?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex items-center gap-3 px-4 py-3 rounded-lg border transition-all text-left',
        active
          ? 'border-primary/50 bg-primary/10 ring-1 ring-primary/30'
          : 'border-border bg-card hover:border-foreground/20',
      )}
    >
      <Icon className={cn('h-5 w-5 flex-shrink-0', color || 'text-muted-foreground')} />
      <div>
        <div className="text-2xl font-bold text-foreground">
          {value}
        </div>
        <div className="text-xs text-muted-foreground">
          {label}
        </div>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Backend Row
// ---------------------------------------------------------------------------

function BackendRow({
  backend,
  onClickRoute,
}: {
  backend: BnkBackendEntry;
  onClickRoute?: (kind: string, name: string, namespace: string) => void;
}) {
  const [expanded, setExpanded] = useState(backend.mapped);
  const portsStr = backend.ports.map(p => `${p.port}/${p.protocol}`).join(', ');

  return (
    <div className="border-b last:border-b-0 border-border transition-colors">
      {/* Main row */}
      <div
        className="flex items-center gap-3 px-4 py-2.5 text-sm cursor-pointer transition-colors hover:bg-muted/50"
        onClick={() => setExpanded(!expanded)}
      >
        {/* Expand chevron */}
        <div className="w-4 flex-shrink-0">
          {backend.routeRefs.length > 0 ? (
            expanded ? (
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
            )
          ) : null}
        </div>

        {/* Mapped indicator */}
        {backend.mapped ? (
          <CheckCircle2 className="h-4 w-4 flex-shrink-0 text-success" />
        ) : (
          <Circle className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
        )}

        {/* Name */}
        <span className="font-medium flex-1 truncate text-foreground">
          {backend.name}
        </span>

        {/* Namespace */}
        <Badge variant="outline" className="text-xs">
          {backend.namespace}
        </Badge>

        {/* Type */}
        {backend.type === 'NodePort' ? (
          <Badge variant="warning" className="text-xs w-20 justify-center">
            {backend.type}
          </Badge>
        ) : backend.type === 'LoadBalancer' ? (
          <Badge variant="info" className="text-xs w-20 justify-center">
            {backend.type}
          </Badge>
        ) : (
          <Badge variant="secondary" className="text-xs w-20 justify-center">
            {backend.type}
          </Badge>
        )}

        {/* Ports */}
        <span className="text-xs w-24 text-right tabular-nums text-muted-foreground">
          {portsStr || '—'}
        </span>

        {/* Route count */}
        {backend.mapped ? (
          <Badge variant="success" className="text-xs w-16 justify-center">
            {backend.routeRefs.length} {backend.routeRefs.length === 1 ? 'route' : 'routes'}
          </Badge>
        ) : (
          <Badge variant="secondary" className="text-xs w-16 justify-center">
            {backend.routeRefs.length} {backend.routeRefs.length === 1 ? 'route' : 'routes'}
          </Badge>
        )}
      </div>

      {/* Expanded: route references */}
      {expanded && backend.routeRefs.length > 0 && (
        <div className="pl-14 pr-4 pb-2 space-y-1 bg-muted/50">
          {backend.routeRefs.map((ref, i) => (
            <div
              key={`${ref.namespace}/${ref.name}-${i}`}
              className="flex items-center gap-2 text-xs py-1 px-2 rounded text-foreground/80"
            >
              <Route className="h-3 w-3 flex-shrink-0 text-primary" />
              <span className="text-xs text-muted-foreground">
                {ref.kind}:
              </span>
              {onClickRoute ? (
                <button
                  onClick={(e) => { e.stopPropagation(); onClickRoute(ref.kind, ref.name, ref.namespace); }}
                  className="font-medium transition-colors hover:text-primary hover:underline"
                >
                  {ref.name}
                </button>
              ) : (
                <span className="font-medium">{ref.name}</span>
              )}
              <Globe className="h-3 w-3 flex-shrink-0 text-muted-foreground ml-2" />
              <span className="text-muted-foreground">
                {ref.gatewayName}/{ref.listenerName}
              </span>
              {ref.port && (
                <>
                  <span className="text-muted-foreground/60">|</span>
                  <span>port {ref.port}</span>
                </>
              )}
              {(() => {
                // Prefer the analyzer's computed weight over the declared spec
                // weight; fall back to `weight` when the analyzer didn't weight
                // this backend (#8). Hide the default of 1 as before.
                const shownWeight = ref.effectiveWeight ?? ref.weight;
                if (shownWeight == null || shownWeight === 1) return null;
                return (
                  <>
                    <span className="text-muted-foreground/60">|</span>
                    <span>weight {shownWeight}</span>
                  </>
                );
              })()}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export function BackendsCollection({ clusterId, namespace }: BackendsCollectionProps) {
  const { data, isLoading, error } = useBnkData(clusterId, { namespace });

  const [filter, setFilter] = useState<FilterMode>('all');
  const [search, setSearch] = useState('');

  const backends = useMemo(() => data?.backends ?? [], [data?.backends]);

  const counts = useMemo(() => {
    const mapped = backends.filter(b => b.mapped).length;
    const namespaces = new Set(backends.map(b => b.namespace)).size;
    return { total: backends.length, mapped, unmapped: backends.length - mapped, namespaces };
  }, [backends]);

  const filtered = useMemo(() => {
    let result = backends;
    if (filter === 'mapped') result = result.filter(b => b.mapped);
    if (filter === 'unmapped') result = result.filter(b => !b.mapped);
    if (search) {
      const q = search.toLowerCase();
      result = result.filter(b =>
        b.name.toLowerCase().includes(q) ||
        b.namespace.toLowerCase().includes(q) ||
        b.type.toLowerCase().includes(q)
      );
    }
    return result;
  }, [backends, filter, search]);

  if (isLoading) {
    return <SkeletonTable rows={8} columns={5} />;
  }

  if (error) {
    return (
      <EmptyState
        icon={Server}
        title="Failed to load backends"
        description={String(error)}
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* Summary Cards */}
      <div className="grid grid-cols-4 gap-3">
        <SummaryCard
          label="Total Services"
          value={counts.total}
          icon={Server}
          active={filter === 'all'}
          onClick={() => setFilter('all')}
        />
        <SummaryCard
          label="Mapped"
          value={counts.mapped}
          icon={CheckCircle2}
          active={filter === 'mapped'}
          onClick={() => setFilter('mapped')}
          color="text-success"
        />
        <SummaryCard
          label="Unmapped"
          value={counts.unmapped}
          icon={Circle}
          active={filter === 'unmapped'}
          onClick={() => setFilter('unmapped')}
          color="text-muted-foreground"
        />
        <SummaryCard
          label="Namespaces"
          value={counts.namespaces}
          icon={Network}
        />
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder="Filter by name, namespace, or type…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-9"
        />
      </div>

      {/* Table Header */}
      <div className="rounded-lg border border-border bg-card overflow-hidden">
        <div className="flex items-center gap-3 px-4 py-2 text-xs font-medium border-b border-border text-muted-foreground bg-muted/50">
          <div className="w-4" />
          <div className="w-4" />
          <div className="flex-1">Service</div>
          <div className="w-20 text-center">Namespace</div>
          <div className="w-20 text-center">Type</div>
          <div className="w-24 text-right">Ports</div>
          <div className="w-16 text-center">Routes</div>
        </div>

        {/* Rows */}
        {filtered.length === 0 ? (
          <div className="py-12 text-center">
            <Server className="h-8 w-8 mx-auto mb-2 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              {search ? `No Services match "${search}"` : 'No Services found'}
            </p>
          </div>
        ) : (
          filtered.map((backend) => (
            <BackendRow
              key={`${backend.namespace}/${backend.name}`}
              backend={backend}
            />
          ))
        )}
      </div>

      {/* Footer */}
      <p className="text-xs text-center text-muted-foreground">
        Showing {filtered.length} of {counts.total} Services
        {filter !== 'all' && ` (${filter})`}
        {search && ` matching "${search}"`}
      </p>
    </div>
  );
}
