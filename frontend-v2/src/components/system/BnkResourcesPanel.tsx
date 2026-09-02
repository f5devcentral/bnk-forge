/**
 * BNK Resources panel for the System page.
 *
 * Shows fleet-wide BNK consumption: overview tiles, per-cluster table,
 * control-plane vs data-plane breakdown, and top consumers.
 */

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
import { formatBytes } from '@/lib/utils';
import type { BnkConsumptionResponse, BnkPlaneConsumption } from '@/types/system';

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
  const topPods = clusters.flatMap((c) =>
    c.top_pods.map((p) => ({ ...p, cluster_name: c.cluster_name }))
  );
  topPods.sort((a, b) => b.cpu_millicores - a.cpu_millicores);
  const topFive = topPods.slice(0, 5);

  return (
    <div className="space-y-6" data-testid="bnk-resources-panel">
      {/* Overview tiles */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <OverviewTile
          icon={Server}
          label="Clusters"
          value={fleet_summary.total_clusters}
          subtext={`${fleet_summary.reachable_clusters} reachable · ${fleet_summary.bnk_installed_clusters} BNK installed`}
        />
        <OverviewTile
          icon={Box}
          label="BNK Pods"
          value={fleet_summary.total_bnk_pods}
          subtext={`${fleet_summary.control_plane_pods} control-plane · ${fleet_summary.data_plane_pods} data-plane`}
        />
        <OverviewTile
          icon={Cpu}
          label="CPU"
          value={
            fleet_summary.total_cpu_millicores > 0
              ? formatCPU(fleet_summary.total_cpu_millicores)
              : formatCPU(fleet_summary.node_capacity_cpu_millicores)
          }
          subtext={
            fleet_summary.total_cpu_millicores > 0
              ? "Across all BNK pods"
              : "Node capacity (metrics-server unavailable)"
          }
        />
        <OverviewTile
          icon={Database}
          label="Memory"
          value={
            fleet_summary.total_memory_bytes > 0
              ? formatMemory(fleet_summary.total_memory_bytes)
              : formatMemory(fleet_summary.node_capacity_memory_bytes)
          }
          subtext={
            fleet_summary.total_memory_bytes > 0
              ? "Across all BNK pods"
              : "Node capacity (metrics-server unavailable)"
          }
        />
      </div>

      {fleet_summary.dpf_detected_clusters > 0 && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Gauge className="h-4 w-4" />
          <span>
            DPF detected on {fleet_summary.dpf_detected_clusters} cluster
            {fleet_summary.dpf_detected_clusters > 1 ? 's' : ''} · {fleet_summary.dpu_count} DPU
            {fleet_summary.dpu_count > 1 ? 's' : ''}
          </span>
        </div>
      )}

      {/* Cluster consumption table */}
      <SectionCard title="Cluster Consumption">
        {clusters.length === 0 ? (
          <p className="text-sm text-muted-foreground">No clusters registered.</p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Cluster</TableHead>
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
                {clusters.map((cluster) => (
                  <TableRow key={cluster.cluster_id}>
                    <TableCell className="font-medium">
                      {cluster.cluster_name}
                      {cluster.bnk_version && (
                        <span className="ml-2 text-xs text-muted-foreground">v{cluster.bnk_version}</span>
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
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        {!fleet_summary.total_bnk_pods && clusters.length > 0 && (
          <p className="text-sm text-muted-foreground mt-4">
            No BNK workloads detected. Install BNK on a cluster to see resource usage.
          </p>
        )}
        {clusters.some((c) => !c.metrics_available && c.node_capacity.cpu_millicores > 0) && (
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
                count: fleet_summary.control_plane_pods,
                cpu_millicores: clusters.reduce((sum, c) => sum + c.control_plane.cpu_millicores, 0),
                memory_bytes: clusters.reduce((sum, c) => sum + c.control_plane.memory_bytes, 0),
              }}
            />
            <PlaneBreakdown
              label="Data Plane"
              plane={{
                count: fleet_summary.data_plane_pods,
                cpu_millicores: clusters.reduce((sum, c) => sum + c.data_plane.cpu_millicores, 0),
                memory_bytes: clusters.reduce((sum, c) => sum + c.data_plane.memory_bytes, 0),
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
