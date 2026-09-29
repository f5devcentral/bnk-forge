import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Cpu, Filter } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';
import type { GaieEndpointPickerRef } from '@/types/kubernetes';

export function InferencePoolDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];

  // inference.networking.k8s.io/v1 InferencePool
  const targetPorts: Array<{ number: number }> = spec.targetPorts || [];
  const selector: Record<string, string> = spec.selector?.matchLabels || {};
  const endpointPickerRef: GaieEndpointPickerRef | undefined = spec.endpointPickerRef;

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          <Section title="Inference Pool Configuration">
            <InfoRow label="Target Ports" value={targetPorts.map((p) => p.number).join(', ')} mono />
            <InfoRow label="Endpoint Picker Port" value={endpointPickerRef?.port?.number} mono />
            <InfoRow label="Failure Mode" value={endpointPickerRef?.failureMode} mono />
            {endpointPickerRef && (
              <div className="flex justify-between items-center text-xs">
                <span className="text-muted-foreground">Endpoint Picker:</span>
                <div className="flex items-center gap-1">
                  <Cpu className="h-3 w-3 text-primary" />
                  <code className="font-mono text-foreground/80">{endpointPickerRef.name}</code>
                  {endpointPickerRef.kind && (
                    <Badge variant="outline" className="text-[10px] font-mono">{endpointPickerRef.kind}</Badge>
                  )}
                </div>
              </div>
            )}
          </Section>

          {/* Pod Selectors */}
          {Object.keys(selector).length > 0 && (
            <Section title="Target Pod Selectors">
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(selector).map(([k, v]) => (
                  <Badge key={k} variant="outline" className="text-[10px] font-mono flex items-center gap-1">
                    <Filter className="h-2.5 w-2.5 text-muted-foreground" />
                    {k}={v}
                  </Badge>
                ))}
              </div>
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
