/**
 * FU-026: lib/api/drift — drift detection API calls
 */
import { describe, it, expect } from 'vitest';
import { driftApi } from '../drift';

describe('driftApi', () => {
  it('getGlobalDriftSummary returns summary', async () => {
    const summary = await driftApi.getGlobalDriftSummary();
    expect(summary).toBeDefined();
  });

  it('getDriftSettings returns settings for project', async () => {
    const settings = await driftApi.getDriftSettings(1);
    expect(settings).toBeDefined();
  });

  it('getDriftChecks returns checks for project', async () => {
    const checks = await driftApi.getDriftChecks(1);
    expect(checks).toBeDefined();
  });

  it('getDriftStats returns stats', async () => {
    const stats = await driftApi.getDriftStats();
    expect(stats).toBeDefined();
  });
});
