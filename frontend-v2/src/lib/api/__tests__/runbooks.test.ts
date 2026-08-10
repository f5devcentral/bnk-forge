/**
 * FU-032: lib/api/runbooks — runbook API calls
 */
import { describe, it, expect } from 'vitest';
import { runbooksApi } from '../runbooks';

describe('runbooksApi', () => {
  it('listRunbooks returns array', async () => {
    const result = await runbooksApi.listRunbooks();
    expect(result).toHaveProperty('runbooks');
    expect(Array.isArray(result.runbooks)).toBe(true);
  });

  it('executeRunbook returns result', async () => {
    const result = await runbooksApi.executeRunbook(1, { params: {} });
    expect(result).toBeDefined();
  });
});
