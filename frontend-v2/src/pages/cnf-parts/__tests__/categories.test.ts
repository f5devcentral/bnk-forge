/**
 * Unit tests for buildCnfCategories — pure function, no React/MSW needed.
 *
 * Covers:
 *  - Registry-enriched CRDs grouped by category field
 *  - Discovered CRDs grouped by group field (fallback)
 *  - Mixed: enriched categories sort before bare-group categories
 *  - Items within each category sort alphabetically by label
 *  - display_name used as label when present, kind as fallback
 */

import { describe, it, expect } from 'vitest';
import { buildCnfCategories } from '../categories';
import type { CRDInfo } from '@/hooks/useCrds';

function crd(overrides: Partial<CRDInfo>): CRDInfo {
  return {
    name: 'things.example.com',
    kind: 'Thing',
    plural: 'things',
    group: 'example.com',
    version: 'v1',
    namespaced: true,
    display_name: null,
    category: null,
    source: 'discovered',
    ...overrides,
  };
}

describe('buildCnfCategories', () => {
  it('returns an empty array for an empty CRD list', () => {
    expect(buildCnfCategories([])).toEqual([]);
  });

  it('groups a discovered CRD under its API group when category is null', () => {
    const result = buildCnfCategories([crd({ group: 'foo.io', category: null })]);
    expect(result).toHaveLength(1);
    expect(result[0].category).toBe('foo.io');
    expect(result[0].enriched).toBe(false);
  });

  it('groups a registry-enriched CRD under its category field', () => {
    const result = buildCnfCategories([
      crd({ category: 'Traffic Management', source: 'registry-enriched' }),
    ]);
    expect(result).toHaveLength(1);
    expect(result[0].category).toBe('Traffic Management');
    expect(result[0].enriched).toBe(true);
  });

  it('puts enriched categories before bare-group categories', () => {
    const crds = [
      crd({ name: 'a.z.io', group: 'z.io', category: null, source: 'discovered' }),
      crd({ name: 'b.curated', group: 'curated', category: 'Alpha', source: 'registry-enriched' }),
    ];
    const result = buildCnfCategories(crds);
    expect(result[0].enriched).toBe(true);
    expect(result[0].category).toBe('Alpha');
    expect(result[1].enriched).toBe(false);
  });

  it('sorts enriched categories alphabetically among themselves', () => {
    const crds = [
      crd({ name: 'b.g', group: 'g', category: 'Zebra', source: 'registry-enriched' }),
      crd({ name: 'a.g', group: 'g', category: 'Alpha', source: 'registry-enriched' }),
      crd({ name: 'c.g', group: 'g', category: 'Middle', source: 'registry-enriched' }),
    ];
    const result = buildCnfCategories(crds);
    const names = result.map((c) => c.category);
    expect(names).toEqual(['Alpha', 'Middle', 'Zebra']);
  });

  it('sorts bare-group categories alphabetically among themselves', () => {
    const crds = [
      crd({ name: 'a.c.io', group: 'c.io', category: null }),
      crd({ name: 'a.a.io', group: 'a.io', category: null }),
      crd({ name: 'a.b.io', group: 'b.io', category: null }),
    ];
    const result = buildCnfCategories(crds);
    expect(result.map((c) => c.category)).toEqual(['a.io', 'b.io', 'c.io']);
  });

  it('sorts items within a category alphabetically by label (display_name)', () => {
    const crds = [
      crd({ name: 'z.g.io', kind: 'Zebra', display_name: 'Zebra Resource', group: 'g.io' }),
      crd({ name: 'a.g.io', kind: 'Alpha', display_name: 'Alpha Resource', group: 'g.io' }),
      crd({ name: 'm.g.io', kind: 'Mid', display_name: 'Mid Resource', group: 'g.io' }),
    ];
    const result = buildCnfCategories(crds);
    expect(result[0].items.map((i) => i.label)).toEqual([
      'Alpha Resource',
      'Mid Resource',
      'Zebra Resource',
    ]);
  });

  it('falls back to kind when display_name is null', () => {
    const result = buildCnfCategories([crd({ kind: 'Widget', display_name: null })]);
    expect(result[0].items[0].label).toBe('Widget');
  });

  it('uses CRDInfo.name as the stable key', () => {
    const result = buildCnfCategories([crd({ name: 'widgets.example.com' })]);
    expect(result[0].items[0].key).toBe('widgets.example.com');
  });

  it('groups multiple CRDs into the same category bucket', () => {
    const crds = [
      crd({ name: 'foo.g.io', kind: 'Foo', group: 'g.io', category: null }),
      crd({ name: 'bar.g.io', kind: 'Bar', group: 'g.io', category: null }),
    ];
    const result = buildCnfCategories(crds);
    expect(result).toHaveLength(1);
    expect(result[0].items).toHaveLength(2);
  });

  it('marks a bucket enriched if any item in it has a category', () => {
    // Both items end up in 'MyCategory'; one is enriched, one is bare.
    // The bucket should be marked enriched.
    const crds = [
      crd({ name: 'a.g', group: 'g', category: 'MyCategory', source: 'registry-enriched' }),
      crd({ name: 'b.g', group: 'g', category: 'MyCategory', source: 'discovered' }),
    ];
    const result = buildCnfCategories(crds);
    expect(result).toHaveLength(1);
    expect(result[0].enriched).toBe(true);
  });

  it('preserves source on items', () => {
    const crds = [
      crd({ name: 'r.io', source: 'registry-enriched', category: 'Cat' }),
      crd({ name: 'd.io', source: 'discovered', group: 'd.io' }),
    ];
    const result = buildCnfCategories(crds);
    const allItems = result.flatMap((c) => c.items);
    const registry = allItems.find((i) => i.key === 'r.io');
    const discovered = allItems.find((i) => i.key === 'd.io');
    expect(registry?.source).toBe('registry-enriched');
    expect(discovered?.source).toBe('discovered');
  });
});
