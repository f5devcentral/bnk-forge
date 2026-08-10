/**
 * A2AIRuleLibrary — Browse and preview A2A-aware iRules.
 *
 * Curated library of iRules for A2A traffic management with
 * syntax-highlighted code preview, event handler badges, and
 * copy-to-clipboard functionality.
 */

import { useState } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Code, Search, Copy, Check, Eye, Shield, Workflow,
  ChevronDown, ChevronRight,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { a2aIRules, type A2AIRule } from '@/data/a2a-irules';

// ---------------------------------------------------------------------------
// Category styling
// ---------------------------------------------------------------------------

const CATEGORY_META: Record<string, { icon: React.ElementType; label: string; color: string }> = {
  inspection: { icon: Eye, label: 'Inspection', color: 'text-info' },
  persistence: { icon: Workflow, label: 'Persistence', color: 'text-warning' },
  rewriting: { icon: Code, label: 'Rewriting', color: 'text-muted-foreground' },
  security: { icon: Shield, label: 'Security', color: 'text-success' },
};

// ---------------------------------------------------------------------------
// iRule Card
// ---------------------------------------------------------------------------

function IRuleCard({ irule }: { irule: A2AIRule }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const meta = CATEGORY_META[irule.category] || CATEGORY_META.inspection;
  const Icon = meta.icon;
  const lineCount = irule.code.split('\n').length;

  const handleCopy = async () => {
    await navigator.clipboard.writeText(irule.code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="rounded-lg border border-border bg-card transition-colors">
      <div className="p-4">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="flex-shrink-0 p-2 rounded-lg bg-muted">
              <Icon className={cn('h-5 w-5', meta.color)} />
            </div>
            <div className="min-w-0">
              <h3 className="font-medium text-sm">{irule.name}</h3>
              <div className="flex items-center gap-2 mt-1">
                <Badge variant="outline" className={cn('text-xs', meta.color)}>
                  {meta.label}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  {lineCount} lines
                </span>
              </div>
            </div>
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={handleCopy}
            className="h-7 px-2 text-xs gap-1 flex-shrink-0"
          >
            {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
            {copied ? 'Copied' : 'Copy'}
          </Button>
        </div>

        {/* Description */}
        <p className="text-sm mt-3 text-muted-foreground">
          {irule.description}
        </p>

        {/* Event handlers */}
        <div className="flex flex-wrap gap-1 mt-2">
          {irule.events.map(event => (
            <Badge key={event} variant="secondary" className="text-xs font-mono">{event}</Badge>
          ))}
        </div>
      </div>

      {/* Code Preview */}
      <div className="border-t border-border px-4 py-2">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1.5 text-xs font-medium w-full text-left text-muted-foreground hover:text-foreground/80"
        >
          {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          <Code className="h-3 w-3" />
          iRule Code
        </button>
        {expanded && (
          <pre className="mt-2 p-3 rounded text-xs overflow-x-auto max-h-[500px] bg-muted text-foreground/80">
            <code>{irule.code}</code>
          </pre>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

interface A2AIRuleLibraryProps {
  clusterId: number;
  namespace: string | undefined;
}

export function A2AIRuleLibrary({ clusterId: _clusterId, namespace: _namespace }: A2AIRuleLibraryProps) {
  const [search, setSearch] = useState('');

  const filtered = search
    ? a2aIRules.filter(r =>
        r.name.toLowerCase().includes(search.toLowerCase()) ||
        r.description.toLowerCase().includes(search.toLowerCase()) ||
        r.category.toLowerCase().includes(search.toLowerCase()) ||
        r.events.some(e => e.toLowerCase().includes(search.toLowerCase()))
      )
    : a2aIRules;

  return (
    <div className="space-y-4">
      {/* Search + count */}
      <div className="flex items-center justify-between gap-4">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search iRules..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9 h-9"
          />
        </div>
        <p className="text-sm text-muted-foreground">
          {filtered.length} iRule{filtered.length !== 1 ? 's' : ''}
        </p>
      </div>

      {/* iRule Cards */}
      <div className="space-y-4">
        {filtered.map(irule => (
          <IRuleCard key={irule.id} irule={irule} />
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="rounded-lg border border-border bg-muted/50 p-8 text-center">
          <Code className="h-10 w-10 mx-auto mb-3 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            No iRules match &quot;{search}&quot;
          </p>
        </div>
      )}
    </div>
  );
}
