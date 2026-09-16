import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Network, Settings, Filter } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function EgressGatewayDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];

  const gatewayClassName = spec.gatewayClassName;
  const parametersRef = spec.infrastructure?.parametersRef;
  const sourceSelector = spec.sourceSelector || {};
  const selectionMode = sourceSelector.selectionMode || 'NamespaceSelector';
  const matchNamespaces: string[] = sourceSelector.namespaces?.matchNames || [];
  const matchLabels: Record<string, string> = sourceSelector.namespaces?.matchLabels || sourceSelector.pods?.matchLabels || {};

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          <Section title="Egress Gateway Configuration">
            <InfoRow label="Gateway Class" value={gatewayClassName} mono />
            <InfoRow label="Selection Mode" value={selectionMode} mono />
            {parametersRef && (
              <div className="flex justify-between items-start gap-2 pt-1">
                <span className="text-muted-foreground shrink-0 text-xs">Parameters Ref:</span>
                <div className="flex items-center gap-1.5 text-xs">
                  <Settings className="h-3 w-3 text-muted-foreground" />
                  <code className="font-mono text-foreground/80">{parametersRef.name}</code>
                  <Badge variant="outline" className="text-[10px] font-mono">{parametersRef.kind}</Badge>
                </div>
              </div>
            )}
          </Section>

          {/* Source Selectors */}
          <Section title="Source Traffic Capture">
            {matchNamespaces.length > 0 && (
              <div className="space-y-1">
                <span className="text-muted-foreground block text-[11px]">Matched Namespaces:</span>
                <div className="flex flex-wrap gap-1.5 pl-2">
                  {matchNamespaces.map((ns: string) => (
                    <Badge key={ns} variant="secondary" className="text-[10px] font-mono">
                      {ns}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {Object.keys(matchLabels).length > 0 && (
              <div className="space-y-1 pt-1">
                <span className="text-muted-foreground block text-[11px]">Match Labels:</span>
                <div className="flex flex-wrap gap-1.5 pl-2">
                  {Object.entries(matchLabels).map(([k, v]) => (
                    <Badge key={k} variant="outline" className="text-[10px] font-mono flex items-center gap-1">
                      <Filter className="h-2.5 w-2.5 text-muted-foreground" />
                      {k}={v}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {matchNamespaces.length === 0 && Object.keys(matchLabels).length === 0 && (
              <div className="flex items-center gap-2 text-muted-foreground text-xs">
                <Network className="h-3 w-3" />
                <span>Captures all cluster traffic matching selection mode</span>
              </div>
            )}
          </Section>

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
