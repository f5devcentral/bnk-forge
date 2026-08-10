/**
 * severityFromConditions — pure helper, unit-tested in __tests__/severity.test.ts
 *
 * Kept in its own module so it can be imported by non-component files without
 * triggering the react-refresh/only-export-components lint rule.
 */

import type { K8sCondition } from '@/types/kubernetes';
import type { HealthSeverity } from '@/types/f5bnk';

/**
 * Derive a HealthSeverity from a resource's status.conditions[].
 *
 * Checks for Ready, Programmed, or Accepted condition types.
 * - Any condition with status 'True' → 'healthy'
 * - Any condition with status 'False' → 'unhealthy'
 * - Any condition with status 'Unknown' → 'degraded'
 * - No matching conditions → 'unknown'
 *
 * If multiple conditions are present, worst severity wins.
 */
export function severityFromConditions(
  conditions: K8sCondition[] | undefined | null
): HealthSeverity {
  if (!conditions || conditions.length === 0) return 'unknown';

  const relevant = conditions.filter(
    (c) => c.type === 'Ready' || c.type === 'Programmed' || c.type === 'Accepted'
  );

  if (relevant.length === 0) return 'unknown';

  const severities: HealthSeverity[] = relevant.map((c) => {
    if (c.status === 'True') return 'healthy';
    if (c.status === 'False') return 'unhealthy';
    return 'degraded'; // 'Unknown' status → degraded
  });

  // Worst severity: unhealthy < degraded < unknown < healthy (lower index = worse)
  const order: Record<HealthSeverity, number> = {
    unhealthy: 0, critical: 0, degraded: 1, warning: 1, unknown: 2, healthy: 3,
  };

  return severities.reduce((worst, current) =>
    order[current] < order[worst] ? current : worst
  );
}
