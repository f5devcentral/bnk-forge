/**
 * BNK Resources panel for the System page.
 *
 * Shows fleet-wide BNK consumption: overview tiles, per-cluster table,
 * control-plane vs data-plane breakdown, and top consumers.
 */

import { useMemo, useState } from 'react';
import { Box, Cpu, Database, Gauge, Server } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { SectionCard } from '@/components/ui/section-card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Skeleton } from '@/components/ui/skeleton';
import { cn, formatBytes } from '@/lib/utils';
import { normalizeProvider } from '@/lib/cloud-providers';
import { getCloudProviderBadgeInfo, getClusterLocationInfo } from '@/lib/aws-regions';
import type { BnkConsumptionResponse, BnkPlaneConsumption } from '@/types/system';

type ProviderKey = 'all' | 'aws' | 'azure' | 'gke' | 'metal';

interface BnkResourcesPanelProps {
  data: BnkConsumptionResponse | undefined;
  isLoading: boolean;
  error: Error | null;
}

function formatCPU(millicores: number): string {
  if (millicores < 1000) {
    return `${millicores}m`;
  }
  return `${(millicores / 1000).toFixed(2)} cores`;
}

function formatMemory(bytes: number): string {
  return formatBytes(bytes, 1);
}

function PlaneBreakdown({
  label,
  plane,
}: {
  label: string;
  plane: BnkPlaneConsumption;
}) {
  return (
    <div className="flex items-center justify-between p-3 rounded-lg border">
      <div className="flex items-center gap-3">
        <div className="p-2 rounded-md bg-primary/10 text-primary">
          <Box className="h-4 w-4" />
        </div>
        <div>
          <p className="text-sm font-medium">{label}</p>
          <p className="text-xs text-muted-foreground">{plane.count} pods</p>
        </div>
      </div>
      <div className="text-right">
        <p className="text-sm font-medium tabular-nums">{formatCPU(plane.cpu_millicores)}</p>
        <p className="text-xs text-muted-foreground tabular-nums">{formatMemory(plane.memory_bytes)}</p>
      </div>
    </div>
  );
}

function OverviewTile({
  icon: Icon,
  label,
  value,
  subtext,
}: {
  icon: React.ElementType;
  label: string;
  value: React.ReactNode;
  subtext?: string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold tabular-nums">{value}</div>
        {subtext && <p className="text-xs text-muted-foreground mt-1">{subtext}</p>}
      </CardContent>
    </Card>
  );
}

export function BnkResourcesPanel({ data, isLoading, error }: BnkResourcesPanelProps) {
  const [selectedProvider, setSelectedProvider] = useState<ProviderKey>('all');

  const clusters = data?.clusters ?? [];
  const fleetSummary = data?.fleet_summary;

  const filteredClusters = useMemo(() => {
    if (selectedProvider === 'all') return clusters;
    return clusters.filter((c) => normalizeProvider(c.cloud_provider) === selectedProvider);
  }, [clusters, selectedProvider]);

  const summary = useMemo(() => {
    if (!fleetSummary) return null;
    if (selectedProvider === 'all') return fleetSummary;
    return {
      total_clusters: filteredClusters.length,
      reachable_clusters: filteredClusters.filter((c) => c.reachable).length,
      bnk_installed_clusters: filteredClusters.filter((c) => c.bnk_installed).length,
      total_bnk_pods: filteredClusters.reduce((sum, c) => sum + c.total.count, 0),
      control_plane_pods: filteredClusters.reduce((sum, c) => sum + c.control_plane.count, 0),
      data_plane_pods: filteredClusters.reduce((sum, c) => sum + c.data_plane.count, 0),
      total_cpu_millicores: filteredClusters.reduce((sum, c) => sum + c.total.cpu_millicores, 0),
      total_memory_bytes: filteredClusters.reduce((sum, c) => sum + c.total.memory_bytes, 0),
      node_capacity_cpu_millicores: filteredClusters.reduce(
        (sum, c) => sum + c.node_capacity.cpu_millicores,
        0
      ),
      node_capacity_memory_bytes: filteredClusters.reduce(
        (sum, c) => sum + c.node_capacity.memory_bytes,
        0
      ),
      dpf_detected_clusters: filteredClusters.filter((c) => c.dpf?.detected).length,
      dpu_count: filteredClusters.reduce((sum, c) => sum + (c.dpf?.dpu_count || 0), 0),
    };
  }, [filteredClusters, fleetSummary, selectedProvider]);

  const topFive = useMemo(() => {
    const topPods = filteredClusters.flatMap((c) =>
      c.top_pods.map((p) => ({ ...p, cluster_name: c.cluster_name }))
    );
    topPods.sort((a, b) => b.cpu_millicores - a.cpu_millicores);
    return topPods.slice(0, 5);
  }, [filteredClusters]);

  const controlPlaneCpu = useMemo(
    () => filteredClusters.reduce((sum, c) => sum + c.control_plane.cpu_millicores, 0),
    [filteredClusters]
  );
  const controlPlaneMemory = useMemo(
    () => filteredClusters.reduce((sum, c) => sum + c.control_plane.memory_bytes, 0),
    [filteredClusters]
  );
  const dataPlaneCpu = useMemo(
    () => filteredClusters.reduce((sum, c) => sum + c.data_plane.cpu_millicores, 0),
    [filteredClusters]
  );
  const dataPlaneMemory = useMemo(
    () => filteredClusters.reduce((sum, c) => sum + c.data_plane.memory_bytes, 0),
    [filteredClusters]
  );

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (error) {
    return (
      <SectionCard title="BNK Resources">
        <p className="text-sm text-destructive">
          Failed to load BNK consumption: {error.message}
        </p>
      </SectionCard>
    );
  }

  if (!data || !summary) {
    return null;
  }

  return (
    <div className="space-y-6" data-testid="bnk-resources-panel">
      {/* Cloud Provider Filter */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div
          className="flex items-center bg-muted/60 p-1 rounded-lg text-xs"
          role="tablist"
          aria-label="Filter by cloud provider"
        >
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
            All ({clusters.length})
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
      </div>

      {/* Overview tiles */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <OverviewTile
          icon={Server}
          label="Clusters"
          value={summary.total_clusters}
          subtext={`${summary.reachable_clusters} reachable · ${summary.bnk_installed_clusters} BNK installed`}
        />
        <OverviewTile
          icon={Box}
          label="BNK Pods"
          value={summary.total_bnk_pods}
          subtext={`${summary.control_plane_pods} control-plane · ${summary.data_plane_pods} data-plane`}
        />
        <OverviewTile
          icon={Cpu}
          label="CPU"
          value={
            summary.total_cpu_millicores > 0
              ? formatCPU(summary.total_cpu_millicores)
              : formatCPU(summary.node_capacity_cpu_millicores)
          }
          subtext={
            summary.total_cpu_millicores > 0
              ? "Across all BNK pods"
              : "Node capacity (metrics-server unavailable)"
          }
        />
        <OverviewTile
          icon={Database}
          label="Memory"
          value={
            summary.total_memory_bytes > 0
              ? formatMemory(summary.total_memory_bytes)
              : formatMemory(summary.node_capacity_memory_bytes)
          }
          subtext={
            summary.total_memory_bytes > 0
              ? "Across all BNK pods"
              : "Node capacity (metrics-server unavailable)"
          }
        />
      </div>

      {summary.dpf_detected_clusters > 0 && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Gauge className="h-4 w-4" />
          <span>
            DPF detected on {summary.dpf_detected_clusters} cluster
            {summary.dpf_detected_clusters > 1 ? 's' : ''} · {summary.dpu_count} DPU
            {summary.dpu_count > 1 ? 's' : ''}
          </span>
        </div>
      )}

      {/* Cluster consumption table */}
      <SectionCard title="Cluster Consumption">
        {filteredClusters.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {clusters.length === 0
              ? 'No clusters registered.'
              : `No clusters found for provider "${selectedProvider.toUpperCase()}".`}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Cluster</TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead>Region</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Nodes</TableHead>
                  <TableHead className="text-right">BNK Pods</TableHead>
                  <TableHead className="text-right">Control Plane</TableHead>
                  <TableHead className="text-right">Data Plane</TableHead>
                  <TableHead className="text-right">CPU</TableHead>
                  <TableHead className="text-right">Memory</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredClusters.map((cluster) => {
                  const badgeInfo = getCloudProviderBadgeInfo(cluster.cloud_provider);
                  const locationInfo = getClusterLocationInfo(cluster.cloud_provider, cluster.region);

                  return (
                    <TableRow key={cluster.cluster_id}>
                      <TableCell className="font-medium">
                        <div className="flex items-center gap-2">
                          <span>{cluster.cluster_name}</span>
                          {cluster.bnk_version && (
                            <span className="text-xs text-muted-foreground">v{cluster.bnk_version}</span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={badgeInfo.badgeVariant} className={badgeInfo.badgeClass}>
                          {badgeInfo.shortLabel}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {locationInfo ? (
                          <span
                            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground font-mono"
                            title={locationInfo.label}
                          >
                            <span>{locationInfo.flag}</span>
                            <span>{locationInfo.display}</span>
                          </span>
                        ) : (
                          <span className="text-muted-foreground text-xs">-</span>
                        )}
                      </TableCell>
                      <TableCell>
                        {cluster.reachable ? (
                          cluster.bnk_installed ? (
                            <Badge variant="outline" className="text-xs">BNK installed</Badge>
                          ) : (
                            <Badge variant="secondary" className="text-xs">No BNK</Badge>
                          )
                        ) : (
                          <Badge variant="destructive" className="text-xs">Offline</Badge>
                        )}
                      </TableCell>
                    <TableCell>{cluster.node_count ?? '-'}</TableCell>
                    <TableCell className="text-right tabular-nums">{cluster.total.count}</TableCell>
                    <TableCell className="text-right tabular-nums">{cluster.control_plane.count}</TableCell>
                    <TableCell className="text-right tabular-nums">{cluster.data_plane.count}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {cluster.metrics_available ? (
                        formatCPU(cluster.total.cpu_millicores)
                      ) : cluster.node_capacity.cpu_millicores > 0 ? (
                        <span title="Node allocatable capacity (metrics-server unavailable)">
                          {formatCPU(cluster.node_capacity.cpu_millicores)}
                          <span className="text-muted-foreground">*</span>
                        </span>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {cluster.metrics_available ? (
                        formatMemory(cluster.total.memory_bytes)
                      ) : cluster.node_capacity.memory_bytes > 0 ? (
                        <span title="Node allocatable capacity (metrics-server unavailable)">
                          {formatMemory(cluster.node_capacity.memory_bytes)}
                          <span className="text-muted-foreground">*</span>
                        </span>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                  </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
        {!summary.total_bnk_pods && filteredClusters.length > 0 && (
          <p className="text-sm text-muted-foreground mt-4">
            No BNK workloads detected. Install BNK on a cluster to see resource usage.
          </p>
        )}
        {filteredClusters.some((c) => !c.metrics_available && c.node_capacity.cpu_millicores > 0) && (
          <p className="text-xs text-muted-foreground mt-4">
            * CPU/Memory values marked with * are node allocatable capacity, not live BNK pod usage.
            Install metrics-server in each cluster to see actual BNK pod consumption.
          </p>
        )}
      </SectionCard>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Plane breakdown */}
        <SectionCard title="Plane Breakdown">
          <div className="space-y-3">
            <PlaneBreakdown
              label="Control Plane"
              plane={{
                count: summary.control_plane_pods,
                cpu_millicores: controlPlaneCpu,
                memory_bytes: controlPlaneMemory,
              }}
            />
            <PlaneBreakdown
              label="Data Plane"
              plane={{
                count: summary.data_plane_pods,
                cpu_millicores: dataPlaneCpu,
                memory_bytes: dataPlaneMemory,
              }}
            />
          </div>
        </SectionCard>

        {/* Top consumers */}
        <SectionCard title="Top Consumers">
          {topFive.length === 0 ? (
            <p className="text-sm text-muted-foreground">No BNK pod metrics available.</p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Pod</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead className="text-right">CPU</TableHead>
                    <TableHead className="text-right">Memory</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {topFive.map((pod) => (
                    <TableRow key={`${pod.cluster_name}/${pod.namespace}/${pod.name}`}>
                      <TableCell>
                        <div className="font-medium text-sm">{pod.name}</div>
                        <div className="text-xs text-muted-foreground">{pod.cluster_name}</div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs capitalize">
                          {pod.role}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{formatCPU(pod.cpu_millicores)}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatMemory(pod.memory_bytes)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  );
}
