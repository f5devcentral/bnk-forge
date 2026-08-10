import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Network } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import type { K8sSnatMember } from '@/types';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function SnatPoolDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];
  const members: K8sSnatMember[] = spec.members || spec.addresses || spec.snatAddresses || [];

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          <Section title="SNAT Pool">
            <InfoRow label="Namespace" value={resource.metadata?.namespace} mono />
            <InfoRow label="Age" value={formatAge(resource.metadata?.creationTimestamp)} />
            <InfoRow label="Members" value={members.length} />
          </Section>

          {members.length > 0 && (
            <Section title="Addresses">
              {members.map((member: K8sSnatMember, idx: number) => (
                <div key={idx} className="flex items-center gap-2">
                  <Network className="h-3 w-3 text-info" />
                  <code className="font-mono text-foreground/80">
                    {typeof member === 'string' ? member : member.address || member.ip || JSON.stringify(member)}
                  </code>
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
