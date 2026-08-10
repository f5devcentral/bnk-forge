/**
 * BNK Upgrade Panel — Sprint 8.2
 *
 * Manages BNK platform upgrades with:
 *   - Current version display + health status
 *   - Version picker (known FLO versions)
 *   - Pre-upgrade validation results
 *   - Upgrade plan review
 *   - Live execution progress with step tracking
 *   - Rollback button for failed upgrades
 *   - Upgrade history
 */

import { useState, useCallback } from 'react';
import type { AxiosError } from 'axios';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  ArrowUpCircle,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Clock,
  Loader2,
  RotateCcw,
  Play,
  ChevronDown,
  ChevronRight,
  Shield,
  Server,
  Network,
  History,
  Info,
} from 'lucide-react';
import {
  useBnkUpgradeVersions,
  useBnkCurrentVersion,
  useBnkUpgradeHistory,
  useBnkUpgradeDetail,
  useCreateBnkUpgradePlan,
  useExecuteBnkUpgrade,
  useRollbackBnkUpgrade,
  useCancelBnkUpgrade,
} from '@/hooks/useK8s';
import type { BnkUpgrade, BnkUpgradePreCheck, BnkUpgradePlanStep } from '@/types';

interface BNKUpgradePanelProps {
  clusterId: number;
}

export function BNKUpgradePanel({ clusterId }: BNKUpgradePanelProps) {
  const [selectedVersion, setSelectedVersion] = useState<string>('');
  const [activeUpgradeId, setActiveUpgradeId] = useState<number | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  // Queries
  const { data: versionData, isLoading: versionsLoading, isError: versionsError } = useBnkUpgradeVersions(clusterId);
  const { data: currentVersion, isLoading: currentVersionLoading, isError: currentVersionError } = useBnkCurrentVersion(clusterId, { pollingEnabled: true });
  const { data: historyData } = useBnkUpgradeHistory(clusterId);
  const { data: activeUpgrade } = useBnkUpgradeDetail(
    clusterId,
    activeUpgradeId,
    { pollingEnabled: activeUpgradeId !== null },
  ) as { data: BnkUpgrade | undefined };

  // Mutations
  const createPlan = useCreateBnkUpgradePlan();
  const executeUpgrade = useExecuteBnkUpgrade();
  const rollbackUpgrade = useRollbackBnkUpgrade();
  const cancelUpgrade = useCancelBnkUpgrade();

  const isUpgradeActive = activeUpgrade && ['in_progress', 'health_check', 'rolling_back'].includes(activeUpgrade.status);

  // Extract UPGRADE_IN_PROGRESS details from a createPlan error (if present).
  const planErrorCode = (createPlan.error as AxiosError<{ error?: { code?: string; details?: { upgrade_id?: number } } }> | null)
    ?.response?.data?.error?.code;
  const planErrorUpgradeId = (createPlan.error as AxiosError<{ error?: { code?: string; details?: { upgrade_id?: number } } }> | null)
    ?.response?.data?.error?.details?.upgrade_id;

  const handleCancelRunning = useCallback(() => {
    if (!planErrorUpgradeId) return;
    cancelUpgrade.mutate(
      { clusterId, upgradeId: planErrorUpgradeId },
      {
        onSuccess: () => {
          setActiveUpgradeId(null);
          createPlan.reset();
        },
      },
    );
  }, [cancelUpgrade, clusterId, planErrorUpgradeId, createPlan]);

  // ================================================================
  // Current Version Card
  // ================================================================
  const renderCurrentVersion = () => {
    if (currentVersionLoading) {
      return (
        <div className="rounded-lg border p-4 bg-card border-border">
          <div className="flex items-center gap-2 py-3">
            <Loader2 className="h-4 w-4 animate-spin text-info" />
            <span className="text-sm text-muted-foreground">Detecting BNK installation...</span>
          </div>
        </div>
      );
    }

    if (currentVersionError) {
      return (
        <div className="rounded-lg border p-4 bg-destructive/10 border-destructive/20">
          <div className="flex items-center gap-2">
            <XCircle className="h-5 w-5 text-destructive" />
            <span className="text-sm font-medium text-destructive">
              Failed to detect BNK installation. Check that the cluster is reachable.
            </span>
          </div>
        </div>
      );
    }

    if (!currentVersion) return null;

    const healthKey = (currentVersion.health || '') as string;
    const healthColorMap: Record<string, string> = {
      healthy: 'text-success',
      degraded: 'text-warning',
      critical: 'text-destructive',
    };
    const healthColor = healthColorMap[healthKey] || 'text-muted-foreground';

    const healthBgMap: Record<string, string> = {
      healthy: 'bg-success/10 border-success/20',
      degraded: 'bg-warning/10 border-warning/20',
      critical: 'bg-destructive/10 border-destructive/20',
    };
    const healthBg = healthBgMap[healthKey] || 'bg-card border-border';

    const gaLabel = currentVersion.ga_label;

    return (
      <div className={cn('rounded-lg border p-4', healthBg)}>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Server className="h-5 w-5 text-info" />
            <h3 className="font-semibold text-foreground">
              Current Installation
              {gaLabel && <span className="ml-2 text-info">{gaLabel}</span>}
            </h3>
          </div>
          <Badge variant="outline" className={healthColor}>
            {currentVersion.health || currentVersion.status || 'unknown'}
          </Badge>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div>
            <span className="text-muted-foreground">Status</span>
            <p className="font-medium text-foreground">
              {currentVersion.status === 'installed' ? '✅ Installed' : currentVersion.status}
            </p>
          </div>
          <div>
            <span className="text-muted-foreground">TMM Pods</span>
            <p className="font-medium text-foreground">
              {currentVersion.tmm_pods
                ? `${currentVersion.tmm_pods.running || 0}/${currentVersion.tmm_pods.pods || 0} running`
                : 'N/A'}
            </p>
          </div>
          <div>
            <span className="text-muted-foreground">VLANs</span>
            <p className="font-medium text-foreground">
              {currentVersion.vlans
                ? `${currentVersion.vlans.filter(v => v.programmed).length}/${currentVersion.vlans.length} programmed`
                : 'N/A'}
            </p>
          </div>
          <div>
            <span className="text-muted-foreground">FLO Chart</span>
            <p className="font-mono text-xs text-muted-foreground">
              {currentVersion.flo_version || 'Not detected'}
            </p>
          </div>
          {(currentVersion.min_k8s || currentVersion.max_k8s) && (
            <div>
              <span className="text-muted-foreground">K8s Support</span>
              <p className="font-medium text-xs text-foreground">
                {currentVersion.min_k8s}–{currentVersion.max_k8s}
              </p>
            </div>
          )}
        </div>
      </div>
    );
  };

  // ================================================================
  // Version Picker
  // ================================================================
  const renderVersionPicker = () => {
    const versions = versionData?.available_versions || [];
    const currentVer = versionData?.current_version;
    const registryAvailable = versionData?.registry_available;
    const registryError = versionData?.registry_error;

    return (
      <div className="rounded-lg border p-4 bg-card border-border">
        <div className="flex items-center gap-2 mb-3">
          <ArrowUpCircle className="h-5 w-5 text-info" />
          <h3 className="font-semibold text-foreground">Upgrade Target</h3>
        </div>

        {/* Loading state */}
        {versionsLoading && (
          <div className="flex items-center gap-2 py-3">
            <Loader2 className="h-4 w-4 animate-spin text-info" />
            <span className="text-sm text-muted-foreground">Querying F5 OCI registry for available versions...</span>
          </div>
        )}

        {/* Error state — API call failed */}
        {versionsError && !versionsLoading && (
          <div className="flex items-center gap-2 py-3 px-3 rounded-md text-sm bg-destructive/10 text-destructive">
            <XCircle className="h-4 w-4 shrink-0" />
            <span>Failed to fetch available versions. Check that the cluster is reachable.</span>
          </div>
        )}

        {/* Registry unavailable but no fallback versions either */}
        {!versionsLoading && !versionsError && registryAvailable === false && versions.length === 0 && (
          <div className="flex items-start gap-2 py-3 px-3 rounded-md text-sm bg-warning/10 text-warning">
            <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
            <div>
              <p className="font-medium">No upgrade versions available</p>
              <p className="mt-1 opacity-80">{registryError || 'Could not query the F5 OCI registry. Ensure the project has a valid cne_pull_secret configured.'}</p>
            </div>
          </div>
        )}

        {/* Versions available (from registry or fallback) */}
        {!versionsLoading && !versionsError && versions.length > 0 && (
          <>
            {/* Info banner when showing fallback known versions */}
            {registryAvailable === false && (
              <div className="flex items-start gap-2 py-2 px-3 rounded-md text-xs mb-3 bg-info/10 text-info">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                <span>{registryError || 'Showing known BNK versions. Configure cne_pull_secret for exact chart versions from the F5 registry.'}</span>
              </div>
            )}
            <div className="flex items-center gap-3">
              <select
                value={selectedVersion}
                onChange={(e) => setSelectedVersion(e.target.value)}
                disabled={!!isUpgradeActive}
                className="flex-1 rounded-md border px-3 py-2 text-sm bg-background border-input text-foreground"
              >
                <option value="">Select target version...</option>
                {versions.map((v: { version: string; label: string; notes?: string }) => (
                  <option key={v.version} value={v.version} disabled={v.version === currentVer}>
                    {v.label !== v.version ? `${v.label} (${v.version})` : v.version}
                    {v.version === currentVer ? ' — current' : ''}
                  </option>
                ))}
              </select>
              <Button
                onClick={() => {
                  if (!selectedVersion) return;
                  createPlan.mutate(
                    { clusterId, targetVersion: selectedVersion },
                    {
                      onSuccess: (data: BnkUpgrade) => {
                        setActiveUpgradeId(data.id);
                      },
                    },
                  );
                }}
                disabled={!selectedVersion || createPlan.isPending || !!isUpgradeActive}
                className="gap-2"
              >
                {createPlan.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Shield className="h-4 w-4" />
                )}
                Validate & Plan
              </Button>
            </div>
            {createPlan.isError && planErrorCode === 'UPGRADE_IN_PROGRESS' ? (
              <div className="mt-3 flex items-center justify-between gap-3 rounded-md px-3 py-2 text-sm bg-warning/5 text-warning border border-warning/20">
                <div className="flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4 shrink-0" />
                  <span>An upgrade is currently running. Cancel it to start a new plan.</span>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleCancelRunning}
                  disabled={cancelUpgrade.isPending}
                  className="shrink-0 border-warning/40 text-warning hover:bg-warning/10"
                >
                  {cancelUpgrade.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Cancel Upgrade'}
                </Button>
              </div>
            ) : createPlan.isError ? (
              <div className="mt-3 flex items-start gap-2 rounded-md px-3 py-2 text-sm bg-destructive/5 text-destructive">
                <XCircle className="h-4 w-4 shrink-0 mt-0.5" />
                <span>
                  {(createPlan.error as { message?: string })?.message
                    ?? 'Validate & Plan failed. Check cluster connectivity and that FLO is installed.'}
                </span>
              </div>
            ) : null}
            {selectedVersion && (
              <div className="mt-2 text-xs text-muted-foreground">
                {versions.find((v: { version: string; notes?: string }) => v.version === selectedVersion)?.notes || ''}
              </div>
            )}
          </>
        )}
      </div>
    );
  };

  // ================================================================
  // Pre-check Results
  // ================================================================
  const renderPreChecks = (checks: BnkUpgradePreCheck[]) => {
    const statusIcon = {
      pass: <CheckCircle2 className="h-4 w-4 text-success" />,
      fail: <XCircle className="h-4 w-4 text-destructive" />,
      warn: <AlertTriangle className="h-4 w-4 text-warning" />,
    };

    return (
      <div className="rounded-lg border p-4 bg-card border-border">
        <h4 className="font-semibold mb-3 flex items-center gap-2 text-foreground">
          <Shield className="h-4 w-4 text-info" />
          Pre-Upgrade Validation
        </h4>
        <div className="space-y-2">
          {checks.map((check, i) => (
            <div
              key={i}
              className={cn(
                'flex items-start gap-2 px-3 py-2 rounded text-sm',
                check.status === 'fail' && 'bg-destructive/10',
                check.status === 'warn' && 'bg-warning/10',
                check.status === 'pass' && 'bg-success/10',
              )}
            >
              <span className="mt-0.5">{statusIcon[check.status]}</span>
              <div className="flex-1">
                <span className="font-medium text-foreground">{check.label}</span>
                <p className="text-muted-foreground">{check.detail}</p>
              </div>
              {check.critical && check.status === 'fail' && (
                <Badge variant="destructive" className="text-xs">Critical</Badge>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  };

  // ================================================================
  // Upgrade Plan
  // ================================================================
  const renderPlan = (plan: BnkUpgradePlanStep[], stepResults?: BnkUpgrade['step_results'], currentStep?: number) => {
    return (
      <div className="rounded-lg border p-4 bg-card border-border">
        <h4 className="font-semibold mb-3 flex items-center gap-2 text-foreground">
          <Network className="h-4 w-4 text-info" />
          Upgrade Plan ({plan.length} steps)
        </h4>
        <div className="space-y-1">
          {plan.map((step) => {
            const result = stepResults?.find(r => r.step === step.step);
            const isCurrentStep = currentStep === step.step;

            const stepIcon = result?.status === 'completed'
              ? <CheckCircle2 className="h-4 w-4 text-success" />
              : result?.status === 'failed'
                ? <XCircle className="h-4 w-4 text-destructive" />
                : isCurrentStep
                  ? <Loader2 className="h-4 w-4 text-info animate-spin" />
                  : <div className="h-4 w-4 rounded-full border-2 border-border" />;

            const actionBadge = {
              helm_upgrade: { label: 'Helm', color: 'bg-info/10 text-info border-info/20' },
              manifest_apply: { label: 'Apply', color: 'bg-info/10 text-info border-info/20' },
              health_gate: { label: 'Health', color: 'bg-success/10 text-success border-success/20' },
              crd_wait: { label: 'CRD', color: 'bg-warning/10 text-warning border-warning/20' },
            }[step.action] || { label: step.action, color: 'bg-muted text-muted-foreground' };

            return (
              <div
                key={step.step}
                className={cn(
                  'flex items-center gap-3 px-3 py-2 rounded text-sm',
                  isCurrentStep && 'bg-info/10 border border-info/30',
                  result?.status === 'failed' && 'bg-destructive/10',
                )}
              >
                {stepIcon}
                <span className="font-mono text-xs text-muted-foreground">#{step.step}</span>
                <Badge variant="outline" className={cn('text-xs', actionBadge.color)}>
                  {actionBadge.label}
                </Badge>
                <span className="flex-1 text-foreground">{step.label}</span>
                {step.module && (
                  <span className="text-xs font-mono text-muted-foreground">{step.module}</span>
                )}
                {result?.error && (
                  <span className="text-xs text-destructive truncate max-w-[200px]" title={result.error}>
                    {result.error}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  // ================================================================
  // Active Upgrade Status + Actions
  // ================================================================
  const renderActiveUpgrade = () => {
    if (!activeUpgrade) return null;

    const statusConfig: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
      planning: { color: 'text-info', icon: <Loader2 className="h-5 w-5 animate-spin text-info" />, label: 'Validating...' },
      ready: { color: 'text-success', icon: <CheckCircle2 className="h-5 w-5 text-success" />, label: 'Ready to Execute' },
      in_progress: { color: 'text-info', icon: <Loader2 className="h-5 w-5 animate-spin text-info" />, label: 'Upgrading...' },
      health_check: { color: 'text-warning', icon: <Loader2 className="h-5 w-5 animate-spin text-warning" />, label: 'Health Check...' },
      completed: { color: 'text-success', icon: <CheckCircle2 className="h-5 w-5 text-success" />, label: 'Completed' },
      failed: { color: 'text-destructive', icon: <XCircle className="h-5 w-5 text-destructive" />, label: 'Failed' },
      rolling_back: { color: 'text-warning', icon: <Loader2 className="h-5 w-5 animate-spin text-warning" />, label: 'Rolling Back...' },
      rolled_back: { color: 'text-warning', icon: <RotateCcw className="h-5 w-5 text-warning" />, label: 'Rolled Back' },
      cancelled: { color: 'text-muted-foreground', icon: <XCircle className="h-5 w-5 text-muted-foreground" />, label: 'Cancelled' },
    };

    const config = statusConfig[activeUpgrade.status] || statusConfig.planning;

    return (
      <div className="space-y-4">
        {/* Status banner */}
        <div className="rounded-lg border p-4 bg-card border-border">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {config.icon}
              <div>
                <h3 className="font-semibold text-foreground">
                  Upgrade: {activeUpgrade.from_version || '?'} → {activeUpgrade.to_version}
                </h3>
                <p className={cn('text-sm', config.color)}>{config.label}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {activeUpgrade.status === 'ready' && (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => cancelUpgrade.mutate({ clusterId, upgradeId: activeUpgrade.id }, {
                      onSuccess: () => setActiveUpgradeId(null),
                    })}
                    disabled={cancelUpgrade.isPending}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    className="gap-2"
                    onClick={() => executeUpgrade.mutate({ clusterId, upgradeId: activeUpgrade.id })}
                    disabled={executeUpgrade.isPending}
                  >
                    {executeUpgrade.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Play className="h-4 w-4" />
                    )}
                    Execute Upgrade
                  </Button>
                </>
              )}
              {activeUpgrade.status === 'failed' && activeUpgrade.rollback_available && (
                <Button
                  variant="destructive"
                  size="sm"
                  className="gap-2"
                  onClick={() => rollbackUpgrade.mutate({ clusterId, upgradeId: activeUpgrade.id })}
                  disabled={rollbackUpgrade.isPending}
                >
                  {rollbackUpgrade.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <RotateCcw className="h-4 w-4" />
                  )}
                  Rollback
                </Button>
              )}
              {['completed', 'rolled_back', 'cancelled', 'failed'].includes(activeUpgrade.status) && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setActiveUpgradeId(null)}
                >
                  Dismiss
                </Button>
              )}
            </div>
          </div>

          {/* Progress bar */}
          {activeUpgrade.total_steps > 0 && (
            <div className="mt-3">
              <div className="flex items-center justify-between text-xs mb-1">
                <span className="text-muted-foreground">
                  Step {activeUpgrade.current_step} of {activeUpgrade.total_steps}
                </span>
                {activeUpgrade.duration_seconds && (
                  <span className="text-muted-foreground">
                    {Math.round(activeUpgrade.duration_seconds)}s
                  </span>
                )}
              </div>
              <div className="h-2 rounded-full overflow-hidden bg-muted">
                <div
                  className={cn(
                    'h-full rounded-full transition-all duration-500',
                    activeUpgrade.status === 'failed' ? 'bg-destructive' :
                    activeUpgrade.status === 'completed' ? 'bg-success' : 'bg-info',
                  )}
                  style={{ width: `${(activeUpgrade.current_step / activeUpgrade.total_steps) * 100}%` }}
                />
              </div>
            </div>
          )}

          {/* Error message */}
          {activeUpgrade.error_message && (
            <div className="mt-3 rounded p-3 text-sm bg-destructive/10 text-destructive">
              <div className="flex items-start gap-2">
                <XCircle className="h-4 w-4 mt-0.5 shrink-0" />
                <div>
                  <p className="font-medium">Error at step {activeUpgrade.error_step}</p>
                  <p className="mt-1">{activeUpgrade.error_message}</p>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Pre-checks */}
        {activeUpgrade.pre_checks && renderPreChecks(activeUpgrade.pre_checks as BnkUpgradePreCheck[])}

        {/* Plan + progress */}
        {activeUpgrade.plan && renderPlan(
          activeUpgrade.plan as BnkUpgradePlanStep[],
          activeUpgrade.step_results,
          activeUpgrade.current_step,
        )}
      </div>
    );
  };

  // ================================================================
  // Upgrade History
  // ================================================================
  const renderHistory = () => {
    const upgrades = historyData?.upgrades || [];
    if (upgrades.length === 0) return null;

    const statusBadge = (status: string) => {
      const config: Record<string, string> = {
        completed: 'bg-success/10 text-success border-success/20',
        failed: 'bg-destructive/10 text-destructive border-destructive/20',
        rolled_back: 'bg-warning/10 text-warning border-warning/20',
        cancelled: 'bg-muted text-muted-foreground',
        in_progress: 'bg-info/10 text-info border-info/20',
        ready: 'bg-success/10 text-success border-success/20',
      };
      return config[status] || 'bg-muted text-muted-foreground';
    };

    return (
      <div className="rounded-lg border p-4 bg-card border-border">
        <button
          onClick={() => setShowHistory(!showHistory)}
          className="w-full flex items-center justify-between text-foreground"
        >
          <div className="flex items-center gap-2">
            <History className="h-4 w-4 text-info" />
            <h4 className="font-semibold">Upgrade History ({upgrades.length})</h4>
          </div>
          {showHistory ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>

        {showHistory && (
          <div className="mt-3 space-y-2">
            {upgrades.map((u: BnkUpgrade) => (
              <button
                key={u.id}
                onClick={() => setActiveUpgradeId(u.id)}
                className={cn(
                  'w-full flex items-center gap-3 px-3 py-2 rounded text-sm text-left',
                  'hover:bg-muted/50',
                  activeUpgradeId === u.id && 'bg-muted/50 ring-1 ring-primary/30',
                )}
              >
                <Badge variant="outline" className={cn('text-xs shrink-0', statusBadge(u.status))}>
                  {u.status}
                </Badge>
                <span className="font-mono text-foreground">
                  {u.from_version || '?'} → {u.to_version}
                </span>
                <span className="text-xs flex-1 text-right text-muted-foreground">
                  {u.created_at ? new Date(u.created_at).toLocaleDateString() : ''}
                </span>
                {u.duration_seconds && (
                  <span className="text-xs text-muted-foreground">
                    <Clock className="h-3 w-3 inline mr-1" />
                    {Math.round(u.duration_seconds)}s
                  </span>
                )}
              </button>
            ))}
          </div>
        )}
      </div>
    );
  };

  // ================================================================
  // Main render
  // ================================================================
  return (
    <div className="space-y-4">
      {renderCurrentVersion()}
      {!activeUpgrade && renderVersionPicker()}
      {activeUpgrade ? renderActiveUpgrade() : (
        <div className="rounded-lg border p-6 text-center bg-card border-border">
          <Info className="h-8 w-8 mx-auto mb-2 text-muted-foreground" />
          <p className="text-muted-foreground">
            Select a target version and click "Validate & Plan" to start an upgrade.
          </p>
        </div>
      )}
      {renderHistory()}
    </div>
  );
}
