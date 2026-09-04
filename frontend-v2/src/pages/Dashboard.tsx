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
import { Link, useNavigate } from 'react-router-dom';
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
                        {driftItem.last_check_at && (
                          <span className="text-xs flex items-center gap-1 text-muted-foreground">
                            <Clock className="h-3 w-3" />
                            {formatTimeAgo(driftItem.last_check_at)}
                          </span>
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
