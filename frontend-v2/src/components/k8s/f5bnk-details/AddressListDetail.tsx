import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Network } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import type { K8sAddressEntry } from '@/types';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function AddressListDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];
  const addresses: K8sAddressEntry[] = spec.addresses || [];

  return (
    <div className="space-y-4">
      <Tabs defaultValue="addresses" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="addresses">Addresses ({addresses.length})</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="addresses" className="space-y-3">
          {addresses.length === 0 ? (
            <div className="p-6 text-center rounded-lg bg-muted/50">
              <Network className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p className="text-xs text-muted-foreground">No addresses defined</p>
            </div>
          ) : (
            <Section title="Addresses">
              {addresses.map((addr: K8sAddressEntry, idx: number) => (
                <div key={idx} className="flex items-center gap-2">
                  <Network className="h-3 w-3 text-success" />
                  <code className="font-mono text-foreground/80">
                    {typeof addr === 'string' ? addr : addr.address || addr.ip || addr.network || JSON.stringify(addr)}
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
