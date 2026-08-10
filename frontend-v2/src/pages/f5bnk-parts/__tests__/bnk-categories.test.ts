/**
 * Unit tests for buildBnkCategories — pure function, no React needed.
 *
 * Covers:
 *  - Static curated list is always the baseline
 *  - Empty CRD list returns static list unchanged
 *  - Discovered CRDs merged into matching static categories
 *  - Discovered CRDs that produce a new category
 *  - Deduplication: static items not doubled by discovery
 *  - VIEW_* special views preserved after merge
 */

import { describe, it, expect } from 'vitest';
import { buildBnkCategories } from '../bnk-categories';
import { VIEW_HEALTH, VIEW_POLICY_BUILDER, bnkResourceCategories } from '../bnk-constants';
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

describe('buildBnkCategories', () => {
  it('returns the static list unchanged when CRD list is empty', () => {
    const result = buildBnkCategories([]);
    const names = result.map((c) => c.category);
    expect(names).toEqual(bnkResourceCategories.map((c) => c.category));
  });

  it('preserves Insights as the first category', () => {
    const result = buildBnkCategories([]);
    expect(result[0].category).toBe('Insights');
  });

  it('preserves VIEW_* special views in static categories', () => {
    const result = buildBnkCategories([]);
    const insights = result.find((c) => c.category === 'Insights');
    const keys = insights?.items.map((i) => i.key) ?? [];
    expect(keys).toContain(VIEW_HEALTH);
  });

  it('preserves VIEW_POLICY_BUILDER in Build category', () => {
    const result = buildBnkCategories([]);
    const build = result.find((c) => c.category === 'Build');
    expect(build?.items.map((i) => i.key)).toContain(VIEW_POLICY_BUILDER);
  });

  it('merges a discovered CRD into an existing category via a real backend slug', () => {
    // CRDInfo.category is the backend slug ('networking'), never the display name.
    const result = buildBnkCategories([
      crd({
        name: 'newvlans.k8s.f5net.com', kind: 'NewVlan', plural: 'newvlans',
        group: 'k8s.f5net.com', category: 'networking', source: 'registry-enriched',
      }),
    ]);
    const networking = result.find((c) => c.category === 'Networking');
    const keys = networking?.items.map((i) => i.key) ?? [];
    expect(keys).toContain('newvlans.k8s.f5net.com');
    // Static items still present
    expect(keys).toContain('f5spkvlan');
  });

  it('collapses an unknown CRD group into "Other" (never a raw-group tab)', () => {
    const result = buildBnkCategories([
      crd({ kind: 'Widget', plural: 'widgets', group: 'custom.io', category: null }),
    ]);
    const names = result.map((c) => c.category);
    expect(names).toContain('Other');
    expect(names).not.toContain('custom.io');
  });

  it('collapses an unmapped backend category slug into "Other" (e.g. "f5-bnk")', () => {
    const result = buildBnkCategories([
      crd({ kind: 'Afm', plural: 'afms', group: 'k8s.f5.com', category: 'f5-bnk' }),
    ]);
    const names = result.map((c) => c.category);
    expect(names).not.toContain('f5-bnk');
    expect(names).toContain('Other');
  });

  it('routes a real curated slug ("networking") into its actual curated tab, not "Other"', () => {
    const result = buildBnkCategories([
      crd({ name: 'vlans.k8s.f5.com', kind: 'Vlan', plural: 'vlans', group: 'k8s.f5.com', category: 'networking' }),
    ]);
    const names = result.map((c) => c.category);
    expect(names).toContain('Networking');
    expect(names).not.toContain('networking'); // lowercase slug never rendered as its own tab
    const networking = result.find((c) => c.category === 'Networking');
    expect(networking?.items.map((i) => i.key)).toContain('vlans.k8s.f5.com');
  });

  it('stays curated + at most one "Other" even with many uncategorized CRDs (regression)', () => {
    const many = ['k8s.f5.com', 'monitoring.coreos.com', 'gateway.envoyproxy.io', 'cert-manager', 'tawon.mantisnet.com']
      .map((g, i) => crd({ kind: `K${i}`, plural: `k${i}s`, group: g, category: null }));
    const names = buildBnkCategories(many).map((c) => c.category);
    const curated = bnkResourceCategories.map((c) => c.category);
    expect(names.filter((n) => n === 'Other')).toHaveLength(1);
    expect(names).toEqual([...curated, 'Other']); // exactly curated + Other, no per-group tabs
  });

  it('does not duplicate a static item (kind-based dedup, mismatched plural)', () => {
    // 'f5spkvlan' (static key = kind.lower()) is already in the curated Networking list.
    // The discovered plural 'f5spkvlans' differs from the static key, but the dedup
    // check matches on kind, not plural, so it must not be double-listed.
    const result = buildBnkCategories([
      crd({
        name: 'f5spkvlans.k8s.f5net.com', kind: 'F5SpkVlan', plural: 'f5spkvlans',
        group: 'k8s.f5net.com', category: 'networking', source: 'registry-enriched',
      }),
    ]);
    const networking = result.find((c) => c.category === 'Networking');
    const vlanItems = networking?.items.filter(
      (i) => i.key === 'f5spkvlan' || i.key === 'f5spkvlans.k8s.f5net.com'
    ) ?? [];
    expect(vlanItems).toHaveLength(1);
  });

  it('dedups a curated Gateway CRD whose backend slug does not map to a curated tab name (kind=Gateway, slug=gateway-api)', () => {
    // Real backend slug: ResourceCategory.GATEWAY_API = 'gateway-api' (core/k8s_types.py:51).
    // 'gateway-api' isn't a curated tab name, so without the global kind-dedup this
    // would render a second 'Gateway' entry in 'Other' alongside the curated one in
    // 'Traffic Management'.
    const result = buildBnkCategories([
      crd({
        name: 'gateways.gateway.networking.k8s.io', kind: 'Gateway', plural: 'gateways',
        group: 'gateway.networking.k8s.io', category: 'gateway-api', source: 'registry-enriched',
      }),
    ]);
    const names = result.map((c) => c.category);
    expect(names).not.toContain('Other');
    const traffic = result.find((c) => c.category === 'Traffic Management');
    const gatewayItems = traffic?.items.filter(
      (i) => i.key === 'gateway' || i.key === 'gateways.gateway.networking.k8s.io'
    ) ?? [];
    expect(gatewayItems).toHaveLength(1);
  });

  it('dedups a curated BNKSecPolicy CRD whose backend slug does not map to a curated tab name (kind=BNKSecPolicy, slug=f5-bnk)', () => {
    // Real backend slug: ResourceCategory.F5_BNK = 'f5-bnk' (core/k8s_types.py:52).
    // Without the global kind-dedup this would render a second 'BNKSecPolicy' entry in
    // 'Other' alongside the curated 'bnksecpolicy' entry in 'Security'.
    const result = buildBnkCategories([
      crd({
        name: 'bnksecpolicies.gateway.k8s.f5net.com', kind: 'BNKSecPolicy', plural: 'bnksecpolicies',
        group: 'gateway.k8s.f5net.com', category: 'f5-bnk', source: 'registry-enriched',
      }),
    ]);
    const names = result.map((c) => c.category);
    expect(names).not.toContain('Other');
    const security = result.find((c) => c.category === 'Security');
    const secPolicyItems = security?.items.filter(
      (i) => i.key === 'bnksecpolicy' || i.key === 'bnksecpolicies.gateway.k8s.f5net.com'
    ) ?? [];
    expect(secPolicyItems).toHaveLength(1);
  });

  it('a genuinely unknown CRD (kind not present in any curated bucket) still lands in "Other"', () => {
    const result = buildBnkCategories([
      crd({
        name: 'somethings.monitoring.coreos.com', kind: 'ServiceMonitor', plural: 'somethings',
        group: 'monitoring.coreos.com', category: null,
      }),
    ]);
    const other = result.find((c) => c.category === 'Other');
    expect(other?.items.map((i) => i.key)).toContain('somethings.monitoring.coreos.com');
  });

  it('does not mutate the original bnkResourceCategories', () => {
    const before = bnkResourceCategories.map((c) => c.items.length);
    buildBnkCategories([
      crd({ name: 'extras.k8s.f5.com', kind: 'Extra', plural: 'extras', category: 'networking', source: 'discovered' }),
    ]);
    const after = bnkResourceCategories.map((c) => c.items.length);
    expect(after).toEqual(before);
  });

  it('uses display_name as label when provided (item lands under "Other")', () => {
    const result = buildBnkCategories([
      crd({ kind: 'MyKind', plural: 'mykinds', display_name: 'Custom Resource', group: 'custom.io' }),
    ]);
    const cat = result.find((c) => c.category === 'Other');
    expect(cat?.items[0].label).toBe('Custom Resource');
  });
});
