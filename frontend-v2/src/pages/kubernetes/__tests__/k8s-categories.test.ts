/**
 * Unit tests for buildK8sCategories — pure function, no React needed.
 *
 * Covers:
 *  - Static baseline always present (built-in K8s kinds)
 *  - Discovered CRDs merged into matching static categories
 *  - Discovered CRDs that produce a new category
 *  - l4route surfaces in Gateway API when present in the CRD list
 *  - Deduplication: discovered entry doesn't double-render a static item
 *  - Empty CRD list returns static baseline unchanged
 */

import { describe, it, expect } from 'vitest';
import { buildK8sCategories } from '../k8s-categories';
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

describe('buildK8sCategories', () => {
  it('includes static baseline categories when CRD list is empty', () => {
    const result = buildK8sCategories([]);
    const names = result.map((c) => c.category);
    expect(names).toContain('Workloads');
    expect(names).toContain('Gateway API');
    expect(names).toContain('Networking');
  });

  it('includes built-in items like Pods and Deployments', () => {
    const result = buildK8sCategories([]);
    const workloads = result.find((c) => c.category === 'Workloads');
    const keys = workloads?.items.map((i) => i.key) ?? [];
    expect(keys).toContain('pod');
    expect(keys).toContain('deployment');
  });

  it('l4route is present in Gateway API from the static baseline', () => {
    const result = buildK8sCategories([]);
    const gatewayApi = result.find((c) => c.category === 'Gateway API');
    const keys = gatewayApi?.items.map((i) => i.key) ?? [];
    expect(keys).toContain('l4route');
  });

  it('merges a discovered CRD into an existing category by slug', () => {
    // CRDInfo.category is the backend slug ('gateway-api'), never the display name.
    const result = buildK8sCategories([
      crd({ name: 'mypolicies.custom.io', kind: 'MyPolicy', plural: 'mypolicies', category: 'gateway-api', source: 'registry-enriched' }),
    ]);
    const gatewayApi = result.find((c) => c.category === 'Gateway API');
    const keys = gatewayApi?.items.map((i) => i.key) ?? [];
    expect(keys).toContain('mypolicies.custom.io');
    // Static entries still present
    expect(keys).toContain('gateway');
    // No parallel slug-named tab was created
    expect(result.map((c) => c.category)).not.toContain('gateway-api');
  });

  it('appends a new category when the CRD group is unknown', () => {
    const result = buildK8sCategories([
      crd({ kind: 'Widget', plural: 'widgets', group: 'custom.io', category: null }),
    ]);
    const names = result.map((c) => c.category);
    expect(names).toContain('custom.io');
  });

  it('does not duplicate a static item already covering the same kind', () => {
    // 'httproute' (static key = kind.lower()) is already in the static Gateway API list.
    // The discovered plural 'httproutes' differs from the static singular key, but the
    // dedup check matches on kind, not plural, so it must not be double-listed.
    const before = buildK8sCategories([]).find((c) => c.category === 'Gateway API');
    const beforeCount = before?.items.length ?? 0;

    const result = buildK8sCategories([
      crd({
        name: 'httproutes.gateway.networking.k8s.io',
        kind: 'HTTPRoute',
        plural: 'httproutes',
        group: 'gateway.networking.k8s.io',
        category: 'gateway-api',
        source: 'registry-enriched',
      }),
    ]);
    const gatewayApi = result.find((c) => c.category === 'Gateway API');
    expect(gatewayApi?.items.length).toBe(beforeCount);
    expect(gatewayApi?.items.filter((i) => i.key.startsWith('httproute'))).toHaveLength(1);
  });

  it('uses display_name as label when provided', () => {
    const result = buildK8sCategories([
      crd({ kind: 'MyKind', plural: 'mykinds', display_name: 'My Resources', group: 'custom.io' }),
    ]);
    const cat = result.find((c) => c.category === 'custom.io');
    expect(cat?.items[0].label).toBe('My Resources');
  });

  it('falls back to kind as label when display_name is null', () => {
    const result = buildK8sCategories([
      crd({ kind: 'MyKind', plural: 'mykinds', display_name: null, group: 'custom.io' }),
    ]);
    const cat = result.find((c) => c.category === 'custom.io');
    expect(cat?.items[0].label).toBe('MyKind');
  });

  it('does not mutate the original resourceTree by reference', () => {
    const r1 = buildK8sCategories([]);
    const r2 = buildK8sCategories([
      crd({ name: 'extras.custom.io', kind: 'Extra', plural: 'extras', category: 'gateway-api', source: 'registry-enriched' }),
    ]);
    const gatewayApi1 = r1.find((c) => c.category === 'Gateway API');
    const gatewayApi2 = r2.find((c) => c.category === 'Gateway API');
    // r1 should not see the 'extras.custom.io' entry added to r2
    expect(gatewayApi1?.items.map((i) => i.key)).not.toContain('extras.custom.io');
    expect(gatewayApi2?.items.map((i) => i.key)).toContain('extras.custom.io');
  });
});
