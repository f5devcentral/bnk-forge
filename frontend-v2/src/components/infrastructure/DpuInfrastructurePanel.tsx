/**
 * DpuInfrastructurePanel — shared DPU hardware view (D-022 P6 Infrastructure IA).
 *
 * Extracted from Fleet.tsx DpuInfrastructureView to live under the new top-level
 * Infrastructure section. Renders a cluster selector + DPFInfrastructurePanel.
 */

import { useState, lazy, Suspense } from 'react';
import { Skeleton } from '@/components/ui/skeleton';
import { SectionCard } from '@/components/ui/section-card';
import { EmptyState } from '@/components/ui/empty-state';
import { CircuitBoard } from 'lucide-react';
import { useAllClusters } from '@/hooks/useK8s';

const DPFInfrastructurePanel = lazy(() =>
  import('@/components/k8s/DPFInfrastructurePanel').then((m) => ({ default: m.DPFInfrastructurePanel })),
);

interface DpuInfrastructurePanelProps {
  /** Pre-select this cluster when the panel first mounts (e.g. from a deep-link). */
  initialClusterId?: number;
}

export function DpuInfrastructurePanel({ initialClusterId }: DpuInfrastructurePanelProps) {
  const { data: clustersData, isLoading } = useAllClusters();
  const clusters = clustersData?.clusters ?? [];
  const [selectedClusterId, setSelectedClusterId] = useState<number | null>(initialClusterId ?? null);
  const clusterId = selectedClusterId ?? clusters[0]?.id ?? null;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Cluster
        </label>
        {isLoading ? (
          <Skeleton className="h-8 w-64" />
        ) : clusters.length === 0 ? (
          <span className="text-xs text-muted-foreground">No clusters available</span>
        ) : (
          <select
            value={clusterId ?? ''}
            onChange={(e) => setSelectedClusterId(Number(e.target.value))}
            className="rounded-md border border-border bg-card text-foreground px-3 py-1.5 text-sm min-w-[240px]"
          >
            {clusters.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {clusterId ? (
        <Suspense
          fallback={
            <SectionCard>
              <div className="text-center space-y-2 py-4">
                <Skeleton className="h-6 w-48 mx-auto" />
                <Skeleton className="h-4 w-64 mx-auto" />
              </div>
            </SectionCard>
          }
        >
          <DPFInfrastructurePanel clusterId={clusterId} />
        </Suspense>
      ) : (
        <EmptyState
          icon={CircuitBoard}
          title="No cluster selected"
          description="Select a Kubernetes cluster to view DPU Infrastructure status."
        />
      )}
    </div>
  );
}
