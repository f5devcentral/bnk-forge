import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Shield, Code2, X, Copy, Check } from 'lucide-react';
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

export function WafPolicyDetail({ resource, isDark = false }: DetailPanelProps) {
  const policy = resource as APPolicyResource;
  const spec = policy.spec || {};
  const status = policy.status || {};
  const bundle = status.bundle;
  const [showJson, setShowJson] = useState(false);
  const [copied, setCopied] = useState(false);

  const policyJson = JSON.stringify(spec.policy, null, 2);
  const policyObj = spec.policy as Record<string, unknown> | undefined;

  const copyJson = () => {
    void navigator.clipboard.writeText(policyJson);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="status">Bundle Status</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-3">
          {/* Policy JSON — toggled via button, not a permanent tab */}
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium text-muted-foreground">{policy.metadata?.name}</p>
            <Button variant="outline" size="sm" className="h-7 text-xs gap-1.5"
              onClick={() => setShowJson(v => !v)}>
              {showJson ? <><X className="h-3 w-3" /> Hide JSON</> : <><Code2 className="h-3 w-3" /> View JSON</>}
            </Button>
          </div>

          {showJson && (
            <div className={cn('rounded-lg border relative', isDark ? 'border-zinc-800 bg-zinc-950' : 'border-slate-200 bg-slate-50')}>
              <Button variant="ghost" size="sm" className="absolute top-1.5 right-1.5 h-6 w-6 p-0" onClick={copyJson}>
                {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
              </Button>
              <pre className="p-3 text-xs overflow-auto max-h-[320px] pr-8"><code>{policyJson}</code></pre>
            </div>
          )}

          <Section title="Policy Configuration" isDark={isDark}>
            <InfoRow label="Enforcement Mode" value={String(policyObj?.['enforcement-mode'] ?? policyObj?.enforcementMode ?? 'blocking')} isDark={isDark} />
            <InfoRow label="Template" value={String((policyObj?.template as { name?: string } | undefined)?.name ?? '—')} isDark={isDark} />
            <InfoRow label="Language" value={String(policyObj?.applicationLanguage ?? 'utf-8')} isDark={isDark} />
            <InfoRow label="Case Insensitive" value={String(policyObj?.caseInsensitive ?? 'false')} isDark={isDark} />
            <InfoRow label="Description" value={String(policyObj?.description ?? '—')} isDark={isDark} />
          </Section>

          <Section title="Active Features" isDark={isDark}>
            <InfoRow label="Signature Sets" value={String((policyObj?.['signature-sets'] as unknown[] | undefined)?.length ?? 0)} isDark={isDark} />
            <InfoRow label="Server Technologies" value={String((policyObj?.['server-technologies'] as unknown[] | undefined)?.length ?? 0)} isDark={isDark} />
            <InfoRow label="Bot Defense" value={String((policyObj?.['bot-defense'] as { settings?: { isEnabled?: boolean } } | undefined)?.settings?.isEnabled ? 'enabled' : 'disabled')} isDark={isDark} />
            <InfoRow label="CSRF Protection" value={String((policyObj?.['csrf-protection'] as { enabled?: boolean } | undefined)?.enabled ? 'enabled' : 'disabled')} isDark={isDark} />
            <InfoRow label="Data Guard" value={String((policyObj?.['data-guard'] as { enabled?: boolean } | undefined)?.enabled ? 'enabled' : 'disabled')} isDark={isDark} />
            <InfoRow label="URL Overrides" value={String((policyObj?.urls as unknown[] | undefined)?.length ?? 0)} isDark={isDark} />
            <InfoRow label="IP Whitelist Entries" value={String((policyObj?.['whitelist-ips'] as unknown[] | undefined)?.length ?? 0)} isDark={isDark} />
          </Section>

          <Section title="Metadata" isDark={isDark}>
            <InfoRow label="Namespace" value={policy.metadata?.namespace} isDark={isDark} mono />
            <InfoRow label="Age" value={formatAge(policy.metadata?.creationTimestamp)} isDark={isDark} />
          </Section>
          {Object.keys(policy.metadata?.labels ?? {}).length > 0 && (
            <Section title="Labels" isDark={isDark}>
              {Object.entries(policy.metadata?.labels ?? {}).map(([k, v]) => (
                <InfoRow key={k} label={k} value={String(v)} isDark={isDark} mono />
              ))}
            </Section>
          )}
          {Object.entries(policy.metadata?.annotations ?? {}).filter(([k]) => !k.startsWith('kubectl.kubernetes.io')).length > 0 && (
            <Section title="Annotations" isDark={isDark}>
              {Object.entries(policy.metadata?.annotations ?? {}).filter(([k]) => !k.startsWith('kubectl.kubernetes.io')).map(([k, v]) => (
                <InfoRow key={k} label={k} value={String(v)} isDark={isDark} mono />
              ))}
            </Section>
          )}
        </TabsContent>

        <TabsContent value="status" className="space-y-3">
          <Section title="Compilation Bundle" isDark={isDark}>
            <div className="flex items-center justify-between mb-2">
              <span className="text-slate-500 text-xs">State</span>
              <Badge variant="outline" className={cn('text-[10px]', getBundleStateColor(bundle?.state))}>
                {bundle?.state ?? 'unknown'}
              </Badge>
            </div>
            <InfoRow label="Compiler Version" value={bundle?.compilerVersion} isDark={isDark} />
            <InfoRow label="Observed Generation" value={bundle?.observedGeneration} isDark={isDark} />
            <InfoRow label="Location" value={bundle?.location} isDark={isDark} mono />
            <InfoRow label="SHA256" value={bundle?.sha256} isDark={isDark} mono />
          </Section>
          {bundle?.signatures && (
            <Section title="Signature Versions" isDark={isDark}>
              <InfoRow label="Attack Sigs" value={bundle.signatures.attackSignatures} isDark={isDark} />
              <InfoRow label="Bot Sigs" value={bundle.signatures.botSignatures} isDark={isDark} />
              <InfoRow label="Threat Campaigns" value={bundle.signatures.threatCampaigns} isDark={isDark} />
            </Section>
          )}
          {status.lastGoodBundle && (
            <Section title="Last Good Bundle (fallback)" isDark={isDark}>
              <InfoRow label="Location" value={status.lastGoodBundle.location} isDark={isDark} mono />
              <InfoRow label="SHA256" value={status.lastGoodBundle.sha256} isDark={isDark} mono />
            </Section>
          )}
          {!spec.policy && (
            <div className={cn('p-6 text-center rounded-lg', isDark ? 'bg-slate-800/50' : 'bg-slate-50')}>
              <Shield className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p className="text-xs text-slate-500">No inline policy defined</p>
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
