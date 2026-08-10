/**
 * FU-005: lib/health-utils — calculateHealthScore()
 */
import { describe, it, expect } from 'vitest';
import { calculateHealthScore } from '../health-utils';

describe('calculateHealthScore()', () => {
  it('returns 100 for zero modules', () => {
    expect(
      calculateHealthScore({ totalModules: 0, deployedModules: 0, failedModules: 0, driftedModules: 0 })
    ).toBe(100);
  });

  it('returns 100 for perfect health', () => {
    expect(
      calculateHealthScore({ totalModules: 10, deployedModules: 10, failedModules: 0, driftedModules: 0 })
    ).toBe(100);
  });

  it('calculates weighted score — 50% deployed, 30% non-failed, 20% non-drifted', () => {
    // 5/10 deployed = 25 points (50% * 50)
    // 8/10 non-failed = 24 points (80% * 30)
    // 9/10 non-drifted = 18 points (90% * 20)
    // Total: 67
    expect(
      calculateHealthScore({ totalModules: 10, deployedModules: 5, failedModules: 2, driftedModules: 1 })
    ).toBe(67);
  });

  it('returns 0 for worst health — nothing deployed, all failed, all drifted', () => {
    expect(
      calculateHealthScore({ totalModules: 10, deployedModules: 0, failedModules: 10, driftedModules: 10 })
    ).toBe(0);
  });

  it('handles partial health', () => {
    // 3/5 deployed = 30 (60% * 50)
    // 4/5 non-failed = 24 (80% * 30)
    // 5/5 non-drifted = 20 (100% * 20)
    // Total: 74
    expect(
      calculateHealthScore({ totalModules: 5, deployedModules: 3, failedModules: 1, driftedModules: 0 })
    ).toBe(74);
  });

  it('rounds result to integer', () => {
    // 1/3 deployed ≈ 16.67 (50% * 33.3%)
    // 3/3 non-failed = 30
    // 3/3 non-drifted = 20
    // Total ≈ 66.67 → 67
    expect(
      calculateHealthScore({ totalModules: 3, deployedModules: 1, failedModules: 0, driftedModules: 0 })
    ).toBe(67);
  });
});
