import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Network, Globe, Route, Layers } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';
import type { BnkInfraIpam, BnkInfraNetwork, BnkInfraStaticRoute, BnkInfraVrf } from '@/types/kubernetes';

export function InfraDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];
  const networks: BnkInfraNetwork[] = spec.networks || [];
  const ipams: BnkInfraIpam[] = spec.ipams || [];
  const staticRoutes: BnkInfraStaticRoute[] = spec.staticRoutes || [];
  const vrfs: BnkInfraVrf[] = spec.vrfs || [];
  const egressDefaults = spec.egressDefaults;

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          {/* Networks */}
          {networks.length > 0 && (
            <Section title={`Networks (${networks.length})`}>
              {networks.map((net, idx) => (
                <div key={idx} className="p-2 rounded border bg-background/50 space-y-1">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Network className="h-3 w-3 text-info" />
                      <code className="font-mono font-medium text-foreground/80">{net.name}</code>
                    </div>
                    {net.type && (
                      <Badge variant="outline" className="text-[10px] uppercase font-mono">
                        {net.type}
                      </Badge>
                    )}
                  </div>
                  {net.vlan && (
                    <div className="text-[11px] text-muted-foreground flex gap-3 pl-5">
                      {net.vlan.tag !== undefined && <span>Tag: <code className="font-mono">{net.vlan.tag}</code></span>}
                      {net.vlan.mtu && <span>MTU: <code className="font-mono">{net.vlan.mtu}</code></span>}
                      {net.vlan.networkAttachmentRef?.name && (
                        <span>NAD: <code className="font-mono">{net.vlan.networkAttachmentRef.name}</code></span>
                      )}
                    </div>
                  )}
                  {net.vxlan && (
                    <div className="text-[11px] text-muted-foreground flex gap-3 pl-5">
                      {net.vxlan.port && <span>Port: <code className="font-mono">{net.vxlan.port}</code></span>}
                      {net.vxlan.vni !== undefined && <span>VNI: <code className="font-mono">{net.vxlan.vni}</code></span>}
                    </div>
                  )}
                </div>
              ))}
            </Section>
          )}

          {/* IPAM Pools */}
          {ipams.length > 0 && (
            <Section title={`IPAM Pools (${ipams.length})`}>
              {ipams.map((ipam, idx) => (
                <div key={idx} className="p-2 rounded border bg-background/50 space-y-1">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Globe className="h-3 w-3 text-success" />
                      <code className="font-mono font-medium text-foreground/80">{ipam.name}</code>
                    </div>
                    {ipam.az && (
                      <Badge variant="secondary" className="text-[10px] font-mono">
                        AZ: {ipam.az}
                      </Badge>
                    )}
                  </div>
                  <div className="text-[11px] text-muted-foreground flex flex-wrap gap-3 pl-5">
                    {ipam.cidr && <span>CIDR: <code className="font-mono">{ipam.cidr}</code></span>}
                    {ipam.rangeStart && ipam.rangeEnd && (
                      <span>Range: <code className="font-mono">{ipam.rangeStart} - {ipam.rangeEnd}</code></span>
                    )}
                    {ipam.networkRef?.name && (
                      <span>Network: <code className="font-mono">{ipam.networkRef.name}</code></span>
                    )}
                  </div>
                </div>
              ))}
            </Section>
          )}

          {/* Static Routes */}
          {staticRoutes.length > 0 && (
            <Section title={`Static Routes (${staticRoutes.length})`}>
              {staticRoutes.map((rt, idx) => (
                <div key={idx} className="flex items-center justify-between p-1.5 rounded border bg-background/50">
                  <div className="flex items-center gap-2">
                    <Route className="h-3 w-3 text-muted-foreground" />
                    <code className="font-mono text-foreground/80">{rt.destination}</code>
                  </div>
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <span>via</span>
                    <code className="font-mono text-foreground/80">{rt.gateway}</code>
                    {rt.vrf && <Badge variant="outline" className="text-[10px]">VRF: {rt.vrf}</Badge>}
                  </div>
                </div>
              ))}
            </Section>
          )}

          {/* VRFs & Egress Defaults */}
          {(vrfs.length > 0 || egressDefaults) && (
            <Section title="Routing & Egress">
              {vrfs.length > 0 && (
                <div className="flex items-center gap-2">
                  <Layers className="h-3 w-3 text-muted-foreground" />
                  <span className="text-muted-foreground">VRFs:</span>
                  <div className="flex flex-wrap gap-1">
                    {vrfs.map((vrf, idx) => (
                      <Badge key={idx} variant="outline" className="text-[10px] font-mono">
                        {typeof vrf === 'string' ? vrf : vrf.name}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
              {egressDefaults && (
                <InfoRow label="Egress SNAT Mode" value={egressDefaults.snatMode || egressDefaults.mode} mono />
              )}
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
