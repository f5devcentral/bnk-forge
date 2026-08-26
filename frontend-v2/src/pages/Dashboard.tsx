/**
 * Dashboard — K8s-first Command Center
 *
 * K8S-UX-009: Reworked to make K8s a first-class citizen.
 *
 * Layout (top to bottom):
 *   1. Action bar — greeting, subtitle with fleet status, quick actions
 *   2. Fleet Health Overview — horizontal fleet status bar with operator mini-cards
 *   3. Active Operations — in-progress tasks (K8s + IaC)
 *   4. Attention Needed — failures, drift, unhealthy clusters, offline operators
 *   5. Projects + Clusters (side by side) — clusters enriched with BNK data
 *   6. Stats row — health ring + stat cards (includes Operators count)
 *   7. Activity feed — recent operations
 *   8. Blueprints — demoted to bottom, more compact
 *
 * Sub-components in components/dashboard/:
 *   HealthRing, StatCard, ActiveOperationCard, AttentionCard, ActivityItem, SectionHeader
 */

import { useState, useMemo } from 'react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { SectionCard } from '@/components/ui/section-card';
import { PageHeader } from '@/components/layout/PageHeader';
import { usePageRefresh } from '@/hooks/usePageRefresh';
import { useDeploymentStats, useRecentDeployments } from '@/hooks/useDeployments';
import { useProjects } from '@/hooks/useProjects';
import { useGlobalDriftSummary, useRecentDrifted, useProjectDriftCounts, useGlobalDriftCount } from '@/hooks/useDrift';
import { useTasks } from '@/hooks/useTasks';
import { useAllClusters } from '@/hooks/useK8s';
import { useStackTemplates } from '@/hooks/useStacks';
import { useFleetHealth, useFleetTargets, useFleetRollups } from '@/hooks/useFleet';
import { ClusterStatusBadge } from '@/components/ui/ClusterStatusBadge';
import { useConnectivity } from '@/hooks/useConnectivity';
import { reachabilityKey } from '@/lib/api/connectivity';
import { cn } from '@/lib/utils';
import { formatTimeAgo } from '@/lib/time-utils';
import { DISPLAY_LIMITS } from '@/lib/constants';
import { calculateHealthScore } from '@/lib/health-utils';
import { StackDetailDialog } from '@/components/stacks/StackDetailDialog';
import { AddClusterFlowDialog } from '@/components/k8s/AddClusterFlowDialog';
import { SSHConnectivityBadge } from '@/components/ui/SSHConnectivityBadge';
import {
  HealthRing,
  StatCard,
  ActiveOperationCard,
  AttentionCard,
  ActivityItem,
  SectionHeader,
  ValueJourneyBanner,
} from '@/components/dashboard';
import type { AttentionItem as AttentionItemType } from '@/components/dashboard';
import {
  EstateSummaryBar,
  FleetTrafficLights,
  healthStateFromRollup,
  policyStateFromRollup,
} from '@/components/fleet/FleetTrafficLights';
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  ArrowUpRight,
  Box,
  CheckCircle2,
  Clock,
  Flag,
  FolderGit2,
  GitCompare,
  Globe,
  Layers,
  Package,
  Rocket,
  Server,
  Shield,
  WifiOff,
  Zap,
} from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { getProjectLocationInfo } from '@/lib/aws-regions';
import type { FleetOperatorHealth, FleetOperatorStatus, FleetRollup } from '@/types/fleet';

// ============================================================================
// Helpers
// ============================================================================

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 17) return 'Good afternoon';
  return 'Good evening';
}

const blueprintCategoryIcons: Record<string, React.ElementType> = {
  infrastructure: Server,
  bnk: Shield,
  solution: Rocket,
  custom: Package,
};

/** Token-pure status dot color for fleet operator status */
function getFleetStatusDot(status: FleetOperatorStatus): string {
  switch (status) {
    case 'healthy': return 'bg-success';
    case 'warning': return 'bg-warning';
    case 'critical': return 'bg-destructive';
    case 'offline': return 'bg-muted-foreground';
    default: return 'bg-muted-foreground';
  }
}

/** Token-pure text color for fleet operator status */
function getFleetStatusText(status: FleetOperatorStatus): string {
  switch (status) {
    case 'healthy': return 'text-success';
    case 'warning': return 'text-warning';
    case 'critical': return 'text-destructive';
    case 'offline': return 'text-muted-foreground';
    default: return 'text-muted-foreground';
  }
}

// ============================================================================
// Main Dashboard Component
// ============================================================================

export default function Dashboard() {
  const navigate = useNavigate();
  const [selectedBlueprintSlug, setSelectedBlueprintSlug] = useState('');
  const [blueprintDialogOpen, setBlueprintDialogOpen] = useState(false);
  const [showAddCluster, setShowAddCluster] = useState(false);

  const { refresh, isRefreshing } = usePageRefresh();

  // --- Data fetching ---
  const { data: stats, isLoading: statsLoading } = useDeploymentStats();
  const { data: recentDeployments } = useRecentDeployments(10);
  const { data: projects, isLoading: projectsLoading, isError: projectsError, error: projectsErrorData, refetch: refetchProjects } = useProjects();
  const { data: driftSummary } = useGlobalDriftSummary();
  const { data: recentDrifted } = useRecentDrifted(6);
  const driftCount = useGlobalDriftCount();
  const projectDriftCounts = useProjectDriftCounts(20);
  const { data: tasksData } = useTasks({ limit: 10 });
  const { data: clustersData, isLoading: clustersLoading } = useAllClusters();
  // Read the connectivity registry once so per-cluster lookups in the .map()
  // below don't violate hooks-in-loops. Used to flag "stale-but-was-healthy"
  // clusters: when reachability is offline but fleet last reported healthy,
  // we render amber + "<status> Xm ago" instead of a misleading green pill.
  const { states: connectivityStates } = useConnectivity();
  const { data: blueprints } = useStackTemplates({ is_featured: true });
  // Fleet health (operator-level) — still used for Clusters section enrichment + Attention
  const { data: fleetHealth } = useFleetHealth();
  // Fleet entity model — D-022 P6: Command Center leads with actual fleets + conformance
  const { data: fleetTargets, isLoading: fleetsLoading } = useFleetTargets();
  const fleetTargetIds = useMemo(() => fleetTargets?.map((t) => t.id) ?? [], [fleetTargets]);
  const { data: fleetRollupList } = useFleetRollups(fleetTargetIds);
  const fleetRollupById = useMemo((): Map<number, FleetRollup> => {
    if (!fleetRollupList) return new Map();
    return new Map(fleetRollupList.map((r) => [r.fleet_id, r]));
  }, [fleetRollupList]);

  const dashboardBlueprints = useMemo(() => {
    if (!blueprints || blueprints.length === 0) return [];
    const featured = blueprints.filter((b) => b.is_featured);
    return featured.length > 0 ? featured.slice(0, 4) : blueprints.slice(0, 4);
  }, [blueprints]);

  const healthScore = calculateHealthScore({
    totalModules: stats?.activeModules || 0,
    deployedModules: stats?.deployedModules || 0,
    failedModules: stats?.failedModules || 0,
    driftedModules: driftSummary?.modules_with_drift || 0,
  });

  const projectCount = projects?.length || 0;
  const clusterCount = clustersData?.clusters?.length || 0;

  // Fleet health derived data (operator-level — used for Clusters enrichment + Attention section)
  const fleetTotal = fleetHealth?.total_clusters || 0;
  const fleetCritical = fleetHealth?.critical || 0;
  const fleetOperators = useMemo(() => fleetHealth?.operators || [], [fleetHealth?.operators]);

  // Stale-healthy reconciliation: clusters the operator last reported as
  // healthy but that forge can't currently reach. They should NOT count as
  // healthy — we have no way to verify right now. Move them to a separate
  // "stale" bucket so the green count is honest.
  const fleetStaleHealthy = useMemo(() => {
    if (!fleetHealth?.operators) return 0;
    return fleetHealth.operators.filter((op) => {
      if (op.status !== 'healthy') return false;
      const conn = connectivityStates[reachabilityKey('cluster', op.cluster_id)];
      return conn?.state === 'unreachable';
    }).length;
  }, [fleetHealth?.operators, connectivityStates]);
  const fleetHealthy = Math.max(0, (fleetHealth?.healthy || 0) - fleetStaleHealthy);

  // Create a map of cluster_name -> fleet health for enriching cluster cards
  const fleetByCluster = useMemo(() => {
    const map: Record<string, FleetOperatorHealth> = {};
    fleetOperators.forEach((op) => {
      map[op.cluster_name] = op;
    });
    return map;
  }, [fleetOperators]);

  // Unhealthy clusters for attention section
  const unhealthyClusters = useMemo(() => {
    return fleetOperators.filter(op => op.status === 'critical' || op.status === 'warning');
  }, [fleetOperators]);

  // Offline operators for attention section
  const offlineOperators = useMemo(() => {
    return fleetOperators.filter(op => op.status === 'offline');
  }, [fleetOperators]);

  const activeOps = useMemo(() => {
    return tasksData?.tasks
      ?.filter(t => t.status === 'in_progress')
      .slice(0, 5)
      .map(task => ({
        id: task.id,
        projectName: task.project_name || 'Unknown',
        moduleName: task.module_name || 'Project',
        taskType: task.task_type,
        projectId: task.project_id,
        time: formatTimeAgo(task.created_at),
      })) || [];
  }, [tasksData]);

  const attentionItems: AttentionItemType[] = useMemo(() => {
    const failedModules = recentDeployments?.filter(m => m.status === 'failed') ?? [];
    return failedModules.slice(0, DISPLAY_LIMITS.DASHBOARD_ATTENTION).map(module => {
      const project = projects?.find(p => p.id === module.project_id);
      return {
        id: module.id,
        type: 'failure' as const,
        project: project?.name || 'Unknown',
        module: module.library_module?.name || module.path_in_project,
        message: module.deployment_error || 'Deployment failed',
        projectId: module.project_id,
      };
    });
  }, [recentDeployments, projects]);

  // Total attention count: failures + drift + unhealthy clusters + offline operators
  const totalAttentionCount = attentionItems.length
    + (recentDrifted?.length || 0)
    + unhealthyClusters.length
    + offlineOperators.length;

  const recentActivity = useMemo(() => {
    return tasksData?.tasks?.slice(0, DISPLAY_LIMITS.RECENT_ACTIVITY).map(task => {
      const statusMap: Record<string, 'success' | 'progress' | 'failed'> = {
        completed: 'success',
        in_progress: 'progress',
        failed: 'failed',
        queued: 'progress',
      };
      return {
        action: task.task_type.charAt(0).toUpperCase() + task.task_type.slice(1),
        module: task.module_name || 'Project',
        project: task.project_name || 'Unknown',
        time: formatTimeAgo(task.created_at),
        status: statusMap[task.status] || ('progress' as const),
      };
    }) || [];
  }, [tasksData]);

  // Fleet conformance subtitle — D-022 P6: lead with fleet entity model
  const fleetSubtitleText = useMemo(() => {
    if (!fleetTargets || fleetTargets.length === 0) return null;
    const total = fleetTargets.length;
    if (!fleetRollupList || fleetRollupList.length === 0) {
      return `${total} fleet${total !== 1 ? 's' : ''}`;
    }
    const healthy = fleetRollupList.filter((r) => r.worst_state === 'ready').length;
    const needAttention = fleetRollupList.filter((r) => {
      const hs = healthStateFromRollup(r);
      const ps = policyStateFromRollup(r);
      return hs === 'red' || hs === 'amber' || ps === 'red' || r.ops_state === 'red';
    }).length;
    if (needAttention > 0) {
      return `${total} fleet${total !== 1 ? 's' : ''} · ${healthy} healthy · ${needAttention} need attention`;
    }
    return `${total} fleet${total !== 1 ? 's' : ''} · ${healthy} healthy`;
  }, [fleetTargets, fleetRollupList]);

  // Subtitle line: prioritize active ops / fleet conformance, then fallback
  const subtitleText = useMemo(() => {
    if (activeOps.length > 0) {
      return `${activeOps.length} operation${activeOps.length > 1 ? 's' : ''} in progress`;
    }
    if (fleetSubtitleText) return fleetSubtitleText;
    if (fleetCritical > 0) {
      return `${fleetCritical} cluster${fleetCritical > 1 ? 's' : ''} in critical state`;
    }
    if (driftCount > 0) {
      return `${driftCount} module${driftCount > 1 ? 's' : ''} with drift detected`;
    }
    if (fleetTotal > 0) {
      return `${fleetHealthy}/${fleetTotal} clusters healthy · ${projectCount} project${projectCount !== 1 ? 's' : ''}`;
    }
    return `${projectCount} project${projectCount !== 1 ? 's' : ''} · ${clusterCount} cluster${clusterCount !== 1 ? 's' : ''}`;
  }, [activeOps, fleetSubtitleText, fleetCritical, fleetHealthy, fleetTotal, driftCount, projectCount, clusterCount]);

  // ========================================================================
  // K8s-first layout
  // ========================================================================

  if (projectsError) {
    return <ErrorState error={projectsErrorData} onRetry={refetchProjects} />;
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto" data-onboarding="dashboard">

      {/* 1. PAGE HEADER */}
      <PageHeader
        title={getGreeting()}
        subtitle={subtitleText}
        onRefresh={refresh}
        isRefreshing={isRefreshing}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => setShowAddCluster(true)} className="gap-1.5">
              <Server className="h-3.5 w-3.5" />
              Add Cluster
            </Button>
            <Button
              size="sm"
              onClick={() => navigate('/projects?action=create')}
              className="gap-1.5"
            >
              <Rocket className="h-3.5 w-3.5" />
              New Project
            </Button>
          </>
        }
      />

      {/* 1b. VALUE JOURNEY — GAP-004 */}
      <ValueJourneyBanner />

      {/* 2. FLEETS OVERVIEW — D-022 P6: fleet-entity model (conformance + attention-needed) */}
      {(fleetsLoading || (fleetTargets && fleetTargets.length > 0)) && (
        <SectionCard>
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Flag className="h-4 w-4 text-muted-foreground" />
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Fleets</p>
              {fleetTargets && (
                <span className="px-1.5 py-0.5 text-xs font-medium rounded-full bg-muted text-muted-foreground">
                  {fleetTargets.length}
                </span>
              )}
            </div>
            <Link to="/fleet" className="text-xs text-primary hover:text-primary/80 font-medium flex items-center gap-1 group">
              Fleet Dashboard
              <ArrowUpRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
            </Link>
          </div>

          {fleetsLoading ? (
            <div className="space-y-2">
              {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-12 rounded-lg" />)}
            </div>
          ) : (
            <div className="space-y-4">
              {/* Estate summary — conformance headline */}
              {fleetRollupList && fleetRollupList.length > 0 && (
                <EstateSummaryBar rollups={fleetRollupList} />
              )}

              {/* Fleets needing attention — red/amber health, policy, or ops */}
              {(() => {
                const attention = (fleetTargets ?? []).filter((t) => {
                  const r = fleetRollupById.get(t.id);
                  if (!r) return false;
                  const hs = healthStateFromRollup(r);
                  const ps = policyStateFromRollup(r);
                  return hs === 'red' || hs === 'amber' || ps === 'red' || r.ops_state === 'red';
                }).sort((a, b) => {
                  // Worst-first: red > amber
                  const score = (id: number) => {
                    const r = fleetRollupById.get(id);
                    if (!r) return 0;
                    const hs = healthStateFromRollup(r);
                    const ps = policyStateFromRollup(r);
                    const hasRed = hs === 'red' || ps === 'red' || r.ops_state === 'red';
                    return hasRed ? 2 : 1;
                  };
                  return score(b.id) - score(a.id);
                });

                if (attention.length === 0 && fleetRollupList && fleetRollupList.length > 0) {
                  return (
                    <div className="flex items-center gap-2 px-1 py-2 text-xs text-success">
                      <CheckCircle2 className="h-3.5 w-3.5" />
                      All fleets healthy
                    </div>
                  );
                }

                if (attention.length === 0) return null;

                return (
                  <div className="space-y-1.5">
                    <p className="text-xs font-medium text-muted-foreground px-1">Needs attention</p>
                    {attention.slice(0, 5).map((target) => {
                      const rollup = fleetRollupById.get(target.id);
                      if (!rollup) return null;
                      const hs = healthStateFromRollup(rollup);
                      const borderColor =
                        hs === 'red' ? 'border-destructive/20 hover:border-destructive/30'
                        : 'border-warning/20 hover:border-warning/30';
                      return (
                        <Link
                          key={target.id}
                          to={`/fleet?fleet=${target.id}`}
                          className="block group"
                        >
                          <div className={cn(
                            'flex items-center gap-3 p-3 rounded-lg border transition-all hover:bg-muted/30',
                            borderColor,
                          )}>
                            <Flag className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                            <div className="flex-1 min-w-0">
                              <span className="font-medium text-sm truncate block text-foreground">
                                {target.name}
                              </span>
                              <div className="flex items-center gap-3 mt-0.5 text-xs text-muted-foreground">
                                <span>{rollup.member_count} member{rollup.member_count !== 1 ? 's' : ''}</span>
                                {rollup.total_evaluated > 0 && (
                                  <span className={rollup.drift_count > 0 ? 'text-warning' : 'text-success'}>
                                    {rollup.compliant_count}/{rollup.total_evaluated} compliant
                                  </span>
                                )}
                                {rollup.drift_count > 0 && (
                                  <span className="text-warning">{rollup.drift_count} drifted</span>
                                )}
                              </div>
                            </div>
                            <FleetTrafficLights rollup={rollup} />
                            <ArrowRight className="h-3.5 w-3.5 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground" />
                          </div>
                        </Link>
                      );
                    })}
                    {attention.length > 5 && (
                      <Link to="/fleet" className="block text-xs text-primary hover:text-primary/80 px-1 py-1">
                        +{attention.length - 5} more fleets need attention
                      </Link>
                    )}
                  </div>
                );
              })()}

              {/* All fleets compact summary — shown when rollups available */}
              {fleetTargets && fleetTargets.length > 0 && fleetRollupList && fleetRollupList.length > 0 && (() => {
                const allGreen = fleetTargets.every((t) => {
                  const r = fleetRollupById.get(t.id);
                  if (!r) return true;
                  return healthStateFromRollup(r) === 'green' && policyStateFromRollup(r) !== 'red' && r.ops_state !== 'red';
                });
                if (allGreen) return null; // already shown "All fleets healthy"

                // Show remaining fleets not in the attention list as compact chips
                const attentionIds = new Set(
                  (fleetTargets).filter((t) => {
                    const r = fleetRollupById.get(t.id);
                    if (!r) return false;
                    const hs = healthStateFromRollup(r);
                    const ps = policyStateFromRollup(r);
                    return hs === 'red' || hs === 'amber' || ps === 'red' || r.ops_state === 'red';
                  }).map((t) => t.id)
                );
                const greenFleets = fleetTargets.filter((t) => !attentionIds.has(t.id));
                if (greenFleets.length === 0) return null;

                return (
                  <div className="flex flex-wrap gap-1.5 pt-1 border-t border-border">
                    {greenFleets.slice(0, 6).map((t) => (
                      <Link key={t.id} to={`/fleet?fleet=${t.id}`}>
                        <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full border border-border text-xs text-muted-foreground hover:border-primary/30 hover:text-foreground transition-colors">
                          <span className="h-1.5 w-1.5 rounded-full bg-success" />
                          {t.name}
                        </span>
                      </Link>
                    ))}
                    {greenFleets.length > 6 && (
                      <Link to="/fleet">
                        <span className="inline-flex items-center px-2 py-1 rounded-full border border-border text-xs text-muted-foreground hover:text-foreground transition-colors">
                          +{greenFleets.length - 6} more
                        </span>
                      </Link>
                    )}
                  </div>
                );
              })()}
            </div>
          )}
        </SectionCard>
      )}

      {/* 3. ACTIVE OPERATIONS */}
      {activeOps.length > 0 && (
        <SectionCard>
          <SectionHeader icon={Zap} title="Active Operations" count={activeOps.length} />
          <div className="space-y-2">
            {activeOps.map(op => (
              <ActiveOperationCard key={op.id} {...op} />
            ))}
          </div>
        </SectionCard>
      )}

      {/* 4. ATTENTION NEEDED — now includes unhealthy clusters + offline operators */}
      {totalAttentionCount > 0 && (
        <SectionCard className="border-warning/30">
          <div className="flex items-center gap-2 mb-4">
            <AlertTriangle className="h-4 w-4 text-warning" />
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Attention Needed</p>
            <Badge variant="warning" className="text-xs">
              {totalAttentionCount}
            </Badge>
          </div>
          <div>
            <div className="space-y-2">
              {/* K8S-UX-009: Unhealthy clusters */}
              {unhealthyClusters.map((op) => (
                <Link key={`cluster-${op.operator_id}`} to="/fleet">
                  <div className={cn(
                    'flex items-center gap-3 p-4 rounded-xl border transition-all hover:shadow-sm',
                    op.status === 'critical'
                      ? 'border-destructive/20 hover:border-destructive/30'
                      : 'border-warning/20 hover:border-warning/30'
                  )}>
                    <div className={cn(
                      'p-2 rounded-lg',
                      op.status === 'critical' ? 'bg-destructive/10' : 'bg-warning/10'
                    )}>
                      <Server className={cn(
                        'h-5 w-5',
                        op.status === 'critical' ? 'text-destructive' : 'text-warning'
                      )} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="font-semibold text-sm text-foreground">
                          {op.cluster_name}
                        </span>
                        <Badge variant={op.status === 'critical' ? 'destructive' : 'warning'} className="text-[10px]">
                          {op.status}
                        </Badge>
                      </div>
                      <div className="flex items-center gap-3">
                        {op.bnk_version && (
                          <span className="text-xs text-muted-foreground">BNK {op.bnk_version}</span>
                        )}
                        <span className="text-xs text-muted-foreground">
                          {op.health_summary.healthy} healthy · {op.health_summary.warning} warning · {op.health_summary.critical} critical
                        </span>
                      </div>
                    </div>
                  </div>
                </Link>
              ))}

              {/* K8S-UX-009: Offline operators */}
              {offlineOperators.map((op) => (
                <Link key={`offline-${op.operator_id}`} to="/fleet?tab=operators">
                  <div className="flex items-center gap-3 p-4 rounded-xl border border-border hover:border-border/80 hover:shadow-sm transition-all">
                    <div className="p-2 rounded-lg bg-muted">
                      <WifiOff className="h-5 w-5 text-muted-foreground" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <span className="font-semibold text-sm text-foreground">
                        {op.cluster_name}
                      </span>
                      <p className="text-xs text-muted-foreground">
                        Operator offline — last seen {op.last_seen ? formatTimeAgo(op.last_seen) : 'never'}
                      </p>
                    </div>
                  </div>
                </Link>
              ))}

              {/* Per-module drift items */}
              {recentDrifted && recentDrifted.length > 0 && recentDrifted.map((driftItem) => {
                const totalChanges = driftItem.resource_changes
                  ? driftItem.resource_changes.add + driftItem.resource_changes.change + driftItem.resource_changes.destroy
                  : 0;
                return (
                  <div
                    key={`drift-${driftItem.id}`}
                    className="p-4 rounded-xl border border-warning/20 hover:border-warning/30 transition-all hover:shadow-sm group"
                  >
                    <div className="flex items-start gap-3">
                      <div className="p-2 rounded-lg bg-warning/10">
                        <GitCompare className="h-5 w-5 text-warning" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-0.5">
                          <span className="font-semibold text-sm text-foreground">
                            {driftItem.module_name}
                          </span>
                          <span className="text-border">·</span>
                          <span className="text-sm text-muted-foreground">
                            {driftItem.project_name}
                          </span>
                        </div>
                        <div className="flex items-center gap-3">
                          {totalChanges > 0 && (
                            <span className="text-xs text-muted-foreground">
                              {totalChanges} resource{totalChanges !== 1 ? 's' : ''} changed
                            </span>
                          )}
                          {driftItem.last_check_at && (
                            <span className="text-xs flex items-center gap-1 text-muted-foreground">
                              <Clock className="h-3 w-3" />
                              {formatTimeAgo(driftItem.last_check_at)}
                            </span>
                          )}
                          {driftItem.resource_changes && (
                            <div className="flex items-center gap-1">
                              {driftItem.resource_changes.add > 0 && (
                                <Badge variant="success" className="text-[10px] px-1 py-0">
                                  +{driftItem.resource_changes.add}
                                </Badge>
                              )}
                              {driftItem.resource_changes.change > 0 && (
                                <Badge variant="warning" className="text-[10px] px-1 py-0">
                                  ~{driftItem.resource_changes.change}
                                </Badge>
                              )}
                              {driftItem.resource_changes.destroy > 0 && (
                                <Badge variant="destructive" className="text-[10px] px-1 py-0">
                                  -{driftItem.resource_changes.destroy}
                                </Badge>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5 flex-shrink-0">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-warning hover:text-warning/80 hover:bg-warning/10 h-7 text-xs"
                          onClick={() => navigate(`/projects/${driftItem.project_id}?tab=drift`)}
                        >
                          Review Changes
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 text-xs text-muted-foreground hover:text-foreground"
                          onClick={() => navigate(`/projects/${driftItem.project_id}`)}
                        >
                          Reconcile
                        </Button>
                      </div>
                    </div>
                  </div>
                );
              })}
              {/* Drift summary fallback */}
              {driftCount > 0 && (!recentDrifted || recentDrifted.length === 0) && (
                <Link to="/projects">
                  <div className="flex items-center gap-3 p-4 rounded-xl border border-warning/20 hover:border-warning/30 hover:shadow-sm transition-all">
                    <div className="p-2 rounded-lg bg-warning/10">
                      <GitCompare className="h-5 w-5 text-warning" />
                    </div>
                    <div className="flex-1">
                      <span className="font-semibold text-sm text-foreground">
                        {driftCount} module{driftCount > 1 ? 's' : ''} with configuration drift
                      </span>
                      <p className="text-xs text-muted-foreground">
                        Infrastructure has changed from desired state
                      </p>
                    </div>
                  </div>
                </Link>
              )}
              {attentionItems.map((item) => (
                <AttentionCard key={item.id} item={item} />
              ))}
            </div>
          </div>
        </SectionCard>
      )}

      {/* 5. PROJECTS + CLUSTERS (side by side) — clusters enriched with BNK data */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Projects */}
        <SectionCard>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <FolderGit2 className="h-4 w-4 text-muted-foreground" />
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Projects</p>
              <span className="px-1.5 py-0.5 text-xs font-medium rounded-full bg-muted text-muted-foreground">
                {projectCount}
              </span>
            </div>
            <Link to="/projects" className="text-xs text-primary hover:text-primary/80 font-medium flex items-center gap-1 group">
              View All
              <ArrowUpRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
            </Link>
          </div>
          {projectsLoading ? (
              <div className="space-y-2">
                {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-12 rounded-lg" />)}
              </div>
            ) : projects && projects.length > 0 ? (
              <div className="space-y-1">
                {projects.slice(0, DISPLAY_LIMITS.DASHBOARD_CARDS).map((project) => {
                  const locationInfo = getProjectLocationInfo(project.cloud_provider, project.region, project.credential_template?.provider, project.project_type);
                  // Status dot: destructive for failures, success for deployed, muted for inactive
                  const statusDot = project.failed_count > 0
                    ? 'bg-destructive'
                    : project.deployed_count > 0
                      ? 'bg-success'
                      : 'bg-muted-foreground';
                  return (
                    <Link key={project.id} to={`/projects/${project.id}`} className="block group">
                      <div className="flex items-center gap-3 p-3 rounded-lg transition-colors hover:bg-muted/50">
                        <div className={cn('h-2 w-2 rounded-full flex-shrink-0', statusDot)} />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-sm truncate text-foreground">{project.name}</span>
                          </div>
                          <span className="text-xs text-muted-foreground">
                            {locationInfo?.flag || '📦'} {locationInfo?.display || 'No location'}
                            {project.target_platform_profile && project.target_platform_profile !== 'unknown' && (
                              <> · {project.target_platform_profile.toUpperCase()}</>
                            )}
                          </span>
                        </div>
                        <span className="text-xs tabular-nums text-muted-foreground">
                          {project.deployed_count || 0}/{project.module_count || 0} deployed
                        </span>
                        {projectDriftCounts[project.id] > 0 && (
                          <span
                            className="px-1.5 py-0.5 text-[10px] font-semibold rounded-full flex items-center gap-1 bg-warning/10 text-warning"
                            title={`${projectDriftCounts[project.id]} drifted module${projectDriftCounts[project.id] > 1 ? 's' : ''}`}
                          >
                            <GitCompare className="h-3 w-3" />
                            {projectDriftCounts[project.id]}
                          </span>
                        )}
                        <SSHConnectivityBadge
                          variant="compact"
                          credentialId={project.ssh_credential_id}
                          label={project.ssh_credential?.name}
                        />
                        <ArrowRight className="h-3.5 w-3.5 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground" />
                      </div>
                    </Link>
                  );
                })}
              </div>
            ) : (
              <EmptyState
                icon={FolderGit2}
                title="No projects yet"
                description="Start your first deployment"
                action={{ label: 'Create Project', onClick: () => navigate('/projects/new') }}
                size="sm"
                illustration={false}
              />
            )}
          </SectionCard>

        {/* Clusters — K8S-UX-009: enriched with BNK version, TMM count, operator status */}
        <SectionCard>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Server className="h-4 w-4 text-muted-foreground" />
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Clusters</p>
              <span className="px-1.5 py-0.5 text-xs font-medium rounded-full bg-muted text-muted-foreground">
                {clusterCount}
              </span>
            </div>
            <Link to="/kubernetes" className="text-xs text-primary hover:text-primary/80 font-medium flex items-center gap-1 group">
              View All
              <ArrowUpRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
            </Link>
          </div>
          <div>
            {clustersLoading ? (
              <div className="space-y-2">
                {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-12 rounded-lg" />)}
              </div>
            ) : clustersData?.clusters && clustersData.clusters.length > 0 ? (
              <div className="space-y-1">
                {clustersData.clusters.slice(0, DISPLAY_LIMITS.DASHBOARD_CARDS).map((cluster) => {
                  const fleetInfo = fleetByCluster[cluster.name];
                  const conn = connectivityStates[reachabilityKey('cluster', cluster.id)];
                  const isReachUnreachable = conn?.state === 'unreachable';
                  // "Stale-but-was-healthy": forge can't reach this cluster right
                  // now, but the operator's last reported status was positive.
                  // Render warning dot + "<status> Xm ago" rather than a misleading
                  // success pill — honest about what we know.
                  const fleetStatusIsPositive = !!fleetInfo &&
                    ['healthy', 'warning', 'degraded'].includes(fleetInfo.status);
                  const isStaleHealthy = isReachUnreachable && fleetStatusIsPositive;
                  const statusDot = isStaleHealthy
                    ? 'bg-warning'
                    : fleetInfo
                      ? getFleetStatusDot(fleetInfo.status)
                      : cluster.status === 'active'
                        ? 'bg-success'
                        : cluster.status === 'error'
                          ? 'bg-destructive'
                          : 'bg-muted-foreground';
                  return (
                    <Link key={cluster.id} to="/kubernetes" className="block group">
                      <div className="flex items-center gap-3 p-3 rounded-lg transition-colors hover:bg-muted/50">
                        <div className={cn('h-2 w-2 rounded-full flex-shrink-0', statusDot)} />
                        <div className="flex-1 min-w-0">
                          <span className="font-medium text-sm truncate block text-foreground">{cluster.name}</span>
                          <div className="flex items-center gap-2">
                            <span className="text-xs text-muted-foreground">
                              {cluster.detected_platform_profile?.toUpperCase() || cluster.cloud_provider?.toUpperCase() || 'On-Prem'}{cluster.region ? ` · ${cluster.region}` : ''}{cluster.account_id ? ` · ${cluster.account_id}` : ''}
                            </span>
                            {fleetInfo?.bnk_version && (
                              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-info/10 text-info">
                                BNK {fleetInfo.bnk_version}
                              </span>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          {fleetInfo && fleetInfo.tmm_count > 0 && (
                            <span className="text-[10px] text-muted-foreground">
                              {fleetInfo.tmm_count} TMM
                            </span>
                          )}
                          <ClusterStatusBadge
                            cluster={cluster}
                            showStatusLabel={isReachUnreachable || !fleetInfo || fleetInfo.status === 'offline'}
                          />
                          {/* Three rendering modes for the secondary status text:
                              1. Stale-but-was-healthy → warning "<status> Xm ago"
                                 using fleetInfo.last_seen — honest about not
                                 being able to verify right now.
                              2. Cluster reachable + fleet data → fleet's own
                                 colored status pill (BNK component health).
                              3. No fleet data → cluster.version as filler. */}
                          {isStaleHealthy ? (
                            <span
                              className="text-[10px] font-medium capitalize text-warning"
                              title={fleetInfo!.last_seen ? `Last reported ${fleetInfo!.status} at ${fleetInfo!.last_seen}` : ''}
                            >
                              {fleetInfo!.status}
                              {fleetInfo!.last_seen ? ` · ${formatTimeAgo(fleetInfo!.last_seen)}` : ''}
                            </span>
                          ) : fleetInfo && fleetInfo.status !== 'offline' && !isReachUnreachable ? (
                            <span className={cn('text-[10px] font-medium capitalize', getFleetStatusText(fleetInfo.status))}>
                              {fleetInfo.status}
                            </span>
                          ) : !fleetInfo ? (
                            <span className="text-xs text-muted-foreground">{cluster.version || '—'}</span>
                          ) : null}
                        </div>
                      </div>
                    </Link>
                  );
                })}
              </div>
            ) : (
              <EmptyState
                icon={Box}
                title="No clusters connected"
                description="Connect a Kubernetes cluster to get started"
                action={{ label: 'Add Cluster', onClick: () => setShowAddCluster(true) }}
                size="sm"
                illustration={false}
              />
            )}
          </div>
        </SectionCard>
      </div>

      {/* 6. STATS ROW */}
      <SectionCard>
        <div className="grid grid-cols-12 gap-4">
          <div className="col-span-6 sm:col-span-3 lg:col-span-2 flex items-center justify-center">
            {statsLoading ? (
              <Skeleton className="h-[100px] w-[100px] rounded-full" />
            ) : (
              <HealthRing score={healthScore} size={100} />
            )}
          </div>
          <div className="col-span-6 sm:col-span-9 lg:col-span-10 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <StatCard label="Projects" value={projectCount} icon={FolderGit2} />
            <StatCard label="Clusters" value={clusterCount} icon={Server} />
            <StatCard label="Fleet" value={fleetTotal} icon={Globe} variant={fleetCritical > 0 ? 'warning' : 'default'} />
            <StatCard label="Active Ops" value={activeOps.length} icon={Zap} variant={activeOps.length > 0 ? 'warning' : 'default'} />
            <StatCard label="Drift" value={driftCount} icon={GitCompare} variant={driftCount > 0 ? 'warning' : 'success'} />
            <StatCard label="Modules" value={stats?.activeModules || 0} icon={Layers} />
          </div>
        </div>
      </SectionCard>

      {/* 7. ACTIVITY FEED */}
      <SectionCard>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-muted-foreground" />
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Recent Operations</p>
          </div>
          <Link to="/tasks" className="text-xs text-primary hover:text-primary/80 font-medium flex items-center gap-1 group">
            All Operations
            <ArrowUpRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
          </Link>
        </div>
        {recentActivity.length > 0 ? (
          <div className="space-y-0.5">
            {recentActivity.map((item, idx) => (
              <ActivityItem key={idx} {...item} />
            ))}
          </div>
        ) : (
          <div className="text-center py-8 text-muted-foreground">
            <Activity className="h-8 w-8 mx-auto mb-2 opacity-30" />
            <p className="font-medium text-sm">No recent operations</p>
            <p className="text-xs mt-1 text-muted-foreground">
              Your deployment history will appear here
            </p>
          </div>
        )}
      </SectionCard>

      {/* 8. BLUEPRINTS — K8S-UX-009: demoted to bottom, more compact */}
      {dashboardBlueprints.length > 0 && (
        <SectionCard>
          <SectionHeader icon={Layers} title="Blueprints" count={blueprints?.length || 0} viewAllHref="/stacks" viewAllLabel="View All Blueprints" />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {dashboardBlueprints.map((blueprint) => {
              const catKey = (blueprint.category || 'infrastructure').toLowerCase();
              const CatIcon = blueprintCategoryIcons[catKey] || Layers;
              return (
                <div
                  key={blueprint.slug}
                  className="group relative p-3 rounded-lg border border-border hover:border-primary/30 bg-muted/30 hover:shadow-sm transition-all cursor-pointer"
                  onClick={() => {
                    setSelectedBlueprintSlug(blueprint.slug);
                    setBlueprintDialogOpen(true);
                  }}
                >
                  <div className="flex items-center gap-2.5">
                    <div className="p-1.5 rounded-lg bg-primary/10">
                      <CatIcon className="h-4 w-4 text-primary" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <h4 className="font-medium text-sm truncate text-foreground group-hover:text-primary transition-colors">
                        {blueprint.name}
                      </h4>
                      <p className="text-xs truncate text-muted-foreground">
                        {blueprint.description}
                      </p>
                    </div>
                    <Badge variant="muted" className="text-[10px] flex-shrink-0">
                      {blueprint.cloud_provider?.toUpperCase() || 'Any'}
                    </Badge>
                  </div>
                </div>
              );
            })}
          </div>
        </SectionCard>
      )}

      {/* Dialogs */}
      <StackDetailDialog
        slug={selectedBlueprintSlug}
        open={blueprintDialogOpen}
        onOpenChange={setBlueprintDialogOpen}
        onSuccess={(projectId) => navigate(`/projects/${projectId}`)}
      />
      <AddClusterFlowDialog open={showAddCluster} onOpenChange={setShowAddCluster} />
    </div>
  );
}
