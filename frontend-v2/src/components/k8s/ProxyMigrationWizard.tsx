/**
 * Proxy Migration Wizard (D-021 P3)
 *
 * Guided, gated, reversible proxy-to-BNK cutover stepper.
 *
 * Steps: Translate → Deploy BNK → Verify → Cut Over → Teardown Old Proxy
 *
 * Key UX decisions (per DECOMPOSITION.md):
 * - Verify result is shown distinctly: programmed + reachable dimensions
 * - Cutover is OPERATOR-CONFIRMED: forge does NOT flip DNS/VIP
 * - "deployed" ≠ "serving": verify panel shown before cutover is enabled
 * - Fail-closed: verify failed = hard stop with clear explanation
 */

import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  CheckCircle2,
  XCircle,
  Clock,
  Loader2,
  AlertTriangle,
  ShieldAlert,
  ArrowRight,
  Trash2,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  useProxyMigration,
  useConfirmCutover,
  useConfirmTeardown,
  useAbortMigration,
  migrationStatusLabel,
} from '@/hooks/useProxyMigration';
import type { ProxyMigrationStep, VerifyResult } from '@/types/proxy-migration';
import { MIGRATION_STEP_LABELS } from '@/types/proxy-migration';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ProxyMigrationWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clusterId: number;
  migrationId: number;
  /** Username of the current operator (for confirm actions). */
  currentUser?: string;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StepIcon({ status }: { status: string }) {
  switch (status) {
    case 'completed':
      return <CheckCircle2 className="h-4 w-4 text-success" />;
    case 'failed':
      return <XCircle className="h-4 w-4 text-destructive" />;
    case 'running':
      return <Loader2 className="h-4 w-4 animate-spin text-info" />;
    case 'awaiting_confirm':
      return <Clock className="h-4 w-4 text-warning" />;
    case 'blocked':
      return <ShieldAlert className="h-4 w-4 text-muted-foreground" />;
    default:
      return <Clock className="h-4 w-4 text-muted-foreground" />;
  }
}

function StepBadge({ status }: { status: string }) {
  const variants: Record<string, string> = {
    completed: 'bg-success/10 text-success border-success/20',
    failed: 'bg-destructive/10 text-destructive border-destructive/20',
    running: 'bg-info/10 text-info border-info/20',
    awaiting_confirm: 'bg-warning/10 text-warning border-warning/20',
    pending: 'bg-muted text-muted-foreground border-border',
    blocked: 'bg-muted text-muted-foreground border-border',
    skipped: 'bg-muted text-muted-foreground border-border',
  };
  const label: Record<string, string> = {
    completed: 'Completed',
    failed: 'Failed',
    running: 'Running',
    awaiting_confirm: 'Awaiting Confirm',
    pending: 'Pending',
    blocked: 'Blocked',
    skipped: 'Skipped',
  };
  return (
    <span
      className={cn(
        'rounded border px-2 py-0.5 text-xs font-medium',
        variants[status] ?? 'bg-muted text-muted-foreground'
      )}
    >
      {label[status] ?? status}
    </span>
  );
}

function VerifyPanel({ result }: { result: VerifyResult }) {
  return (
    <div className="space-y-2 rounded-lg border p-3 text-sm">
      <p className="font-medium">Verify Result</p>
      <div className="flex items-center gap-2">
        {result.programmed ? (
          <CheckCircle2 className="h-4 w-4 text-success" />
        ) : (
          <XCircle className="h-4 w-4 text-destructive" />
        )}
        <span>Control plane: {result.programmed ? 'Gateway programmed' : 'Not programmed'}</span>
      </div>
      <div className="flex items-center gap-2">
        {result.reachable ? (
          <CheckCircle2 className="h-4 w-4 text-success" />
        ) : (
          <XCircle className="h-4 w-4 text-destructive" />
        )}
        <span>Data plane: {result.reachable ? 'Backend reachable' : 'Backend not reachable'}</span>
      </div>
      {result.reasons.length > 0 && (
        <div className="mt-1">
          <p className="text-xs font-medium text-muted-foreground">Reasons:</p>
          <ul className="mt-1 list-disc pl-4 text-xs text-muted-foreground">
            {result.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function StepRow({ step }: { step: ProxyMigrationStep }) {
  const label =
    MIGRATION_STEP_LABELS[step.name as keyof typeof MIGRATION_STEP_LABELS] ?? step.name;
  return (
    <div className="flex items-start gap-3 py-2">
      <div className="mt-0.5">
        <StepIcon status={step.status} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{label}</span>
          <StepBadge status={step.status} />
        </div>
        <p className="text-xs text-muted-foreground">{step.description}</p>
        {step.error_message && (
          <p className="mt-1 text-xs text-destructive">{step.error_message}</p>
        )}
        {step.output_log && step.status === 'completed' && (
          <details className="mt-1">
            <summary className="cursor-pointer text-xs text-muted-foreground">Show output</summary>
            <pre className="mt-1 max-h-32 overflow-auto rounded bg-muted p-2 text-xs">
              {step.output_log}
            </pre>
          </details>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main wizard
// ---------------------------------------------------------------------------

export function ProxyMigrationWizard({
  open,
  onOpenChange,
  clusterId,
  migrationId,
  currentUser = 'operator',
}: ProxyMigrationWizardProps) {
  const { data: migration, isLoading } = useProxyMigration(clusterId, migrationId);
  const confirmCutoverMut = useConfirmCutover(clusterId, migrationId);
  const confirmTeardownMut = useConfirmTeardown(clusterId, migrationId);
  const abortMut = useAbortMigration(clusterId, migrationId);

  const [confirmingCutover, setConfirmingCutover] = useState(false);
  const [confirmingTeardown, setConfirmingTeardown] = useState(false);

  if (isLoading || !migration) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent>
          <div className="flex items-center gap-2 py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            <span className="text-sm text-muted-foreground">Loading migration...</span>
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  const proxyType =
    (migration.source_descriptor?.proxy_type as string | undefined) ?? 'proxy';
  const className =
    (migration.source_descriptor?.class_name as string | undefined) ?? '';
  const displayName = `${proxyType}${className ? `/${className}` : ''}`;

  const isTerminal = ['completed', 'failed', 'aborted', 'rolled_back'].includes(migration.status);
  const isVerifyFailed = migration.status === 'verify_failed';
  const isAwaitingCutover = migration.status === 'awaiting_cutover';
  const isAwaitingTeardown = migration.status === 'awaiting_teardown';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ArrowRight className="h-5 w-5 text-info" />
            Proxy Migration: {displayName}
          </DialogTitle>
          <DialogDescription>
            Guided cutover from existing proxy to BNK Gateway API.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Status badge */}
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">Status:</span>
            <Badge variant="outline">{migrationStatusLabel(migration.status)}</Badge>
            {migration.current_step && !isTerminal && (
              <span className="text-xs text-muted-foreground">
                (step: {migration.current_step})
              </span>
            )}
          </div>

          {/* Error message */}
          {migration.error_message && (
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>
                {isVerifyFailed ? 'Verify Failed — Old Proxy Still Serving' : 'Error'}
              </AlertTitle>
              <AlertDescription className="text-sm">{migration.error_message}</AlertDescription>
            </Alert>
          )}

          {/* Verify failed: hard-stop explanation */}
          {isVerifyFailed && (
            <Alert className="border-warning/20 bg-warning/10">
              <ShieldAlert className="h-4 w-4 text-warning" />
              <AlertTitle className="text-warning">Verify Gate: Hard Stop</AlertTitle>
              <AlertDescription className="text-sm text-warning">
                The BNK gateway did not pass the verify check. The old proxy is still serving
                traffic — <strong>no cutover or teardown was attempted</strong>. Review the
                gateway/route status, fix the issue, then restart the migration.
              </AlertDescription>
            </Alert>
          )}

          {/* Verify result panel */}
          {migration.verify_result && (
            <VerifyPanel result={migration.verify_result as VerifyResult} />
          )}

          {/* Steps */}
          <ScrollArea className="max-h-64 rounded-lg border">
            <div className="divide-y px-3">
              {migration.steps.map((step) => (
                <StepRow key={step.id} step={step} />
              ))}
            </div>
          </ScrollArea>

          {/* Cutover confirmation panel */}
          {isAwaitingCutover && (
            <Alert className="border-info/20 bg-info/10">
              <AlertTitle className="text-info">Ready for DNS/VIP Cutover</AlertTitle>
              <AlertDescription className="space-y-3 text-sm text-info">
                <p>
                  The BNK gateway is <strong>programmed and reachable</strong>. You must now
                  repoint DNS or your VIP to the new BNK gateway address{' '}
                  {migration.bnk_gateway_name && (
                    <code className="rounded bg-info/10 px-1">
                      {migration.bnk_gateway_name}
                    </code>
                  )}{' '}
                  — Forge cannot do this automatically.
                </p>
                <p className="text-xs text-info/70">
                  After completing the DNS/VIP change externally, click "Confirm Cutover" to
                  record it and proceed to teardown.
                </p>
                {confirmingCutover ? (
                  <div className="flex items-center gap-2">
                    <Alert variant="destructive" className="flex-1">
                      <AlertTitle>Are you sure?</AlertTitle>
                      <AlertDescription>
                        This records that traffic is now flowing to the BNK gateway. The old
                        proxy will be available for teardown.
                      </AlertDescription>
                    </Alert>
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        onClick={() => {
                          confirmCutoverMut.mutate({ confirmed_by: currentUser });
                          setConfirmingCutover(false);
                        }}
                        disabled={confirmCutoverMut.isPending}
                      >
                        {confirmCutoverMut.isPending && (
                          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                        )}
                        Yes, confirm
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setConfirmingCutover(false)}
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
                  <Button size="sm" onClick={() => setConfirmingCutover(true)}>
                    Confirm Cutover
                  </Button>
                )}
              </AlertDescription>
            </Alert>
          )}

          {/* Teardown confirmation panel */}
          {isAwaitingTeardown && (
            <Alert className="border-warning/20 bg-warning/10">
              <Trash2 className="h-4 w-4 text-warning" />
              <AlertTitle className="text-warning">Ready to Teardown Old Proxy</AlertTitle>
              <AlertDescription className="space-y-3 text-sm text-warning">
                <p>
                  Cutover is confirmed. You may now uninstall the old proxy. This action cannot
                  be undone.
                </p>
                {migration.teardown_info ? (
                  <p className="text-xs">
                    Teardown method:{' '}
                    {(migration.teardown_info.helm_release as string | undefined)
                      ? `Helm uninstall ${migration.teardown_info.helm_release}`
                      : 'kubectl delete'}
                  </p>
                ) : (
                  <p className="text-xs text-warning/70">
                    No teardown info available — teardown will be surfaced as a manual step.
                  </p>
                )}
                {confirmingTeardown ? (
                  <div className="flex items-center gap-2">
                    <Alert variant="destructive" className="flex-1">
                      <AlertTitle>Confirm teardown?</AlertTitle>
                      <AlertDescription>
                        This will uninstall/delete the old proxy. Ensure traffic is flowing
                        through the BNK gateway before proceeding.
                      </AlertDescription>
                    </Alert>
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        variant="destructive"
                        onClick={() => {
                          confirmTeardownMut.mutate({ confirmed_by: currentUser });
                          setConfirmingTeardown(false);
                        }}
                        disabled={confirmTeardownMut.isPending}
                      >
                        {confirmTeardownMut.isPending && (
                          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                        )}
                        Yes, teardown
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setConfirmingTeardown(false)}
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setConfirmingTeardown(true)}
                  >
                    Teardown Old Proxy
                  </Button>
                )}
              </AlertDescription>
            </Alert>
          )}
        </div>

        {/* Footer actions */}
        <div className="flex justify-between border-t pt-4">
          {!isTerminal && (
            <Button
              variant="outline"
              size="sm"
              className="text-destructive hover:text-destructive/80"
              onClick={() => abortMut.mutate({ by: currentUser })}
              disabled={abortMut.isPending}
            >
              {abortMut.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              Abort Migration
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
            className="ml-auto"
          >
            Close
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
