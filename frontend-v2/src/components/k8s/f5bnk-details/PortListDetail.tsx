import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Network } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import type { K8sPortEntry } from '@/types';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function PortListDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];
  const ports: K8sPortEntry[] = spec.ports || [];

  return (
    <div className="space-y-4">
      <Tabs defaultValue="ports" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="ports">Ports ({ports.length})</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="ports" className="space-y-3">
          {ports.length === 0 ? (
            <div className="p-6 text-center rounded-lg bg-muted/50">
              <Network className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p className="text-xs text-muted-foreground">No ports defined</p>
            </div>
          ) : (
            <Section title="Ports">
              {ports.map((port: K8sPortEntry, idx: number) => (
                <div key={idx} className="flex items-center gap-2">
                  <Network className="h-3 w-3 text-muted-foreground" />
                  <code className="font-mono text-foreground/80">
                    {typeof port === 'string' || typeof port === 'number' ? port : port.port || port.name || JSON.stringify(port)}
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
