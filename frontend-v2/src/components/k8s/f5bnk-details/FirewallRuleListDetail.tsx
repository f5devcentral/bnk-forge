import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Shield } from 'lucide-react';
import type { K8sFirewallRule } from '@/types';
import { InfoRow, ConditionsTab, type DetailPanelProps } from './shared';

export function FirewallRuleListDetail({ resource }: DetailPanelProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const conditions = status.conditions || [];
  // F5BigFwRulelist CRD uses spec.rule (singular), same as F5BigFwPolicy
  const rules: K8sFirewallRule[] = spec.rule || spec.rules || [];

  return (
    <div className="space-y-4">
      <Tabs defaultValue="rules" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="rules">Rules ({rules.length})</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        <TabsContent value="rules" className="space-y-2">
          {rules.length === 0 ? (
            <div className="p-6 text-center rounded-lg bg-muted/50">
              <Shield className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p className="text-xs text-muted-foreground">No rules in this list</p>
            </div>
          ) : (
            rules.map((rule: K8sFirewallRule, idx: number) => {
              const isAllow = ['accept', 'allow'].includes(rule.action?.toLowerCase() ?? '');
              return (
                <div
                  key={idx}
                  className="p-3 rounded-lg border bg-muted/50 border-border"
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-medium text-sm">{rule.name || `Rule ${idx + 1}`}</span>
                    <Badge variant={isAllow ? 'success' : 'destructive'} className="text-[10px]">
                      {rule.action || 'N/A'}
                    </Badge>
                  </div>
                  <div className="space-y-1 text-xs">
                    <InfoRow label="Protocol" value={rule.ipProtocol || rule.protocol} mono />
                    <InfoRow label="Logging" value={rule.logging ? 'Enabled' : 'Disabled'} />
                  </div>
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
