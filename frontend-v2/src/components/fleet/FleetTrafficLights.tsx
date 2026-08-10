/**
 * Shared fleet traffic-light presentational components.
 *
 * Extracted from Fleet.tsx (D-022 P6) so Dashboard and other surfaces can
 * reuse the same visual language without duplicating logic.
 *
 * Exports:
 *   TrafficDot            — single coloured dot with label
 *   FleetTrafficLights    — Health / Policy / Ops trio for a FleetRollup
 *   EstateSummaryBar      — aggregate headline across all rollups
 *   healthStateFromRollup — derive TrafficLight from worst_state
 *   policyStateFromRollup — derive TrafficLight from compliance counts
 */

import { cn } from '@/lib/utils';
import type { FleetRollup, TrafficLight } from '@/types/fleet';

// ─────────────────────────────────────────────────────────────────────────────
// Color/label maps (re-exported so callers can build their own dots)
// ─────────────────────────────────────────────────────────────────────────────

export const TRAFFIC_DOT_COLOR: Record<TrafficLight, string> = {
  green: 'bg-success',
  amber: 'bg-warning',
  red: 'bg-destructive',
  grey: 'bg-muted-foreground',
};

// ─────────────────────────────────────────────────────────────────────────────
// Derivation helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Derive health traffic-light from worst_state. */
export function healthStateFromRollup(rollup: FleetRollup): TrafficLight {
  switch (rollup.worst_state) {
    case 'ready':        return 'green';
    case 'provisioning': return 'amber';
    case 'drifted':      return 'amber';
    case 'unreachable':  return 'red';
    case 'error':        return 'red';
    case 'unknown':      return 'grey';
    default:             return 'grey';
  }
}

/** Derive policy traffic-light from compliance counts. */
export function policyStateFromRollup(rollup: FleetRollup): TrafficLight {
  if (rollup.policy_count === 0 || rollup.total_evaluated === 0) return 'grey';
  if (rollup.drift_count === 0) return 'green';
  // >50% drifted → red
  if (rollup.total_evaluated > 0 && rollup.drift_count / rollup.total_evaluated > 0.5) return 'red';
  return 'amber';
}

// ─────────────────────────────────────────────────────────────────────────────
// Presentational components
// ─────────────────────────────────────────────────────────────────────────────

/** Traffic-light dot — a 2×2 coloured circle with an optional text label. */
export function TrafficDot({ state, label }: { state: TrafficLight; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className={cn('h-2 w-2 rounded-full shrink-0', TRAFFIC_DOT_COLOR[state])} />
      <span className="text-[11px] text-muted-foreground">{label}</span>
    </span>
  );
}

/**
 * Three traffic-light indicators for a fleet row: Health / Policy / Operations.
 * Each dot carries a tooltip with detail text.
 */
export function FleetTrafficLights({ rollup }: { rollup: FleetRollup }) {
  const healthState = healthStateFromRollup(rollup);
  const policyState = policyStateFromRollup(rollup);
  const opsState = rollup.ops_state;

  const healthTip = (() => {
    const reachable = rollup.member_count - rollup.unreachable_count;
    const base = `${reachable}/${rollup.member_count} reachable`;
    if (rollup.worst_state === 'ready')        return `Ready — ${base}`;
    if (rollup.worst_state === 'provisioning') return `Provisioning — ${base}`;
    if (rollup.worst_state === 'drifted')      return `Drifted — ${base}`;
    if (rollup.worst_state === 'unreachable')  return `Unreachable — ${rollup.unreachable_count} unreachable`;
    return 'Unknown — no members or no data';
  })();

  const policyTip = (() => {
    if (rollup.policy_count === 0)      return 'No policies defined';
    if (rollup.total_evaluated === 0)   return 'No evaluations run yet';
    if (rollup.drift_count === 0)       return `${rollup.compliant_count}/${rollup.total_evaluated} compliant — all clear`;
    return `${rollup.compliant_count}/${rollup.total_evaluated} compliant, ${rollup.drift_count} drifted`;
  })();

  const opsTip = (() => {
    if (opsState === 'grey')  return 'No operations run yet';
    if (opsState === 'red')   return 'Last run failed — needs attention';
    if (opsState === 'amber') return `${rollup.active_run_count} active run${rollup.active_run_count !== 1 ? 's' : ''} in progress`;
    return 'All runs complete — all clear';
  })();

  return (
    <div className="flex items-center gap-3">
      <span title={`Health: ${healthTip}`} className="cursor-default">
        <TrafficDot state={healthState} label="Health" />
      </span>
      <span title={`Policy: ${policyTip}`} className="cursor-default">
        <TrafficDot state={policyState} label="Policy" />
      </span>
      <span title={`Operations: ${opsTip}`} className="cursor-default">
        <TrafficDot state={opsState} label="Ops" />
      </span>
    </div>
  );
}

/**
 * Estate summary bar — aggregate conformance headline across all fleet rollups.
 * Shows N fleets · X healthy · Y with drift · Z ops-failed · W provisioning
 */
export function EstateSummaryBar({ rollups }: { rollups: FleetRollup[] }) {
  if (rollups.length === 0) return null;

  const total        = rollups.length;
  const healthy      = rollups.filter((r) => r.worst_state === 'ready').length;
  const withDrift    = rollups.filter((r) => r.drift_count > 0).length;
  const failingOps   = rollups.filter((r) => r.ops_state === 'red').length;
  const provisioning = rollups.filter((r) => r.worst_state === 'provisioning').length;
  const unreachable  = rollups.filter((r) => r.worst_state === 'unreachable').length;
  const activeOps    = rollups.filter((r) => r.ops_state === 'amber').length;

  const chips: Array<{ label: string; count: number; color: string }> = [
    { label: 'fleet',   count: total,   color: 'text-foreground' },
    { label: 'healthy', count: healthy, color: 'text-success' },
  ];
  if (withDrift    > 0) chips.push({ label: 'with drift',   count: withDrift,    color: 'text-warning' });
  if (unreachable  > 0) chips.push({ label: 'unreachable',  count: unreachable,  color: 'text-muted-foreground' });
  if (provisioning > 0) chips.push({ label: 'provisioning', count: provisioning, color: 'text-primary' });
  if (failingOps   > 0) chips.push({ label: 'ops failed',   count: failingOps,   color: 'text-destructive' });
  if (activeOps    > 0) chips.push({ label: 'ops active',   count: activeOps,    color: 'text-warning' });

  return (
    <div className="flex flex-wrap items-center gap-1.5 px-1">
      {chips.map((chip, idx) => (
        <span key={chip.label} className="inline-flex items-center gap-1">
          {idx > 0 && <span className="text-muted-foreground/40 text-xs">·</span>}
          <span className={cn('text-xs font-semibold tabular-nums', chip.color)}>{chip.count}</span>
          <span className="text-xs text-muted-foreground">
            {chip.label}{chip.count !== 1 && chip.label === 'fleet' ? 's' : ''}
          </span>
        </span>
      ))}
    </div>
  );
}
