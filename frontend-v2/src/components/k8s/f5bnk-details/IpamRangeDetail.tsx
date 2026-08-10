import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { formatAge } from '@/lib/time-utils';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function IpamRangeDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          <Section title="IPAM Range">
            <InfoRow label="Namespace" value={resource.metadata?.namespace} mono />
            <InfoRow label="Age" value={formatAge(resource.metadata?.creationTimestamp)} />
            <InfoRow label="Start" value={spec.startAddress || spec.start} mono />
            <InfoRow label="End" value={spec.endAddress || spec.end} mono />
            <InfoRow label="CIDR" value={spec.cidr} mono />
            <InfoRow label="Subnet" value={spec.subnet} mono />
          </Section>

          {/* Allocated IPs from status */}
          {status.allocatedAddresses && (
            <Section title="Allocated Addresses">
              {Object.entries(status.allocatedAddresses as Record<string, unknown>).map(([ip, owner]) => (
                <div key={ip} className="flex items-center justify-between">
                  <code className="font-mono text-foreground/80">{ip}</code>
                  <Badge variant="outline" className="text-[10px]">{String(owner)}</Badge>
                </div>
              ))}
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
