/**
 * FU-027: lib/api/operators — operator API calls
 */
import { describe, it, expect } from 'vitest';
import { operatorsApi } from '../operators';

describe('operatorsApi', () => {
  it('listOperators returns array of operators', async () => {
    // API does .then(res => res.data) — handler returns plain array
    const operators = await operatorsApi.listOperators();
    expect(Array.isArray(operators)).toBe(true);
    expect(operators.length).toBeGreaterThan(0);
  });

  it('getConnectivityModes returns modes array', async () => {
    // API does .then(res => res.data) — handler returns plain array
    const modes = await operatorsApi.getConnectivityModes();
    expect(Array.isArray(modes)).toBe(true);
  });

  it('sendOperatorCommand returns result', async () => {
    const result = await operatorsApi.sendOperatorCommand('op1', 'status');
    expect(result).toHaveProperty('success', true);
    expect(result).toHaveProperty('command_id');
  });
});
