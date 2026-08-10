import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Network, Globe } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import type { K8sVlanInterface } from '@/types';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function VlanDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];
  const selfIps = spec.selfip_v4s || spec.selfipV4s || [];
  const interfaces: K8sVlanInterface[] = spec.interfaces || [];

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          <Section title="VLAN Configuration">
            <InfoRow label="Name" value={spec.name} mono />
            <InfoRow label="Tag" value={spec.tag} mono />
            <InfoRow label="MTU" value={spec.mtu} mono />
            <InfoRow label="Internal" value={spec.internal ? 'Yes' : 'No'} />
            <InfoRow label="CMP Hash" value={spec.cmp_hash || spec.cmpHash} mono />
            <InfoRow label="Auto Lasthop" value={spec.auto_lasthop || spec.autoLasthop} />
          </Section>

          {interfaces.length > 0 && (
            <Section title="Interfaces">
              {interfaces.map((iface: K8sVlanInterface, idx: number) => (
                <div key={idx} className="flex items-center gap-2">
                  <Network className="h-3 w-3 text-info" />
                  <code className="font-mono text-foreground/80">
                    {typeof iface === 'string' ? iface : iface.name || JSON.stringify(iface)}
                  </code>
                </div>
              ))}
            </Section>
          )}

          {selfIps.length > 0 && (
            <Section title="Self IPs">
              {selfIps.map((ip: string, idx: number) => (
                <div key={idx} className="flex items-center gap-2">
                  <Globe className="h-3 w-3 text-success" />
                  <code className="font-mono text-foreground/80">
                    {ip}
                  </code>
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
