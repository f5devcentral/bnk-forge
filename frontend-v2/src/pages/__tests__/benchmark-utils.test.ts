/**
 * Tests for the regression-badge classifier (getRegressionStatus) — pure function,
 * no rendering. Diffs a completed run's latency_p99 / overall_rps against its
 * context's baseline using the shared REGRESSION_THRESHOLD_PCT (10%).
 */
import { describe, it, expect } from 'vitest';
import { getRegressionStatus, REGRESSION_THRESHOLD_PCT } from '@/pages/benchmark-utils';

describe('getRegressionStatus', () => {
  it('returns "baseline" when the run itself is the baseline', () => {
    expect(getRegressionStatus({ is_baseline: true })).toBe('baseline');
  });

  it('returns null when there is no baseline context to compare against', () => {
    expect(
      getRegressionStatus({
        is_baseline: false,
        latency_p99: 0.2,
        overall_rps: 100,
        baseline_latency_p99: null,
        baseline_overall_rps: null,
      }),
    ).toBeNull();
  });

  it('returns "regression" when latency_p99 is more than 10% worse than baseline', () => {
    const status = getRegressionStatus({
      latency_p99: 0.25, // 25% worse than 0.2
      baseline_latency_p99: 0.2,
      overall_rps: 100,
      baseline_overall_rps: 100,
    });
    expect(status).toBe('regression');
  });

  it('returns "regression" when overall_rps is more than 10% lower than baseline', () => {
    const status = getRegressionStatus({
      latency_p99: 0.2,
      baseline_latency_p99: 0.2,
      overall_rps: 80, // 20% lower than 100
      baseline_overall_rps: 100,
    });
    expect(status).toBe('regression');
  });

  it('returns "improvement" when latency_p99 is more than 10% better than baseline', () => {
    const status = getRegressionStatus({
      latency_p99: 0.15, // 25% better than 0.2
      baseline_latency_p99: 0.2,
      overall_rps: 100,
      baseline_overall_rps: 100,
    });
    expect(status).toBe('improvement');
  });

  it('returns "improvement" when overall_rps is more than 10% higher than baseline', () => {
    const status = getRegressionStatus({
      latency_p99: 0.2,
      baseline_latency_p99: 0.2,
      overall_rps: 120, // 20% higher than 100
      baseline_overall_rps: 100,
    });
    expect(status).toBe('improvement');
  });

  it('returns "neutral" when within the threshold band both ways', () => {
    const status = getRegressionStatus({
      latency_p99: 0.205, // 2.5% worse — within threshold
      baseline_latency_p99: 0.2,
      overall_rps: 98, // 2% lower — within threshold
      baseline_overall_rps: 100,
    });
    expect(status).toBe('neutral');
  });

  it('regression takes precedence over an improving metric when signals conflict', () => {
    const status = getRegressionStatus({
      latency_p99: 0.25, // regresses
      baseline_latency_p99: 0.2,
      overall_rps: 130, // improves
      baseline_overall_rps: 100,
    });
    expect(status).toBe('regression');
  });

  it('exposes the shared 10% threshold constant', () => {
    expect(REGRESSION_THRESHOLD_PCT).toBe(10);
  });
});
