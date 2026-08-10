/**
 * DPU Cluster Detail View
 *
 * Shows DPUCluster CRD resources with:
 *   - Control plane type (Kamaji / Static)
 *   - Cluster endpoint (VIP, interface)
 *   - K8s version, max nodes, node count
 *   - Kubeconfig secret reference
 *   - Full condition list with lifecycle progress
 *
 * Data comes from the shared DPF unified data cache.
 */

import { useState } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  HelpCircle,
  ChevronDown,
  ChevronRight,
  Network,
  Key,
  Layers,
} from 'lucide-react';
import type { DpfDpuCluster, DpfK8sCondition } from '@/types';

// ── Props ─────────────────────────────────────────────────────────────────

interface DPUClusterDetailProps {
  clusters: DpfDpuCluster[];
  isLoading?: boolean;
}

// ── Helpers ───────────────────────────────────────────────────────────────

type ConditionStatus = 'True' | 'False' | 'Unknown' | undefined;

function conditionIcon(status: ConditionStatus) {
  switch (status) {
    case 'True':  return CheckCircle2;
    case 'False': return XCircle;
    default:      return HelpCircle;
  }
}

function conditionColor(status: ConditionStatus) {
  switch (status) {
    case 'True':  return 'text-success';
    case 'False': return 'text-destructive';
    default:      return 'text-muted-foreground';
  }
}

function isClusterReady(conditions: DpfK8sCondition[] | undefined): boolean {
  return (conditions ?? []).some(
    (c) => c.type === 'Ready' && c.status === 'True',
  );
}

// ── Detail Field ──────────────────────────────────────────────────────────

function DetailField({
  label,
  value,
  mono,
}: {
  label: string;
  value: string | number | undefined | null;
  mono?: boolean;
}) {
  if (value == null) return null;
  return (
    <div>
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <p className={cn('mt-0.5 text-xs text-foreground/80', mono && 'font-mono')}>
        {String(value)}
      </p>
    </div>
  );
}

// ── Cluster Card ──────────────────────────────────────────────────────────

function ClusterCard({ cluster }: { cluster: DpfDpuCluster }) {
  const [expanded, setExpanded] = useState(true); // default expanded since there are few clusters
  const conditions = cluster.status?.conditions ?? [];
  const ready = isClusterReady(conditions);
  const clusterType = cluster.spec?.type ?? 'kamaji';
  const vip = cluster.spec?.clusterEndpoint?.keepalived?.vip;
  const kubeconfigSecret = cluster.spec?.kubeconfig;  // secret name for static clusters

  return (
    <div className="rounded-lg border border-border bg-card">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center w-full gap-3 px-5 py-4 text-left transition-colors hover:bg-muted/50"
      >
        {expanded
          ? <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          : <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        }

        <Layers className="h-5 w-5 shrink-0 text-muted-foreground" />

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-sm text-foreground">
              {cluster.metadata.name}
            </span>
            <Badge variant="outline" className="text-[10px] px-1.5 py-0">
              {clusterType}
            </Badge>
          </div>
          {cluster.metadata.namespace && (
            <span className="text-xs text-muted-foreground">
              {cluster.metadata.namespace}
            </span>
          )}
        </div>

        {/* Ready badge */}
        {ready ? (
          <Badge variant="success" className="gap-1">
            <CheckCircle2 className="h-3 w-3" /> Ready
          </Badge>
        ) : (
          <Badge variant="warning" className="gap-1">
            <AlertTriangle className="h-3 w-3" /> Not Ready
          </Badge>
        )}
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="px-5 pb-5 space-y-4 border-t border-border bg-muted/50">
          {/* Key-value grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-3 pt-4">
            <DetailField label="Type" value={clusterType} />
            <DetailField label="K8s Version" value={cluster.status?.version} mono />
            <DetailField label="Max Nodes" value={cluster.spec?.maxNodes} />
            <DetailField label="Current Nodes" value={cluster.status?.nodesCount} />
            <DetailField label="Phase" value={cluster.status?.phase} />
            <DetailField label="Created" value={cluster.metadata.creationTimestamp} />
          </div>

          {/* Endpoint info */}
          {vip && (
            <div className="flex items-center gap-2 rounded-lg px-4 py-3 text-xs bg-card">
              <Network className="h-4 w-4 shrink-0 text-muted-foreground" />
              <div>
                <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  Control Plane VIP
                </span>
                <p className="font-mono text-foreground/80">
                  {vip}
                  {cluster.spec?.clusterEndpoint?.keepalived?.interface && (
                    <span className="text-muted-foreground">
                      {' '}({cluster.spec.clusterEndpoint.keepalived.interface})
                    </span>
                  )}
                  {cluster.spec?.clusterEndpoint?.keepalived?.virtualRouterID != null && (
                    <span className="text-muted-foreground">
                      {' '}VRID: {cluster.spec.clusterEndpoint.keepalived.virtualRouterID}
                    </span>
                  )}
                </p>
              </div>
            </div>
          )}

          {/* Kubeconfig secret (static clusters) */}
          {kubeconfigSecret && (
            <div className="flex items-center gap-2 rounded-lg px-4 py-3 text-xs bg-card">
              <Key className="h-4 w-4 shrink-0 text-muted-foreground" />
              <div>
                <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  Kubeconfig Secret
                </span>
                <p className="font-mono text-foreground/80">
                  {kubeconfigSecret}
                </p>
              </div>
            </div>
          )}

          {/* Conditions */}
          {conditions.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold mb-2 text-foreground/80">
                Conditions
              </h4>
              <div className="space-y-1">
                {conditions.map((cond) => {
                  const Icon = conditionIcon(cond.status as ConditionStatus);
                  return (
                    <div
                      key={cond.type}
                      className="flex items-center gap-2 rounded px-3 py-1.5 text-xs bg-card"
                    >
                      <Icon className={cn('h-3.5 w-3.5 shrink-0', conditionColor(cond.status as ConditionStatus))} />
                      <span className="font-medium min-w-[160px] text-foreground/80">
                        {cond.type}
                      </span>
                      <span className={cond.status === 'True' ? 'text-success' : 'text-muted-foreground'}>
                        {cond.status}
                      </span>
                      {cond.reason && (
                        <span className="text-muted-foreground">({cond.reason})</span>
                      )}
                      {cond.message && (
                        <span className="truncate flex-1 text-muted-foreground">
                          {cond.message}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Note: Etcd configuration is managed by Kamaji controller, not exposed in DPUCluster spec */}
        </div>
      )}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────

export function DPUClusterDetail({ clusters, isLoading }: DPUClusterDetailProps) {
  if (isLoading) {
    return (
      <div className="space-y-4">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="h-32 rounded-lg animate-pulse bg-muted/50" />
        ))}
      </div>
    );
  }

  if (clusters.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-lg border border-border bg-muted/50 p-10 text-center">
        <Layers className="h-10 w-10 mb-3 text-muted-foreground" />
        <h3 className="text-base font-semibold mb-1 text-foreground/80">
          No DPU Clusters
        </h3>
        <p className="text-sm text-muted-foreground">
          No DPUCluster resources found. Create a DPU cluster to provision a control plane for DPU nodes.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Summary */}
      <div className="flex items-center gap-4">
        <Badge variant="outline" className="text-xs">
          {clusters.length} cluster{clusters.length !== 1 ? 's' : ''}
        </Badge>
        <Badge variant="success" className="text-xs">
          {clusters.filter((c) => isClusterReady(c.status?.conditions)).length} ready
        </Badge>
      </div>

      {/* Cluster cards */}
      {clusters.map((cluster) => (
        <ClusterCard
          key={cluster.metadata.uid ?? cluster.metadata.name}
          cluster={cluster}
        />
      ))}
    </div>
  );
}
