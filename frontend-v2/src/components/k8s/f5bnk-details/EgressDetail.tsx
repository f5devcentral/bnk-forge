import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Route } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import type { K8sEgressRoute } from '@/types';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function EgressDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];
  const routes: K8sEgressRoute[] = spec.routes || [];
  const snatPoolRef = spec.snatPoolRef || spec.snatPool;

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          <Section title="Egress Config">
            <InfoRow label="Namespace" value={resource.metadata?.namespace} mono />
            <InfoRow label="Age" value={formatAge(resource.metadata?.creationTimestamp)} />
            {snatPoolRef && (
              <InfoRow label="SNAT Pool" value={typeof snatPoolRef === 'string' ? snatPoolRef : snatPoolRef.name} mono />
            )}
          </Section>

          {routes.length > 0 && (
            <Section title="Routes">
              {routes.map((route: K8sEgressRoute, idx: number) => (
                <div key={idx} className="flex items-center gap-2">
                  <Route className="h-3 w-3 text-info" />
                  <code className="font-mono text-foreground/80">
                    {typeof route === 'string' ? route : `${route.destination || route.network || 'N/A'} → ${route.gateway || route.gw || 'N/A'}`}
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
