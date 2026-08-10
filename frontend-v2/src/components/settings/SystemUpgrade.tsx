import { useState, useRef, useEffect, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useSystemVersion } from '@/hooks/useSystem';
import { queryKeys } from '@/lib/queryKeys';
import type { VersionInfo, UpgradeResponse } from '@/types/system';

// ---------------------------------------------------------------------------
// Types (component-local only)
// ---------------------------------------------------------------------------

interface UpgradeLogLine {
  timestamp: string;
  level: string;
  line: string;
  phase?: string;
}

/** Ordered upgrade phases emitted by upgrade.sh via ##PHASE:xxx markers */
const UPGRADE_PHASES = [
  { key: 'pull',     label: 'Pull code' },
  { key: 'build',    label: 'Build containers' },
  { key: 'restart',  label: 'Restart services' },
  { key: 'migrate',  label: 'Run migrations' },
  { key: 'verify',   label: 'Verify health' },
] as const;

type PhaseKey = typeof UPGRADE_PHASES[number]['key'];

type PhaseStatus = 'pending' | 'active' | 'done' | 'failed';

// ---------------------------------------------------------------------------
// Imports
// ---------------------------------------------------------------------------

import { cn } from '@/lib/utils';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
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
  RefreshCw,
  Download,
  CheckCircle,
  AlertTriangle,
  Loader2,
  ArrowUp,
  GitBranch,
  Clock,
  Terminal,
  Settings,
  ShieldAlert,
  XCircle,
  RotateCcw,
} from 'lucide-react';
import { notify } from '@/lib/notify';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function SystemUpgrade() {
  // isDark removed — tokens handle theming (D-020)
  const queryClient = useQueryClient();

  // Core state
  const [isUpgrading, setIsUpgrading] = useState(false);
  const [upgradeStatus, setUpgradeStatus] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [upgradeLog, setUpgradeLog] = useState<UpgradeLogLine[]>([]);
  const [showLog, setShowLog] = useState(false);
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);

  // Phase tracking — driven by ##PHASE markers from upgrade.sh
  const [phaseStatuses, setPhaseStatuses] = useState<Record<PhaseKey, PhaseStatus>>({
    pull: 'pending', build: 'pending', restart: 'pending', migrate: 'pending', verify: 'pending',
  });

  // Version tracking for completion detection (UP-002 fix)
  const [preUpgradeVersion, setPreUpgradeVersion] = useState<string | null>(null);
  const [upgradeComplete, setUpgradeComplete] = useState(false);
  const [upgradeFailed, setUpgradeFailed] = useState(false);

  // UP-011: Post-upgrade verification state
  const [verificationResult, setVerificationResult] = useState<{
    verdict: 'healthy' | 'degraded' | 'unhealthy';
    checks: Record<string, { status: string; error?: string; note?: string }>;
  } | null>(null);

  // Refs
  const logEndRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const healthPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const healthTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Auto-scroll log to bottom
  useEffect(() => {
    if (logEndRef.current && showLog) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [upgradeLog, showLog]);

  // ---------------------------------------------------------------------------
  // Phase management helpers
  // ---------------------------------------------------------------------------

  const advanceToPhase = useCallback((phase: string) => {
    const phaseKeys = UPGRADE_PHASES.map(p => p.key);
    const phaseIndex = phaseKeys.indexOf(phase as PhaseKey);

    if (phase === 'complete') {
      // Mark all phases as done
      setPhaseStatuses({
        pull: 'done', build: 'done', restart: 'done', migrate: 'done', verify: 'done',
      });
      return;
    }

    if (phase === 'failed') {
      // Mark current phase as failed, leave later phases as pending
      setPhaseStatuses(prev => {
        const updated = { ...prev };
        for (const p of phaseKeys) {
          if (updated[p] === 'active') {
            updated[p] = 'failed';
          }
        }
        return updated;
      });
      return;
    }

    if (phaseIndex === -1) return; // Unknown phase like 'start'

    setPhaseStatuses(prev => {
      const updated = { ...prev };
      // Everything before this phase is done
      for (let i = 0; i < phaseIndex; i++) {
        updated[phaseKeys[i]] = 'done';
      }
      // This phase is active
      updated[phaseKeys[phaseIndex]] = 'active';
      // Everything after is still pending (don't touch)
      return updated;
    });
  }, []);

  // ---------------------------------------------------------------------------
  // UP-011: Post-upgrade verification
  // ---------------------------------------------------------------------------

  const runPostUpgradeVerification = useCallback(async () => {
    try {
      const result = await api.verifyPostUpgrade();
      setVerificationResult({ verdict: result.verdict, checks: result.checks });
      if (result.verdict === 'healthy') {
        notify.success('All post-upgrade checks passed', undefined, { category: 'system' });
      } else if (result.verdict === 'degraded') {
        notify.info('Upgrade complete — some services may still be starting', undefined, { category: 'system' });
      } else {
        notify.error('Post-upgrade verification found issues');
      }
    } catch {
      // Verification endpoint not available — non-critical
      setVerificationResult(null);
    }
  }, []);

  // ---------------------------------------------------------------------------
  // WebSocket connection with reconnection (UP-008 fix)
  // ---------------------------------------------------------------------------

  const connectUpgradeWs = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/tasks/ws/tasks`;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type !== 'system_upgrade') return;

          // Add to log (skip raw phase markers — they're machine-readable)
          const line = data.line || '';
          if (!line.startsWith('##PHASE:')) {
            setUpgradeLog((prev) => [...prev, {
              timestamp: data.timestamp || new Date().toISOString(),
              level: data.level || 'info',
              line,
              phase: data.phase,
            }]);
          }

          // Handle structured phase messages
          if (data.phase) {
            advanceToPhase(data.phase);

            if (data.phase === 'complete') {
              setUpgradeStatus('Upgrade complete — verifying...');
              setUpgradeComplete(true);
              setIsUpgrading(false);
              stopHealthPolling();
              queryClient.invalidateQueries({ queryKey: queryKeys.system.version() });
              queryClient.invalidateQueries({ queryKey: queryKeys.system.health() });
              runPostUpgradeVerification();
            } else if (data.phase === 'failed') {
              setUpgradeStatus(`Upgrade failed: ${line}`);
              setUpgradeFailed(true);
              setIsUpgrading(false);
              stopHealthPolling();
              notify.error(`Upgrade failed: ${line}`);
            } else {
              // Update status text from phase label
              const phaseLabel = data.phase_label || data.phase;
              setUpgradeStatus(`${phaseLabel}...`);
            }
          }
        } catch {
          // Ignore parse errors from non-upgrade messages
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        // Auto-reconnect if still upgrading (backend may have restarted)
        if (isUpgrading) {
          setTimeout(() => {
            connectUpgradeWs();
          }, 3000);
        }
      };

      ws.onerror = () => {
        // Will trigger onclose, which handles reconnection
      };
    } catch {
      // WebSocket connection failed — will retry via health polling
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isUpgrading, advanceToPhase, queryClient]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      stopHealthPolling();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------------------
  // UP-003: Recover upgrade state on mount (page refresh during upgrade)
  // ---------------------------------------------------------------------------

  useEffect(() => {
    let cancelled = false;

    async function recoverUpgradeState() {
      try {
        const state = await api.getUpgradeStatus();
        if (cancelled) return;

        if (state.status === 'in_progress') {
          // Upgrade is still running — reconnect WS + restart health polling
          setIsUpgrading(true);
          setShowLog(true);
          setUpgradeComplete(false);
          setUpgradeFailed(false);
          setPreUpgradeVersion(state.old_version ?? null);
          setUpgradeStatus(state.phase_label ?? 'Upgrade in progress...');

          // Restore phase statuses
          if (state.current_phase) {
            advanceToPhase(state.current_phase);
          }

          // Restore log lines
          if (state.log && state.log.length > 0) {
            setUpgradeLog(state.log.map((line) => ({
              timestamp: new Date().toISOString(),
              level: line.startsWith('ERROR') ? 'error' : line.startsWith('WARNING') ? 'warning' : 'info',
              line,
            })));
          }

          connectUpgradeWs();
          if (state.old_version) {
            startHealthPolling(state.old_version);
          }
        } else if (state.status === 'interrupted') {
          // Backend restarted mid-upgrade — show stale warning
          setUpgradeFailed(true);
          setUpgradeStatus(state.phase_label ?? 'Upgrade was interrupted');
          setShowLog(true);
          if (state.current_phase) {
            advanceToPhase('failed');
          }
          if (state.log && state.log.length > 0) {
            setUpgradeLog(state.log.map((line) => ({
              timestamp: new Date().toISOString(),
              level: line.startsWith('ERROR') ? 'error' : 'info',
              line,
            })));
          }
        }
        // For 'completed', 'failed', 'none' — don't restore (user already saw the result
        // or there's nothing to show). The version query will show current state.
      } catch {
        // API not available yet — ignore (might be during startup)
      }
    }

    recoverUpgradeState();
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------------------
  // Health/version polling (UP-002 fix: detect by version change, not down-then-up)
  // ---------------------------------------------------------------------------

  const stopHealthPolling = useCallback(() => {
    if (healthPollRef.current) {
      clearInterval(healthPollRef.current);
      healthPollRef.current = null;
    }
    if (healthTimeoutRef.current) {
      clearTimeout(healthTimeoutRef.current);
      healthTimeoutRef.current = null;
    }
  }, []);

  const startHealthPolling = useCallback((oldVersion: string) => {
    // Poll version endpoint. Completion = version changed from old to new.
    // Backend being unreachable is expected during upgrade — just keep polling.
    const poll = setInterval(async () => {
      try {
        const versionData = await api.getSystemVersion() as VersionInfo;
        if (versionData.current_version && versionData.current_version !== oldVersion) {
          // Version changed — upgrade succeeded
          clearInterval(poll);
          healthPollRef.current = null;
          stopHealthPolling();
          setUpgradeComplete(true);
          setIsUpgrading(false);
          advanceToPhase('complete');
          setUpgradeStatus('Upgrade complete — verifying...');
          queryClient.invalidateQueries({ queryKey: queryKeys.system.version() });
          queryClient.invalidateQueries({ queryKey: queryKeys.system.health() });
          runPostUpgradeVerification();
        }
      } catch {
        // Backend unreachable — expected during restart, keep polling
        setUpgradeStatus('Services restarting... waiting for backend.');
      }
    }, 5000);

    healthPollRef.current = poll;

    // Timeout after 5 minutes
    healthTimeoutRef.current = setTimeout(() => {
      clearInterval(poll);
      healthPollRef.current = null;
      setIsUpgrading(false);
      setUpgradeFailed(true);
      advanceToPhase('failed');
      setUpgradeStatus('Upgrade timed out. Services may still be starting — check the System Monitor tab.');
      notify.error('Upgrade timed out after 5 minutes');
    }, 300_000);
  }, [stopHealthPolling, advanceToPhase, queryClient, runPostUpgradeVerification]);

  // ---------------------------------------------------------------------------
  // Queries & mutations
  // ---------------------------------------------------------------------------

  const { data: versionInfo, isLoading, refetch, isRefetching } = useSystemVersion();

  const upgradeReady = versionInfo?.upgrade_readiness?.upgrade_ready ?? false;
  const upgradeNotConfigured = versionInfo?.upgrade_readiness && !versionInfo.upgrade_readiness.upgrade_ready;
  const deploymentMode = versionInfo?.upgrade_readiness?.deployment_mode ?? 'server';
  const recommendedCommand = versionInfo?.upgrade_readiness?.recommended_command;
  const recommendedLabel = versionInfo?.upgrade_readiness?.recommended_label ?? 'Deploy';
  const guiUpgradeSupported = versionInfo?.upgrade_readiness?.gui_upgrade_supported ?? upgradeReady;

  const upgradeMutation = useAppMutation<UpgradeResponse, Error>({
    mutationFn: () => api.triggerSystemUpgrade(),
    onSuccess: (data: UpgradeResponse) => {
      if (data.status === 'upgrading') {
        const oldVer = data.old_version || versionInfo?.current_version || 'unknown';
        setPreUpgradeVersion(oldVer);
        setIsUpgrading(true);
        setUpgradeComplete(false);
        setUpgradeFailed(false);
        setUpgradeLog([]);
        setShowLog(true);
        setPhaseStatuses({
          pull: 'pending', build: 'pending', restart: 'pending', migrate: 'pending', verify: 'pending',
        });
        setUpgradeStatus('Upgrade started...');
        notify.success('Upgrade started! Watch the progress below.', undefined, { category: 'system' });

        connectUpgradeWs();
        // Start polling for version change (handles case where WS disconnects)
        startHealthPolling(oldVer);

      } else if (data.status === 'no_update') {
        notify.info('System is already at the latest version', undefined, { category: 'system' });
      } else if (data.status === 'blocked') {
        notify.error(data.message);
      } else if (data.status === 'docker_error') {
        notify.error(data.message);
      } else if (data.status === 'not_configured') {
        notify.error(data.message);
      } else if (data.status === 'already_upgrading') {
        notify.info('An upgrade is already in progress', undefined, { category: 'system' });
        setIsUpgrading(true);
        setShowLog(true);
        connectUpgradeWs();
      }
    },
    onError: (error: Error) => {
      setIsUpgrading(false);
      setUpgradeStatus(null);
      notify.error(`Upgrade failed: ${error.message}`);
    },
  });

  const handleCheckForUpdates = async () => {
    try {
      const result = await refetch();
      const data = result.data;
      setLastChecked(new Date());
      if (data?.error) {
        notify.error(`Version check failed: ${data.error}`);
      } else if (data?.update_available) {
        notify.success(`Update available: v${data.latest_version}`, undefined, { category: 'system' });
      } else {
        notify.success(`You're up to date (v${data?.current_version})`, undefined, { category: 'system' });
      }
    } catch {
      notify.error('Failed to check for updates');
    }
  };

  const handleUpgrade = () => {
    setShowConfirmDialog(false);
    upgradeMutation.mutate();
  };

  // ---------------------------------------------------------------------------
  // Render helpers
  // ---------------------------------------------------------------------------

  /** Render a single phase step in the progress stepper */
  const renderPhaseStep = (phaseKey: PhaseKey, label: string, index: number) => {
    const status = phaseStatuses[phaseKey];
    const isLast = index === UPGRADE_PHASES.length - 1;

    return (
      <div key={phaseKey} className="flex items-center gap-2">
        {/* Step indicator */}
        <div className={cn(
          "flex items-center justify-center w-7 h-7 rounded-full border-2 flex-shrink-0 transition-colors",
          status === 'done' && 'bg-success border-success text-success-foreground',
          status === 'active' && 'bg-primary border-primary text-primary-foreground',
          status === 'failed' && 'bg-destructive border-destructive text-destructive-foreground',
          status === 'pending' && 'border-border text-muted-foreground',
        )}>
          {status === 'done' && <CheckCircle className="h-4 w-4" />}
          {status === 'active' && <Loader2 className="h-4 w-4 animate-spin" />}
          {status === 'failed' && <XCircle className="h-4 w-4" />}
          {status === 'pending' && <span className="text-xs font-medium">{index + 1}</span>}
        </div>

        {/* Label */}
        <span className={cn(
          "text-sm font-medium",
          status === 'done' && 'text-success',
          status === 'active' && 'text-primary',
          status === 'failed' && 'text-destructive',
          status === 'pending' && 'text-muted-foreground',
        )}>
          {label}
        </span>

        {/* Connector line */}
        {!isLast && (
          <div className={cn(
            "flex-1 h-0.5 mx-1",
            status === 'done' ? 'bg-success/40' : 'bg-border',
          )} />
        )}
      </div>
    );
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <>
      <SectionCard title="System upgrade">
        <p className="text-sm text-muted-foreground -mt-1 mb-4">
          Check for and install BNK-Forge updates
        </p>
        <div className="space-y-4">
          {/* Version Info */}
          <div className="flex items-center justify-between p-4 rounded-lg border border-border bg-muted/30">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <GitBranch className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm font-medium">Current Version</span>
              </div>
              {isLoading ? (
                <div className="flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span className="text-sm text-muted-foreground">Loading...</span>
                </div>
              ) : (
                <>
                  <div className="flex items-center gap-2">
                    <span className="text-2xl font-bold">v{versionInfo?.current_version || 'unknown'}</span>
                    {versionInfo?.update_available && !isUpgrading && !upgradeComplete && (
                      <Badge variant="warning">
                        Update Available
                      </Badge>
                    )}
                    {upgradeComplete && (
                      <Badge variant="success">
                        <CheckCircle className="h-3 w-3 mr-1" />
                        Just Upgraded
                      </Badge>
                    )}
                    {!versionInfo?.update_available && !upgradeComplete && versionInfo?.current_version && !versionInfo?.error && (
                      <Badge variant="success">
                        <CheckCircle className="h-3 w-3 mr-1" />
                        Up to date
                      </Badge>
                    )}
                  </div>
                  {lastChecked && (
                    <p className="text-xs text-muted-foreground mt-1">
                      Last checked: {lastChecked.toLocaleTimeString()}
                    </p>
                  )}
                </>
              )}
            </div>

            <Button
              variant="outline"
              size="sm"
              onClick={handleCheckForUpdates}
              disabled={isRefetching || isUpgrading}
              aria-label="Check for system updates"
            >
              {isRefetching ? (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              ) : (
                <RefreshCw className="h-4 w-4 mr-2" />
              )}
              {isRefetching ? 'Checking...' : 'Check for Updates'}
            </Button>
          </div>

          {/* Configuration Warning / mode guidance */}
          {upgradeNotConfigured && !isUpgrading && (
            <Alert className="border-warning/30 bg-warning/5">
              <Settings className="h-4 w-4 text-warning" />
              <AlertTitle className="text-warning">
                {deploymentMode === 'local' ? 'Local upgrade is command-driven' : 'GUI Upgrade Not Configured'}
              </AlertTitle>
              <AlertDescription className="text-warning/80">
                <div className="mt-1 space-y-1 text-sm">
                  {deploymentMode === 'local' ? (
                    <>
                      <p>
                        This instance is running in <strong>local laptop mode</strong>. After checking versions, update by running <code className="px-1 py-0.5 rounded bg-muted text-xs">{recommendedCommand || 'make local-deploy'}</code> from the repo root.
                      </p>
                      <p className="text-xs mt-1 text-warning/80">
                        GUI upgrade is intentionally disabled in local mode because the laptop compose overlay does not mount Docker socket / host repo path into the backend container.
                      </p>
                    </>
                  ) : !versionInfo?.upgrade_readiness?.host_repo_path_set && (
                    <p>
                      <code className="px-1 py-0.5 rounded bg-muted text-xs">HOST_REPO_PATH</code> is not set.
                      Add it to <code className="px-1 py-0.5 rounded bg-muted text-xs">docker-compose.yml</code> under backend environment (e.g., <code className="text-xs">HOST_REPO_PATH=/home/user/bnk-forge-v2</code>).
                    </p>
                  )}
                  {deploymentMode !== 'local' && !versionInfo?.upgrade_readiness?.docker_socket_available && (
                    <p>Docker socket not mounted. Copy <code className="px-1 py-0.5 rounded bg-muted text-xs">docker-compose.override.example.yml</code> to <code className="px-1 py-0.5 rounded bg-muted text-xs">docker-compose.override.yml</code> and restart.</p>
                  )}
                  {deploymentMode !== 'local' && (
                    <p className="text-xs mt-1 text-warning/80">
                      You can still upgrade via SSH: <code>cd ~/bnk-forge-v2 && {recommendedCommand || './upgrade.sh'}</code>
                    </p>
                  )}
                </div>
              </AlertDescription>
            </Alert>
          )}

          {/* Update Available Alert */}
          {versionInfo?.update_available && !isUpgrading && !upgradeComplete && (
            <Alert className="border-warning/30 bg-warning/5">
              <Download className="h-4 w-4 text-warning" />
              <AlertTitle className="text-warning">
                Update Available: v{versionInfo.latest_version}
              </AlertTitle>
              <AlertDescription className="text-warning/80">
                <div className="mt-2 space-y-2">
                  <p>
                    A new version of BNK-Forge is available.
                    {versionInfo.commits_behind > 0 && (
                      <span className="ml-1">
                        ({versionInfo.commits_behind} patch version{versionInfo.commits_behind > 1 ? 's' : ''} behind)
                      </span>
                    )}
                  </p>
                  <div className="flex items-center gap-2">
                    {guiUpgradeSupported ? (
                      <Button
                        onClick={() => setShowConfirmDialog(true)}
                        disabled={upgradeMutation.isPending || !upgradeReady}
                      >
                        {upgradeMutation.isPending ? (
                          <Loader2 className="h-4 w-4 animate-spin mr-2" />
                        ) : (
                          <ArrowUp className="h-4 w-4 mr-2" />
                        )}
                        Upgrade Now
                      </Button>
                    ) : (
                      <div className="rounded-md border border-warning/30 bg-warning/5 px-3 py-2 text-xs">
                        <div className="font-medium">{recommendedLabel}</div>
                        <div className="mt-1 font-mono">{recommendedCommand || 'make local-deploy'}</div>
                      </div>
                    )}
                    {!upgradeReady && guiUpgradeSupported && (
                      <span className="text-xs text-warning">
                        Configure HOST_REPO_PATH to enable GUI upgrades
                      </span>
                    )}
                  </div>
                </div>
              </AlertDescription>
            </Alert>
          )}

          {/* Upgrading Status — with phase stepper */}
          {isUpgrading && (
            <Alert className="border-primary/30 bg-primary/5">
              <Loader2 className="h-4 w-4 animate-spin text-primary" />
              <AlertTitle className="text-primary flex items-center gap-2">
                Upgrading System
                <Badge variant="info" className="text-xs">
                  <ShieldAlert className="h-3 w-3 mr-1" />
                  Deployments locked
                </Badge>
              </AlertTitle>
              <AlertDescription className="text-primary/80">
                <div className="mt-3 space-y-3">
                  {/* Progress stepper */}
                  <div className="flex items-center gap-1">
                    {UPGRADE_PHASES.map((p, i) => renderPhaseStep(p.key, p.label, i))}
                  </div>

                  {/* Current status text */}
                  {upgradeStatus && (
                    <p className="text-sm">{upgradeStatus}</p>
                  )}

                  <div className="flex items-center gap-2 text-sm text-primary/80">
                    <Clock className="h-4 w-4" />
                    <span>This may take a few minutes. You can safely stay on this page.</span>
                  </div>
                </div>
              </AlertDescription>
            </Alert>
          )}

          {/* Upgrade Complete */}
          {upgradeComplete && (
            <Alert className={cn(
              verificationResult?.verdict === 'unhealthy'
                ? 'border-destructive/30 bg-destructive/5'
                : verificationResult?.verdict === 'degraded'
                  ? 'border-warning/30 bg-warning/5'
                  : 'border-success/30 bg-success/5',
            )}>
              {verificationResult?.verdict === 'unhealthy' ? (
                <AlertTriangle className="h-4 w-4 text-destructive" />
              ) : verificationResult?.verdict === 'degraded' ? (
                <AlertTriangle className="h-4 w-4 text-warning" />
              ) : (
                <CheckCircle className="h-4 w-4 text-success" />
              )}
              <AlertTitle className={cn(
                verificationResult?.verdict === 'unhealthy' && 'text-destructive',
                verificationResult?.verdict === 'degraded' && 'text-warning',
                (!verificationResult || verificationResult.verdict === 'healthy') && 'text-success',
              )}>
                {verificationResult?.verdict === 'unhealthy'
                  ? 'Upgrade Complete — Issues Detected'
                  : verificationResult?.verdict === 'degraded'
                    ? 'Upgrade Complete — Partially Healthy'
                    : 'Upgrade Complete'}
              </AlertTitle>
              <AlertDescription className={cn(
                verificationResult?.verdict === 'unhealthy' && 'text-destructive/80',
                verificationResult?.verdict === 'degraded' && 'text-warning/80',
                (!verificationResult || verificationResult.verdict === 'healthy') && 'text-success/80',
              )}>
                <div className="mt-2 space-y-3">
                  {/* Progress stepper — all done */}
                  <div className="flex items-center gap-1">
                    {UPGRADE_PHASES.map((p, i) => renderPhaseStep(p.key, p.label, i))}
                  </div>

                  <p className="text-sm">
                    {preUpgradeVersion && versionInfo?.current_version && preUpgradeVersion !== versionInfo.current_version
                      ? `Successfully upgraded from v${preUpgradeVersion} to v${versionInfo.current_version}.`
                      : 'Upgrade completed successfully.'}
                  </p>

                  {/* UP-011: Verification results */}
                  {verificationResult && (
                    <div className="text-xs space-y-1 p-2 rounded border border-border bg-muted/30">
                      <p className="font-medium text-sm mb-1.5">Verification Checks</p>
                      {Object.entries(verificationResult.checks).map(([name, check]) => (
                        <div key={name} className="flex items-center gap-2">
                          {check.status === 'pass' && <CheckCircle className="h-3 w-3 text-success flex-shrink-0" />}
                          {check.status === 'warn' && <AlertTriangle className="h-3 w-3 text-warning flex-shrink-0" />}
                          {check.status === 'fail' && <XCircle className="h-3 w-3 text-destructive flex-shrink-0" />}
                          {check.status === 'skip' && <Clock className="h-3 w-3 text-muted-foreground flex-shrink-0" />}
                          <span className="capitalize">{name}</span>
                          {check.error && <span className="text-destructive ml-1">— {check.error}</span>}
                          {check.note && <span className="text-muted-foreground ml-1">— {check.note}</span>}
                        </div>
                      ))}
                    </div>
                  )}

                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => window.location.reload()}
                    className={cn(
                      verificationResult?.verdict === 'unhealthy'
                        ? 'text-destructive border-destructive/30 hover:bg-destructive/10'
                        : 'text-success border-success/30 hover:bg-success/10',
                    )}
                  >
                    <RotateCcw className="h-4 w-4 mr-2" />
                    Reload to update UI
                  </Button>
                </div>
              </AlertDescription>
            </Alert>
          )}

          {/* Upgrade Failed */}
          {upgradeFailed && !isUpgrading && (
            <Alert variant="destructive">
              <XCircle className="h-4 w-4" />
              <AlertTitle>Upgrade Failed</AlertTitle>
              <AlertDescription>
                <div className="mt-2 space-y-3">
                  {/* Progress stepper — shows where it failed */}
                  <div className="flex items-center gap-1">
                    {UPGRADE_PHASES.map((p, i) => renderPhaseStep(p.key, p.label, i))}
                  </div>

                  {upgradeStatus && (
                    <p className="text-sm">{upgradeStatus}</p>
                  )}

                  <p className="text-sm">
                    Check the upgrade output below for details. You can also upgrade via SSH:
                    {' '}<code className="px-1 py-0.5 rounded bg-muted text-xs">cd ~/bnk-forge-v2 && ./upgrade.sh</code>
                  </p>
                </div>
              </AlertDescription>
            </Alert>
          )}

          {/* Live Upgrade Output Terminal */}
          {(showLog && upgradeLog.length > 0) && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                  <Terminal className="h-4 w-4" />
                  Upgrade Output
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowLog(false)}
                  className="text-xs h-6 px-2"
                >
                  Hide
                </Button>
              </div>
              <div
                className={cn(
                  "rounded-lg border p-3 font-mono text-xs max-h-64 overflow-y-auto",
                  "bg-card border-border font-mono text-foreground"
                )}
                role="log"
                aria-label="Upgrade output log"
              >
                {upgradeLog.map((entry, i) => (
                  <div
                    key={i}
                    className={cn(
                      "py-0.5 leading-relaxed whitespace-pre-wrap break-all",
                      entry.level === 'error' && 'text-destructive',
                      entry.level === 'success' && 'text-success',
                      entry.level === 'warning' && 'text-warning',
                    )}
                  >
                    {entry.line}
                  </div>
                ))}
                <div ref={logEndRef} />
              </div>
            </div>
          )}

          {/* Collapsed log toggle */}
          {(!showLog && upgradeLog.length > 0) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowLog(true)}
              className="text-xs"
            >
              <Terminal className="h-3 w-3 mr-1" />
              Show upgrade output ({upgradeLog.length} lines)
            </Button>
          )}

          {/* Error State — version check */}
          {versionInfo?.error && !isUpgrading && !upgradeComplete && !upgradeFailed && (
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Version Check Failed</AlertTitle>
              <AlertDescription>
                {versionInfo.error}
              </AlertDescription>
            </Alert>
          )}

          {/* Info — what happens during upgrade */}
          {!isUpgrading && !upgradeComplete && !upgradeFailed && (
            <div className="text-xs text-muted-foreground space-y-1">
              <p>
                <strong>What happens during upgrade:</strong>
              </p>
              <ul className="list-disc list-inside ml-2 space-y-0.5">
                <li>Latest code is pulled from git repository</li>
                <li>Docker containers are rebuilt with new code</li>
                <li>Services are restarted (brief downtime ~1-3 min)</li>
                <li>Database migrations run automatically if needed</li>
                <li>Deployments are locked until upgrade completes</li>
              </ul>
              <p className="mt-2">
                <strong>Note:</strong> Your data (projects, configurations) is preserved during upgrades.
              </p>
            </div>
          )}
        </div>
      </SectionCard>

      {/* Upgrade Confirmation Dialog (UP-006: replaces window.confirm) */}
      <AlertDialog open={showConfirmDialog} onOpenChange={setShowConfirmDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Upgrade BNK-Forge?</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-3">
                {/* Version change */}
                <div className="flex items-center gap-3 p-3 rounded-lg bg-muted/30 border border-border">
                  <div className="text-center">
                    <div className="text-xs text-muted-foreground">Current</div>
                    <div className="font-mono font-bold">v{versionInfo?.current_version}</div>
                  </div>
                  <ArrowUp className="h-4 w-4 text-muted-foreground rotate-90" />
                  <div className="text-center">
                    <div className="text-xs text-muted-foreground">New</div>
                    <div className="font-mono font-bold text-success">v{versionInfo?.latest_version}</div>
                  </div>
                </div>

                {/* What will happen */}
                <div className="text-sm space-y-1.5">
                  <p className="font-medium">This will:</p>
                  <ul className="list-disc list-inside ml-1 space-y-0.5 text-muted-foreground">
                    <li>Pull latest code from the repository</li>
                    <li>Rebuild and restart all containers (~2-5 min)</li>
                    <li>Run database migrations if needed</li>
                    <li>Lock deployments until upgrade completes</li>
                  </ul>
                </div>

                {/* Warning */}
                <div className="flex items-start gap-2 p-2 rounded-md bg-warning/5 border border-warning/20">
                  <AlertTriangle className="h-4 w-4 text-warning mt-0.5 flex-shrink-0" />
                  <p className="text-xs text-warning/80">
                    The system will be briefly unavailable during the upgrade. Active users may experience a brief disconnection.
                  </p>
                </div>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleUpgrade}
            >
              <ArrowUp className="h-4 w-4 mr-2" />
                      {guiUpgradeSupported ? 'Upgrade Now' : (recommendedLabel || 'Deploy')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
