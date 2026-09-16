import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Cpu, Server } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function F5EPPDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];

  const engine = spec.engine || spec.runtime || 'vllm';
  const mode = spec.mode || 'aggregated';
  const poolRef = spec.poolRef || spec.targetPool;
  const blockSize = spec.blockSize;
  const natsURL = spec.natsURL || spec.natsUrl;
  const tokenizer = spec.tokenizer;
  const modelName = spec.modelName || spec.model;

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          <Section title="EPP Analyzer Configuration">
            <InfoRow label="Model Name" value={modelName} mono />
            <div className="flex justify-between items-center text-xs">
              <span className="text-muted-foreground">Inference Engine:</span>
              <Badge variant="outline" className="font-mono text-[10px] uppercase">
                {engine}
              </Badge>
            </div>
            <div className="flex justify-between items-center text-xs">
              <span className="text-muted-foreground">Routing Mode:</span>
              <Badge variant="secondary" className="font-mono text-[10px] capitalize">
                {mode}
              </Badge>
            </div>
            {poolRef && (
              <div className="flex justify-between items-center text-xs">
                <span className="text-muted-foreground">Inference Pool:</span>
                <div className="flex items-center gap-1">
                  <Server className="h-3 w-3 text-info" />
                  <code className="font-mono text-foreground/80">{typeof poolRef === 'string' ? poolRef : poolRef.name}</code>
                </div>
              </div>
            )}
            <InfoRow label="KV Cache Block Size" value={blockSize} mono />
            <InfoRow label="Tokenizer" value={tokenizer} mono />
            <InfoRow label="NATS PubSub URL" value={natsURL} mono />
          </Section>

          {/* Runtime Metrics or Endpoints */}
          {status.endpoints && (
            <Section title="Active Endpoints">
              <div className="space-y-1">
                {Array.isArray(status.endpoints) && status.endpoints.map((ep: any, idx: number) => (
                  <div key={idx} className="flex items-center justify-between p-1.5 rounded border bg-background/50 text-xs">
                    <div className="flex items-center gap-1.5">
                      <Cpu className="h-3 w-3 text-primary" />
                      <code className="font-mono">{ep.address || ep.name || String(ep)}</code>
                    </div>
                    {ep.state && <Badge variant="outline" className="text-[10px]">{ep.state}</Badge>}
                  </div>
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
