import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { formatAge } from '@/lib/time-utils';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function LogProfileDetail({ resource }: DetailPanelProps) {
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
          <Section title="Log Profile">
            <InfoRow label="Namespace" value={resource.metadata?.namespace} mono />
            <InfoRow label="Age" value={formatAge(resource.metadata?.creationTimestamp)} />
            <InfoRow label="HSL Publisher" value={spec.hslPublisher || spec.publisher} mono />
          </Section>

          {/* Network Security logging */}
          {spec.networkSecurity && (
            <Section title="Network Security Logging">
              <InfoRow label="Enabled" value={spec.networkSecurity.enabled ? 'Yes' : 'No'} />
              <InfoRow label="Rate Limit" value={spec.networkSecurity.rateLimit} />
              <InfoRow label="Publisher" value={spec.networkSecurity.publisher} mono />
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
