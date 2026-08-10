import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Globe, Server } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import type { K8sGatewayRef, K8sL4RouteRule, K8sBackendRef } from '@/types';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function L4RouteDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];
  const parentRefs: K8sGatewayRef[] = spec.parentRefs || [];
  const rules: K8sL4RouteRule[] = spec.rules || [];

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="backends">Backends</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          {parentRefs.length > 0 && (
            <Section title="Parent Gateways">
              {parentRefs.map((ref: K8sGatewayRef, idx: number) => (
                <div key={idx} className="flex items-center gap-2">
                  <Globe className="h-3 w-3 text-info" />
                  <code className="font-mono text-foreground/80">
                    {ref.name}
                  </code>
                  {ref.sectionName && (
                    <Badge variant="outline" className="text-[10px]">
                      {ref.sectionName}
                    </Badge>
                  )}
                </div>
              ))}
            </Section>
          )}

          <Section title="Metadata">
            <InfoRow label="Namespace" value={resource.metadata?.namespace} mono />
            <InfoRow label="Age" value={formatAge(resource.metadata?.creationTimestamp)} />
            <InfoRow label="Rules" value={rules.length} />
          </Section>
        </TabsContent>

        <TabsContent value="backends" className="space-y-3">
          {rules.length === 0 ? (
            <div className="p-6 text-center rounded-lg bg-muted/50">
              <Server className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p className="text-xs text-muted-foreground">No rules defined</p>
            </div>
          ) : (
            rules.map((rule: K8sL4RouteRule, idx: number) => {
              const backends: K8sBackendRef[] = rule.backendRefs || [];
              return (
                <div key={idx}>
                  {backends.map((backend: K8sBackendRef, bidx: number) => (
                    <div
                      key={bidx}
                      className="p-3 rounded-lg border mb-2 bg-muted/50 border-border"
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <Server className="h-3.5 w-3.5 text-info" />
                        <code className="font-mono text-sm text-foreground/80">
                          {backend.name}
                        </code>
                        {backend.namespace && backend.namespace !== resource.metadata?.namespace && (
                          <Badge variant="warning" className="text-[10px]">
                            ns:{backend.namespace}
                          </Badge>
                        )}
                        {backend.kind && backend.kind !== 'Service' && (
                          <Badge variant="outline" className="text-[10px]">
                            {backend.kind}
                          </Badge>
                        )}
                      </div>
                      <div className="space-y-1 text-xs">
                        <InfoRow label="Port" value={backend.port} mono />
                        <InfoRow label="Weight" value={backend.weight} />
                        {backend.namespace && backend.namespace !== resource.metadata?.namespace && (
                          <InfoRow label="Namespace" value={backend.namespace} mono />
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              );
            })
          )}
        </TabsContent>

        <TabsContent value="status">
          <ConditionsTab conditions={conditions} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
