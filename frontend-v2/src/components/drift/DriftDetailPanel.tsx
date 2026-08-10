/**
 * DriftDetailPanel — Side-by-side / unified diff view for drifted resources.
 *
 * Shows:
 *   - Header: module name, resource type, last drift check time, severity
 *   - Resource changes with colored diff lines (success = add, destructive = remove, warning = change)
 *   - Action buttons: [Reconcile] [Accept Changes] [View Full Module]
 *
 * Uses simple styled <pre> blocks for diff rendering — no heavy diff libraries.
 */

import { useState } from 'react';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  GitCompare,
  Loader2,
  RefreshCw,
  Shield,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useDriftCheck } from '@/hooks/useDrift';
import { useApplyModule } from '@/hooks/useModules';
import { formatTimeAgo } from '@/lib/time-utils';
import { useNavigate } from 'react-router-dom';
import type { DriftCheck } from '@/types';

// ============================================================================
// Types
// ============================================================================

interface DriftDetailPanelProps {
  /** Drift check ID to display */
  checkId?: number;
  /** Alternatively, pass the drift check data directly */
  driftCheck?: DriftCheck;
  /** Project ID (for navigation) */
  projectId?: number;
  /** Module ID (for reconcile action) */
  moduleId?: number;
  /** Callback when reconcile is triggered */
  onReconcile?: () => void;
  /** Compact mode — less padding, no card wrapper */
  compact?: boolean;
}

// ============================================================================
// Sub-components
// ============================================================================

type ActionVariant = 'success' | 'warning' | 'destructive' | 'muted' | 'info';

const ACTION_CONFIG: Record<string, { label: string; variant: ActionVariant }> = {
  create: { label: 'Add', variant: 'success' },
  update: { label: 'Change', variant: 'warning' },
  delete: { label: 'Destroy', variant: 'destructive' },
  'no-op': { label: 'No-op', variant: 'muted' },
  read: { label: 'Read', variant: 'info' },
};

function normalizeAction(action: string): string {
  return action.toLowerCase().replace('~', 'update').replace('+', 'create').replace('-', 'delete');
}

/** Simple diff view — shows drift summary text as colored lines */
function DiffView({ summary }: { summary: string }) {
  const lines = summary.split('\n');

  return (
    <pre className="rounded-lg border border-border p-4 overflow-x-auto text-xs font-mono leading-relaxed bg-muted/50">
      {lines.map((line, idx) => {
        let lineClass = 'text-muted-foreground';
        if (line.startsWith('+') || line.startsWith('> ')) {
          lineClass = 'text-success bg-success/10';
        } else if (line.startsWith('-') || line.startsWith('< ')) {
          lineClass = 'text-destructive bg-destructive/10';
        } else if (line.startsWith('~') || line.startsWith('!')) {
          lineClass = 'text-warning bg-warning/10';
        } else if (line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++')) {
          lineClass = 'text-info';
        }
        return (
          <div key={idx} className={cn('px-2 -mx-2 rounded', lineClass)}>
            {line || ' '}
          </div>
        );
      })}
    </pre>
  );
}

// ============================================================================
// Main Component
// ============================================================================

export function DriftDetailPanel({
  checkId,
  driftCheck: externalCheck,
  projectId,
  moduleId,
  onReconcile,
  compact = false,
}: DriftDetailPanelProps) {
  const navigate = useNavigate();
  const [showAllResources, setShowAllResources] = useState(false);

  // Fetch drift check data if checkId provided (otherwise use external data)
  const { data: fetchedCheck, isLoading } = useDriftCheck(
    externalCheck ? undefined : checkId
  );
  const check = externalCheck || fetchedCheck;

  // Reconcile action — triggers apply on the module
  const applyModule = useApplyModule();

  const handleReconcile = () => {
    if (moduleId) {
      applyModule.mutate({ moduleId, autoApprove: true });
      onReconcile?.();
    }
  };

  // Loading state
  if (isLoading && !check) {
    if (compact) {
      return (
        <div className="space-y-4">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      );
    }
    return (
      <SectionCard>
        <div className="space-y-4">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      </SectionCard>
    );
  }

  if (!check) {
    return (
      <div className="text-center py-12 rounded-xl border-2 border-dashed border-border bg-muted/50">
        <GitCompare className="h-10 w-10 mx-auto mb-3 opacity-30 text-muted-foreground" />
        <p className="font-medium text-sm text-foreground/80">
          No drift check selected
        </p>
        <p className="text-xs mt-1 text-muted-foreground">
          Select a drift check to view details
        </p>
      </div>
    );
  }

  const details = check.drift_details;
  const resourceChanges = details?.resource_changes;
  const changedResources = details?.changed_resources || [];
  const totalChanges = resourceChanges
    ? resourceChanges.add + resourceChanges.change + resourceChanges.destroy
    : 0;
  const severity = totalChanges >= 5 ? 'high' : totalChanges >= 2 ? 'medium' : 'low';
  const severityVariant: Record<'high' | 'medium' | 'low', { label: string; variant: 'destructive' | 'warning' | 'success' }> = {
    high: { label: 'High', variant: 'destructive' },
    medium: { label: 'Medium', variant: 'warning' },
    low: { label: 'Low', variant: 'success' },
  };

  const displayResources = showAllResources ? changedResources : changedResources.slice(0, 10);

  const effectiveProjectId = projectId || check.project_id;
  const effectiveModuleId = moduleId || check.module_id;

  const content = (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <h3 className="font-semibold text-base text-foreground">
              {check.module_name || 'Module'}
            </h3>
            <Badge variant={severityVariant[severity].variant} className="text-xs">
              {severityVariant[severity].label} Severity
            </Badge>
          </div>
          <div className="flex items-center gap-3">
            {check.last_check_at && (
              <span className="text-xs flex items-center gap-1 text-muted-foreground">
                <Clock className="h-3 w-3" />
                Last checked {formatTimeAgo(check.last_check_at)}
              </span>
            )}
            {check.drift_detected ? (
              <Badge variant="destructive" className="text-xs gap-1">
                <AlertTriangle className="h-3 w-3" />
                Drift Detected
              </Badge>
            ) : (
              <Badge variant="success" className="text-xs gap-1">
                <CheckCircle2 className="h-3 w-3" />
                No Drift
              </Badge>
            )}
          </div>
        </div>
      </div>

      {/* Resource Change Summary */}
      {resourceChanges && totalChanges > 0 && (
        <div className="flex items-center gap-4 p-3 rounded-lg border border-border bg-muted/50">
          <span className="text-sm font-medium text-foreground/80">
            {totalChanges} resource{totalChanges !== 1 ? 's' : ''} affected
          </span>
          <div className="flex items-center gap-2">
            {resourceChanges.add > 0 && (
              <Badge variant="success">
                +{resourceChanges.add} to add
              </Badge>
            )}
            {resourceChanges.change > 0 && (
              <Badge variant="warning">
                ~{resourceChanges.change} to change
              </Badge>
            )}
            {resourceChanges.destroy > 0 && (
              <Badge variant="destructive">
                -{resourceChanges.destroy} to destroy
              </Badge>
            )}
          </div>
        </div>
      )}

      {/* Changed Resources Table */}
      {changedResources.length > 0 && (
        <div>
          <h4 className="text-sm font-semibold mb-2 text-foreground/80">
            Changed Resources
          </h4>
          <div className="border border-border rounded-md overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
                    Resource
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground text-right">
                    Action
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {displayResources.map((resource, idx) => {
                  const config =
                    ACTION_CONFIG[normalizeAction(resource.action)] || ACTION_CONFIG['update'];
                  return (
                    <TableRow key={idx}>
                      <TableCell>
                        <code className="font-mono text-xs break-all text-foreground/80">
                          {resource.address}
                        </code>
                      </TableCell>
                      <TableCell className="text-right">
                        <Badge variant={config.variant} className="text-xs">
                          {config.label}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          {changedResources.length > 10 && !showAllResources && (
            <Button
              variant="outline"
              size="sm"
              className="mt-2 w-full"
              onClick={() => setShowAllResources(true)}
            >
              Show {changedResources.length - 10} more resources
            </Button>
          )}
        </div>
      )}

      {/* Drift Summary / Diff Text */}
      {check.drift_summary && (
        <div>
          <h4 className="text-sm font-semibold mb-2 text-foreground/80">
            Drift Summary
          </h4>
          <DiffView summary={check.drift_summary} />
        </div>
      )}

      {/* Action Buttons */}
      <div className="flex items-center gap-2 pt-3 border-t border-border">
        {effectiveModuleId && (
          <Button
            variant="outline"
            size="sm"
            onClick={handleReconcile}
            disabled={applyModule.isPending}
          >
            {applyModule.isPending ? (
              <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
            )}
            Reconcile
          </Button>
        )}
        {effectiveProjectId && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => navigate(`/projects/${effectiveProjectId}?tab=drift`)}
          >
            <Shield className="h-3.5 w-3.5 mr-1.5" />
            View Full Module
          </Button>
        )}
      </div>
    </div>
  );

  if (compact) {
    return content;
  }

  return <SectionCard title="Drift Details">{content}</SectionCard>;
}
