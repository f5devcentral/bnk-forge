/**
 * HealthDetailCard — Expandable health card with explanations and actions.
 *
 * UX-009: Each BNK component gets a card that shows:
 *   - Collapsed: component name, status badge, one-line summary
 *   - Expanded:  WHY explanation, WHAT'S WRONG (pod issues), pod details, action buttons
 *
 * Warning/Critical cards auto-expand. Healthy cards stay collapsed.
 * Actions: View Logs, Restart Pod, Describe — use existing K8s hooks.
 *
 * D-020: token-pure surfaces; status colors via `text-success`/`text-warning`/
 * `text-destructive` rather than `text-emerald-*`/`text-amber-*`/`text-red-*`.
 */

import { useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
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
  ChevronDown,
  ChevronRight,
  ScrollText,
  RotateCcw,
  Eye,
  Info,
  AlertCircle,
  Container,
  MapPin,
  Server,
  Layers,
} from 'lucide-react';
import { useRestartPod } from '@/hooks/useK8s';
import { notify } from '@/lib/notify';
import type {
  HealthSeverity,
  HealthRemediationAction,
  HealthPodDetail,
} from '@/types';
import { SEVERITY_CONFIG } from '@/lib/health-severity';

// --- Severity config (shared via PLAT-REL-001 / UX-OPS-002) ---

const severityConfig = SEVERITY_CONFIG;

// --- Component props ---

interface HealthDetailCardProps {
  /** Display name for the component */
  name: string;
  /** Current severity */
  severity: HealthSeverity;
  /** Short summary line (e.g., "2/3 running") */
  summary: string;
  /** WHY this component matters */
  explanation: string;
  /** Pod-level details for expanded view */
  podDetails: HealthPodDetail[];
  /** Available remediation actions */
  remediationActions: HealthRemediationAction[];
  /** Namespaces the component's pods run in */
  namespaces: string[];
  /** Availability zones the component's pods run in */
  zones: string[];
  /** Node names the component's pods are scheduled on */
  nodes: string[];
  /** K8s cluster ID for API calls */
  clusterId: number;
  /** Additional content to render in the collapsed view */
  children?: React.ReactNode;
  /** Callback when View Logs action is triggered */
  onViewLogs?: (podName: string, namespace: string) => void;
  /** Callback when Describe action is triggered */
  onDescribe?: (podName: string, namespace: string) => void;
}

export function HealthDetailCard({
  name,
  severity,
  summary,
  explanation,
  podDetails,
  remediationActions,
  namespaces,
  zones,
  nodes,
  clusterId,
  children,
  onViewLogs,
  onDescribe,
}: HealthDetailCardProps) {
  const config = severityConfig[severity] || severityConfig.unknown;
  const StatusIcon = config.icon;

  // Auto-expand for warning/critical
  const [isOpen, setIsOpen] = useState(severity === 'warning' || severity === 'critical');
  const [restartTarget, setRestartTarget] = useState<{ podName: string; namespace: string } | null>(null);

  const restartPod = useRestartPod();

  const unhealthyPods = podDetails.filter(p => p.issue);
  const hasIssues = unhealthyPods.length > 0;

  const handleAction = useCallback((action: HealthRemediationAction) => {
    switch (action.action) {
      case 'view_logs':
        onViewLogs?.(action.target, action.namespace);
        break;
      case 'restart_pod':
        setRestartTarget({ podName: action.target, namespace: action.namespace });
        break;
      case 'describe':
        onDescribe?.(action.target, action.namespace);
        break;
      case 'diagnostics':
        // Diagnostics are available via the Diagnostics view on the BNK page
        break;
    }
  }, [onViewLogs, onDescribe]);

  const handleConfirmRestart = useCallback(() => {
    if (!restartTarget) return;
    restartPod.mutate(
      { clusterId, podName: restartTarget.podName, namespace: restartTarget.namespace },
      {
        onSuccess: () => {
          notify.success('Pod restarting...', `${restartTarget.podName} will be recreated by its controller`, { category: 'system' });
        },
        onError: () => {
          // useRestartPod already calls notifyError
        },
      },
    );
    setRestartTarget(null);
  }, [restartTarget, restartPod, clusterId]);

  // Border emphasis for outer card — only for warning/critical
  const outerBorderClass =
    severity === 'critical'
      ? 'border-destructive/30'
      : severity === 'warning'
        ? 'border-warning/30'
        : 'border-border';

  return (
    <>
      <Collapsible open={isOpen} onOpenChange={setIsOpen}>
        <div className={cn('rounded-lg border bg-card transition-colors', outerBorderClass)}>
          {/* Collapsed header — always visible */}
          <CollapsibleTrigger asChild>
            <button
              className="w-full flex items-center justify-between p-4 text-left rounded-lg transition-colors hover:bg-muted/50"
              aria-label={`${name} health: ${config.label}`}
            >
              <div className="flex items-center gap-3 min-w-0">
                <div className={cn('p-2 rounded-lg shrink-0', config.bg)}>
                  <StatusIcon className={cn('h-4 w-4', config.color)} />
                </div>
                <div className="min-w-0">
                  <h3 className="font-semibold text-sm text-foreground">{name}</h3>
                  <p className="text-xs truncate text-muted-foreground">
                    {summary}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0 ml-2">
                <Badge className={cn('gap-1 font-medium text-xs', config.bg, config.color, config.border)}>
                  <StatusIcon className="h-3 w-3" />
                  {config.label}
                </Badge>
                {isOpen
                  ? <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
              </div>
            </button>
          </CollapsibleTrigger>

          {/* Expanded content */}
          <CollapsibleContent>
            <div className="px-4 pb-4 space-y-3 border-t border-border">

              {/* WHY section */}
              {explanation && (
                <div className="flex items-start gap-2 mt-3 p-2.5 rounded-md bg-muted/50">
                  <Info className="h-4 w-4 shrink-0 mt-0.5 text-muted-foreground" />
                  <div>
                    <p className="text-xs font-medium mb-0.5 text-foreground">
                      Why this matters
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {explanation}
                    </p>
                  </div>
                </div>
              )}

              {/* WHAT'S WRONG section */}
              {hasIssues && (
                <div className={cn(
                  'p-2.5 rounded-md',
                  severity === 'critical' ? 'bg-destructive/10' : 'bg-warning/10',
                )}>
                  <div className="flex items-center gap-1.5 mb-2">
                    <AlertCircle className={cn(
                      'h-3.5 w-3.5',
                      severity === 'critical' ? 'text-destructive' : 'text-warning',
                    )} />
                    <span className={cn(
                      'text-xs font-medium',
                      severity === 'critical' ? 'text-destructive' : 'text-warning',
                    )}>
                      {unhealthyPods.length === 1 ? '1 issue detected' : `${unhealthyPods.length} issues detected`}
                    </span>
                  </div>
                  <div className="space-y-1.5">
                    {unhealthyPods.map((pod) => (
                      <div key={pod.podName} className="flex items-start gap-2">
                        <Container className="h-3 w-3 shrink-0 mt-0.5 text-muted-foreground" />
                        <div className="min-w-0">
                          <code className="text-xs font-mono block truncate text-foreground">
                            {pod.podName}
                          </code>
                          <span className={cn(
                            'text-xs',
                            severity === 'critical' ? 'text-destructive' : 'text-warning',
                          )}>
                            {pod.issue}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Pod details table */}
              {podDetails.length > 0 && (
                <div>
                  <p className="text-xs font-medium mb-1.5 text-muted-foreground">
                    Pod Details
                  </p>
                  <div className="rounded-md border border-border overflow-hidden">
                    <Table className="text-xs">
                      <TableHeader>
                        <TableRow>
                          <TableHead className="py-1.5 px-2 h-auto">Pod</TableHead>
                          <TableHead className="py-1.5 px-2 h-auto">Node</TableHead>
                          <TableHead className="py-1.5 px-2 h-auto">Zone</TableHead>
                          <TableHead className="py-1.5 px-2 h-auto">Type</TableHead>
                          <TableHead className="py-1.5 px-2 h-auto">Status</TableHead>
                          <TableHead className="py-1.5 px-2 h-auto text-right">Containers</TableHead>
                          <TableHead className="py-1.5 px-2 h-auto text-right">Restarts</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {podDetails.map((pod) => {
                          const phaseVariant: 'success' | 'info' | 'warning' =
                            pod.phase === 'Running' ? 'success' :
                            pod.phase === 'Succeeded' ? 'info' : 'warning';
                          return (
                            <TableRow key={pod.podName}>
                              <TableCell className="py-1.5 px-2">
                                <code className="font-mono truncate block max-w-[180px] text-foreground">
                                  {pod.podName}
                                </code>
                              </TableCell>
                              <TableCell className="py-1.5 px-2">
                                <span className="font-mono truncate block max-w-[120px] text-[10px] text-muted-foreground">
                                  {pod.nodeName || '--'}
                                </span>
                              </TableCell>
                              <TableCell className="py-1.5 px-2">
                                <span className="truncate block max-w-[100px] text-[10px] text-muted-foreground">
                                  {pod.nodeZone || '--'}
                                </span>
                              </TableCell>
                              <TableCell className="py-1.5 px-2">
                                <span className="truncate block max-w-[100px] text-[10px] text-muted-foreground">
                                  {pod.nodeInstanceType || '--'}
                                </span>
                              </TableCell>
                              <TableCell className="py-1.5 px-2">
                                <Badge variant={phaseVariant} className="text-[10px] px-1.5 py-0">
                                  {pod.phase}
                                </Badge>
                              </TableCell>
                              <TableCell className="py-1.5 px-2 text-right tabular-nums text-muted-foreground">
                                {pod.containersReady}
                              </TableCell>
                              <TableCell className={cn(
                                'py-1.5 px-2 text-right tabular-nums',
                                pod.restartCount > 5 ? 'text-warning font-medium' : 'text-muted-foreground',
                              )}>
                                {pod.restartCount}
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </div>
                </div>
              )}

              {/* Placement context */}
              {(namespaces.length > 0 || zones.length > 0 || nodes.length > 0) && (
                <div className="space-y-2">
                  {namespaces.length > 0 && (
                    <div>
                      <p className="text-xs font-medium mb-1.5 text-muted-foreground flex items-center gap-1.5">
                        <Layers className="h-3 w-3" />
                        Namespaces
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {namespaces.map((ns) => (
                          <Badge key={ns} variant="outline" className="text-[10px] font-normal">
                            {ns}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {zones.length > 0 && (
                    <div>
                      <p className="text-xs font-medium mb-1.5 text-muted-foreground flex items-center gap-1.5">
                        <MapPin className="h-3 w-3" />
                        Availability Zones
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {zones.map((zone) => (
                          <Badge key={zone} variant="outline" className="text-[10px] font-normal">
                            {zone}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {nodes.length > 0 && (
                    <div>
                      <p className="text-xs font-medium mb-1.5 text-muted-foreground flex items-center gap-1.5">
                        <Server className="h-3 w-3" />
                        Nodes
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {nodes.map((node) => (
                          <Badge key={node} variant="outline" className="text-[10px] font-normal">
                            {node}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Extra child content (StatRows, FeatureBadges, etc.) */}
              {children && (
                <div className="space-y-2">
                  {children}
                </div>
              )}

              {/* Action buttons */}
              {remediationActions.length > 0 && (
                <div className="flex flex-wrap gap-2 pt-1">
                  {remediationActions.map((action) => {
                    const actionIcons: Record<string, typeof ScrollText> = {
                      view_logs: ScrollText,
                      restart_pod: RotateCcw,
                      describe: Eye,
                      diagnostics: AlertTriangle,
                    };
                    const ActionIcon = actionIcons[action.action] || Eye;
                    const isDestructive = action.action === 'restart_pod';

                    return (
                      <Button
                        key={`${action.action}-${action.target}`}
                        variant="outline"
                        size="sm"
                        className={cn(
                          'h-7 text-xs gap-1.5',
                          isDestructive && 'text-warning hover:text-warning hover:border-warning/50',
                        )}
                        onClick={() => handleAction(action)}
                      >
                        <ActionIcon className="h-3 w-3" />
                        {action.label}
                      </Button>
                    );
                  })}
                </div>
              )}
            </div>
          </CollapsibleContent>
        </div>
      </Collapsible>

      {/* Restart Pod Confirmation Dialog */}
      <AlertDialog open={!!restartTarget} onOpenChange={(open) => { if (!open) setRestartTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Restart Pod</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to restart{' '}
              <code className="font-mono text-sm">{restartTarget?.podName}</code>?
              The pod will be deleted and recreated by its controller. This may cause brief service disruption.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmRestart}>
              Restart Pod
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
