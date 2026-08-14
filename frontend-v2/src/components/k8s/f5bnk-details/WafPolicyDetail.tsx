import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Shield } from 'lucide-react';
import { cn } from '@/lib/utils';
import { formatAge } from '@/lib/time-utils';
import type { APPolicyResource, BundleState } from '@/types';
import { InfoRow, Section, type DetailPanelProps } from './shared';

function getBundleStateColor(state: BundleState | undefined) {
  switch (state) {
    case 'ready':
      return 'bg-green-500/10 text-green-600 border-green-500/20';
    case 'invalid':
      return 'bg-red-500/10 text-red-600 border-red-500/20';
    case 'processing':
      return 'bg-blue-500/10 text-blue-600 border-blue-500/20';
    case 'pending':
    default:
      return 'bg-slate-500/10 text-slate-600 border-slate-500/20';
  }
}

export function WafPolicyDetail({ resource }: DetailPanelProps) {
  const policy = resource as APPolicyResource;
  const spec = policy.spec || {};
  const status = policy.status || {};
  const bundle = status.bundle;

  return (
    <div className="space-y-4">
      <Tabs defaultValue="policy" className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="policy">Policy</TabsTrigger>
          <TabsTrigger value="status">Bundle Status</TabsTrigger>
          <TabsTrigger value="summary">Summary</TabsTrigger>
        </TabsList>

        <TabsContent value="policy" className="space-y-2">
          {!spec.policy ? (
            <div className={cn('p-6 text-center rounded-lg bg-slate-50 dark:bg-slate-800/50')}>
              <Shield className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p className="text-xs text-slate-500">No inline policy defined</p>
            </div>
          ) : (
            <pre className={cn('p-3 rounded-lg text-xs overflow-auto max-h-[400px] bg-slate-50 dark:bg-slate-800/50')}>
              <code>{JSON.stringify(spec.policy, null, 2)}</code>
            </pre>
          )}
        </TabsContent>

        <TabsContent value="status" className="space-y-3">
          <Section title="Bundle">
            <div className="flex items-center justify-between mb-2">
              <span className="text-slate-500">State</span>
              <Badge variant="outline" className={cn('text-[10px]', getBundleStateColor(bundle?.state))}>
                {bundle?.state ?? 'unknown'}
              </Badge>
            </div>
            <InfoRow label="Location" value={bundle?.location} mono />
            <InfoRow label="SHA256" value={bundle?.sha256} mono />
            <InfoRow label="Compiler Version" value={bundle?.compilerVersion} />
            <InfoRow label="Observed Generation" value={bundle?.observedGeneration} />
          </Section>
          {status.lastGoodBundle && (
            <Section title="Last Good Bundle (fallback)">
              <InfoRow label="Location" value={status.lastGoodBundle.location} mono />
              <InfoRow label="SHA256" value={status.lastGoodBundle.sha256} mono />
            </Section>
          )}
        </TabsContent>

        <TabsContent value="summary" className="space-y-3">
          <Section title="Policy Info">
            <InfoRow label="Namespace" value={policy.metadata?.namespace} mono />
            <InfoRow label="Age" value={formatAge(policy.metadata?.creationTimestamp)} />
            <InfoRow label="Modifications" value={spec.modifications?.length ?? 0} />
          </Section>
        </TabsContent>
      </Tabs>
    </div>
  );
}
