import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Globe } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import type { K8sGatewayRef } from '@/types';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function SecurityPolicyDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];
  const targetRefs: K8sGatewayRef[] = spec.targetRefs || [];

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          {/* Target refs */}
          {targetRefs.length > 0 && (
            <Section title="Target Gateways">
              {targetRefs.map((ref: K8sGatewayRef, idx: number) => (
                <div key={idx} className="flex items-center gap-2">
                  <Globe className="h-3 w-3 text-info" />
                  <code className="font-mono text-foreground/80">
                    {ref.name}
                  </code>
                  {ref.sectionName && (
                    <Badge variant="outline" className="text-[10px]">
                      listener: {ref.sectionName}
                    </Badge>
                  )}
                </div>
              ))}
            </Section>
          )}

          {/* Firewall policy reference */}
          {spec.firewallPolicy && (
            <Section title="Firewall Policy">
              <InfoRow label="Name" value={spec.firewallPolicy.name || spec.firewallPolicy} mono />
              <InfoRow label="Namespace" value={spec.firewallPolicy.namespace} mono />
            </Section>
          )}

          {/* Extension policy references */}
          {spec.policy && (
            <Section title="Extension Policies">
              {spec.policy.firewallPolicy && (
                <InfoRow label="Firewall" value={spec.policy.firewallPolicy.name || spec.policy.firewallPolicy} mono />
              )}
            </Section>
          )}

          <Section title="Metadata">
            <InfoRow label="Namespace" value={resource.metadata?.namespace} mono />
            <InfoRow label="Age" value={formatAge(resource.metadata?.creationTimestamp)} />
            <InfoRow label="Target Count" value={targetRefs.length} />
          </Section>
        </TabsContent>

        <TabsContent value="status">
          <ConditionsTab conditions={conditions} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
