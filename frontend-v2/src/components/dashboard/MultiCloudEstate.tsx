import { useState, useMemo } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Server,
  FolderGit2,
  ArrowRight,
  ArrowUpRight,
  GitCompare,
  Box,
  Layers,
  LayoutGrid,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty-state';
import { ClusterStatusBadge } from '@/components/ui/ClusterStatusBadge';
import { reachabilityKey } from '@/lib/api/connectivity';
import { cn } from '@/lib/utils';
import {
  getCloudProviderBadgeInfo,
  getClusterLocationInfo,
} from '@/lib/aws-regions';
import type { Project, K8sCluster } from '@/types';
import type { FleetOperatorHealth } from '@/types/fleet';

export interface EnvironmentNode {
  id: string;
  name: string;
  cloud_provider?: string | null;
  region?: string | null;
  cluster?: K8sCluster;
  project?: Project;
}

interface MultiCloudEstateProps {
  projects?: Project[];
  clusters?: K8sCluster[];
  projectsLoading?: boolean;
  clustersLoading?: boolean;
  fleetByCluster?: Record<string, FleetOperatorHealth>;
  connectivityStates?: Record<string, any>;
  projectDriftCounts?: Record<number, number>;
  onAddCluster?: () => void;
  className?: string;
}

type ProviderKey = 'all' | 'aws' | 'azure' | 'gke' | 'metal' | 'ibm';
type ViewLayout = 'grouped' | 'flat';

interface ProviderGroup {
  id: string;
  name: string;
  shortLabel: string;
  badgeClass: string;
  badgeVariant: 'default' | 'secondary' | 'outline' | 'destructive';
  environments: EnvironmentNode[];
}

function normalizeProvider(provider?: string | null): string {
  const p = (provider || '').toLowerCase().trim();
  if (p === 'aws' || p === 'eks') return 'aws';
  if (p === 'azure' || p === 'aks') return 'azure';
  if (p === 'gcp' || p === 'gke' || p === 'google') return 'gke';
  if (p === 'bare-metal' || p === 'on-prem' || p === 'metal' || p === 'kubernetes') return 'metal';
  if (p === 'ibm' || p === 'roks' || p === 'ibmcloud') return 'ibm';
  return 'other';
}

function cleanNameForMatching(name: string): string {
  return name
    .toLowerCase()
    .replace(/^(aws|azr|gke|ibm|metal|gcp|k8s)bnkctl-/, '')
    .replace(/^(aws|azr|gke|ibm|metal|gcp|k8s)-/, '')
    .trim();
}

export function MultiCloudEstate({
  projects = [],
  clusters = [],
  projectsLoading = false,
  clustersLoading = false,
  fleetByCluster = {},
  connectivityStates = {},
  projectDriftCounts = {},
  onAddCluster,
  className,
}: MultiCloudEstateProps) {
  const navigate = useNavigate();
  const [selectedProvider, setSelectedProvider] = useState<ProviderKey>('all');
  const [viewLayout, setViewLayout] = useState<ViewLayout>('grouped');
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});

  const toggleGroup = (groupId: string) => {
    setCollapsedGroups((prev) => ({
      ...prev,
      [groupId]: !prev[groupId],
    }));
  };

  // Build unified environment nodes by pairing clusters with their managing OpenTofu projects
  const environments = useMemo((): EnvironmentNode[] => {
    const pairedProjectIds = new Set<number>();
    const envNodes: EnvironmentNode[] = [];

    // 1. Process clusters and find matching project
    for (const cluster of clusters) {
      const cNormProv = normalizeProvider(cluster.cloud_provider);
      const cCleanName = cleanNameForMatching(cluster.name);

      // Match project by:
      // a) Name contains or matches clean name
      // b) Same provider + same region
      const matchedProject = projects.find((p) => {
        if (pairedProjectIds.has(p.id)) return false;
        const pCleanName = cleanNameForMatching(p.name);
        const pNormProv = normalizeProvider(p.cloud_provider || p.project_type);

        if (pNormProv !== cNormProv && pNormProv !== 'other' && cNormProv !== 'other') {
          return false;
        }

        if (
          pCleanName === cCleanName ||
          p.name.toLowerCase().includes(cluster.name.toLowerCase()) ||
          cluster.name.toLowerCase().includes(pCleanName)
        ) {
          return true;
        }

        if (
          pNormProv === cNormProv &&
          p.region &&
          cluster.region &&
          p.region.toLowerCase() === cluster.region.toLowerCase()
        ) {
          return true;
        }

        return false;
      });

      if (matchedProject) {
        pairedProjectIds.add(matchedProject.id);
      }

      envNodes.push({
        id: `env-${cluster.id}-${matchedProject?.id || 'none'}`,
        name: cluster.name,
        cloud_provider: cluster.cloud_provider || matchedProject?.cloud_provider,
        region: cluster.region || matchedProject?.region,
        cluster,
        project: matchedProject,
      });
    }

    // 2. Add remaining unpaired projects as standalone environment nodes
    for (const project of projects) {
      if (!pairedProjectIds.has(project.id)) {
        envNodes.push({
          id: `env-proj-${project.id}`,
          name: project.name,
          cloud_provider: project.cloud_provider || project.project_type,
          region: project.region,
          project,
        });
      }
    }

    return envNodes;
  }, [clusters, projects]);

  // Group environment nodes by provider
  const groups = useMemo((): ProviderGroup[] => {
    const providerDefs: Array<{
      id: string;
      name: string;
      shortLabel: string;
      matchKey: string;
      badgeClass: string;
      badgeVariant: 'default' | 'secondary' | 'outline' | 'destructive';
    }> = [
      {
        id: 'aws',
        name: 'Amazon Web Services (EKS)',
        shortLabel: 'AWS',
        matchKey: 'aws',
        badgeClass: 'border-amber-500/40 text-amber-500 bg-amber-500/10 font-semibold text-[10px] px-1.5 py-0.5',
        badgeVariant: 'outline',
      },
      {
        id: 'azure',
        name: 'Microsoft Azure (AKS)',
        shortLabel: 'AZR',
        matchKey: 'azure',
        badgeClass: 'border-sky-500/40 text-sky-500 bg-sky-500/10 font-semibold text-[10px] px-1.5 py-0.5',
        badgeVariant: 'outline',
      },
      {
        id: 'gke',
        name: 'Google Cloud Platform (GKE)',
        shortLabel: 'GKE',
        matchKey: 'gke',
        badgeClass: 'border-blue-500/40 text-blue-500 bg-blue-500/10 font-semibold text-[10px] px-1.5 py-0.5',
        badgeVariant: 'outline',
      },
      {
        id: 'metal',
        name: 'Bare-Metal & On-Premises',
        shortLabel: 'METAL',
        matchKey: 'metal',
        badgeClass: 'border-emerald-500/40 text-emerald-500 bg-emerald-500/10 font-semibold text-[10px] px-1.5 py-0.5',
        badgeVariant: 'outline',
      },
      {
        id: 'ibm',
        name: 'IBM Cloud (ROKS)',
        shortLabel: 'IBM',
        matchKey: 'ibm',
        badgeClass: 'border-indigo-500/40 text-indigo-500 bg-indigo-500/10 font-semibold text-[10px] px-1.5 py-0.5',
        badgeVariant: 'outline',
      },
      {
        id: 'other',
        name: 'Other Kubernetes Infrastructure',
        shortLabel: 'K8S',
        matchKey: 'other',
        badgeClass: 'font-semibold text-[10px] px-1.5 py-0.5',
        badgeVariant: 'secondary',
      },
    ];

    const result: ProviderGroup[] = [];

    for (const def of providerDefs) {
      const groupEnvs = environments.filter(
        (e) => normalizeProvider(e.cloud_provider) === def.matchKey
      );

      if (groupEnvs.length > 0 || def.id === 'aws' || def.id === 'azure' || def.id === 'gke') {
        result.push({
          id: def.id,
          name: def.name,
          shortLabel: def.shortLabel,
          badgeClass: def.badgeClass,
          badgeVariant: def.badgeVariant,
          environments: groupEnvs,
        });
      }
    }

    return result;
  }, [environments]);

  const filteredGroups = useMemo(() => {
    if (selectedProvider === 'all') return groups;
    return groups.filter((g) => g.id === selectedProvider);
  }, [groups, selectedProvider]);

  const filteredEnvironments = useMemo(() => {
    if (selectedProvider === 'all') return environments;
    return environments.filter((e) => normalizeProvider(e.cloud_provider) === selectedProvider);
  }, [environments, selectedProvider]);

  const totalClusters = clusters.length;
  const totalProjects = projects.length;
  const totalEnvironments = environments.length;

  function renderEnvironmentCard(env: EnvironmentNode) {
    const cluster = env.cluster;
    const project = env.project;
    const fleetInfo = cluster ? fleetByCluster[cluster.name] : undefined;
    const conn = cluster ? connectivityStates[reachabilityKey('cluster', cluster.id)] : undefined;

    const isReachUnreachable = conn?.state === 'unreachable';
    const isReachChecking = conn?.state === 'checking';
    const isReachable = conn?.state === 'reachable' || (cluster && cluster.status === 'active' && !isReachUnreachable);

    const statusDot = isReachUnreachable
      ? 'bg-destructive'
      : isReachChecking
        ? 'bg-warning animate-pulse'
        : isReachable || (project && project.deployed_count > 0)
          ? 'bg-success'
          : 'bg-muted-foreground';

    const locationInfo = getClusterLocationInfo(
      env.cloud_provider || cluster?.cloud_provider || project?.cloud_provider,
      env.region || cluster?.region || project?.region
    );

    const providerBadge = getCloudProviderBadgeInfo(env.cloud_provider || cluster?.cloud_provider || project?.cloud_provider);

    const hasBnkAlerts = fleetInfo && fleetInfo.status !== 'healthy' && fleetInfo.status !== 'offline';
    const bnkIssueText = fleetInfo?.health_issues?.[0]?.message || (fleetInfo?.status === 'critical' ? 'Data plane alerts' : undefined);

    return (
      <div
        key={env.id}
        className="group p-4 rounded-xl border border-border/70 bg-card hover:border-primary/50 hover:shadow-sm transition-all h-full flex flex-col justify-between"
      >
        <div>
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-center gap-2 min-w-0">
              <div className={cn('h-2.5 w-2.5 rounded-full shrink-0', statusDot)} />
              <h4 className="font-semibold text-sm text-foreground truncate group-hover:text-primary transition-colors">
                {env.name}
              </h4>
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <Badge className={providerBadge.badgeClass} variant={providerBadge.badgeVariant}>
                {providerBadge.shortLabel}
              </Badge>
              {cluster && <ClusterStatusBadge cluster={cluster} />}
            </div>
          </div>

          <div className="flex items-center gap-1.5 flex-wrap mt-2">
            {locationInfo && (
              <span className="text-xs text-muted-foreground inline-flex items-center gap-1 font-mono">
                <span>{locationInfo.flag}</span>
                <span>{locationInfo.display}</span>
              </span>
            )}
            {cluster?.version && (
              <span className="text-xs text-muted-foreground font-mono">
                v{cluster.version}
              </span>
            )}
            {cluster?.detected_platform_profile && (
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-medium">
                {cluster.detected_platform_profile}
              </span>
            )}
          </div>

          {cluster && (
            <div className="mt-3 pt-2.5 border-t border-border/40 space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground flex items-center gap-1">
                  <Server className="w-3.5 h-3.5 text-primary" />
                  <span className="font-mono text-[11px] truncate max-w-[130px]">
                    {cluster.status === 'active' ? 'Connected' : (cluster.status || 'Active')}
                  </span>
                </span>
                <div className="flex items-center gap-1">
                  {fleetInfo?.bnk_version && (
                    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-info/10 text-info font-medium">
                      BNK {fleetInfo.bnk_version}
                    </span>
                  )}
                  {fleetInfo && fleetInfo.tmm_count > 0 && (
                    <span className="text-[10px] text-muted-foreground font-mono">
                      {fleetInfo.tmm_count} TMM
                    </span>
                  )}
                </div>
              </div>

              {hasBnkAlerts && (
                <div
                  className="flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-md bg-warning/10 text-warning border border-warning/20 font-medium"
                  title={fleetInfo.health_issues?.map((i) => i.message).join('; ') || 'BNK data plane alerts detected'}
                >
                  <span className="shrink-0">⚠️</span>
                  <span className="truncate">
                    {bnkIssueText || 'Data plane alerts'}
                  </span>
                </div>
              )}
            </div>
          )}

          {project && (
            <div className="mt-2.5 pt-2 border-t border-border/40">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground flex items-center gap-1 min-w-0">
                  <FolderGit2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                  <span className="font-mono text-[11px] truncate max-w-[140px]" title={project.name}>
                    {project.name}
                  </span>
                </span>
                <div className="flex items-center gap-1 shrink-0">
                  <span className="font-mono text-[11px] text-muted-foreground">
                    {project.deployed_count || 0}/{project.module_count || 0} deployed
                  </span>
                  {projectDriftCounts[project.id] > 0 && (
                    <span
                      className="px-1.5 py-0.5 text-[10px] font-semibold rounded-full flex items-center gap-0.5 bg-warning/10 text-warning"
                      title={`${projectDriftCounts[project.id]} drifted module${projectDriftCounts[project.id] > 1 ? 's' : ''}`}
                    >
                      <GitCompare className="h-2.5 w-2.5" />
                      {projectDriftCounts[project.id]}
                    </span>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between text-xs text-muted-foreground mt-3 pt-2.5 border-t border-border/50">
          {cluster ? (
            <Link
              to={`/kubernetes?cluster=${cluster.id}&view=advanced`}
              className="flex items-center text-primary font-medium hover:underline gap-1"
            >
              <span>Explore K8s</span>
              <ArrowRight className="w-3 h-3 transition-transform group-hover:translate-x-0.5" />
            </Link>
          ) : (
            <span />
          )}

          {project && (
            <Link
              to={`/projects/${project.id}`}
              className="flex items-center text-muted-foreground hover:text-foreground font-medium hover:underline gap-1 text-[11px]"
            >
              <span>IaC Project</span>
              <ArrowUpRight className="w-3 h-3" />
            </Link>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={cn('space-y-6', className)}>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border/60 pb-4">
        <div>
          <div className="flex items-center gap-2">
            <Layers className="w-5 h-5 text-primary" />
            <h2 className="text-lg font-bold tracking-tight text-foreground">Multi-Cloud Estate</h2>
            <Badge variant="secondary" className="font-mono text-xs">
              {totalEnvironments} Environment{totalEnvironments !== 1 ? 's' : ''} · {totalClusters} Cluster{totalClusters !== 1 ? 's' : ''} · {totalProjects} Project{totalProjects !== 1 ? 's' : ''}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">
            Unified multi-cloud clusters, live telemetry, and OpenTofu module projects.
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex items-center bg-muted/60 p-1 rounded-lg text-xs">
            <button
              type="button"
              onClick={() => setSelectedProvider('all')}
              className={cn(
                'px-2.5 py-1 rounded-md font-medium transition-colors cursor-pointer text-xs',
                selectedProvider === 'all'
                  ? 'bg-card text-foreground shadow-xs font-semibold'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              All ({totalEnvironments})
            </button>
            <button
              type="button"
              onClick={() => setSelectedProvider('aws')}
              className={cn(
                'px-2.5 py-1 rounded-md font-medium transition-colors cursor-pointer text-xs',
                selectedProvider === 'aws'
                  ? 'bg-card text-foreground shadow-xs font-semibold'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              AWS
            </button>
            <button
              type="button"
              onClick={() => setSelectedProvider('azure')}
              className={cn(
                'px-2.5 py-1 rounded-md font-medium transition-colors cursor-pointer text-xs',
                selectedProvider === 'azure'
                  ? 'bg-card text-foreground shadow-xs font-semibold'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              Azure
            </button>
            <button
              type="button"
              onClick={() => setSelectedProvider('gke')}
              className={cn(
                'px-2.5 py-1 rounded-md font-medium transition-colors cursor-pointer text-xs',
                selectedProvider === 'gke'
                  ? 'bg-card text-foreground shadow-xs font-semibold'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              GKE
            </button>
            <button
              type="button"
              onClick={() => setSelectedProvider('metal')}
              className={cn(
                'px-2.5 py-1 rounded-md font-medium transition-colors cursor-pointer text-xs',
                selectedProvider === 'metal'
                  ? 'bg-card text-foreground shadow-xs font-semibold'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              Metal
            </button>
          </div>

          <div className="flex items-center bg-muted/60 p-1 rounded-lg text-xs border border-border/50">
            <button
              type="button"
              onClick={() => setViewLayout('grouped')}
              title="Grouped by Cloud Provider"
              className={cn(
                'p-1.5 rounded-md transition-colors cursor-pointer',
                viewLayout === 'grouped'
                  ? 'bg-card text-primary shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              <Layers className="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              onClick={() => setViewLayout('flat')}
              title="Flat Grid View"
              className={cn(
                'p-1.5 rounded-md transition-colors cursor-pointer',
                viewLayout === 'flat'
                  ? 'bg-card text-primary shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              <LayoutGrid className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>

      {(projectsLoading || clustersLoading) && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div key={i} className="p-4 rounded-xl border border-border bg-card space-y-3">
              <div className="flex items-center justify-between">
                <Skeleton className="h-5 w-32" />
                <Skeleton className="h-4 w-12" />
              </div>
              <Skeleton className="h-4 w-24" />
              <div className="pt-2 border-t border-border/40 flex justify-between">
                <Skeleton className="h-3 w-16" />
                <Skeleton className="h-3 w-16" />
              </div>
            </div>
          ))}
        </div>
      )}

      {!projectsLoading && !clustersLoading && totalEnvironments === 0 && (
        <EmptyState
          icon={Box}
          title="No multi-cloud infrastructure detected"
          description="Connect an existing Kubernetes cluster or launch a new OpenTofu project blueprint to populate your estate."
          action={{
            label: 'Add Cluster',
            onClick: () => (onAddCluster ? onAddCluster() : navigate('/kubernetes')),
          }}
          secondaryAction={{
            label: 'Create Project',
            onClick: () => navigate('/projects/new'),
          }}
          illustration={false}
        />
      )}

      {!projectsLoading && !clustersLoading && totalEnvironments > 0 && viewLayout === 'grouped' && (
        <div className="space-y-6">
          {filteredGroups.map((group) => {
            const isCollapsed = collapsedGroups[group.id] ?? false;
            const groupEnvCount = group.environments.length;

            return (
              <div
                key={group.id}
                className="rounded-xl border border-border/80 bg-card/40 overflow-hidden shadow-xs transition-all"
              >
                <div
                  onClick={() => toggleGroup(group.id)}
                  className="flex items-center justify-between px-4 py-3 bg-muted/40 hover:bg-muted/60 cursor-pointer border-b border-border/60 select-none transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      className="text-muted-foreground hover:text-foreground transition-colors p-0.5"
                    >
                      {isCollapsed ? (
                        <ChevronRight className="w-4 h-4" />
                      ) : (
                        <ChevronDown className="w-4 h-4" />
                      )}
                    </button>
                    <Badge variant={group.badgeVariant} className={group.badgeClass}>
                      {group.shortLabel}
                    </Badge>
                    <div>
                      <h3 className="font-semibold text-sm text-foreground">{group.name}</h3>
                      <p className="text-xs text-muted-foreground font-mono">
                        {groupEnvCount} Environment{groupEnvCount !== 1 ? 's' : ''}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">
                      {isCollapsed ? 'Expand' : 'Collapse'}
                    </span>
                  </div>
                </div>

                {!isCollapsed && (
                  <div className="p-4">
                    {groupEnvCount === 0 ? (
                      <div className="p-4 rounded-lg border border-dashed border-border/60 text-center text-xs text-muted-foreground">
                        No {group.shortLabel} environments connected yet.{' '}
                        <button
                          type="button"
                          onClick={() => (onAddCluster ? onAddCluster() : navigate('/kubernetes'))}
                          className="text-primary font-medium hover:underline cursor-pointer"
                        >
                          Add a cluster
                        </button>
                      </div>
                    ) : (
                      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                        {group.environments.map((env) => renderEnvironmentCard(env))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* View Mode 2: Flat Grid View */}
      {!projectsLoading && !clustersLoading && totalEnvironments > 0 && viewLayout === 'flat' && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {filteredEnvironments.map((env) => renderEnvironmentCard(env))}
        </div>
      )}
    </div>
  );
}

