import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Activity } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function iRuleDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];
  const iRuleCode = spec.iRule || spec.irule || '';

  // Extract event handlers from code
  const eventHandlers = iRuleCode
    ? Array.from(iRuleCode.matchAll(/when\s+(\w+)/g) as IterableIterator<RegExpMatchArray>).map((m) => m[1])
    : [];
  const lineCount = iRuleCode ? iRuleCode.split('\n').length : 0;

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          <Section title="iRule Info">
            <InfoRow label="Namespace" value={resource.metadata?.namespace} mono />
            <InfoRow label="Age" value={formatAge(resource.metadata?.creationTimestamp)} />
            <InfoRow label="Lines" value={lineCount} />
          </Section>

          {eventHandlers.length > 0 && (
            <Section title="Event Handlers">
              {eventHandlers.map((handler: string, idx: number) => (
                <div key={idx} className="flex items-center gap-2">
                  <Activity className="h-3 w-3 text-muted-foreground" />
                  <code className="font-mono text-foreground/80">
                    {handler}
                  </code>
                </div>
              ))}
            </Section>
          )}

          {/* Code preview — show full code for short iRules, truncate for long ones */}
          {iRuleCode && (
            <div className="p-3 rounded-lg bg-muted/50">
              <h4 className="text-xs font-semibold mb-2">
                {lineCount <= 30 ? 'iRule Code' : 'Code Preview'}
              </h4>
              <pre
                className="text-[11px] font-mono leading-relaxed overflow-auto text-muted-foreground"
                style={{ maxHeight: lineCount <= 30 ? 'none' : '200px' }}
              >
                {lineCount <= 30
                  ? iRuleCode
                  : iRuleCode.split('\n').slice(0, 25).join('\n') + `\n... (${lineCount - 25} more lines)`
                }
              </pre>
            </div>
          )}
        </TabsContent>

        <TabsContent value="status">
          <ConditionsTab conditions={conditions} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
