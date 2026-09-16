import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Cpu, Filter } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function InferencePoolDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];

  const modelName = spec.modelName;
  const targetPort = spec.targetPortNumber || spec.targetPort;
  const selector: Record<string, string> = spec.selector?.matchLabels || spec.selector || {};
  const endpointPickerRef = spec.endpointPickerRef;
  const failureMode = spec.failureMode;

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          <Section title="Inference Pool Configuration">
            <InfoRow label="Model Name" value={modelName} mono />
            <InfoRow label="Target Port" value={targetPort} mono />
            <InfoRow label="Failure Mode" value={failureMode} mono />
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
