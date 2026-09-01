/**
 * Fleet page — D-020 redesign + D-022 fleet inventory.
 *
 * One coherent surface: bold heading, Tabs for Overview / DPU Infrastructure /
 * Inventory / Bulk Operations / Compliance, a 6-tile KPI strip, a flat cluster
 * table, and an inline compare panel.
 * No in-page sidebar, no per-status whole-card tints — status conveyed by small
 * Badge variants and 2px-border accents only.
 */

import { useState, useMemo, memo, useCallback, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { SectionCard } from '@/components/ui/section-card';
import { Tabs, TabsContent } from '@/components/ui/tabs';
import { ResourceViewTabs } from '@/components/layout/ResourceViewTabs';
import { cn } from '@/lib/utils';
import {
  Globe,
  Plus,
  Activity,
  ArrowUpDown,
  GitCompare,
  Eye,
  X,
  Server,
  Route,
  Layers,
  Clock,
  Upload,
  Cpu,
  RefreshCw,
  AlertTriangle,
  Tag,
  List,
  Pencil,
  Trash2,
  Flag,
  Users,
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Play,
  Square,
  Gauge,
} from 'lucide-react';
import {
  useFleetHealth,
  useFleetCompare,
  useFleetMembers,
  useFleetTargets,
  useCreateFleetTarget,
  useUpdateFleetTarget,
  useDeleteFleetTarget,
  useFleetMembersByFleet,
  useResolveFleetTarget,
  useStartBulkOp,
  useFleetBulkRun,
  useFleetBulkRuns,
  useResumeBulkRun,
  useCancelBulkRun,
  useFleetPolicies,
  useEvaluateFleetPolicy,
  useEnforceFleetPolicy,
  usePolicyCompliance,
  useCreateFleetPolicy,
  useUpdateFleetPolicy,
  useDeleteFleetPolicy,
  useFleetRollup,
  useFleetRollups,
  useUpdateFleetMemberLabels,
  useRunSelection,
  useStrategies,
  useCreateStrategy,
  useDeleteStrategy,
  useRerunBulkRun,
} from '@/hooks/useFleet';
import type { FleetLifecycleState, FleetOperationStrategy, FleetPolicy, FleetRollup, RunSelectionRequest, WorstState } from '@/types/fleet';
// useAllClusters removed — DpuInfrastructureView (which used it) relocated to /infrastructure.
import { ConfigPromotionWizard } from '@/components/fleet/ConfigPromotionWizard';
import { SelectorBuilder } from '@/components/fleet/SelectorBuilder';
import {
  FleetTrafficLights,
  EstateSummaryBar,
} from '@/components/fleet/FleetTrafficLights';
import { AddClusterFlowDialog } from '@/components/k8s/AddClusterFlowDialog';
import { PageHeader } from '@/components/layout/PageHeader';
import { usePageRefresh } from '@/hooks/usePageRefresh';
import { useBnkConsumption } from '@/hooks/useSystem';
import { BnkResourcesPanel } from '@/components/system/BnkResourcesPanel';
import { queryKeys } from '@/lib/queryKeys';

// DPFInfrastructurePanel is now rendered under /infrastructure (D-022 P6 IA).
// Import removed — Fleet no longer owns the DPU hardware view.
import { EmptyState } from '@/components/ui/empty-state';
import { TimeAgo } from '@/components/ui/TimeAgo';
import { ConnectionStatusBadge } from '@/components/ui/ConnectionStatusBadge';
import { ClusterStatusBadge } from '@/components/ui/ClusterStatusBadge';
import { useConnectivity } from '@/hooks/useConnectivity';
import { reachabilityKey } from '@/lib/api/connectivity';
import type {
  FleetBulkRun,
  FleetDecision,
  FleetMember,
  FleetOperatorHealth,
  FleetOperatorStatus,
  FleetCompareResult,
  FleetTarget,
} from '@/types/fleet';
import {
  isClusterConfigComparison,
  isGroupedMembersResponse,
  isHealthReportComparison,
} from '@/types/fleet';
import { getPlatformProfileLabel } from '@/lib/platform-context';
import { useProjects } from '@/hooks/useProjects';
import { SSHConnectivityBadge } from '@/components/ui/SSHConnectivityBadge';

// ──────────────────────────────────────────────────────────────────────────────
// Sort helpers
// ──────────────────────────────────────────────────────────────────────────────

const STATUS_PRIORITY: Record<FleetOperatorStatus, number> = {
  critical: 0,
  unhealthy: 0,
  warning: 1,
  degraded: 1,
  healthy: 2,
  offline: 3,
  unknown: 4,
};

type SortField = 'status' | 'name' | 'last_seen';

function sortOperators(
  ops: FleetOperatorHealth[],
  field: SortField,
  asc: boolean,
): FleetOperatorHealth[] {
  return [...ops].sort((a, b) => {
    let cmp = 0;
    switch (field) {
      case 'status':
        cmp = STATUS_PRIORITY[a.status] - STATUS_PRIORITY[b.status];
        break;
      case 'name':
        cmp = a.cluster_name.localeCompare(b.cluster_name);
        break;
      case 'last_seen': {
        const dateA = a.last_seen ? new Date(a.last_seen).getTime() : 0;
        const dateB = b.last_seen ? new Date(b.last_seen).getTime() : 0;
        cmp = dateB - dateA;
        break;
      }
    }
    return asc ? cmp : -cmp;
  });
}

// ──────────────────────────────────────────────────────────────────────────────
// Aggregate stats — six small KPI tiles
// ──────────────────────────────────────────────────────────────────────────────

function AggregateStats({
  total, healthy, stale, warning, critical, offline,
}: {
  total: number; healthy: number; stale: number; warning: number; critical: number; offline: number;
}) {
  const stats: { label: string; value: number; tone: 'foreground' | 'success' | 'warning' | 'destructive' | 'muted'; title?: string }[] = [
    { label: 'Total', value: total, tone: 'foreground' },
    { label: 'Healthy', value: healthy, tone: 'success' },
    {
      label: 'Stale',
      value: stale,
      tone: 'warning',
      title: 'Last reported healthy but currently unreachable from forge — operator may still be running, but we cannot verify.',
    },
    { label: 'Warning', value: warning, tone: 'warning' },
    { label: 'Critical', value: critical, tone: 'destructive' },
    { label: 'Offline', value: offline, tone: 'muted' },
  ];
  const toneText: Record<string, string> = {
    foreground: 'text-foreground',
    success: 'text-success',
    warning: 'text-warning',
    destructive: 'text-destructive',
    muted: 'text-muted-foreground',
  };
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      {stats.map((s) => (
        <div
          key={s.label}
          className="rounded-lg border border-border bg-card px-4 py-3 text-center"
          title={s.title}
        >
          <div className={cn('text-2xl font-bold tabular-nums', toneText[s.tone])}>{s.value}</div>
          <div className="text-xs text-muted-foreground mt-0.5">{s.label}</div>
        </div>
      ))}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Cluster row
// ──────────────────────────────────────────────────────────────────────────────

interface ClusterRowProps {
  op: FleetOperatorHealth;
  selected: boolean;
  onToggleSelect: (operatorId: number) => void;
  onViewHealth: (op: FleetOperatorHealth) => void;
  onViewConfig: (op: FleetOperatorHealth) => void;
  onViewDpf: (op: FleetOperatorHealth) => void;
}

const ClusterRow = memo(function ClusterRow({
  op, selected, onToggleSelect, onViewHealth, onViewConfig, onViewDpf,
}: ClusterRowProps) {
  const isOffline = op.status === 'offline';
  const bnkSeverity = op.bnk_severity ?? (op.status === 'offline' ? 'unknown' : op.status);
  const { states: connectivityStates } = useConnectivity();
  const conn = connectivityStates[reachabilityKey('cluster', op.cluster_id)];
  const isReachUnreachable = conn?.state === 'unreachable';
  const isStaleHealthy =
    isReachUnreachable && ['healthy', 'warning', 'degraded'].includes(op.status);

  return (
    <div
      className={cn(
        'flex items-center gap-4 px-4 py-3 border-b border-border last:border-0 transition-colors',
        isOffline && 'opacity-60',
        selected ? 'bg-primary/5' : 'hover:bg-muted/40',
      )}
    >
      <button
        type="button"
        onClick={() => onToggleSelect(op.operator_id)}
        className={cn(
          'h-5 w-5 rounded border flex items-center justify-center shrink-0 transition-colors',
          selected
            ? 'bg-primary border-primary text-primary-foreground'
            : 'border-border hover:border-foreground/30',
        )}
        aria-label={`Select ${op.cluster_name} for comparison`}
      >
        {selected && (
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        )}
      </button>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <h4 className="text-sm font-semibold text-foreground truncate">{op.cluster_name}</h4>
          <ClusterStatusBadge
            cluster={{ id: op.cluster_id, name: op.cluster_name }}
            showStatusLabel={isOffline || isReachUnreachable}
          />
          {/* Three rendering modes for the BNK severity:
              1. Operator offline → hide entirely (no signal to report).
              2. Cluster reachable + operator alive → BNK pill (truth).
              3. Stale-healthy → token-warning pill honest about what we know. */}
          {isOffline ? null : isStaleHealthy ? (
            <span
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border bg-warning/10 text-warning border-warning/20"
              title="Last reported healthy but currently unreachable from forge — operator may still be running, but we cannot verify."
            >
              <span className="h-1.5 w-1.5 rounded-full bg-warning" />
              <span className="capitalize">{op.status}</span>
              {op.last_seen && (
                <span className="opacity-70">· <TimeAgo dateStr={op.last_seen} /></span>
              )}
            </span>
          ) : (
            <ConnectionStatusBadge status={bnkSeverity} className="opacity-90" />
          )}
        </div>
        {isOffline && op.last_seen && (
          <div className="text-xs text-muted-foreground mt-0.5">Last seen: <TimeAgo dateStr={op.last_seen} /></div>
        )}
        <div className="text-xs text-muted-foreground mt-0.5">
          Platform: {getPlatformProfileLabel(op.detected_platform_profile)}
        </div>
        {op.health_issues && op.health_issues.length > 0 && (
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1">
            {op.health_issues.map((issue) => (
              <span
                key={issue.component}
                className={cn(
                  'inline-flex items-center gap-1 text-[11px]',
                  ['critical', 'unhealthy'].includes(issue.severity) ? 'text-destructive' : 'text-warning',
                )}
              >
                <AlertTriangle className="h-3 w-3 shrink-0" />
                {issue.message}
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="hidden md:flex flex-col items-center w-20">
        <div className="text-xs font-medium text-foreground tabular-nums">{op.bnk_version || '—'}</div>
        <div className="text-[10px] text-muted-foreground uppercase tracking-wider">BNK</div>
      </div>
      <div className="hidden sm:flex items-center gap-1 w-14 justify-center text-muted-foreground">
        <Route className="h-3 w-3" />
        <span className="text-xs font-medium text-foreground tabular-nums">{op.route_count}</span>
      </div>
      <div className="hidden sm:flex items-center gap-1 w-14 justify-center text-muted-foreground">
        <Server className="h-3 w-3" />
        <span className="text-xs font-medium text-foreground tabular-nums">{op.tmm_count}</span>
      </div>
      <div className="hidden lg:flex items-center gap-1 w-14 justify-center text-muted-foreground">
        <Layers className="h-3 w-3" />
        <span className="text-xs font-medium text-foreground tabular-nums">{op.gateway_count}</span>
      </div>
      <div className="hidden lg:flex items-center gap-1 w-14 justify-center">
        {op.dpf_detected ? (
          <button
            onClick={() => onViewDpf(op)}
            className="flex items-center gap-1 hover:opacity-80 transition-opacity"
            title="View DPU Infrastructure"
          >
            <Cpu className={cn('h-3 w-3', op.dpf_status === 'ready' ? 'text-success' : 'text-warning')} />
            <span className="text-xs font-medium text-foreground underline decoration-dotted underline-offset-2 tabular-nums">
              {op.dpu_count ?? 0}
            </span>
          </button>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </div>
      <div className="hidden lg:flex flex-col items-center w-20">
        {isOffline ? (
          <div className="text-xs text-muted-foreground"><TimeAgo dateStr={op.last_seen} /></div>
        ) : (
          <>
            <div className="text-xs font-medium text-foreground tabular-nums">{op.uptime}</div>
            <div className="text-[10px] text-muted-foreground uppercase tracking-wider">uptime</div>
          </>
        )}
      </div>
      <div className="flex items-center gap-1 shrink-0">
        <Button
          variant="ghost"
          size="sm"
          title="View Health"
          onClick={() => onViewHealth(op)}
          className="h-7 w-7 p-0"
        >
          <Activity className="h-3.5 w-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          title="View Config"
          onClick={() => onViewConfig(op)}
          className="h-7 w-7 p-0"
        >
          <Eye className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
});

// ──────────────────────────────────────────────────────────────────────────────
// Compare panel
// ──────────────────────────────────────────────────────────────────────────────

function ComparePanel({
  operators, selectedIds, onClose,
}: {
  operators: FleetOperatorHealth[]; selectedIds: number[]; onClose: () => void;
}) {
  const compare = useFleetCompare();
  const [operatorAId, setOperatorAId] = useState<number>(selectedIds[0] ?? 0);
  const [operatorBId, setOperatorBId] = useState<number>(selectedIds[1] ?? 0);

  const handleCompare = () => {
    if (operatorAId && operatorBId && operatorAId !== operatorBId) {
      compare.mutate({ operatorAId, operatorBId });
    }
  };

  return (
    <SectionCard
      title="Compare configs"
      compact
      className="relative"
    >
      <Button
        variant="ghost"
        size="sm"
        onClick={onClose}
        className="absolute top-3 right-3 h-7 w-7 p-0"
        aria-label="Close compare panel"
      >
        <X className="h-4 w-4" />
      </Button>
      <div className="space-y-3">
        <div className="flex flex-col md:flex-row md:items-center gap-3">
          <select
            value={operatorAId}
            onChange={(e) => setOperatorAId(Number(e.target.value))}
            className="flex-1 rounded-md border border-border bg-card text-foreground px-3 py-2 text-sm"
          >
            <option value={0}>Select cluster A…</option>
            {operators.map((op) => (
              <option key={op.operator_id} value={op.operator_id}>{op.cluster_name}</option>
            ))}
          </select>
          <span className="text-xs text-muted-foreground font-medium uppercase tracking-wider">vs</span>
          <select
            value={operatorBId}
            onChange={(e) => setOperatorBId(Number(e.target.value))}
            className="flex-1 rounded-md border border-border bg-card text-foreground px-3 py-2 text-sm"
          >
            <option value={0}>Select cluster B…</option>
            {operators.map((op) => (
              <option key={op.operator_id} value={op.operator_id}>{op.cluster_name}</option>
            ))}
          </select>
          <Button
            onClick={handleCompare}
            disabled={!operatorAId || !operatorBId || operatorAId === operatorBId || compare.isPending}
            size="sm"
          >
            {compare.isPending ? 'Comparing…' : 'Compare'}
          </Button>
        </div>
        {compare.data && <CompareResult result={compare.data} />}
      </div>
    </SectionCard>
  );
}

function CompareResult({ result }: { result: FleetCompareResult }) {
  if (!result) return null;
  const platformContext = result.platform_context;
  const mixedPlatformProfiles = platformContext?.mixed_platform_profiles;
  const caveatText = platformContext?.comparison_caveats?.[0];

  if (isHealthReportComparison(result)) {
    return (
      <div className="space-y-3">
        {mixedPlatformProfiles && caveatText && (
          <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
            {caveatText}
          </div>
        )}
        <div className="text-xs text-muted-foreground">{result.summary}</div>
        <div className="rounded-md border border-border bg-muted/40 p-3 space-y-2">
          {result.differences.length === 0 ? (
            <div className="text-sm text-muted-foreground">No health-report differences found.</div>
          ) : (
            result.differences.map((diff) => (
              <div
                key={diff.key}
                className="grid grid-cols-[160px_1fr_1fr] gap-3 text-sm items-center text-foreground/80"
              >
                <div className="font-medium text-muted-foreground">{diff.field}</div>
                <div className="rounded px-2 py-1 text-xs bg-card border border-border">
                  <span className="text-muted-foreground">{result.operator_a}: </span>
                  {String(diff.value_a ?? '—')}
                </div>
                <div className="rounded px-2 py-1 text-xs bg-card border border-border">
                  <span className="text-muted-foreground">{result.operator_b}: </span>
                  {String(diff.value_b ?? '—')}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    );
  }
  if (!isClusterConfigComparison(result)) return null;
  const r = result;
  const resourceDiffs =
    (r.resources?.only_in_a?.length || 0) +
    (r.resources?.only_in_b?.length || 0) +
    (r.resources?.changed?.length || 0);
  const moduleDiffs =
    (r.modules?.only_in_a?.length || 0) +
    (r.modules?.only_in_b?.length || 0) +
    (r.modules?.changed?.length || 0);

  const alignResourceRows = (() => {
    const left = r.resources.only_in_a;
    const right = r.resources.only_in_b;
    const max = Math.max(left.length, right.length);
    return Array.from({ length: max }, (_, i) => ({ left: left[i] ?? null, right: right[i] ?? null }));
  })();

  const alignModuleRows = (() => {
    const left = r.modules.only_in_a;
    const right = r.modules.only_in_b;
    const max = Math.max(left.length, right.length);
    return Array.from({ length: max }, (_, i) => ({ left: left[i] ?? null, right: right[i] ?? null }));
  })();

  const renderResourceRef = (item: { kind: string; name: string; namespace?: string }) => (
    <div className="rounded-md border border-border bg-card px-3 py-2 text-xs text-foreground/80">
      <div className="font-medium text-foreground">{item.kind}</div>
      <div>{item.name}</div>
      {item.namespace && <div className="text-muted-foreground">ns: {item.namespace}</div>}
    </div>
  );

  return (
    <div className="space-y-4">
      {mixedPlatformProfiles && caveatText && (
        <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
          {caveatText}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{r.summary}</span>
        <Badge variant="outline">{resourceDiffs} resource diffs</Badge>
        <Badge variant="outline">{moduleDiffs} module diffs</Badge>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {[
          { label: `Only in ${r.operator_a}`, value: r.resources.only_in_a.length },
          { label: `Only in ${r.operator_b}`, value: r.resources.only_in_b.length },
          { label: 'Changed resources', value: r.resources.changed.length },
          { label: 'Changed modules', value: r.modules.changed.length },
        ].map((tile) => (
          <div key={tile.label} className="rounded-md border border-border bg-muted/40 p-3">
            <div className="text-xs text-muted-foreground">{tile.label}</div>
            <div className="mt-1 text-2xl font-semibold text-foreground tabular-nums">{tile.value}</div>
          </div>
        ))}
      </div>

      <div className="rounded-md border border-border bg-card overflow-hidden">
        <div className="grid grid-cols-2 gap-px bg-border">
          <div className="p-3 bg-card">
            <div className="text-sm font-semibold text-foreground">{r.operator_a}</div>
            <div className="text-xs text-muted-foreground">Only in {r.cluster_a}</div>
          </div>
          <div className="p-3 bg-card">
            <div className="text-sm font-semibold text-foreground">{r.operator_b}</div>
            <div className="text-xs text-muted-foreground">Only in {r.cluster_b}</div>
          </div>
        </div>
        <div className="max-h-80 overflow-y-auto">
          {alignResourceRows.length === 0 ? (
            <div className="p-3 text-sm text-muted-foreground">No unique resources on either side.</div>
          ) : (
            alignResourceRows.map((row, idx) => (
              <div key={`resource-row-${idx}`} className="grid grid-cols-2 gap-px bg-border">
                <div className="p-2 min-h-[84px] bg-card">
                  {row.left ? renderResourceRef(row.left) : (
                    <div className="h-full flex items-center text-xs text-muted-foreground">—</div>
                  )}
                </div>
                <div className="p-2 min-h-[84px] bg-card">
                  {row.right ? renderResourceRef(row.right) : (
                    <div className="h-full flex items-center text-xs text-muted-foreground">—</div>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-md border border-border bg-muted/40 p-3 space-y-3">
          <div>
            <div className="text-sm font-semibold text-foreground">Changed resources</div>
            <div className="text-xs text-muted-foreground">
              Resources that exist in both clusters but differ in spec.
            </div>
          </div>
          <div className="space-y-2 max-h-64 overflow-y-auto">
            {r.resources.changed.length === 0 ? (
              <div className="text-sm text-muted-foreground">No changed resources.</div>
            ) : (
              r.resources.changed.map((item) => (
                <div
                  key={item.resource}
                  className="rounded-md border border-border bg-card px-3 py-2 text-xs text-foreground/80"
                >
                  <div className="font-medium text-foreground">{item.kind}</div>
                  <div>{item.resource}</div>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="rounded-md border border-border bg-muted/40 p-3 space-y-3">
          <div>
            <div className="text-sm font-semibold text-foreground">Module differences</div>
            <div className="text-xs text-muted-foreground">OpenTofu / module config drift summary.</div>
          </div>
          <div className="space-y-3 max-h-64 overflow-y-auto">
            {moduleDiffs === 0 ? (
              <div className="text-sm text-muted-foreground">No module differences.</div>
            ) : (
              <>
                <div className="rounded-md border border-border overflow-hidden">
                  <div className="grid grid-cols-2 gap-px bg-border">
                    <div className="px-3 py-2 text-xs font-semibold bg-card text-foreground">{r.operator_a}</div>
                    <div className="px-3 py-2 text-xs font-semibold bg-card text-foreground">{r.operator_b}</div>
                  </div>
                  {alignModuleRows.length === 0 ? (
                    <div className="px-3 py-2 text-xs text-muted-foreground bg-card">No unique modules.</div>
                  ) : (
                    alignModuleRows.map((row, idx) => (
                      <div key={`module-row-${idx}`} className="grid grid-cols-2 gap-px bg-border">
                        <div className="px-3 py-2 text-xs bg-card text-foreground/80">{row.left ?? '—'}</div>
                        <div className="px-3 py-2 text-xs bg-card text-foreground/80">{row.right ?? '—'}</div>
                      </div>
                    ))
                  )}
                </div>
                {r.modules.changed.map((item) => (
                  <div
                    key={item.module_path}
                    className="rounded-md border border-border bg-card px-3 py-2 text-xs text-foreground/80"
                  >
                    Changed: {item.module_path}
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Overview view
// ──────────────────────────────────────────────────────────────────────────────

function OverviewView({ fleetId }: { fleetId?: number }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: fleet, isLoading, isFetching, isError, error } = useFleetHealth();
  const { states: connectivityStates } = useConnectivity();

  // When fleetId is set, resolve this fleet's cluster member ids and filter operators.
  const { data: fleetMembers } = useFleetMembersByFleet(fleetId ?? null);
  const fleetClusterIds = useMemo(() => {
    if (!fleetId || !fleetMembers) return null;
    return new Set(
      fleetMembers.members
        .filter((m) => m.member_type === 'cluster')
        .map((m) => m.member_id)
    );
  }, [fleetId, fleetMembers]);

  // counts — fleet-scoped when fleetId is set, global otherwise.
  // Must be declared AFTER `operators` would be available, but `operators` is
  // declared further below. We compute it inline here to avoid TDZ by reading
  // from fleet?.operators directly for the global branch and computing the
  // fleet branch after `operators` is resolved via the closure over
  // `fleetClusterIds`. We re-declare `operators` inline for the fleet-scoped
  // branch to keep a single useMemo.
  const counts = useMemo(() => {
    if (!fleet) return { total: 0, healthy: 0, stale: 0, warning: 0, critical: 0, offline: 0 };

    if (!fleetId || fleetClusterIds === null) {
      // Global view — use the aggregate from the API response directly.
      const stale = (fleet.operators ?? []).filter((op) => {
        if (!['healthy', 'warning', 'degraded'].includes(op.status)) return false;
        const conn = connectivityStates[reachabilityKey('cluster', op.cluster_id)];
        return conn?.state === 'unreachable';
      }).length;
      return {
        total: fleet.total_clusters,
        healthy: Math.max(0, fleet.healthy - stale),
        stale,
        warning: fleet.warning,
        critical: fleet.critical,
        offline: fleet.offline,
      };
    }

    // Fleet-scoped view — derive from the filtered operator list.
    const filtered = (fleet.operators ?? []).filter((op) => fleetClusterIds.has(op.cluster_id));
    const stale = filtered.filter((op) => {
      if (!['healthy', 'warning', 'degraded'].includes(op.status)) return false;
      const conn = connectivityStates[reachabilityKey('cluster', op.cluster_id)];
      return conn?.state === 'unreachable';
    }).length;
    return {
      total: filtered.length,
      healthy: Math.max(0, filtered.filter((op) => op.status === 'healthy').length - stale),
      stale,
      warning: filtered.filter((op) => op.status === 'warning').length,
      critical: filtered.filter((op) => op.status === 'critical' || op.status === 'unhealthy').length,
      offline: filtered.filter((op) => op.status === 'offline').length,
    };
  }, [fleet, fleetId, fleetClusterIds, connectivityStates]);

  const [showCompare, setShowCompare] = useState(false);
  const [showPromotionWizard, setShowPromotionWizard] = useState(false);
  const [showAddCluster, setShowAddCluster] = useState(false);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [sortField, setSortField] = useState<SortField>('status');
  const [sortAsc, setSortAsc] = useState(true);

  const handleRefresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['fleet'] });
    queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', 'connectivity'] });
  }, [queryClient]);

  // When scoped to a fleet, filter operators to those in the fleet's cluster members.
  const operators = useMemo(() => {
    const all = fleet?.operators ?? [];
    if (fleetClusterIds === null) return all;
    return all.filter((op) => fleetClusterIds.has(op.cluster_id));
  }, [fleet?.operators, fleetClusterIds]);

  const sortedOperators = useMemo(
    () => sortOperators(operators, sortField, sortAsc),
    [operators, sortField, sortAsc],
  );

  const handleSort = (field: SortField) => {
    if (sortField === field) setSortAsc(!sortAsc);
    else {
      setSortField(field);
      setSortAsc(true);
    }
  };

  const toggleSelect = useCallback((id: number) => {
    setSelectedIds((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= 2) return [prev[1], id];
      return [...prev, id];
    });
  }, []);

  const handleViewHealth = useCallback(
    (op: FleetOperatorHealth) => {
      localStorage.setItem('bnk-forge-bnk-cluster', String(op.cluster_id));
      navigate('/bnk');
    },
    [navigate],
  );

  const handleViewConfig = useCallback(
    (op: FleetOperatorHealth) => {
      localStorage.setItem('bnk-forge-k8s-cluster', String(op.cluster_id));
      navigate('/kubernetes');
    },
    [navigate],
  );

  const handleViewDpf = useCallback(
    (op: FleetOperatorHealth) => {
      // D-022 P6 IA: DPU hardware view moved to the Infrastructure section.
      navigate(`/infrastructure?tab=dpus&cluster=${op.cluster_id}`);
    },
    [navigate],
  );

  const canCompare = selectedIds.length === 2;

  return (
    <div className="space-y-6">
      {/* Action bar — global actions (Refresh / Promote config / Add cluster) are
          hidden when rendered as a fleet Health facet (fleetId set); in that context
          the view is read-only health for the fleet's members only. */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs text-muted-foreground">
          {operators.length > 0 &&
            `${operators.length} cluster${operators.length === 1 ? '' : 's'} · select two to compare`}
        </div>
        <div className="flex items-center gap-2">
          {!fleetId && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleRefresh}
              disabled={isFetching}
              title="Re-check connectivity for all clusters"
            >
              <RefreshCw className={cn('h-4 w-4 mr-1.5', isFetching && 'animate-spin')} />
              {isFetching && !isLoading ? 'Checking…' : 'Refresh'}
            </Button>
          )}
          {canCompare && (
            <Button variant="outline" size="sm" onClick={() => setShowCompare(true)}>
              <GitCompare className="h-4 w-4 mr-1.5" />
              Compare selected
            </Button>
          )}
          {!fleetId && operators.length >= 2 && (
            <Button variant="outline" size="sm" onClick={() => setShowPromotionWizard(true)}>
              <Upload className="h-4 w-4 mr-1.5" />
              Promote config
            </Button>
          )}
          {!fleetId && (
            <Button variant="outline" size="sm" onClick={() => setShowAddCluster(true)}>
              <Plus className="h-4 w-4 mr-1.5" />
              Add cluster
            </Button>
          )}
        </div>
      </div>

      {/* Background refetch hint */}
      {isFetching && !isLoading && (
        <div className="flex items-center gap-2 rounded-md border border-info/20 bg-info/5 px-3 py-2 text-xs text-info">
          <RefreshCw className="h-3 w-3 animate-spin" />
          Refreshing cluster health data…
        </div>
      )}

      {/* KPI tiles */}
      {fleet && (
        <AggregateStats
          total={counts.total}
          healthy={counts.healthy}
          stale={counts.stale}
          warning={counts.warning}
          critical={counts.critical}
          offline={counts.offline}
        />
      )}

      {/* Platform caveat */}
      {fleet?.platform_context?.mixed_platform_profiles &&
        fleet.platform_context.comparison_caveats?.[0] && (
          <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
            {fleet.platform_context.comparison_caveats[0]}
          </div>
        )}

      {/* Error */}
      {isError && (
        <SectionCard>
          <div className="text-center py-2">
            <Activity className="h-10 w-10 mx-auto mb-3 text-destructive" />
            <h3 className="text-sm font-semibold text-foreground mb-1">Failed to load fleet health</h3>
            <p className="text-xs text-muted-foreground">
              {error instanceof Error ? error.message : 'Unable to reach the backend.'}
            </p>
          </div>
        </SectionCard>
      )}

      {/* Loading */}
      {isLoading && (
        <SectionCard>
          <div className="text-center space-y-4 py-2">
            <RefreshCw className="h-8 w-8 mx-auto text-primary animate-spin" />
            <div>
              <h3 className="text-sm font-semibold text-foreground">Querying cluster health…</h3>
              <p className="text-xs text-muted-foreground mt-1 max-w-prose mx-auto">
                Connecting to each cluster via kubeconfig, fetching BNK resources, and analyzing
                health. This may take 10–30 seconds for on-prem or high-latency clusters.
              </p>
            </div>
            <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-1 text-xs text-muted-foreground pt-2">
              {['TCP connectivity check', 'Kubernetes API query', 'Health analysis'].map((s) => (
                <span key={s} className="flex items-center gap-1.5">
                  <span className="h-1.5 w-1.5 rounded-full bg-primary animate-pulse" />
                  {s}
                </span>
              ))}
            </div>
          </div>
        </SectionCard>
      )}

      {/* Empty */}
      {!isLoading && operators.length === 0 && (
        <EmptyState
          icon={Globe}
          title={fleetId ? 'No cluster members in this fleet' : 'No clusters added yet'}
          description={
            fleetId
              ? 'This fleet has no cluster members. Add clusters via the Members tab.'
              : 'Add a Kubernetes cluster to see fleet health. Clusters are queried directly via kubeconfig — no operator installation required.'
          }
          action={
            !fleetId
              ? {
                  label: 'Add your first cluster',
                  onClick: () => setShowAddCluster(true),
                  icon: <Plus className="h-4 w-4 mr-1.5" />,
                }
              : undefined
          }
        />
      )}

      {/* Cluster table */}
      {!isLoading && operators.length > 0 && (
        <SectionCard compact>
          <div className="overflow-x-auto">
            <div className="rounded-md border border-border overflow-hidden">
              <div className="flex items-center gap-4 px-4 py-2 bg-muted/40 border-b border-border text-xs font-medium uppercase tracking-wider text-muted-foreground">
                <div className="w-5" />
                <button
                  type="button"
                  className="flex-1 flex items-center gap-1 hover:text-foreground transition-colors text-left"
                  onClick={() => handleSort('name')}
                >
                  Cluster
                  <ArrowUpDown className="h-3 w-3" />
                </button>
                <div className="hidden md:block w-20 text-center">BNK</div>
                <div className="hidden sm:flex w-14 items-center justify-center gap-1">
                  <Route className="h-3 w-3" />
                  Routes
                </div>
                <div className="hidden sm:flex w-14 items-center justify-center gap-1">
                  <Server className="h-3 w-3" />
                  TMM
                </div>
                <div className="hidden lg:flex w-14 items-center justify-center gap-1">
                  <Layers className="h-3 w-3" />
                  GW
                </div>
                <div className="hidden lg:flex w-14 items-center justify-center gap-1">
                  <Cpu className="h-3 w-3" />
                  DPU
                </div>
                <button
                  type="button"
                  className="hidden lg:flex w-20 items-center justify-center gap-1 hover:text-foreground transition-colors"
                  onClick={() => handleSort('last_seen')}
                >
                  <Clock className="h-3 w-3" />
                  Uptime
                  <ArrowUpDown className="h-3 w-3" />
                </button>
                <div className="w-[68px] text-center">Actions</div>
              </div>
              {sortedOperators.map((op) => (
                <ClusterRow
                  key={op.operator_uuid}
                  op={op}
                  selected={selectedIds.includes(op.operator_id)}
                  onToggleSelect={toggleSelect}
                  onViewHealth={handleViewHealth}
                  onViewConfig={handleViewConfig}
                  onViewDpf={handleViewDpf}
                />
              ))}
            </div>
          </div>
        </SectionCard>
      )}

      {showCompare && (
        <ComparePanel
          operators={operators}
          selectedIds={selectedIds}
          onClose={() => setShowCompare(false)}
        />
      )}

      <ConfigPromotionWizard
        open={showPromotionWizard}
        onOpenChange={setShowPromotionWizard}
        operators={operators}
      />
      <AddClusterFlowDialog open={showAddCluster} onOpenChange={setShowAddCluster} />
    </div>
  );
}

// DpuInfrastructureView removed — D-022 P6 IA: DPU hardware view relocated to
// the top-level Infrastructure page (/infrastructure). See:
//   frontend-v2/src/pages/Infrastructure.tsx
//   frontend-v2/src/components/infrastructure/DpuInfrastructurePanel.tsx

// ──────────────────────────────────────────────────────────────────────────────
// Inventory view — D-022 host-inclusive fleet view (clusters + hosts + DPUs)
// ──────────────────────────────────────────────────────────────────────────────

const MEMBER_TYPE_LABELS: Record<string, string> = {
  'cluster': 'Cluster',
  'bare-metal-host': 'Bare-Metal Host',
  'dpu': 'DPU',
};

const MEMBER_TYPE_ICONS: Record<string, typeof Globe> = {
  'cluster': Globe,
  'bare-metal-host': Server,
  'dpu': Cpu,
};

function MemberTypeBadge({ memberType }: { memberType: string }) {
  const label = MEMBER_TYPE_LABELS[memberType] ?? memberType;
  // Use design-token-adjacent semantic colors rather than raw zinc/blue/emerald/purple.
  const colorMap: Record<string, string> = {
    'cluster': 'bg-primary/10 text-primary',
    'bare-metal-host': 'bg-success/10 text-success',
    'dpu': 'bg-info/10 text-info',
  };
  const color = colorMap[memberType] ?? 'bg-muted text-muted-foreground';
  return (
    <span className={cn('inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium', color)}>
      {label}
    </span>
  );
}

/** Lifecycle state badge — derived unified state per fleet member (D-022 P4). */
function LifecycleBadge({ state }: { state: FleetLifecycleState }) {
  if (!state) return null;
  // status-color-as-badge: pill with a status dot, never a full-card tint.
  const pillMap: Record<string, { pill: string; dot: string; extra?: string }> = {
    provisioning: { pill: 'bg-warning/10 text-warning border-warning/20', dot: 'bg-warning' },
    active: { pill: 'bg-success/10 text-success border-success/20', dot: 'bg-success' },
    draining: { pill: 'bg-warning/10 text-warning border-warning/20', dot: 'bg-warning' },
    decommissioned: { pill: 'bg-muted text-muted-foreground border-border', dot: 'bg-muted-foreground', extra: 'line-through' },
    unknown: { pill: 'bg-destructive/10 text-destructive border-destructive/20', dot: 'bg-destructive' },
  };
  const { pill, dot, extra } = pillMap[state] ?? { pill: 'bg-muted text-muted-foreground border-border', dot: 'bg-muted-foreground' };
  return (
    <span className={cn('inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border', pill, extra)}>
      <span className={cn('h-1.5 w-1.5 rounded-full', dot)} />
      {state}
    </span>
  );
}

function LabelChips({ labels }: { labels: Record<string, string> }) {
  const entries = Object.entries(labels);
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {entries.map(([k, v]) => (
        <span
          key={k}
          className="inline-flex items-center gap-0.5 rounded bg-muted/50 border border-border px-1.5 py-0.5 text-xs text-foreground/70"
        >
          <span className="text-muted-foreground">{k}=</span>{v}
        </span>
      ))}
    </div>
  );
}

interface MemberRowProps {
  member: FleetMember;
  selected?: boolean;
  onToggleSelect?: (id: number) => void;
  /** SSH credential id from the associated project, if any. */
  sshCredentialId?: number | null;
}

function MemberRow({ member, selected, onToggleSelect, sshCredentialId }: MemberRowProps) {
  const Icon = MEMBER_TYPE_ICONS[member.member_type] ?? Server;
  const selectable = onToggleSelect != null;
  return (
    <div
      className={cn(
        'flex items-start gap-4 px-4 py-3 border-b border-border last:border-0 transition-colors',
        selectable && (selected ? 'bg-primary/5' : 'hover:bg-muted/40'),
      )}
    >
      {/* Checkbox — only rendered when inside a fleet-scoped InventoryView */}
      {selectable && (
        <button
          type="button"
          onClick={() => onToggleSelect(member.id)}
          className={cn(
            'mt-0.5 h-5 w-5 rounded border flex items-center justify-center shrink-0 transition-colors',
            selected
              ? 'bg-primary border-primary text-primary-foreground'
              : 'border-border hover:border-foreground/30',
          )}
          aria-label={`Select ${member.name}`}
        >
          {selected && (
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          )}
        </button>
      )}
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-foreground truncate">{member.name}</span>
          <MemberTypeBadge memberType={member.member_type} />
          <LifecycleBadge state={member.lifecycle_state} />
          {/* SSH jumphost reachability — self-hides when no credential is associated */}
          <SSHConnectivityBadge variant="compact" credentialId={sshCredentialId} />
          {member.health_status && (
            <span className="text-xs text-muted-foreground">{member.health_status}</span>
          )}
        </div>
        {Object.keys(member.discovered_labels).length > 0 && (
          <div className="flex items-center gap-1">
            <span className="text-xs text-muted-foreground shrink-0">discovered:</span>
            <LabelChips labels={member.discovered_labels} />
          </div>
        )}
        {Object.keys(member.assigned_labels).length > 0 && (
          <div className="flex items-center gap-1">
            <Tag className="h-3 w-3 shrink-0 text-muted-foreground" />
            <LabelChips labels={member.assigned_labels} />
          </div>
        )}
      </div>
      {member.fault_domain && (
        <span className="shrink-0 text-xs text-muted-foreground">fd: {member.fault_domain}</span>
      )}
    </div>
  );
}

/** Safe actions the verb bar can dispatch — mirrors backend SAFE_ACTIONS. */
const FLEET_SAFE_ACTIONS = ['re-reconcile', 'set-labels'] as const;
type FleetSafeAction = typeof FLEET_SAFE_ACTIONS[number];

/** Human-readable description of each safe action. */
const FLEET_ACTION_DESCRIPTIONS: Record<string, string> = {
  're-reconcile': 'Re-reconcile: asks each member to re-apply its desired state — useful after config drift or a failed deploy.',
  'set-labels': 'Set-labels: assigns or updates labels on each member (e.g. mark tier=prod for an environment promotion).',
};

function InventoryView({ fleetId }: { fleetId?: number }) {
  const [filterText, setFilterText] = useState('');
  const [groupBy, setGroupBy] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [lifecycleFilter, setLifecycleFilter] = useState('');

  // Selection state — only meaningful when fleetId is set.
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  // Verb bar dialog state.
  const [showRelabelDialog, setShowRelabelDialog] = useState(false);
  const [relabelInput, setRelabelInput] = useState('');
  const [showRunDialog, setShowRunDialog] = useState(false);
  const [runAction, setRunAction] = useState<FleetSafeAction>('re-reconcile');
  const [runConcurrency, setRunConcurrency] = useState<number | undefined>(undefined);

  const updateLabels = useUpdateFleetMemberLabels();
  const runSelection = useRunSelection();

  const [searchParams, setSearchParams] = useSearchParams();

  // SSH connectivity badge: join fleet members to their projects to surface jumphost reachability.
  const { data: projects } = useProjects();
  const projectSshCredMap = useMemo(() => {
    const m = new Map<number, number | null>();
    for (const p of projects ?? []) {
      if (p.id != null) m.set(p.id, p.ssh_credential_id ?? null);
    }
    return m;
  }, [projects]);

  const handleToggleSelect = useCallback((id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const handleClearSelection = useCallback(() => setSelectedIds(new Set()), []);

  // Relabel — fan-out PUT /members/{id}/labels per selected member.
  const handleRelabel = useCallback(async () => {
    const labels: Record<string, string> = {};
    for (const pair of relabelInput.split(',')) {
      const [k, v] = pair.trim().split('=');
      if (k && v !== undefined) labels[k.trim()] = v.trim();
    }
    if (Object.keys(labels).length === 0) return;
    await Promise.all(
      Array.from(selectedIds).map((id) => updateLabels.mutateAsync({ memberId: id, labels })),
    );
    setShowRelabelDialog(false);
    setRelabelInput('');
    setSelectedIds(new Set());
  }, [relabelInput, selectedIds, updateLabels]);

  // Run operation — calls run-selection endpoint.
  const handleRunOperation = useCallback(async () => {
    if (!fleetId) return;
    const body: RunSelectionRequest = {
      member_ids: Array.from(selectedIds),
      action: runAction,
      concurrency: runConcurrency,
    };
    const run = await runSelection.mutateAsync({ fleetId, body });
    setShowRunDialog(false);
    setSelectedIds(new Set());
    // Navigate to Operations tab to show the run.
    const next = new URLSearchParams(searchParams);
    next.set('detail_tab', 'operations');
    setSearchParams(next);
    void run; // result available if needed
  }, [fleetId, selectedIds, runAction, runConcurrency, runSelection, searchParams, setSearchParams]);

  // Parse label filters from free-text input (key=value pairs, space-separated).
  const labelFilters = useMemo(() => {
    return filterText
      .split(/\s+/)
      .filter((t) => t.includes('='));
  }, [filterText]);

  // When fleetId is set, source from the per-fleet member resolver instead.
  const { data: fleetMembersData, isLoading: fleetMembersLoading } = useFleetMembersByFleet(
    fleetId ?? null,
  );
  const { data: globalData, isLoading: globalLoading, error } = useFleetMembers(
    fleetId == null
      ? {
          label: labelFilters.length > 0 ? labelFilters : undefined,
          member_type: typeFilter || undefined,
          group_by: groupBy || undefined,
        }
      : undefined,
  );

  // Merge: when fleet-scoped, use fleetMembersData (FleetMembersResponse shape).
  const data = fleetId != null ? fleetMembersData : globalData;
  const isLoading = fleetId != null ? fleetMembersLoading : globalLoading;

  const totalCount = data?.total ?? 0;
  const selectionCount = selectedIds.size;
  const hasSelection = selectionCount > 0;

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <List className="h-4 w-4 text-muted-foreground shrink-0" />
          <span className="text-sm font-medium text-foreground">
            Fleet Inventory
          </span>
          <span className="text-xs text-muted-foreground">({totalCount} members)</span>
        </div>

        {/* Type filter — hidden when fleet-scoped (data comes from fleet resolver) */}
        {fleetId == null && (
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="rounded border border-border bg-card text-foreground px-2 py-1 text-xs"
          >
            <option value="">All types</option>
            <option value="cluster">Clusters</option>
            <option value="bare-metal-host">Hosts</option>
            <option value="dpu">DPUs</option>
          </select>
        )}

        {/* Lifecycle filter */}
        <select
          value={lifecycleFilter}
          onChange={(e) => setLifecycleFilter(e.target.value)}
          className="rounded border border-border bg-card text-foreground px-2 py-1 text-xs"
        >
          <option value="">All lifecycle</option>
          <option value="provisioning">Provisioning</option>
          <option value="active">Active</option>
          <option value="draining">Draining</option>
          <option value="decommissioned">Decommissioned</option>
          <option value="unknown">Unknown</option>
        </select>

        {/* Group-by — hidden when fleet-scoped */}
        {fleetId == null && (
          <select
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value)}
            className="rounded border border-border bg-card text-foreground px-2 py-1 text-xs"
          >
            <option value="">No grouping</option>
            <option value="vendor">By vendor</option>
            <option value="region">By region</option>
            <option value="env">By env</option>
            <option value="team">By team</option>
            <option value="platform-profile">By platform profile</option>
            <option value="has-dpu">By has-dpu</option>
          </select>
        )}

        {/* Label filter — hidden when fleet-scoped */}
        {fleetId == null && (
          <input
            type="text"
            placeholder="Filter: env=prod has-dpu=true ..."
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            className="rounded border border-border bg-card text-foreground px-2 py-1 text-xs w-64"
          />
        )}
      </div>

      {/* Verb bar — only visible when fleetId is set AND ≥1 member selected */}
      {fleetId != null && hasSelection && (
        <SectionCard compact>
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-xs font-medium text-foreground">
              {selectionCount} selected
            </span>
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-primary/10 text-primary border border-primary/20">
              {selectionCount} member{selectionCount > 1 ? 's' : ''}
            </span>

            {/* Relabel */}
            <Button
              size="sm"
              variant="outline"
              onClick={() => setShowRelabelDialog(true)}
            >
              <Tag className="h-3.5 w-3.5 mr-1.5" />
              Relabel
            </Button>

            {/* Run operation */}
            <Button
              size="sm"
              onClick={() => setShowRunDialog(true)}
            >
              <Play className="h-3.5 w-3.5 mr-1.5" />
              Run operation
            </Button>

            {/* Clear */}
            <button
              type="button"
              onClick={handleClearSelection}
              className="ml-auto text-xs text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1"
            >
              <Square className="h-3 w-3" />
              Clear
            </button>
          </div>

          {/* Relabel inline form */}
          {showRelabelDialog && (
            <div className="mt-3 pt-3 border-t border-border space-y-2">
              <p className="text-xs text-muted-foreground">
                Assign labels to {selectionCount} member{selectionCount > 1 ? 's' : ''} (key=value pairs, comma-separated):
              </p>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={relabelInput}
                  onChange={(e) => setRelabelInput(e.target.value)}
                  placeholder="env=prod,team=platform"
                  className="flex-1 rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm"
                />
                <Button
                  size="sm"
                  onClick={() => void handleRelabel()}
                  disabled={updateLabels.isPending || !relabelInput.trim()}
                >
                  {updateLabels.isPending ? 'Applying…' : 'Apply'}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setShowRelabelDialog(false)}
                >
                  Cancel
                </Button>
              </div>
              {updateLabels.error && (
                <p className="text-xs text-destructive">{String(updateLabels.error)}</p>
              )}
            </div>
          )}

          {/* Run operation inline form */}
          {showRunDialog && (
            <div className="mt-3 pt-3 border-t border-border space-y-3">
              <p className="text-xs text-muted-foreground">
                Run operation on {selectionCount} selected member{selectionCount > 1 ? 's' : ''}:
              </p>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground">Action</label>
                  <select
                    value={runAction}
                    onChange={(e) => setRunAction(e.target.value as FleetSafeAction)}
                    className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm mt-1"
                  >
                    {FLEET_SAFE_ACTIONS.map((a) => (
                      <option key={a} value={a}>{a}</option>
                    ))}
                  </select>
                  {FLEET_ACTION_DESCRIPTIONS[runAction] && (
                    <p className="mt-1 text-[11px] text-muted-foreground leading-snug">{FLEET_ACTION_DESCRIPTIONS[runAction]}</p>
                  )}
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Concurrency (leave blank for default)</label>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={runConcurrency ?? ''}
                    onChange={(e) => setRunConcurrency(e.target.value ? Number(e.target.value) : undefined)}
                    placeholder="default"
                    className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm mt-1"
                  />
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={() => void handleRunOperation()}
                  disabled={runSelection.isPending}
                >
                  {runSelection.isPending ? 'Starting…' : 'Start run'}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setShowRunDialog(false)}
                >
                  Cancel
                </Button>
              </div>
              {runSelection.error && (
                <p className="text-xs text-destructive">{String(runSelection.error)}</p>
              )}
            </div>
          )}
        </SectionCard>
      )}

      {/* Content */}
      {isLoading && (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-16 w-full" />)}
        </div>
      )}
      {error && (
        <div className="rounded border border-border px-4 py-3 text-sm text-muted-foreground">
          Failed to load fleet members.
        </div>
      )}
      {data && !isLoading && (() => {
        // Client-side lifecycle filter (lifecycle_state is returned per-member
        // but the backend list endpoint doesn't filter by it yet).
        const matchesLifecycle = (m: { lifecycle_state: FleetLifecycleState }) =>
          !lifecycleFilter || (m.lifecycle_state ?? 'unknown') === lifecycleFilter;

        if (isGroupedMembersResponse(data)) {
          // Apply lifecycle filter within each group.
          const filteredGroups = Object.fromEntries(
            Object.entries(data.groups)
              .map(([gv, members]) => [gv, members.filter(matchesLifecycle)] as const)
              .filter(([, members]) => members.length > 0),
          );
          return (
            <div className="space-y-4">
              {Object.entries(filteredGroups).map(([groupValue, members]) => (
                <SectionCard key={groupValue} compact>
                  <div className="flex items-center gap-2 pb-2 mb-1 border-b border-border">
                    <span className="text-xs font-medium text-foreground">
                      {data.group_by}={groupValue}
                    </span>
                    <span className="text-xs text-muted-foreground">({members.length})</span>
                  </div>
                  {members.map((m) => (
                    <MemberRow
                      key={m.id}
                      member={m}
                      selected={fleetId != null ? selectedIds.has(m.id) : undefined}
                      onToggleSelect={fleetId != null ? handleToggleSelect : undefined}
                      sshCredentialId={m.project_id != null ? projectSshCredMap.get(m.project_id) : null}
                    />
                  ))}
                </SectionCard>
              ))}
              {Object.keys(filteredGroups).length === 0 && (
                <EmptyState
                  icon={List}
                  title="No fleet members found"
                  description="Adjust your label filter or add targets to see inventory here."
                />
              )}
            </div>
          );
        }
        const filteredMembers = data.members.filter(matchesLifecycle);
        return (
          <SectionCard compact>
            {filteredMembers.length === 0 ? (
              <EmptyState
                icon={List}
                title="No fleet members found"
                description="Adjust your label filter or add targets to see inventory here."
              />
            ) : (
              filteredMembers.map((m) => (
                <MemberRow
                  key={m.id}
                  member={m}
                  selected={fleetId != null ? selectedIds.has(m.id) : undefined}
                  onToggleSelect={fleetId != null ? handleToggleSelect : undefined}
                  sshCredentialId={m.project_id != null ? projectSshCredMap.get(m.project_id) : null}
                />
              ))
            )}
          </SectionCard>
        );
      })()}
    </div>
  );
}

// ============================================================================
// BULK-OPS VIEW — D-022 Phase 2: define target, blast-radius preview, run
// ============================================================================

/** Simple status pill for bulk run progress — status-color-as-badge. */
function BulkRunStatusBadge({ status }: { status: FleetBulkRun['status'] }) {
  const pillMap: Record<string, { pill: string; dot: string }> = {
    pending: { pill: 'bg-muted text-muted-foreground border-border', dot: 'bg-muted-foreground' },
    running: { pill: 'bg-primary/10 text-primary border-primary/20', dot: 'bg-primary' },
    paused_gate: { pill: 'bg-warning/10 text-warning border-warning/20', dot: 'bg-warning' },
    completed: { pill: 'bg-success/10 text-success border-success/20', dot: 'bg-success' },
    failed: { pill: 'bg-destructive/10 text-destructive border-destructive/20', dot: 'bg-destructive' },
    cancelled: { pill: 'bg-muted text-muted-foreground border-border', dot: 'bg-muted-foreground' },
  };
  const { pill, dot } = pillMap[status] ?? pillMap['pending'];
  return (
    <span className={cn('inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border', pill)}>
      <span className={cn('h-1.5 w-1.5 rounded-full', dot)} />
      {status}
    </span>
  );
}

/** Countdown display for timed_wait gate — "resumes in HH:MM:SS". */
function TimedWaitCountdown({ resumesAt }: { resumesAt: string }) {
  const [remaining, setRemaining] = useState<string>('');

  useEffect(() => {
    const compute = () => {
      const diff = Math.max(0, new Date(resumesAt).getTime() - Date.now());
      const s = Math.floor(diff / 1000);
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      const sec = s % 60;
      setRemaining(
        h > 0
          ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
          : `${m}:${String(sec).padStart(2, '0')}`
      );
    };
    compute();
    const id = setInterval(compute, 1000);
    return () => clearInterval(id);
  }, [resumesAt]);

  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border bg-info/10 text-info border-info/20">
      <Clock className="h-3 w-3" />
      resumes in {remaining || '…'}
    </span>
  );
}

function WaveProgressView({ run }: { run: FleetBulkRun }) {
  const results = run.results ?? [];
  if (results.length === 0) return null;

  const byWave = results.reduce<Record<number, typeof results>>((acc, r) => {
    acc[r.wave_index] = acc[r.wave_index] ?? [];
    acc[r.wave_index].push(r);
    return acc;
  }, {});

  return (
    <div className="space-y-3">
      {Object.entries(byWave).sort(([a], [b]) => Number(a) - Number(b)).map(([waveIdx, rows]) => {
        const fd = rows[0]?.fault_domain ?? '(none)';
        const succeeded = rows.filter((r) => r.status === 'succeeded').length;
        const failed = rows.filter((r) => r.status === 'failed').length;
        return (
          <div key={waveIdx} className="rounded-md border border-border bg-muted/40 p-3 space-y-2">
            <div className="flex items-center gap-2 border-b border-border pb-2">
              <span className="text-xs font-medium text-foreground">Wave {Number(waveIdx) + 1}</span>
              <span className="text-xs text-muted-foreground">fault_domain={fd}</span>
              <span className="text-xs text-success">{succeeded} succeeded</span>
              {failed > 0 && <span className="text-xs text-destructive">{failed} failed</span>}
            </div>
            {rows.map((r) => {
              const rowColor: Record<string, string> = {
                succeeded: 'text-success',
                failed: 'text-destructive',
                running: 'text-primary',
              };
              return (
                <div key={r.id} className="flex items-start gap-3 px-1 py-1 text-xs">
                  <span className={cn('shrink-0', rowColor[r.status] ?? 'text-muted-foreground')}>{r.status}</span>
                  <span className="text-muted-foreground shrink-0">member #{r.fleet_member_id}</span>
                  {/* detail = why-stuck / why-failed line */}
                  {r.detail && (
                    <span className="text-muted-foreground break-all leading-tight">{r.detail}</span>
                  )}
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}

function BulkOpsView({ fleetId }: { fleetId?: number }) {
  const queryClient = useQueryClient();

  // Target state
  const [targetName, setTargetName] = useState('');
  const [labelInput, setLabelInput] = useState('');

  // Run state — when fleetId is set, pre-select that target.
  const [selectedTargetId, setSelectedTargetId] = useState<number | null>(fleetId ?? null);
  const [decisionId, setDecisionId] = useState<number | null>(null);
  const [activeRunId, setActiveRunId] = useState<number | null>(null);
  const [action, setAction] = useState('re-reconcile');
  const [concurrency, setConcurrency] = useState(5);
  const [gateMode, setGateMode] = useState<'manual' | 'health_stable'>('manual');
  // D-022 P6 Slice D: optional strategy to snapshot onto run
  const [selectedStrategyId, setSelectedStrategyId] = useState<number | null>(null);

  // Strategy authoring state
  const [showStrategyForm, setShowStrategyForm] = useState(false);
  const [strategyName, setStrategyName] = useState('');
  const [strategyWaveBy, setStrategyWaveBy] = useState('fault_domain');
  const [strategyLabelKey, setStrategyLabelKey] = useState('');
  const [strategyPct, setStrategyPct] = useState(25);
  const [strategyGateKind, setStrategyGateKind] = useState<'approval' | 'timed_wait' | 'health_stable'>('approval');
  const [strategyWaitSeconds, setStrategyWaitSeconds] = useState(300);

  const { data: targets } = useFleetTargets();
  const createTarget = useCreateFleetTarget();
  const resolveTarget = useResolveFleetTarget();
  const startBulkOp = useStartBulkOp();
  const resumeBulkRun = useResumeBulkRun();
  const cancelBulkRun = useCancelBulkRun();
  const rerunBulkRun = useRerunBulkRun();
  const { data: bulkRun } = useFleetBulkRun(activeRunId);
  // When fleet-scoped, filter recent runs to this fleet.
  const { data: recentRuns } = useFleetBulkRuns(fleetId != null ? { fleet_id: fleetId } : undefined);
  // Strategies — fleet-scoped when fleetId set
  const { data: strategies } = useStrategies(fleetId);
  const createStrategy = useCreateStrategy();
  const deleteStrategy = useDeleteStrategy();

  // Derive decision from resolved step
  const [decision, setDecision] = useState<FleetDecision | null>(null);

  const handleCreateTarget = () => {
    if (!targetName.trim()) return;
    const labels = labelInput.split(/\s+/).filter((l) => l.includes('='));
    createTarget.mutate(
      { name: targetName.trim(), selector: { labels } },
      {
        onSuccess: () => {
          setTargetName('');
          setLabelInput('');
          queryClient.invalidateQueries({ queryKey: ['fleet', 'targets'] });
        },
      }
    );
  };

  const handleResolve = () => {
    if (!selectedTargetId) return;
    resolveTarget.mutate(selectedTargetId, {
      onSuccess: (d) => {
        setDecision(d);
        setDecisionId(d.id);
      },
    });
  };

  const handleStartRun = () => {
    if (!decisionId) return;
    startBulkOp.mutate(
      {
        decision_id: decisionId,
        action,
        concurrency,
        gate_mode: gateMode,
        strategy_id: selectedStrategyId ?? undefined,
      },
      {
        onSuccess: (run) => {
          setActiveRunId(run.id);
          setDecision(null);
          setDecisionId(null);
        },
      }
    );
  };

  const handleResume = () => {
    if (!activeRunId) return;
    resumeBulkRun.mutate(activeRunId, {
      onSuccess: (run) => {
        queryClient.invalidateQueries({ queryKey: queryKeys.fleet.bulkRun(run.id) });
      },
    });
  };

  const handleCancel = () => {
    if (!activeRunId) return;
    cancelBulkRun.mutate(activeRunId);
  };

  const handleRerun = (runId: number) => {
    rerunBulkRun.mutate(runId, {
      onSuccess: (run) => {
        setActiveRunId(run.id);
      },
    });
  };

  const handleCreateStrategy = () => {
    if (!strategyName.trim()) return;
    const waveBy = strategyWaveBy === 'fault_domain'
      ? 'fault_domain'
      : `label:${strategyLabelKey.trim()}`;
    createStrategy.mutate(
      {
        name: strategyName.trim(),
        target_id: fleetId ?? undefined,
        wave_by: waveBy,
        max_concurrency_pct: strategyPct,
        gate_kind: strategyGateKind,
        gate_wait_seconds: strategyGateKind === 'timed_wait' ? strategyWaitSeconds : undefined,
      },
      {
        onSuccess: () => {
          setStrategyName('');
          setStrategyWaveBy('fault_domain');
          setStrategyLabelKey('');
          setStrategyPct(25);
          setStrategyGateKind('approval');
          setStrategyWaitSeconds(300);
          setShowStrategyForm(false);
        },
      }
    );
  };

  const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);

  const hasStrategies = (strategies ?? []).length > 0;
  const hasRuns = (recentRuns ?? []).length > 0;

  return (
    <div className="space-y-6">
      {/* Intro card — shown when there are no strategies and no runs yet */}
      {!hasStrategies && !hasRuns && (
        <SectionCard>
          <div className="space-y-4 py-2">
            <div className="flex items-start gap-3">
              <RefreshCw className="h-5 w-5 text-primary shrink-0 mt-0.5" />
              <div className="space-y-1">
                <p className="text-sm font-semibold text-foreground">Operations run an action across fleet members in safe, staged waves</p>
                <p className="text-xs text-muted-foreground leading-relaxed max-w-2xl">
                  A <span className="font-medium text-foreground">Strategy</span> is a reusable rollout plan — which order to process members,
                  how many to touch at once (blast radius), and a pause or gate between waves.
                  A <span className="font-medium text-foreground">Run</span> is one execution of an action using a strategy — you watch it
                  progress wave-by-wave and can pause, cancel, or re-run it.
                </p>
                <p className="text-xs text-muted-foreground">
                  Start with a default strategy or create your own, then pick an action and launch a Run.
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={() => setShowStrategyForm(true)}>
                <Plus className="h-4 w-4 mr-1.5" />
                New Strategy
              </Button>
            </div>
          </div>
        </SectionCard>
      )}

      {/* Strategy Authoring — D-022 P6 Slice D */}
      <SectionCard compact>
        <div className="flex items-center gap-2 pb-2 mb-3 border-b border-border">
          <Flag className="w-4 h-4 text-primary" />
          <h3 className="text-sm font-semibold text-foreground">Strategies</h3>
          <span className="text-xs text-muted-foreground ml-auto">
            {(strategies ?? []).length} defined
          </span>
          <Button size="sm" variant="outline" onClick={() => setShowStrategyForm((v) => !v)}>
            {showStrategyForm ? 'Cancel' : (
              <><Plus className="h-3.5 w-3.5 mr-1" />New strategy</>
            )}
          </Button>
        </div>

        {/* Existing strategies */}
        {(strategies ?? []).length > 0 && (
          <div className="space-y-1 mb-3">
            {(strategies ?? []).map((s: FleetOperationStrategy) => (
              <div key={s.id} className="flex items-center gap-3 px-2 py-1.5 rounded-md border border-border bg-muted/20 text-xs">
                <div className="flex-1 min-w-0">
                  <span className="font-medium text-foreground">{s.name}</span>
                  <span className="ml-2 text-muted-foreground">
                    waves={s.wave_by} · {s.max_concurrency_pct}% blast radius · gate={s.gate_kind}
                    {s.gate_kind === 'timed_wait' && s.gate_wait_seconds != null && ` (${s.gate_wait_seconds}s)`}
                  </span>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => deleteStrategy.mutate(s.id)}
                  disabled={deleteStrategy.isPending}
                  className="h-6 w-6 p-0 text-destructive hover:bg-destructive/10"
                  title="Delete strategy"
                >
                  <Trash2 className="h-3 w-3" />
                </Button>
              </div>
            ))}
          </div>
        )}

        {/* Create strategy form */}
        {showStrategyForm && (
          <div className="space-y-3 pt-2 border-t border-border">
            <p className="text-xs text-muted-foreground">
              Suggested defaults: fault-domain waves · 25% concurrency · approval gate (safest for first-time use).
            </p>
            <div className="grid grid-cols-2 gap-2">
              <div className="col-span-2">
                <label className="text-xs font-medium text-muted-foreground">Name</label>
                <input
                  value={strategyName}
                  onChange={(e) => setStrategyName(e.target.value)}
                  placeholder="e.g. prod-canary"
                  className="mt-1 w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Wave by</label>
                <select
                  value={strategyWaveBy}
                  onChange={(e) => setStrategyWaveBy(e.target.value)}
                  className="mt-1 w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs"
                >
                  <option value="fault_domain">fault_domain</option>
                  <option value="label">label (pick key below)</option>
                </select>
                <p className="mt-1 text-[11px] text-muted-foreground leading-snug">
                  {strategyWaveBy === 'label'
                    ? 'Members are grouped into waves by a label key (e.g. region → one wave per region).'
                    : 'Members are grouped into waves by fault-domain — roll out zone-by-zone for blast-radius control.'}
                </p>
              </div>
              {strategyWaveBy === 'label' && (
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Label key</label>
                  <input
                    value={strategyLabelKey}
                    onChange={(e) => setStrategyLabelKey(e.target.value)}
                    placeholder="region"
                    className="mt-1 w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs"
                  />
                  <p className="mt-1 text-[11px] text-muted-foreground leading-snug">
                    E.g. <code className="bg-muted px-1 rounded">region</code> → one wave per unique region value.
                  </p>
                </div>
              )}
              <div>
                <label className="text-xs font-medium text-muted-foreground">
                  Max concurrency % <span className="font-normal">(blast radius)</span>
                </label>
                <input
                  type="range"
                  min={1}
                  max={100}
                  value={strategyPct}
                  onChange={(e) => setStrategyPct(Number(e.target.value))}
                  className="mt-1 w-full"
                />
                <span className="text-xs text-foreground">{strategyPct}%</span>
                <p className="mt-1 text-[11px] text-muted-foreground leading-snug">
                  How many members in a wave run at once. Smaller = safer; larger = faster. 25% is a safe starting point.
                </p>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Gate kind</label>
                <select
                  value={strategyGateKind}
                  onChange={(e) => setStrategyGateKind(e.target.value as typeof strategyGateKind)}
                  className="mt-1 w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs"
                >
                  <option value="approval">approval (manual resume)</option>
                  <option value="timed_wait">timed_wait (auto-resume after delay)</option>
                  <option value="health_stable">health_stable (auto-proceed)</option>
                </select>
                <p className="mt-1 text-[11px] text-muted-foreground leading-snug">
                  {strategyGateKind === 'approval'
                    ? 'Pauses between waves for a human OK — safest choice for production.'
                    : strategyGateKind === 'timed_wait'
                    ? 'Pauses N seconds between waves to soak before proceeding automatically.'
                    : 'Proceeds automatically once members in the completed wave stay healthy.'}
                </p>
              </div>
              {strategyGateKind === 'timed_wait' && (
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Wait (seconds)</label>
                  <input
                    type="number"
                    min={1}
                    value={strategyWaitSeconds}
                    onChange={(e) => setStrategyWaitSeconds(Number(e.target.value))}
                    className="mt-1 w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs"
                  />
                  <p className="mt-1 text-[11px] text-muted-foreground leading-snug">300s (5 min) is a reasonable soak time.</p>
                </div>
              )}
            </div>
            <Button
              size="sm"
              onClick={handleCreateStrategy}
              disabled={createStrategy.isPending || !strategyName.trim() || (strategyWaveBy === 'label' && !strategyLabelKey.trim())}
            >
              {createStrategy.isPending ? 'Creating…' : 'Create strategy'}
            </Button>
            {createStrategy.isError && (
              <p className="text-xs text-destructive">{String(createStrategy.error)}</p>
            )}
          </div>
        )}
      </SectionCard>

      {/* Create Target */}
      <SectionCard compact>
        <div className="flex items-center gap-2 pb-2 mb-3 border-b border-border">
          <Tag className="w-4 h-4 text-primary" />
          <h3 className="text-sm font-semibold text-foreground">Define Target</h3>
        </div>
        <div className="space-y-3">
          <div className="flex gap-2">
            <input
              placeholder="Target name (e.g. prod-clusters)"
              value={targetName}
              onChange={(e) => setTargetName(e.target.value)}
              className="flex-1 rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs"
            />
          </div>
          <div className="flex gap-2">
            <input
              placeholder="Label selector (e.g. env=prod has-dpu=true)"
              value={labelInput}
              onChange={(e) => setLabelInput(e.target.value)}
              className="flex-1 rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs"
            />
            <Button size="sm" onClick={handleCreateTarget} disabled={createTarget.isPending || !targetName.trim()}>
              {createTarget.isPending ? 'Creating...' : 'Create Target'}
            </Button>
          </div>
          {createTarget.isError && (
            <div className="text-xs text-destructive">{String(createTarget.error)}</div>
          )}
        </div>
      </SectionCard>

      {/* Resolve + Blast-Radius Preview */}
      <SectionCard compact>
        <div className="flex items-center gap-2 pb-2 mb-3 border-b border-border">
          <Eye className="w-4 h-4 text-warning" />
          <h3 className="text-sm font-semibold text-foreground">Blast-Radius Preview</h3>
        </div>
        <div className="space-y-3">
          <div className="flex gap-2 items-center">
            <select
              value={selectedTargetId ?? ''}
              onChange={(e) => { setSelectedTargetId(Number(e.target.value) || null); setDecision(null); setDecisionId(null); }}
              className="flex-1 rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs"
            >
              <option value="">Select a target...</option>
              {(targets ?? []).map((t: FleetTarget) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
            <Button size="sm" variant="outline" onClick={handleResolve} disabled={!selectedTargetId || resolveTarget.isPending}>
              {resolveTarget.isPending ? 'Resolving...' : 'Resolve'}
            </Button>
          </div>

          {decision && (
            <div className="rounded-md border border-border bg-muted/40 p-3 space-y-2">
              <div className="flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-warning" />
                <span className="text-sm font-semibold text-foreground">
                  This will act on {decision.member_count} member{decision.member_count !== 1 ? 's' : ''}
                </span>
              </div>
              {(decision.members ?? []).length > 0 && (
                <div className="space-y-1 max-h-40 overflow-y-auto">
                  {(decision.members ?? []).slice(0, 20).map((m) => (
                    <div key={m.id} className="flex items-center gap-2 text-xs text-muted-foreground">
                      <span className="text-foreground">{m.name}</span>
                      <MemberTypeBadge memberType={m.member_type} />
                      {m.fault_domain && <span>fd:{m.fault_domain}</span>}
                    </div>
                  ))}
                  {(decision.member_count) > 20 && (
                    <div className="text-xs text-muted-foreground">…and {decision.member_count - 20} more</div>
                  )}
                </div>
              )}
              {decision.member_count === 0 && (
                <div className="text-xs text-muted-foreground">No members matched the selector.</div>
              )}
            </div>
          )}
        </div>
      </SectionCard>

      {/* Start Run */}
      {decision && decision.member_count > 0 && (
        <SectionCard compact>
          <div className="flex items-center gap-2 pb-2 mb-3 border-b border-border">
            <RefreshCw className="w-4 h-4 text-primary" />
            <h3 className="text-sm font-semibold text-foreground">Configure Bulk Operation</h3>
          </div>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Action</label>
                <select
                  value={action}
                  onChange={(e) => setAction(e.target.value)}
                  className="mt-1 w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs"
                >
                  <option value="re-reconcile">re-reconcile</option>
                  <option value="set-labels">set-labels</option>
                </select>
                {FLEET_ACTION_DESCRIPTIONS[action] && (
                  <p className="mt-1 text-[11px] text-muted-foreground leading-snug">{FLEET_ACTION_DESCRIPTIONS[action]}</p>
                )}
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Strategy (optional)</label>
                <select
                  value={selectedStrategyId ?? ''}
                  onChange={(e) => setSelectedStrategyId(Number(e.target.value) || null)}
                  className="mt-1 w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs"
                >
                  <option value="">No strategy (use manual settings)</option>
                  {(strategies ?? []).map((s: FleetOperationStrategy) => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
              </div>
              {!selectedStrategyId && (
                <>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">Concurrency</label>
                    <input
                      type="number"
                      min={1}
                      max={50}
                      value={concurrency}
                      onChange={(e) => setConcurrency(Number(e.target.value))}
                      className="mt-1 w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs"
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">Gate mode</label>
                    <select
                      value={gateMode}
                      onChange={(e) => setGateMode(e.target.value as 'manual' | 'health_stable')}
                      className="mt-1 w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs"
                    >
                      <option value="manual">manual (pause between waves)</option>
                      <option value="health_stable">health_stable (auto-proceed)</option>
                    </select>
                  </div>
                </>
              )}
            </div>
            {/* What will happen summary */}
            {decision && (
              <p className="text-xs text-muted-foreground border border-border rounded-md px-3 py-2 bg-muted/30">
                <span className="font-medium text-foreground">What will happen: </span>
                Run <code className="bg-muted px-1 rounded">{action}</code> on{' '}
                <span className="font-medium text-foreground">{decision.member_count} member{decision.member_count !== 1 ? 's' : ''}</span>
                {selectedStrategyId
                  ? (() => {
                      const s = (strategies ?? []).find((st: FleetOperationStrategy) => st.id === selectedStrategyId);
                      return s
                        ? `, using strategy "${s.name}" (${s.wave_by} waves · ${s.max_concurrency_pct}% concurrency · ${s.gate_kind} gate)`
                        : ' using the selected strategy';
                    })()
                  : `, in waves of ≤${concurrency} with ${gateMode === 'manual' ? 'a manual approval gate' : 'auto-proceed when members are healthy'} between waves`
                }.
              </p>
            )}
            <Button
              onClick={handleStartRun}
              disabled={startBulkOp.isPending}
              className="w-full"
            >
              {startBulkOp.isPending ? 'Starting...' : `Run ${action} on ${decision.member_count} members`}
            </Button>
          </div>
        </SectionCard>
      )}

      {/* Active Run Status */}
      {bulkRun && (
        <SectionCard compact>
          <div className="flex items-center justify-between pb-2 mb-3 border-b border-border">
            <div className="flex items-center gap-2 flex-wrap">
              <Activity className="w-4 h-4 text-primary" />
              <h3 className="text-sm font-semibold text-foreground">Run #{bulkRun.id} — {bulkRun.action}</h3>
              <BulkRunStatusBadge status={bulkRun.status} />
              {/* timed_wait countdown — shown when paused with gate_resumes_at */}
              {bulkRun.status === 'paused_gate' && bulkRun.gate_resumes_at && (
                <TimedWaitCountdown resumesAt={bulkRun.gate_resumes_at} />
              )}
            </div>
            <div className="flex items-center gap-2">
              {bulkRun.status === 'paused_gate' && !bulkRun.gate_resumes_at && (
                <Button size="sm" onClick={handleResume} disabled={resumeBulkRun.isPending}>
                  {resumeBulkRun.isPending ? 'Resuming...' : 'Resume next wave'}
                </Button>
              )}
              {TERMINAL_STATUSES.has(bulkRun.status) && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => handleRerun(bulkRun.id)}
                  disabled={rerunBulkRun.isPending}
                >
                  {rerunBulkRun.isPending ? 'Re-running…' : 'Re-run'}
                </Button>
              )}
              {!TERMINAL_STATUSES.has(bulkRun.status) && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleCancel}
                  disabled={cancelBulkRun.isPending}
                  className="border-destructive/30 text-destructive hover:bg-destructive/10"
                >
                  {cancelBulkRun.isPending ? 'Cancelling...' : 'Cancel'}
                </Button>
              )}
            </div>
          </div>
          <div className="space-y-2">
            <div className="text-xs text-muted-foreground">
              Wave {bulkRun.current_wave_index + 1}
              {bulkRun.total_waves != null ? ` of ${bulkRun.total_waves}` : ''} •
              Gate: {bulkRun.gate_mode} •
              Concurrency: {bulkRun.concurrency}
              {bulkRun.strategy_id && <span> · Strategy #{bulkRun.strategy_id}</span>}
            </div>
            {bulkRun.error_message && (
              <div className="text-xs text-destructive">{bulkRun.error_message}</div>
            )}
            <WaveProgressView run={bulkRun} />
          </div>
        </SectionCard>
      )}

      {/* Runs History */}
      {(recentRuns ?? []).length > 0 && (
        <SectionCard compact>
          <div className="flex items-center gap-2 pb-2 mb-3 border-b border-border">
            <List className="w-4 h-4 text-muted-foreground" />
            <h3 className="text-sm font-semibold text-foreground">Recent Runs</h3>
            <span className="text-xs text-muted-foreground ml-auto">{(recentRuns ?? []).length} run{(recentRuns ?? []).length !== 1 ? 's' : ''}</span>
          </div>
          <div className="space-y-1">
            {(recentRuns ?? []).slice(0, 20).map((run) => (
              <div
                key={run.id}
                className={cn(
                  'flex items-center gap-2 px-2 py-2 rounded-md text-xs border transition-colors',
                  activeRunId === run.id
                    ? 'border-primary/30 bg-primary/5'
                    : 'border-transparent hover:bg-muted/60',
                )}
              >
                <button
                  type="button"
                  onClick={() => setActiveRunId(run.id)}
                  className="flex items-center gap-2 flex-1 text-left"
                >
                  <BulkRunStatusBadge status={run.status} />
                  <span className="font-medium text-foreground flex-1 truncate">
                    #{run.id} — {run.action}
                  </span>
                  {run.started_at && (
                    <span className="text-muted-foreground shrink-0">
                      <TimeAgo dateStr={run.started_at} />
                    </span>
                  )}
                </button>
                {/* Re-run button on terminal runs in history list */}
                {TERMINAL_STATUSES.has(run.status) && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => handleRerun(run.id)}
                    disabled={rerunBulkRun.isPending}
                    className="h-6 px-2 text-xs shrink-0"
                    title="Re-run this operation"
                  >
                    Re-run
                  </Button>
                )}
              </div>
            ))}
          </div>
        </SectionCard>
      )}
    </div>
  );
}

// ============================================================================
// COMPLIANCE VIEW — D-022 Phase 3: fleet policies + compliance dashboard
// ============================================================================

/** Status pill for a single member's compliance status — status-color-as-badge. */
function ComplianceStatusBadge({ status }: { status: string }) {
  const pillMap: Record<string, { pill: string; dot: string }> = {
    compliant: { pill: 'bg-success/10 text-success border-success/20', dot: 'bg-success' },
    drifted: { pill: 'bg-warning/10 text-warning border-warning/20', dot: 'bg-warning' },
    unknown: { pill: 'bg-muted text-muted-foreground border-border', dot: 'bg-muted-foreground' },
  };
  const { pill, dot } = pillMap[status] ?? pillMap['unknown'];
  return (
    <span className={cn('inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border', pill)}>
      <span className={cn('h-1.5 w-1.5 rounded-full', dot)} />
      {status}
    </span>
  );
}

/** Per-policy compliance card — includes edit + delete affordances (D-022 P5 S3). */
const PolicyComplianceCard = memo(function PolicyComplianceCard({
  policy,
  onDeleted,
}: {
  policy: FleetPolicy;
  onDeleted: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const { data: rollup, refetch: refetchRollup } = usePolicyCompliance(policy.id);
  const evaluateMut = useEvaluateFleetPolicy();
  const enforceMut = useEnforceFleetPolicy();
  const updateMut = useUpdateFleetPolicy();
  const deleteMut = useDeleteFleetPolicy();

  const handleEvaluate = async () => {
    await evaluateMut.mutateAsync(policy.id);
    await refetchRollup();
  };

  const handleEnforce = async () => {
    await enforceMut.mutateAsync(policy.id);
    await refetchRollup();
  };

  const handleDelete = async () => {
    if (!window.confirm(`Delete policy "${policy.name}"? This cannot be undone.`)) return;
    await deleteMut.mutateAsync(policy.id);
    onDeleted();
  };

  const canEnforce = rollup?.supports_enforce && policy.mode === 'enforce';
  const hasDrift = (rollup?.drifted_count ?? 0) > 0;

  return (
    <SectionCard compact>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-sm text-foreground">{policy.name}</span>
            <span className="text-xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{policy.kind}</span>
            <span
              className={cn('text-xs px-1.5 py-0.5 rounded cursor-default', policy.mode === 'enforce' ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground')}
              title={
                policy.mode === 'enforce'
                  ? 'Enforce: drift is auto-remediated on the next run via a gated rollout.'
                  : 'Inform: reports drift only — no changes are made to members. Switch to Enforce to auto-remediate.'
              }
            >
              {policy.mode}
            </span>
          </div>
          {rollup && (
            <div className="mt-2 flex items-center gap-4 text-xs">
              <span className="text-success">{rollup.compliant_count} compliant</span>
              <span className={hasDrift ? 'text-warning' : 'text-muted-foreground'}>{rollup.drifted_count} drifted</span>
              <span className="text-muted-foreground">{rollup.unknown_count} unknown</span>
              {rollup.last_evaluated_at && (
                <span className="text-muted-foreground">last: {new Date(rollup.last_evaluated_at).toLocaleString()}</span>
              )}
            </div>
          )}
          {!rollup && <p className="text-xs mt-1 text-muted-foreground">No evaluations yet — run Evaluate to see compliance.</p>}
        </div>
        <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
          <Button
            size="sm"
            variant="outline"
            onClick={handleEvaluate}
            disabled={evaluateMut.isPending}
          >
            {evaluateMut.isPending ? 'Evaluating…' : 'Evaluate'}
          </Button>
          {canEnforce && (
            <Button
              size="sm"
              onClick={handleEnforce}
              disabled={enforceMut.isPending || !hasDrift}
              className={hasDrift ? 'bg-destructive hover:bg-destructive/90 text-destructive-foreground' : ''}
            >
              {enforceMut.isPending ? 'Enforcing…' : `Enforce (${rollup?.drifted_count ?? 0} drifted)`}
            </Button>
          )}
          {rollup && rollup.drifted_count > 0 && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-xs text-muted-foreground hover:underline"
            >
              {expanded ? 'Hide drift' : 'Show drift'}
            </button>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={() => setEditing(!editing)}
          >
            {editing ? 'Cancel' : 'Edit'}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void handleDelete()}
            disabled={deleteMut.isPending}
            className="text-destructive hover:text-destructive border-destructive/40 hover:border-destructive"
          >
            {deleteMut.isPending ? 'Deleting…' : 'Delete'}
          </Button>
        </div>
      </div>

      {/* Inline edit form — reuses CreatePolicyForm logic with prefill + PUT */}
      {editing && (
        <div className="mt-3 border-t border-border pt-3">
          <EditPolicyForm
            policy={policy}
            onSaved={() => {
              setEditing(false);
            }}
            onCancel={() => setEditing(false)}
            updateMut={updateMut}
          />
        </div>
      )}

      {/* Drifted member list */}
      {expanded && rollup && rollup.drifted_members.length > 0 && (
        <div className="mt-3 border-t border-border pt-3">
          <p className="text-xs font-medium mb-2 text-muted-foreground">Drifted members</p>
          <div className="space-y-1">
            {rollup.drifted_members.map((m) => (
              <div key={m.member_id} className="flex items-start gap-2 text-xs">
                <ComplianceStatusBadge status={m.status} />
                <span className="text-muted-foreground">member #{m.member_id}</span>
                {m.detail && <span className="text-muted-foreground"> — {m.detail}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Mutation errors */}
      {evaluateMut.error && (
        <p className="mt-2 text-xs text-destructive">{String(evaluateMut.error)}</p>
      )}
      {enforceMut.error && (
        <p className="mt-2 text-xs text-destructive">{String(enforceMut.error)}</p>
      )}
      {deleteMut.error && (
        <p className="mt-2 text-xs text-destructive">{String(deleteMut.error)}</p>
      )}
    </SectionCard>
  );
});

/**
 * Inline edit form for an existing policy — prefills current values and
 * submits via PUT /api/fleet/policies/{id}.  #203 reskin: SectionCard,
 * semantic tokens, no isDark/useTheme.
 */
function EditPolicyForm({
  policy,
  onSaved,
  onCancel,
  updateMut,
}: {
  policy: FleetPolicy;
  onSaved: () => void;
  onCancel: () => void;
  updateMut: ReturnType<typeof useUpdateFleetPolicy>;
}) {
  const [name, setName] = useState(policy.name);
  const [kind, setKind] = useState<'label-compliance' | 'os-compliance-seed'>(
    policy.kind as 'label-compliance' | 'os-compliance-seed'
  );
  const [mode, setMode] = useState<'inform' | 'enforce'>(policy.mode as 'inform' | 'enforce');
  const [requiredLabels, setRequiredLabels] = useState(() => {
    const rl = (policy.desired as { required_labels?: string[] }).required_labels;
    return rl ? rl.join(', ') : '';
  });
  const [osName, setOsName] = useState(
    () => (policy.desired as { os?: string }).os ?? ''
  );
  const [minVersion, setMinVersion] = useState(
    () => (policy.desired as { min_version?: string }).min_version ?? ''
  );

  const handleSave = async () => {
    const desired =
      kind === 'label-compliance'
        ? { required_labels: requiredLabels.split(',').map((s) => s.trim()).filter(Boolean) }
        : { os: osName, min_version: minVersion };
    await updateMut.mutateAsync({ policyId: policy.id, body: { name, kind, mode, desired } });
    onSaved();
  };

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-foreground">Edit Policy</h3>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-muted-foreground">Name</label>
          <input
            className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm mt-1"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">Kind</label>
          <select
            className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm mt-1"
            value={kind}
            onChange={(e) => setKind(e.target.value as typeof kind)}
          >
            <option value="label-compliance">label-compliance</option>
            <option value="os-compliance-seed">os-compliance-seed (inform-only)</option>
          </select>
        </div>
        <div>
          <label className="text-xs text-muted-foreground">Mode</label>
          <select
            className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm mt-1"
            value={mode}
            onChange={(e) => setMode(e.target.value as typeof mode)}
            disabled={kind === 'os-compliance-seed'}
          >
            <option value="inform">inform</option>
            <option value="enforce">enforce</option>
          </select>
          {kind !== 'os-compliance-seed' && (
            <p className="mt-1 text-[11px] text-muted-foreground leading-snug">
              {mode === 'enforce'
                ? 'Switch to Enforce → drift will be auto-remediated on the next run.'
                : 'Inform = report drift only (safe, no changes). Switch to Enforce to auto-remediate.'}
            </p>
          )}
        </div>
      </div>
      {kind === 'label-compliance' && (
        <div>
          <label className="text-xs text-muted-foreground">Required labels (comma-separated k=v)</label>
          <input
            className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm mt-1"
            value={requiredLabels}
            onChange={(e) => setRequiredLabels(e.target.value)}
            placeholder="env=prod,team=platform"
          />
        </div>
      )}
      {kind === 'os-compliance-seed' && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">OS</label>
            <input
              className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm mt-1"
              value={osName}
              onChange={(e) => setOsName(e.target.value)}
              placeholder="ubuntu"
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Min version</label>
            <input
              className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm mt-1"
              value={minVersion}
              onChange={(e) => setMinVersion(e.target.value)}
              placeholder="22.04"
            />
          </div>
        </div>
      )}
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          onClick={() => void handleSave()}
          disabled={updateMut.isPending || !name}
        >
          {updateMut.isPending ? 'Saving…' : 'Save'}
        </Button>
        <Button size="sm" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        {updateMut.error && (
          <span className="text-xs text-destructive">{String(updateMut.error)}</span>
        )}
      </div>
    </div>
  );
}

/** Create policy form.
 * When `fleetId` is provided (fleet-scoped context), the target selector is
 * pre-filled with that fleet's id and hidden — a policy belongs to one fleet.
 */
function CreatePolicyForm({
  onCreated,
  fleetId,
  memberCount,
}: {
  onCreated: () => void;
  fleetId?: number;
  memberCount?: number;
}) {
  const { data: targets } = useFleetTargets();
  const createMut = useCreateFleetPolicy();
  const [name, setName] = useState('');
  // When fleet-scoped, targetId is locked to fleetId; otherwise user picks.
  const [targetId, setTargetId] = useState<number | null>(fleetId ?? null);
  const [kind, setKind] = useState<'label-compliance' | 'os-compliance-seed'>('label-compliance');
  // Default to inform — safe first step, no changes to members.
  const [mode, setMode] = useState<'inform' | 'enforce'>('inform');
  const [requiredLabels, setRequiredLabels] = useState('');
  const [osName, setOsName] = useState('');
  const [minVersion, setMinVersion] = useState('');

  const handleSubmit = async () => {
    if (!name || !targetId) return;
    const desired =
      kind === 'label-compliance'
        ? { required_labels: requiredLabels.split(',').map((s) => s.trim()).filter(Boolean) }
        : { os: osName, min_version: minVersion };
    await createMut.mutateAsync({ name, target_id: targetId, kind, mode, desired });
    onCreated();
  };

  // When called from fleet-scoped ComplianceView, resolve the fleet's name for display.
  const fleetName = fleetId != null
    ? (targets ?? []).find((t) => t.id === fleetId)?.name ?? `Fleet #${fleetId}`
    : null;

  // What will happen when saved — shown as a one-line summary below the form.
  const saveSummary = (() => {
    if (!name) return null;
    const n = memberCount ?? 0;
    const memberPhrase = n > 0 ? `${n} member${n !== 1 ? 's' : ''}` : 'all members';
    if (mode === 'inform') {
      return `Saving will check ${memberPhrase} and report any drift — no changes made.`;
    }
    return `Saving will check ${memberPhrase} and automatically fix drift on the next run.`;
  })();

  return (
    <SectionCard compact>
      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">New Policy</h3>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">Name</label>
            <input
              className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm mt-1"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. prod-label-policy"
            />
          </div>
          {/* Target selector — hidden when fleet-scoped (policy belongs to this fleet). */}
          {fleetId == null ? (
            <div>
              <label className="text-xs text-muted-foreground">Target</label>
              <select
                className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm mt-1"
                value={targetId ?? ''}
                onChange={(e) => setTargetId(Number(e.target.value))}
              >
                <option value="">Select target…</option>
                {(targets ?? []).map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
            </div>
          ) : (
            <div>
              <label className="text-xs text-muted-foreground">Target</label>
              <div className="mt-1 flex items-center gap-1.5">
                <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-primary/10 text-primary border border-primary/20">
                  {fleetName}
                </span>
                <span className="text-xs text-muted-foreground">(this fleet)</span>
              </div>
            </div>
          )}
          <div className="col-span-2 grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-muted-foreground">Kind</label>
              <select
                className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm mt-1"
                value={kind}
                onChange={(e) => {
                  const next = e.target.value as typeof kind;
                  setKind(next);
                  // os-compliance-seed is inform-only — reset mode to inform.
                  if (next === 'os-compliance-seed') setMode('inform');
                }}
              >
                <option value="label-compliance">label-compliance</option>
                <option value="os-compliance-seed">os-compliance-seed</option>
              </select>
              <p className="mt-1 text-[11px] text-muted-foreground leading-snug">
                {kind === 'label-compliance'
                  ? 'Members must carry specific labels (e.g. tier=prod).'
                  : 'Members must run a minimum OS/version — inform-only, reports drift without auto-fixing.'}
              </p>
            </div>
            <div>
              <label className="text-xs text-muted-foreground">Mode</label>
              <select
                className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm mt-1"
                value={mode}
                onChange={(e) => setMode(e.target.value as typeof mode)}
                disabled={kind === 'os-compliance-seed'}
              >
                <option value="inform">inform</option>
                <option value="enforce">enforce</option>
              </select>
              <p className="mt-1 text-[11px] text-muted-foreground leading-snug">
                {kind === 'os-compliance-seed'
                  ? 'os-compliance-seed is always inform — it reports drift but never auto-fixes.'
                  : mode === 'inform'
                  ? 'Inform = report drift only (safe, no changes to members).'
                  : 'Enforce = auto-remediate drift via a gated rollout. Switch to Inform first if unsure.'}
              </p>
            </div>
          </div>
        </div>
        {kind === 'label-compliance' && (
          <div>
            <label className="text-xs text-muted-foreground">Required labels (comma-separated key=value)</label>
            <input
              className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm mt-1"
              value={requiredLabels}
              onChange={(e) => setRequiredLabels(e.target.value)}
              placeholder="tier=prod,env=production"
            />
            <p className="mt-1 text-[11px] text-muted-foreground">
              Example: <code className="bg-muted px-1 py-0.5 rounded">tier=prod,team=platform</code> — every member must carry all listed labels.
            </p>
          </div>
        )}
        {kind === 'os-compliance-seed' && (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-muted-foreground">OS name</label>
              <input
                className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm mt-1"
                value={osName}
                onChange={(e) => setOsName(e.target.value)}
                placeholder="ubuntu"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground">Minimum version</label>
              <input
                className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-sm mt-1"
                value={minVersion}
                onChange={(e) => setMinVersion(e.target.value)}
                placeholder="22.04"
              />
              <p className="mt-1 text-[11px] text-muted-foreground col-span-2">
                Example: OS <code className="bg-muted px-1 py-0.5 rounded">ubuntu</code> min version <code className="bg-muted px-1 py-0.5 rounded">22.04</code>.
              </p>
            </div>
          </div>
        )}
        <div className="flex items-center gap-2 flex-wrap">
          <Button size="sm" onClick={() => void handleSubmit()} disabled={createMut.isPending || !name || !targetId}>
            {createMut.isPending ? 'Creating…' : 'Create Policy'}
          </Button>
          {createMut.error && <span className="text-xs text-destructive">{String(createMut.error)}</span>}
        </div>
        {saveSummary && (
          <p className="text-[11px] text-muted-foreground border-t border-border pt-2">{saveSummary}</p>
        )}
      </div>
    </SectionCard>
  );
}

function ComplianceView({ fleetId }: { fleetId?: number }) {
  const { data: allPolicies, refetch: refetchPolicies, isLoading } = useFleetPolicies();
  const { data: fleetMembers } = useFleetMembersByFleet(fleetId ?? null);
  const [showCreate, setShowCreate] = useState(false);

  // When fleet-scoped, filter to policies targeting this fleet.
  const policies = useMemo(() => {
    if (fleetId == null || !allPolicies) return allPolicies;
    return allPolicies.filter((p) => p.target_id === fleetId);
  }, [allPolicies, fleetId]);

  const memberCount = fleetMembers?.total ?? fleetMembers?.members?.length ?? 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-foreground">Fleet Compliance</h2>
          <p className="text-xs mt-0.5 text-muted-foreground">
            Desired-state policies over fleet targets. Inform mode reports drift; enforce remediates via gated bulk ops.
          </p>
        </div>
        <Button size="sm" onClick={() => setShowCreate(!showCreate)}>
          {showCreate ? 'Cancel' : 'New Policy'}
        </Button>
      </div>

      {showCreate && (
        <CreatePolicyForm
          fleetId={fleetId}
          memberCount={memberCount}
          onCreated={() => {
            setShowCreate(false);
            void refetchPolicies();
          }}
        />
      )}

      {isLoading && (
        <div className="space-y-3">
          {[1, 2].map((i) => <Skeleton key={i} className="h-20 w-full rounded-xl" />)}
        </div>
      )}

      {!isLoading && (!policies || policies.length === 0) && !showCreate && (
        <SectionCard>
          <div className="space-y-4 py-2">
            <div className="flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 text-primary shrink-0 mt-0.5" />
              <div className="space-y-1">
                <p className="text-sm font-semibold text-foreground">No policies defined yet</p>
                <p className="text-xs text-muted-foreground leading-relaxed max-w-2xl">
                  Policies are compliance rules for this fleet's members. Define what "good" looks like — then forge
                  continuously checks every member against it.{' '}
                  <span className="font-medium text-foreground">Inform</span> shows you which members have drifted;{' '}
                  <span className="font-medium text-foreground">Enforce</span> automatically fixes them via a gated rollout.
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Examples: require every member to carry label <code className="bg-muted px-1 py-0.5 rounded text-foreground">tier=prod</code>,
                  or run OS ≥ <code className="bg-muted px-1 py-0.5 rounded text-foreground">ubuntu 22.04</code>.
                </p>
              </div>
            </div>
            <Button size="sm" onClick={() => setShowCreate(true)}>
              <Plus className="h-4 w-4 mr-1.5" />
              New Policy
            </Button>
          </div>
        </SectionCard>
      )}

      {!isLoading && policies && policies.length > 0 && (
        <div className="space-y-3">
          {policies.map((p) => (
            <PolicyComplianceCard key={p.id} policy={p} onDeleted={() => void refetchPolicies()} />
          ))}
        </div>
      )}
    </div>
  );
}

// MigrationView removed in D-022 P6 Slice A — moved to K8s page Migration tab.
// See: frontend-v2/src/components/k8s/migration/MigrationPanel.tsx

// ──────────────────────────────────────────────────────────────────────────────
// D-022 P6 Slice B: rollup helpers + Fleet detail drill-down
// ──────────────────────────────────────────────────────────────────────────────

/** Worst-state pill badge — mirrors LifecycleBadge/BulkRunStatusBadge pattern. */
function WorstStateBadge({ state }: { state: WorstState | undefined }) {
  if (!state) return null;
  const map: Record<WorstState, { pill: string; icon: typeof CheckCircle2 | typeof XCircle | typeof AlertCircle | null; label: string }> = {
    error:        { pill: 'bg-destructive/10 text-destructive border-destructive/20', icon: XCircle, label: 'Error' },
    drifted:      { pill: 'bg-warning/10 text-warning border-warning/20', icon: AlertCircle, label: 'Drifted' },
    provisioning: { pill: 'bg-primary/10 text-primary border-primary/20', icon: null, label: 'Provisioning' },
    unreachable:  { pill: 'bg-muted text-muted-foreground border-border', icon: null, label: 'Unreachable' },
    ready:        { pill: 'bg-success/10 text-success border-success/20', icon: CheckCircle2, label: 'Ready' },
    unknown:      { pill: 'bg-muted text-muted-foreground border-border', icon: null, label: 'Unknown' },
  };
  const { pill, icon: Icon, label } = map[state] ?? map.unknown;
  return (
    <span className={cn('inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border', pill)}>
      {Icon && <Icon className="h-3 w-3" />}
      {label}
    </span>
  );
}

/** Compact rollup summary strip — member count · compliant/total · drift · worst-state. */
function RollupSummaryStrip({ rollup }: { rollup: FleetRollup }) {
  return (
    <div className="flex items-center gap-3 text-xs text-muted-foreground flex-wrap">
      <span className="text-foreground font-medium">{rollup.member_count} members</span>
      {rollup.total_evaluated > 0 && (
        <span className="text-success">{rollup.compliant_count}/{rollup.total_evaluated} compliant</span>
      )}
      {rollup.drift_count > 0 && (
        <span className="text-warning">{rollup.drift_count} drifted</span>
      )}
      {rollup.unreachable_count > 0 && (
        <span className="text-muted-foreground">{rollup.unreachable_count} unreachable</span>
      )}
      <WorstStateBadge state={rollup.worst_state} />
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// D-022 P6: Traffic-light helpers — now in shared module
// TrafficDot, FleetTrafficLights, EstateSummaryBar, healthStateFromRollup,
// policyStateFromRollup are imported from @/components/fleet/FleetTrafficLights
// ──────────────────────────────────────────────────────────────────────────────

// ──────────────────────────────────────────────────────────────────────────────
// FleetDetailShell — per-fleet sub-tabs: Health / Members / Policies / Operations
// ──────────────────────────────────────────────────────────────────────────────

type FleetDetailTab = 'health' | 'members' | 'policies' | 'operations';

function FleetDetailShell({ fleetId }: { fleetId: number }) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [activeTab, setActiveTab] = useState<FleetDetailTab>('health');

  const { data: targets } = useFleetTargets();
  const { data: rollup, isLoading: rollupLoading } = useFleetRollup(fleetId);
  const fleet = targets?.find((t) => t.id === fleetId);

  const handleBack = useCallback(() => {
    const next = new URLSearchParams(searchParams);
    next.delete('fleet');
    navigate(`?${next.toString()}`);
  }, [navigate, searchParams]);

  const handleTabChange = useCallback((tab: FleetDetailTab) => {
    setActiveTab(tab);
    const next = new URLSearchParams(searchParams);
    next.set('detail_tab', tab);
    setSearchParams(next);
  }, [searchParams, setSearchParams]);

  // Restore tab from URL.
  const urlTab = searchParams.get('detail_tab') as FleetDetailTab | null;
  const validDetailTabs: FleetDetailTab[] = ['health', 'members', 'policies', 'operations'];
  if (urlTab && validDetailTabs.includes(urlTab) && urlTab !== activeTab) {
    setActiveTab(urlTab);
  }

  const tabs = [
    { key: 'health' as FleetDetailTab, label: 'Health', icon: Globe },
    { key: 'members' as FleetDetailTab, label: 'Members', icon: Users },
    { key: 'policies' as FleetDetailTab, label: 'Policies', icon: AlertTriangle },
    { key: 'operations' as FleetDetailTab, label: 'Operations', icon: RefreshCw },
  ];

  return (
    <div className="space-y-6">
      {/* Back affordance + fleet header */}
      <SectionCard compact>
        <div className="space-y-3">
          <button
            type="button"
            onClick={handleBack}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <ChevronLeft className="w-3.5 h-3.5" />
            Back to Fleets
          </button>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <Flag className="w-4 h-4 text-primary shrink-0" />
                <span className="text-sm font-semibold text-foreground truncate">
                  {fleet?.name ?? `Fleet #${fleetId}`}
                </span>
              </div>
              {fleet?.description && (
                <p className="text-xs text-muted-foreground mt-1">{fleet.description}</p>
              )}
              <div className="mt-2">
                {rollupLoading && <Skeleton className="h-4 w-48" />}
                {rollup && <RollupSummaryStrip rollup={rollup} />}
              </div>
            </div>
          </div>
        </div>
      </SectionCard>

      {/* Sub-tabs */}
      <Tabs value={activeTab} onValueChange={(v) => handleTabChange(v as FleetDetailTab)}>
        <ResourceViewTabs
          variant="inline"
          aria-label="Fleet detail views"
          active={activeTab}
          onChange={(key) => handleTabChange(key as FleetDetailTab)}
          tabs={tabs}
        />

        <TabsContent value="health" className="mt-6">
          <OverviewView fleetId={fleetId} />
        </TabsContent>

        <TabsContent value="members" className="mt-6">
          <InventoryView fleetId={fleetId} />
        </TabsContent>

        <TabsContent value="policies" className="mt-6">
          <ComplianceView fleetId={fleetId} />
        </TabsContent>

        <TabsContent value="operations" className="mt-6">
          <BulkOpsView fleetId={fleetId} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Fleets view — D-022 P5 S2
// ──────────────────────────────────────────────────────────────────────────────

/**
 * FleetsView — primary Fleet entity list with per-fleet rollup column.
 *
 * D-022 P6 Slice B: clicking a row navigates to ?fleet=<id> → FleetDetailShell.
 * The old selectedFleetId scoping is replaced by URL-based drill-down.
 * CRUD (create/edit/delete) remains here.
 */
function FleetsView() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();

  const { data: targets, isLoading } = useFleetTargets();
  const createTarget = useCreateFleetTarget();
  const updateTarget = useUpdateFleetTarget();
  const deleteTarget = useDeleteFleetTarget();

  // Batch rollup for all visible fleets.
  const targetIds = useMemo(() => targets?.map((t) => t.id) ?? [], [targets]);
  const { data: rollupList } = useFleetRollups(targetIds);
  const rollupById = useMemo((): Map<number, FleetRollup> => {
    if (!rollupList) return new Map();
    return new Map(rollupList.map((r) => [r.fleet_id, r]));
  }, [rollupList]);

  // Create form state
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createDescription, setCreateDescription] = useState('');
  const [createLabels, setCreateLabels] = useState<string[]>([]);

  // Edit state
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editLabels, setEditLabels] = useState<string[]>([]);

  const handleCreate = () => {
    if (!createName.trim()) return;
    const labels = createLabels.filter((l) => l.includes('='));
    createTarget.mutate(
      {
        name: createName.trim(),
        description: createDescription.trim() || undefined,
        selector: { labels },
      },
      {
        onSuccess: () => {
          setCreateName('');
          setCreateDescription('');
          setCreateLabels([]);
          setShowCreateForm(false);
          queryClient.invalidateQueries({ queryKey: ['fleet', 'targets'] });
        },
      }
    );
  };

  const startEdit = (target: import('@/types/fleet').FleetTarget) => {
    setEditingId(target.id);
    setEditName(target.name);
    setEditDescription(target.description ?? '');
    setEditLabels(target.selector.labels ?? []);
  };

  const handleUpdate = (targetId: number) => {
    const labels = editLabels.filter((l) => l.includes('='));
    updateTarget.mutate(
      {
        targetId,
        body: {
          name: editName.trim() || undefined,
          description: editDescription.trim() || undefined,
          selector: { labels },
        },
      },
      {
        onSuccess: () => {
          setEditingId(null);
          queryClient.invalidateQueries({ queryKey: ['fleet', 'targets'] });
          queryClient.invalidateQueries({ queryKey: ['fleet', 'fleet-members', targetId] });
        },
      }
    );
  };

  const handleDelete = (targetId: number) => {
    deleteTarget.mutate(targetId, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ['fleet', 'targets'] });
      },
    });
  };

  const handleOpenFleet = useCallback((targetId: number) => {
    const next = new URLSearchParams(searchParams);
    next.set('fleet', String(targetId));
    navigate(`?${next.toString()}`);
  }, [navigate, searchParams]);

  return (
    <div className="space-y-6">
      {/* Fleet list */}
      <SectionCard compact>
        <div className="flex items-center justify-between pb-2 mb-3 border-b border-border">
          <div className="flex items-center gap-2">
            <Flag className="w-4 h-4 text-primary" />
            <h3 className="text-sm font-semibold text-foreground">Fleets</h3>
            {targets && (
              <Badge variant="secondary" className="text-xs">
                {targets.length}
              </Badge>
            )}
          </div>
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs gap-1"
            onClick={() => setShowCreateForm(!showCreateForm)}
          >
            <Plus className="w-3 h-3" />
            New Fleet
          </Button>
        </div>

        {/* Create form */}
        {showCreateForm && (
          <div className="mb-4 p-3 rounded-lg border border-border bg-muted/40 space-y-2">
            <div className="text-xs font-medium text-foreground">New Fleet</div>
            <input
              placeholder="Fleet name (e.g. prod-clusters)"
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
              className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs"
            />
            <input
              placeholder="Description (optional)"
              value={createDescription}
              onChange={(e) => setCreateDescription(e.target.value)}
              className="w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs"
            />
            <div className="text-xs text-muted-foreground font-medium">Label selectors (AND)</div>
            <SelectorBuilder
              value={createLabels}
              onChange={setCreateLabels}
              disabled={createTarget.isPending}
            />
            <div className="flex gap-2 pt-1">
              <Button
                size="sm"
                className="h-7 text-xs"
                onClick={handleCreate}
                disabled={createTarget.isPending || !createName.trim()}
              >
                {createTarget.isPending ? 'Creating…' : 'Create'}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 text-xs"
                onClick={() => setShowCreateForm(false)}
              >
                Cancel
              </Button>
            </div>
          </div>
        )}

        {/* Estate summary bar — shown when rollups are available */}
        {rollupList && rollupList.length > 0 && (
          <div className="mb-3 pb-3 border-b border-border">
            <EstateSummaryBar rollups={rollupList} />
          </div>
        )}

        {/* Fleet rows */}
        {isLoading && (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-14 w-full rounded-lg" />
            ))}
          </div>
        )}

        {!isLoading && (!targets || targets.length === 0) && (
          <EmptyState
            icon={Flag}
            title="No fleets defined yet"
            description="Create a fleet to group clusters by label selectors and run bulk operations."
          />
        )}

        {!isLoading && targets && targets.length > 0 && (
          <div className="space-y-2">
            {targets.map((target) => {
              const rollup = rollupById.get(target.id);
              return (
                <div
                  key={target.id}
                  className="rounded-lg border border-border bg-card hover:border-primary/40 p-3 cursor-pointer transition-colors"
                  onClick={() => handleOpenFleet(target.id)}
                >
                  {editingId === target.id ? (
                    /* Edit inline form */
                    <div className="space-y-2" onClick={(e) => e.stopPropagation()}>
                      <input
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="w-full rounded border border-border bg-background text-foreground px-2 py-1 text-xs"
                        placeholder="Fleet name"
                      />
                      <input
                        value={editDescription}
                        onChange={(e) => setEditDescription(e.target.value)}
                        className="w-full rounded border border-border bg-background text-foreground px-2 py-1 text-xs"
                        placeholder="Description (optional)"
                      />
                      <div className="text-xs text-muted-foreground font-medium pt-1">Label selectors (AND)</div>
                      <SelectorBuilder
                        value={editLabels}
                        onChange={setEditLabels}
                        disabled={updateTarget.isPending}
                      />
                      <div className="flex gap-2 pt-1">
                        <Button
                          size="sm"
                          className="h-6 text-xs"
                          onClick={() => handleUpdate(target.id)}
                          disabled={updateTarget.isPending}
                        >
                          {updateTarget.isPending ? 'Saving…' : 'Save'}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-6 text-xs"
                          onClick={() => setEditingId(null)}
                        >
                          Cancel
                        </Button>
                      </div>
                    </div>
                  ) : (
                    /* Read view with rollup column */
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-foreground truncate">
                            {target.name}
                          </span>
                          <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                        </div>
                        {target.description && (
                          <div className="text-xs text-muted-foreground mt-0.5 truncate">
                            {target.description}
                          </div>
                        )}
                        <div className="flex flex-wrap gap-1 mt-1.5">
                          {(target.selector.labels ?? []).length === 0 ? (
                            <span className="text-xs text-muted-foreground italic">no selectors</span>
                          ) : (
                            (target.selector.labels ?? []).map((l) => (
                              <Badge
                                key={l}
                                variant="outline"
                                className="text-xs font-mono px-1.5 py-0"
                              >
                                {l}
                              </Badge>
                            ))
                          )}
                        </div>
                        {/* Traffic lights + rollup strip */}
                        {rollup && (
                          <div className="mt-2 space-y-1.5">
                            <FleetTrafficLights rollup={rollup} />
                            <RollupSummaryStrip rollup={rollup} />
                          </div>
                        )}
                      </div>
                      <div
                        className="flex items-center gap-1 shrink-0"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 w-7 p-0"
                          title="Edit fleet"
                          onClick={() => startEdit(target)}
                        >
                          <Pencil className="w-3 h-3" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 w-7 p-0 text-destructive hover:text-destructive"
                          title="Delete fleet"
                          onClick={() => handleDelete(target.id)}
                          disabled={deleteTarget.isPending}
                        >
                          <Trash2 className="w-3 h-3" />
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </SectionCard>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Main page
// ──────────────────────────────────────────────────────────────────────────────

// D-022 P6 IA: 'dpf' removed — DPU Infrastructure relocated to /infrastructure.
type FleetView = 'overview' | 'inventory' | 'bulkops' | 'compliance' | 'fleets' | 'bnk';

export default function Fleet() {
  const [searchParams, setSearchParams] = useSearchParams();

  // D-022 P6 Slice B: ?fleet=<id> triggers the per-fleet detail drill-down.
  const fleetParam = searchParams.get('fleet');
  const fleetDetailId = fleetParam ? Number(fleetParam) : null;

  // backward compat with ?view=overview etc. (dpf deep-links now redirect to /infrastructure)
  const urlView = searchParams.get('view') ?? searchParams.get('tab');
  const validViews: FleetView[] = ['fleets', 'bnk', 'inventory', 'bulkops', 'compliance', 'overview'];
  const initialView: FleetView =
    urlView && validViews.includes(urlView as FleetView) ? (urlView as FleetView) : 'fleets';
  const [activeView, setActiveView] = useState<FleetView>(initialView);

  const handleSelectView = useCallback(
    (view: string) => {
      const v = validViews.includes(view as FleetView) ? (view as FleetView) : 'fleets';
      setActiveView(v);
      // Clear the fleet drill-down when switching top-level tabs.
      const next = new URLSearchParams(searchParams);
      next.delete('fleet');
      next.delete('detail_tab');
      if (v === 'fleets') {
        next.delete('view');
        next.delete('tab');
      } else {
        next.set('view', v);
        next.delete('tab');
      }
      setSearchParams(next);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [searchParams, setSearchParams],
  );

  const { refresh, isRefreshing } = usePageRefresh();
  const {
    data: bnkConsumption,
    isLoading: bnkLoading,
    error: bnkError,
  } = useBnkConsumption();

  const subtitle = 'Group clusters into fleets and operate them at scale — health, policy, compliance, and staged operations.';

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <PageHeader
        title="Fleet"
        subtitle={subtitle}
        onRefresh={refresh}
        isRefreshing={isRefreshing}
      />

      {/* When ?fleet=<id> is set, show per-fleet detail shell (no top-level tabs). */}
      {fleetDetailId != null ? (
        <FleetDetailShell fleetId={fleetDetailId} />
      ) : (
        <Tabs value={activeView} onValueChange={handleSelectView}>
          {/* D-022 P6 IA: 'DPU Infrastructure' tab removed — relocated to /infrastructure.
              Top-level tabs: Fleets list + fleet-wide BNK Resources. */}
          <ResourceViewTabs
            variant="inline"
            aria-label="Fleet views"
            active={activeView}
            onChange={(key) => handleSelectView(key)}
            tabs={[
              { key: 'fleets', label: 'Fleets', icon: Flag },
              { key: 'bnk', label: 'BNK Resources', icon: Gauge },
            ]}
          />

          <TabsContent value="fleets" className="mt-6">
            <FleetsView />
          </TabsContent>

          <TabsContent value="bnk" className="mt-6">
            <BnkResourcesPanel data={bnkConsumption} isLoading={bnkLoading} error={bnkError} />
          </TabsContent>

          {/* Legacy deep-links: ?view=inventory|bulkops|compliance|overview are forwarded to
              the Fleets list tab (the views now live inside FleetDetailShell). */}
          <TabsContent value="inventory" className="mt-6">
            <FleetsView />
          </TabsContent>
          <TabsContent value="bulkops" className="mt-6">
            <FleetsView />
          </TabsContent>
          <TabsContent value="compliance" className="mt-6">
            <FleetsView />
          </TabsContent>
          <TabsContent value="overview" className="mt-6">
            <FleetsView />
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
