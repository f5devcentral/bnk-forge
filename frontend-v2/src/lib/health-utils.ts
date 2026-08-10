/**
 * Health score calculation utility.
 * Extracted from Dashboard.tsx to be reusable.
 */

interface HealthScoreInputs {
  totalModules: number;
  deployedModules: number;
  failedModules: number;
  driftedModules: number;
}

/**
 * Calculate a 0-100 health score from module status breakdown.
 *
 * Scoring:
 * - 50% weight: ratio of deployed modules
 * - 30% weight: ratio of non-failed modules
 * - 20% weight: ratio of non-drifted modules
 *
 * Returns 100 if there are no modules (nothing to be unhealthy).
 */
export function calculateHealthScore({
  totalModules,
  deployedModules,
  failedModules,
  driftedModules,
}: HealthScoreInputs): number {
  if (totalModules === 0) return 100;

  const deployedScore = (deployedModules / totalModules) * 50;
  const failureScore = ((totalModules - failedModules) / totalModules) * 30;
  const driftScore = ((totalModules - driftedModules) / totalModules) * 20;

  return Math.round(deployedScore + failureScore + driftScore);
}
