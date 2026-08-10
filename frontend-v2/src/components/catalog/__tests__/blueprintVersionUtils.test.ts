import { describe, expect, it } from 'vitest';
import { compareBlueprintVersions, sortVersionsDesc } from '../blueprintVersionUtils';

describe('compareBlueprintVersions', () => {
  describe('pure semver', () => {
    it('orders 0.7.1 > 0.7.0', () => {
      expect(compareBlueprintVersions('0.7.1', '0.7.0')).toBeGreaterThan(0);
    });

    it('orders 0.7.0 > 0.6.0', () => {
      expect(compareBlueprintVersions('0.7.0', '0.6.0')).toBeGreaterThan(0);
    });

    it('orders 0.6.0 > 0.5.0', () => {
      expect(compareBlueprintVersions('0.6.0', '0.5.0')).toBeGreaterThan(0);
    });

    it('correctly orders the full set 0.7.1 > 0.7.0 > 0.6.0 > 0.5.0', () => {
      const input = ['0.5.0', '0.7.1', '0.6.0', '0.7.0'];
      const sorted = sortVersionsDesc(input, (v) => v);
      expect(sorted).toEqual(['0.7.1', '0.7.0', '0.6.0', '0.5.0']);
    });

    it('returns 0 for equal versions', () => {
      expect(compareBlueprintVersions('1.2.3', '1.2.3')).toBe(0);
    });

    it('compares by major version', () => {
      expect(compareBlueprintVersions('2.0.0', '1.9.9')).toBeGreaterThan(0);
    });

    it('compares by minor version', () => {
      expect(compareBlueprintVersions('1.10.0', '1.9.0')).toBeGreaterThan(0);
    });

    it('compares by patch version', () => {
      expect(compareBlueprintVersions('1.2.10', '1.2.9')).toBeGreaterThan(0);
    });
  });

  describe('semver with pre-release / EHF suffix', () => {
    it('stable 2.3.0 > pre-release 2.3.0-ehf-2-3.2598.3-0.0.17', () => {
      expect(compareBlueprintVersions('2.3.0', '2.3.0-ehf-2-3.2598.3-0.0.17')).toBeGreaterThan(0);
    });

    it('pre-release 2.3.0-ehf < stable 2.3.0', () => {
      expect(compareBlueprintVersions('2.3.0-ehf-2-3.2598.3-0.0.17', '2.3.0')).toBeLessThan(0);
    });

    it('stable higher version beats lower stable (both parse)', () => {
      expect(compareBlueprintVersions('2.4.0', '2.3.0-ehf-2-3.2598.3-0.0.17')).toBeGreaterThan(0);
    });

    it('two pre-releases with same base compare lexicographically by pre string', () => {
      const result = compareBlueprintVersions('1.0.0-rc.2', '1.0.0-rc.1');
      expect(result).toBeGreaterThan(0);
    });
  });

  describe('malformed / non-semver fallback to lexicographic', () => {
    it('falls back lexicographically for non-parseable strings', () => {
      // "b" > "a" lexicographically
      expect(compareBlueprintVersions('b', 'a')).toBeGreaterThan(0);
    });

    it('non-parseable string is treated as older than any valid semver', () => {
      expect(compareBlueprintVersions('not-a-version', '1.0.0')).toBeLessThan(0);
      expect(compareBlueprintVersions('1.0.0', 'not-a-version')).toBeGreaterThan(0);
    });

    it('two non-parseable strings compare lexicographically', () => {
      const result = compareBlueprintVersions('banana', 'apple');
      expect(result).toBeGreaterThan(0); // 'b' > 'a'
    });
  });

  describe('sortVersionsDesc', () => {
    it('returns a new array, does not mutate the input', () => {
      const input = ['1.0.0', '2.0.0'];
      const sorted = sortVersionsDesc(input, (v) => v);
      expect(input).toEqual(['1.0.0', '2.0.0']);
      expect(sorted).toEqual(['2.0.0', '1.0.0']);
    });

    it('sorts objects by a version key', () => {
      const items = [
        { id: 1, version: '0.5.0' },
        { id: 3, version: '0.7.1' },
        { id: 2, version: '0.6.0' },
      ];
      const sorted = sortVersionsDesc(items, (i) => i.version);
      expect(sorted.map((i) => i.version)).toEqual(['0.7.1', '0.6.0', '0.5.0']);
    });
  });
});
