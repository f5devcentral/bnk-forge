/**
 * FU-031: lib/api/snapshots — snapshot API calls
 */
import { describe, it, expect } from 'vitest';
import { snapshotsApi } from '../snapshots';

describe('snapshotsApi', () => {
  it('listSnapshots returns snapshot data', async () => {
    const result = await snapshotsApi.listSnapshots(1);
    expect(result).toHaveProperty('snapshots');
    expect(Array.isArray(result.snapshots)).toBe(true);
  });

  it('getSnapshot returns snapshot detail', async () => {
    const snapshot = await snapshotsApi.getSnapshot(1);
    expect(snapshot).toBeDefined();
    expect(snapshot).toHaveProperty('id');
  });

  it('createSnapshot returns new snapshot with id', async () => {
    const result = await snapshotsApi.createSnapshot(1, { description: 'test snapshot' });
    expect(result).toHaveProperty('id');
    expect(result).toHaveProperty('snapshot_type', 'manual');
  });
});
