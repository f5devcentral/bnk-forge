/**
 * MigrationPanel — standalone per-cluster proxy/CIS migration surface.
 *
 * Extracted from Fleet › MigrationView (D-022 P6 Slice A) and promoted to the
 * K8s page as its own view-mode tab ("Migration"). The shared cards live in the
 * same migration/ module; this component owns only the scan trigger wrapper.
 *
 * #203 reskin: SectionCard, space-y-6, semantic tokens, no isDark/useTheme.
 */

import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { SectionCard } from '@/components/ui/section-card';
import { EmptyState } from '@/components/ui/empty-state';
import { RefreshCw, ArrowRightLeft } from 'lucide-react';
import { useClusterScan } from '@/hooks/useK8s';
import { queryKeys } from '@/lib/queryKeys';
import { ExistingProxiesCard } from './ExistingProxiesCard';
import { CisMigrationCard } from './CisMigrationCard';

interface MigrationPanelProps {
  clusterId: number;
  clusterName?: string;
}

export function MigrationPanel({ clusterId }: MigrationPanelProps) {
  const scanMutation = useClusterScan();
  const queryClient = useQueryClient();

  // Trigger scan when the cluster changes
  useEffect(() => {
    if (clusterId) scanMutation.mutate(clusterId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clusterId]);

  // Prefer cached data over mutation data so the view stays populated on tab switch
  const scanData =
    scanMutation.data ??
    (clusterId
      ? queryClient.getQueryData<typeof scanMutation.data>(
          queryKeys.k8s.clusters.scan(clusterId)
        )
      : undefined);

  const proxies = scanData?.prerequisites?.existing_proxies?.proxies ?? [];
  const discoveredCount = scanData?.prerequisites?.existing_proxies?.discovered_count ?? 0;
  const cis = scanData?.prerequisites?.cis;
  const hasAnything = discoveredCount > 0 || (cis && cis.status !== 'missing');

  return (
    <div className="space-y-6">
      {/* Scan-pending indicator */}
      {scanMutation.isPending && (
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <RefreshCw className="h-3 w-3 animate-spin" />
          Scanning…
        </div>
      )}

      {/* Content */}
      {!scanData && !scanMutation.isPending ? (
        <SectionCard>
          <div className="py-4 text-center text-sm text-muted-foreground">
            Scan is loading — please wait.
          </div>
        </SectionCard>
      ) : !scanData && scanMutation.isPending ? (
        <SectionCard>
          <div className="py-4 text-center text-sm text-muted-foreground flex items-center justify-center gap-2">
            <RefreshCw className="h-4 w-4 animate-spin" />
            Scanning cluster for migration candidates…
          </div>
        </SectionCard>
      ) : !hasAnything ? (
        <EmptyState
          icon={ArrowRightLeft}
          title="Nothing to migrate"
          description="No non-BNK proxies or CIS controllers detected on this cluster. Run a fresh scan on the cluster's Scan tab if you expect migration candidates."
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {discoveredCount > 0 && (
            <ExistingProxiesCard proxies={proxies} clusterId={clusterId} />
          )}
          {cis && cis.status !== 'missing' && (
            <CisMigrationCard cis={cis} clusterId={clusterId} />
          )}
        </div>
      )}
    </div>
  );
}
