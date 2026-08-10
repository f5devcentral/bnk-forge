import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { CheckCircle2, Network } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function CNEInstanceDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];

  // Feature toggles
  const features = [
    { label: 'Firewall ACL', value: spec.firewallACL?.enabled },
    { label: 'Intelligent LB', value: spec.intelligentLB?.enabled },
    { label: 'Metric Subsystem', value: spec.metricSubsystem?.enabled },
    { label: 'Logging Subsystem', value: spec.loggingSubsystem?.enabled },
    { label: 'Core Collection', value: spec.coreCollection?.enabled },
    { label: 'DPU', value: spec.dpu?.enabled },
    { label: 'Env Discovery', value: spec.envDiscovery?.enabled },
    { label: 'Pseudo CNI', value: spec.pseudoCNI?.enabled },
    { label: 'Whole Cluster', value: spec.wholeCluster },
  ].filter(f => f.value !== undefined);

  const networkAttachments = spec.networkAttachments || [];

  return (
    <div className="space-y-4">
      <Tabs defaultValue="features" className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="features">Features</TabsTrigger>
          <TabsTrigger value="config">Config</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="features" className="space-y-3">
          <Section title="Feature Toggles">
            {features.map((f, idx) => (
              <div key={idx} className="flex items-center justify-between">
                <span className="text-muted-foreground">{f.label}:</span>
                {f.value ? (
                  <Badge variant="success" className="text-[10px]">
                    <CheckCircle2 className="h-2.5 w-2.5 mr-1" />
                    Enabled
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-[10px]">
                    Disabled
                  </Badge>
                )}
              </div>
            ))}
          </Section>
        </TabsContent>

        <TabsContent value="config" className="space-y-3">
          <Section title="Instance Config">
            <InfoRow label="Namespace" value={resource.metadata?.namespace} mono />
            <InfoRow label="Age" value={formatAge(resource.metadata?.creationTimestamp)} />
            <InfoRow label="Container Platform" value={spec.containerPlatform} />
            <InfoRow label="Storage Class" value={spec.storageClassName} mono />
            <InfoRow label="TMM Replicas" value={spec.tmmReplicas} />
          </Section>

          {networkAttachments.length > 0 && (
            <Section title="Network Attachments">
              {networkAttachments.map((na: string, idx: number) => (
                <div key={idx} className="flex items-center gap-2">
                  <Network className="h-3 w-3 text-info" />
                  <code className="font-mono text-foreground/80">
                    {na}
                  </code>
                </div>
              ))}
            </Section>
          )}

          {/* TMM env vars */}
          {spec.tmmEnv && Object.keys(spec.tmmEnv).length > 0 && (
            <Section title="TMM Environment">
              {Object.entries(spec.tmmEnv as Record<string, unknown>).map(([key, val]) => (
                <InfoRow key={key} label={key} value={String(val)} mono />
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
