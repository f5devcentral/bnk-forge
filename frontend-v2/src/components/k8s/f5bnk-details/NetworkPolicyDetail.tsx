import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  CheckCircle2,
  AlertCircle,
  Network,
  Code,
  Globe,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { K8sGatewayRef, K8sExtensionRef, K8sAncestorStatus, K8sDescendantStatus, K8sCondition } from '@/types';
import { getConditionIcon, getConditionColor, InfoRow, Section, type DetailPanelProps } from './shared';

export function NetworkPolicyDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const targetRefs: K8sGatewayRef[] = spec.targetRefs || [];
  const extensionRefs: K8sExtensionRef[] = spec.extensionRefs || [];
  const ancestors: K8sAncestorStatus[] = status.ancestors || [];
  const descendants: K8sDescendantStatus[] = status.descendants || [];

  // Group extensions by kind for display
  const iRuleRefs = extensionRefs.filter((r: K8sExtensionRef) => r.kind === 'F5BigCneIrule');
  const otherRefs = extensionRefs.filter((r: K8sExtensionRef) => r.kind !== 'F5BigCneIrule');

  return (
    <div className="space-y-4">
      <Tabs defaultValue="extensions" className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="extensions">Extensions ({extensionRefs.length})</TabsTrigger>
          <TabsTrigger value="targets">Targets ({targetRefs.length})</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="extensions" className="space-y-3">
          {/* iRule references */}
          {iRuleRefs.length > 0 && (
            <Section title="iRules">
              {iRuleRefs.map((ref: K8sExtensionRef, idx: number) => {
                // Check if resolved from descendants
                const descendant = descendants.find((d: K8sDescendantStatus) => d.descendantRef?.name === ref.name);
                const resolved = descendant?.conditions?.find((c: K8sCondition) => c.type === 'ResolvedRefs');
                const isResolved = resolved?.status === 'True';
                return (
                  <div key={idx} className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Code className="h-3 w-3 text-muted-foreground" />
                      <code className="font-mono text-foreground/80">
                        {ref.name}
                      </code>
                    </div>
                    {isResolved ? (
                      <Badge variant="success" className="text-[10px]">
                        <CheckCircle2 className="h-2.5 w-2.5 mr-1" />
                        Resolved
                      </Badge>
                    ) : descendant ? (
                      <Badge variant="warning" className="text-[10px]">
                        <AlertCircle className="h-2.5 w-2.5 mr-1" />
                        Pending
                      </Badge>
                    ) : null}
                  </div>
                );
              })}
            </Section>
          )}

          {/* Other extension refs (log profiles, TCP settings, etc.) */}
          {otherRefs.length > 0 && (
            <Section title="Other Extensions">
              {otherRefs.map((ref: K8sExtensionRef, idx: number) => {
                const descendant = descendants.find((d: K8sDescendantStatus) => d.descendantRef?.name === ref.name);
                const resolved = descendant?.conditions?.find((c: K8sCondition) => c.type === 'ResolvedRefs');
                const isResolved = resolved?.status === 'True';
                return (
                  <div key={idx} className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Network className="h-3 w-3 text-info" />
                      <div>
                        <code className="font-mono text-foreground/80">
                          {ref.name}
                        </code>
                        <span className="text-[10px] ml-1.5 text-muted-foreground">
                          {ref.kind}
                        </span>
                      </div>
                    </div>
                    {isResolved && (
                      <Badge variant="success" className="text-[10px]">
                        <CheckCircle2 className="h-2.5 w-2.5 mr-1" />
                        Resolved
                      </Badge>
                    )}
                  </div>
                );
              })}
            </Section>
          )}

          {extensionRefs.length === 0 && (
            <div className="p-6 text-center rounded-lg bg-muted/50">
              <Code className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p className="text-xs text-muted-foreground">No extensions configured</p>
            </div>
          )}
        </TabsContent>

        <TabsContent value="targets" className="space-y-3">
          {targetRefs.length === 0 ? (
            <div className="p-6 text-center rounded-lg bg-muted/50">
              <Globe className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p className="text-xs text-muted-foreground">No target gateways</p>
            </div>
          ) : (
            targetRefs.map((ref: K8sGatewayRef, idx: number) => {
              // Check if ancestor (gateway) is resolved
              const ancestor = ancestors.find((a: K8sAncestorStatus) =>
                a.ancestorRef?.name === ref.name && a.ancestorRef?.sectionName === ref.sectionName
              );
              const resolved = ancestor?.conditions?.find((c: K8sCondition) => c.type === 'ResolvedRefs');
              const isResolved = resolved?.status === 'True';
              return (
                <div
                  key={idx}
                  className="p-3 rounded-lg border bg-muted/50 border-border"
                >
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <Globe className="h-3.5 w-3.5 text-info" />
                      <span className="font-medium text-sm">{ref.name}</span>
                    </div>
                    {isResolved ? (
                      <Badge variant="success" className="text-[10px]">
                        <CheckCircle2 className="h-2.5 w-2.5 mr-1" />
                        Bound
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="text-[10px]">Pending</Badge>
                    )}
                  </div>
                  <div className="space-y-1 text-xs">
                    <InfoRow label="Kind" value={ref.kind || 'Gateway'} />
                    <InfoRow label="Listener" value={ref.sectionName} mono />
                    {ancestor?.controllerName && (
                      <InfoRow label="Controller" value={ancestor.controllerName} mono />
                    )}
                  </div>
                </div>
              );
            })
          )}
        </TabsContent>

        <TabsContent value="status" className="space-y-3">
          {/* Show ancestor conditions (gateway binding status) */}
          {ancestors.length > 0 && (
            <Section title="Gateway Bindings">
              {ancestors.map((a: K8sAncestorStatus, idx: number) => {
                const ref = a.ancestorRef || {};
                const conds = a.conditions || [];
                return (
                  <div key={idx} className="mb-2">
                    <div className="flex items-center gap-1 mb-1">
                      <Globe className="h-3 w-3 text-info" />
                      <span className="font-medium text-foreground/80">
                        {ref.name}
                      </span>
                      {ref.sectionName && (
                        <span className="text-muted-foreground">
                          → {ref.sectionName}
                        </span>
                      )}
                    </div>
                    {conds.map((c: K8sCondition, cidx: number) => (
                      <div key={cidx} className="flex items-center gap-1.5 ml-4">
                        {getConditionIcon(c.status)}
                        <span className={cn('text-[11px]', getConditionColor(c.status))}>
                          {c.type}: {c.reason}
                        </span>
                      </div>
                    ))}
                  </div>
                );
              })}
            </Section>
          )}

          {/* Show descendant conditions (extension resolution status) */}
          {descendants.length > 0 && (
            <Section title="Extension Resolution">
              {descendants.map((d: K8sDescendantStatus, idx: number) => {
                const ref = d.descendantRef || {};
                const conds = d.conditions || [];
                return (
                  <div key={idx} className="mb-2">
                    <div className="flex items-center gap-1 mb-1">
                      <Code className="h-3 w-3 text-muted-foreground" />
                      <span className="font-medium text-foreground/80">
                        {ref.name}
                      </span>
                      <span className="text-[10px] text-muted-foreground">
                        {ref.kind}
                      </span>
                    </div>
                    {conds.map((c: K8sCondition, cidx: number) => (
                      <div key={cidx} className="flex items-center gap-1.5 ml-4">
                        {getConditionIcon(c.status)}
                        <span className={cn('text-[11px]', getConditionColor(c.status))}>
                          {c.type}: {c.message}
                        </span>
                      </div>
                    ))}
                  </div>
                );
              })}
            </Section>
          )}

          {ancestors.length === 0 && descendants.length === 0 && (
            <div className="p-6 text-center rounded-lg bg-muted/50">
              <AlertCircle className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p className="text-xs text-muted-foreground">No status information available</p>
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
