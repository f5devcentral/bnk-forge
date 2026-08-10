/**
 * Tests for RunBenchmarkWizard's pure decision logic: proxy/agent eligibility
 * filters, auto-select rules (exactly-one-candidate), step-advancement gating,
 * and the "Re-run last" prefill mapping (incl. the agent-disconnected guard).
 */
import { describe, it, expect } from 'vitest';
import {
  emptyWizardState,
  eligibleProxies,
  connectedAgents,
  autoSelectTargetId,
  autoSelectProxyId,
  autoSelectAgentId,
  canAdvanceStep,
  prefillFromRun,
} from '@/pages/run-benchmark-wizard-logic';
import type { BenchmarkAgent, ProxyDeployment } from '@/types';

const now = '2026-07-20T10:00:00Z';

function mockProxy(overrides: Partial<ProxyDeployment> = {}): ProxyDeployment {
  return {
    id: 1,
    target_id: 1,
    proxy_type: 'envoy',
    helm_release: null,
    helm_chart: null,
    helm_version: null,
    helm_values: null,
    proxy_url: null,
    external_url: null,
    status: 'ready',
    status_message: null,
    celery_task_id: null,
    deployed_at: now,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

function mockAgent(overrides: Partial<BenchmarkAgent> = {}): BenchmarkAgent {
  return {
    id: 1,
    name: 'agent-1',
    hostname: null,
    ip_address: null,
    tags: null,
    capabilities: null,
    status: 'connected',
    last_heartbeat: now,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

describe('eligibleProxies', () => {
  it('keeps ready and discovered proxies, drops the rest', () => {
    const proxies = [
      mockProxy({ id: 1, status: 'ready' }),
      mockProxy({ id: 2, status: 'discovered' }),
      mockProxy({ id: 3, status: 'deploying' }),
      mockProxy({ id: 4, status: 'failed' }),
    ];
    expect(eligibleProxies(proxies).map((p) => p.id)).toEqual([1, 2]);
  });
});

describe('connectedAgents', () => {
  it('keeps only connected agents', () => {
    const agents = [
      mockAgent({ id: 1, status: 'connected' }),
      mockAgent({ id: 2, status: 'disconnected' }),
    ];
    expect(connectedAgents(agents).map((a) => a.id)).toEqual([1]);
  });
});

describe('autoSelectTargetId', () => {
  it('auto-selects when exactly one target exists', () => {
    expect(autoSelectTargetId([{ id: 42 }])).toBe(42);
  });

  it('does not auto-select when there are zero or multiple targets', () => {
    expect(autoSelectTargetId([])).toBeNull();
    expect(autoSelectTargetId([{ id: 1 }, { id: 2 }])).toBeNull();
  });
});

describe('autoSelectProxyId', () => {
  it('auto-selects when exactly one eligible proxy exists', () => {
    const proxies = [mockProxy({ id: 5, status: 'ready' })];
    expect(autoSelectProxyId(proxies)).toBe(5);
  });

  it('does not auto-select when zero or multiple proxies are eligible', () => {
    expect(autoSelectProxyId([])).toBeNull();
    expect(autoSelectProxyId([mockProxy({ id: 1 }), mockProxy({ id: 2 })])).toBeNull();
  });

  it('ignores non-eligible proxies when counting "exactly one"', () => {
    const proxies = [
      mockProxy({ id: 1, status: 'ready' }),
      mockProxy({ id: 2, status: 'failed' }),
      mockProxy({ id: 3, status: 'deploying' }),
    ];
    expect(autoSelectProxyId(proxies)).toBe(1);
  });
});

describe('autoSelectAgentId', () => {
  it('auto-selects when exactly one connected agent exists', () => {
    const agents = [mockAgent({ id: 7, status: 'connected' }), mockAgent({ id: 8, status: 'disconnected' })];
    expect(autoSelectAgentId(agents)).toBe(7);
  });

  it('does not auto-select when zero or multiple agents are connected', () => {
    expect(autoSelectAgentId([])).toBeNull();
    expect(autoSelectAgentId([mockAgent({ id: 1 }), mockAgent({ id: 2 })])).toBeNull();
  });
});

describe('canAdvanceStep', () => {
  const base = emptyWizardState();

  it('step 1 requires both target and proxy', () => {
    expect(canAdvanceStep(1, base)).toBe(false);
    expect(canAdvanceStep(1, { ...base, targetId: 1 })).toBe(false);
    expect(canAdvanceStep(1, { ...base, targetId: 1, proxyId: 2 })).toBe(true);
  });

  it('step 2 requires an agent', () => {
    expect(canAdvanceStep(2, base)).toBe(false);
    expect(canAdvanceStep(2, { ...base, agentId: 3 })).toBe(true);
  });

  it('step 3 in run mode never blocks (config is optional)', () => {
    expect(canAdvanceStep(3, { ...base, mode: 'run' })).toBe(true);
  });

  it('step 3 in scenario mode requires a scenario key', () => {
    expect(canAdvanceStep(3, { ...base, mode: 'scenario', scenarioKey: null })).toBe(false);
    expect(canAdvanceStep(3, { ...base, mode: 'scenario', scenarioKey: 'prefix-cache' })).toBe(true);
  });

  it('step 4 (launch) is always advanceable — launch validity is checked separately', () => {
    expect(canAdvanceStep(4, base)).toBe(true);
  });
});

describe('prefillFromRun', () => {
  const lastRun = {
    target_id: 10,
    proxy_deployment_id: 20,
    agent_id: 30,
    config_id: 40,
    run_label: 'nightly-envoy-run',
    scenario_key: null,
  };

  it('prefills target/proxy/config/label and keeps the agent when still connected', () => {
    const state = prefillFromRun(lastRun, [30, 31], false);
    expect(state).toEqual({
      step: 1,
      targetId: 10,
      proxyId: 20,
      agentId: 30,
      mode: 'run',
      configId: 40,
      scenarioKey: null,
      runLabel: 'nightly-envoy-run (re-run)',
    });
  });

  it('prefills mode:"scenario" with the source scenario_key when the run was a scenario child — never silently normalizes it into a plain single run', () => {
    const scenarioRun = { ...lastRun, scenario_key: 'prefix-cache', config_id: 40 };
    const state = prefillFromRun(scenarioRun, [30], false);
    expect(state).toEqual({
      step: 1,
      targetId: 10,
      proxyId: 20,
      agentId: 30,
      mode: 'scenario',
      configId: null,
      scenarioKey: 'prefix-cache',
      runLabel: 'nightly-envoy-run (re-run)',
    });
  });

  it('scenario-source prefill still jumps to step 4 and still nulls a disconnected agent', () => {
    const scenarioRun = { ...lastRun, scenario_key: 'prefix-cache' };
    expect(prefillFromRun(scenarioRun, [30], true).step).toBe(4);
    expect(prefillFromRun(scenarioRun, [999], true).agentId).toBeNull();
  });

  it('jumps to step 4 (launch) when jumpToLaunch is true', () => {
    expect(prefillFromRun(lastRun, [30], true).step).toBe(4);
  });

  it('nulls out the agent when it is no longer connected — surfaces the disconnected warning', () => {
    const state = prefillFromRun(lastRun, [999], true);
    expect(state.agentId).toBeNull();
  });

  it('nulls out the proxy when it is no longer eligible', () => {
    const state = prefillFromRun(lastRun, [30], true, [999]);
    expect(state.proxyId).toBeNull();
  });

  it('produces an empty run label when the source run had none', () => {
    const state = prefillFromRun({ ...lastRun, run_label: null }, [30], false);
    expect(state.runLabel).toBe('');
  });

  it('falls back to null target/proxy/config when the source run has none', () => {
    const state = prefillFromRun(
      { target_id: null, proxy_deployment_id: null, agent_id: null, config_id: null, run_label: null, scenario_key: null },
      [],
      false,
    );
    expect(state.targetId).toBeNull();
    expect(state.proxyId).toBeNull();
    expect(state.agentId).toBeNull();
    expect(state.configId).toBeNull();
  });
});
