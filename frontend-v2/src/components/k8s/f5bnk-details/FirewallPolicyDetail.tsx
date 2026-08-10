import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Shield } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
import type { K8sFirewallRule } from '@/types';
import { InfoRow, Section, ConditionsTab, type DetailPanelProps } from './shared';

export function FirewallPolicyDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];
  // F5BigFwPolicy CRD uses spec.rule (singular), not spec.rules
  const rules: K8sFirewallRule[] = spec.rule || spec.rules || [];

  const getActionVariant = (action: string): 'success' | 'destructive' | 'warning' | 'muted' => {
    switch (action?.toLowerCase()) {
      case 'accept':
      case 'allow':
        return 'success';
      case 'drop':
      case 'deny':
        return 'destructive';
      case 'reject':
        return 'warning';
      default:
        return 'muted';
    }
  };

  // Helper to format source/destination lists, filtering empty arrays
  const formatRefs = (refs: string[] | undefined) => {
    if (!refs || refs.length === 0) return null;
    return refs.join(', ');
  };

  return (
    <div className="space-y-4">
      <Tabs defaultValue="rules" className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="rules">Rules ({rules.length})</TabsTrigger>
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="rules" className="space-y-2">
          {rules.length === 0 ? (
            <div className="p-6 text-center rounded-lg bg-muted/50">
              <Shield className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p className="text-xs text-muted-foreground">No rules defined</p>
            </div>
          ) : (
            rules.map((rule: K8sFirewallRule, idx: number) => {
              const srcAddressLists = formatRefs(rule.source?.addressLists);
              const srcAddresses = formatRefs(rule.source?.addresses);
              const dstAddressLists = formatRefs(rule.destination?.addressLists);
              const dstAddresses = formatRefs(rule.destination?.addresses);
              const dstPortLists = formatRefs(rule.destination?.portLists);
              const dstPorts = formatRefs(rule.destination?.ports);
              const sourceDisplay = srcAddressLists || srcAddresses || 'Any';
              const destDisplay = dstAddressLists || dstAddresses || 'Any';

              return (
                <div
                  key={idx}
                  className="p-3 rounded-lg border bg-muted/50 border-border"
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-medium text-sm">{rule.name || `Rule ${idx + 1}`}</span>
                    <Badge variant={getActionVariant(rule.action || '')} className="text-[10px]">
                      {rule.action || 'N/A'}
                    </Badge>
                  </div>
                  <div className="space-y-1 text-xs">
                    <InfoRow label="Protocol" value={rule.ipProtocol || rule.protocol} mono />
                    <InfoRow label="Source" value={sourceDisplay} mono />
                    <InfoRow label="Destination" value={destDisplay} mono />
                    {dstPortLists && (
                      <InfoRow label="Port Lists" value={dstPortLists} mono />
                    )}
                    {dstPorts && (
                      <InfoRow label="Ports" value={dstPorts} mono />
                    )}
                    <InfoRow label="Logging" value={rule.logging ? 'Enabled' : 'Disabled'} />
                  </div>
                </div>
              );
            })
          )}
        </TabsContent>

        <TabsContent value="summary" className="space-y-3">
          <Section title="Policy Info">
            <InfoRow label="Namespace" value={resource.metadata?.namespace} mono />
            <InfoRow label="Age" value={formatAge(resource.metadata?.creationTimestamp)} />
            <InfoRow label="Description" value={spec.description || undefined} />
            <InfoRow label="Total Rules" value={rules.length} />
            <InfoRow
              label="Accept Rules"
              value={rules.filter((r: K8sFirewallRule) => r.action?.toLowerCase() === 'accept').length}
            />
            <InfoRow
              label="Drop Rules"
              value={rules.filter((r: K8sFirewallRule) => r.action?.toLowerCase() === 'drop').length}
            />
          </Section>
        </TabsContent>

        <TabsContent value="status">
          <ConditionsTab conditions={conditions} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
