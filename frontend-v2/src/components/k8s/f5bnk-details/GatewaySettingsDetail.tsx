import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Network, Server, Settings, Globe } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';
import type { BnkEgressConfig, BnkListenerNetwork, BnkSourceNatPool } from '@/types/kubernetes';

export function GatewaySettingsDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];

  const listenerNetwork: BnkListenerNetwork | undefined = spec.ingressConfig?.defaultListenerNetwork;
  const sourceNATPools: BnkSourceNatPool[] = spec.sourceNATPools || [];
  const egressConfigs: BnkEgressConfig[] = spec.egressConfigs || [];

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          {/* Ingress Configuration */}
          {listenerNetwork && (
            <Section title="Ingress Network Configuration">
              <InfoRow label="SNAT Type" value={listenerNetwork.sourceNATConfig?.type} mono />
              <InfoRow label="SNAT Pool" value={listenerNetwork.sourceNATConfig?.sourceNATPoolRef?.name} mono />
              {listenerNetwork.networkRefs && (
                <div className="space-y-1 pt-1">
                  <span className="text-muted-foreground block text-[11px]">Network References:</span>
                  <div className="flex flex-wrap gap-1.5 pl-2">
                    {listenerNetwork.networkRefs.map((net, idx) => (
                      <Badge key={idx} variant="outline" className="text-[10px] font-mono flex items-center gap-1">
                        <Network className="h-2.5 w-2.5 text-info" />
                        {net.name}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
              {listenerNetwork.ipamRefs && (
                <div className="space-y-1 pt-1">
                  <span className="text-muted-foreground block text-[11px]">IPAM References:</span>
                  <div className="flex flex-wrap gap-1.5 pl-2">
                    {listenerNetwork.ipamRefs.map((ipam, idx) => (
                      <Badge key={idx} variant="secondary" className="text-[10px] font-mono flex items-center gap-1">
                        <Globe className="h-2.5 w-2.5 text-success" />
                        {ipam.name}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </Section>
          )}

          {/* Source NAT Pools */}
          {sourceNATPools.length > 0 && (
            <Section title={`Source NAT Pools (${sourceNATPools.length})`}>
              {sourceNATPools.map((pool, idx) => (
                <div key={idx} className="p-2 rounded border bg-background/50 space-y-1">
                  <div className="flex items-center gap-2">
                    <Server className="h-3 w-3 text-info" />
                    <code className="font-mono font-medium text-foreground/80">{pool.name}</code>
                  </div>
                  {pool.ipamRefs && pool.ipamRefs.length > 0 && (
                    <div className="text-[11px] text-muted-foreground pl-5">
                      IPAM: <code className="font-mono">{pool.ipamRefs.map((r) => r.name).join(', ')}</code>
                    </div>
                  )}
                </div>
              ))}
            </Section>
          )}

          {/* Egress Configs */}
          {egressConfigs.length > 0 && (
            <Section title={`Egress Configurations (${egressConfigs.length})`}>
              {egressConfigs.map((eg, idx) => (
                <div key={idx} className="p-2 rounded border bg-background/50 space-y-1">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Settings className="h-3 w-3 text-muted-foreground" />
                      <code className="font-mono font-medium text-foreground/80">{eg.name}</code>
                    </div>
                    {eg.sourceNATConfig?.type && (
                      <Badge variant="secondary" className="text-[10px] font-mono">
                        {eg.sourceNATConfig.type}
                      </Badge>
                    )}
                  </div>
                  <div className="text-[11px] text-muted-foreground pl-5 flex flex-wrap gap-3">
                    {eg.networkRef?.name && (
                      <span>Network: <code className="font-mono">{eg.networkRef.name}</code></span>
                    )}
                    {eg.sourceNATConfig?.sourceNATPoolRef?.name && (
                      <span>SNAT Pool: <code className="font-mono">{eg.sourceNATConfig.sourceNATPoolRef.name}</code></span>
                    )}
                  </div>
                </div>
              ))}
            </Section>
          )}

          <Section title="Metadata">
            <InfoRow label="Namespace" value={resource.metadata?.namespace} mono />
            <InfoRow label="Age" value={formatAge(resource.metadata?.creationTimestamp)} />
          </Section>
        </TabsContent>

        <TabsContent value="status">
          <ConditionsTab conditions={conditions} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
