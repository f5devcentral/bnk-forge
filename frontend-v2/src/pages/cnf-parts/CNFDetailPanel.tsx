/**
 * CNF Detail Panel — read-only metadata + conditions + raw YAML view.
 *
 * Implements the FallbackDetail pattern (no resource-registry lookup,
 * no mutation actions — P2 is strictly read-only).
 *
 * Layout:
 * 1. Header: resource name + kind/namespace + close button
 * 2. Describe button (opens ResourceDescribeViewer in the parent)
 * 3. Metadata section: kind, namespace, age, apiVersion, labels, annotations
 * 4. Conditions section (if present)
 * 5. Raw YAML/JSON toggle view of the full object
 */

import { useState } from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { formatAge } from '@/lib/time-utils';
import { getSeverityConfig } from '@/lib/health-severity';
import { Eye, X, ChevronDown, ChevronRight } from 'lucide-react';
import { severityFromConditions } from './severity';
import type { K8sResource, K8sCondition } from '@/types/kubernetes';

interface CNFDetailPanelProps {
  resource: K8sResource;
  onClose: () => void;
  onDescribe: (resource: K8sResource) => void;
}

export function CNFDetailPanel({
  resource,
  onClose,
  onDescribe,
}: CNFDetailPanelProps) {
  const [rawView, setRawView] = useState<'yaml' | 'json' | null>(null);
  const [conditionsExpanded, setConditionsExpanded] = useState(true);

  const conditions = resource.status?.conditions as K8sCondition[] | undefined;
  const labels = resource.metadata?.labels as Record<string, string> | undefined;
  const annotations = resource.metadata?.annotations as Record<string, string> | undefined;

  // Raw JSON/YAML representation (simple JSON formatting; YAML omitted to avoid dep)
  const rawJson = JSON.stringify(resource, null, 2);

  return (
    <div className="h-full overflow-y-auto">
      <div className="p-4">
        {/* Header */}
        <div className="flex items-start justify-between mb-4">
          <div className="min-w-0 flex-1 mr-2">
            <h3 className="font-semibold text-base mb-1 truncate">{resource.metadata?.name}</h3>
            <p className="text-xs text-muted-foreground">
              {resource.kind} • {resource.metadata?.namespace || 'cluster-wide'}
            </p>
          </div>
          <Button variant="outline" size="sm" className="h-8 w-8 p-0 flex-shrink-0" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Describe action */}
        <div className="mb-4">
          <Button
            variant="outline"
            size="sm"
            className="h-9 text-xs w-full"
            onClick={() => onDescribe(resource)}
          >
            <Eye className="h-3.5 w-3.5 mr-1.5" />
            Describe
          </Button>
        </div>

        {/* Metadata */}
        <div className="p-3 rounded-lg mb-3 bg-muted/50">
          <h4 className="text-xs font-semibold mb-2">Metadata</h4>
          <div className="space-y-1.5 text-xs">
            <MetaRow label="Kind">
              <code className="font-mono text-foreground">
                {resource.kind}
              </code>
            </MetaRow>
            <MetaRow label="Namespace">
              <code className="font-mono text-foreground">
                {resource.metadata?.namespace || 'cluster-wide'}
              </code>
            </MetaRow>
            <MetaRow label="Age">
              <span className="text-foreground">
                {formatAge(resource.metadata?.creationTimestamp || '')}
              </span>
            </MetaRow>
            <MetaRow label="API Version">
              <code className="font-mono text-foreground">
                {resource.apiVersion}
              </code>
            </MetaRow>
          </div>
        </div>

        {/* Labels */}
        {labels && Object.keys(labels).length > 0 && (
          <div className="p-3 rounded-lg mb-3 bg-muted/50">
            <h4 className="text-xs font-semibold mb-2">Labels</h4>
            <div className="flex flex-wrap gap-1">
              {Object.entries(labels).map(([k, v]) => (
                <Badge key={k} variant="outline" className="text-[10px] font-mono h-5">
                  {k}={v}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* Annotations */}
        {annotations && Object.keys(annotations).length > 0 && (
          <div className="p-3 rounded-lg mb-3 bg-muted/50">
            <h4 className="text-xs font-semibold mb-2">
              Annotations
              <span className="ml-1 font-normal text-muted-foreground">
                ({Object.keys(annotations).length})
              </span>
            </h4>
            <div className="space-y-1 text-[10px] font-mono">
              {Object.entries(annotations).slice(0, 5).map(([k, v]) => (
                <div key={k} className="flex gap-1 min-w-0">
                  <span className="shrink-0 text-muted-foreground">{k}:</span>
                  <span className="truncate text-foreground">{v}</span>
                </div>
              ))}
              {Object.keys(annotations).length > 5 && (
                <span className="text-muted-foreground">
                  +{Object.keys(annotations).length - 5} more…
                </span>
              )}
            </div>
          </div>
        )}

        {/* Conditions */}
        {conditions && conditions.length > 0 && (
          <div className="p-3 rounded-lg mb-3 bg-muted/50">
            <button
              onClick={() => setConditionsExpanded((v) => !v)}
              className="w-full flex items-center gap-1.5 text-xs font-semibold mb-0"
            >
              {conditionsExpanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              Conditions
              <span className="font-normal ml-1 text-muted-foreground">
                ({conditions.length})
              </span>
            </button>
            {conditionsExpanded && (
              <div className="mt-2 space-y-1.5">
                {conditions.map((cond, i) => {
                  const severity = severityFromConditions([cond]);
                  const severityConfig = getSeverityConfig(severity);
                  const CondIcon = severityConfig.icon;
                  return (
                    <div key={`${cond.type}-${i}`} className="text-xs">
                      <div className="flex items-center gap-1.5">
                        <CondIcon className={cn('h-3 w-3 flex-shrink-0', severityConfig.color)} />
                        <span className="font-medium">{cond.type}</span>
                        <span className="text-muted-foreground">
                          {cond.status}
                        </span>
                      </div>
                      {cond.reason && (
                        <p className="ml-5 text-[10px] text-muted-foreground">
                          {cond.reason}
                        </p>
                      )}
                      {cond.message && (
                        <p className="ml-5 text-[10px] text-muted-foreground">
                          {cond.message}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* Raw object view */}
        <div className="p-3 rounded-lg bg-muted/50">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-xs font-semibold">Raw Object</h4>
            <div className="flex gap-1">
              <Button
                variant={rawView === 'json' ? 'default' : 'outline'}
                size="sm"
                className="h-6 text-[10px] px-2"
                onClick={() => setRawView(rawView === 'json' ? null : 'json')}
              >
                JSON
              </Button>
            </div>
          </div>
          {rawView === 'json' && (
            <pre className="text-[10px] font-mono overflow-auto max-h-64 p-2 rounded bg-background text-foreground">
              {rawJson}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MetaRow helper
// ---------------------------------------------------------------------------

function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between items-start gap-2">
      <span className="text-muted-foreground">{label}:</span>
      <div className="text-right max-w-[60%] overflow-hidden">{children}</div>
    </div>
  );
}
