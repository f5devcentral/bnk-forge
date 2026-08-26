/**
 * WafPolicyDetail — NIM-style 3-tab detail panel for APPolicy resources.
 *
 * Tabs:
 *   1. Details  — metadata, configuration summary, bundle status, sig versions
 *   2. Policy JSON — read-only JSON with copy/download, version selector placeholder
 *   3. Security Logs — existing SecurityLogsTab
 */

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Shield, Copy, Check, Download } from 'lucide-react';
import { cn } from '@/lib/utils';
import { formatAge } from '@/lib/time-utils';
import type { APPolicyResource, BundleState } from '@/types';
import { SecurityLogsTab } from '@/components/k8s/waf/SecurityLogsTab';
import { InfoRow, Section, type DetailPanelProps } from './shared';

type DetailTab = 'details' | 'json' | 'logs';

function bundleStateBadge(state: BundleState | undefined) {
  switch (state) {
    case 'ready':      return 'bg-success/10 text-success border-success/30';
    case 'invalid':    return 'bg-destructive/10 text-destructive border-destructive/30';
    case 'processing': return 'bg-primary/10 text-primary border-primary/30';
    default:           return 'bg-muted-foreground/10 text-muted-foreground border-muted-foreground/30';
  }
}

export function WafPolicyDetail({ resource, isDark = false, clusterId }: DetailPanelProps) {
  const policy = resource as APPolicyResource;
  const spec = policy.spec || {};
  const status = policy.status || {};
  const bundle = status.bundle;
  const policyObj = spec.policy as Record<string, unknown> | undefined;

  const [tab, setTab]       = useState<DetailTab>('details');
  const [copied, setCopied] = useState(false);

  const policyJson = JSON.stringify(policyObj ?? spec, null, 2);
  const fullJson   = JSON.stringify(policy, null, 2);

  const copyJson = () => {
    void navigator.clipboard.writeText(policyJson);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  const downloadJson = () => {
    const blob = new Blob([fullJson], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `apolicy-${policy.metadata?.name ?? 'policy'}.json`; a.click();
    URL.revokeObjectURL(url);
  };

  const enfMode = String(policyObj?.['enforcement-mode'] ?? policyObj?.['enforcementMode'] ?? 'blocking');

  return (
    <div className="flex flex-col h-full">
      {/* NIM-style tab bar */}
      <div className="border-b border-border px-4">
        <div className="flex gap-0">
          {([
            { key: 'details', label: 'Details' },
            { key: 'json',    label: 'Policy JSON' },
            { key: 'logs',    label: 'Security Logs' },
          ] as { key: DetailTab; label: string }[]).map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={cn(
                'px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px',
                tab === key
                  ? 'border-primary text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground hover:border-border',
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Details tab */}
      {tab === 'details' && (
        <div className="flex-1 overflow-y-auto p-4 space-y-4">

          {/* Top summary strip — mimics NIM's header line in policy detail */}
          <div className="flex flex-wrap items-center gap-3">
            <Badge variant="outline" className={cn('text-[10px]', bundleStateBadge(bundle?.state))}>
              {bundle?.state ?? 'unknown'}
            </Badge>
            <Badge variant="outline" className={cn('text-[10px]',
              enfMode === 'blocking'     ? 'bg-destructive/10 text-destructive border-destructive/30'
              : enfMode === 'transparent' ? 'bg-warning/10 text-warning border-warning/30'
              : 'bg-muted-foreground/10 text-muted-foreground border-muted-foreground/30'
            )}>
              {enfMode}
            </Badge>
            <span className="text-xs text-muted-foreground ml-auto">
              {formatAge(policy.metadata?.creationTimestamp)} ago
            </span>
          </div>

          <Section title="Policy Configuration" isDark={isDark}>
            <InfoRow label="Enforcement Mode" value={enfMode} isDark={isDark} />
            <InfoRow label="Template" value={String((policyObj?.template as { name?: string } | undefined)?.name ?? '—')} isDark={isDark} />
            <InfoRow label="App Language" value={String(policyObj?.applicationLanguage ?? 'utf-8')} isDark={isDark} />
            <InfoRow label="Description" value={String(policyObj?.description ?? '—')} isDark={isDark} />
          </Section>

          <Section title="Active Protections" isDark={isDark}>
            <InfoRow label="Signature Sets" value={String((policyObj?.['signature-sets'] as unknown[] | undefined)?.length ?? 0)} isDark={isDark} />
            <InfoRow label="Server Technologies" value={String((policyObj?.['server-technologies'] as unknown[] | undefined)?.length ?? 0)} isDark={isDark} />
            <InfoRow label="Bot Defense" value={(policyObj?.['bot-defense'] as { settings?: { isEnabled?: boolean } } | undefined)?.settings?.isEnabled ? 'Enabled' : 'Disabled'} isDark={isDark} />
            <InfoRow label="CSRF Protection" value={(policyObj?.['csrf-protection'] as { enabled?: boolean } | undefined)?.enabled ? 'Enabled' : 'Disabled'} isDark={isDark} />
            <InfoRow label="Data Guard" value={(policyObj?.['data-guard'] as { enabled?: boolean } | undefined)?.enabled ? 'Enabled' : 'Disabled'} isDark={isDark} />
            <InfoRow label="Custom URLs" value={String((policyObj?.urls as unknown[] | undefined)?.length ?? 0)} isDark={isDark} />
          </Section>

          <Section title="Compilation" isDark={isDark}>
            <InfoRow label="Compiler Version" value={bundle?.compilerVersion} isDark={isDark} />
            <InfoRow label="Bundle State" value={bundle?.state} isDark={isDark} />
            <InfoRow label="SHA256" value={bundle?.sha256?.slice(0, 16) ? `${bundle.sha256.slice(0, 16)}…` : '—'} isDark={isDark} mono />
            <InfoRow label="Location" value={bundle?.location?.split('/').pop() ?? '—'} isDark={isDark} mono />
          </Section>

          {bundle?.signatures && (
            <Section title="Signature Package Versions" isDark={isDark}>
              <InfoRow label="Attack Signatures" value={String(bundle.signatures.attackSignatures ?? '—')} isDark={isDark} />
              <InfoRow label="Bot Signatures" value={String(bundle.signatures.botSignatures ?? '—')} isDark={isDark} />
              <InfoRow label="Threat Campaigns" value={String(bundle.signatures.threatCampaigns ?? '—')} isDark={isDark} />
            </Section>
          )}

          <Section title="Metadata" isDark={isDark}>
            <InfoRow label="Name" value={policy.metadata?.name} isDark={isDark} mono />
            <InfoRow label="Namespace" value={policy.metadata?.namespace} isDark={isDark} mono />
            <InfoRow label="UID" value={policy.metadata?.uid?.slice(0, 8) ? `${policy.metadata.uid.slice(0, 8)}…` : '—'} isDark={isDark} mono />
            <InfoRow label="Created" value={formatAge(policy.metadata?.creationTimestamp) + ' ago'} isDark={isDark} />
          </Section>
        </div>
      )}

      {/* Policy JSON tab */}
      {tab === 'json' && (
        <div className="flex-1 overflow-hidden flex flex-col">
          {/* Toolbar */}
          <div className="flex items-center gap-2 px-4 py-2 border-b border-border bg-muted/20">
            <span className="text-xs text-muted-foreground flex-1">
              Read-only · <span className="font-mono">{policy.metadata?.name}</span>
            </span>
            <Button variant="ghost" size="sm" className="h-7 gap-1.5 text-xs" onClick={copyJson}>
              {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
              {copied ? 'Copied' : 'Copy'}
            </Button>
            <Button variant="ghost" size="sm" className="h-7 gap-1.5 text-xs" onClick={downloadJson}>
              <Download className="h-3 w-3" /> Download
            </Button>
          </div>
          <div className="flex-1 overflow-auto bg-muted/30 dark:bg-card/50">
            <pre className="p-4 text-xs font-mono text-foreground/90 whitespace-pre leading-relaxed">
              <code>{policyJson}</code>
            </pre>
          </div>
        </div>
      )}

      {/* Security Logs tab */}
      {tab === 'logs' && (
        <div className="flex-1 overflow-y-auto p-4">
          {clusterId ? (
            <SecurityLogsTab
              clusterId={clusterId}
              namespace={policy.metadata?.namespace ?? 'default'}
              crKind="appolicy"
              crName={policy.metadata?.name ?? ''}
            />
          ) : (
            <div className="flex flex-col items-center justify-center py-12 gap-2 text-center">
              <Shield className="h-8 w-8 text-muted-foreground/30" />
              <p className="text-xs text-muted-foreground">Cluster context unavailable for log retrieval.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
