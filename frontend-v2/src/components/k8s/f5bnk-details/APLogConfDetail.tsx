/**
 * APLogConfDetail — read-only detail panel for APLogConf resources.
 * "View JSON" button on Overview tab (matches WafPolicyDetail pattern).
 */

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Code2, X, Copy, Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { formatAge } from '@/lib/time-utils';
import type { APLogConfResource, BundleState } from '@/types';
import { InfoRow, Section, type DetailPanelProps } from './shared';

function getBundleColor(state: BundleState | undefined) {
  switch (state) {
    case 'ready':      return 'bg-success/10 text-success border-success/50/20';
    case 'invalid':    return 'bg-destructive/10 text-destructive border-destructive/50/20';
    case 'processing': return 'bg-primary/10 text-primary border-primary/50/20';
    default:           return 'bg-muted-foreground/20/10 text-muted-foreground border-muted-foreground/30/20';
  }
}

export function APLogConfDetail({ resource }: DetailPanelProps) {
  const lc = resource as APLogConfResource;
  const content = lc.spec?.content as Record<string, unknown> | undefined;
  const filter = lc.spec?.filter;
  const bundle = lc.status?.bundle;

  const [tab, setTab] = useState<'overview' | 'status'>('overview');
  const [showJson, setShowJson] = useState(false);
  const [copied, setCopied] = useState(false);

  const specJson = JSON.stringify(lc.spec, null, 2);
  const copyJson = () => { void navigator.clipboard.writeText(specJson); setCopied(true); setTimeout(() => setCopied(false), 2000); };

  const REQUEST_TYPE_LABELS: Record<string, string> = {
    illegal: 'illegal — violations only',
    blocked: 'blocked — blocked requests only',
    all: 'all — every request (high volume)',
  };

  return (
    <div className="space-y-0">
      <div className="flex border-b border-border dark:border-border">
        {(['overview', 'status'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={cn('px-4 py-2 text-xs font-medium border-b-2 transition-colors',
              tab === t ? 'border-primary text-foreground dark:text-white'
                        : 'border-transparent text-muted-foreground hover:text-foreground/80 dark:text-muted-foreground')}>
            {t === 'overview' ? 'Overview' : 'Bundle Status'}
          </button>
        ))}
      </div>

      <div className="p-4 space-y-4">
        {tab === 'overview' && (
          <>
            {/* View JSON toggle — same pattern as WafPolicyDetail */}
            <div className="flex items-center justify-between">
              <p className="text-xs font-medium text-muted-foreground">{lc.metadata.name}</p>
              <Button variant="outline" size="sm" className="h-7 text-xs gap-1.5" onClick={() => setShowJson(v => !v)}>
                {showJson ? <><X className="h-3 w-3" /> Hide JSON</> : <><Code2 className="h-3 w-3" /> View JSON</>}
              </Button>
            </div>

            {showJson && (
              <div className="rounded-lg border relative border-border dark:border-border bg-muted/50 dark:bg-card">
                <Button variant="ghost" size="sm" className="absolute top-1.5 right-1.5 h-6 w-6 p-0" onClick={copyJson}>
                  {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
                </Button>
                <pre className="p-3 text-xs overflow-auto max-h-[280px] pr-8 font-mono"><code>{specJson}</code></pre>
              </div>
            )}

            <Section title="Content Settings">
              <InfoRow label="Format" value={String(content?.format ?? 'default')} />
              <InfoRow label="Format String" value={String(content?.format_string ?? '—')} mono />
              <InfoRow label="Max Message Size" value={String(content?.max_message_size ?? '10k')} />
              <InfoRow label="Max Request Size" value={String(content?.max_request_size ?? 'any')} />
              <InfoRow label="List Delimiter" value={String(content?.list_delimiter ?? ',')} />
              {content?.list_prefix != null && <InfoRow label="List Prefix" value={String(content.list_prefix)} mono />}
              {content?.list_suffix != null && <InfoRow label="List Suffix" value={String(content.list_suffix)} mono />}
              {Array.isArray(content?.escaping_characters) && (content.escaping_characters as unknown[]).length > 0 && (
                <InfoRow label="Escaping Pairs" value={String((content.escaping_characters as Array<{from:string;to:string}>).map(p => `"${p.from}"→"${p.to}"`).join(', '))} mono />
              )}
            </Section>

            <Section title="Filter">
              <InfoRow label="Request Type" value={REQUEST_TYPE_LABELS[filter?.request_type ?? 'illegal'] ?? filter?.request_type ?? 'illegal'} />
            </Section>

            <Section title="Metadata">
              <InfoRow label="Namespace" value={lc.metadata.namespace} />
              <InfoRow label="Age" value={formatAge(lc.metadata.creationTimestamp)} />
            </Section>
            {Object.keys(lc.metadata.labels ?? {}).length > 0 && (
              <Section title="Labels">
                {Object.entries(lc.metadata.labels ?? {}).map(([k, v]) => (
                  <InfoRow key={k} label={k} value={String(v)} mono />
                ))}
              </Section>
            )}
            {Object.keys(lc.metadata.annotations ?? {}).filter(k => !k.startsWith('kubectl.kubernetes.io')).length > 0 && (
              <Section title="Annotations">
                {Object.entries(lc.metadata.annotations ?? {}).filter(([k]) => !k.startsWith('kubectl.kubernetes.io')).map(([k, v]) => (
                  <InfoRow key={k} label={k} value={String(v)} mono />
                ))}
              </Section>
            )}
          </>
        )}

        {tab === 'status' && (
          <>
            {bundle ? (
              <Section title="Compilation Bundle">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-muted-foreground">State</span>
                  <Badge variant="outline" className={cn('text-[10px]', getBundleColor(bundle.state))}>{bundle.state ?? 'unknown'}</Badge>
                </div>
                <InfoRow label="Compiler Version" value={bundle.compilerVersion} />
                <InfoRow label="Observed Generation" value={bundle.observedGeneration} />
                <InfoRow label="Location" value={bundle.location} mono />
                <InfoRow label="SHA256" value={bundle.sha256} mono />
              </Section>
            ) : (
              <p className="text-xs text-muted-foreground p-2">No bundle status yet — controller may still be compiling.</p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
