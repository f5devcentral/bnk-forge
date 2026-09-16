import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Network, Server, Settings, Globe } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function GatewaySettingsDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];

  const ingressConfig = spec.ingressConfig;
  const sourceNATPools: any[] = spec.sourceNATPools || [];
  const egressConfigs: any[] = spec.egressConfigs || [];

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          {/* Ingress Configuration */}
          {ingressConfig && (
            <Section title="Ingress Network Configuration">
              <InfoRow label="SNAT Mode" value={ingressConfig.snatMode || ingressConfig.defaultListenerNetwork?.snatMode} mono />
              {ingressConfig.defaultListenerNetwork?.networkRefs && (
                <div className="space-y-1 pt-1">
                  <span className="text-muted-foreground block text-[11px]">Network References:</span>
                  <div className="flex flex-wrap gap-1.5 pl-2">
                    {ingressConfig.defaultListenerNetwork.networkRefs.map((net: any, idx: number) => (
                      <Badge key={idx} variant="outline" className="text-[10px] font-mono flex items-center gap-1">
                        <Network className="h-2.5 w-2.5 text-info" />
                        {typeof net === 'string' ? net : net.name}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
              {ingressConfig.defaultListenerNetwork?.ipamRefs && (
                <div className="space-y-1 pt-1">
                  <span className="text-muted-foreground block text-[11px]">IPAM References:</span>
                  <div className="flex flex-wrap gap-1.5 pl-2">
                    {ingressConfig.defaultListenerNetwork.ipamRefs.map((ipam: any, idx: number) => (
                      <Badge key={idx} variant="secondary" className="text-[10px] font-mono flex items-center gap-1">
                        <Globe className="h-2.5 w-2.5 text-success" />
                        {typeof ipam === 'string' ? ipam : ipam.name}
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
              {sourceNATPools.map((pool: any, idx: number) => (
                <div key={idx} className="p-2 rounded border bg-background/50 space-y-1">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Server className="h-3 w-3 text-info" />
                      <code className="font-mono font-medium text-foreground/80">{pool.name}</code>
                    </div>
                    {pool.mode && (
                      <Badge variant="outline" className="text-[10px] font-mono">
                        {pool.mode}
                      </Badge>
                    )}
                  </div>
                  {pool.addresses && (
                    <div className="text-[11px] text-muted-foreground pl-5">
                      Addresses: <code className="font-mono">{Array.isArray(pool.addresses) ? pool.addresses.join(', ') : pool.addresses}</code>
                    </div>
                  )}
                </div>
              ))}
            </Section>
          )}

          {/* Egress Configs */}
          {egressConfigs.length > 0 && (
            <Section title={`Egress Configurations (${egressConfigs.length})`}>
              {egressConfigs.map((eg: any, idx: number) => (
                <div key={idx} className="p-2 rounded border bg-background/50 space-y-1">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Settings className="h-3 w-3 text-muted-foreground" />
                      <code className="font-mono font-medium text-foreground/80">{eg.name}</code>
                    </div>
                    {eg.snatMode && (
                      <Badge variant="secondary" className="text-[10px] font-mono">
                        {eg.snatMode}
                      </Badge>
                    )}
                  </div>
                  {eg.networkRefs && (
                    <div className="text-[11px] text-muted-foreground pl-5 flex gap-2 items-center">
                      <span>Networks:</span>
                      {eg.networkRefs.map((nr: any, nIdx: number) => (
                        <code key={nIdx} className="font-mono">{typeof nr === 'string' ? nr : nr.name}</code>
                      ))}
                    </div>
                  )}
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
