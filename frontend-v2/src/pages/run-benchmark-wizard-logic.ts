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
  /** aiperf settings edited at launch, applied over the config (null = unset a config key). */
  overrides: Record<string, unknown>;
  /** Load steps of a sweep scenario as typed ("2, 4, 8"); empty = the scenario default. */
  steps: string;
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
    overrides: {},
    steps: '',
  };
}

/** Settings a run gets when neither a config nor an edit sets them (mirrors the trigger route). */
export const RUN_DEFAULTS: Record<string, unknown> = {
  endpoint_type: 'chat',
  streaming: true,
  request_timeout_seconds: 600,
  concurrency: 50,
  request_count: 250,
  synthetic_input_tokens_mean: 500,
  output_tokens_mean: 128,
  extra_inputs: ['ignore_eos:true'],
};

/** Keys the target/proxy owns; not editable per run. */
const IDENTITY_KEYS = new Set(['url', 'model', 'endpoint']);

/** The settings shown in the editor: config (minus identity / Forge keys) with edits applied. */
export function effectiveSettings(base: Record<string, unknown>, overrides: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries({ ...base, ...overrides })) {
    if (v !== null && v !== undefined && !IDENTITY_KEYS.has(k) && !k.startsWith('_')) out[k] = v;
  }
  return out;
}

/** Turn edited settings back into overrides: changed keys, and null for keys removed from the config. */
export function diffOverrides(base: Record<string, unknown>, edited: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(edited)) {
    if (JSON.stringify(v) !== JSON.stringify(base[k])) out[k] = v;
  }
  for (const k of Object.keys(base)) {
    if (!(k in edited) && !IDENTITY_KEYS.has(k) && !k.startsWith('_')) out[k] = null;
  }
  return out;
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
  if (step === 3) {
    return state.mode === 'run' || (state.mode === 'scenario' && !!state.scenarioKey && parseSteps(state.steps) !== 'invalid');
  }
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
 * If the run's agent is no longer connected (or its proxy is no longer
 * eligible), that field comes back null and jumpToLaunch lands on the first
 * step that needs input instead of Launch, so nothing launches against a
 * stale id.
 */
/** The first step that still needs input (4 = everything resolves, ready to launch). */
export function firstIncompleteStep(state: WizardState): WizardStep {
  for (const step of [1, 2, 3] as const) {
    if (!canAdvanceStep(step, state)) return step;
  }
  return 4;
}

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
  const state: WizardState = {
    step: 1,
    targetId: run.target_id ?? null,
    proxyId: proxyStillEligible ? (run.proxy_deployment_id ?? null) : null,
    agentId: agentStillConnected ? (run.agent_id ?? null) : null,
    mode: isFromScenario ? 'scenario' : 'run',
    configId: isFromScenario ? null : (run.config_id ?? null),
    scenarioKey: isFromScenario ? (run.scenario_key ?? null) : null,
    runLabel: run.run_label ? `${run.run_label} (re-run)` : '',
    overrides: {},
    steps: '',
  };
  // Jump to Launch only when every step resolves; otherwise open the first
  // step that still needs input.
  state.step = jumpToLaunch ? firstIncompleteStep(state) : 1;
  return state;
}

/** Typed load steps → sorted unique numbers; null when empty (use the scenario's), 'invalid' otherwise. */
export function parseSteps(text: string, max = 12): number[] | null | 'invalid' {
  const parts = text.split(/[\s,]+/).filter(Boolean);
  if (!parts.length) return null;
  const nums = parts.map(Number);
  if (nums.some((n) => !Number.isFinite(n) || n <= 0)) return 'invalid';
  const unique = [...new Set(nums)].sort((a, b) => a - b);
  return unique.length > max ? 'invalid' : unique;
}

/** Minutes a rate sweep takes: per step, warmup plus max(floor, minRequests / rate). */
export function rateSweepMinutes(rates: number[], warmupS: number, floorS: number, minRequests: number): number {
  const seconds = rates.reduce((sum, r) => sum + warmupS + Math.max(floorS, Math.ceil(minRequests / r)), 0);
  return Math.ceil(seconds / 60);
}
