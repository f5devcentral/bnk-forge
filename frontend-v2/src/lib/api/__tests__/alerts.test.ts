/**
 * FU-030: lib/api/alerts — alert channel API calls
 */
import { describe, it, expect } from 'vitest';
import { alertsApi } from '../alerts';

describe('alertsApi', () => {
  it('listAlertChannels returns array of channels', async () => {
    // API does .then(res => res.data) — handler returns plain array
    const result = await alertsApi.listAlertChannels();
    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBeGreaterThan(0);
  });

  it('getRecentAlerts returns history', async () => {
    const history = await alertsApi.getRecentAlerts();
    expect(history).toBeDefined();
  });

  it('testAlertChannel returns result', async () => {
    const result = await alertsApi.testAlertChannel(1);
    expect(result).toHaveProperty('success', true);
  });
});
