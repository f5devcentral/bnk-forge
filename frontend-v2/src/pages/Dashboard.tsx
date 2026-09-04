/**
 * Dashboard — K8s-first Multi-Cloud Command Center & Hero Omnisearch
 *
 * Updated: Replaced static health scoring ring, recent operations, and blueprint catalog
 * with Hero Omnisearch (multi-cloud FQDN / ingress / host / cluster / project search)
 * and an interactive Multi-Cloud Estate View grouped by cloud provider (AWS, Azure, GKE, Bare Metal, IBM).
 */

import { useState, useMemo } from 'react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { ErrorState } from '@/components/ui/error-state';
import { SectionCard } from '@/components/ui/section-card';
import { PageHeader } from '@/components/layout/PageHeader';
import { usePageRefresh } from '@/hooks/usePageRefresh';
import { useRecentDeployments } from '@/hooks/useDeployments';
import { useProjects } from '@/hooks/useProjects';
import { useGlobalDriftSummary, useRecentDrifted, useProjectDriftCounts, useGlobalDriftCount } from '@/hooks/useDrift';
import { useTasks } from '@/hooks/useTasks';
import { useAllClusters } from '@/hooks/useK8s';
import { useFleetHealth, useFleetTargets, useFleetRollups } from '@/hooks/useFleet';
import { useConnectivity } from '@/hooks/useConnectivity';
import { reachabilityKey } from '@/lib/api/connectivity';
import { cn } from '@/lib/utils';
import { formatTimeAgo } from '@/lib/time-utils';
import { DISPLAY_LIMITS } from '@/lib/constants';
import { AddClusterFlowDialog } from '@/components/k8s/AddClusterFlowDialog';
import {
  ActiveOperationCard,
  AttentionCard,
  SectionHeader,
  ValueJourneyBanner,
  HeroOmniSearch,
  MultiCloudEstate,
} from '@/components/dashboard';
import type { AttentionItem as AttentionItemType } from '@/components/dashboard';
import {
  EstateSummaryBar,
  FleetTrafficLights,
  healthStateFromRollup,
  policyStateFromRollup,
} from '@/components/fleet/FleetTrafficLights';
import {
  AlertTriangle,
  ArrowRight,
  ArrowUpRight,
  CheckCircle2,
  Clock,
  Flag,
  GitCompare,
  Rocket,
  Server,
  WifiOff,
  Zap,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type { FleetOperatorHealth, FleetRollup } from '@/types/fleet';

// ============================================================================
// Helpers
// ============================================================================

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 17) return 'Good afternoon';
  return 'Good evening';
}

// ============================================================================
// Main Dashboard Component
// ============================================================================

export default function Dashboard() {
  const navigate = useNavigate();
  const [showAddCluster, setShowAddCluster] = useState(false);

  const { refresh, isRefreshing } = usePageRefresh();

  // --- Data fetching ---
  const { data: recentDeployments } = useRecentDeployments(10);
  const { data: projects, isLoading: projectsLoading, isError: projectsError, error: projectsErrorData, refetch: refetchProjects } = useProjects();
  useGlobalDriftSummary();
  const { data: recentDrifted } = useRecentDrifted(6);
  const driftCount = useGlobalDriftCount();
  const projectDriftCounts = useProjectDriftCounts(20);
  const { data: tasksData } = useTasks({ limit: 10 });
  const { data: clustersData, isLoading: clustersLoading } = useAllClusters();
  const { states: connectivityStates } = useConnectivity();
  const { data: fleetHealth } = useFleetHealth();
  const { data: fleetTargets, isLoading: fleetsLoading } = useFleetTargets();
  const fleetTargetIds = useMemo(() => fleetTargets?.map((t) => t.id) ?? [], [fleetTargets]);
  const { data: fleetRollupList } = useFleetRollups(fleetTargetIds);
  const fleetRollupById = useMemo((): Map<number, FleetRollup> => {
    if (!fleetRollupList) return new Map();
    return new Map(fleetRollupList.map((r) => [r.fleet_id, r]));
  }, [fleetRollupList]);

  const projectCount = projects?.length || 0;
  const clusterCount = clustersData?.clusters?.length || 0;

  // Fleet health derived data
  const fleetTotal = fleetHealth?.total_clusters || 0;
  const fleetCritical = fleetHealth?.critical || 0;
  const fleetOperators = useMemo(() => fleetHealth?.operators || [], [fleetHealth?.operators]);

  const fleetStaleHealthy = useMemo(() => {
    if (!fleetHealth?.operators) return 0;
    return fleetHealth.operators.filter((op) => {
      if (op.status !== 'healthy') return false;
      const conn = connectivityStates[reachabilityKey('cluster', op.cluster_id)];
      return conn?.state === 'unreachable';
    }).length;
  }, [fleetHealth?.operators, connectivityStates]);
  const fleetHealthy = Math.max(0, (fleetHealth?.healthy || 0) - fleetStaleHealthy);

  const fleetByCluster = useMemo(() => {
    const map: Record<string, FleetOperatorHealth> = {};
    fleetOperators.forEach((op) => {
      map[op.cluster_name] = op;
    });
    return map;
  }, [fleetOperators]);

  const unhealthyClusters = useMemo(() => {
    return fleetOperators.filter(op => op.status === 'critical' || op.status === 'warning');
  }, [fleetOperators]);

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

  const totalAttentionCount = attentionItems.length
    + (recentDrifted?.length || 0)
    + unhealthyClusters.length
    + offlineOperators.length;

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

      {/* 1b. VALUE JOURNEY */}
      <ValueJourneyBanner />

      {/* 2. HERO OMNISEARCH — Instant FQDN, Ingress, VIP, Cluster, and Project jumping */}
      <HeroOmniSearch
        projects={projects || []}
        clusters={clustersData?.clusters || []}
      />

      {/* 3. FLEETS OVERVIEW — Fleet entity model conformance */}
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
              {fleetRollupList && fleetRollupList.length > 0 && (
                <EstateSummaryBar rollups={fleetRollupList} />
              )}

              {(() => {
                const attention = (fleetTargets ?? []).filter((t) => {
                  const r = fleetRollupById.get(t.id);
                  if (!r) return false;
                  const hs = healthStateFromRollup(r);
                  const ps = policyStateFromRollup(r);
                  return hs === 'red' || hs === 'amber' || ps === 'red' || r.ops_state === 'red';
                }).sort((a, b) => {
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
                  </div>
                );
              })()}
            </div>
          )}
        </SectionCard>
      )}

      {/* 4. ACTIVE OPERATIONS */}
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

      {/* 5. ATTENTION NEEDED */}
      {totalAttentionCount > 0 && (
        <SectionCard className="border-warning/30">
          <div className="flex items-center gap-2 mb-4">
            <AlertTriangle className="h-4 w-4 text-warning" />
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Attention Needed</p>
            <Badge variant="warning" className="text-xs">
              {totalAttentionCount}
            </Badge>
          </div>
          <div className="space-y-2">
            {/* Unhealthy clusters */}
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

            {/* Offline operators */}
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
<<<<<<< HEAD
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
                  const providerBadge = getCloudProviderBadgeInfo(project.cloud_provider || project.project_type);
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
                            <Badge variant={providerBadge.badgeVariant} className={providerBadge.badgeClass}>
                              {providerBadge.shortLabel}
                            </Badge>
                          </div>
                          <span className="text-xs text-muted-foreground inline-flex items-center gap-1 font-mono">
                            {locationInfo ? (
                              <>
                                <span>{locationInfo.flag}</span>
                                <span>{locationInfo.display}</span>
                              </>
                            ) : (
                              <span className="font-sans">📦 No location</span>
                            )}
                            {project.target_platform_profile && project.target_platform_profile !== 'unknown' && (
                              <span className="font-sans"> · {project.target_platform_profile.toUpperCase()}</span>
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
=======
                        {driftItem.last_check_at && (
                          <span className="text-xs flex items-center gap-1 text-muted-foreground">
                            <Clock className="h-3 w-3" />
                            {formatTimeAgo(driftItem.last_check_at)}
>>>>>>> abb84b2 (feat(ux): streamline multi-cloud estate, global omni-search, and bnk topology views)
                          </span>
                        )}
                      </div>
<<<<<<< HEAD
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
                  const clusterLocation = getClusterLocationInfo(cluster.cloud_provider, cluster.region);
                  const clusterBadge = getCloudProviderBadgeInfo(cluster.cloud_provider);
                  return (
                    <Link key={cluster.id} to="/kubernetes" className="block group">
                      <div className="flex items-center gap-3 p-3 rounded-lg transition-colors hover:bg-muted/50">
                        <div className={cn('h-2 w-2 rounded-full flex-shrink-0', statusDot)} />
                        <div className="flex-1 min-w-0">
                          <span className="font-medium text-sm truncate block text-foreground">{cluster.name}</span>
                          <div className="flex items-center gap-1.5 flex-wrap mt-0.5">
                            <Badge variant={clusterBadge.badgeVariant} className={clusterBadge.badgeClass}>
                              {clusterBadge.shortLabel}
                            </Badge>
                            {clusterLocation ? (
                              <span className="text-xs text-muted-foreground inline-flex items-center gap-1 font-mono">
                                <span>{clusterLocation.flag}</span>
                                <span>{clusterLocation.display}</span>
                              </span>
                            ) : (
                              <span className="text-xs text-muted-foreground">
                                {cluster.detected_platform_profile?.toUpperCase() || 'On-Prem'}
                              </span>
                            )}
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
=======
>>>>>>> abb84b2 (feat(ux): streamline multi-cloud estate, global omni-search, and bnk topology views)
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
                    </div>
                  </div>
                </div>
              );
            })}

            {attentionItems.map((item) => (
              <AttentionCard key={item.id} item={item} />
            ))}
          </div>
        </SectionCard>
      )}

      {/* 6. MULTI-CLOUD ESTATE VIEW — Grouped clusters & OpenTofu projects */}
      <MultiCloudEstate
        projects={projects || []}
        clusters={clustersData?.clusters || []}
        projectsLoading={projectsLoading}
        clustersLoading={clustersLoading}
        fleetByCluster={fleetByCluster}
        connectivityStates={connectivityStates}
        projectDriftCounts={projectDriftCounts}
        onAddCluster={() => setShowAddCluster(true)}
      />

      {/* Add Cluster Dialog */}
      <AddClusterFlowDialog open={showAddCluster} onOpenChange={setShowAddCluster} />
    </div>
  );
}
