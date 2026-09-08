/**
 * Pure decision logic for RunBenchmarkWizard — auto-select rules, step
 * advancement gating, and the "Re-run last" prefill mapping. Extracted so
 * these can be unit-tested directly without mounting the wizard's Dialog/
 * Select tree (Radix Select is expensive to drive in jsdom).
 */
import type { BenchmarkAgent, BenchmarkRun, ProxyDeployment } from '@/types';

export type WizardMode = 'run' | 'scenario';
export type WizardStep = 1 | 2 | 3 | 4;

export interface WizardState {
  step: WizardStep;
  targetId: number | null;
  proxyId: number | null;
  agentId: number | null;
  mode: WizardMode;
  configId: number | null;
  scenarioKey: string | null;
  runLabel: string;
}

export function emptyWizardState(): WizardState {
  return {
    step: 1,
    targetId: null,
    proxyId: null,
    agentId: null,
    mode: 'run',
    configId: null,
    scenarioKey: null,
    runLabel: '',
  };
}

/** Proxies a run can actually be launched against (matches BenchmarkTargetsTab's
 * "Run test" eligibility: canRun = status === 'ready' || status === 'discovered'). */
export function eligibleProxies(proxies: ProxyDeployment[]): ProxyDeployment[] {
  return proxies.filter((p) => p.status === 'ready' || p.status === 'discovered');
}

export function connectedAgents(agents: BenchmarkAgent[]): BenchmarkAgent[] {
  return agents.filter((a) => a.status === 'connected');
}

/** Auto-select when there's exactly one target — otherwise the user picks. */
export function autoSelectTargetId(targets: Array<{ id: number }>): number | null {
  return targets.length === 1 ? targets[0].id : null;
}

/** Auto-select when there's exactly one READY/DISCOVERED proxy on the target. */
export function autoSelectProxyId(proxies: ProxyDeployment[]): number | null {
  const eligible = eligibleProxies(proxies);
  return eligible.length === 1 ? eligible[0].id : null;
}

/** Auto-select when there's exactly one connected agent. */
export function autoSelectAgentId(agents: BenchmarkAgent[]): number | null {
  const connected = connectedAgents(agents);
  return connected.length === 1 ? connected[0].id : null;
}

/** Whether the wizard can advance past `step` given the current selections. */
export function canAdvanceStep(step: WizardStep, state: WizardState): boolean {
  if (step === 1) return state.targetId != null && state.proxyId != null;
  if (step === 2) return state.agentId != null;
  if (step === 3) return state.mode === 'run' || (state.mode === 'scenario' && !!state.scenarioKey);
  return true;
}

/**
 * "Re-run last" — prefill every step from the most recently completed run.
 *
 * If the source run has a scenario_key (it was a child of a scenario sweep —
 * BenchmarkRun.scenario_key is only set on scenario-expanded runs), prefill
 * mode:'scenario' with that key instead of normalizing it into a plain
 * single run: re-running re-expands the same sweep via the existing
 * run-scenario endpoint, rather than silently dropping the scenario context
 * and launching a single config-based run with no indication it was 1-of-N.
 *
 * If the run's agent is no longer connected, agentId comes back null so the
 * wizard's step 2 (or the step-4 review) surfaces the "agent disconnected"
 * warning instead of silently launching against a stale agent id.
 */
export function prefillFromRun(
  run: Pick<BenchmarkRun, 'target_id' | 'proxy_deployment_id' | 'agent_id' | 'config_id' | 'run_label' | 'scenario_key'>,
  connectedAgentIds: number[],
  jumpToLaunch: boolean,
  eligibleProxyIds?: number[],
): WizardState {
  const agentStillConnected = run.agent_id != null && connectedAgentIds.includes(run.agent_id);
  const proxyStillEligible =
    run.proxy_deployment_id != null &&
    (eligibleProxyIds == null || eligibleProxyIds.includes(run.proxy_deployment_id));
  const isFromScenario = run.scenario_key != null;
  return {
    step: jumpToLaunch ? 4 : 1,
    targetId: run.target_id ?? null,
    proxyId: proxyStillEligible ? (run.proxy_deployment_id ?? null) : null,
    agentId: agentStillConnected ? (run.agent_id ?? null) : null,
    mode: isFromScenario ? 'scenario' : 'run',
    configId: isFromScenario ? null : (run.config_id ?? null),
    scenarioKey: isFromScenario ? (run.scenario_key ?? null) : null,
    runLabel: run.run_label ? `${run.run_label} (re-run)` : '',
  };
}
