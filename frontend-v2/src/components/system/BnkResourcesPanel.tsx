/**
 * BNK Resources panel for the System page.
 *
 * Shows fleet-wide BNK consumption: overview tiles, per-cluster table,
 * control-plane vs data-plane breakdown, and top consumers.
 */

import { useState, useMemo } from 'react';
import { Box, Cpu, Database, Gauge, Server, Layers } from 'lucide-react';
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
import { formatBytes, cn } from '@/lib/utils';
import { getCloudProviderBadgeInfo, getClusterLocationInfo } from '@/lib/aws-regions';
import type { BnkConsumptionResponse, BnkPlaneConsumption } from '@/types/system';

export type ProviderScope = 'all' | 'aws' | 'azure' | 'gke' | 'ibm' | 'metal';

function normalizeProvider(provider?: string | null): string {
  const p = (provider || '').toLowerCase().trim();
  if (p === 'aws' || p === 'eks') return 'aws';
  if (p === 'azure' || p === 'aks') return 'azure';
  if (p === 'gcp' || p === 'gke' || p === 'google') return 'gke';
  if (p === 'bare-metal' || p === 'on-prem' || p === 'metal' || p === 'kubernetes') return 'metal';
  if (p === 'ibm' || p === 'roks' || p === 'ibmcloud') return 'ibm';
  return 'other';
}

interface BnkResourcesPanelProps {
  data: BnkConsumptionResponse | undefined;
  isLoading: boolean;
  error: Error | null;
  provider?: ProviderScope;
  onProviderChange?: (provider: ProviderScope) => void;
  hideProviderChips?: boolean;
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

export function BnkResourcesPanel({
  data,
  isLoading,
  error,
  provider,
  onProviderChange,
  hideProviderChips,
}: BnkResourcesPanelProps) {
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

  if (!data) {
    return null;
  }

  const { fleet_summary, clusters } = data;
  const [internalProvider, setInternalProvider] = useState<ProviderScope>('all');
  const selectedProvider = provider !== undefined ? provider : internalProvider;
  const setSelectedProvider = onProviderChange || setInternalProvider;

  const filteredClusters = useMemo(() => {
    if (selectedProvider === 'all') return clusters;
    return clusters.filter((c) => normalizeProvider(c.cloud_provider) === selectedProvider);
  }, [clusters, selectedProvider]);

  const summary = useMemo(() => {
    if (selectedProvider === 'all') return fleet_summary;
    const total_clusters = filteredClusters.length;
    const reachable_clusters = filteredClusters.filter((c) => c.reachable).length;
    const bnk_installed_clusters = filteredClusters.filter((c) => c.bnk_installed).length;
    const total_bnk_pods = filteredClusters.reduce((sum, c) => sum + c.total.count, 0);
    const control_plane_pods = filteredClusters.reduce((sum, c) => sum + c.control_plane.count, 0);
    const data_plane_pods = filteredClusters.reduce((sum, c) => sum + c.data_plane.count, 0);
    const total_cpu_millicores = filteredClusters.reduce(
      (sum, c) => sum + (c.metrics_available ? c.total.cpu_millicores : 0),
      0
    );
    const node_capacity_cpu_millicores = filteredClusters.reduce(
      (sum, c) => sum + c.node_capacity.cpu_millicores,
      0
    );
    const total_memory_bytes = filteredClusters.reduce(
      (sum, c) => sum + (c.metrics_available ? c.total.memory_bytes : 0),
      0
    );
    const node_capacity_memory_bytes = filteredClusters.reduce(
      (sum, c) => sum + c.node_capacity.memory_bytes,
      0
    );
    const dpf_detected_clusters = filteredClusters.filter((c) => c.dpf?.detected).length;
    const dpu_count = filteredClusters.reduce((sum, c) => sum + (c.dpf?.dpu_count || 0), 0);

    return {
      total_clusters,
      reachable_clusters,
      bnk_installed_clusters,
      total_bnk_pods,
      control_plane_pods,
      data_plane_pods,
      total_cpu_millicores,
      node_capacity_cpu_millicores,
      total_memory_bytes,
      node_capacity_memory_bytes,
      dpf_detected_clusters,
      dpu_count,
    };
  }, [filteredClusters, selectedProvider, fleet_summary]);

  const topPods = filteredClusters.flatMap((c) =>
    c.top_pods.map((p) => ({ ...p, cluster_name: c.cluster_name }))
  );
  topPods.sort((a, b) => b.cpu_millicores - a.cpu_millicores);
  const topFive = topPods.slice(0, 5);

  return (
    <div className="space-y-6" data-testid="bnk-resources-panel">
      {/* Header with Provider Filter Chips */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border/60 pb-3">
        <div>
          <h2 className="text-base font-semibold text-foreground flex items-center gap-2">
            <Layers className="h-4 w-4 text-primary" />
            BNK Infrastructure & Consumption
          </h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            Fleet-wide telemetry across Control Plane and Data Plane (TMM / CNF).
          </p>
        </div>
        {!hideProviderChips && (
          <div className="flex items-center bg-muted/60 p-1 rounded-lg text-xs self-start sm:self-auto">
            {[
              { id: 'all', label: `ALL (${clusters.length})` },
              { id: 'aws', label: 'AWS' },
              { id: 'azure', label: 'AZR' },
              { id: 'gke', label: 'GKE' },
              { id: 'ibm', label: 'IBM' },
              { id: 'metal', label: 'METAL' },
            ].map((scope) => (
              <button
                key={scope.id}
                type="button"
                onClick={() => setSelectedProvider(scope.id as ProviderScope)}
                className={cn(
                  'px-2.5 py-1 rounded-md font-medium transition-colors cursor-pointer text-xs',
                  selectedProvider === scope.id
                    ? 'bg-card text-foreground shadow-xs font-semibold'
                    : 'text-muted-foreground hover:text-foreground'
                )}
              >
                {scope.label}
              </button>
            ))}
          </div>
        )}
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
              ? 'Across filtered BNK pods'
              : 'Node capacity (metrics-server unavailable)'
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
              ? 'Across filtered BNK pods'
              : 'Node capacity (metrics-server unavailable)'
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
      <SectionCard title={selectedProvider === 'all' ? 'Cluster Consumption' : `Cluster Consumption (${selectedProvider.toUpperCase()})`}>
        {filteredClusters.length === 0 ? (
          <p className="text-sm text-muted-foreground py-4 text-center">No clusters found for this provider filter.</p>
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
                cpu_millicores: filteredClusters.reduce((sum, c) => sum + c.control_plane.cpu_millicores, 0),
                memory_bytes: filteredClusters.reduce((sum, c) => sum + c.control_plane.memory_bytes, 0),
              }}
            />
            <PlaneBreakdown
              label="Data Plane"
              plane={{
                count: summary.data_plane_pods,
                cpu_millicores: filteredClusters.reduce((sum, c) => sum + c.data_plane.cpu_millicores, 0),
                memory_bytes: filteredClusters.reduce((sum, c) => sum + c.data_plane.memory_bytes, 0),
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
