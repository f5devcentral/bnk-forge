/**
 * APUserSigDetail — read-only detail panel for APUserSig resources.
 *
 * Tabs:
 *   1. Overview   — identity, status
 *   2. Signatures — list of signature rules
 *   3. YAML       — raw spec as formatted JSON
 */

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { formatAge } from '@/lib/time-utils';
import type { APUserSigResource } from '@/types';
import { InfoRow, Section, type DetailPanelProps } from './shared';

export function APUserSigDetail({ resource }: DetailPanelProps) {
  const us = resource as APUserSigResource;
  const [tab, setTab] = useState<'overview' | 'signatures' | 'yaml'>('overview');
  const tabs = ['overview', 'signatures', 'yaml'] as const;
  const tabLabels = { overview: 'Overview', signatures: `Signatures (${(us.spec?.signatures ?? []).length})`, yaml: 'Spec JSON' };
  const installState = us.status?.installationState;

  const stateClass = installState === 'success'
    ? 'bg-success/10 text-success border-success/50/20'
    : installState === 'failure'
      ? 'bg-destructive/10 text-destructive border-destructive/50/20'
      : 'bg-muted-foreground/20/10 text-muted-foreground border-muted-foreground/30/20';

  return (
    <div className="space-y-0">
      <div className="flex border-b border-border dark:border-border">
        {tabs.map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              'px-4 py-2 text-xs font-medium border-b-2 transition-colors',
              tab === t
                ? 'border-primary text-foreground dark:text-white'
                : 'border-transparent text-muted-foreground hover:text-foreground/80 dark:text-muted-foreground dark:hover:text-foreground/90'
            )}
          >
            {tabLabels[t]}
          </button>
        ))}
      </div>

      <div className="p-4 space-y-4">
        {tab === 'overview' && (
          <>
            <Section title="Identity">
              <InfoRow label="Name" value={us.metadata.name} />
              <InfoRow label="Namespace" value={us.metadata.namespace} />
              <InfoRow label="Age" value={formatAge(us.metadata.creationTimestamp)} />
              <InfoRow label="Tag" value={us.spec?.tag} mono />
              <InfoRow label="Software Version" value={us.spec?.softwareVersion ?? '—'} />
              <InfoRow label="Signatures Defined" value={(us.spec?.signatures ?? []).length} />
            </Section>

            <Section title="Status">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-muted-foreground">Installation State</span>
                <Badge variant="outline" className={cn('text-[10px]', stateClass)}>
                  {installState ?? 'unknown'}
                </Badge>
              </div>
              <InfoRow label="Policy Update State" value={us.status?.policyUpdateState ?? '—'} />
              {us.status?.processing?.datetime && (
                <InfoRow label="Last Processed" value={us.status.processing.datetime} />
              )}
              {us.status?.processing?.errors && us.status.processing.errors.length > 0 && (
                <div className="mt-2 rounded border border-destructive/20 bg-destructive/10 dark:border-destructive/20 dark:bg-destructive/20/20 p-2">
                  <p className="text-xs font-medium text-destructive mb-1">Processing Errors</p>
                  {us.status.processing.errors.map((e, i) => (
                    <p key={i} className="text-xs text-destructive font-mono">{e}</p>
                  ))}
                </div>
              )}
            </Section>
          </>
        )}

        {tab === 'signatures' && (
          <div className="space-y-3">
            {(us.spec?.signatures ?? []).length === 0 ? (
              <p className="text-xs text-muted-foreground">No signature rules defined.</p>
            ) : (
              (us.spec?.signatures ?? []).map((sig, i) => (
                <div key={i} className="rounded-md border border-border dark:border-border p-3 space-y-1.5">
                  <div className="flex items-center gap-2">
                    <p className="text-xs font-medium">{sig.name ?? `Rule ${i + 1}`}</p>
                    <Badge variant="outline" className="text-[10px]">{sig.signatureType ?? 'request'}</Badge>
                    {sig.risk && <Badge variant="outline" className="text-[10px]">{sig.risk}</Badge>}
                    {sig.accuracy && <Badge variant="outline" className="text-[10px]">{sig.accuracy} accuracy</Badge>}
                  </div>
                  {sig.rule && (
                    <pre className="text-xs font-mono bg-muted/50 dark:bg-card rounded px-2 py-1 overflow-x-auto">{sig.rule}</pre>
                  )}
                  {sig.description && <p className="text-xs text-muted-foreground">{sig.description}</p>}
                  {sig.attackType?.name && <p className="text-xs text-muted-foreground">Attack type: {sig.attackType.name}</p>}
                </div>
              ))
            )}
          </div>
        )}

        {tab === 'yaml' && (
          <pre className="rounded-lg bg-muted/50 dark:bg-card border border-border dark:border-border p-3 text-xs font-mono overflow-auto max-h-[400px]">
            <code>{JSON.stringify(us.spec, null, 2)}</code>
          </pre>
        )}
      </div>
    </div>
  );
}
