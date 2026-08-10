/**
 * A2ATemplates — Pre-built Gateway API + iRule configurations for A2A patterns.
 *
 * Browse, preview, and copy ready-to-deploy Kubernetes manifests for
 * A2A traffic patterns: Gateway routing, session persistence, URL rewriting,
 * JWT validation, and JSON-RPC inspection.
 */

import { useState } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Globe, Code, Shield, Copy, Check, ChevronDown, ChevronRight,
  FileText,
} from 'lucide-react';
import { a2aTemplates, type A2ATemplate } from '@/data/a2a-templates';

// ---------------------------------------------------------------------------
// Category icons & labels
// ---------------------------------------------------------------------------

const CATEGORY_META: Record<string, { icon: React.ElementType; label: string; color: string }> = {
  gateway: { icon: Globe, label: 'Gateway Config', color: 'text-info' },
  irule: { icon: Code, label: 'iRule', color: 'text-warning' },
  security: { icon: Shield, label: 'Security', color: 'text-success' },
};

// ---------------------------------------------------------------------------
// Template Card
// ---------------------------------------------------------------------------

function TemplateCard({ template }: { template: A2ATemplate }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const meta = CATEGORY_META[template.category] || CATEGORY_META.irule;
  const Icon = meta.icon;

  const handleCopy = async () => {
    await navigator.clipboard.writeText(template.yaml);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="rounded-lg border border-border bg-card transition-colors">
      {/* Header */}
      <div className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="flex-shrink-0 p-2 rounded-lg bg-muted">
              <Icon className={cn('h-5 w-5', meta.color)} />
            </div>
            <div className="min-w-0">
              <h3 className="font-medium text-sm">{template.name}</h3>
              <Badge variant="outline" className={cn('text-xs mt-1', meta.color)}>
                {meta.label}
              </Badge>
            </div>
          </div>
        </div>
        <p className="text-sm mt-3 text-muted-foreground">
          {template.description}
        </p>
        <div className="flex flex-wrap gap-1 mt-2">
          {template.tags.map(tag => (
            <Badge key={tag} variant="secondary" className="text-xs">{tag}</Badge>
          ))}
        </div>
      </div>

      {/* YAML Preview Toggle */}
      <div className="border-t border-border px-4 py-2">
        <div className="flex items-center justify-between">
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground/80"
          >
            {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
            <FileText className="h-3 w-3" />
            YAML Manifest
          </button>
          <Button
            size="sm"
            variant="ghost"
            onClick={handleCopy}
            className="h-7 px-2 text-xs gap-1"
          >
            {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
            {copied ? 'Copied' : 'Copy'}
          </Button>
        </div>
        {expanded && (
          <pre className="mt-2 p-3 rounded text-xs overflow-x-auto max-h-96 bg-muted text-foreground/80">
            <code>{template.yaml}</code>
          </pre>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

interface A2ATemplatesProps {
  clusterId: number;
  namespace: string | undefined;
}

export function A2ATemplates({ clusterId: _clusterId, namespace: _namespace }: A2ATemplatesProps) {
  const [filter, setFilter] = useState<string>('all');

  const filtered = filter === 'all'
    ? a2aTemplates
    : a2aTemplates.filter(t => t.category === filter);

  return (
    <div className="space-y-4">
      {/* Filter Tabs */}
      <div className="flex items-center gap-2">
        {[
          { key: 'all', label: 'All Templates', count: a2aTemplates.length },
          { key: 'gateway', label: 'Gateway', count: a2aTemplates.filter(t => t.category === 'gateway').length },
          { key: 'irule', label: 'iRule', count: a2aTemplates.filter(t => t.category === 'irule').length },
          { key: 'security', label: 'Security', count: a2aTemplates.filter(t => t.category === 'security').length },
        ].map(tab => (
          <button
            key={tab.key}
            onClick={() => setFilter(tab.key)}
            className={cn(
              'px-3 py-1.5 rounded-md text-xs font-medium transition-colors',
              filter === tab.key
                ? 'bg-secondary text-secondary-foreground'
                : 'text-muted-foreground hover:text-foreground/80 hover:bg-muted',
            )}
          >
            {tab.label} ({tab.count})
          </button>
        ))}
      </div>

      {/* Template Cards */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {filtered.map(template => (
          <TemplateCard key={template.id} template={template} />
        ))}
      </div>
    </div>
  );
}
