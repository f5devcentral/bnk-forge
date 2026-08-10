/**
 * ExistingProxiesCard — shared migration card for proxy discovery.
 *
 * Extracted from ClusterScanResults.tsx so it can be reused on both the
 * cluster-scan page and the Fleet › Migration tab (D-022 P5 S4).
 */

import { useState } from 'react';
import { Network, Loader2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScanCard } from './ScanCard';
import { StatRow } from './StatRow';
import { ProxyMigrationPreviewDialog } from '@/components/k8s/ProxyMigrationPreviewDialog';
import { useTranslateProxy } from '@/hooks/k8s/useResources';
import type { ClusterScanProxy } from '@/types';

export function ExistingProxiesCard({
  proxies,
  clusterId,
}: {
  proxies: ClusterScanProxy[];
  clusterId: number;
}) {
  const nonBnk = proxies.filter((p) => !p.is_bnk);
  const bnk = proxies.filter((p) => p.is_bnk);
  const overallStatus = nonBnk.length > 0 ? ('partial' as const) : ('detected' as const);

  const translateProxy = useTranslateProxy();
  const [previewOpen, setPreviewOpen] = useState(false);
  const [activeProxy, setActiveProxy] = useState<ClusterScanProxy | null>(null);

  const handlePreview = (proxy: ClusterScanProxy) => {
    setActiveProxy(proxy);
    setPreviewOpen(true);
    const classNameFromDetails =
      (proxy.details as Record<string, string>)['ingress_class_name'] ||
      (proxy.details as Record<string, string>)['gateway_class_name'] ||
      proxy.proxy_type;
    translateProxy.mutate({
      clusterId,
      payload: {
        proxy_type: proxy.proxy_type,
        source_kind: proxy.kind,
        class_name: classNameFromDetails,
        namespace: proxy.namespace ?? undefined,
        gateway_class_name: 'f5-bnk',
      },
    });
  };

  return (
    <>
      <ScanCard
        title="Existing Proxies"
        icon={Network}
        status={overallStatus}
        collapsible
        defaultOpen
      >
        <StatRow label="Total detected" value={proxies.length} />
        {bnk.length > 0 && (
          <StatRow label="F5 BNK" value={bnk.length} status="detected" />
        )}
        {nonBnk.length > 0 && (
          <StatRow label="Non-BNK (migration candidates)" value={nonBnk.length} status="partial" />
        )}
        {proxies.length > 0 && (
          <div className="pt-2 border-t border-border space-y-2">
            {proxies.map((proxy, idx) => (
              <div key={idx} className="rounded-md border border-border bg-muted/30 p-2.5 text-xs">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-medium">{proxy.display_name}</span>
                  <div className="flex items-center gap-1.5">
                    <Badge variant="outline" className="text-[10px] font-mono">
                      {proxy.kind}
                    </Badge>
                    {proxy.is_bnk && (
                      <Badge className="text-[10px] h-4 bg-success/10 text-success border-success/30">
                        BNK
                      </Badge>
                    )}
                    {!proxy.is_bnk && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-5 text-[10px] px-1.5"
                        onClick={() => handlePreview(proxy)}
                        disabled={translateProxy.isPending && activeProxy === proxy}
                      >
                        {translateProxy.isPending && activeProxy === proxy ? (
                          <Loader2 className="h-2.5 w-2.5 animate-spin" />
                        ) : (
                          'Preview BNK migration'
                        )}
                      </Button>
                    )}
                  </div>
                </div>
                <div className="font-mono text-[10px] truncate text-muted-foreground">
                  {proxy.controller}
                </div>
                {proxy.namespace && (
                  <div className="text-[10px] mt-0.5 text-muted-foreground">
                    ns: {proxy.namespace}
                  </div>
                )}
                {proxy.backends.length > 0 && (
                  <div className="mt-1.5 pt-1.5 border-t border-border">
                    <span className="text-[10px] text-muted-foreground">
                      Backends:
                    </span>
                    {proxy.backends.slice(0, 3).map((b, bi) => (
                      <div key={bi} className="font-mono text-[10px] text-foreground/70">
                        {b.service}/{b.namespace}
                      </div>
                    ))}
                    {proxy.backends.length > 3 && (
                      <div className="text-[10px] text-muted-foreground">
                        +{proxy.backends.length - 3} more
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </ScanCard>

      <ProxyMigrationPreviewDialog
        open={previewOpen}
        onOpenChange={(open) => {
          setPreviewOpen(open);
          if (!open) translateProxy.reset();
        }}
        result={translateProxy.data ?? null}
        isLoading={translateProxy.isPending}
        error={translateProxy.isError ? ((translateProxy.error as Error)?.message ?? 'Translation failed') : null}
        proxyDisplayName={activeProxy?.display_name ?? ''}
      />
    </>
  );
}
