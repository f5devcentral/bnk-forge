/**
 * FU-011: lib/storage-keys — localStorage key constants
 */
import { describe, it, expect } from 'vitest';
import { STORAGE_KEYS } from '../storage-keys';

describe('STORAGE_KEYS', () => {
  it('has expected keys', () => {
    const expectedKeys = [
      'K8S_PROJECT', 'K8S_CLUSTER', 'HELM_CLUSTER',
      'BNK_PROJECT', 'BNK_CLUSTER', 'AUTH_TOKEN',
      'ONBOARDING_COMPLETE', 'MOCKUP_THEME',
    ];
    for (const key of expectedKeys) {
      expect(STORAGE_KEYS).toHaveProperty(key);
    }
  });

  it('values are all strings', () => {
    for (const value of Object.values(STORAGE_KEYS)) {
      expect(typeof value).toBe('string');
    }
  });

  it('values are unique (no duplicate keys)', () => {
    const values = Object.values(STORAGE_KEYS);
    const uniqueValues = new Set(values);
    expect(uniqueValues.size).toBe(values.length);
  });

  it('has auth token key', () => {
    expect(STORAGE_KEYS.AUTH_TOKEN).toBe('auth_token');
  });
});
