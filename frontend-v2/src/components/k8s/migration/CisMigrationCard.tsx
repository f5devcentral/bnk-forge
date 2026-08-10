/**
 * CisMigrationCard — shared migration card for CIS / BIG-IP discovery.
 *
 * Extracted from ClusterScanResults.tsx so it can be reused on both the
 * cluster-scan page and the Fleet › Migration tab (D-022 P5 S4).
 */

import { useState } from 'react';
import { ArrowRight, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScanCard } from './ScanCard';
import { StatRow } from './StatRow';
import { ProxyMigrationPreviewDialog } from '@/components/k8s/ProxyMigrationPreviewDialog';
import { useTranslateCis } from '@/hooks/k8s/useResources';
import type { ClusterScanCis } from '@/types/kubernetes';

export function CisMigrationCard({
  cis,
  clusterId,
}: {
  cis: ClusterScanCis;
  clusterId: number;
}) {
  const translateCis = useTranslateCis();
  const [previewOpen, setPreviewOpen] = useState(false);

  const vsCount = cis.inventory.virtual_servers.length;
  const tsCount = cis.inventory.transport_servers.length;
  const hasInventory = vsCount > 0 || tsCount > 0;

  const handlePreview = () => {
    setPreviewOpen(true);
    translateCis.mutate({
      clusterId,
      payload: {
        source_mode: 'virtualserver',
        vs_names: [],
        ts_names: [],
        gateway_class_name: 'f5-bnk',
      },
    });
  };

  return (
    <>
      <ScanCard
        title="CIS / BIG-IP Migration"
        icon={ArrowRight}
        status={cis.status === 'detected' ? 'partial' : 'missing'}
        collapsible
        defaultOpen
      >
        <StatRow label="VirtualServers" value={vsCount} status={vsCount > 0 ? 'partial' : 'missing'} />
        <StatRow label="TransportServers" value={tsCount} status={tsCount > 0 ? 'partial' : 'missing'} />
        {cis.external_bigip.host && (
          <StatRow
            label="BIG-IP endpoint"
            value={`${cis.external_bigip.host}${cis.external_bigip.port ? `:${cis.external_bigip.port}` : ''}`}
            mono
          />
        )}
        {cis.eol_note && (
          <div className="mt-1 text-[11px] text-warning">
            {cis.eol_note}
          </div>
        )}
        {hasInventory && (
          <div className="pt-2">
            <Button
              variant="outline"
              size="sm"
              className="h-6 text-xs"
              onClick={handlePreview}
              disabled={translateCis.isPending}
            >
              {translateCis.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin mr-1" />
              ) : (
                <ArrowRight className="h-3 w-3 mr-1" />
              )}
              Preview BNK migration
            </Button>
          </div>
        )}
      </ScanCard>

      <ProxyMigrationPreviewDialog
        open={previewOpen}
        onOpenChange={(open) => {
          setPreviewOpen(open);
          if (!open) translateCis.reset();
        }}
        result={translateCis.data ?? null}
        isLoading={translateCis.isPending}
        error={translateCis.isError ? ((translateCis.error as Error)?.message ?? 'Translation failed') : null}
        proxyDisplayName={`CIS BIG-IP (${vsCount} VS, ${tsCount} TS)`}
      />
    </>
  );
}
