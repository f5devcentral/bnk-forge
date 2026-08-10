import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { formatAge } from '@/lib/time-utils';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function EgressDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];
  const capturedNamespaces: string[] = spec.pseudoCNIConfig?.namespaces || [];
  const vxlan = spec.pseudoCNIConfig?.vxlan;

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          <Section title="Egress Config">
            <InfoRow label="SNAT Type" value={spec.snatType} mono />
            <InfoRow label="SNAT Pool" value={spec.egressSnatpool} mono />
            <InfoRow label="Firewall Policy" value={spec.firewallEnforcedPolicy} mono />
            <InfoRow label="Log Profile" value={spec.logProfile} mono />
            <InfoRow label="Namespace" value={resource.metadata?.namespace} mono />
            <InfoRow label="Age" value={formatAge(resource.metadata?.creationTimestamp)} />
          </Section>

          {capturedNamespaces.length > 0 && (
            <Section title="Captured Namespaces">
              <div className="flex flex-wrap gap-1.5">
                {capturedNamespaces.map((ns: string) => (
                  <Badge key={ns} variant="secondary" className="text-[10px] py-0 font-mono">
                    {ns}
                  </Badge>
                ))}
              </div>
            </Section>
          )}

          {vxlan && (
            <Section title="VXLAN">
              <InfoRow label="TMM Interface" value={vxlan.tmmInterfaceName} mono />
              <InfoRow label="Node Interface" value={vxlan.nodeInterfaceName} mono />
            </Section>
          )}
        </TabsContent>

        <TabsContent value="status">
          <ConditionsTab conditions={conditions} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
