import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Server, Network } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { formatAge } from '@/lib/time-utils';
import type { K8sServicePort } from '@/types';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function ServiceDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];
  const ports: K8sServicePort[] = spec.ports || [];
  const selector = spec.selector || {};

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          <Section title="Service">
            <InfoRow label="Namespace" value={resource.metadata?.namespace} mono />
            <InfoRow label="Type" value={spec.type} />
            <InfoRow label="Cluster IP" value={spec.clusterIP || status.clusterIP} mono />
            <InfoRow label="Age" value={formatAge(resource.metadata?.creationTimestamp)} />
          </Section>

          {ports.length > 0 && (
            <Section title="Ports">
              {ports.map((port: K8sServicePort, idx: number) => (
                <div key={idx} className="flex items-center gap-2">
                  <Network className="h-3 w-3 text-info" />
                  <code className="font-mono text-foreground/80">
                    {port.port}
                    {port.targetPort !== undefined && ` → ${port.targetPort}`}
                    {port.nodePort !== undefined && ` (node:${port.nodePort})`}
                  </code>
                  {port.name && <Badge variant="outline" className="text-[10px]">{port.name}</Badge>}
                  {port.protocol && <Badge variant="secondary" className="text-[10px]">{port.protocol}</Badge>}
                </div>
              ))}
            </Section>
          )}

          {Object.keys(selector).length > 0 && (
            <Section title="Selector">
              {Object.entries(selector).map(([key, value]) => (
                <div key={key} className="flex items-center gap-2">
                  <Server className="h-3 w-3 text-muted-foreground" />
                  <code className="font-mono text-foreground/80">{key}={String(value)}</code>
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
