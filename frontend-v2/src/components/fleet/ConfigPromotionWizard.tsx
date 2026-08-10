/**
 * ConfigPromotionWizard — 3-step wizard for cross-cluster config promotion (UX-013).
 *
 * Step 1: Pick Source and Target clusters
 * Step 2: Review Changes (dry_run result) — colored diff display
 * Step 3: Confirm & Apply — confirmation dialog, apply, success/failure state
 *
 * Reuses the diff display pattern from DriftDetailPanel (colored <pre> blocks).
 */

import { useState, useMemo } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
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
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { usePromoteConfig } from '@/hooks/useConfigPromotion';
import {
  ArrowRight,
  ArrowLeft,
  CheckCircle2,
  XCircle,
  RefreshCw,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Loader2,
  Upload,
  Check,
} from 'lucide-react';
import { SectionCard } from '@/components/ui/section-card';
import type { FleetOperatorHealth } from '@/types/fleet';
import type {
  PromotionChange,
  PromoteResponse,
  PromotionAction,
} from '@/types/config-promotion';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ConfigPromotionWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Available operators/clusters to select from */
  operators: FleetOperatorHealth[];
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

type WizardStep = 'select' | 'review' | 'result';

const ACTION_CONFIG: Record<
  PromotionAction,
  { label: string; badgeVariant: 'success' | 'warning' | 'destructive'; icon: string }
> = {
  add: {
    label: 'ADD',
    badgeVariant: 'success',
    icon: '+',
  },
  modify: {
    label: 'MODIFY',
    badgeVariant: 'warning',
    icon: '~',
  },
  remove: {
    label: 'REMOVE',
    badgeVariant: 'destructive',
    icon: '-',
  },
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Step indicator bar */
function StepIndicator({ currentStep }: { currentStep: WizardStep }) {
  const steps: Array<{ key: WizardStep; label: string }> = [
    { key: 'select', label: 'Select Clusters' },
    { key: 'review', label: 'Review Changes' },
    { key: 'result', label: 'Apply' },
  ];

  const stepIndex = steps.findIndex((s) => s.key === currentStep);

  return (
    <div className="flex items-center gap-3 flex-wrap">
      {steps.map((step, idx) => {
        const isActive = idx === stepIndex;
        const isDone = idx < stepIndex;
        return (
          <div
            key={step.key}
            className={cn(
              'flex items-center gap-2 text-xs',
              isActive ? 'text-foreground font-medium' : 'text-muted-foreground',
            )}
          >
            <span
              className={cn(
                'flex h-5 w-5 items-center justify-center rounded-full border text-[10px] font-semibold',
                isActive
                  ? 'border-primary bg-primary text-primary-foreground'
                  : isDone
                    ? 'border-primary/40 text-primary'
                    : 'border-border text-muted-foreground',
              )}
            >
              {isDone ? <Check className="h-3 w-3" /> : idx + 1}
            </span>
            {step.label}
          </div>
        );
      })}
    </div>
  );
}

/** Change row with optional expandable diff */
function ChangeRow({ change }: { change: PromotionChange }) {
  const [expanded, setExpanded] = useState(false);
  const cfg = ACTION_CONFIG[change.action];

  return (
    <div className="rounded-lg border border-border bg-card transition-colors">
      <button
        type="button"
        className="flex items-center justify-between w-full px-3 py-2.5 text-left"
        onClick={() => change.diff && setExpanded(!expanded)}
        disabled={!change.diff}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Badge
            variant={cfg.badgeVariant}
            className="text-[10px] font-bold px-1.5 py-0 flex-shrink-0"
          >
            {cfg.label}
          </Badge>
          <code className="font-mono text-xs truncate text-foreground/80">
            {change.kind}/{change.namespace ? `${change.namespace}/` : ''}
            {change.name}
          </code>
        </div>
        {change.diff && (
          <span className="flex-shrink-0 ml-2">
            {expanded ? (
              <ChevronUp className="w-3.5 h-3.5 text-muted-foreground" />
            ) : (
              <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
            )}
          </span>
        )}
      </button>

      {expanded && change.diff && (
        <div className="border-t border-border px-3 py-2">
          <DiffBlock diff={change.diff} />
        </div>
      )}
    </div>
  );
}

/**
 * Colored diff block — same pattern as DriftDetailPanel DiffView.
 * Lines starting with + are success, - are destructive, ~ are warning.
 */
function DiffBlock({ diff }: { diff: string }) {
  const lines = diff.split('\n');

  return (
    <pre className="rounded-md border border-border bg-muted/50 p-3 overflow-x-auto text-xs font-mono leading-relaxed">
      {lines.map((line, idx) => {
        let lineClass = 'text-muted-foreground';
        if (line.startsWith('+')) {
          lineClass = 'text-success bg-success/10';
        } else if (line.startsWith('-')) {
          lineClass = 'text-destructive bg-destructive/10';
        } else if (line.startsWith('~')) {
          lineClass = 'text-warning bg-warning/10';
        }
        return (
          <div key={idx} className={cn('px-2 -mx-2 rounded', lineClass)}>
            {line || ' '}
          </div>
        );
      })}
    </pre>
  );
}

// ---------------------------------------------------------------------------
// Step Components
// ---------------------------------------------------------------------------

/** Step 1: Select source + target clusters */
function SelectStep({
  operators,
  sourceId,
  targetId,
  onSourceChange,
  onTargetChange,
  onCompare,
  isComparing,
}: {
  operators: FleetOperatorHealth[];
  sourceId: number;
  targetId: number;
  onSourceChange: (id: number) => void;
  onTargetChange: (id: number) => void;
  onCompare: () => void;
  isComparing: boolean;
}) {
  // Only connected (non-offline) operators can be promoted from/to
  const available = operators.filter((op) => op.status !== 'offline');
  const canCompare =
    sourceId > 0 && targetId > 0 && sourceId !== targetId && !isComparing;

  const selectClass =
    'w-full rounded-md border border-border bg-card text-foreground px-3 py-2.5 text-sm';

  return (
    <SectionCard title="Cluster Selection">
      <p className="text-sm text-muted-foreground mb-5 -mt-3">
        Select the source cluster (to copy config FROM) and the target cluster
        (to apply config TO). Only connected clusters are shown.
      </p>

      <div className="space-y-3">
        <div>
          <label className="block text-xs font-medium mb-1.5 text-muted-foreground">
            Source Cluster (copy from)
          </label>
          <select
            value={sourceId}
            onChange={(e) => onSourceChange(Number(e.target.value))}
            className={selectClass}
          >
            <option value={0}>Select source cluster...</option>
            {available.map((op) => (
              <option
                key={op.operator_id}
                value={op.operator_id}
                disabled={op.operator_id === targetId}
              >
                {op.cluster_name}
                {op.bnk_version ? ` (BNK ${op.bnk_version})` : ''}
              </option>
            ))}
          </select>
        </div>

        <div className="flex justify-center">
          <ArrowRight className="w-5 h-5 text-muted-foreground" />
        </div>

        <div>
          <label className="block text-xs font-medium mb-1.5 text-muted-foreground">
            Target Cluster (apply to)
          </label>
          <select
            value={targetId}
            onChange={(e) => onTargetChange(Number(e.target.value))}
            className={selectClass}
          >
            <option value={0}>Select target cluster...</option>
            {available.map((op) => (
              <option
                key={op.operator_id}
                value={op.operator_id}
                disabled={op.operator_id === sourceId}
              >
                {op.cluster_name}
                {op.bnk_version ? ` (BNK ${op.bnk_version})` : ''}
              </option>
            ))}
          </select>
        </div>
      </div>

      {sourceId > 0 && targetId > 0 && sourceId === targetId && (
        <div className="rounded-md border border-warning/30 bg-warning/10 text-warning px-3 py-2.5 text-sm">
          <AlertTriangle className="w-4 h-4 inline mr-1.5" />
          Source and target must be different clusters.
        </div>
      )}

      <Button
        className="w-full mt-5"
        disabled={!canCompare}
        onClick={onCompare}
      >
        {isComparing ? (
          <>
            <Loader2 className="w-4 h-4 mr-1.5 animate-spin" />
            Comparing configs...
          </>
        ) : (
          <>
            <RefreshCw className="w-4 h-4 mr-1.5" />
            Compare Configs
          </>
        )}
      </Button>
    </SectionCard>
  );
}

/** Step 2: Review changes from dry_run */
function ReviewStep({
  result,
  onBack,
  onApply,
}: {
  result: PromoteResponse;
  onBack: () => void;
  onApply: () => void;
}) {
  const addCount = result.changes.filter((c) => c.action === 'add').length;
  const modifyCount = result.changes.filter(
    (c) => c.action === 'modify'
  ).length;
  const removeCount = result.changes.filter(
    (c) => c.action === 'remove'
  ).length;

  // Zero changes = in sync
  if (result.total_changes === 0) {
    return (
      <div className="space-y-5">
        <div className="rounded-lg border border-success/20 bg-success/5 px-4 py-8 text-center">
          <CheckCircle2 className="w-10 h-10 mx-auto mb-3 text-success" />
          <h3 className="text-lg font-semibold text-foreground">
            Clusters are in sync
          </h3>
          <p className="text-sm text-muted-foreground mt-1">
            No changes needed between{' '}
            <strong>{result.source_cluster.name}</strong> and{' '}
            <strong>{result.target_cluster.name}</strong>.
          </p>
        </div>
        <Button variant="outline" onClick={onBack} className="w-full">
          <ArrowLeft className="w-4 h-4 mr-1.5" />
          Back
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Summary bar */}
      <div className="flex items-center gap-3 p-3 rounded-lg border border-border bg-muted/50">
        <span className="text-sm font-medium text-foreground/80">
          {result.total_changes} change{result.total_changes !== 1 ? 's' : ''}{' '}
          to apply from{' '}
          <strong>{result.source_cluster.name}</strong>{' '}
          <ArrowRight className="w-3.5 h-3.5 inline" />{' '}
          <strong>{result.target_cluster.name}</strong>
        </span>
      </div>

      {/* Change count badges */}
      <div className="flex items-center gap-2">
        {addCount > 0 && (
          <Badge variant="success">+{addCount} add</Badge>
        )}
        {modifyCount > 0 && (
          <Badge variant="warning">~{modifyCount} modify</Badge>
        )}
        {removeCount > 0 && (
          <Badge variant="destructive">-{removeCount} remove</Badge>
        )}
      </div>

      {/* Change list */}
      <div className="space-y-1.5 max-h-64 overflow-y-auto">
        {result.changes.map((change, idx) => (
          <ChangeRow key={idx} change={change} />
        ))}
      </div>

      {/* Removal note */}
      {removeCount > 0 && (
        <div className="rounded-md border border-border bg-muted/50 text-muted-foreground px-3 py-2 text-xs">
          <AlertTriangle className="w-3.5 h-3.5 inline mr-1" />
          Removals are skipped during promotion for safety. Only additions and
          modifications will be applied.
        </div>
      )}

      {/* Action buttons */}
      <div className="flex items-center gap-2 pt-2">
        <Button variant="outline" onClick={onBack} className="flex-1">
          <ArrowLeft className="w-4 h-4 mr-1.5" />
          Back
        </Button>
        <Button onClick={onApply} className="flex-1">
          <Upload className="w-4 h-4 mr-1.5" />
          Apply {addCount + modifyCount} Changes
        </Button>
      </div>
    </div>
  );
}

/** Step 3: Result after apply */
function ResultStep({
  result,
  onClose,
}: {
  result: PromoteResponse;
  onClose: () => void;
}) {
  const applyResults = result.apply_results;
  const appliedCount = applyResults?.applied?.length ?? 0;
  const failedCount = applyResults?.failed?.length ?? 0;
  const skippedCount = applyResults?.skipped?.length ?? 0;
  const isSuccess = failedCount === 0;

  return (
    <div className="space-y-4">
      {/* Success/Failure banner */}
      <div
        className={cn(
          'rounded-lg border px-4 py-6 text-center',
          isSuccess
            ? 'border-success/20 bg-success/5'
            : 'border-destructive/20 bg-destructive/5'
        )}
      >
        {isSuccess ? (
          <CheckCircle2 className="w-10 h-10 mx-auto mb-3 text-success" />
        ) : (
          <XCircle className="w-10 h-10 mx-auto mb-3 text-destructive" />
        )}
        <h3 className="text-lg font-semibold text-foreground">
          {isSuccess ? 'Changes Applied Successfully' : 'Some Changes Failed'}
        </h3>
        <p className="text-sm text-muted-foreground mt-1">
          {result.source_cluster.name} → {result.target_cluster.name}
        </p>
      </div>

      {/* Result summary */}
      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="rounded-lg border border-border bg-muted/50 px-3 py-2">
          <div className="text-lg font-bold text-success">
            {appliedCount}
          </div>
          <div className="text-[10px] text-muted-foreground">Applied</div>
        </div>
        <div className="rounded-lg border border-border bg-muted/50 px-3 py-2">
          <div className="text-lg font-bold text-destructive">{failedCount}</div>
          <div className="text-[10px] text-muted-foreground">Failed</div>
        </div>
        <div className="rounded-lg border border-border bg-muted/50 px-3 py-2">
          <div className="text-lg font-bold text-muted-foreground">{skippedCount}</div>
          <div className="text-[10px] text-muted-foreground">Skipped</div>
        </div>
      </div>

      {/* Failed details */}
      {failedCount > 0 && applyResults?.failed && (
        <div className="space-y-1">
          <h4 className="text-xs font-semibold text-foreground/80">
            Failed Resources
          </h4>
          {applyResults.failed.map((f, idx) => (
            <div
              key={idx}
              className="rounded-md border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs"
            >
              <code className="text-destructive">
                {f.kind}/{f.namespace ? `${f.namespace}/` : ''}
                {f.name}
              </code>
              <div className="text-muted-foreground mt-0.5">{f.error}</div>
            </div>
          ))}
        </div>
      )}

      <Button onClick={onClose} className="w-full">
        Done
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export function ConfigPromotionWizard({
  open,
  onOpenChange,
  operators,
}: ConfigPromotionWizardProps) {
  const promoteMutation = usePromoteConfig();

  // Wizard state
  const [step, setStep] = useState<WizardStep>('select');
  const [sourceId, setSourceId] = useState<number>(0);
  const [targetId, setTargetId] = useState<number>(0);
  const [dryRunResult, setDryRunResult] = useState<PromoteResponse | null>(
    null
  );
  const [applyResult, setApplyResult] = useState<PromoteResponse | null>(null);
  const [showConfirm, setShowConfirm] = useState(false);

  // Cluster name for display in confirmation/applying states
  const targetName = useMemo(
    () => operators.find((o) => o.operator_id === targetId)?.cluster_name ?? '',
    [operators, targetId]
  );

  // Actionable change count (excludes removals which are skipped)
  const actionableCount = useMemo(() => {
    if (!dryRunResult) return 0;
    return dryRunResult.changes.filter((c) => c.action !== 'remove').length;
  }, [dryRunResult]);

  const handleCompare = () => {
    promoteMutation.mutate(
      {
        sourceClusterId: sourceId,
        targetClusterId: targetId,
        dryRun: true,
      },
      {
        onSuccess: (data) => {
          setDryRunResult(data);
          setStep('review');
        },
      }
    );
  };

  const handleApplyClick = () => {
    setShowConfirm(true);
  };

  const handleConfirmApply = () => {
    setShowConfirm(false);
    promoteMutation.mutate(
      {
        sourceClusterId: sourceId,
        targetClusterId: targetId,
        dryRun: false,
      },
      {
        onSuccess: (data) => {
          setApplyResult(data);
          setStep('result');
        },
      }
    );
  };

  const handleClose = () => {
    // Reset state
    setStep('select');
    setSourceId(0);
    setTargetId(0);
    setDryRunResult(null);
    setApplyResult(null);
    setShowConfirm(false);
    promoteMutation.reset();
    onOpenChange(false);
  };

  const handleBack = () => {
    setDryRunResult(null);
    setStep('select');
    promoteMutation.reset();
  };

  return (
    <>
      <Dialog open={open} onOpenChange={handleClose}>
        <DialogContent className="sm:max-w-xl max-h-[85vh] overflow-y-auto bg-card border-border">
          <DialogHeader>
            <DialogTitle className="text-xl font-bold">
              Promote Config
            </DialogTitle>
            <DialogDescription>
              Export config from one cluster and apply it to another.
            </DialogDescription>
          </DialogHeader>

          <StepIndicator currentStep={step} />

          {/* Step 1: Select */}
          {step === 'select' && (
            <SelectStep
              operators={operators}
              sourceId={sourceId}
              targetId={targetId}
              onSourceChange={setSourceId}
              onTargetChange={setTargetId}
              onCompare={handleCompare}
              isComparing={promoteMutation.isPending}
            />
          )}

          {/* Step 2: Review */}
          {step === 'review' && dryRunResult && (
            <ReviewStep
              result={dryRunResult}
              onBack={handleBack}
              onApply={handleApplyClick}
            />
          )}

          {/* Applying spinner */}
          {step === 'review' && promoteMutation.isPending && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin text-primary mr-2" />
              <span className="text-sm text-muted-foreground">
                Applying changes to {targetName}...
              </span>
            </div>
          )}

          {/* Step 3: Result */}
          {step === 'result' && applyResult && (
            <ResultStep
              result={applyResult}
              onClose={handleClose}
            />
          )}

          {/* Error state */}
          {promoteMutation.isError && step !== 'result' && (
            <div className="rounded-md border border-destructive/30 bg-destructive/10 text-destructive px-3 py-2.5 text-sm">
              <XCircle className="w-4 h-4 inline mr-1.5" />
              {promoteMutation.error?.message || 'An error occurred'}
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Confirmation AlertDialog */}
      <AlertDialog open={showConfirm} onOpenChange={setShowConfirm}>
        <AlertDialogContent className="bg-card border-border">
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm Config Promotion</AlertDialogTitle>
            <AlertDialogDescription>
              This will apply{' '}
              <strong className="text-foreground">{actionableCount}</strong>{' '}
              change{actionableCount !== 1 ? 's' : ''} to{' '}
              <strong className="text-foreground">{targetName}</strong>.
              This action cannot be undone (but a snapshot will be created
              automatically before apply operations).
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmApply}>
              Apply Changes
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
