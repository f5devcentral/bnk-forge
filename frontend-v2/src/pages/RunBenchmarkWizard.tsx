/**
 * RunBenchmarkWizard — guided target → agent → config/scenario → launch flow.
 *
 * Launches through the EXISTING trigger endpoints (useTriggerRun / useRunScenario
 * — same calls BenchmarkTargetsTab's per-proxy "Run test" / "Run Scenario"
 * buttons already make). No new backend routes. "Re-run last" prefills every
 * step from the most recently COMPLETED run and jumps straight to Launch when
 * everything it needs (agent still connected) still resolves cleanly.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { ArrowLeft, ArrowRight, History, Loader2, Play, AlertTriangle } from 'lucide-react';
import {
  useBenchmarkTargets,
  useProxyDeployments,
  useBenchmarkAgents,
  useBenchmarkConfigs,
  useScenarios,
  useBenchmarkRuns,
  useTriggerRun,
  useRunScenario,
} from '@/hooks/useBenchmarks';
import { parseApiError } from '@/lib/error-handler';
import { ProxyBadge } from './benchmark-utils';
import type { SetupSection } from './benchmark-runs-view';
import {
  emptyWizardState,
  eligibleProxies as computeEligibleProxies,
  connectedAgents as computeConnectedAgents,
  autoSelectTargetId,
  autoSelectProxyId,
  autoSelectAgentId,
  canAdvanceStep,
  prefillFromRun,
  type WizardMode,
  type WizardStep,
} from './run-benchmark-wizard-logic';

const LAST_CONFIG_KEY_PREFIX = 'benchmarks_last_config_for_target_';

export interface RunBenchmarkWizardLaunchResult {
  runId?: number;
  groupId?: number;
}

interface RunBenchmarkWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onLaunched: (result: RunBenchmarkWizardLaunchResult) => void;
  onGoToSetup: (section: SetupSection) => void;
  /** When true (set on open), prefill everything from the most recent
   * completed run and jump to Launch if it fully resolves. */
  initialReRunLast?: boolean;
}

export function RunBenchmarkWizard({
  open,
  onOpenChange,
  onLaunched,
  onGoToSetup,
  initialReRunLast,
}: RunBenchmarkWizardProps) {
  const [state, setState] = useState(emptyWizardState);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [prefilledFromReRun, setPrefilledFromReRun] = useState(false);

  const { data: targetsData } = useBenchmarkTargets();
  const targets = useMemo(() => targetsData?.targets ?? [], [targetsData]);

  const { data: proxies } = useProxyDeployments(state.targetId ?? undefined);
  const eligibleProxies = useMemo(() => computeEligibleProxies(proxies ?? []), [proxies]);

  const { data: agents } = useBenchmarkAgents();
  const connectedAgents = useMemo(() => computeConnectedAgents(agents ?? []), [agents]);

  const { data: configs } = useBenchmarkConfigs();
  const { data: scenarioCatalog } = useScenarios();
  const scenarios = scenarioCatalog?.scenarios ?? [];

  const { data: lastCompletedData } = useBenchmarkRuns({ status: 'completed', limit: 1, pollingEnabled: false });
  const lastCompletedRun = lastCompletedData?.runs?.[0];

  const triggerRun = useTriggerRun();
  const runScenario = useRunScenario();
  const isLaunching = triggerRun.isPending || runScenario.isPending;
  // Belt-and-suspenders single-submission guard: isPending flips on the next
  // render, which can lag a fast double-click by a tick. This ref blocks
  // synchronously the instant handleLaunch runs.
  const submittingRef = useRef(false);

  const prefillFromLastRun = useCallback((jumpToLaunch: boolean) => {
    if (!lastCompletedRun) return;
    setState(prefillFromRun(lastCompletedRun, connectedAgents.map((a) => a.id), jumpToLaunch, eligibleProxies.map((p) => p.id)));
    setPrefilledFromReRun(true);
  }, [lastCompletedRun, connectedAgents, eligibleProxies]);

  // Reset on close; on open, either prefill from "re-run last" or start blank.
  useEffect(() => {
    if (!open) {
      setState(emptyWizardState());
      setLaunchError(null);
      setPrefilledFromReRun(false);
      submittingRef.current = false;
      return;
    }
    if (initialReRunLast && lastCompletedRun && !prefilledFromReRun) {
      prefillFromLastRun(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialReRunLast, lastCompletedRun, prefilledFromReRun]);

  // Auto-select the target when there's exactly one.
  useEffect(() => {
    if (!open || state.targetId != null) return;
    const autoId = autoSelectTargetId(targets);
    if (autoId != null) setState((s) => ({ ...s, targetId: autoId }));
  }, [open, state.targetId, targets]);

  // Auto-select the proxy when there's exactly one eligible one for the chosen target.
  useEffect(() => {
    if (!open || state.targetId == null || state.proxyId != null) return;
    const autoId = autoSelectProxyId(proxies ?? []);
    if (autoId != null) setState((s) => ({ ...s, proxyId: autoId }));
  }, [open, state.targetId, state.proxyId, proxies]);

  // Auto-select the agent when there's exactly one connected.
  useEffect(() => {
    if (!open || state.agentId != null) return;
    const autoId = autoSelectAgentId(agents ?? []);
    if (autoId != null) setState((s) => ({ ...s, agentId: autoId }));
  }, [open, state.agentId, agents]);

  // Default config: last-used config for this target, if it still exists.
  useEffect(() => {
    if (!open || state.targetId == null || state.configId != null || prefilledFromReRun) return;
    try {
      const saved = localStorage.getItem(`${LAST_CONFIG_KEY_PREFIX}${state.targetId}`);
      const savedId = saved ? Number(saved) : null;
      if (savedId && (configs ?? []).some((c) => c.id === savedId)) {
        setState((s) => ({ ...s, configId: savedId }));
      }
    } catch {
      /* localStorage unavailable — skip default */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, state.targetId, configs]);

  if (!targets.length) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Run benchmark</DialogTitle>
            <DialogDescription>No targets yet — add one on the Setup tab first.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
            <Button onClick={() => { onOpenChange(false); onGoToSetup('targets'); }}>Go to Setup</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }

  const selectedTarget = targets.find((t) => t.id === state.targetId);
  const selectedProxy = eligibleProxies.find((p) => p.id === state.proxyId);
  const selectedAgent = connectedAgents.find((a) => a.id === state.agentId);
  const selectedProxyStillEligible = state.proxyId != null && !!selectedProxy;
  const selectedAgentStillConnected = state.agentId != null && !!selectedAgent;

  const canGoNext = canAdvanceStep(state.step, state);

  const goNext = () => setState((s) => ({ ...s, step: (Math.min(4, s.step + 1) as WizardStep) }));
  const goBack = () => setState((s) => ({ ...s, step: (Math.max(1, s.step - 1) as WizardStep) }));

  const handleLaunch = () => {
    if (isLaunching || submittingRef.current) return; // single-submission guard
    setLaunchError(null);

    if (state.targetId == null || state.proxyId == null || state.agentId == null) {
      setLaunchError('Missing target, proxy, or agent selection.');
      return;
    }
    if (!selectedProxyStillEligible) {
      setLaunchError('The selected proxy is no longer ready or available. Go back and pick another proxy.');
      return;
    }
    if (!selectedAgentStillConnected) {
      setLaunchError('The selected agent is no longer connected. Go back and pick another agent.');
      return;
    }

    submittingRef.current = true;
    const clearSubmitting = () => { submittingRef.current = false; };

    if (state.mode === 'scenario') {
      if (!state.scenarioKey) {
        submittingRef.current = false;
        setLaunchError('Pick a scenario first.');
        return;
      }
      runScenario.mutate(
        {
          targetId: state.targetId,
          proxyId: state.proxyId,
          data: {
            scenario_key: state.scenarioKey,
            agent_id: state.agentId,
            run_label: state.runLabel || undefined,
          },
        },
        {
          onSuccess: (result) => {
            onLaunched({ groupId: result.run_group_id });
            onOpenChange(false);
          },
          onError: (err) => { clearSubmitting(); setLaunchError(parseApiError(err).message); },
        },
      );
      return;
    }

    triggerRun.mutate(
      {
        targetId: state.targetId,
        proxyId: state.proxyId,
        data: {
          config_id: state.configId ?? undefined,
          agent_id: state.agentId,
          run_label: state.runLabel || undefined,
        },
      },
      {
        onSuccess: (result) => {
          try {
            if (state.configId != null && state.targetId != null) {
              localStorage.setItem(`${LAST_CONFIG_KEY_PREFIX}${state.targetId}`, String(state.configId));
            }
          } catch {
            /* localStorage unavailable — non-fatal */
          }
          onLaunched({ runId: result.run_id });
          onOpenChange(false);
        },
        onError: (err) => { clearSubmitting(); setLaunchError(parseApiError(err).message); },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <div className="flex items-center justify-between gap-2">
            <DialogTitle>Run benchmark</DialogTitle>
            {lastCompletedRun && !prefilledFromReRun && (
              <Button
                variant="ghost" size="sm" className="gap-1.5 text-xs"
                onClick={() => prefillFromLastRun(true)}
              >
                <History className="h-3.5 w-3.5" />
                Re-run last
              </Button>
            )}
          </div>
          <DialogDescription>Step {state.step} of 4</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 min-h-[180px]">
          {state.step === 1 && (
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Target</Label>
                <Select
                  value={state.targetId != null ? String(state.targetId) : undefined}
                  onValueChange={(v) => setState((s) => ({ ...s, targetId: Number(v), proxyId: null }))}
                >
                  <SelectTrigger><SelectValue placeholder="Select a target" /></SelectTrigger>
                  <SelectContent>
                    {targets.map((t) => (
                      <SelectItem key={t.id} value={String(t.id)}>{t.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {state.targetId != null && (
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">Proxy</Label>
                  {eligibleProxies.length === 0 ? (
                    <Alert>
                      <AlertTriangle className="h-4 w-4" />
                      <AlertDescription>
                        No ready proxies on this target.{' '}
                        <button className="underline" onClick={() => { onOpenChange(false); onGoToSetup('targets'); }}>
                          Deploy one on Setup
                        </button>
                      </AlertDescription>
                    </Alert>
                  ) : (
                    <Select
                      value={state.proxyId != null ? String(state.proxyId) : undefined}
                      onValueChange={(v) => setState((s) => ({ ...s, proxyId: Number(v) }))}
                    >
                      <SelectTrigger><SelectValue placeholder="Select a proxy" /></SelectTrigger>
                      <SelectContent>
                        {eligibleProxies.map((p) => (
                          <SelectItem key={p.id} value={String(p.id)}>{p.proxy_type}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                </div>
              )}
            </div>
          )}

          {state.step === 2 && (
            <div className="space-y-3">
              {connectedAgents.length === 0 ? (
                <Alert variant="destructive">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>
                    No connected agents.{' '}
                    <button className="underline" onClick={() => { onOpenChange(false); onGoToSetup('agents'); }}>
                      Register one on Setup
                    </button>
                  </AlertDescription>
                </Alert>
              ) : (
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">Agent</Label>
                  <Select
                    value={state.agentId != null ? String(state.agentId) : undefined}
                    onValueChange={(v) => setState((s) => ({ ...s, agentId: Number(v) }))}
                  >
                    <SelectTrigger><SelectValue placeholder="Select a connected agent" /></SelectTrigger>
                    <SelectContent>
                      {connectedAgents.map((a) => (
                        <SelectItem key={a.id} value={String(a.id)}>{a.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
            </div>
          )}

          {state.step === 3 && (
            <div className="space-y-3">
              <RadioGroup
                value={state.mode}
                onValueChange={(v) => setState((s) => ({ ...s, mode: v as WizardMode }))}
                className="flex gap-4"
              >
                <div className="flex items-center space-x-2">
                  <RadioGroupItem value="run" id="wizard-mode-run" />
                  <Label htmlFor="wizard-mode-run" className="text-sm cursor-pointer">Saved config</Label>
                </div>
                <div className="flex items-center space-x-2">
                  <RadioGroupItem value="scenario" id="wizard-mode-scenario" disabled={scenarios.length === 0} />
                  <Label htmlFor="wizard-mode-scenario" className="text-sm cursor-pointer">Scenario</Label>
                </div>
              </RadioGroup>

              {state.mode === 'run' ? (
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">Config (optional — defaults if none picked)</Label>
                  <Select
                    value={state.configId != null ? String(state.configId) : 'none'}
                    onValueChange={(v) => setState((s) => ({ ...s, configId: v === 'none' ? null : Number(v) }))}
                  >
                    <SelectTrigger><SelectValue placeholder="Default config" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">Default (no saved config)</SelectItem>
                      {(configs ?? []).map((c) => (
                        <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ) : (
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">Scenario</Label>
                  <Select
                    value={state.scenarioKey ?? undefined}
                    onValueChange={(v) => setState((s) => ({ ...s, scenarioKey: v }))}
                  >
                    <SelectTrigger><SelectValue placeholder="Select a scenario" /></SelectTrigger>
                    <SelectContent>
                      {scenarios.map((sc) => (
                        <SelectItem key={sc.key} value={sc.key}>
                          {sc.name} <span className="text-muted-foreground">({sc.child_run_count} runs)</span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}

              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Run label (optional)</Label>
                <Input
                  value={state.runLabel}
                  onChange={(e) => setState((s) => ({ ...s, runLabel: e.target.value }))}
                  placeholder={selectedProxy ? `${selectedProxy.proxy_type}-${selectedTarget?.name ?? ''}` : ''}
                />
              </div>
            </div>
          )}

          {state.step === 4 && (
            <div className="space-y-3 text-sm">
              <div className="rounded-md border border-border p-3 space-y-2">
                <SummaryRow label="Target" value={selectedTarget?.name ?? '—'} />
                <SummaryRow label="Proxy" value={selectedProxy ? <ProxyBadge proxy={selectedProxy.proxy_type} /> : '—'} />
                <SummaryRow label="Agent" value={selectedAgent?.name ?? '—'} />
                <SummaryRow
                  label={state.mode === 'scenario' ? 'Scenario' : 'Config'}
                  value={
                    state.mode === 'scenario'
                      ? scenarios.find((sc) => sc.key === state.scenarioKey)?.name ?? state.scenarioKey ?? '—'
                      : (configs ?? []).find((c) => c.id === state.configId)?.name ?? 'Default'
                  }
                />
              </div>

              {!selectedProxyStillEligible && (
                <Alert variant="destructive">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>
                    The selected proxy is no longer ready. <button className="underline" onClick={goBack}>Go back</button> and pick another.
                  </AlertDescription>
                </Alert>
              )}

              {!selectedAgentStillConnected && (
                <Alert variant="destructive">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>
                    The selected agent disconnected. <button className="underline" onClick={goBack}>Go back</button> and pick another.
                  </AlertDescription>
                </Alert>
              )}

              {launchError && (
                <Alert variant="destructive">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>{launchError}</AlertDescription>
                </Alert>
              )}
            </div>
          )}
        </div>

        <DialogFooter className="flex items-center justify-between sm:justify-between">
          <div>
            {state.step > 1 && (
              <Button variant="ghost" size="sm" className="gap-1" onClick={goBack} disabled={isLaunching}>
                <ArrowLeft className="h-3.5 w-3.5" />
                Back
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Badge variant="muted" className="self-center">{state.step}/4</Badge>
            {state.step < 4 ? (
              <Button size="sm" className="gap-1" disabled={!canGoNext} onClick={goNext}>
                Next
                <ArrowRight className="h-3.5 w-3.5" />
              </Button>
            ) : (
              <Button size="sm" className="gap-1.5" onClick={handleLaunch} disabled={isLaunching}>
                {isLaunching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                Launch
              </Button>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SummaryRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-sm text-foreground">{value}</span>
    </div>
  );
}
