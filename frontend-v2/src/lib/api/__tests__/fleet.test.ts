/**
 * FU-028: lib/api/fleet — fleet API calls
 */
import { describe, it, expect } from 'vitest';
import { fleetApi } from '../fleet';

describe('fleetApi', () => {
  it('getFleetHealth returns health data with operators', async () => {
    const health = await fleetApi.getFleetHealth();
    expect(health).toBeDefined();
    expect(health).toHaveProperty('total_clusters');
    expect(health).toHaveProperty('operators');
  });

  it('compareFleetConfigs returns comparison result', async () => {
    const result = await fleetApi.compareFleetConfigs(1, 2);
    expect(result).toBeDefined();
  });
});
